# Tele-Sum Database Schema

Reference for the 10 tables tele-sum owns, plus the two integration points it shares with the sibling app on the same Postgres.

The full ORM is in [`src/models/models.py`](src/models/models.py). Migrations live in [`migrations/versions/`](migrations/versions/) — current head is `20260612_0001`.

## Shared database

Tele-sum uses a single Postgres instance (`DATABASE_URL`). In the reference deployment it is shared with a sibling grants app; standalone deployments just point at their own database. Tele-sum stays in a tight lane:

| Table | Owner | Tele-sum access |
|-------|-------|-----------------|
| `api_providers` | sibling app | **read-only** — `AIService._resolve_config_from_db()` looks up the active provider row when no `AI_*` env override is set |
| `api_usage_log` | sibling app | **write-only** — every `AIService.analyze_content()` call inserts one row (provider, model, purpose='summarize', tokens_in, tokens_out, wire, duration_ms, success, error_message) |

Tele-sum never reads or writes any other tables outside the 10 listed below.

## Production query pattern

Always require both `processing_status = 'SUCCESS'` and `data_quality = 'complete'`:

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

The `/api/entries` router in [`src/api/entries.py`](src/api/entries.py) applies these filters automatically.

---

## Core tables

### messages

Raw Telegram channel posts. One row per `(channel_id, telegram_id)`.

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| telegram_id | INTEGER | Telegram message id |
| channel_id | VARCHAR(255) | Telegram chat id (numeric, as string) |
| channel_config_id | INTEGER FK → channel_configs.id | Links to wire metadata |
| bot_id | INTEGER FK → telegram_bots.id | Which bot ingested |
| import_method | VARCHAR(20) | `bot` (live) or `file` (one-off backfill). Legacy rows may say `phone` |
| wire | TEXT | Inherited from channel config. Any slug is valid; common values: `crypto`, `ai`, `oss` |
| timestamp | TIMESTAMPTZ | Message post time |
| raw_content | TEXT | |
| extracted_urls | JSON | Array of URL strings |
| created_at / updated_at | TIMESTAMPTZ | |

Indexes: `uq_messages_channel_telegram_id` (unique), `ix_messages_channel_timestamp`, `ix_messages_bot_timestamp`.

### summaries

AI output for each message. 1:1 with `messages` via `message_id`.

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| message_id | INTEGER FK → messages.id (unique) | |
| title | VARCHAR(100) | 4–5 word title |
| summary | TEXT | 3–4 sentence factual summary |
| entities | JSON | See shape below |
| category_id | INTEGER FK → categories.id | |
| confidence | ENUM(`confident`,`uncertain`) | |
| wire | TEXT | Mirrors `messages.wire`. Any slug is valid |
| processing_status | ENUM(`PENDING`,`PROCESSING`,`SUCCESS`,`RETRY`,`FAILED`) | |
| data_quality | VARCHAR(20) | `complete` or `incomplete` |
| processing_attempts | INTEGER | |
| last_processed_at | TIMESTAMPTZ | |
| error_log | TEXT | Last failure reason, truncated to 1000 chars |
| created_at / updated_at | TIMESTAMPTZ | |

Indexes: `ix_summaries_status_processed`, `ix_summaries_category_created`, plus column indexes on `processing_status`, `wire`, `data_quality`.

#### `entities` JSON shape

For **grant** channels, validated on write by `EntitiesPayload` in [`src/schemas/entities.py`](src/schemas/entities.py):

```json
{
  "source": {
    "name": "Optimism Governance Forum",
    "type": "forum",
    "original_date": "2024-01-15",
    "url": "https://gov.optimism.io/...",
    "note": null
  },
  "orgs":      ["Optimism", "Coinbase"],
  "amounts":   ["$5M", "100,000 OP"],
  "deadlines": ["2024-02-01"],
  "key_terms": ["retroactive funding"],
  "urls":      ["https://gov.optimism.io/..."]
}
```

`source.type` is one of: `forum | twitter | blog | governance | news | substack | mirror | medium | github | discord | other`.

> **Note**: the key was renamed from `protocols` to `orgs` in migration `backfill_protocols_to_orgs`. All existing rows have been backfilled.

For **digest** channels, `entities` has the shape:

```json
{
  "profile": "digest",
  "items": [
    { "title": "...", "url": "...", "note": "...", "category": "...", "tags": ["..."], "org": "..." }
  ],
  "urls":      ["https://..."],
  "confidence": "confident"
}
```

Validated on write by `DigestAnalysis` in [`src/schemas/digest.py`](src/schemas/digest.py).

### categories

User-defined classification categories. Default categories are seeded on startup by `seed_default_categories_if_empty()` in [`src/main.py`](src/main.py:63).

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| user_id | INTEGER FK → users.id | |
| name | VARCHAR(100) | |
| description | TEXT | |
| keywords | JSON | List of keyword hints for the LLM |
| created_at / updated_at | TIMESTAMPTZ | |

### users

Multi-tenant scaffold. In practice, all categories use `user_id = 1`.

### review_queue

Tracks summaries that need a human to fix the source URL.

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| summary_id | INTEGER FK → summaries.id (unique) | |
| issue_type | VARCHAR(50) | `url_mismatch`, `url_missing`, `search_failed`, `unknown_protocol`, `no_forum` |
| our_title, matched_url, matched_page_title, protocol, notes | various | Diagnostic context |
| status | ENUM(`pending`,`in_progress`,`corrected`,`skipped`) | |
| corrected_url, reviewed_by, reviewed_at | various | Audit trail |
| created_at / updated_at | TIMESTAMPTZ | |

---

## Telegram-side tables

### telegram_bots

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| bot_token | VARCHAR(500) unique | |
| bot_username, bot_name | VARCHAR | |
| is_active | BOOLEAN | |
| webhook_url | VARCHAR(500) | Unused — polling only |
| last_update_id | INTEGER | Per-bot offset for `getUpdates` long-poll |
| created_at / updated_at | TIMESTAMPTZ | |

### channel_configs

One row per Telegram channel tele-sum ingests from. Each deployment defines its own channels; the reference deployment (Sovereign Signal Grant Wire) runs grant-wire channels (crypto/ai/oss) plus digest channels.

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| channel_id | VARCHAR(255) unique | Numeric Telegram chat id; may be `@username` as a placeholder (resolved on first post) |
| channel_username, channel_title | VARCHAR | |
| wire | TEXT | Stamped onto every imported message. Any slug is valid |
| extraction_profile | VARCHAR(32) | `grant` (default) or `digest`. Unknown values fall back to `grant` at runtime |
| prompt_hint | TEXT (nullable) | Free-text hint passed to the AI for digest channels; ignored for grant |
| notion_database_id | VARCHAR(64) (nullable) | Notion database to sync digest items into; null means no sync for this channel |
| import_mode | ENUM(`phone`,`bot`,`hybrid`) | Always `bot` for new configs; older enum values retained for schema stability |
| historical_import_complete | BOOLEAN | |
| historical_import_date | TIMESTAMPTZ | |
| last_historical_message_id | INTEGER | |
| bot_id | INTEGER FK → telegram_bots.id | |
| is_active | BOOLEAN | |
| created_at / updated_at | TIMESTAMPTZ | |

### sync_status

Per-channel cursor for incremental imports.

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| channel_id | VARCHAR(255) unique | |
| channel_config_id | INTEGER FK → channel_configs.id | |
| last_message_id, last_phone_sync_id, last_bot_sync_id | INTEGER | Cursors |
| last_sync_at | TIMESTAMPTZ | |
| total_messages_synced | INTEGER | |
| is_initial_import_complete, transition_to_bot_complete | BOOLEAN | Legacy flags from earlier import strategies |
| created_at / updated_at | TIMESTAMPTZ | |

### notion_pages

Tracks Notion pages created from digest items. One row per `(summary_id, item_index)`.

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| summary_id | INTEGER FK → summaries.id (CASCADE DELETE) | |
| item_index | INTEGER | Position of the item in `entities.items[]` |
| page_id | VARCHAR(64) | Notion page id returned by the API |
| created_at | TIMESTAMPTZ | |

Unique constraint: `uq_notion_pages_summary_item` on `(summary_id, item_index)` — the idempotency key used by the `notion_sync` job to skip already-created pages.

### ai_request_reservations

TeleSum's shared rolling-hour AI budget. A worker atomically reserves a slot before
calling a provider. Failed calls and interrupted workers still consume their slot.
Reservations commit independently of message transactions. Expired slots are removed
when a new slot is requested. The indexed `reserved_at` timestamp uses database time.

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | Reservation identifier |
| reserved_at | TIMESTAMPTZ | Indexed; counts against the cap for one hour |

PostgreSQL workers serialize the count-and-insert transaction with a transaction-scoped
advisory lock. SQLite uses `BEGIN IMMEDIATE`. No lock is held during inference.
Startup creates this table through the existing model initialization path. Existing
`api_usage_log` records remain telemetry; other apps sharing that log do not spend
TeleSum's budget. The legacy `grok_usage` table is unchanged.

### grok_usage *(read-only, legacy)*

Pre-`api_usage_log` telemetry table. Tele-sum no longer writes to it; new telemetry goes to the shared `api_usage_log` (see top of doc). The model is retained for historical queries — see [`src/models/models.py:238`](src/models/models.py:238).

---

## Common queries

```sql
-- Per-wire counts of production-ready entries
SELECT wire, COUNT(*) AS n
FROM summaries
WHERE processing_status = 'SUCCESS' AND data_quality = 'complete'
GROUP BY wire;

-- Failed summaries with error text
SELECT m.id, s.error_log, s.processing_attempts
FROM summaries s JOIN messages m ON s.message_id = m.id
WHERE s.processing_status = 'FAILED';

-- Entities containing a specific org
SELECT title, summary, entities->'source'->>'url' AS url
FROM summaries
WHERE processing_status = 'SUCCESS'
  AND data_quality = 'complete'
  AND entities->'orgs' @> '["Arbitrum"]'::jsonb;

-- AI usage over the last hour (shared table)
SELECT provider, model, COUNT(*) AS calls,
       SUM(tokens_in) AS tin, SUM(tokens_out) AS tout
FROM api_usage_log
WHERE created_at > NOW() - INTERVAL '1 hour'
  AND purpose = 'summarize'
GROUP BY provider, model;
```

## ORM helper

```python
from src.models.models import Summary, Message, ProcessingStatus

def production_entries(db, limit=100):
    return (
        db.query(Summary).join(Message)
          .filter(Summary.processing_status == ProcessingStatus.SUCCESS,
                  Summary.data_quality == "complete")
          .order_by(Message.timestamp.desc())
          .limit(limit).all()
    )
```
