# CLAUDE.md — AI Assistant Guide for Tele-Sum

## What this is

**Tele-Sum** is a Telegram-channel → AI-summarizer → Postgres pipeline. Bot polls registered channels, each new message is summarized by an AI provider, the result is stored in `summaries`. Three "wires" (crypto, ai, oss) channel content into separate verticals.

## Architecture (as of the 2026-04 rationalization)

- **Bot mode only.** Phone/Telethon and "hybrid" modes were removed. Polling uses `python-telegram-bot` via `getUpdates`.
- **Provider-agnostic AI.** `src/services/ai.py` reads provider config from the shared `api_providers` Postgres table (anthropic / grok / perplexity / ollama / OpenAI-compatible) or from env overrides (`AI_PROVIDER` + `AI_API_KEY`). One row per provider; one source of truth across this app and its sibling.
- **Validated AI responses.** `src/schemas/entities.py` defines pydantic schemas (`AIAnalysis`, `EntitiesPayload`, `SourceInfo`) that every AI response must validate against before storage.
- **Telemetry to shared `api_usage_log`.** Each AI call writes one row (provider, model, tokens, wire, duration_ms, success). The legacy `grok_usage` table is read-only (historical rows preserved, no new writes).
- **In-process scheduler.** APScheduler with 5 jobs: sync (every N hours), process_pending (2 min), retry_failed (15 min), cleanup_stale (1 hour), daily_stats (midnight). It's load-bearing — don't disable it without replacing.

## Tech stack

- Python 3.12 (`runtime.txt`)
- FastAPI 0.115, Uvicorn 0.32
- SQLAlchemy 2.0, Alembic 1.14
- python-telegram-bot 21.9
- Pydantic 2.10, pydantic-settings 2.7
- httpx 0.28, BeautifulSoup4
- APScheduler 3.10
- Jinja2 (web UI for monitoring/login)
- Deployed on Railway

## Quick reference

```bash
# Local dev
uvicorn src.main:app --reload

# Production (Railway entrypoint)
uvicorn src.main:app --host 0.0.0.0 --port ${PORT:-8000}

# Migrations
alembic upgrade head

# Manual triggers (require auth)
curl -X POST $URL/api/trigger-sync    -H "X-API-Key: $API_KEY"
curl -X POST $URL/api/trigger-process -H "X-API-Key: $API_KEY"
```

## Layout

```
src/
├── main.py                # FastAPI app, lifespan, auth (session + X-API-Key)
├── config.py              # Pydantic Settings — env-driven config
├── api/
│   ├── messages.py        # Message search
│   ├── categories.py      # Category CRUD
│   ├── entries.py         # Production data API (public interface)
│   ├── exports.py         # CSV/JSON/RSS exports
│   ├── channels.py        # Multi-channel + wire filtering
│   └── config.py          # /api/config/status, /api/config/health/db
├── services/
│   ├── ai.py              # Provider-agnostic AI service
│   ├── processor.py       # Message processing state machine
│   ├── scheduler.py       # APScheduler job orchestration
│   └── telegram_bot.py    # python-telegram-bot polling
├── schemas/
│   └── entities.py        # Pydantic models for AI response + entities JSONB
├── scrapers/              # ContentScraper, TwitterScraper
├── models/
│   ├── database.py        # Engine, session factory, health
│   └── models.py          # All ORM models
└── utils/
    └── url_extractor.py
templates/                 # base, index, login, categories, settings
migrations/versions/       # 3 alembic migrations (head: backfill_protocols_to_orgs)
```

## Database — tele-sum's tables (own this app's lane)

| Table | Purpose |
|-------|---------|
| `messages` | Raw Telegram messages, extracted URLs, wire |
| `summaries` | AI title/summary, category, confidence, validated entities JSON, processing state |
| `categories` | User-defined classification categories |
| `users` | Multi-tenant placeholder (single user today) |
| `telegram_bots` | Bot tokens & polling offsets |
| `channel_configs` | Per-channel settings (wire, bot, active, last imported) |
| `sync_status` | Per-channel sync bookkeeping |
| `review_queue` | Manual URL-correction queue |
| `grok_usage` | **Historical** — read-only; new writes go to `api_usage_log` |

Other tables on the same Postgres instance (e.g. `api_providers`, `api_usage_log`, `model_pricing`, `feeds`, `monitored_accounts`, `scan_*`, `org_profiles`, `grant_entries`, etc.) are owned by **the sibling app**. Tele-sum reads from `api_providers` and writes to `api_usage_log`; otherwise stays out of those lanes.

### Key enums

```python
class ProcessingStatus(enum.Enum):
    PENDING, PROCESSING, SUCCESS, RETRY, FAILED

class DataQuality(enum.Enum):
    COMPLETE, INCOMPLETE   # filter to COMPLETE for production queries

class ConfidenceLevel(enum.Enum):
    CONFIDENT, UNCERTAIN

class Wire(enum.Enum):
    crypto, ai, oss

class ImportMode(enum.Enum):
    PHONE, BOT, HYBRID   # legacy enum — only BOT is used; PHONE/HYBRID kept for old rows
```

### Production query pattern

Always filter on `data_quality = 'complete'` and `processing_status = 'SUCCESS'` for clean data. Prefer reading `entities->>'orgs'` (the `protocols` key was migrated and removed).

## Environment variables

### Required
```bash
DATABASE_URL=postgresql://...    # or PG* set by Railway
TELEGRAM_BOT_TOKENS=token1,token2
```

### Recommended
```bash
API_KEY=...                      # protects /api/* and the web UI
SECRET_KEY=...                   # session cookie signing
```

### AI provider — defaults read from `api_providers` DB row; env overrides:
```bash
AI_PROVIDER=grok                 # row name in api_providers (anthropic/grok/perplexity/ollama/...)
AI_API_KEY=...                   # override the DB-stored key
AI_BASE_URL=https://api.x.ai/v1  # override base URL
AI_MODEL=grok-3                  # override default model
```

### Tuning
```bash
SYNC_INTERVAL_HOURS=6
MAX_AI_REQUESTS_PER_HOUR=100
MAX_PROCESSING_RETRIES=3
PROCESSING_BATCH_SIZE=10
RUN_SYNC_ON_STARTUP=true
ENABLE_BOT_MODE=true
BOT_POLLING_INTERVAL=2
APP_ENV=production
```

## Conventions

- snake_case functions, PascalCase classes, UPPER_SNAKE constants, snake_case tables.
- Type hints throughout. FastAPI routes async. External calls via httpx async.
- Logging: `logger.info("✅ ...")`, `logger.warning("⚠️ ...")`, `logger.error("❌ ...")`.
- All AI responses MUST round-trip through `AIAnalysis` (`src/schemas/entities.py`) — don't take the dict directly.

## API routes

| Prefix | Auth | Purpose |
|--------|------|---------|
| `/health`, `/login`, `/logout` | public | health + auth |
| `/api/messages` | required | message search |
| `/api/categories` | required | category CRUD |
| `/api/entries` | required | production data API |
| `/api/export` | required | CSV/JSON/RSS exports |
| `/api/channels` | required | channel listing + wire filtering |
| `/api/config` | required | configuration status, DB health |
| `/api/stats`, `/api/trigger-sync`, `/api/trigger-process` | required | ops |

## Notes for AI assistants

1. **Filter production queries by `data_quality = 'complete'` AND `processing_status = 'SUCCESS'`.**
2. Provider config lives in the `api_providers` DB row by default. Don't hardcode keys.
3. AI responses must validate against `AIAnalysis` — if validation fails, return None and let the state machine retry.
4. Don't write to sibling-app tables (anything outside the 9 listed under "tele-sum's tables").
5. Telemetry: every AI call writes to `api_usage_log`. The `wire` column is critical for cross-app cost analysis.
6. State machine transitions live in `src/services/processor.py` — preserve PENDING → PROCESSING → SUCCESS/RETRY/FAILED semantics.
