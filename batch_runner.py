#!/usr/bin/env python3
"""
Resilient batch processor that handles database timeouts automatically.
Runs in smaller chunks and auto-restarts on failures.
"""
import subprocess
import sys
import time
import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

def get_next_unprocessed(start_id, end_id):
    """Find next message ID that needs processing."""
    engine = create_engine(os.getenv('DATABASE_URL'))
    try:
        with engine.connect() as conn:
            result = conn.execute(text(f"""
                SELECT m.id FROM messages m
                JOIN summaries s ON m.id = s.message_id
                WHERE m.id BETWEEN {start_id} AND {end_id}
                AND (s.summary IS NULL OR s.summary = '')
                ORDER BY m.id
                LIMIT 1
            """)).fetchone()
            return result[0] if result else None
    except Exception as e:
        print(f"Error checking status: {e}")
        return start_id
    finally:
        engine.dispose()

def get_progress(start_id, end_id):
    """Get current progress count."""
    engine = create_engine(os.getenv('DATABASE_URL'))
    try:
        with engine.connect() as conn:
            result = conn.execute(text(f"""
                SELECT
                    SUM(CASE WHEN s.summary IS NOT NULL AND s.summary <> '' THEN 1 ELSE 0 END) as done,
                    COUNT(*) as total
                FROM messages m
                JOIN summaries s ON m.id = s.message_id
                WHERE m.id BETWEEN {start_id} AND {end_id}
            """)).fetchone()
            return result[0] or 0, result[1] or 0
    except:
        return 0, 0
    finally:
        engine.dispose()

def run_batch(start_id, count):
    """Run a single batch, returns True if completed without error."""
    try:
        result = subprocess.run(
            ['python3', 'process_batch.py', '--start', str(start_id), '--count', str(count), '--no-sync'],
            capture_output=True,
            text=True,
            timeout=180  # 3 minute timeout per chunk
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        print(f"  Chunk timed out, will retry remaining...")
        return False
    except Exception as e:
        print(f"  Error: {e}")
        return False

def process_range(start_id, end_id, chunk_size=25):
    """Process a range of IDs with automatic retry on failures."""
    total_range = end_id - start_id + 1
    print(f"\n{'='*60}")
    print(f"Processing IDs {start_id} to {end_id} ({total_range} messages)")
    print(f"Chunk size: {chunk_size}")
    print(f"{'='*60}\n")

    current_id = start_id
    retries = 0
    max_retries = 3

    while current_id <= end_id:
        # Check what's next
        next_id = get_next_unprocessed(current_id, end_id)

        if next_id is None:
            done, total = get_progress(start_id, end_id)
            print(f"\n*** RANGE COMPLETE: {done}/{total} messages processed ***")
            return True

        current_id = next_id
        remaining = end_id - current_id + 1
        chunk = min(chunk_size, remaining)

        print(f"Processing chunk: IDs {current_id} to {current_id + chunk - 1}...")

        success = run_batch(current_id, chunk)

        if success:
            retries = 0
            current_id += chunk
        else:
            retries += 1
            if retries >= max_retries:
                print(f"  Too many retries, moving to next chunk...")
                current_id += 5  # Skip a few and continue
                retries = 0
            else:
                print(f"  Retry {retries}/{max_retries}...")
                time.sleep(2)  # Brief pause before retry

    done, total = get_progress(start_id, end_id)
    print(f"\n*** RANGE COMPLETE: {done}/{total} messages processed ***")
    return done == total

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 batch_runner.py <start_id> <end_id> [chunk_size]")
        print("Example: python3 batch_runner.py 1101 1200 25")
        sys.exit(1)

    start_id = int(sys.argv[1])
    end_id = int(sys.argv[2])
    chunk_size = int(sys.argv[3]) if len(sys.argv) > 3 else 25

    process_range(start_id, end_id, chunk_size)
