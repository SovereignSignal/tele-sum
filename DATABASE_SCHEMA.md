# Grant Wire Database Schema

Complete database schema reference for the Grant Wire summarization system.

## Quick Reference: Production Queries

**Always filter by `data_quality = 'complete'` for production use:**

```sql
SELECT s.id, s.title, s.summary, s.entities, m.timestamp
FROM summaries s
JOIN messages m ON s.message_id = m.id
WHERE s.processing_status = 'SUCCESS'
  AND s.data_quality = 'complete'
ORDER BY m.timestamp DESC;
```

## Current Statistics

| Metric | Value |
|--------|-------|
| Total entries | 3,069 |
| Complete (production-ready) | 3,069 (100%) |
| Incomplete (missing URL) | 0 (0%) |
| Average title length | 34 characters |
| Average summary length | 404 characters |

---

## Core Tables

### messages

Raw Telegram messages imported from Grant Wire channels.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| telegram_id | INTEGER | Unique Telegram message ID |
| channel_id | VARCHAR(255) | Telegram channel ID |
| bot_id | INTEGER | FK to telegram_bots (if bot import) |
| import_method | VARCHAR(20) | `phone_initial`, `phone_sync`, `x23`, `bot`, `file` |
| wire | ENUM | Grant Wire vertical: `crypto`, `ai`, `oss` |
| timestamp | TIMESTAMP | When message was posted on Telegram |
| raw_content | TEXT | Original message text |
| extracted_urls | JSON | Array of URLs found in message |
| created_at | TIMESTAMP | When imported into database |
| updated_at | TIMESTAMP | Last modification time |

**Indexes:**
- `ix_messages_telegram_id` (unique)
- `ix_messages_channel_timestamp` (channel_id, timestamp)
- `ix_messages_bot_timestamp` (bot_id, timestamp)
- `ix_messages_wire`

---

### summaries

AI-generated summaries and metadata for each message.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| message_id | INTEGER | FK to messages.id (unique) |
| title | VARCHAR(100) | Short 4-6 word title |
| summary | TEXT | AI-generated summary (200-600 chars typical) |
| entities | JSON | Structured metadata (see below) |
| category_id | INTEGER | FK to categories.id |
| confidence | ENUM | `confident` or `uncertain` |
| wire | ENUM | Grant Wire vertical: `crypto`, `ai`, `oss` |
| processing_status | ENUM | `PENDING`, `PROCESSING`, `SUCCESS`, `FAILED`, `RETRY` |
| data_quality | VARCHAR(20) | `complete` or `incomplete` |
| error_log | TEXT | Error details if processing failed |
| processing_attempts | INTEGER | Number of processing attempts |
| last_processed_at | TIMESTAMP | Last processing attempt time |
| created_at | TIMESTAMP | When record was created |
| updated_at | TIMESTAMP | Last modification time |

**Indexes:**
- `ix_summaries_processing_status`
- `ix_summaries_data_quality`
- `ix_summaries_wire`
- `ix_summaries_status_processed` (processing_status, last_processed_at)
- `ix_summaries_category_created` (category_id, created_at)

#### The `entities` JSON Structure

```json
{
  "source": {
    "url": "https://forum.example.com/t/topic/123",
    "type": "forum",
    "name": "Arbitrum Governance Forum",
    "original_date": "2024-01-15",
    "x23_url": "https://app.x23.ai/arbitrum/discussions/topic/123/slug"
  },
  "urls": ["https://forum.example.com/t/topic/123"],
  "protocols": ["Arbitrum", "Optimism"],
  "amounts": ["$500K", "1M ARB"],
  "deadlines": ["2024-02-01"],
  "key_terms": ["grants", "funding", "proposal"]
}
```

#### `data_quality` Values

| Value | Count | Description |
|-------|-------|-------------|
| `complete` | 3,069 | Full data with verified source URL - **use for production** |
| `incomplete` | 0 | Missing or unverified source URL - excluded from production |

#### `processing_status` Values

| Value | Description |
|-------|-------------|
| `PENDING` | New message, never processed |
| `PROCESSING` | Currently being processed by Grok AI |
| `SUCCESS` | Successfully processed with summary |
| `RETRY` | Failed but will be retried |
| `FAILED` | Max retries exceeded, needs manual intervention |

#### `wire` Values

The system supports multiple Grant Wire verticals:

| Value | Channel | Description |
|-------|---------|-------------|
| `crypto` | @cryptograntwire | Crypto/Web3 grants, governance, funding |
| `ai` | @aigrantwire | AI research grants, fellowships, hackathons |
| `oss` | @ossgrantwire | Open source funding opportunities |

Wire is inherited from channel configuration at import time:
1. `channel_configs.wire` → `messages.wire` → `summaries.wire`

To filter by wire in queries:
```sql
WHERE s.wire = 'ai'  -- or 'crypto' or 'oss'
```

---

### categories

User-defined classification categories.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| user_id | INTEGER | FK to users.id |
| name | VARCHAR(100) | Category name |
| description | TEXT | Category description |
| keywords | JSON | Keywords for AI classification hints |
| created_at | TIMESTAMP | When created |
| updated_at | TIMESTAMP | Last modification |

**Current categories:**

| Category | Count | Percentage |
|----------|-------|------------|
| Governance & Treasury | 1,210 | 39.4% |
| Funding Opportunities | 691 | 22.5% |
| Grant Programs | 507 | 16.5% |
| Research & Analysis | 305 | 9.9% |
| Tools & Infrastructure | 222 | 7.2% |
| Incentives | 134 | 4.4% |

---

### review_queue

Tracks entries needing manual review for URL correction.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| summary_id | INTEGER | FK to summaries.id (unique) |
| issue_type | VARCHAR(50) | `search_failed`, `no_forum`, `unknown_protocol`, `url_mismatch` |
| our_title | VARCHAR(255) | Title we generated |
| matched_url | TEXT | URL we matched (if any) |
| matched_page_title | VARCHAR(255) | Title of matched page |
| protocol | VARCHAR(100) | Detected protocol name |
| notes | TEXT | Additional context |
| status | ENUM | `pending`, `in_progress`, `corrected`, `skipped` |
| corrected_url | TEXT | Manually corrected URL |
| reviewed_by | VARCHAR(100) | Who reviewed it |
| reviewed_at | TIMESTAMP | When reviewed |
| created_at | TIMESTAMP | When added to queue |
| updated_at | TIMESTAMP | Last modification |

**Current review queue:**

| Status | Count |
|--------|-------|
| pending | 0 |
| corrected | 0 |

*Note: The review queue is empty. All 259 entries that required manual URL recovery have been corrected and removed.*

---

## Supporting Tables

### users

Multi-tenant user support.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| username | VARCHAR(255) | Unique username |
| created_at | TIMESTAMP | When created |
| updated_at | TIMESTAMP | Last modification |

---

### telegram_bots

Bot configuration for channel monitoring.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| bot_token | VARCHAR(500) | Encrypted bot token |
| bot_username | VARCHAR(100) | Bot username |
| bot_name | VARCHAR(100) | Display name |
| is_active | BOOLEAN | Whether bot is active |
| webhook_url | VARCHAR(500) | Webhook URL if using webhooks |
| last_update_id | INTEGER | For polling mode |
| created_at | TIMESTAMP | When created |
| updated_at | TIMESTAMP | Last modification |

---

### channel_configs

Channel import configuration. Each channel belongs to a wire (crypto, ai, oss).

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| channel_id | VARCHAR(255) | Telegram channel ID |
| channel_username | VARCHAR(100) | Channel username |
| channel_title | VARCHAR(255) | Display title |
| wire | ENUM | Grant Wire vertical: `crypto`, `ai`, `oss` |
| import_mode | ENUM | `phone`, `bot`, `hybrid` |
| historical_import_complete | BOOLEAN | Whether historical import is done |
| historical_import_date | TIMESTAMP | When historical import completed |
| last_historical_message_id | INTEGER | Last imported message ID |
| bot_id | INTEGER | FK to telegram_bots |
| is_active | BOOLEAN | Whether channel is active |
| created_at | TIMESTAMP | When created |
| updated_at | TIMESTAMP | Last modification |

**Current Channels:**

| Wire | Username | Status |
|------|----------|--------|
| crypto | @cryptograntwire | Active |
| ai | @aigrantwire | Active |
| oss | @ossgrantwire | Placeholder |

---

### sync_status

Tracks Telegram sync state for incremental imports.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| channel_id | VARCHAR(255) | Telegram channel ID |
| channel_config_id | INTEGER | FK to channel_configs |
| last_message_id | INTEGER | Last synced message ID |
| last_phone_sync_id | INTEGER | Last phone import ID |
| last_bot_sync_id | INTEGER | Last bot import ID |
| last_sync_at | TIMESTAMP | Last sync time |
| total_messages_synced | INTEGER | Total messages synced |
| is_initial_import_complete | BOOLEAN | Historical import done |
| transition_to_bot_complete | BOOLEAN | Transitioned to bot mode |
| created_at | TIMESTAMP | When created |
| updated_at | TIMESTAMP | Last modification |

---

### grok_usage

Tracks Grok API usage for monitoring.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| timestamp | TIMESTAMP | When request was made |
| tokens_used | INTEGER | Tokens consumed |
| request_type | VARCHAR(50) | `analysis`, `retry`, etc. |
| success | BOOLEAN | Whether request succeeded |
| error_message | TEXT | Error if failed |

---

## Common Queries

### Get all production-ready entries

```sql
SELECT
    s.id,
    s.title,
    s.summary,
    s.entities->>'category' as category,
    s.entities->'source'->>'url' as source_url,
    s.entities->'source'->>'type' as source_type,
    m.timestamp as published_at
FROM summaries s
JOIN messages m ON s.message_id = m.id
WHERE s.processing_status = 'SUCCESS'
  AND s.data_quality = 'complete'
ORDER BY m.timestamp DESC;
```

### Get entries by category

```sql
SELECT s.title, s.summary, m.timestamp
FROM summaries s
JOIN messages m ON s.message_id = m.id
JOIN categories c ON s.category_id = c.id
WHERE s.processing_status = 'SUCCESS'
  AND s.data_quality = 'complete'
  AND c.name = 'Grant Programs'
ORDER BY m.timestamp DESC;
```

### Search by protocol

```sql
SELECT s.title, s.summary, s.entities->'source'->>'url' as url
FROM summaries s
JOIN messages m ON s.message_id = m.id
WHERE s.processing_status = 'SUCCESS'
  AND s.data_quality = 'complete'
  AND (
    s.entities->'source'->>'protocol' ILIKE '%arbitrum%'
    OR s.entities::text ILIKE '%arbitrum%'
  )
ORDER BY m.timestamp DESC;
```

### Full-text search

```sql
SELECT s.title, s.summary, m.timestamp
FROM summaries s
JOIN messages m ON s.message_id = m.id
WHERE s.processing_status = 'SUCCESS'
  AND s.data_quality = 'complete'
  AND (
    s.title ILIKE '%grants%'
    OR s.summary ILIKE '%grants%'
  )
ORDER BY m.timestamp DESC;
```

### Get processing statistics

```sql
SELECT
    processing_status,
    data_quality,
    COUNT(*) as count
FROM summaries
GROUP BY processing_status, data_quality
ORDER BY processing_status, data_quality;
```

### Get entries by import method

```sql
SELECT
    m.import_method,
    COUNT(*) as total,
    COUNT(CASE WHEN s.data_quality = 'complete' THEN 1 END) as complete,
    COUNT(CASE WHEN s.data_quality = 'incomplete' THEN 1 END) as incomplete
FROM messages m
JOIN summaries s ON s.message_id = m.id
WHERE s.processing_status = 'SUCCESS'
GROUP BY m.import_method;
```

### Get review queue by issue type

```sql
SELECT
    rq.issue_type,
    rq.protocol,
    COUNT(*) as count
FROM review_queue rq
WHERE rq.status = 'pending'
GROUP BY rq.issue_type, rq.protocol
ORDER BY count DESC;
```

---

## ORM Usage (Python)

### Filter for production use

```python
from src.models.models import Summary, Message
from sqlalchemy.orm import Session

def get_production_entries(db: Session, limit: int = 100):
    return db.query(Summary).join(Message).filter(
        Summary.processing_status == 'SUCCESS',
        Summary.data_quality == 'complete'
    ).order_by(Message.timestamp.desc()).limit(limit).all()
```

### Check data quality

```python
def get_quality_stats(db: Session):
    complete = db.query(Summary).filter(
        Summary.data_quality == 'complete'
    ).count()

    incomplete = db.query(Summary).filter(
        Summary.data_quality == 'incomplete'
    ).count()

    return {'complete': complete, 'incomplete': incomplete}
```

---

## Best Practices

1. **Always filter by `data_quality = 'complete'`** for production/public use
2. **Always filter by `processing_status = 'SUCCESS'`** to exclude unprocessed entries
3. **Use the `entities` JSON** for structured queries on category, protocol, amounts
4. **Join with `messages`** to get the original timestamp and raw content
5. **Use indexes** - all filter columns have indexes for efficient queries
6. **Paginate results** for large datasets using LIMIT/OFFSET or keyset pagination
