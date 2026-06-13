"""Deployments without the sibling app's tables must run cleanly.
seen_urls (grant dedup) and api_providers (config) are both optional."""

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from src.services.processor import _check_seen
from src.services.notion import get_notion_api_key


def make_db():
    engine = create_engine("sqlite://")
    return sessionmaker(bind=engine)()


def test_check_seen_fails_open_without_seen_urls_table():
    db = make_db()
    assert _check_seen(db, ["https://example.org/a"], "crypto") is None
    assert db.execute(text("SELECT 1")).scalar() == 1  # session still usable


def test_notion_key_lookup_fails_open_without_api_providers(monkeypatch):
    monkeypatch.delenv("NOTION_API_KEY", raising=False)
    db = make_db()
    assert get_notion_api_key(db) is None
    assert db.execute(text("SELECT 1")).scalar() == 1
