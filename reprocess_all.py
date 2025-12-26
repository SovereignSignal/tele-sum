#!/usr/bin/env python3
"""
Reprocess ALL summaries with the improved pipeline:
- Twitter scraper for accurate tweet content
- Title generation (4-5 words)
- Updated Grok prompts for accuracy

This ensures all entries have accurate summaries and titles.
"""
import subprocess
import sys
import time
import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

def get_stats():
    """Get current processing stats."""
    engine = create_engine(os.getenv('DATABASE_URL'))
    try:
        with engine.connect() as conn:
            result = conn.execute(text("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN title IS NOT NULL AND title != '' THEN 1 ELSE 0 END) as with_title,
                    SUM(CASE WHEN summary IS NOT NULL AND summary != '' THEN 1 ELSE 0 END) as with_summary
                FROM summaries
            """)).fetchone()

            print(f"\nProcessing Status:")
            print(f"  Total entries: {result[0]}")
            print(f"  With titles: {result[1]}")
            print(f"  With summaries: {result[2]}")
            print(f"  Remaining to process: {result[0] - result[1]}")
            return result[0], result[1]
    finally:
        engine.dispose()

def reset_range(start_id, end_id):
    """Reset summaries in a range to PENDING."""
    engine = create_engine(os.getenv('DATABASE_URL'))
    try:
        with engine.connect() as conn:
            result = conn.execute(text(f"""
                UPDATE summaries
                SET summary = NULL,
                    title = NULL,
                    processing_status = 'PENDING',
                    processing_attempts = 0
                WHERE message_id BETWEEN {start_id} AND {end_id}
                RETURNING id
            """))
            conn.commit()
            count = result.rowcount
            print(f"Reset {count} summaries (IDs {start_id}-{end_id})")
            return count
    finally:
        engine.dispose()

def get_next_unprocessed():
    """Find the next message ID that needs processing (no title)."""
    engine = create_engine(os.getenv('DATABASE_URL'))
    try:
        with engine.connect() as conn:
            result = conn.execute(text("""
                SELECT m.id
                FROM messages m
                JOIN summaries s ON s.message_id = m.id
                WHERE s.title IS NULL OR s.title = ''
                ORDER BY m.id
                LIMIT 1
            """)).fetchone()
            return result[0] if result else None
    finally:
        engine.dispose()

def run_batch(start_id, count):
    """Run process_batch.py for a range."""
    try:
        result = subprocess.run(
            ['python3', 'process_batch.py', '--start', str(start_id), '--count', str(count), '--no-sync'],
            capture_output=False,
            timeout=600  # 10 minute timeout
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        print(f"  Batch timed out")
        return False
    except Exception as e:
        print(f"  Error: {e}")
        return False

def reprocess_range(start_id, end_id, chunk_size=25):
    """Reprocess a range of message IDs."""
    print(f"\n{'='*60}")
    print(f"Reprocessing IDs {start_id} to {end_id}")
    print(f"Chunk size: {chunk_size}")
    print(f"{'='*60}\n")

    # Reset the range
    reset_range(start_id, end_id)

    # Process in chunks
    current_id = start_id
    while current_id <= end_id:
        chunk_end = min(current_id + chunk_size - 1, end_id)
        print(f"\nProcessing chunk: IDs {current_id} to {chunk_end}...")

        success = run_batch(current_id, chunk_size)

        if not success:
            print(f"  Chunk failed, continuing...")

        current_id += chunk_size

        # Brief pause between chunks
        time.sleep(1)

    print(f"\n*** Range {start_id}-{end_id} complete ***")
    get_stats()

def reprocess_all(chunk_size=25, start_from=1):
    """Reprocess ALL entries from start_from."""
    engine = create_engine(os.getenv('DATABASE_URL'))
    try:
        with engine.connect() as conn:
            max_id = conn.execute(text("SELECT MAX(id) FROM messages")).fetchone()[0]
    finally:
        engine.dispose()

    print(f"\n{'='*60}")
    print(f"FULL REPROCESSING: IDs {start_from} to {max_id}")
    print(f"Total entries: {max_id - start_from + 1}")
    print(f"Chunk size: {chunk_size}")
    print(f"{'='*60}\n")

    # Process in ranges of 100 to allow progress tracking
    range_size = 100
    current_start = start_from

    while current_start <= max_id:
        current_end = min(current_start + range_size - 1, max_id)

        print(f"\n>>> RANGE {current_start} to {current_end} <<<")
        reprocess_range(current_start, current_end, chunk_size)

        current_start += range_size

    print(f"\n{'='*60}")
    print("FULL REPROCESSING COMPLETE")
    print(f"{'='*60}")
    get_stats()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 reprocess_all.py status")
        print("  python3 reprocess_all.py range <start_id> <end_id> [chunk_size]")
        print("  python3 reprocess_all.py all [chunk_size] [start_from]")
        print("  python3 reprocess_all.py continue [chunk_size]  # Continue from next unprocessed")
        print("\nExamples:")
        print("  python3 reprocess_all.py range 1 100 25      # Reprocess IDs 1-100")
        print("  python3 reprocess_all.py all 25 1            # Reprocess everything")
        print("  python3 reprocess_all.py continue 25         # Continue where left off")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "status":
        get_stats()
    elif cmd == "range":
        start_id = int(sys.argv[2])
        end_id = int(sys.argv[3])
        chunk_size = int(sys.argv[4]) if len(sys.argv) > 4 else 25
        reprocess_range(start_id, end_id, chunk_size)
    elif cmd == "all":
        chunk_size = int(sys.argv[2]) if len(sys.argv) > 2 else 25
        start_from = int(sys.argv[3]) if len(sys.argv) > 3 else 1
        reprocess_all(chunk_size, start_from)
    elif cmd == "continue":
        chunk_size = int(sys.argv[2]) if len(sys.argv) > 2 else 25
        next_id = get_next_unprocessed()
        if next_id:
            print(f"Continuing from ID {next_id}")
            reprocess_all(chunk_size, next_id)
        else:
            print("All entries have titles - nothing to reprocess")
            get_stats()
    else:
        print(f"Unknown command: {cmd}")
