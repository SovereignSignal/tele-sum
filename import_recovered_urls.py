#!/usr/bin/env python3
"""
Import Recovered Source URLs from CSV

This script imports source URLs that were recovered externally (e.g., via manual research
or another AI) and updates the database accordingly.

Expected CSV format (supports two formats):
  Format 1 (with summary_id):
    - summary_id: ID from summaries table
    - original_source_url: The recovered source URL
    - source_type: 'forum' or 'snapshot' (optional)
    - protocol: Protocol name (optional)

  Format 2 (with title lookup):
    - our_title: Title to match against summaries.title
    - source_url: The recovered source URL
    - source_type: 'forum' or 'snapshot' (optional)
    - protocol: Protocol name (optional)

Usage:
    python3 import_recovered_urls.py <csv_file>
    python3 import_recovered_urls.py source_urls_part1.csv

What it does:
    1. Reads entries from CSV with valid source URLs
    2. Updates summaries.entities.source.url with the recovered URL
    3. Updates review_queue.status to 'corrected'
    4. Updates summaries.data_quality to 'complete'
    5. Reports statistics on what was updated
"""

import csv
import sys
import os
import json
from datetime import datetime
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv('DATABASE_URL')


def import_urls_from_csv(csv_file: str, dry_run: bool = False):
    """Import recovered URLs from CSV and update database."""

    if not os.path.exists(csv_file):
        print(f"Error: File not found: {csv_file}")
        return

    engine = create_engine(DATABASE_URL)

    # Build title -> summary_id lookup from review_queue
    title_to_summary_id = {}
    try:
        with engine.connect() as conn:
            results = conn.execute(text("""
                SELECT rq.summary_id, rq.our_title
                FROM review_queue rq
                WHERE rq.our_title IS NOT NULL
            """)).fetchall()
            for summary_id, title in results:
                title_to_summary_id[title] = summary_id
            print(f"Loaded {len(title_to_summary_id)} title mappings from review_queue")
    except Exception as e:
        print(f"Warning: Could not load title mappings: {e}")

    # Read and parse CSV
    entries_to_update = []
    skipped = 0
    not_found = []

    with open(csv_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)

        for row in reader:
            # Support both formats: summary_id or title lookup
            summary_id = row.get('summary_id')
            title = row.get('our_title', '').strip()

            # Try to get source_url from either column name
            source_url = row.get('original_source_url', '').strip()
            if not source_url:
                source_url = row.get('source_url', '').strip()

            # If no summary_id, try to look up by title
            if not summary_id and title:
                summary_id = title_to_summary_id.get(title)
                if not summary_id:
                    not_found.append(title)
                    skipped += 1
                    continue

            # Skip if no summary_id or URL
            if not summary_id or not source_url:
                skipped += 1
                continue

            # Skip if URL is 'None', 'null', empty, or placeholder
            if source_url.lower() in ('none', 'null', '', 'n/a', 'unavailable'):
                skipped += 1
                continue

            # Validate it looks like a URL
            if not source_url.startswith(('http://', 'https://')):
                skipped += 1
                continue

            entries_to_update.append({
                'summary_id': int(summary_id),
                'source_url': source_url,
                'source_type': row.get('source_type', 'forum'),
                'protocol': row.get('protocol', '')
            })

    if not_found:
        print(f"\nWarning: Could not find summary_id for {len(not_found)} titles:")
        for t in not_found[:5]:
            print(f"  - {t}")
        if len(not_found) > 5:
            print(f"  ... and {len(not_found) - 5} more")

    print(f"\nCSV Analysis:")
    print(f"  Valid entries with URLs: {len(entries_to_update)}")
    print(f"  Skipped (no valid URL): {skipped}")

    if not entries_to_update:
        print("\nNo entries to update.")
        return

    if dry_run:
        print(f"\n[DRY RUN] Would update {len(entries_to_update)} entries")
        print("\nSample entries:")
        for entry in entries_to_update[:5]:
            print(f"  - Summary {entry['summary_id']}: {entry['source_url'][:60]}...")
        return

    # Update database
    updated = 0
    errors = 0

    try:
        with engine.connect() as conn:
            for entry in entries_to_update:
                try:
                    # 1. Update summaries.entities.source.url
                    # First get current entities
                    result = conn.execute(text("""
                        SELECT entities FROM summaries WHERE id = :id
                    """), {'id': entry['summary_id']}).fetchone()

                    if not result:
                        print(f"  Warning: Summary {entry['summary_id']} not found")
                        errors += 1
                        continue

                    entities = result[0] or {}
                    if isinstance(entities, str):
                        entities = json.loads(entities)

                    # Update source URL
                    if 'source' not in entities:
                        entities['source'] = {}
                    entities['source']['url'] = entry['source_url']
                    entities['source']['type'] = entry['source_type']
                    if entry['protocol']:
                        entities['source']['protocol'] = entry['protocol']
                    # Remove unavailable markers if present
                    entities['source'].pop('unavailable', None)
                    entities['source'].pop('unavailable_reason', None)

                    # Update entities in summaries
                    conn.execute(text("""
                        UPDATE summaries
                        SET entities = :entities,
                            data_quality = 'complete',
                            updated_at = NOW()
                        WHERE id = :id
                    """), {
                        'id': entry['summary_id'],
                        'entities': json.dumps(entities)
                    })

                    # 2. Update review_queue status to 'corrected'
                    conn.execute(text("""
                        UPDATE review_queue
                        SET status = 'corrected',
                            corrected_url = :url,
                            reviewed_by = 'csv_import',
                            reviewed_at = NOW(),
                            updated_at = NOW()
                        WHERE summary_id = :summary_id
                    """), {
                        'summary_id': entry['summary_id'],
                        'url': entry['source_url']
                    })

                    updated += 1

                except Exception as e:
                    print(f"  Error updating summary {entry['summary_id']}: {e}")
                    errors += 1

            conn.commit()

    except Exception as e:
        print(f"Database error: {e}")
        return
    finally:
        engine.dispose()

    print(f"\nResults:")
    print(f"  Successfully updated: {updated}")
    print(f"  Errors: {errors}")

    # Get updated stats
    engine = create_engine(DATABASE_URL)
    try:
        with engine.connect() as conn:
            pending = conn.execute(text("""
                SELECT COUNT(*) FROM review_queue WHERE status = 'pending'
            """)).fetchone()[0]

            corrected = conn.execute(text("""
                SELECT COUNT(*) FROM review_queue WHERE status = 'corrected'
            """)).fetchone()[0]

            complete = conn.execute(text("""
                SELECT COUNT(*) FROM summaries WHERE data_quality = 'complete'
            """)).fetchone()[0]

            incomplete = conn.execute(text("""
                SELECT COUNT(*) FROM summaries WHERE data_quality = 'incomplete'
            """)).fetchone()[0]

            print(f"\nUpdated Database Stats:")
            print(f"  Review Queue - Pending: {pending}, Corrected: {corrected}")
            print(f"  Data Quality - Complete: {complete}, Incomplete: {incomplete}")
            print(f"  Production Ready: {complete}/{complete+incomplete} ({100*complete/(complete+incomplete):.1f}%)")
    finally:
        engine.dispose()


def show_current_stats():
    """Show current database stats."""
    engine = create_engine(DATABASE_URL)
    try:
        with engine.connect() as conn:
            # Review queue stats
            rq_stats = conn.execute(text("""
                SELECT status, COUNT(*)
                FROM review_queue
                GROUP BY status
                ORDER BY status
            """)).fetchall()

            print("\nReview Queue Status:")
            for status, count in rq_stats:
                print(f"  {status}: {count}")

            # Data quality stats
            dq_stats = conn.execute(text("""
                SELECT data_quality, COUNT(*)
                FROM summaries
                GROUP BY data_quality
            """)).fetchall()

            print("\nData Quality:")
            for quality, count in dq_stats:
                print(f"  {quality}: {count}")

            # Pending entries by issue type
            pending_by_type = conn.execute(text("""
                SELECT issue_type, COUNT(*)
                FROM review_queue
                WHERE status = 'pending'
                GROUP BY issue_type
                ORDER BY COUNT(*) DESC
            """)).fetchall()

            if pending_by_type:
                print("\nPending by Issue Type:")
                for issue_type, count in pending_by_type:
                    print(f"  {issue_type}: {count}")
    finally:
        engine.dispose()


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__)
        print("\nCurrent database stats:")
        show_current_stats()
        sys.exit(0)

    command = sys.argv[1]

    if command == 'stats':
        show_current_stats()
    elif command == '--dry-run' and len(sys.argv) > 2:
        import_urls_from_csv(sys.argv[2], dry_run=True)
    elif command.endswith('.csv'):
        import_urls_from_csv(command)
    else:
        print(f"Unknown command: {command}")
        print(__doc__)
