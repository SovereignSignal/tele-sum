from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.models.models import (
    Base, ChannelConfig, Message, NotionPage, ProcessingStatus, Summary,
)
import httpx

from src.services import notion as notion_module
from src.services.notion import pending_notion_items, sync_pending_to_notion


def make_db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def seed(db, *, profile="digest", notion_db="db-1", status=ProcessingStatus.SUCCESS,
         items=({"title": "A"}, {"title": "B"})):
    cc = ChannelConfig(channel_id="-100123", channel_username="clawbytes", wire="claw",
                       extraction_profile=profile, notion_database_id=notion_db, is_active=True)
    db.add(cc); db.flush()
    msg = Message(telegram_id=42, channel_id="-100123", channel_config_id=cc.id, wire="claw",
                  timestamp=datetime(2026, 6, 12, tzinfo=timezone.utc), raw_content="digest post",
                  import_method="bot")
    db.add(msg); db.flush()
    s = Summary(message_id=msg.id, wire="claw", processing_status=status,
                entities={"profile": "digest", "items": list(items)})
    db.add(s); db.commit()
    return s


def test_pending_returns_each_unsynced_item_with_target_db():
    db = make_db()
    s = seed(db)
    work = pending_notion_items(db)
    assert [(w.summary_id, w.item_index, w.database_id) for w in work] == [
        (s.id, 0, "db-1"), (s.id, 1, "db-1"),
    ]
    assert work[0].item["title"] == "A"
    assert work[0].source_link == "https://t.me/clawbytes/42"
    assert work[0].posted_at == "2026-06-12"


def test_already_synced_items_are_excluded():
    db = make_db()
    s = seed(db)
    db.add(NotionPage(summary_id=s.id, item_index=0, page_id="pg-0")); db.commit()
    work = pending_notion_items(db)
    assert [(w.summary_id, w.item_index) for w in work] == [(s.id, 1)]


def test_non_digest_unsuccessful_and_unconfigured_channels_are_excluded():
    assert pending_notion_items(seed_into(make_db(), profile="grant")) == []
    assert pending_notion_items(seed_into(make_db(), status=ProcessingStatus.PENDING)) == []
    assert pending_notion_items(seed_into(make_db(), notion_db=None)) == []


def seed_into(db, **kwargs):
    seed(db, **kwargs)
    return db


async def test_sync_pending_creates_pages_and_records_idempotency(monkeypatch):
    db = make_db()
    s = seed(db)
    monkeypatch.setenv("NOTION_API_KEY", "tk")

    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, json={"id": f"pg-{len(calls)}"})

    real_client = notion_module.NotionClient

    def client_factory(api_key):
        return real_client(api_key, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(notion_module, "NotionClient", client_factory)

    result = await sync_pending_to_notion(db)
    assert result == {"synced": 2, "failed": 0, "skipped_no_token": False}
    assert len(calls) == 2
    # idempotent: second run does nothing
    result2 = await sync_pending_to_notion(db)
    assert result2["synced"] == 0
    assert len(calls) == 2


async def test_sync_counts_api_failures_and_leaves_them_pending(monkeypatch):
    db = make_db()
    seed(db)
    monkeypatch.setenv("NOTION_API_KEY", "tk")

    def handler(request):
        return httpx.Response(400, json={"message": "nope"})

    real_client = notion_module.NotionClient
    monkeypatch.setattr(
        notion_module, "NotionClient",
        lambda api_key: real_client(api_key, transport=httpx.MockTransport(handler)),
    )

    result = await sync_pending_to_notion(db)
    assert result["synced"] == 0
    assert result["failed"] == 2
    # nothing recorded; items remain pending for next cycle
    assert len(notion_module.pending_notion_items(db)) == 2
