#!/usr/bin/env python3
"""
Extract x23.ai URLs from original Telegram message entities.

The x23 messages have "More details" text that links to x23.ai URLs.
These URLs were not captured during initial import because only plain text
URLs were extracted, not hyperlink entities.

This script:
1. Gets all x23 telegram_ids from the database
2. Re-fetches those messages from Telegram
3. Extracts URLs from message entities (hyperlinks)
4. Updates the review_queue with the extracted x23.ai URLs

Run: python3 extract_x23_urls.py [command]

Commands:
  check     - Check what x23.ai URLs can be found (dry run)
  extract   - Extract and save x23.ai URLs to review_queue
  sample    - Show sample of 5 messages with their entities
"""
import sys
import os
import asyncio
import logging
from datetime import datetime
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.tl.types import MessageEntityTextUrl, MessageEntityUrl

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv('DATABASE_URL')
TELEGRAM_API_ID = os.getenv('TELEGRAM_API_ID')
TELEGRAM_API_HASH = os.getenv('TELEGRAM_API_HASH')
TELEGRAM_PHONE = os.getenv('TELEGRAM_PHONE')
TELEGRAM_CHANNEL = os.getenv('TELEGRAM_CHANNEL_USERNAME', 'TheGrantWire')


def get_x23_telegram_ids():
    """Get all telegram_ids for x23 messages."""
    engine = create_engine(DATABASE_URL)
    try:
        with engine.connect() as conn:
            result = conn.execute(text("""
                SELECT m.id, m.telegram_id, s.id as summary_id, s.title
                FROM messages m
                JOIN summaries s ON s.message_id = m.id
                WHERE m.import_method = 'x23'
                ORDER BY m.telegram_id
            """)).fetchall()
            return result
    finally:
        engine.dispose()


def extract_urls_from_entities(message):
    """Extract all URLs from message entities."""
    urls = []

    if not message.entities:
        return urls

    text = message.message or ""

    for entity in message.entities:
        # MessageEntityTextUrl: text that links to a URL (like "More details" -> x23.ai)
        if isinstance(entity, MessageEntityTextUrl):
            urls.append({
                'type': 'text_url',
                'url': entity.url,
                'text': text[entity.offset:entity.offset + entity.length]
            })
        # MessageEntityUrl: a URL that appears as text in the message
        elif isinstance(entity, MessageEntityUrl):
            url_text = text[entity.offset:entity.offset + entity.length]
            urls.append({
                'type': 'url',
                'url': url_text,
                'text': url_text
            })

    return urls


async def fetch_messages_by_ids(client, channel, telegram_ids):
    """Fetch specific messages by their IDs."""
    messages = {}

    # Telegram's get_messages can fetch multiple at once
    # Process in batches to avoid timeouts
    batch_size = 100

    for i in range(0, len(telegram_ids), batch_size):
        batch = telegram_ids[i:i + batch_size]
        try:
            fetched = await client.get_messages(channel, ids=batch)
            for msg in fetched:
                if msg:
                    messages[msg.id] = msg
        except Exception as e:
            logger.warning(f"Error fetching batch {i}-{i+batch_size}: {e}")

    return messages


def get_session_path():
    """Get the session path, checking for existing sessions."""
    # Check for existing session files
    possible_paths = [
        '~/Documents/GitHub/tele-sum/telesum_session',
        '~/Documents/GitHub/tele-sum/telegram_session',
        'telesum_session',
    ]
    for path in possible_paths:
        if os.path.exists(f"{path}.session"):
            return path
    return 'telesum_session'


async def check_x23_urls():
    """Check what x23.ai URLs can be extracted (dry run)."""
    session_path = get_session_path()
    client = TelegramClient(session_path, TELEGRAM_API_ID, TELEGRAM_API_HASH)

    try:
        await client.start(phone=TELEGRAM_PHONE)
        channel = await client.get_entity(TELEGRAM_CHANNEL)

        # Get x23 message IDs
        x23_entries = get_x23_telegram_ids()
        telegram_ids = [row[1] for row in x23_entries]

        print(f"Found {len(x23_entries)} x23 entries in database")
        print(f"Fetching messages from Telegram...")

        # Fetch messages
        messages = await fetch_messages_by_ids(client, channel, telegram_ids)
        print(f"Fetched {len(messages)} messages from Telegram")

        # Extract URLs
        x23_urls_found = 0
        other_urls_found = 0
        no_entities = 0
        not_found = 0

        for db_id, tg_id, summary_id, title in x23_entries:
            if tg_id not in messages:
                not_found += 1
                continue

            msg = messages[tg_id]
            urls = extract_urls_from_entities(msg)

            if not urls:
                no_entities += 1
                continue

            for url_info in urls:
                if 'x23.ai' in url_info['url']:
                    x23_urls_found += 1
                    print(f"  [{summary_id}] {title[:40]}...")
                    print(f"    -> {url_info['url']}")
                else:
                    other_urls_found += 1

        print("\n" + "=" * 60)
        print("SUMMARY")
        print("=" * 60)
        print(f"Total x23 entries:       {len(x23_entries)}")
        print(f"Messages found:          {len(messages)}")
        print(f"Messages not found:      {not_found}")
        print(f"x23.ai URLs found:       {x23_urls_found}")
        print(f"Other URLs found:        {other_urls_found}")
        print(f"No entities:             {no_entities}")

    finally:
        await client.disconnect()


async def sample_messages():
    """Show sample messages with their entities."""
    session_path = get_session_path()
    client = TelegramClient(session_path, TELEGRAM_API_ID, TELEGRAM_API_HASH)

    try:
        await client.start(phone=TELEGRAM_PHONE)
        channel = await client.get_entity(TELEGRAM_CHANNEL)

        # Get first 5 x23 message IDs
        x23_entries = get_x23_telegram_ids()[:5]
        telegram_ids = [row[1] for row in x23_entries]

        print(f"Fetching {len(telegram_ids)} sample messages...")

        messages = await fetch_messages_by_ids(client, channel, telegram_ids)

        for db_id, tg_id, summary_id, title in x23_entries:
            print("\n" + "=" * 80)
            print(f"Summary ID: {summary_id} | Telegram ID: {tg_id}")
            print(f"Title: {title}")
            print("-" * 80)

            if tg_id not in messages:
                print("MESSAGE NOT FOUND IN TELEGRAM")
                continue

            msg = messages[tg_id]
            print(f"Message text (first 300 chars):")
            print(msg.message[:300] if msg.message else "No text")
            print("-" * 40)

            if msg.entities:
                print(f"Entities ({len(msg.entities)}):")
                for i, entity in enumerate(msg.entities):
                    print(f"  {i+1}. {type(entity).__name__}")
                    if hasattr(entity, 'url'):
                        print(f"      URL: {entity.url}")
                    if hasattr(entity, 'offset') and hasattr(entity, 'length'):
                        text_slice = msg.message[entity.offset:entity.offset + entity.length]
                        print(f"      Text: {text_slice[:50]}...")
            else:
                print("No entities")

    finally:
        await client.disconnect()


async def extract_and_save():
    """Extract x23.ai URLs and save to review_queue."""
    session_path = get_session_path()
    client = TelegramClient(session_path, TELEGRAM_API_ID, TELEGRAM_API_HASH)
    engine = create_engine(DATABASE_URL)

    try:
        await client.start(phone=TELEGRAM_PHONE)
        channel = await client.get_entity(TELEGRAM_CHANNEL)

        # Get x23 message IDs
        x23_entries = get_x23_telegram_ids()
        telegram_ids = [row[1] for row in x23_entries]

        print(f"Found {len(x23_entries)} x23 entries")
        print("Fetching messages from Telegram...")

        messages = await fetch_messages_by_ids(client, channel, telegram_ids)
        print(f"Fetched {len(messages)} messages")

        # Process and update
        updated = 0
        not_found = 0
        no_x23_url = 0

        with engine.connect() as conn:
            for db_id, tg_id, summary_id, title in x23_entries:
                if tg_id not in messages:
                    not_found += 1
                    continue

                msg = messages[tg_id]
                urls = extract_urls_from_entities(msg)

                # Find x23.ai URL
                x23_url = None
                for url_info in urls:
                    if 'x23.ai' in url_info['url']:
                        x23_url = url_info['url']
                        break

                if not x23_url:
                    no_x23_url += 1
                    continue

                # Update review_queue if entry exists
                result = conn.execute(text("""
                    UPDATE review_queue
                    SET matched_url = :url,
                        notes = COALESCE(notes, '') || E'\nx23.ai URL: ' || :url,
                        updated_at = NOW()
                    WHERE summary_id = :summary_id
                    RETURNING id
                """), {'url': x23_url, 'summary_id': summary_id})

                if result.fetchone():
                    updated += 1
                    print(f"  Updated review_queue for summary {summary_id}")

                # Also update the summaries table entities
                # Use CAST to properly type the parameter for to_jsonb
                conn.execute(text("""
                    UPDATE summaries
                    SET entities = jsonb_set(
                        COALESCE(entities::jsonb, '{}'::jsonb),
                        '{source,x23_url}',
                        to_jsonb(CAST(:url AS text))
                    )
                    WHERE id = :summary_id
                """), {'url': x23_url, 'summary_id': summary_id})

            conn.commit()

        print("\n" + "=" * 60)
        print("RESULTS")
        print("=" * 60)
        print(f"Total x23 entries:       {len(x23_entries)}")
        print(f"Messages found:          {len(messages)}")
        print(f"Messages not found:      {not_found}")
        print(f"No x23.ai URL:           {no_x23_url}")
        print(f"Review queue updated:    {updated}")

    finally:
        await client.disconnect()
        engine.dispose()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "check":
        asyncio.run(check_x23_urls())
    elif cmd == "sample":
        asyncio.run(sample_messages())
    elif cmd == "extract":
        asyncio.run(extract_and_save())
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)
