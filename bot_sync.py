"""
Bot-based sync script - syncs messages using Telegram Bot API.

Unlike process_batch.py (which uses phone auth), this uses bot tokens
and doesn't require interactive authentication.

Usage:
    python bot_sync.py              # Sync and process pending
    python bot_sync.py --sync-only  # Just sync, don't process
"""
import asyncio
import os
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / '.env')

import httpx
from src.models import database
from src.models.models import Message, Summary, ProcessingStatus, TelegramBot, ChannelConfig
from src.services.processor import MessageProcessor
from src.utils.url_extractor import extract_urls
from src.config import settings


async def bot_sync():
    """Sync messages using bot API (getUpdates)"""
    print("=" * 60)
    print("🤖 Bot-based Telegram Sync")
    print("=" * 60)

    database.get_engine()
    db = database.SessionLocal()

    try:
        bot_tokens = settings.get_bot_tokens()
        if not bot_tokens:
            print("❌ No TELEGRAM_BOT_TOKENS configured")
            return 0

        total_imported = 0

        for bot_token in bot_tokens:
            print(f"\n📡 Syncing with bot token: {bot_token[:20]}...")

            # Get or create bot record
            bot_record = db.query(TelegramBot).filter(
                TelegramBot.bot_token == bot_token
            ).first()

            if not bot_record:
                bot_record = TelegramBot(
                    bot_token=bot_token,
                    is_active=True
                )
                db.add(bot_record)
                db.commit()
                db.refresh(bot_record)
                print(f"   Created new bot record (ID: {bot_record.id})")

            # Get updates starting from last known offset
            offset = (bot_record.last_update_id or 0) + 1
            print(f"   Starting from update offset: {offset}")

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f'https://api.telegram.org/bot{bot_token}/getUpdates',
                    params={'limit': 100, 'offset': offset}
                )
                updates = response.json()

                if not updates.get('ok'):
                    print(f"   ❌ Bot API error: {updates}")
                    continue

                imported = 0
                last_update_id = None

                for update in updates['result']:
                    last_update_id = update['update_id']

                    post = update.get('channel_post') or update.get('message')
                    if not post:
                        continue

                    telegram_id = post['message_id']
                    chat_id = str(post['chat']['id'])
                    chat_username = post['chat'].get('username', '')
                    text_content = post.get('text') or post.get('caption') or ''
                    timestamp = post['date']

                    # Check if channel is configured
                    channel_config = db.query(ChannelConfig).filter(
                        ChannelConfig.channel_id == chat_id,
                        ChannelConfig.is_active == True
                    ).first()

                    # Also check by username
                    if not channel_config and chat_username:
                        channel_config = db.query(ChannelConfig).filter(
                            ChannelConfig.channel_username == chat_username,
                            ChannelConfig.is_active == True
                        ).first()
                        if channel_config:
                            channel_config.channel_id = chat_id
                            channel_config.channel_title = post['chat'].get('title', chat_username)
                            db.commit()
                            print(f"   Updated channel_id for @{chat_username} -> {chat_id}")

                    if not channel_config:
                        print(f"   Skipping message from unconfigured channel: {chat_id} (@{chat_username})")
                        continue

                    # Check if already exists
                    existing = db.query(Message).filter(
                        Message.telegram_id == telegram_id,
                        Message.channel_id == chat_id
                    ).first()

                    if existing:
                        continue

                    # Extract URLs and create message
                    urls = extract_urls(text_content) if text_content else []

                    new_msg = Message(
                        telegram_id=telegram_id,
                        channel_id=chat_id,
                        bot_id=bot_record.id,
                        import_method='bot_sync_script',
                        timestamp=datetime.utcfromtimestamp(timestamp),
                        raw_content=text_content,
                        extracted_urls=urls
                    )
                    db.add(new_msg)
                    db.flush()

                    # Create summary with PENDING status
                    summary = Summary(
                        message_id=new_msg.id,
                        processing_status=ProcessingStatus.PENDING,
                        processing_attempts=0
                    )
                    db.add(summary)
                    imported += 1
                    print(f"   ✅ Imported TG#{telegram_id}: {text_content[:50]}...")

                db.commit()

                # Update offset
                if last_update_id is not None:
                    bot_record.last_update_id = last_update_id
                    db.commit()

                print(f"   Imported {imported} new messages from this bot")
                total_imported += imported

        print(f"\n{'=' * 60}")
        print(f"✅ Total imported: {total_imported} messages")
        return total_imported

    finally:
        db.close()


async def process_pending(limit: int = 20):
    """Process pending messages with Grok AI"""
    print(f"\n{'=' * 60}")
    print(f"🧠 Processing pending messages (limit: {limit})")
    print("=" * 60)

    database.get_engine()
    db = database.SessionLocal()

    try:
        processor = MessageProcessor()
        processed = await processor.process_batch(db, limit=limit, include_retries=True)
        print(f"\n✅ Processed {processed} messages")
        return processed
    finally:
        db.close()


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Bot-based Telegram sync")
    parser.add_argument("--sync-only", action="store_true", help="Only sync, don't process")
    parser.add_argument("--process-only", action="store_true", help="Only process, don't sync")
    parser.add_argument("--limit", type=int, default=20, help="Number of messages to process")
    args = parser.parse_args()

    if not args.process_only:
        await bot_sync()

    if not args.sync_only:
        await process_pending(args.limit)


if __name__ == "__main__":
    asyncio.run(main())
