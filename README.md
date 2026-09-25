# Tele-Sum

AI-powered Telegram channel summarization pipeline. Tele-sum ingests messages from a configurable set of Telegram channels, summarizes the linked content with an LLM, and stores structured rows for search and downstream use.

The reference deployment (Sovereign Signal's Grant Wire) monitors channels such as:

| Wire | Telegram channel | Description |
|------|------------------|-------------|
| `crypto` | [@cryptograntwire](https://t.me/cryptograntwire) | Crypto/Web3 grants and governance |
| `ai`     | [@aigrantwire](https://t.me/aigrantwire)         | AI research grants and fellowships |
| `oss`    | [@ossgrantwire](https://t.me/ossgrantwire)       | Open source funding |

Any channels and wire labels can be configured for your own deployment.

## What it does

1. A Telegram bot (for example `@GrantWireScraper_BOT` in the reference deployment) sits in each channel and polls `getUpdates`. New posts are inserted into `messages`.
2. A scheduler picks up `PENDING` rows, scrapes any URLs (Twitter via FxTwitter, others via BeautifulSoup), and sends the whole thing to a configured LLM provider.
3. The provider response is validated against a Pydantic schema (`src/schemas/entities.py`) and written to `summaries` with `wire`, `category`, `source`, and extracted entities.
4. Each AI call writes one row to the shared `api_usage_log` table for telemetry. Each call is also rate-limited in process (default 100/hour).

The state machine is `PENDING -> PROCESSING -> SUCCESS | RETRY | FAILED`. Background jobs retry transient failures and reset stuck rows. See [PROCESSING.md](PROCESSING.md).

## Architecture

- **Runtime**: Python 3.12.7, FastAPI 0.115, SQLAlchemy 2.0, Pydantic 2.10
- **Ingest**: `python-telegram-bot` 21.9 (bot mode only — no phone auth)
- **AI**: Provider-agnostic `AIService` (`src/services/ai.py`). Supports Anthropic, Ollama Cloud, and any OpenAI-compatible chat-completions endpoint (Grok, Perplexity, OpenRouter, OpenAI, Groq, Together, ...). Currently running on Ollama Cloud with `glm-5.1`.
- **Extraction profiles**: each channel can use the `grant` profile (one post = one item) or the `digest` profile (one post = many items). See [docs/WIRES_AND_PROFILES.md](docs/WIRES_AND_PROFILES.md).
- **Storage**: PostgreSQL. Tele-sum owns 10 tables (see [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md)). The DB is shared with a sibling app — tele-sum reads `api_providers` and writes `api_usage_log`, otherwise stays in its own lane.
- **Deployment**: Railway (reference deployment: service `web` in project `glorious-prosperity`). See [RAILWAY_DEPLOYMENT.md](RAILWAY_DEPLOYMENT.md).

## Quickstart

1. **Postgres** — any instance. `export DATABASE_URL=postgresql://...`
2. **Telegram bot** — create one with @BotFather, add it as **admin** to each channel you want to capture. `export TELEGRAM_BOT_TOKENS=<token>`
3. **AI provider** — `export AI_PROVIDER=anthropic AI_API_KEY=...` (or `ollama`, or any OpenAI-compatible endpoint via `AI_BASE_URL`).
4. **Run** — `alembic upgrade head && uvicorn src.main:app`
5. **Add a channel** — insert a `channel_configs` row with the channel's numeric Telegram id (or `@username` as a placeholder — the bot will resolve it to the real id on the first incoming post). Pick a **wire** (any slug — your vertical label) and an **extraction profile**: `grant` (single-item funding posts) or `digest` (posts that list many items). See [docs/WIRES_AND_PROFILES.md](docs/WIRES_AND_PROFILES.md).
6. **Optional: Notion** — see [docs/NOTION_SYNC.md](docs/NOTION_SYNC.md) to land digest items in your own Notion databases.

Tele-sum runs standalone: the shared-table integrations some deployments use (`api_providers`, `seen_urls`, `distribution_logs`) are all optional and fail open when absent.

## Local dev

```bash
git clone https://github.com/SovereignSignal/tele-sum.git
cd tele-sum
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # then edit
alembic upgrade head
uvicorn src.main:app --reload
```

The web UI is at `http://localhost:8000` (login with `API_KEY`). Health at `/health`.

## Required environment variables

```bash
# Database
DATABASE_URL=postgresql://user:pass@host:port/db

# Telegram (bot mode only)
TELEGRAM_BOT_TOKENS=<bot_token>      # comma-separated if multiple
ENABLE_BOT_MODE=true

# App
API_KEY=<random_string>              # protects /api/* and the web UI
SECRET_KEY=<random_string>           # session cookie; auto-generated if unset
```

### AI provider

`AIService` resolves config in this order:
1. **Env vars** — `AI_PROVIDER` + `AI_API_KEY` (+ `AI_BASE_URL`, `AI_MODEL`). Used when both provider name and key are set.
2. **`api_providers` DB row** — fallback that looks up the provider name (`AI_PROVIDER`) in the shared table. Convenient if you want one source of truth across multiple services; in production we use env vars for clarity.

```bash
AI_PROVIDER=ollama                   # anthropic | ollama | grok | openai | perplexity | ...
AI_API_KEY=<provider_key>
AI_BASE_URL=https://ollama.com/api   # required for ollama / OpenAI-compat; omit for anthropic
AI_MODEL=glm-5.3-flash
AI_OLLAMA_THINK=low                 # boolean or supported level: low/medium/high/max
```

Use a thinking value supported by the selected model. GLM-5.3 Flash requires
`low`, `high`, or `max`; it does not support `false`. Extraction rejects links
not present in the supplied input and digest counts that contradict an explicit
source total. Rejected responses follow the existing bounded retry path. Source
types come from known URL hosts, with `other` for unknown types.
For grant posts with retrieved sources, the Telegram wrapper is excluded from
the model input. When retrieval fails, wrapper claims are explicitly unverified.
Publication dates come from tweet metadata or a webpage's `article:published_time`
metadata, never an inferred date from neighboring content. Missing dates stay null.

Optional tuning (defaults shown):

```bash
SYNC_INTERVAL_HOURS=6
MAX_AI_REQUESTS_PER_HOUR=100
MAX_PROCESSING_RETRIES=3
PROCESSING_BATCH_SIZE=10
RUN_SYNC_ON_STARTUP=true
BOT_POLLING_INTERVAL=2
APP_ENV=production
```

`MAX_AI_REQUESTS_PER_HOUR` is a rolling-hour cap shared by every TeleSum worker
using this database. A committed reservation precedes each inference request,
including failed calls. Restarts and retries cannot reset the counter. Set it to
`0` to pause inference. Exhausted or unavailable accounting leaves messages
queued without consuming processing attempts. Startup creates the small
`ai_request_reservations` table; PostgreSQL advisory locks serialize reservations.
This cap covers TeleSum, not other applications sharing `api_usage_log`.

Optional integrations:

```bash
NOTION_API_KEY=ntn_...               # enables Notion sync for digest channels
ENABLE_NOTION_SYNC=false            # disable Notion sync; retain PostgreSQL processing
```

## Operating

- **Manual sync**: `POST /api/trigger-sync` (auth required)
- **Manual processing pass**: `POST /api/trigger-process`
- **Stats**: `GET /api/stats` and `GET /api/entries/stats`
- **Production data**: filter by `processing_status = 'SUCCESS'` and `data_quality = 'complete'`. The `/api/entries` router does this for you.

```sql
SELECT s.id, s.title, s.summary, s.wire,
       s.entities->'source'->>'url' AS source_url,
       m.timestamp
FROM summaries s
JOIN messages m ON s.message_id = m.id
WHERE s.processing_status = 'SUCCESS'
  AND s.data_quality = 'complete'
ORDER BY m.timestamp DESC;
```

## Documentation

| Doc | Purpose |
|-----|---------|
| [CLAUDE.md](CLAUDE.md) | Authoritative architecture reference for AI assistants and contributors |
| [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md) | The tele-sum tables + cross-app integration points |
| [PROCESSING.md](PROCESSING.md) | State machine, scheduler jobs, troubleshooting |
| [docs/WIRES_AND_PROFILES.md](docs/WIRES_AND_PROFILES.md) | Wire slugs and extraction profiles (grant vs digest) |
| [docs/NOTION_SYNC.md](docs/NOTION_SYNC.md) | Notion integration for digest channels |
| [RAILWAY_DEPLOYMENT.md](RAILWAY_DEPLOYMENT.md) | Deployment topology and env vars |
| [VISION.md](VISION.md) | Product goals |

## License

MIT
