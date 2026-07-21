"""Processor-time round-trip guard: distribution echoes must not burn AI calls.

The bot-time guard (telegram_bot.py) races wire-room-ops' post-send log
write: Telegram delivers the channel post to our long-poll BEFORE the
sibling app commits its distribution_logs success row (measured July 2026:
33 of 54 distributed posts imported with a 0-second lag — the bot-time
check can never win that race for fresh posts).

The processor runs >=2 minutes later, when the log row definitively
exists, so process_message must re-check the guard before spending an AI
call and mark echoes as terminal SUCCESS with a marker instead.

Uses in-memory SQLite + the real ORM models, with distribution_logs
created manually (it is the sibling app's table, not in our metadata).
"""

import asyncio
from datetime import datetime, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from src.models.models import Base, ChannelConfig, Message, ProcessingStatus, Summary
from src.services.processor import MessageProcessor


class StubAIService:
    def __init__(self):
        self.digest_calls = []
        self.content_calls = []

    async def analyze_digest(self, **kwargs):
        self.digest_calls.append(kwargs)
        return None

    async def analyze_content(self, *args, **kwargs):
        self.content_calls.append((args, kwargs))
        return None


def make_db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.execute(text("""
        CREATE TABLE distribution_logs (
            id INTEGER PRIMARY KEY,
            channel TEXT NOT NULL,
            wire TEXT NOT NULL,
            status TEXT NOT NULL,
            external_id TEXT
        )
    """))
    session.commit()
    return session


def make_message(db, telegram_id):
    cc = db.query(ChannelConfig).filter(ChannelConfig.channel_id == "-100").first()
    if cc is None:
        cc = ChannelConfig(
            channel_id="-100",
            channel_username="cryptowire",
            wire="crypto",
            extraction_profile="grant",
            is_active=True,
        )
        db.add(cc)
        db.flush()
    msg = Message(
        telegram_id=telegram_id,
        channel_id="-100",
        channel_config_id=cc.id,
        wire="crypto",
        timestamp=datetime(2026, 7, 21, tzinfo=timezone.utc),
        raw_content="Some Grant Program is now open — apply today",
        import_method="bot",
    )
    db.add(msg)
    db.commit()
    return msg


def test_distribution_echo_is_skipped_without_ai_call():
    db = make_db()
    db.execute(text(
        "INSERT INTO distribution_logs (channel, wire, status, external_id) "
        "VALUES ('telegram', 'crypto', 'success', '555')"
    ))
    db.commit()
    msg = make_message(db, telegram_id=555)

    processor = MessageProcessor()
    processor.ai_service = StubAIService()

    ok = asyncio.run(processor.process_message(msg, db))

    assert ok is True
    assert processor.ai_service.content_calls == []
    assert processor.ai_service.digest_calls == []

    summary = db.query(Summary).filter(Summary.message_id == msg.id).one()
    assert summary.processing_status == ProcessingStatus.SUCCESS
    assert summary.entities.get("distribution_echo") is True
    assert summary.data_quality == "duplicate"


def test_organic_post_still_reaches_ai():
    db = make_db()
    msg = make_message(db, telegram_id=556)

    processor = MessageProcessor()
    processor.ai_service = StubAIService()

    asyncio.run(processor.process_message(msg, db))

    assert len(processor.ai_service.content_calls) == 1
