#!/usr/bin/env python3
"""
Reprocess all Twitter-based summaries with the new Twitter scraper.
These entries likely have inaccurate summaries due to Grok hallucination.
"""
import subprocess
import sys
import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

def get_twitter_message_ids():
    """Get all message IDs that have Twitter URLs."""
    engine = create_engine(os.getenv('DATABASE_URL'))
    try:
        with engine.connect() as conn:
            result = conn.execute(text("""
                SELECT m.id
                FROM messages m
                JOIN summaries s ON s.message_id = m.id
                WHERE m.extracted_urls::text LIKE '%twitter.com%'
                   OR m.extracted_urls::text LIKE '%x.com%'
                ORDER BY m.id
            """)).fetchall()
            return [row[0] for row in result]
    finally:
        engine.dispose()

def reset_summaries(message_ids):
    """Reset summaries for given message IDs to PENDING."""
    engine = create_engine(os.getenv('DATABASE_URL'))
    try:
        with engine.connect() as conn:
            ids_str = ','.join(str(id) for id in message_ids)
            conn.execute(text(f"""
                UPDATE summaries
                SET summary = NULL,
                    title = NULL,
                    processing_status = 'PENDING',
                    processing_attempts = 0
                WHERE message_id IN ({ids_str})
            """))
            conn.commit()
            return len(message_ids)
    finally:
        engine.dispose()

def get_stats():
    """Get current reprocessing stats."""
    engine = create_engine(os.getenv('DATABASE_URL'))
    try:
        with engine.connect() as conn:
            result = conn.execute(text("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN s.title IS NOT NULL AND s.title != '' THEN 1 ELSE 0 END) as with_title,
                    SUM(CASE WHEN m.extracted_urls::text LIKE '%twitter.com%' OR m.extracted_urls::text LIKE '%x.com%' THEN 1 ELSE 0 END) as twitter_total
                FROM messages m
                JOIN summaries s ON s.message_id = m.id
            """)).fetchone()

            twitter_with_title = conn.execute(text("""
                SELECT COUNT(*)
                FROM messages m
                JOIN summaries s ON s.message_id = m.id
                WHERE (m.extracted_urls::text LIKE '%twitter.com%' OR m.extracted_urls::text LIKE '%x.com%')
                  AND s.title IS NOT NULL AND s.title != ''
            """)).fetchone()[0]

            print(f"\nReprocessing Status:")
            print(f"  Total entries: {result[0]}")
            print(f"  Twitter entries: {result[2]}")
            print(f"  Twitter with titles (reprocessed): {twitter_with_title}")
            print(f"  Twitter remaining: {result[2] - twitter_with_title}")
    finally:
        engine.dispose()

def run_batch(start_id, count):
    """Run process_batch.py for a range."""
    try:
        result = subprocess.run(
            ['python3', 'process_batch.py', '--start', str(start_id), '--count', str(count), '--no-sync'],
            capture_output=True,
            text=True,
            timeout=300  # 5 minute timeout
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        print(f"  Batch timed out")
        return False
    except Exception as e:
        print(f"  Error: {e}")
        return False

def reprocess_twitter_batch(start_idx=0, batch_size=25):
    """Reprocess Twitter entries in batches."""
    twitter_ids = get_twitter_message_ids()
    total = len(twitter_ids)

    print(f"\n{'='*60}")
    print(f"Reprocessing {total} Twitter-based entries")
    print(f"Starting from index {start_idx}, batch size {batch_size}")
    print(f"{'='*60}\n")

    # Process in batches
    idx = start_idx
    while idx < total:
        batch_ids = twitter_ids[idx:idx + batch_size]
        if not batch_ids:
            break

        print(f"Batch {idx//batch_size + 1}: IDs {batch_ids[0]} to {batch_ids[-1]} ({len(batch_ids)} entries)...")

        # Reset these summaries
        reset_summaries(batch_ids)

        # Process each ID
        for msg_id in batch_ids:
            success = run_batch(msg_id, 1)
            if success:
                print(f"  ✓ ID {msg_id}")
            else:
                print(f"  ✗ ID {msg_id} (will retry later)")

        idx += batch_size

        # Progress update
        processed = min(idx, total)
        print(f"\nProgress: {processed}/{total} ({round(processed/total*100, 1)}%)\n")

    print(f"\n*** REPROCESSING COMPLETE ***")
    get_stats()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 reprocess_twitter.py status")
        print("  python3 reprocess_twitter.py list          # List Twitter message IDs")
        print("  python3 reprocess_twitter.py run [start_idx] [batch_size]")
        print("\nExample:")
        print("  python3 reprocess_twitter.py run 0 25     # Start from beginning, 25 at a time")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "status":
        get_stats()
    elif cmd == "list":
        ids = get_twitter_message_ids()
        print(f"Twitter message IDs ({len(ids)} total):")
        print(ids[:50], "..." if len(ids) > 50 else "")
    elif cmd == "run":
        start_idx = int(sys.argv[2]) if len(sys.argv) > 2 else 0
        batch_size = int(sys.argv[3]) if len(sys.argv) > 3 else 25
        reprocess_twitter_batch(start_idx, batch_size)
    else:
        print(f"Unknown command: {cmd}")
