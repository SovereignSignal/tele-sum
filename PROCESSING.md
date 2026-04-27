# Processing Pipeline

How tele-sum gets a Telegram post from "just arrived" to a validated `Summary` row.

## Pipeline stages

For each message:

1. **Ingest** — `sync_and_process()` in [`src/services/scheduler.py:32`](src/services/scheduler.py:32) calls Telegram Bot API `getUpdates` for each bot token. New posts are inserted into `messages`, with `wire` and `channel_config_id` copied from the matching `channel_configs` row. A blank `Summary` row is created with status `PENDING`.
2. **URL extraction** — `src/utils/url_extractor.py` pulls URLs out of `raw_content` at insert time.
3. **Scrape** — `MessageProcessor.process_message()` in [`src/services/processor.py:58`](src/services/processor.py:58) splits URLs into Twitter and non-Twitter. Twitter URLs go through `TwitterScraper` (FxTwitter), others through `ContentScraper` (BeautifulSoup).
4. **AI call** — `AIService.analyze_content()` in [`src/services/ai.py:271`](src/services/ai.py:271) builds the prompt and dispatches to the configured provider (Anthropic, Ollama Cloud, or any OpenAI-compatible chat-completions endpoint).
5. **Validate** — the response is parsed as JSON and validated by `AIAnalysis` from [`src/schemas/entities.py`](src/schemas/entities.py). Anything that fails validation is dropped and the row is re-queued.
6. **Persist** — `Summary` is updated with `title`, `summary`, `category_id`, `confidence`, and `entities`. `processing_status` flips to `SUCCESS`.
7. **Telemetry** — every AI call writes one row to the shared `api_usage_log` table (provider, model, purpose='summarize', tokens_in, tokens_out, wire, duration_ms, success, error_message).

## State machine

```
PENDING ──▶ PROCESSING ──▶ SUCCESS
                ├─▶ RETRY  (returned None, validation fail, transient error)
                └─▶ FAILED (attempts >= MAX_PROCESSING_RETRIES)

RETRY  ──▶ PROCESSING (next cycle)
FAILED ──▶ RETRY      (retry_failed_messages, within 24h, under cap)
```

| Status | Meaning |
|--------|---------|
| `PENDING` | Created at ingest, never processed |
| `PROCESSING` | Currently in flight. Reset to `RETRY` if stuck >30 min |
| `SUCCESS` | Validated row written |
| `RETRY` | Eligible to be picked up again |
| `FAILED` | Hit `MAX_PROCESSING_RETRIES`. Re-queued by `retry_failed_messages` if recent |

The state transition logic lives in `MessageProcessor.process_message()` and `retry_failed_messages()` in [`src/services/processor.py`](src/services/processor.py).

## Scheduler jobs

Five APScheduler jobs are registered in `start_scheduler()` ([`src/services/scheduler.py:300`](src/services/scheduler.py:300)):

| Job id | Trigger | Function | Purpose |
|--------|---------|----------|---------|
| `sync_job` | every `SYNC_INTERVAL_HOURS` (default 6) | `sync_and_process()` | Pull `getUpdates` for each bot, insert new messages, then process up to 200 in batches of 20 |
| `process_pending` | every 2 min | `process_pending_only()` | Process up to 10 `PENDING`/`RETRY` rows. Keeps queue moving between full syncs |
| `retry_failed` | every 15 min | `retry_failed_messages()` | Move `FAILED` → `RETRY` for rows with `last_processed_at` within 24h and `attempts < max_retries` |
| `cleanup_stale` | every 1 hour | `cleanup_stale_processing()` | Reset rows stuck in `PROCESSING` for >30 min back to `RETRY` |
| `daily_stats` | cron `0 0 * * *` | `log_daily_stats()` | Emit a stats line to logs at midnight UTC |

If `RUN_SYNC_ON_STARTUP=true` (default), `sync_and_process()` also fires once on application boot.

## Configuration

| Env var | Default | Effect |
|---------|---------|--------|
| `SYNC_INTERVAL_HOURS` | 6 | Interval for `sync_job` |
| `MAX_AI_REQUESTS_PER_HOUR` | 100 | Cap enforced by `AIService._RateLimiter` (process-local, in-memory) |
| `MAX_PROCESSING_RETRIES` | 3 | Hard ceiling before `FAILED` |
| `PROCESSING_BATCH_SIZE` | 10 | Batch size for ad-hoc calls; the scheduler uses 20 in `sync_and_process` and 10 in `process_pending_only` |
| `RUN_SYNC_ON_STARTUP` | true | Kick off one full sync on boot |
| `BOT_POLLING_INTERVAL` | 2 | Used by `telegram_bot.py` long-poll loop |

## AI provider resolution

`AIService` resolves its provider lazily, in order:

1. Env override: if `AI_PROVIDER` and `AI_API_KEY` are set, use those (with optional `AI_BASE_URL`, `AI_MODEL`).
2. DB lookup: select from `api_providers` where `enabled = true`, excluding non-LLM rows (`firecrawl`, `resend`). If `AI_PROVIDER` is set but no key, that provider name is used to filter the lookup.

Three call paths are implemented in [`src/services/ai.py`](src/services/ai.py):

- `_call_anthropic` — native `/v1/messages`
- `_call_ollama` — Ollama Cloud `/api/chat` (different shape from OpenAI-compat; uses `format: "json"`)
- `_call_openai_compat` — `/chat/completions` for everything else (Grok, OpenAI, Perplexity, OpenRouter, Groq, Together, ...)

If no provider resolves, processing skips silently — messages stay in `PENDING` rather than failing.

## Triggering manually

```bash
# Full sync + process pass (auth required)
curl -X POST -H "X-API-Key: $API_KEY" \
  https://web-production-82642.up.railway.app/api/trigger-sync

# Process-only pass (no Telegram pull)
curl -X POST -H "X-API-Key: $API_KEY" \
  https://web-production-82642.up.railway.app/api/trigger-process

# Stats
curl -H "X-API-Key: $API_KEY" \
  https://web-production-82642.up.railway.app/api/stats
```

## Troubleshooting

**Messages stuck in `PENDING`** — scheduler not running, or no AI provider resolved. Check `GET /health` for scheduler state and the boot log line `AI provider (...): <name> / <model>`. If you see `No AI provider configured`, set `AI_*` env vars or insert a row into `api_providers`.

**Many `FAILED`** — check `error_log` distribution:

```sql
SELECT LEFT(error_log, 80) AS err, COUNT(*)
FROM summaries WHERE processing_status = 'FAILED'
GROUP BY 1 ORDER BY 2 DESC;
```

Rate-limit errors clear themselves the next hour. Validation errors mean the provider returned shape that didn't match `AIAnalysis` — usually a prompt or model issue.

**Stuck in `PROCESSING`** — the `cleanup_stale` job resets these every hour. To force it:

```sql
UPDATE summaries
SET processing_status = 'RETRY', error_log = 'manual reset'
WHERE processing_status = 'PROCESSING'
  AND last_processed_at < NOW() - INTERVAL '30 minutes';
```

**No new messages arriving** — bot isn't in the channel, or the bot token is wrong. Verify with `curl https://api.telegram.org/bot<TOKEN>/getMe` and check the corresponding `channel_configs.is_active` and `channel_id` values.
