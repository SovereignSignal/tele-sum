#!/usr/bin/env python3
"""
Re-process Summaries with Source URLs through Grok

This script re-processes entries that have source URLs but were originally
summarized from Telegram content only. It scrapes the actual source URL
and generates a fresh summary using Grok.

Usage:
    python3 reprocess_with_urls.py [--batch-size N] [--dry-run]

Options:
    --batch-size N   Process N entries at a time (default: 10)
    --dry-run        Show what would be processed without making changes
    --limit N        Only process N total entries
"""

import asyncio
import sys
import os
from pathlib import Path

sys.path.insert(0, '~/Documents/GitHub/tele-sum')

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / '.env')

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from src.models.models import Message, Summary, ProcessingStatus
from src.services.processor import MessageProcessor

DATABASE_URL = os.getenv('DATABASE_URL')


async def reprocess_entries(batch_size: int = 10, dry_run: bool = False, limit: int = None):
    """Re-process entries that have source URLs through Grok."""

    engine = create_engine(DATABASE_URL)
    Session = sessionmaker(bind=engine)

    # Get entries to reprocess - those updated recently with valid URLs
    with engine.connect() as conn:
        query = """
            SELECT s.id, s.message_id, s.title, s.entities->'source'->>'url' as source_url
            FROM summaries s
            WHERE s.updated_at > NOW() - INTERVAL '4 hours'
              AND s.entities->'source'->>'url' IS NOT NULL
              AND s.entities->'source'->>'url' NOT LIKE '%unavailable%'
            ORDER BY s.id
        """
        if limit:
            query += f" LIMIT {limit}"

        entries = conn.execute(text(query)).fetchall()

    print(f"Found {len(entries)} entries to re-process")

    if dry_run:
        print("\n[DRY RUN] Would re-process:")
        for sid, mid, title, url in entries[:10]:
            print(f"  Summary {sid}: {title[:40]}...")
            print(f"    URL: {url[:60]}...")
        if len(entries) > 10:
            print(f"  ... and {len(entries) - 10} more")
        return

    # Process in batches
    processor = MessageProcessor()
    processed = 0
    failed = 0

    for i in range(0, len(entries), batch_size):
        batch = entries[i:i + batch_size]
        print(f"\nProcessing batch {i // batch_size + 1} ({len(batch)} entries)...")

        db = Session()
        try:
            for sid, mid, title, source_url in batch:
                print(f"  Processing Summary {sid}: {title[:40]}...")

                # Get the message
                message = db.query(Message).filter(Message.id == mid).first()
                if not message:
                    print(f"    ERROR: Message {mid} not found")
                    failed += 1
                    continue

                # Update message's extracted_urls to include the source URL
                # This ensures the processor uses our recovered URL
                if not message.extracted_urls:
                    message.extracted_urls = []
                if source_url not in message.extracted_urls:
                    message.extracted_urls = [source_url] + list(message.extracted_urls)

                # Reset processing status to force re-processing
                summary = db.query(Summary).filter(Summary.id == sid).first()
                if summary:
                    summary.processing_status = ProcessingStatus.PENDING
                    summary.processing_attempts = 0
                    db.commit()

                # Process through Grok
                success = await processor.process_message(message, db)

                if success:
                    processed += 1
                    print(f"    SUCCESS")
                else:
                    failed += 1
                    print(f"    FAILED")

                # Rate limiting
                await asyncio.sleep(1.5)

            db.commit()

        except Exception as e:
            print(f"  Batch error: {e}")
            db.rollback()
        finally:
            db.close()

        print(f"  Batch complete. Total: {processed} processed, {failed} failed")

    print(f"\n{'='*60}")
    print(f"RE-PROCESSING COMPLETE")
    print(f"  Total processed: {processed}")
    print(f"  Total failed: {failed}")
    print(f"{'='*60}")


def show_status():
    """Show current status of entries needing re-processing."""
    engine = create_engine(DATABASE_URL)

    with engine.connect() as conn:
        # Count entries
        count = conn.execute(text("""
            SELECT COUNT(*)
            FROM summaries s
            WHERE s.updated_at > NOW() - INTERVAL '4 hours'
              AND s.entities->'source'->>'url' IS NOT NULL
              AND s.entities->'source'->>'url' NOT LIKE '%unavailable%'
        """)).fetchone()[0]

        print(f"Entries to re-process: {count}")

        # Show sample
        samples = conn.execute(text("""
            SELECT s.id, s.title, s.entities->'source'->>'url' as url
            FROM summaries s
            WHERE s.updated_at > NOW() - INTERVAL '4 hours'
              AND s.entities->'source'->>'url' IS NOT NULL
              AND s.entities->'source'->>'url' NOT LIKE '%unavailable%'
            ORDER BY s.id
            LIMIT 5
        """)).fetchall()

        print("\nSample entries:")
        for sid, title, url in samples:
            print(f"  {sid}: {title[:40]}...")
            print(f"      {url[:60]}...")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch-size', type=int, default=10, help='Batch size')
    parser.add_argument('--dry-run', action='store_true', help='Dry run mode')
    parser.add_argument('--limit', type=int, help='Limit total entries')
    parser.add_argument('--status', action='store_true', help='Show status only')
    args = parser.parse_args()

    if args.status:
        show_status()
    else:
        asyncio.run(reprocess_entries(
            batch_size=args.batch_size,
            dry_run=args.dry_run,
            limit=args.limit
        ))
