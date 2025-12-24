#!/usr/bin/env python3
"""Sync new messages from Telegram channel (non-interactive)"""
import asyncio
import os
import sys
from pathlib import Path

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / '.env')

from telethon import TelegramClient
from telethon.tl.types import Message as TelegramMessage
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import re

def extract_urls(text: str) -> list:
    url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
    urls = re.findall(url_pattern, text or '')
    return list(set(urls))

async def sync_new_messages():
    print("=" * 60)
    print("🔄 Syncing new messages from Telegram")
    print("=" * 60)

    # Config
    api_id = int(os.getenv('TELEGRAM_API_ID'))
    api_hash = os.getenv('TELEGRAM_API_HASH')
    phone = os.getenv('TELEGRAM_PHONE')
    channel_name = os.getenv('TELEGRAM_CHANNEL_USERNAME')
    database_url = os.getenv('DATABASE_URL')

    # Setup database
    from src.models.models import Base, Message, Summary, ProcessingStatus
    engine = create_engine(database_url)
    Session = sessionmaker(bind=engine)
    db = Session()

    existing_count = db.query(Message).count()
    print(f"\n📊 Current messages in database: {existing_count}")

    # Get the highest telegram_id we have
    latest = db.query(Message).order_by(Message.telegram_id.desc()).first()
    min_id = latest.telegram_id if latest else 0
    print(f"📍 Latest telegram_id: {min_id}")

    # Connect to Telegram
    session_path = project_root / 'telegram_session'
    client = TelegramClient(str(session_path), api_id, api_hash)

    print("\n⏳ Connecting to Telegram...")
    await client.start(phone=phone)
    print("✅ Connected")

    # Get channel
    channel = await client.get_entity(channel_name)
    channel_id = str(channel.id)
    print(f"✅ Found channel: {getattr(channel, 'title', channel_name)}")

    # Fetch only new messages (after our latest)
    print(f"\n⏳ Fetching messages newer than ID {min_id}...")

    new_count = 0
    async for message in client.iter_messages(channel, min_id=min_id, limit=None):
        if not isinstance(message, TelegramMessage) or not message.message:
            continue

        # Double-check for duplicates
        existing = db.query(Message).filter(
            Message.telegram_id == message.id,
            Message.channel_id == channel_id
        ).first()

        if existing:
            continue

        # Extract URLs
        urls = extract_urls(message.message)

        # Create message
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

        # Create pending summary
        summary = Summary(
            message_id=msg.id,
            processing_status=ProcessingStatus.PENDING
        )
        db.add(summary)
        new_count += 1

        print(f"   + New: {message.id} - {message.message[:50]}...")

    db.commit()
    await client.disconnect()

    print("\n" + "=" * 60)
    print(f"✅ Sync complete! Added {new_count} new messages")
    print("=" * 60)

    if new_count > 0:
        total = db.query(Message).count()
        print(f"\n📊 Total messages now: {total}")
        print(f"\n📋 To process new messages, run:")
        print(f"   python3 process_batch.py --start {existing_count + 1} --count {new_count}")

    db.close()

if __name__ == "__main__":
    asyncio.run(sync_new_messages())
