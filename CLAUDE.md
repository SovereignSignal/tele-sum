# CLAUDE.md - AI Assistant Guide for Tele-Sum

This document provides context for AI assistants working on the Tele-Sum codebase.

## Project Overview

**Tele-Sum** (Grant Wire Archive) is an AI-powered Telegram channel summarization system that transforms message streams into structured, searchable data using Grok AI. Built for [The Grant Wire](https://t.me/thegrantwire), it aggregates crypto grants, governance proposals, and funding opportunities.

### What It Does

1. **Imports messages** from Telegram channels (phone auth for historical, bot for ongoing)
2. **Summarizes content** using Grok AI (reads linked content including X/Twitter posts)
3. **Categorizes** into user-defined categories with AI classification
4. **Extracts entities** like protocols, amounts, dates, and key terms
5. **Stores in PostgreSQL** for full-text search and analysis
6. **Filters by quality** - production queries exclude incomplete entries

## Tech Stack

- **Framework**: FastAPI 0.104.1 with Uvicorn
- **Database**: PostgreSQL with SQLAlchemy 2.0 ORM
- **Telegram**: Telethon (phone client) + python-telegram-bot (bot polling)
- **AI**: Grok API (x.ai) for content analysis
- **Background Jobs**: APScheduler with AsyncIOScheduler
- **Configuration**: Pydantic Settings with environment variables
- **Templates**: Jinja2 for web UI
- **Deployment**: Railway with NIXPACKS

## Quick Reference

### Running the Application

```bash
# Start web server (development)
python -m uvicorn src.main:app --reload

# Start web server (production)
uvicorn src.main:app --host 0.0.0.0 --port ${PORT:-8000}

# Process messages
python process_batch.py --count 50      # Sync + process 50 messages
python process_batch.py --count 50 --no-sync  # Process only (no sync)
python sync_new.py                      # Sync only (no processing)

# Resilient batch processing with auto-retry
python batch_runner.py 1001 1100 25     # <start_id> <end_id> [chunk_size]

# Run tests
pytest test_*.py
```

### Key Commands for Development

```bash
# Database migrations
python -m alembic upgrade head

# Check database health
curl http://localhost:8000/health

# Trigger manual sync (requires auth)
curl -X POST http://localhost:8000/api/trigger-sync -H "X-API-Key: YOUR_KEY"
```

## Directory Structure

```
tele-sum/
├── src/                           # Main application code
│   ├── main.py                    # FastAPI app entry point, lifespan, auth
│   ├── config.py                  # Pydantic Settings configuration
│   ├── api/                       # Route handlers
│   │   ├── messages.py            # Message search/retrieval
│   │   ├── categories.py          # Category CRUD
│   │   ├── entries.py             # Production API (public interface)
│   │   ├── imports.py             # Telegram import endpoints
│   │   ├── exports.py             # CSV/JSON/RSS exports
│   │   ├── config.py              # Configuration status
│   │   └── hybrid.py              # Bot/phone hybrid mode
│   ├── services/                  # Business logic
│   │   ├── grok.py                # Grok AI integration
│   │   ├── processor.py           # Message processing pipeline
│   │   ├── scheduler.py           # Background job scheduler
│   │   ├── telegram.py            # Telethon phone client
│   │   ├── telegram_bot.py        # python-telegram-bot client
│   │   └── telegram_hybrid.py     # Hybrid mode support
│   ├── models/                    # Database layer
│   │   ├── database.py            # Engine, sessions, health checks
│   │   └── models.py              # SQLAlchemy ORM models
│   ├── scrapers/                  # Content fetching
│   │   ├── content_scraper.py     # HTML scraper (BeautifulSoup)
│   │   └── twitter_scraper.py     # Twitter/X content
│   └── utils/
│       └── url_extractor.py       # URL parsing utilities
├── templates/                     # Jinja2 HTML templates
├── static/                        # CSS, JS assets
├── migrations/                    # Alembic migrations
├── process_batch.py               # Main batch processing script
├── batch_runner.py                # Resilient batch runner
├── sync_new.py                    # Telegram sync only
├── backfill_*.py                  # Data maintenance scripts
└── test_*.py                      # Test files
```

## Database Models

### Core Tables

| Table | Purpose |
|-------|---------|
| `messages` | Raw Telegram messages with extracted URLs |
| `summaries` | AI-generated summaries, categories, entities |
| `categories` | User-defined classification categories |
| `users` | Multi-tenant user support |

### Supporting Tables

| Table | Purpose |
|-------|---------|
| `telegram_bots` | Bot token configuration |
| `channel_configs` | Channel import settings |
| `sync_status` | Telegram sync tracking |
| `review_queue` | Manual URL correction queue |
| `grok_usage` | API usage monitoring |

### Key Enums

```python
class ProcessingStatus(enum.Enum):
    PENDING = "pending"      # Not yet processed
    PROCESSING = "processing"  # Currently being processed
    SUCCESS = "success"      # Successfully completed
    RETRY = "retry"          # Failed, will retry
    FAILED = "failed"        # Max retries exceeded

class DataQuality(enum.Enum):
    COMPLETE = "complete"    # Production-ready
    INCOMPLETE = "incomplete"  # Missing source URL
```

### Production Query Pattern

Always filter by `data_quality = 'complete'` for production use:

```sql
SELECT s.id, s.title, s.summary,
       s.entities->'source'->>'url' as source_url
FROM summaries s
JOIN messages m ON s.message_id = m.id
WHERE s.processing_status = 'SUCCESS'
  AND s.data_quality = 'complete'
ORDER BY m.timestamp DESC;
```

## Architecture Patterns

### Service Layer Pattern

Business logic is separated into services (`src/services/`). Controllers call services, not directly accessing data.

### State Machine for Processing

```
PENDING → PROCESSING → SUCCESS
                    ↘ RETRY (on failure, if attempts < max)
                    ↘ FAILED (if attempts >= max)
```

### Graceful Degradation

External services are optional. The system continues functioning (at reduced capacity) when:
- Grok AI unavailable → Messages stored without summaries
- Telegram unavailable → Read-only mode with existing data
- Bot mode disabled → Phone mode only (and vice versa)

### Authentication

```python
# Session-based (browser)
request.session.get("authenticated")

# API key header (programmatic)
request.headers.get("X-API-Key")

# Query parameter (simple access)
request.query_params.get("api_key")
```

## Environment Variables

### Required

```bash
DATABASE_URL=postgresql://user:pass@host:port/db
# OR Railway PostgreSQL variables: PGHOST, PGPORT, PGUSER, PGPASSWORD, PGDATABASE
```

### Telegram (Phone Mode)

```bash
TELEGRAM_API_ID=your_api_id
TELEGRAM_API_HASH=your_api_hash
TELEGRAM_PHONE=+1234567890
TELEGRAM_CHANNEL_USERNAME=your_channel
```

### Telegram (Bot Mode)

```bash
TELEGRAM_BOT_TOKENS=token1,token2  # Comma-separated
ENABLE_BOT_MODE=true
BOT_POLLING_INTERVAL=2
```

### Grok AI

```bash
GROK_API_KEY=your_grok_api_key
GROK_API_URL=https://api.x.ai/v1
GROK_MODEL=grok-3
```

### Application

```bash
APP_ENV=production              # or development
API_KEY=your_api_key           # Protects endpoints
SECRET_KEY=your_secret_key     # Auto-generated if not set
PORT=8000
```

### Processing

```bash
SYNC_INTERVAL_HOURS=6
MAX_GROK_REQUESTS_PER_HOUR=100
MAX_PROCESSING_RETRIES=3
PROCESSING_BATCH_SIZE=10
```

## Code Conventions

### Naming

- **Functions**: `snake_case` (e.g., `extract_urls`, `sync_and_process`)
- **Classes**: `PascalCase` (e.g., `MessageProcessor`, `GrokService`)
- **Constants**: `UPPER_SNAKE_CASE` (e.g., `ProcessingStatus.PENDING`)
- **Routes**: kebab-case URLs (e.g., `/api/messages`, `/api/trigger-sync`)
- **Database**: snake_case tables, `_id` suffix for foreign keys

### Type Hints

Extensive use of type hints throughout:

```python
async def process_message(message_id: int, db: Session) -> Optional[Summary]:
    ...
```

### Async Patterns

- FastAPI routes are async
- APScheduler uses AsyncIOScheduler
- External API calls use httpx (async)

### Error Handling

- Try-except with logging
- HTTP exceptions in routes (HTTPException)
- State machine prevents invalid transitions
- Retry logic with exponential backoff for external services

### Logging

```python
import logging
logger = logging.getLogger(__name__)

logger.info("✅ Success message")
logger.warning("⚠️  Warning message")
logger.error("❌ Error message")
```

## API Endpoints

### Public

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Health check for Railway |
| GET | `/login` | Login page |
| POST | `/login` | Login form submission |
| GET | `/logout` | Logout |

### Protected (require auth)

| Prefix | Router | Purpose |
|--------|--------|---------|
| `/api/messages` | messages.py | Message search/retrieval |
| `/api/categories` | categories.py | Category CRUD |
| `/api/entries` | entries.py | Production data API |
| `/api/import` | imports.py | Telegram imports |
| `/api/export` | exports.py | CSV/JSON/RSS exports |
| `/api/config` | config.py | Configuration status |
| `/api/hybrid` | hybrid.py | Bot management |
| `/api/stats` | main.py | Processing statistics |
| `/api/trigger-sync` | main.py | Manual sync trigger |
| `/api/trigger-process` | main.py | Manual process trigger |

## Processing Pipeline

1. **URL Detection** - Extract URLs from Telegram message
2. **Content Fetching** - Scrape linked page content (BeautifulSoup)
3. **Grok Analysis** - Send message + content + categories to Grok
4. **Response Parsing** - Extract summary, category, confidence, entities
5. **Storage** - Save to PostgreSQL with quality flags

### Grok Response Format

```json
{
  "title": "Short 4-5 word title",
  "summary": "3-4 sentence factual summary...",
  "category": "Grant Programs",
  "confidence": "confident",
  "source": {
    "name": "Optimism Governance Forum",
    "type": "forum",
    "url": "https://gov.optimism.io/..."
  },
  "entities": {
    "protocols": ["Optimism"],
    "amounts": ["$5M", "100,000 OP"],
    "key_terms": ["retroactive funding"]
  }
}
```

## Common Development Tasks

### Adding a New API Endpoint

1. Create route in appropriate `src/api/*.py` file
2. Add to router with proper dependencies
3. Include authentication: `dependencies=[Depends(auth_required)]`

### Adding a New Database Model

1. Define model in `src/models/models.py`
2. Add relationships and indexes
3. Run `alembic revision --autogenerate -m "description"`
4. Apply: `alembic upgrade head`

### Modifying Processing Logic

1. Update `src/services/processor.py`
2. Consider state machine transitions
3. Update Grok prompts in `src/services/grok.py` if needed

### Adding a New Category

Categories are user-defined and stored in database. Default categories are seeded on startup in `src/main.py`.

## Testing

```bash
# Run all tests
pytest test_*.py

# Run specific test
pytest test_url_discovery.py -v
```

Test files are in the root directory:
- `test_pydantic_settings.py` - Configuration loading
- `test_railway_compatibility.py` - Deployment compatibility
- `test_url_discovery.py` - URL extraction
- `test_url_search.py` - URL search functionality

## Deployment (Railway)

### Build & Start

Defined in `railway.json`:
- Build: `pip install -r requirements.txt`
- Start: `uvicorn src.main:app --host 0.0.0.0 --port ${PORT:-8000}`

### Health Check

Railway monitors `/health` endpoint for container health.

### Database

Railway auto-provisions PostgreSQL. Connection via `DATABASE_URL` or individual `PG*` environment variables.

## Documentation Files

| File | Description |
|------|-------------|
| [README.md](README.md) | Quick start and overview |
| [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md) | Complete database reference |
| [PROCESSING.md](PROCESSING.md) | Processing pipeline details |
| [DATA_SOURCES.md](DATA_SOURCES.md) | Data import methods |
| [SCRIPTS.md](SCRIPTS.md) | Utility script documentation |
| [RAILWAY_DEPLOYMENT.md](RAILWAY_DEPLOYMENT.md) | Production deployment guide |
| [HYBRID_SETUP.md](HYBRID_SETUP.md) | Bot + phone hybrid setup |
| [PROJECT_OVERVIEW.md](PROJECT_OVERVIEW.md) | Technical architecture |
| [VISION.md](VISION.md) | Project goals |

## Important Notes for AI Assistants

1. **Always filter production data** by `data_quality = 'complete'`
2. **Use the Settings class** via `from src.config import settings`
3. **Check service availability** before using (e.g., `settings.is_grok_configured()`)
4. **Follow state machine transitions** in processor - don't skip states
5. **Handle database sessions properly** - always close or use context managers
6. **Maintain backward compatibility** with existing API responses
7. **Log with appropriate severity** - INFO for success, WARNING for degraded, ERROR for failures
8. **Respect rate limits** - Grok API has hourly limits (~100 requests)
9. **Use async patterns** for external API calls
10. **Keep the graceful degradation pattern** - features should work independently
