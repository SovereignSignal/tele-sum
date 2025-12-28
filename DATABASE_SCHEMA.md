# Grant Wire Database Schema

This document describes the database schema for the Grant Wire summarization system. Use this as a reference for querying and integrating with the data.

## Quick Reference: Production Queries

### Get all complete, production-ready entries:
```sql
SELECT s.id, s.title, s.summary, s.entities, m.timestamp
FROM summaries s
JOIN messages m ON s.message_id = m.id
WHERE s.processing_status = 'SUCCESS'
  AND s.data_quality = 'complete'
ORDER BY m.timestamp DESC;
```

### Filter criteria explained:
- `processing_status = 'SUCCESS'` - Entry has been processed by AI
- `data_quality = 'complete'` - Entry has verified source URL and complete data

---

## Core Tables

### `messages`
Raw Telegram messages imported from the Grant Wire channel.

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER | Primary key |
| `telegram_id` | INTEGER | Unique Telegram message ID |
| `channel_id` | VARCHAR(255) | Telegram channel ID |
| `import_method` | VARCHAR(20) | How imported: `phone_initial`, `phone_sync`, `x23`, `bot`, `file` |
| `timestamp` | TIMESTAMP | When the message was posted |
| `raw_content` | TEXT | Original message text |
| `extracted_urls` | JSON | Array of URLs found in the message |
| `created_at` | TIMESTAMP | When imported into database |

### `summaries`
AI-generated summaries and metadata for each message.

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER | Primary key |
| `message_id` | INTEGER | Foreign key to messages.id |
| `title` | VARCHAR(100) | Short 4-6 word title |
| `summary` | TEXT | AI-generated summary (200-600 chars typical) |
| `entities` | JSON | Structured metadata (see below) |
| `category_id` | INTEGER | Optional category classification |
| `confidence` | ENUM | `confident` or `uncertain` |
| `processing_status` | ENUM | `PENDING`, `PROCESSING`, `SUCCESS`, `FAILED`, `RETRY` |
| `data_quality` | VARCHAR(20) | **`complete`** or **`incomplete`** |
| `error_log` | TEXT | Error details if processing failed |
| `created_at` | TIMESTAMP | When record was created |

#### The `entities` JSON Structure:
```json
{
  "source": {
    "url": "https://forum.example.com/t/topic/123",
    "type": "forum_post",
    "protocol": "Arbitrum"
  },
  "category": "Governance & Treasury",
  "protocols": ["Arbitrum", "Optimism"],
  "amounts": ["$500K", "1M ARB"],
  "key_terms": ["grants", "funding", "proposal"]
}
```

#### `data_quality` Values:
- **`complete`** (2,907 entries) - Full data with verified source URL. **Use these for production.**
- **`incomplete`** (162 entries) - Missing or unverified source URL. Exclude from production displays.

---

## Data Quality Filtering

### For Website/API Display (Production):
Always filter by `data_quality = 'complete'` to show only verified entries:

```sql
-- Get entries for public display
SELECT
    s.id,
    s.title,
    s.summary,
    s.entities->>'category' as category,
    s.entities->'source'->>'url' as source_url,
    s.entities->'source'->>'protocol' as protocol,
    m.timestamp as published_at
FROM summaries s
JOIN messages m ON s.message_id = m.id
WHERE s.processing_status = 'SUCCESS'
  AND s.data_quality = 'complete'
ORDER BY m.timestamp DESC
LIMIT 20;
```

### Get entries by category:
```sql
SELECT s.title, s.summary, m.timestamp
FROM summaries s
JOIN messages m ON s.message_id = m.id
WHERE s.processing_status = 'SUCCESS'
  AND s.data_quality = 'complete'
  AND s.entities->>'category' = 'Grant Programs'
ORDER BY m.timestamp DESC;
```

### Search by protocol:
```sql
SELECT s.title, s.summary, s.entities->'source'->>'url' as url
FROM summaries s
JOIN messages m ON s.message_id = m.id
WHERE s.processing_status = 'SUCCESS'
  AND s.data_quality = 'complete'
  AND (
    s.entities->'source'->>'protocol' = 'Arbitrum'
    OR s.entities::text ILIKE '%arbitrum%'
  )
ORDER BY m.timestamp DESC;
```

### Full-text search:
```sql
SELECT s.title, s.summary
FROM summaries s
WHERE s.processing_status = 'SUCCESS'
  AND s.data_quality = 'complete'
  AND (
    s.title ILIKE '%grants%'
    OR s.summary ILIKE '%grants%'
  )
ORDER BY s.id DESC;
```

---

## Supporting Tables

### `review_queue`
Tracks entries needing manual review (source URL corrections).

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER | Primary key |
| `summary_id` | INTEGER | FK to summaries.id |
| `issue_type` | VARCHAR(50) | `search_failed`, `no_forum`, `unknown_protocol` |
| `status` | ENUM | `pending`, `in_progress`, `corrected`, `skipped` |
| `protocol` | VARCHAR(100) | Detected protocol name |
| `corrected_url` | TEXT | Manually corrected URL (if fixed) |

### `sync_status`
Tracks Telegram sync state for incremental imports.

### `grok_usage`
Tracks AI API usage for monitoring.

---

## Category Reference

Entries are classified into these categories (stored in `entities->>'category'`):

| Category | Count | Description |
|----------|-------|-------------|
| Governance & Treasury | 1,210 | DAO governance, treasury, proposals |
| Funding Opportunities | 691 | Active grant programs, applications |
| Grant Programs | 507 | Grant program announcements, updates |
| Research & Analysis | 305 | Research, reports, analysis |
| Tools & Infrastructure | 222 | Developer tools, infrastructure |
| Incentives | 134 | Incentive programs, rewards |

---

## Statistics (as of Phase 1 completion)

- **Total entries**: 3,069
- **Complete (production-ready)**: 2,907 (94.7%)
- **Incomplete (missing URL)**: 162 (5.3%)
- **Average title length**: 34 characters
- **Average summary length**: 404 characters

---

## Import Methods

| Method | Description | Count |
|--------|-------------|-------|
| `phone_initial` | Initial import via Telegram phone auth | 1,524 |
| `x23` | Imported from x23.ai aggregator | 1,540 |
| `phone_sync` | Incremental phone sync | 5 |

---

## Best Practices

1. **Always filter by `data_quality = 'complete'`** for production use
2. **Always filter by `processing_status = 'SUCCESS'`** to exclude unprocessed entries
3. **Use the `entities` JSON** for structured queries on category, protocol, amounts
4. **Join with `messages`** to get the original timestamp and raw content
5. **Index exists on `data_quality`** so filtering is efficient
