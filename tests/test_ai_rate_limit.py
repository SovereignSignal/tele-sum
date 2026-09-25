"""Run against SQLite by default, and isolated PostgreSQL schemas when configured."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import os
import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from src.models.models import Base, AIRequestReservation, ChannelConfig, Message, ProcessingStatus, Summary
from src.services.ai import AIService, ProviderConfig
from src.services.ai_rate_limit import AIRequestDeferred, AIRequestLimiter
from src.services.processor import MessageProcessor


@pytest.fixture(params=["sqlite", "postgresql"])
def budget_engine(request, tmp_path):
    if request.param == "sqlite":
        engine = create_engine(f"sqlite:///{tmp_path / 'budget.db'}", connect_args={"timeout": 10})
        Base.metadata.create_all(engine)
        yield engine
        engine.dispose()
    else:
        url = os.getenv("TEST_POSTGRES_URL")
        if not url:
            pytest.skip("Set TEST_POSTGRES_URL to a disposable PostgreSQL database")
        schema = "telesum_test_" + uuid.uuid4().hex
        admin = create_engine(url)
        with admin.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
        try:
            Base.metadata.create_all(engine)
            yield engine
        finally:
            engine.dispose()
            with admin.begin() as conn:
                conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            admin.dispose()


def test_workers_share_exact_cap_and_new_instances_cannot_reset_it(budget_engine):
    def worker(_):
        with Session(budget_engine) as db:
            try:
                AIRequestLimiter(5).reserve(db)
                return True
            except AIRequestDeferred:
                return False

    with ThreadPoolExecutor(max_workers=12) as pool:
        assert sum(pool.map(worker, range(30))) == 5
    # New connection pool + limiter simulate a restarted worker.
    budget_engine.dispose()
    with Session(budget_engine) as db:
        assert AIRequestLimiter(5).remaining(db) == 0
        with pytest.raises(AIRequestDeferred, match="limit reached"):
            AIRequestLimiter(5).reserve(db)


def test_expired_slots_reopen_capacity_without_erasing_current_slots(budget_engine):
    with Session(budget_engine) as db:
        limiter = AIRequestLimiter(2)
        limiter.reserve(db)
        with budget_engine.begin() as conn:
            old = "clock_timestamp() - INTERVAL '61 minutes'" if budget_engine.dialect.name == "postgresql" else "datetime('now', '-61 minutes')"
            conn.execute(text(f"UPDATE ai_request_reservations SET reserved_at={old}"))
        limiter.reserve(db)
        assert limiter.remaining(db) == 1
        limiter.reserve(db)
        assert limiter.remaining(db) == 0
        assert db.query(AIRequestReservation).count() == 2


def test_message_rollback_does_not_refund_reserved_request(budget_engine):
    with Session(budget_engine) as db:
        db.execute(text("SELECT 1"))
        AIRequestLimiter(1).reserve(db)
        db.rollback()
        assert AIRequestLimiter(1).remaining(db) == 0


@pytest.mark.parametrize("profile", ["grant", "digest"])
async def test_failed_provider_attempt_counts_and_next_call_is_blocked(budget_engine, monkeypatch, profile):
    with Session(budget_engine) as db:
        service = AIService()
        service._config = ProviderConfig("ollama", "test", "test", "https://example.org")
        service.rate_limiter = AIRequestLimiter(1)
        provider = AsyncMock(side_effect=RuntimeError("provider unavailable"))
        monkeypatch.setattr(service, "_call_ollama", provider)
        monkeypatch.setattr(service, "_log_usage", lambda *a, **kw: None)
        async def analyze():
            if profile == "digest":
                return await service.analyze_digest("test", [], {}, db=db)
            return await service.analyze_content("test", [], {}, [], db=db)
        assert await analyze() is None
        with pytest.raises(AIRequestDeferred):
            await analyze()
        assert provider.await_count == 1
        assert service.get_status(db)["rate_limit_remaining"] == 0


@pytest.mark.parametrize("status,attempts", [(ProcessingStatus.PENDING, 0), (ProcessingStatus.RETRY, 2)])
async def test_last_slot_race_preserves_queue_state_and_attempts(budget_engine, monkeypatch, status, attempts):
    with Session(budget_engine) as db:
        cc = ChannelConfig(channel_id="-1", extraction_profile="digest")
        db.add(cc)
        db.flush()
        msg = Message(telegram_id=1, channel_id="-1", channel_config_id=cc.id,
                      timestamp=datetime.now(timezone.utc), raw_content="One item")
        db.add(msg)
        db.flush()
        summary = Summary(message_id=msg.id, processing_status=status, processing_attempts=attempts,
                          error_log="previous error", last_processed_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
        db.add(summary)
        db.commit()
        original_time = summary.last_processed_at
        processor = MessageProcessor()
        processor.ai_service._config = ProviderConfig("ollama", "test", "test", "https://example.org")
        processor.ai_service.rate_limiter = AIRequestLimiter(0)
        provider = AsyncMock()
        monkeypatch.setattr(processor.ai_service, "_call_ollama", provider)
        # Simulate an advisory precheck passing before another worker takes the slot.
        monkeypatch.setattr(processor.ai_service.rate_limiter, "can_request", lambda db: True)
        monkeypatch.setattr("src.services.processor.asyncio.sleep", AsyncMock())
        assert await processor.process_batch(db) == 0
        db.refresh(summary)
        assert summary.processing_status == status
        assert summary.processing_attempts == attempts
        assert summary.error_log == "previous error"
        assert summary.last_processed_at == original_time
        provider.assert_not_awaited()


async def test_missing_budget_table_fails_closed_without_provider_call(budget_engine, monkeypatch):
    AIRequestReservation.__table__.drop(budget_engine)
    with Session(budget_engine) as db:
        service = AIService()
        service._config = ProviderConfig("ollama", "test", "test", "https://example.org")
        provider = AsyncMock()
        monkeypatch.setattr(service, "_call_ollama", provider)
        with pytest.raises(AIRequestDeferred, match="unavailable"):
            await service.analyze_digest("test", [], {}, db=db)
        provider.assert_not_awaited()
        status = service.get_status(db)
        assert status["rate_limit_remaining"] is None
        assert status["rate_limit_available"] is False


def test_zero_cap_pauses_inference(budget_engine):
    with Session(budget_engine) as db:
        limiter = AIRequestLimiter(0)
        assert limiter.remaining(db) == 0
        with pytest.raises(AIRequestDeferred):
            limiter.reserve(db)
        assert db.query(AIRequestReservation).count() == 0
