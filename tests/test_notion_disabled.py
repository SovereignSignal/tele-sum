from unittest.mock import MagicMock

from src.config import Settings
from src.services import notion, scheduler


async def test_disabled_notion_sync_does_not_read_token_or_touch_db(monkeypatch):
    monkeypatch.setattr(notion.settings, "enable_notion_sync", False)
    token = MagicMock(side_effect=AssertionError("must not read token"))
    monkeypatch.setattr(notion, "get_notion_api_key", token)
    assert await notion.sync_pending_to_notion(None) == {
        "synced": 0, "failed": 0, "skipped_disabled": True,
    }
    token.assert_not_called()


def test_disabled_notion_job_leaves_processing_scheduled(monkeypatch):
    jobs = MagicMock()
    jobs.running = False
    jobs.get_job.return_value = None
    monkeypatch.setattr(scheduler, "scheduler", jobs)
    monkeypatch.setattr(scheduler.database, "get_engine", lambda: None)
    monkeypatch.setattr(scheduler.settings, "enable_notion_sync", False)
    scheduler.start_scheduler()
    ids = {c.kwargs["id"] for c in jobs.add_job.call_args_list}
    assert ids == {"sync_job", "process_pending", "retry_failed", "cleanup_stale", "daily_stats"}


def test_notion_switch_reads_environment(monkeypatch):
    monkeypatch.setenv("ENABLE_NOTION_SYNC", "false")
    assert Settings().enable_notion_sync is False
