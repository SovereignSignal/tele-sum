"""Tests for the wire-room-ops round-trip guard.

Posts that wire-room-ops publishes to the wire channels are recorded in
distribution_logs (channel='telegram', status='success', external_id=the
Telegram message id, wire=the wire). Tele-sum must skip importing those
posts — their content already exists in summaries via the approve
write-through, so re-ingesting them duplicates the entry and burns an
AI call.
"""

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from src.services.distribution_guard import is_distribution_post


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
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
    session.execute(text("""
        INSERT INTO distribution_logs (channel, wire, status, external_id) VALUES
        ('telegram', 'crypto', 'success', '3524'),
        ('telegram', 'ai', 'failed', '999'),
        ('x', 'crypto', 'success', '777')
    """))
    session.commit()
    yield session
    session.close()


def test_skips_post_recorded_as_successful_telegram_distribution(db):
    assert is_distribution_post(db, 3524, "crypto") is True


def test_keeps_organic_post(db):
    assert is_distribution_post(db, 4000, "crypto") is False


def test_keeps_post_when_wire_differs(db):
    # Same message id exists on the crypto wire; an ai-wire post with that
    # id is a different channel's organic post, not a distribution.
    assert is_distribution_post(db, 3524, "ai") is False


def test_keeps_post_when_distribution_failed(db):
    assert is_distribution_post(db, 999, "ai") is False


def test_keeps_post_when_only_a_non_telegram_channel_matches(db):
    assert is_distribution_post(db, 777, "crypto") is False


def test_keeps_post_when_wire_is_missing(db):
    assert is_distribution_post(db, 3524, None) is False


def test_missing_table_returns_false_and_leaves_session_usable():
    # Dev databases may not have the sibling app's distribution_logs table.
    # The guard must fail open (import normally) and roll back so the
    # caller's session isn't stuck in an aborted transaction.
    engine = create_engine("sqlite://")
    session = sessionmaker(bind=engine)()
    try:
        assert is_distribution_post(session, 1, "crypto") is False
        assert session.execute(text("SELECT 1")).scalar() == 1
    finally:
        session.close()
