from unittest.mock import AsyncMock, MagicMock

from src.services import scheduler


async def test_partial_batch_does_not_abandon_remaining_backlog(monkeypatch):
    db = MagicMock()
    processor = MagicMock()
    # A concurrent worker claims four rows from the first twenty-row batch.
    processor.process_batch = AsyncMock(side_effect=[16, 20, 0])
    monkeypatch.setattr(scheduler.database, "get_engine", lambda: None)
    monkeypatch.setattr(scheduler.database, "SessionLocal", lambda: db)
    monkeypatch.setattr(scheduler, "MessageProcessor", lambda: processor)
    monkeypatch.setattr(scheduler.asyncio, "sleep", AsyncMock())

    await scheduler.sync_and_process()

    assert processor.process_batch.await_count == 3
    db.close.assert_called_once()


async def test_drain_stops_when_processor_cannot_make_progress(monkeypatch):
    db = MagicMock()
    processor = MagicMock()
    # A rate-limited processor returns zero without consuming pending attempts.
    processor.process_batch = AsyncMock(return_value=0)
    monkeypatch.setattr(scheduler.database, "get_engine", lambda: None)
    monkeypatch.setattr(scheduler.database, "SessionLocal", lambda: db)
    monkeypatch.setattr(scheduler, "MessageProcessor", lambda: processor)
    pause = AsyncMock()
    monkeypatch.setattr(scheduler.asyncio, "sleep", pause)

    await scheduler.sync_and_process()

    processor.process_batch.assert_awaited_once_with(db, limit=20, include_retries=True)
    pause.assert_not_awaited()
    db.close.assert_called_once()
