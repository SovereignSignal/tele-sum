"""Integration test: digest channel dispatch seam.

Verifies that a message on a digest-profile channel:
  1. Routes to ai_service.analyze_digest (not analyze_content).
  2. Passes prompt_hint through correctly.
  3. Stores a SUCCESS summary with entities carrying the expected keys.

Uses in-memory SQLite + the real ORM models. The processor's ai_service is
replaced post-construction with a stub — no network calls are made.

seen_urls is NOT created: the digest profile gates both _check_seen and
_record_seen on `profile == "grant"`, so the test passing without that table
also proves the gating works correctly.
"""

import asyncio
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.models.models import Base, ChannelConfig, Message, ProcessingStatus, Summary
from src.services.processor import MessageProcessor


class StubAIService:
    def __init__(self, result):
        self._result = result
        self.digest_calls = []
        self.content_calls = []

    async def analyze_digest(self, **kwargs):
        self.digest_calls.append(kwargs)
        return self._result

    async def analyze_content(self, *args, **kwargs):
        self.content_calls.append((args, kwargs))
        return None


def make_db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_digest_channel_routes_to_analyze_digest_and_stores_items():
    db = make_db()

    cc = ChannelConfig(
        channel_id="-1",
        channel_username="clawbytes",
        wire="claw",
        extraction_profile="digest",
        prompt_hint="claw digest",
        is_active=True,
    )
    db.add(cc)
    db.flush()

    msg = Message(
        telegram_id=1,
        channel_id="-1",
        channel_config_id=cc.id,
        wire="claw",
        timestamp=datetime(2026, 6, 12, tzinfo=timezone.utc),
        raw_content="digest text",
        import_method="bot",
    )
    db.add(msg)
    db.commit()

    digest_result = {
        "title": "T",
        "summary": "S",
        "confidence": "confident",
        "items": [
            {
                "title": "A",
                "url": None,
                "note": "",
                "category": None,
                "tags": [],
                "org": None,
            }
        ],
    }

    processor = MessageProcessor()
    processor.ai_service = StubAIService(digest_result)

    ok = asyncio.run(processor.process_message(msg, db))

    assert ok is True
    assert len(processor.ai_service.digest_calls) == 1
    assert processor.ai_service.digest_calls[0]["prompt_hint"] == "claw digest"
    assert processor.ai_service.content_calls == []

    summary = db.query(Summary).filter(Summary.message_id == msg.id).one()
    assert summary.processing_status == ProcessingStatus.SUCCESS
    assert summary.entities["profile"] == "digest"
    assert summary.entities["items"][0]["title"] == "A"
    assert summary.entities["confidence"] == "confident"
