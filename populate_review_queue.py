#!/usr/bin/env python3
"""
Populate the review_queue table with entries that need manual review.

This script:
1. Creates the review_queue table if it doesn't exist
2. Populates it with x23 entries that have issues:
   - url_missing: entries marked as 'unavailable' (search_failed, unknown_protocol, no_forum)
   - url_mismatch: entries where backfilled URL may not match (detected via spot check)

Run: python3 populate_review_queue.py [command]

Commands:
  create    - Create the review_queue table
  populate  - Populate with entries needing review
  stats     - Show current review queue stats
  list      - List entries in review queue
  complete  - Mark an entry as corrected (removes from pending)
  clear     - Clear all entries from review queue
"""
import sys
import os
from datetime import datetime
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv('DATABASE_URL')


def create_table():
    """Create the review_queue table."""
    engine = create_engine(DATABASE_URL)
    try:
        with engine.connect() as conn:
            # Check if table exists
            result = conn.execute(text("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_name = 'review_queue'
                )
            """)).fetchone()

            if result[0]:
                print("Table 'review_queue' already exists")
                return

            # Create the table
            conn.execute(text("""
                CREATE TYPE review_status AS ENUM ('pending', 'in_progress', 'corrected', 'skipped');
            """))

            conn.execute(text("""
                CREATE TABLE review_queue (
                    id SERIAL PRIMARY KEY,
                    summary_id INTEGER UNIQUE NOT NULL REFERENCES summaries(id),
                    issue_type VARCHAR(50) NOT NULL,
                    our_title VARCHAR(255),
                    matched_url TEXT,
                    matched_page_title VARCHAR(255),
                    protocol VARCHAR(100),
                    notes TEXT,
                    status review_status DEFAULT 'pending',
                    corrected_url TEXT,
                    reviewed_by VARCHAR(100),
                    reviewed_at TIMESTAMP WITH TIME ZONE,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    updated_at TIMESTAMP WITH TIME ZONE
                );

                CREATE INDEX ix_review_queue_status_type ON review_queue(status, issue_type);
                CREATE INDEX ix_review_queue_issue_type ON review_queue(issue_type);
                CREATE INDEX ix_review_queue_summary_id ON review_queue(summary_id);
            """))
            conn.commit()
            print("Created 'review_queue' table successfully")
    except Exception as e:
        if 'already exists' in str(e):
            print("Table or type already exists, skipping creation")
        else:
            raise e
    finally:
        engine.dispose()


def populate_unavailable():
    """Populate review queue with entries marked as unavailable."""
    engine = create_engine(DATABASE_URL)
    try:
        with engine.connect() as conn:
            # Get all x23 entries marked as unavailable
            result = conn.execute(text("""
                SELECT s.id, s.title,
                       s.entities->'source'->>'url' as source_url,
                       s.entities->'source'->>'unavailable_reason' as reason,
                       s.entities->'source'->>'protocol' as protocol
                FROM messages m
                JOIN summaries s ON s.message_id = m.id
                WHERE m.import_method = 'x23'
                  AND s.entities->'source'->>'url' = 'unavailable'
                ORDER BY s.id
            """)).fetchall()

            print(f"Found {len(result)} unavailable entries to add to review queue")

            added = 0
            skipped = 0
            for row in result:
                summary_id = row[0]
                title = row[1]
                reason = row[3]
                protocol = row[4]

                # Map reason to issue_type
                issue_type = reason if reason else 'unknown'

                # Check if already in queue
                exists = conn.execute(text(
                    "SELECT 1 FROM review_queue WHERE summary_id = :id"
                ), {'id': summary_id}).fetchone()

                if exists:
                    skipped += 1
                    continue

                # Insert into review queue
                conn.execute(text("""
                    INSERT INTO review_queue (summary_id, issue_type, our_title, protocol, notes)
                    VALUES (:summary_id, :issue_type, :title, :protocol, :notes)
                """), {
                    'summary_id': summary_id,
                    'issue_type': issue_type,
                    'title': title,
                    'protocol': protocol,
                    'notes': f"Unavailable: {reason}"
                })
                added += 1

            conn.commit()
            print(f"Added {added} entries to review queue ({skipped} already existed)")
    finally:
        engine.dispose()


def get_stats():
    """Show review queue statistics."""
    engine = create_engine(DATABASE_URL)
    try:
        with engine.connect() as conn:
            # Overall stats
            total = conn.execute(text("SELECT COUNT(*) FROM review_queue")).fetchone()[0]

            # By status
            by_status = conn.execute(text("""
                SELECT status, COUNT(*)
                FROM review_queue
                GROUP BY status
                ORDER BY status
            """)).fetchall()

            # By issue type (pending only)
            by_type = conn.execute(text("""
                SELECT issue_type, COUNT(*)
                FROM review_queue
                WHERE status = 'pending'
                GROUP BY issue_type
                ORDER BY COUNT(*) DESC
            """)).fetchall()

            # By protocol (pending only)
            by_protocol = conn.execute(text("""
                SELECT COALESCE(protocol, 'Unknown') as proto, COUNT(*)
                FROM review_queue
                WHERE status = 'pending'
                GROUP BY protocol
                ORDER BY COUNT(*) DESC
                LIMIT 15
            """)).fetchall()

            print("\n" + "=" * 60)
            print("REVIEW QUEUE STATISTICS")
            print("=" * 60)
            print(f"\nTotal entries: {total}")

            print("\nBy Status:")
            for row in by_status:
                print(f"  {row[0]:15} | {row[1]}")

            print("\nBy Issue Type (pending only):")
            for row in by_type:
                print(f"  {row[0]:20} | {row[1]}")

            print("\nBy Protocol (pending only, top 15):")
            for row in by_protocol:
                print(f"  {row[0]:20} | {row[1]}")

    finally:
        engine.dispose()


def list_entries(issue_type=None, protocol=None, limit=50):
    """List entries in the review queue."""
    engine = create_engine(DATABASE_URL)
    try:
        with engine.connect() as conn:
            query = """
                SELECT rq.id, rq.summary_id, rq.issue_type, rq.our_title,
                       rq.protocol, rq.status, rq.matched_url
                FROM review_queue rq
                WHERE rq.status = 'pending'
            """
            params = {}

            if issue_type:
                query += " AND rq.issue_type = :issue_type"
                params['issue_type'] = issue_type

            if protocol:
                query += " AND rq.protocol = :protocol"
                params['protocol'] = protocol

            query += f" ORDER BY rq.id LIMIT {limit}"

            result = conn.execute(text(query), params).fetchall()

            print(f"\nReview Queue Entries ({len(result)} shown):")
            print("=" * 100)
            for row in result:
                print(f"ID {row[0]:4} | Summary {row[1]:4} | {row[2]:18} | {row[4] or 'Unknown':15} | {row[3][:40] if row[3] else 'No title'}")
    finally:
        engine.dispose()


def show_entry(review_id):
    """Show full details of a single review queue entry."""
    engine = create_engine(DATABASE_URL)
    try:
        with engine.connect() as conn:
            result = conn.execute(text("""
                SELECT rq.id, rq.summary_id, rq.issue_type, rq.our_title,
                       rq.protocol, rq.status, rq.matched_url, rq.notes,
                       rq.summary_text, rq.raw_content, rq.protocols,
                       rq.amounts, rq.key_terms, rq.original_date, rq.source_name
                FROM review_queue rq
                WHERE rq.id = :id
            """), {'id': review_id}).fetchone()

            if not result:
                print(f"Entry {review_id} not found")
                return

            print("\n" + "=" * 80)
            print(f"REVIEW QUEUE ENTRY #{result[0]}")
            print("=" * 80)
            print(f"Summary ID:     {result[1]}")
            print(f"Issue Type:     {result[2]}")
            print(f"Status:         {result[5]}")
            print(f"Protocol:       {result[4] or 'Unknown'}")
            print(f"Original Date:  {result[13] or 'Unknown'}")
            print(f"Source Name:    {result[14] or 'Unknown'}")
            print("-" * 80)
            print(f"TITLE: {result[3]}")
            print("-" * 80)
            print(f"PROTOCOLS: {result[10] or 'None'}")
            print(f"AMOUNTS:   {result[11] or 'None'}")
            print(f"KEY TERMS: {result[12] or 'None'}")
            print("-" * 80)
            print("SUMMARY:")
            print(result[8] or 'No summary')
            print("-" * 80)
            print("RAW CONTENT (x23 original):")
            print(result[9] or 'No raw content')
            print("-" * 80)
            if result[6]:
                print(f"MATCHED URL: {result[6]}")
            if result[7]:
                print(f"NOTES: {result[7]}")
            print("=" * 80)
    finally:
        engine.dispose()


def export_entries(issue_type=None, protocol=None, limit=100, format='text'):
    """Export entries for easy sharing/review."""
    engine = create_engine(DATABASE_URL)
    try:
        with engine.connect() as conn:
            query = """
                SELECT rq.id, rq.summary_id, rq.issue_type, rq.our_title,
                       rq.protocol, rq.summary_text, rq.raw_content,
                       rq.protocols, rq.amounts, rq.key_terms,
                       rq.original_date, rq.source_name
                FROM review_queue rq
                WHERE rq.status = 'pending'
            """
            params = {}

            if issue_type:
                query += " AND rq.issue_type = :issue_type"
                params['issue_type'] = issue_type

            if protocol:
                query += " AND rq.protocol = :protocol"
                params['protocol'] = protocol

            query += f" ORDER BY rq.id LIMIT {limit}"

            result = conn.execute(text(query), params).fetchall()

            print(f"\n# Review Queue Export ({len(result)} entries)")
            print(f"# Issue Type: {issue_type or 'All'}")
            print(f"# Protocol: {protocol or 'All'}")
            print()

            for row in result:
                print("=" * 80)
                print(f"## Entry #{row[0]} (Summary ID: {row[1]})")
                print(f"**Issue:** {row[2]} | **Protocol:** {row[4] or 'Unknown'}")
                print(f"**Title:** {row[3]}")
                print(f"**Date:** {row[10] or 'Unknown'} | **Source:** {row[11] or 'Unknown'}")
                print()
                print(f"**Protocols:** {row[7] or 'None'}")
                print(f"**Amounts:** {row[8] or 'None'}")
                print(f"**Key Terms:** {row[9] or 'None'}")
                print()
                print("**Summary:**")
                print(row[5] or 'No summary')
                print()
                print("**Original x23 Content:**")
                print(row[6] or 'No content')
                print()
    finally:
        engine.dispose()


def mark_complete(review_id, corrected_url=None, skip=False):
    """Mark an entry as corrected or skipped."""
    engine = create_engine(DATABASE_URL)
    try:
        with engine.connect() as conn:
            status = 'skipped' if skip else 'corrected'

            result = conn.execute(text("""
                UPDATE review_queue
                SET status = :status,
                    corrected_url = :url,
                    reviewed_at = NOW()
                WHERE id = :id
                RETURNING summary_id
            """), {
                'status': status,
                'url': corrected_url,
                'id': review_id
            })

            row = result.fetchone()
            if row:
                # If corrected with a URL, update the summary's entities
                if corrected_url and not skip:
                    import json
                    summary_id = row[0]

                    # Get current entities
                    entities_row = conn.execute(text(
                        "SELECT entities FROM summaries WHERE id = :id"
                    ), {'id': summary_id}).fetchone()

                    entities = entities_row[0] if entities_row and entities_row[0] else {}

                    # Update source URL
                    if 'source' not in entities:
                        entities['source'] = {}
                    entities['source']['url'] = corrected_url
                    entities['source']['manually_corrected'] = True

                    # Remove unavailable markers
                    if 'unavailable_reason' in entities.get('source', {}):
                        del entities['source']['unavailable_reason']

                    # Save
                    conn.execute(text(
                        "UPDATE summaries SET entities = CAST(:entities AS json) WHERE id = :id"
                    ), {'entities': json.dumps(entities), 'id': summary_id})

                conn.commit()
                print(f"Marked review queue entry {review_id} as {status}")
            else:
                print(f"Entry {review_id} not found")
    finally:
        engine.dispose()


def clear_queue():
    """Clear all entries from the review queue."""
    engine = create_engine(DATABASE_URL)
    try:
        with engine.connect() as conn:
            result = conn.execute(text("DELETE FROM review_queue RETURNING id"))
            count = len(result.fetchall())
            conn.commit()
            print(f"Cleared {count} entries from review queue")
    finally:
        engine.dispose()


def remove_completed():
    """Remove entries that have status 'corrected' from the queue."""
    engine = create_engine(DATABASE_URL)
    try:
        with engine.connect() as conn:
            result = conn.execute(text("""
                DELETE FROM review_queue
                WHERE status = 'corrected'
                RETURNING id
            """))
            count = len(result.fetchall())
            conn.commit()
            print(f"Removed {count} corrected entries from review queue")
    finally:
        engine.dispose()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("""
Usage: python3 populate_review_queue.py <command> [args]

Commands:
  create                          - Create the review_queue table
  populate                        - Populate with entries needing review
  stats                           - Show current review queue stats
  list [issue_type] [protocol]    - List entries (brief view)
  show <review_id>                - Show full details of one entry
  export [issue_type] [protocol] [limit] - Export entries for review
  complete <review_id> [url]      - Mark entry as corrected with URL
  skip <review_id>                - Mark entry as skipped (not recoverable)
  cleanup                         - Remove corrected entries from queue
  clear                           - Clear all entries (requires confirmation)

Examples:
  python3 populate_review_queue.py stats
  python3 populate_review_queue.py list search_failed MakerDAO
  python3 populate_review_queue.py show 1
  python3 populate_review_queue.py export search_failed MakerDAO 10
  python3 populate_review_queue.py complete 1 https://forum.makerdao.com/t/...
  python3 populate_review_queue.py skip 42
""")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "create":
        create_table()
    elif cmd == "populate":
        populate_unavailable()
    elif cmd == "stats":
        get_stats()
    elif cmd == "list":
        issue_type = sys.argv[2] if len(sys.argv) > 2 else None
        protocol = sys.argv[3] if len(sys.argv) > 3 else None
        list_entries(issue_type, protocol)
    elif cmd == "show":
        if len(sys.argv) < 3:
            print("Usage: python3 populate_review_queue.py show <review_id>")
            sys.exit(1)
        review_id = int(sys.argv[2])
        show_entry(review_id)
    elif cmd == "export":
        issue_type = sys.argv[2] if len(sys.argv) > 2 else None
        protocol = sys.argv[3] if len(sys.argv) > 3 else None
        limit = int(sys.argv[4]) if len(sys.argv) > 4 else 100
        export_entries(issue_type, protocol, limit)
    elif cmd == "complete":
        if len(sys.argv) < 3:
            print("Usage: python3 populate_review_queue.py complete <review_id> [corrected_url]")
            sys.exit(1)
        review_id = int(sys.argv[2])
        corrected_url = sys.argv[3] if len(sys.argv) > 3 else None
        mark_complete(review_id, corrected_url)
    elif cmd == "skip":
        if len(sys.argv) < 3:
            print("Usage: python3 populate_review_queue.py skip <review_id>")
            sys.exit(1)
        review_id = int(sys.argv[2])
        mark_complete(review_id, skip=True)
    elif cmd == "clear":
        confirm = input("Are you sure you want to clear all entries? (yes/no): ")
        if confirm.lower() == 'yes':
            clear_queue()
    elif cmd == "cleanup":
        remove_completed()
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
