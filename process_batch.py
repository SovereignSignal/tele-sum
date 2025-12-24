"""Process a batch of messages with the updated processor"""
import asyncio
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, '~/Documents/GitHub/tele-sum')

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / '.env')

from src.models import database
from src.models.models import Message, Summary, ProcessingStatus
from src.services.processor import MessageProcessor
from sqlalchemy import text

async def sync_telegram_first():
    """Always sync new Telegram messages before processing"""
    from telethon import TelegramClient
    from telethon.tl.types import Message as TelegramMessage
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from src.models.models import Base, Summary as SummaryModel

    print("=" * 60)
    print("🔄 Checking for new Telegram messages...")
    print("=" * 60)

    api_id = int(os.getenv('TELEGRAM_API_ID'))
    api_hash = os.getenv('TELEGRAM_API_HASH')
    phone = os.getenv('TELEGRAM_PHONE')
    channel_name = os.getenv('TELEGRAM_CHANNEL_USERNAME')
    database_url = os.getenv('DATABASE_URL')

    engine = create_engine(database_url)
    Session = sessionmaker(bind=engine)
    db = Session()

    # Get latest telegram_id
    latest = db.query(Message).order_by(Message.telegram_id.desc()).first()
    min_id = latest.telegram_id if latest else 0
    existing_count = db.query(Message).count()

    # Connect to Telegram
    session_path = Path(__file__).parent / 'telegram_session'
    client = TelegramClient(str(session_path), api_id, api_hash)

    await client.start(phone=phone)
    channel = await client.get_entity(channel_name)
    channel_id = str(channel.id)

    # Fetch new messages
    new_count = 0
    async for message in client.iter_messages(channel, min_id=min_id, limit=None):
        if not isinstance(message, TelegramMessage) or not message.message:
            continue

        existing = db.query(Message).filter(
            Message.telegram_id == message.id,
            Message.channel_id == channel_id
        ).first()

        if existing:
            continue

        # Extract URLs
        url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
        urls = list(set(re.findall(url_pattern, message.message or '')))

        msg = Message(
            telegram_id=message.id,
            channel_id=channel_id,
            import_method="phone_sync",
            timestamp=message.date,
            raw_content=message.message,
            extracted_urls=urls
        )
        db.add(msg)
        db.flush()

        summary = SummaryModel(
            message_id=msg.id,
            processing_status=ProcessingStatus.PENDING
        )
        db.add(summary)
        new_count += 1

    db.commit()
    await client.disconnect()
    db.close()

    if new_count > 0:
        print(f"✅ Added {new_count} new messages (total now: {existing_count + new_count})")
    else:
        print(f"✅ No new messages (total: {existing_count})")

    print()
    return new_count

async def process_batch(start_id: int = None, count: int = 5):
    """Process a batch of messages starting from start_id (or first unprocessed)"""
    database.get_engine()
    db = database.SessionLocal()

    try:
        # If no start_id provided, find first unprocessed message
        if start_id is None:
            result = db.execute(text('''
                SELECT m.id FROM messages m
                JOIN summaries s ON s.message_id = m.id
                WHERE s.processing_status != 'SUCCESS'
                ORDER BY m.id
                LIMIT 1
            ''')).fetchone()

            if result:
                start_id = result[0]
                print(f"📍 Starting from first unprocessed: ID {start_id}")
            else:
                print("✅ All messages already processed!")
                return

        # Get messages to process
        messages = db.query(Message).filter(
            Message.id >= start_id
        ).order_by(Message.id).limit(count).all()

        if not messages:
            print("No messages found to process")
            return

        print(f"Processing {len(messages)} messages (IDs {messages[0].id} to {messages[-1].id})\n")

        processor = MessageProcessor()

        for msg in messages:
            print(f"{'='*60}")
            print(f"Message ID: {msg.id}")
            print(f"Has URLs: {bool(msg.extracted_urls)}")
            print(f"Content: {msg.raw_content[:150]}...")
            print(f"{'='*60}")

            success = await processor.process_message(msg, db)

            if success:
                # Refresh to get updated summary
                db.refresh(msg)
                summary = msg.summary
                entities = summary.entities or {}
                source = entities.get("source", {})

                print(f"\nSUCCESS!")
                print(f"  Summary: {summary.summary[:100]}..." if summary.summary else "  No summary")
                print(f"  Category: {summary.category.name if summary.category else 'None'}")
                print(f"  Source: {source.get('name', 'Unknown')}")
                print(f"  URL: {source.get('url', 'None')}")
                print(f"  Confidence: {summary.confidence}")
            else:
                print(f"\nFAILED - will retry later")

            print("\n")
            await asyncio.sleep(1.5)  # Rate limit

        print("Batch complete!")

    finally:
        db.close()

async def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=int, default=None, help="Starting message ID (auto-detects if not set)")
    parser.add_argument("--count", type=int, default=5, help="Number of messages to process")
    parser.add_argument("--no-sync", action="store_true", help="Skip Telegram sync")
    args = parser.parse_args()

    # Always sync new Telegram messages first (unless --no-sync)
    if not args.no_sync:
        await sync_telegram_first()

    await process_batch(args.start, args.count)

if __name__ == "__main__":
    asyncio.run(main())
