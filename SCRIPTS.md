# Scripts Documentation

This document describes all utility scripts in the tele-sum project.

## Core Processing Scripts

### process_batch.py
**Purpose**: Main batch processing script - syncs from Telegram and processes messages with Grok AI.

```bash
# Default: sync + process 5 messages
python3 process_batch.py

# Process specific count
python3 process_batch.py --count 50

# Start from specific ID
python3 process_batch.py --start 100 --count 20

# Skip Telegram sync, process existing only
python3 process_batch.py --no-sync --count 10
```

**Options**:
- `--start ID`: Start from specific message ID (auto-detects if not set)
- `--count N`: Number of messages to process (default: 5)
- `--no-sync`: Skip Telegram sync

**Output**: Shows message ID, content preview, processing result, summary, category, source URL, and confidence.

---

### batch_runner.py
**Purpose**: Resilient batch runner with auto-retry on database timeouts. Recommended for bulk processing.

```bash
python3 batch_runner.py 1001 1100 25
# Arguments: <start_id> <end_id> [chunk_size]
```

**Features**:
- Processes in chunks (default: 25)
- Auto-retries on Railway database timeouts
- Auto-detects next unprocessed message
- Skips and continues on persistent failures
- Retries failed chunks up to 3 times

---

### sync_new.py
**Purpose**: Sync new messages from Telegram without processing them.

```bash
python3 sync_new.py
```

**Use when**:
- Import messages without using Grok API quota
- Testing Telegram connectivity
- Building up a backlog to process later

**Output**: Number of new messages found, suggested `process_batch.py` command.

---

## URL Backfill Scripts

### backfill_source_urls.py
**Purpose**: Search Discourse forums to find and backfill source URLs for x23 entries.

```bash
# Check current stats
python3 backfill_source_urls.py stats

# Test search for specific entry
python3 backfill_source_urls.py test <summary_id>

# Run backfill on batch
python3 backfill_source_urls.py run 50

# Check match quality
python3 backfill_source_urls.py verify <summary_id>
```

**Commands**:
- `stats`: Show current URL coverage statistics
- `test <id>`: Test search for a specific summary ID
- `run <count>`: Backfill URLs for next N entries
- `verify <id>`: Verify a matched URL is correct

**Supported Forums**: Arbitrum, Optimism, MakerDAO, ENS, Uniswap, Aave, Compound, Lido, Curve, Gitcoin, and 20+ more.

---

### extract_x23_urls.py
**Purpose**: Extract x23.ai URLs from Telegram message entities (hidden hyperlinks).

```bash
python3 extract_x23_urls.py
```

**What it does**:
1. Connects to Telegram and re-fetches x23 messages
2. Extracts `MessageEntityTextUrl` entities (clickable "More details" links)
3. Stores x23.ai URLs in `entities.source.x23_url`
4. Updates review queue with matched URLs

**Output**: Count of extracted URLs, updates to database.

---

### reconstruct_urls.py
**Purpose**: Reconstruct actual forum URLs from x23.ai URL patterns.

```bash
# Show stats
python3 reconstruct_urls.py stats

# Reconstruct and verify URLs
python3 reconstruct_urls.py run

# Test specific entry
python3 reconstruct_urls.py test <summary_id>
```

**How it works**:
- x23.ai URLs follow pattern: `https://app.x23.ai/{protocol}/discussions/topic/{id}/{slug}`
- Maps protocol names to forum domains (e.g., `arbitrum` → `forum.arbitrum.foundation`)
- Reconstructs URL: `https://{forum}/t/{slug}/{id}`
- Verifies URL is accessible before updating database

**Protocol mappings**: Arbitrum, Optimism, MakerDAO, Lido, Gitcoin, CoW DAO, ENS, Safe, and more.

---

## Review Queue Scripts

### populate_review_queue.py
**Purpose**: Manage the review queue for entries needing manual URL correction.

```bash
# Show statistics
python3 populate_review_queue.py stats

# List pending entries
python3 populate_review_queue.py list [--limit 20]

# Show details of specific entry
python3 populate_review_queue.py show <summary_id>

# Export pending entries to CSV
python3 populate_review_queue.py export [filename.csv]

# Mark entry as corrected with URL
python3 populate_review_queue.py complete <summary_id> <url>

# Mark entry as skipped (unrecoverable)
python3 populate_review_queue.py skip <summary_id> [reason]

# Clean up corrected entries
python3 populate_review_queue.py cleanup

# Clear all pending (use with caution)
python3 populate_review_queue.py clear
```

**Issue types**:
- `search_failed`: Forum search couldn't find matching post
- `no_forum`: Protocol has no Discourse forum
- `unknown_protocol`: Protocol couldn't be identified
- `url_mismatch`: Found URL doesn't match

---

## Reprocessing Scripts

### reprocess_all.py
**Purpose**: Reprocess all entries with Grok AI (e.g., after prompt changes).

```bash
# Reprocess all entries
python3 reprocess_all.py

# Reprocess specific range
python3 reprocess_all.py --start 100 --end 200

# Dry run (show what would be processed)
python3 reprocess_all.py --dry-run
```

**Use when**:
- Grok prompt has been updated
- Category list has changed
- Need to regenerate titles/summaries

---

### reprocess_twitter.py
**Purpose**: Reprocess entries with Twitter/X URLs to get fresh content.

```bash
python3 reprocess_twitter.py [--count 50]
```

**Use when**:
- Twitter scraping has improved
- Need to update Twitter-sourced summaries

---

### backfill_titles.py
**Purpose**: Generate titles for entries that are missing them.

```bash
# Check entries missing titles
python3 backfill_titles.py stats

# Generate titles for batch
python3 backfill_titles.py run 50
```

**Title format**:
- NEWS: "Protocol Action Subject" (e.g., "Optimism Grants Council Results")
- OPINION: "Author on Topic" (e.g., "Polynya on L2 Economics")

---

## Database Schema

All scripts use the same database models defined in `src/models/models.py`:

### Key Tables
- `messages`: Raw Telegram content
- `summaries`: AI-generated summaries with `data_quality` flag
- `review_queue`: Entries needing manual review

### Data Quality Flag
```python
# In summaries table
data_quality = 'complete'    # Production ready
data_quality = 'incomplete'  # Missing source URL
```

See [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md) for complete schema reference.

---

## Environment Requirements

All scripts require these environment variables:

```bash
DATABASE_URL=postgresql://...
TELEGRAM_API_ID=...
TELEGRAM_API_HASH=...
TELEGRAM_PHONE=...
TELEGRAM_CHANNEL_USERNAME=...
GROK_API_KEY=...
```

Scripts load from `.env` file automatically using `python-dotenv`.

---

## Script Execution Order

For initial setup:
1. `sync_new.py` - Import all messages
2. `batch_runner.py` - Process with Grok AI
3. `backfill_source_urls.py` - Find source URLs for x23 entries
4. `extract_x23_urls.py` - Extract x23.ai URLs
5. `reconstruct_urls.py` - Reconstruct forum URLs
6. `populate_review_queue.py` - Manage remaining incomplete entries

For ongoing maintenance:
1. `process_batch.py` - Daily sync and process
2. `populate_review_queue.py stats` - Check for issues
