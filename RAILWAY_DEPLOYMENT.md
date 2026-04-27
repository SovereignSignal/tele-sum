# Railway Deployment

Tele-sum runs on Railway. The web service and the Postgres database live in **separate** Railway projects, which matters for how `DATABASE_URL` is configured.

## Topology

| Component | Railway project | Service | Notes |
|-----------|-----------------|---------|-------|
| App | `glorious-prosperity` | `web` | Autodeploy from `main` on GitHub |
| Postgres | `wire-room-db` | shared with sibling app | Tele-sum reads `api_providers`, writes `api_usage_log`; otherwise stays in its own 9 tables |

Public URL: `https://web-production-82642.up.railway.app`

Because the DB is in a different Railway project, the in-cluster private DNS does not resolve. `DATABASE_URL` must use the public TCP proxy address (currently `<railway-db-proxy>`). Get the exact value from the Postgres service in the `wire-room-db` project under "Connect → Public Network → DATABASE_PUBLIC_URL".

## Build & start

Defined in [`railway.json`](railway.json):

- Builder: `NIXPACKS`
- Build: `pip install -r requirements.txt`
- Start: `uvicorn src.main:app --host 0.0.0.0 --port ${PORT:-8000}`
- Healthcheck: `GET /health`, 30s timeout
- Restart: `ON_FAILURE`, max 10 retries

Python 3.12.7 is pinned in [`runtime.txt`](runtime.txt).

## Required env vars

Set these on the `web` service (Variables tab):

```bash
# Database — point at the wire-room-db public proxy
DATABASE_URL=postgresql://postgres:REDACTED@<railway-db-proxy>/railway

# Telegram bot
TELEGRAM_BOT_TOKENS=<bot_token>          # comma-separate if you run multiple bots
ENABLE_BOT_MODE=true

# Auth
API_KEY=<random_string>                  # protects /api/* and the web UI
SECRET_KEY=<random_string>               # session cookie signing
```

## Optional env vars

```bash
APP_ENV=production            # default
SYNC_INTERVAL_HOURS=6
MAX_AI_REQUESTS_PER_HOUR=100
MAX_PROCESSING_RETRIES=3
PROCESSING_BATCH_SIZE=10
RUN_SYNC_ON_STARTUP=true
BOT_POLLING_INTERVAL=2
WEBHOOK_BASE_URL=             # unused — bot runs in long-poll mode
```

### AI provider override (optional)

By default tele-sum reads provider config from the shared `api_providers` table on app boot (the sibling app owns and rotates this row). Override per-deployment with:

```bash
AI_PROVIDER=ollama            # anthropic | ollama | grok | openai | perplexity | openrouter | ...
AI_API_KEY=<provider_key>
AI_BASE_URL=https://ollama.com/api  # required for ollama and OpenAI-compat; omit for anthropic
AI_MODEL=glm-5.1
```

The currently-running config is Ollama Cloud with `glm-5.1`, sourced from `api_providers`.

## Deploy flow

1. Push to `main` on GitHub (`SovereignSignal/tele-sum`).
2. Railway autodeploys the `web` service.
3. On boot, `lifespan()` in [`src/main.py:103`](src/main.py:103) creates any missing tables (`Base.metadata.create_all`), seeds default categories if the table is empty, and starts the bot polling + APScheduler. Watch logs for the configuration banner — it tells you in one screen what's wired up.

To run Alembic manually (rare; the app does `create_all` on boot):

```bash
railway run alembic upgrade head    # from the `web` service in glorious-prosperity
```

## Health & monitoring

- `GET /health` — Railway healthcheck. Returns scheduler status.
- `GET /api/config/status` — auth-protected; reports DB / bot / AI provider state.
- `GET /api/stats` — auth-protected; counts by `processing_status`.

Application logs include a startup banner with `Database / API Key / Telegram Bot / AI provider` resolution status. If anything is `❌`, fix the corresponding env var or DB row.

## Troubleshooting

**App boots but no messages arrive** — bot token wrong, or bot isn't in the channel as an admin. Verify `channel_configs.is_active` and `channel_id` match the actual Telegram chat id.

**`AI provider: will resolve from api_providers table on first call`** is normal. `⚠️  No AI provider configured` after the first AI call means neither path resolved — check `api_providers` has an enabled row (excluding `firecrawl`/`resend`) or set `AI_PROVIDER` + `AI_API_KEY`.

**Database connection failures** — confirm `DATABASE_URL` is the **public** proxy URL from the `wire-room-db` project, not an internal `*.railway.internal` host. Internal DNS doesn't cross Railway projects.
