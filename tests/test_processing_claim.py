from datetime import datetime, timezone
from unittest.mock import AsyncMock
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.models.models import Base, Message, Summary, ProcessingStatus
from src.services.processor import MessageProcessor


async def test_another_worker_processing_the_message_is_left_untouched():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        msg = Message(telegram_id=1, channel_id="-1", timestamp=datetime.now(timezone.utc), raw_content="test")
        db.add(msg); db.flush()
        s = Summary(message_id=msg.id, processing_status=ProcessingStatus.PROCESSING, processing_attempts=1)
        db.add(s); db.commit()
        processor = MessageProcessor()
        processor.ai_service.analyze_content = AsyncMock()
        processor.ai_service.analyze_digest = AsyncMock()
        assert await processor.process_message(msg, db) is False
        db.refresh(s)
        assert s.processing_status == ProcessingStatus.PROCESSING
        assert s.processing_attempts == 1
        processor.ai_service.analyze_content.assert_not_called()
        processor.ai_service.analyze_digest.assert_not_called()


async def test_rate_limited_batch_preserves_pending_attempts():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        msg = Message(telegram_id=2, channel_id="-1", timestamp=datetime.now(timezone.utc), raw_content="test")
        db.add(msg); db.flush()
        s = Summary(message_id=msg.id, processing_status=ProcessingStatus.PENDING, processing_attempts=0)
        db.add(s); db.commit()
        processor = MessageProcessor()
        processor.ai_service.rate_limiter = SimpleNamespace(can_request=lambda db: False)
        processor.process_message = AsyncMock()
        assert await processor.process_batch(db) == 0
        processor.process_message.assert_not_called()
        db.refresh(s)
        assert s.processing_status == ProcessingStatus.PENDING
        assert s.processing_attempts == 0
