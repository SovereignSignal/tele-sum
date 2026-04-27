# Tele-Sum — Grant Wire Archive

AI-powered Telegram channel summarization for the Sovereign Signal Grant Wire properties. Tele-Sum ingests messages from a small set of curated Telegram channels, summarizes the linked content with an LLM, and stores structured rows for search and downstream use.

| Wire | Telegram channel | Description |
|------|------------------|-------------|
| `crypto` | [@cryptograntwire](https://t.me/cryptograntwire) | Crypto/Web3 grants and governance |
| `ai`     | [@aigrantwire](https://t.me/aigrantwire)         | AI research grants and fellowships |
| `oss`    | [@ossgrantwire](https://t.me/ossgrantwire)       | Open source funding |

## What it does

1. A Telegram bot (`@GrantWireScraper_BOT`) sits in each channel and polls `getUpdates`. New posts are inserted into `messages`.
2. A scheduler picks up `PENDING` rows, scrapes any URLs (Twitter via FxTwitter, others via BeautifulSoup), and sends the whole thing to a configured LLM provider.
3. The provider response is validated against a Pydantic schema (`src/schemas/entities.py`) and written to `summaries` with `wire`, `category`, `source`, and extracted entities.
4. Each AI call writes one row to the shared `api_usage_log` table for telemetry. Each call is also rate-limited in process (default 100/hour).

The state machine is `PENDING -> PROCESSING -> SUCCESS | RETRY | FAILED`. Background jobs retry transient failures and reset stuck rows. See [PROCESSING.md](PROCESSING.md).

## Architecture

- **Runtime**: Python 3.12.7, FastAPI 0.115, SQLAlchemy 2.0, Pydantic 2.10
- **Ingest**: `python-telegram-bot` 21.9 (bot mode only — no phone auth)
- **AI**: Provider-agnostic `AIService` (`src/services/ai.py`). Supports Anthropic, Ollama Cloud, and any OpenAI-compatible chat-completions endpoint (Grok, Perplexity, OpenRouter, OpenAI, Groq, Together, ...). Currently running on Ollama Cloud with `glm-5.1`.
- **Storage**: PostgreSQL. Tele-sum owns 9 tables (see [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md)). The DB is shared with a sibling app — tele-sum reads `api_providers` and writes `api_usage_log`, otherwise stays in its own lane.
- **Deployment**: Railway. Service `web` in project `glorious-prosperity`. See [RAILWAY_DEPLOYMENT.md](RAILWAY_DEPLOYMENT.md).

## Quick start (local)

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
AI_MODEL=glm-5.1
```

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
| [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md) | The 9 tele-sum tables + cross-app integration points |
| [PROCESSING.md](PROCESSING.md) | State machine, scheduler jobs, troubleshooting |
| [RAILWAY_DEPLOYMENT.md](RAILWAY_DEPLOYMENT.md) | Deployment topology and env vars |
| [VISION.md](VISION.md) | Product goals |

## License

MIT
