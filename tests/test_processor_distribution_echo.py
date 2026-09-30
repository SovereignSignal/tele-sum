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
from unittest.mock import AsyncMock

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from src.models.models import Base, Category, ChannelConfig, Message, ProcessingStatus, Summary
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


def test_retry_clears_stale_category_when_application_evidence_is_missing():
    db = make_db()
    msg = make_message(db, telegram_id=557)
    category = Category(user_id=1, name="Funding Opportunities")
    db.add(category)
    db.flush()
    summary = Summary(message_id=msg.id, processing_status=ProcessingStatus.RETRY,
                      processing_attempts=1, category_id=category.id)
    db.add(summary)
    db.commit()
    processor = MessageProcessor()
    processor.ai_service.analyze_content = AsyncMock(return_value={
        "title": "Foundation Partnership", "summary": "A partnership, with no application call.",
        "category": None, "confidence": "uncertain", "source": {}, "entities": {},
    })

    assert asyncio.run(processor.process_message(msg, db)) is True
    db.refresh(summary)
    assert summary.processing_status == ProcessingStatus.SUCCESS
    assert summary.category_id is None
    assert summary.title == "Foundation Partnership"
    processor.ai_service.analyze_content.assert_awaited_once()


def test_grant_uses_stored_embedded_link_when_plain_url_list_is_empty(monkeypatch):
    db = make_db()
    msg = make_message(db, telegram_id=558)
    url = 'https://example.org/Apply?Track=AI'
    msg.extracted_urls = []
    msg.entity_links = [{'text': 'Source', 'url': url}, {'url': 'javascript:alert(1)'}]
    db.commit()
    monkeypatch.setattr('src.services.processor._check_seen', lambda *a: None)
    processor = MessageProcessor()
    processor.content_scraper.scrape_multiple_urls = AsyncMock(return_value={url: 'Apply today.'})
    processor.ai_service.analyze_content = AsyncMock(return_value={
        'title': 'Program', 'summary': 'Applications open.', 'category': None,
        'confidence': 'confident', 'entities': {}, 'source': {},
    })
    assert asyncio.run(processor.process_message(msg, db)) is True
    processor.content_scraper.scrape_multiple_urls.assert_awaited_once_with([url])
    assert processor.ai_service.analyze_content.await_args.args[1] == [url]
    assert msg.summary.entities['source']['url'] == url
    assert msg.summary.entities['urls'] == [url]


def test_grant_processing_never_writes_seen_urls(monkeypatch):
    """seen_urls belongs to wire-room-ops. A row it writes carries the owning
    scan_entries id; a row with no owner is treated over there as permanent
    "seed history" and parks the next submission of that URL invisibly. From
    2026-04-27 to 2026-09-30 this processor wrote an ownerless row for every
    summarized URL, which parked 55 of 57 bridged entries in Aug-Sep 2026.
    tele-sum may read the table (_check_seen) but must never insert into it."""
    db = make_db()
    msg = make_message(db, telegram_id=559)
    url = 'https://example.org/grants/open-call'
    msg.extracted_urls = [url]
    db.commit()
    monkeypatch.setattr('src.services.processor._check_seen', lambda *a: None)
    executed = []
    real_execute = db.execute
    def spy(statement, *args, **kwargs):
        executed.append(str(getattr(statement, 'text', statement)))
        return real_execute(statement, *args, **kwargs)
    monkeypatch.setattr(db, 'execute', spy)
    processor = MessageProcessor()
    processor.content_scraper.scrape_multiple_urls = AsyncMock(return_value={url: 'Apply today.'})
    processor.ai_service.analyze_content = AsyncMock(return_value={
        'title': 'Open Call', 'summary': 'Applications open.', 'category': None,
        'confidence': 'confident', 'entities': {}, 'source': {},
    })
    assert asyncio.run(processor.process_message(msg, db)) is True
    assert msg.summary.processing_status == ProcessingStatus.SUCCESS
    assert not [s for s in executed if 'seen_urls' in s.lower()], executed
