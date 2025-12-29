# Grant Wire Archive

AI-powered Telegram channel summarization system that transforms message streams into structured, searchable data using Grok AI. Built for [The Grant Wire](https://t.me/thegrantwire), a curated channel aggregating crypto grants, governance proposals, and funding opportunities.

## Current Status

| Metric | Value |
|--------|-------|
| Total Entries | 3,069 |
| Production Ready | 3,069 (100%) |
| Quality Score | 100% acceptable (95% excellent) |
| Average Summary | 404 characters |

## What This Does

Tele-Sum solves the problem of Telegram channels being write-only archives where valuable links and content get buried:

- **Imports messages** from any Telegram channel (full historical + ongoing sync)
- **Summarizes content** using Grok AI (reads linked content including X/Twitter posts)
- **Categorizes** into user-defined categories
- **Extracts entities** like protocols, amounts, dates, and key terms
- **Stores everything** in PostgreSQL for search and analysis
- **Filters by quality** - production queries exclude incomplete entries

### Categories

- Governance & Treasury (1,210)
- Funding Opportunities (691)
- Grant Programs (507)
- Research & Analysis (305)
- Tools & Infrastructure (222)
- Incentives (134)

## Quick Start

### Prerequisites

- Python 3.9+
- PostgreSQL database
- Telegram API credentials
- Grok API key (from x.ai)

### Setup

```bash
git clone https://github.com/yourusername/tele-sum.git
cd tele-sum
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your credentials
```

### Environment Variables

```bash
# Database
DATABASE_URL=postgresql://user:pass@host:port/db

# Telegram
TELEGRAM_API_ID=your_api_id
TELEGRAM_API_HASH=your_api_hash
TELEGRAM_PHONE=+1234567890
TELEGRAM_CHANNEL_USERNAME=your_channel

# Grok AI
GROK_API_KEY=your_grok_api_key
GROK_MODEL=grok-3
```

## Daily Workflow

### Sync & Process New Messages

```bash
# Sync + process next batch
python3 process_batch.py --count 50

# Just sync (no processing)
python3 sync_new.py

# Process without syncing
python3 process_batch.py --count 50 --no-sync
```

### Resilient Batch Processing

For large batches with auto-retry on timeouts:

```bash
python3 batch_runner.py 1001 1100 25
# Arguments: <start_id> <end_id> [chunk_size]
```

## Using the Data

### Production Queries

Always filter by `data_quality = 'complete'` for production use:

```sql
SELECT s.id, s.title, s.summary,
       s.entities->'source'->>'url' as source_url,
       m.timestamp
FROM summaries s
JOIN messages m ON s.message_id = m.id
WHERE s.processing_status = 'SUCCESS'
  AND s.data_quality = 'complete'
ORDER BY m.timestamp DESC;
```

### Data Quality Filtering

| Quality | Count | Use Case |
|---------|-------|----------|
| `complete` | 3,069 | Production - verified source URLs |
| `incomplete` | 0 | Review queue - missing/unverified URLs |

See [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md) for complete query reference.

## Project Structure

```
tele-sum/
├── src/
│   ├── config.py              # Settings and environment
│   ├── main.py                # FastAPI application
│   ├── models/
│   │   ├── database.py        # Database connection
│   │   └── models.py          # SQLAlchemy models
│   ├── services/
│   │   ├── grok.py            # Grok AI integration
│   │   ├── processor.py       # Message processing pipeline
│   │   ├── telegram.py        # Telegram phone client
│   │   └── telegram_bot.py    # Telegram bot client
│   ├── scrapers/
│   │   ├── content_scraper.py # URL content fetching
│   │   └── twitter_scraper.py # Twitter/X scraping
│   └── utils/
│       └── url_extractor.py   # URL parsing utilities
├── process_batch.py           # Main batch processing
├── batch_runner.py            # Resilient batch runner
├── sync_new.py                # Telegram sync only
├── backfill_source_urls.py    # URL backfill for x23 entries
├── populate_review_queue.py   # Review queue management
├── extract_x23_urls.py        # Extract x23.ai URLs
└── reconstruct_urls.py        # Reconstruct forum URLs
```

See [SCRIPTS.md](SCRIPTS.md) for documentation on all utility scripts.

## Documentation

| Document | Description |
|----------|-------------|
| [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md) | Complete database schema and query reference |
| [DATA_SOURCES.md](DATA_SOURCES.md) | Data sources, import methods, URL coverage |
| [SCRIPTS.md](SCRIPTS.md) | Utility scripts documentation |
| [PROCESSING.md](PROCESSING.md) | Batch processing, retry logic, scheduler |
| [PROJECT_OVERVIEW.md](PROJECT_OVERVIEW.md) | Technical architecture |
| [VISION.md](VISION.md) | Project goals and purpose |
| [RAILWAY_DEPLOYMENT.md](RAILWAY_DEPLOYMENT.md) | Production deployment guide |
| [HYBRID_SETUP.md](HYBRID_SETUP.md) | Bot + Phone hybrid setup |

## How Grok Processes Content

Each message goes through this pipeline:

1. **URL Detection** - Extract URLs from Telegram message
2. **Content Fetching** - For non-Twitter URLs, scrape the page content
3. **Grok Analysis** - Send to Grok with message + scraped content + category list
4. **Response Parsing** - Extract summary, category, confidence, entities
5. **Storage** - Save to PostgreSQL with source metadata and quality flags

### What Grok Returns

```json
{
  "title": "Optimism Grants Council Results",
  "summary": "3-4 sentence factual summary of the content...",
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
    "key_terms": ["retroactive funding", "public goods"]
  }
}
```

## Rate Limits

- **Grok API**: ~100 requests/hour (depends on tier)
- **Telegram**: No significant limits for reading

When you hit the rate limit, messages are marked as "retry" status. Wait ~1 hour and run again.

## Production Deployment (Railway)

1. Fork this repo
2. Connect to Railway
3. Add PostgreSQL service
4. Configure environment variables
5. Auto-syncs every 6 hours

See [RAILWAY_DEPLOYMENT.md](RAILWAY_DEPLOYMENT.md) for full guide.

## License

MIT
