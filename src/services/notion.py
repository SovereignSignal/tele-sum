"""Minimal Notion REST client + property mapping for the digest profile.

Composability rules: token comes from NOTION_API_KEY env or the 'notion' row
in api_providers (same precedence pattern as AI providers); the target
database is per-channel config. No workspace-specific anything in here.
"""

import logging
import os
from dataclasses import dataclass
from typing import Dict, List, Optional

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.models.models import ChannelConfig, Message, NotionPage, ProcessingStatus, Summary

logger = logging.getLogger(__name__)

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

# One generic template for every digest database.
DIGEST_DB_PROPERTIES: Dict[str, dict] = {
    "Title": {"title": {}},
    "URL": {"url": {}},
    "Note": {"rich_text": {}},
    "Category": {"select": {}},
    "Tags": {"multi_select": {}},
    "Org": {"select": {}},
    "Posted": {"date": {}},
    "Source post": {"url": {}},
    "Wire": {"select": {}},
}


class NotionClient:
    def __init__(self, api_key: str, transport: Optional[httpx.BaseTransport] = None):
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(headers=self._headers, timeout=30, transport=self._transport)

    async def create_page(self, database_id: str, properties: dict) -> Optional[str]:
        payload = {"parent": {"database_id": database_id}, "properties": properties}
        async with self._client() as client:
            try:
                resp = await client.post(f"{NOTION_API}/pages", json=payload)
            except httpx.HTTPError as e:
                logger.warning(f"⚠️ Notion create_page transport error: {e}")
                return None
        if resp.status_code != 200:
            logger.warning(f"⚠️ Notion create_page failed ({resp.status_code}): {resp.text[:300]}")
            return None
        return resp.json().get("id")

    async def create_database(self, parent_page_id: str, title: str) -> Optional[str]:
        payload = {
            "parent": {"type": "page_id", "page_id": parent_page_id},
            "title": [{"type": "text", "text": {"content": title}}],
            "properties": DIGEST_DB_PROPERTIES,
        }
        async with self._client() as client:
            try:
                resp = await client.post(f"{NOTION_API}/databases", json=payload)
            except httpx.HTTPError as e:
                logger.warning(f"⚠️ Notion create_database transport error: {e}")
                return None
        if resp.status_code != 200:
            logger.warning(f"⚠️ Notion create_database failed ({resp.status_code}): {resp.text[:300]}")
            return None
        return resp.json().get("id")


def build_item_properties(
    item: dict,
    posted_at: Optional[str],
    source_link: Optional[str],
    wire: Optional[str],
) -> dict:
    """Map a DigestItem dict onto DIGEST_DB_PROPERTIES. Optional fields are
    omitted when empty so Notion doesn't render blank selects."""
    note = item.get("note") or ""
    props: dict = {
        "Title": {"title": [{"text": {"content": (item.get("title") or "Untitled")[:200]}}]},
        "Note": {"rich_text": ([{"text": {"content": note[:1900]}}] if note else [])},
    }
    if item.get("url"):
        props["URL"] = {"url": item["url"]}
    if item.get("category"):
        props["Category"] = {"select": {"name": str(item["category"])[:100]}}
    tags: List[str] = item.get("tags") or []
    if tags:
        props["Tags"] = {"multi_select": [{"name": str(t)[:100]} for t in tags[:10]]}
    if item.get("org"):
        props["Org"] = {"select": {"name": str(item["org"])[:100]}}
    if posted_at:
        props["Posted"] = {"date": {"start": posted_at}}
    if source_link:
        props["Source post"] = {"url": source_link}
    if wire:
        props["Wire"] = {"select": {"name": wire}}
    return props


def get_notion_api_key(db: Optional[Session]) -> Optional[str]:
    """NOTION_API_KEY env wins; else the enabled 'notion' row in api_providers.
    Fails open (None) when neither exists — standalone deployments without the
    shared table just don't sync."""
    env_key = os.environ.get("NOTION_API_KEY")
    if env_key:
        return env_key
    if db is None:
        return None
    try:
        row = db.execute(
            text("SELECT api_key FROM api_providers WHERE provider = 'notion' AND enabled = true LIMIT 1")
        ).first()
        return row[0] if row and row[0] else None
    except Exception as e:
        db.rollback()
        logger.warning(f"⚠️ api_providers lookup failed (standalone mode?): {e}")
        return None


# =============================================================================
# NOTION SYNC — selection logic + per-cycle runner
# =============================================================================


@dataclass
class NotionWorkItem:
    summary_id: int
    item_index: int
    item: dict
    database_id: str
    posted_at: Optional[str]
    source_link: Optional[str]
    wire: Optional[str]


def _telegram_link(username: Optional[str], telegram_id: Optional[int]) -> Optional[str]:
    if username and telegram_id:
        return f"https://t.me/{username.lstrip('@')}/{telegram_id}"
    return None


def pending_notion_items(db: Session, limit: int = 200) -> List[NotionWorkItem]:
    """Digest items on Notion-enabled channels whose summary is SUCCESS and
    which have no notion_pages row yet. (summary_id, item_index) idempotency."""
    rows = (
        db.query(Summary, Message, ChannelConfig)
        .join(Message, Summary.message_id == Message.id)
        .join(ChannelConfig, Message.channel_config_id == ChannelConfig.id)
        .filter(
            Summary.processing_status == ProcessingStatus.SUCCESS,
            ChannelConfig.extraction_profile == "digest",
            ChannelConfig.notion_database_id.isnot(None),
        )
        .order_by(Summary.id)
        .limit(limit)
        .all()
    )
    work: List[NotionWorkItem] = []
    for summary, message, cc in rows:
        entities = summary.entities or {}
        items = entities.get("items") or []
        if not items:
            continue
        synced = {
            np.item_index
            for np in db.query(NotionPage).filter(NotionPage.summary_id == summary.id).all()
        }
        for idx, item in enumerate(items):
            if idx in synced:
                continue
            work.append(NotionWorkItem(
                summary_id=summary.id,
                item_index=idx,
                item=item,
                database_id=cc.notion_database_id,
                posted_at=message.timestamp.date().isoformat() if message.timestamp else None,
                source_link=_telegram_link(cc.channel_username, message.telegram_id),
                wire=summary.wire,
            ))
    return work


async def sync_pending_to_notion(db: Session) -> dict:
    """One notion_sync cycle. Never raises; failures stay unsynced and are
    retried next cycle. Returns counters for logging."""
    api_key = get_notion_api_key(db)
    if not api_key:
        return {"synced": 0, "failed": 0, "skipped_no_token": True}

    client = NotionClient(api_key)
    synced = failed = 0
    for w in pending_notion_items(db):
        props = build_item_properties(w.item, w.posted_at, w.source_link, w.wire)
        page_id = await client.create_page(w.database_id, props)
        if page_id is None:
            failed += 1
            continue
        db.add(NotionPage(summary_id=w.summary_id, item_index=w.item_index, page_id=page_id))
        db.commit()
        synced += 1
    if synced or failed:
        logger.info(f"✅ Notion sync: {synced} items created, {failed} failed (will retry)")
    return {"synced": synced, "failed": failed, "skipped_no_token": False}
