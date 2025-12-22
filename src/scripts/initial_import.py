#!/usr/bin/env python3
"""
Initial Import Script for Tele-Sum

Run this locally to import historical messages from a Telegram channel.
This script handles the interactive phone verification that can't be done on Railway.

Usage:
    python -m src.scripts.initial_import

Requirements:
    - .env file with DATABASE_URL, TELEGRAM_API_ID, TELEGRAM_API_HASH, 
      TELEGRAM_PHONE, TELEGRAM_CHANNEL_USERNAME
    - Or set these as environment variables
"""

import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

# Load .env file if it exists
from dotenv import load_dotenv
load_dotenv(project_root / '.env')

from telethon import TelegramClient
from telethon.tl.types import Message as TelegramMessage


def get_config():
    """Get configuration from environment variables"""
    config = {
        'api_id': os.getenv('TELEGRAM_API_ID'),
        'api_hash': os.getenv('TELEGRAM_API_HASH'),
        'phone': os.getenv('TELEGRAM_PHONE'),
        'channel': os.getenv('TELEGRAM_CHANNEL_USERNAME'),
        'database_url': os.getenv('DATABASE_URL'),
    }
    
    missing = [k for k, v in config.items() if not v]
    if missing:
        print(f"❌ Missing required environment variables: {', '.join(missing)}")
        print("\nCreate a .env file with:")
        print("  DATABASE_URL=postgresql://...")
        print("  TELEGRAM_API_ID=your_api_id")
        print("  TELEGRAM_API_HASH=your_api_hash")
        print("  TELEGRAM_PHONE=+1234567890")
        print("  TELEGRAM_CHANNEL_USERNAME=channelname")
        sys.exit(1)
    
    config['api_id'] = int(config['api_id'])
    return config


def setup_database(database_url: str):
    """Initialize database connection and create tables"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    
    # Import models to register them
    from src.models.models import Base, Message, Summary, ProcessingStatus
    
    engine = create_engine(database_url)
    Base.metadata.create_all(bind=engine)
    
    Session = sessionmaker(bind=engine)
    return Session(), Message, Summary, ProcessingStatus


def extract_urls(text: str) -> list:
    """Extract URLs from message text"""
    import re
    url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
    urls = re.findall(url_pattern, text or '')
    return list(set(urls))


async def run_import():
    """Main import function"""
    print("=" * 60)
    print("🚀 Tele-Sum Initial Import")
    print("=" * 60)
    
    # Get configuration
    config = get_config()
    print(f"\n📱 Phone: {config['phone']}")
    print(f"📢 Channel: {config['channel']}")
    print(f"🗄️  Database: {config['database_url'][:50]}...")
    
    # Setup database
    print("\n⏳ Connecting to database...")
    db, Message, Summary, ProcessingStatus = setup_database(config['database_url'])
    print("✅ Database connected")
    
    # Check existing messages
    existing_count = db.query(Message).count()
    if existing_count > 0:
        print(f"\n⚠️  Database already has {existing_count} messages")
        response = input("Continue importing? (y/n): ").strip().lower()
        if response != 'y':
            print("Import cancelled")
            return
    
    # Create Telegram client
    # Session file stored locally - this is what allows re-authentication
    session_path = project_root / 'telegram_session'
    client = TelegramClient(str(session_path), config['api_id'], config['api_hash'])
    
    print("\n⏳ Connecting to Telegram...")
    print("   (You may be prompted for a verification code)")
    
    await client.start(phone=config['phone'])
    print("✅ Connected to Telegram")
    
    # Get channel
    print(f"\n⏳ Finding channel: {config['channel']}")
    try:
        channel = await client.get_entity(config['channel'])
        print(f"✅ Found channel: {getattr(channel, 'title', config['channel'])}")
    except Exception as e:
        print(f"❌ Could not find channel: {e}")
        await client.disconnect()
        return
    
    channel_id = str(channel.id)
    
    # Import messages
    print("\n⏳ Importing messages...")
    print("   This may take a while for large channels\n")
    
    total_imported = 0
    duplicates = 0
    batch_count = 0
    
    async for message in client.iter_messages(channel, limit=None):
        # Skip non-text messages
        if not isinstance(message, TelegramMessage) or not message.message:
            continue
        
        # Check for duplicate
        existing = db.query(Message).filter(
            Message.telegram_id == message.id,
            Message.channel_id == channel_id
        ).first()
        
        if existing:
            duplicates += 1
            continue
        
        # Extract URLs
        urls = extract_urls(message.message)
        
        # Create message record
        msg = Message(
            telegram_id=message.id,
            channel_id=channel_id,
            import_method="phone_initial",
            timestamp=message.date,
            raw_content=message.message,
            extracted_urls=urls
        )
        db.add(msg)
        db.flush()
        
        # Create pending summary record
        summary = Summary(
            message_id=msg.id,
            processing_status=ProcessingStatus.PENDING
        )
        db.add(summary)
        
        total_imported += 1
        
        # Commit in batches
        if total_imported % 100 == 0:
            db.commit()
            batch_count += 1
            print(f"   Imported {total_imported} messages...")
    
    # Final commit
    db.commit()
    
    # Disconnect
    await client.disconnect()
    
    # Summary
    print("\n" + "=" * 60)
    print("✅ Import Complete!")
    print("=" * 60)
    print(f"   Total imported: {total_imported}")
    print(f"   Duplicates skipped: {duplicates}")
    print(f"   Channel ID: {channel_id}")
    print("\n📋 Next steps:")
    print("   1. Deploy to Railway (or run locally)")
    print("   2. Set GROK_API_KEY to enable AI processing")
    print("   3. Messages will be processed automatically")
    print("   4. Set up a bot for ongoing capture (optional)")


def main():
    """Entry point"""
    try:
        asyncio.run(run_import())
    except KeyboardInterrupt:
        print("\n\nImport cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
