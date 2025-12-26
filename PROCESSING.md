# Processing System Documentation

This document explains how the batch processing, retry logic, and background scheduler work in Tele-Sum.

## Table of Contents
- [Processing Status State Machine](#processing-status-state-machine)
- [Batch Processing Workflow](#batch-processing-workflow)
- [Background Scheduler Jobs](#background-scheduler-jobs)
- [Configuration Reference](#configuration-reference)
- [Manual Operations](#manual-operations)
- [Troubleshooting](#troubleshooting)

---

## Processing Status State Machine

Every message has an associated `Summary` record that tracks its processing status through these states:

```
                    ┌─────────────────────────────────────┐
                    │                                     │
                    ▼                                     │
┌─────────┐    ┌────────────┐    ┌─────────┐             │
│ PENDING │───▶│ PROCESSING │───▶│ SUCCESS │             │
└─────────┘    └────────────┘    └─────────┘             │
                    │                                     │
                    │ (Grok API error                     │
                    │  or exception)                      │
                    ▼                                     │
               ┌─────────┐                                │
               │  RETRY  │────────────────────────────────┘
               └─────────┘        (next processing cycle)
                    │
                    │ (max retries exceeded)
                    ▼
               ┌─────────┐
               │ FAILED  │
               └─────────┘
```

### State Descriptions

| Status | Description |
|--------|-------------|
| `PENDING` | New message, never processed. Created when message is synced from Telegram. |
| `PROCESSING` | Currently being processed by Grok AI. Temporary state during API call. |
| `SUCCESS` | Successfully processed. Summary, category, and entities extracted. |
| `RETRY` | Processing failed but will be retried. Attempt count < max retries. |
| `FAILED` | Processing failed and max retries exceeded. Requires manual intervention. |

### State Transitions

| From | To | Trigger |
|------|-----|---------|
| PENDING | PROCESSING | Message selected for processing |
| PROCESSING | SUCCESS | Grok API returns valid result |
| PROCESSING | RETRY | Grok API returns None (rate limit) or exception occurs |
| PROCESSING | FAILED | Exception occurs AND attempt count >= max_retries |
| RETRY | PROCESSING | Message selected for processing in next cycle |
| FAILED | RETRY | `retry_failed_messages()` job re-queues within 24 hours |

### Key Fields

The `Summary` model tracks:
- `processing_status`: Current state (enum)
- `processing_attempts`: Number of times processing was attempted (increments each time)
- `last_processed_at`: Timestamp of last processing attempt
- `error_log`: Error message if failed (up to 1000 chars)

---

## Batch Processing Workflow

### Message Selection Priority

When processing a batch, messages are selected in this order:

1. **PENDING** messages first (never been processed)
2. **RETRY** messages second (failed but eligible for retry)
3. Within each status, **oldest messages first** (by timestamp)

```python
# Query logic (simplified)
messages = db.query(Message).join(Summary).filter(
    Summary.processing_status.in_([PENDING, RETRY])
).order_by(
    Summary.processing_status.desc(),  # PENDING before RETRY
    Message.timestamp.asc()            # oldest first
).limit(batch_size)
```

### Processing Loop

For each message in the batch:

1. **Check if already done**: Skip if status is SUCCESS
2. **Check retry limit**: If attempts >= max_retries, mark as FAILED
3. **Update status**: Set to PROCESSING, increment attempts, update timestamp
4. **Extract URLs**: Separate Twitter URLs from other URLs
5. **Scrape content**: Fetch non-Twitter URLs (Twitter is accessed directly by Grok)
6. **Call Grok API**: Send message + scraped content for analysis
7. **Handle result**:
   - Success → Update summary, set status to SUCCESS
   - No result → Set status to RETRY
   - Exception → Set to RETRY or FAILED based on attempt count
8. **Rate limit delay**: Wait 1-1.5 seconds before next message

### Rate Limiting

- **Between messages**: 1 second delay in `process_batch()`, 1.5 seconds in `process_batch.py`
- **Grok API limit**: Default 100 requests/hour (configurable)
- **When limit hit**: Grok service returns None, message goes to RETRY status

---

## Background Scheduler Jobs

The scheduler runs 5 background jobs using APScheduler:

| Job ID | Interval | Function | Purpose |
|--------|----------|----------|---------|
| `sync_job` | Every N hours (default: 6) | `sync_and_process()` | Full Telegram sync + process up to 200 messages |
| `process_pending` | Every 2 minutes | `process_pending_only()` | Process 10 pending/retry messages |
| `retry_failed` | Every 15 minutes | `retry_failed_messages()` | Re-queue FAILED messages (within 24h, under retry limit) |
| `cleanup_stale` | Every 1 hour | `cleanup_stale_processing()` | Reset stuck PROCESSING → RETRY (>10 min old) |
| `daily_stats` | Midnight (cron) | `log_daily_stats()` | Log processing statistics |

### Job Details

#### sync_and_process (Main Sync Job)
- Runs every `sync_interval_hours` (default: 6 hours)
- First syncs new messages from Telegram (phone or bot)
- Then processes messages in batches of 20
- Processes up to 10 batches (200 messages max per run)
- 5 second pause between batches
- Stops early if a batch processes fewer than 20 (no more pending)

#### process_pending_only
- Runs every 2 minutes
- Processes only 10 messages per run
- Does NOT sync from Telegram
- Keeps processing queue moving between main sync jobs

#### retry_failed_messages
- Runs every 15 minutes
- Finds FAILED messages where:
  - `last_processed_at` is within 24 hours
  - `processing_attempts` < max_retries
- Changes status from FAILED → RETRY
- These messages will be picked up by next process_pending run

#### cleanup_stale_processing
- Runs every 1 hour
- Finds messages stuck in PROCESSING status for >10 minutes
- Resets them to RETRY status
- Prevents messages from being permanently stuck (e.g., after crash)

#### log_daily_stats
- Runs at midnight (00:00)
- Logs summary of all processing statistics
- Includes Grok API rate limit status

---

## Configuration Reference

Set these in your `.env` file or environment:

| Variable | Default | Description |
|----------|---------|-------------|
| `SYNC_INTERVAL_HOURS` | 6 | Hours between full Telegram syncs |
| `MAX_GROK_REQUESTS_PER_HOUR` | 100 | Grok API rate limit |
| `MAX_PROCESSING_RETRIES` | 3 | Attempts before marking as FAILED |
| `PROCESSING_BATCH_SIZE` | 10 | Messages per batch in background jobs |

### Tuning Recommendations

**High-volume channels (>100 messages/day):**
```
SYNC_INTERVAL_HOURS=2
MAX_GROK_REQUESTS_PER_HOUR=100
PROCESSING_BATCH_SIZE=20
```

**Low-volume channels (<20 messages/day):**
```
SYNC_INTERVAL_HOURS=12
MAX_GROK_REQUESTS_PER_HOUR=50
PROCESSING_BATCH_SIZE=5
```

**Catching up on backlog:**
```
MAX_GROK_REQUESTS_PER_HOUR=100
PROCESSING_BATCH_SIZE=50
# Run process_batch.py manually with --count 100
```

---

## Manual Operations

### process_batch.py

Process messages with Grok AI. Syncs from Telegram first by default.

```bash
# Process 5 messages starting from first unprocessed
python3 process_batch.py

# Process 20 messages starting from ID 100
python3 process_batch.py --start 100 --count 20

# Process without syncing from Telegram first
python3 process_batch.py --no-sync --count 10
```

**Options:**
- `--start ID`: Start from specific message ID (auto-detects if not set)
- `--count N`: Number of messages to process (default: 5)
- `--no-sync`: Skip Telegram sync, process existing messages only

**Output shows:**
- Message ID and content preview
- Processing result (SUCCESS/FAILED)
- Summary, category, source URL, and confidence level

### sync_new.py

Sync new messages from Telegram without processing them.

```bash
python3 sync_new.py
```

**Use when:**
- You want to import messages without using Grok API quota
- Testing Telegram connectivity
- Building up a backlog to process later

**Output shows:**
- Number of new messages found
- Suggested `process_batch.py` command with count

### Checking Processing Status

Query the database to see current status:

```sql
-- Count by status
SELECT processing_status, COUNT(*)
FROM summaries
GROUP BY processing_status;

-- Find failed messages
SELECT m.id, m.raw_content, s.error_log, s.processing_attempts
FROM messages m
JOIN summaries s ON s.message_id = m.id
WHERE s.processing_status = 'FAILED';

-- Find messages stuck in processing
SELECT m.id, s.last_processed_at
FROM messages m
JOIN summaries s ON s.message_id = m.id
WHERE s.processing_status = 'PROCESSING'
AND s.last_processed_at < NOW() - INTERVAL '10 minutes';
```

---

## Troubleshooting

### Messages stuck in PENDING

**Cause:** Scheduler not running or Grok API not configured.

**Fix:**
1. Check scheduler status: `GET /health` should show scheduler running
2. Verify `GROK_API_KEY` is set
3. Run manually: `python3 process_batch.py --count 5`

### Messages stuck in PROCESSING

**Cause:** Process crashed during Grok API call.

**Fix:** The `cleanup_stale_processing` job runs hourly and resets these to RETRY. Or run manually:

```sql
UPDATE summaries
SET processing_status = 'RETRY', error_log = 'Manual reset'
WHERE processing_status = 'PROCESSING'
AND last_processed_at < NOW() - INTERVAL '10 minutes';
```

### Many messages in FAILED status

**Cause:** Repeated Grok API failures (rate limit, invalid responses).

**Check:**
```sql
SELECT error_log, COUNT(*)
FROM summaries
WHERE processing_status = 'FAILED'
GROUP BY error_log;
```

**Fix:**
1. If rate limit: Wait and let `retry_failed_messages` job re-queue them
2. If API errors: Check Grok API status, verify API key
3. Manual reset: Change status to RETRY in database

### Processing is slow

**Cause:** Rate limiting or small batch sizes.

**Fix:**
1. Increase `PROCESSING_BATCH_SIZE` (default: 10)
2. Check `MAX_GROK_REQUESTS_PER_HOUR` (default: 100)
3. Run `process_batch.py` with `--count 50` to process faster manually

### No new messages syncing

**Cause:** Telegram authentication expired or channel access lost.

**Fix:**
1. Check Telegram session: `ls -la telegram_session*`
2. Re-authenticate if needed
3. Verify channel username in `TELEGRAM_CHANNEL_USERNAME`
4. Check bot tokens if using bot mode
