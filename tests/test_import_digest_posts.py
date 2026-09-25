import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.models.models import Base, ChannelConfig, Message, Summary, ProcessingStatus
from scripts.import_digest_posts import import_one_post


# There are NO shared db_session/repos_channel fixtures in tests/ — conftest.py is
# only a sys.path shim. Define them here, mirroring the in-memory SQLite pattern of
# tests/test_processor_digest_integration.py's make_db() helper.
@pytest.fixture
def db_session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture
def repos_channel(db_session):
    ch = ChannelConfig(
        channel_id="-1001",                 # Message.channel_id is the string chat id
        channel_username="interestingrepos",
        wire="repos",
        extraction_profile="digest",
        is_active=True,
    )
    db_session.add(ch)
    db_session.commit()
    return ch


def test_import_skips_existing_telegram_id(db_session, repos_channel):
    # first import inserts a Message + PENDING Summary
    res1 = import_one_post(db_session, repos_channel, telegram_id=1001,
                           text="ratty — a fast TUI", url_text=None)
    assert res1 == "imported"
    # re-running with same telegram_id is a no-op (dedup on channel_id + telegram_id,
    # the uq_messages_channel_telegram_id constraint)
    res2 = import_one_post(db_session, repos_channel, telegram_id=1001,
                           text="ratty — a fast TUI", url_text=None)
    assert res2 == "duplicate"


def test_import_skips_empty_text(db_session, repos_channel):
    assert import_one_post(db_session, repos_channel, telegram_id=1002, text="  ", url_text=None) == "empty"


def test_import_creates_pending_summary_with_channel_wire(db_session, repos_channel):
    import_one_post(db_session, repos_channel, telegram_id=1003, text="some repo digest", url_text=None)
    msg = db_session.query(Message).filter_by(telegram_id=1003).one()
    assert msg.import_method == "file"          # backfill marker, not "bot"
    assert msg.wire == repos_channel.wire
    assert msg.timestamp is not None            # Message.timestamp is NOT NULL — must be set
    summ = db_session.query(Summary).filter_by(message_id=msg.id).one()
    assert summ.processing_status == ProcessingStatus.PENDING


def test_dry_run_reports_would_import_but_writes_nothing(db_session, repos_channel):
    r = import_one_post(db_session, repos_channel, telegram_id=2001,
                        text="would import", url_text=None, dry_run=True)
    assert r == "imported"                       # passes every gate
    assert db_session.query(Message).filter_by(telegram_id=2001).first() is None  # no write


def test_backfill_preserves_hidden_links_and_original_post_time(db_session, repos_channel):
    from datetime import datetime, timezone
    posted = datetime(2026, 4, 1, tzinfo=timezone.utc)
    links = [{"text": "Repo A", "url": "https://example.com/a"},
             {"text": "Repo B", "url": "https://example.com/b"}]
    import_one_post(db_session, repos_channel, 2002, "Repo A and Repo B",
                    "https://example.com/a", posted_at=posted, entity_links=links)
    msg = db_session.query(Message).filter_by(telegram_id=2002).one()
    assert msg.extracted_urls == ["https://example.com/a", "https://example.com/b"]
    assert msg.entity_links == links
    assert msg.timestamp.replace(tzinfo=timezone.utc) == posted
