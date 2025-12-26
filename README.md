# Tele-Sum

AI-powered Telegram channel summarization system. Transforms message streams into structured, searchable data using Grok AI.

## 🎯 What This Does

Tele-Sum solves the problem of Telegram channels being write-only archives where valuable links and content get buried. The system:

- **Imports messages** from any Telegram channel (full historical + ongoing sync)
- **Summarizes content** using Grok AI (reads linked content including X/Twitter posts)
- **Categorizes** into user-defined categories
- **Extracts entities** like names, amounts, dates, and key terms
- **Stores everything** in PostgreSQL for search and analysis

### Example Use Case: The Grant Wire

This system was built for **The Grant Wire**, a curated Telegram channel aggregating crypto grants, governance proposals, and funding opportunities. Categories include:

- Governance & Treasury
- Grant Programs
- Funding Opportunities
- Incentives
- Research & Analysis
- Tools & Infrastructure

## 🚀 Quick Start

### Prerequisites

- Python 3.9+
- PostgreSQL database (Railway or local)
- Telegram API credentials
- Grok API key (from x.ai)

### Setup

1. **Clone and install:**

   ```bash
   git clone https://github.com/yourusername/tele-sum.git
   cd tele-sum
   pip install -r requirements.txt
   ```

2. **Configure environment:**

   ```bash
   cp .env.example .env
   # Edit .env with your credentials
   ```

3. **Required environment variables:**

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
   GROK_MODEL=grok-4-fast-reasoning  # or grok-3
   ```

## 📝 Daily Workflow

### Sync & Process New Messages

```bash
# Sync new Telegram messages + process next batch
python3 process_batch.py --count 50

# Just sync (no processing)
python3 sync_new.py

# Process specific range
python3 process_batch.py --start 146 --count 100

# Skip sync, just process
python3 process_batch.py --count 50 --no-sync
```

The `process_batch.py` script automatically:

1. Syncs new messages from Telegram
2. Finds the first unprocessed message (by ID)
3. Processes sequentially so you can watch in real-time
4. Handles rate limits gracefully (100 requests/hour on Grok)

### Resilient Batch Processing

For large batch processing with automatic retry on database timeouts:

```bash
# Process 100 messages with auto-retry (recommended for bulk processing)
python3 batch_runner.py 1001 1100 25

# Arguments: <start_id> <end_id> [chunk_size]
# - Processes in chunks of 25 (default)
# - Auto-retries on database timeouts
# - Continues from where it left off
```

The `batch_runner.py` script handles Railway database connection timeouts gracefully by:

- Breaking large batches into smaller chunks
- Auto-detecting next unprocessed message
- Retrying failed chunks up to 3 times
- Skipping and continuing on persistent failures

### View Results

Use **TablePlus** (or any PostgreSQL client) to view results:

```sql
-- View processed summaries
SELECT m.id, s.summary, c.name as category,
       s.entities->'source'->>'name' as source,
       s.entities->'source'->>'url' as url
FROM messages m
JOIN summaries s ON s.message_id = m.id
LEFT JOIN categories c ON c.id = s.category_id
WHERE s.processing_status = 'SUCCESS'
ORDER BY m.id;

-- Check processing status
SELECT processing_status, COUNT(*)
FROM summaries
GROUP BY processing_status;
```

## 🤖 How Grok Processes Content

Each message goes through this pipeline:

1. **URL Detection** - Extract URLs from Telegram message
2. **Content Fetching** - For non-Twitter URLs, scrape the page content
3. **Grok Analysis** - Send to Grok with:
   - The message content
   - Scraped URL content (or Twitter URL for Grok to read directly)
   - Category list for classification
4. **Response Parsing** - Extract summary, category, confidence, entities
5. **Storage** - Save to PostgreSQL with source metadata

### What Grok Returns

```json
{
  "summary": "3-4 sentence factual summary",
  "category": "Grant Programs",
  "confidence": "confident",
  "source": {
    "name": "Optimism Governance Forum",
    "type": "forum",
    "original_date": "2024-01-15",
    "url": "https://gov.optimism.io/..."
  },
  "entities": {
    "protocols": ["Optimism", "Uniswap"],
    "amounts": ["$5M", "100,000 OP"],
    "deadlines": ["2024-02-01"],
    "key_terms": ["retroactive funding", "public goods"]
  }
}
```

## 📁 Project Structure

```text
tele-sum/
├── src/
│   ├── config.py           # Settings and environment
│   ├── models/
│   │   ├── database.py     # Database connection
│   │   └── models.py       # SQLAlchemy models
│   ├── services/
│   │   ├── grok.py         # Grok AI integration
│   │   └── processor.py    # Message processing pipeline
│   ├── scrapers/
│   │   └── content_scraper.py  # URL content fetching
│   └── scripts/
│       └── initial_import.py   # Full historical import
├── process_batch.py        # Main batch processing script
├── batch_runner.py         # Resilient batch runner with auto-retry
├── sync_new.py             # Telegram sync only
└── .env                    # Environment configuration
```

## ⚡ Rate Limits

- **Grok API**: ~100 requests/hour (depends on your tier)
- **Telegram**: No significant limits for reading

When you hit the Grok rate limit, the script will mark messages as "retry" status. Wait ~1 hour and run again.

## 🗄️ Database Schema

**messages** - Raw Telegram content

- `id`, `telegram_id`, `channel_id`
- `raw_content`, `extracted_urls`
- `timestamp`, `created_at`

**summaries** - AI-processed results

- `message_id` (FK)
- `summary`, `category_id`
- `entities` (JSON with source, protocols, amounts, etc.)
- `processing_status` (PENDING, SUCCESS, RETRY, FAILED)
- `confidence` (CONFIDENT, UNCERTAIN)

**categories** - User-defined categories

- `name`, `description`, `keywords`

## 🔧 Configuration Options

| Variable | Default | Description |
| -------- | ------- | ----------- |
| `GROK_MODEL` | grok-3 | Grok model to use |
| `GROK_API_URL` | `https://api.x.ai/v1` | Grok API endpoint |
| `SYNC_INTERVAL_HOURS` | 6 | Auto-sync interval (Railway) |
| `MAX_GROK_REQUESTS_PER_HOUR` | 100 | Rate limit setting |

## 📚 Additional Documentation

- [VISION.md](VISION.md) - Project goals and purpose
- [PROJECT_OVERVIEW.md](PROJECT_OVERVIEW.md) - Technical architecture
- [RAILWAY_DEPLOYMENT.md](RAILWAY_DEPLOYMENT.md) - Production deployment guide
- [HYBRID_SETUP.md](HYBRID_SETUP.md) - Bot + Phone hybrid setup

## 🚢 Production Deployment (Railway)

For ongoing automated processing, deploy to Railway:

1. Fork this repo
2. Connect to Railway
3. Add PostgreSQL service
4. Configure environment variables
5. Auto-syncs every 6 hours

See [RAILWAY_DEPLOYMENT.md](RAILWAY_DEPLOYMENT.md) for full guide.

## License

MIT
