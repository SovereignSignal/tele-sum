"""One-off backfill: import already-exported digest posts as messages + PENDING
summaries, then let the normal processor summarize them.

Source of post text: the public web preview https://t.me/s/<channel> (the same
source the claw/models backfills used). Produce JSONL with parse_tme_preview.py:
  {"telegram_id": int, "text": str, "url": "optional", "posted_at": "iso8601 optional"}

Usage:
  # dry-run (read-only — reports what WOULD import, writes nothing):
  DATABASE_URL=... python3 scripts/import_digest_posts.py interestingrepos posts.jsonl --dry-run
  # real import:
  DATABASE_URL=... python3 scripts/import_digest_posts.py interestingrepos posts.jsonl
"""
import json
import os
import sys
from datetime import datetime, timezone
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from src.models.models import (ChannelConfig, Message, Summary, ProcessingStatus)  # noqa: E402
from src.services.distribution_guard import is_distribution_post  # noqa: E402
from src.utils.url_extractor import extract_urls  # noqa: E402


def import_one_post(db: Session, channel: ChannelConfig, telegram_id: int,
                    text: str, url_text, posted_at: Optional[datetime] = None,
                    dry_run: bool = False, entity_links=None):
    """Insert one backfilled post as a Message + PENDING Summary. Returns a status
    string: 'empty' | 'duplicate' | 'distribution' | 'imported'. With dry_run=True
    it runs every gate read-only and returns 'imported' for posts that WOULD import,
    writing nothing."""
    text = (text or "").strip()
    if not text:
        return "empty"
    existing = db.query(Message).filter(
        Message.telegram_id == telegram_id,
        Message.channel_id == channel.channel_id,
    ).first()
    if existing:
        return "duplicate"
    if is_distribution_post(db, telegram_id, channel.wire):
        return "distribution"      # guard fails open if distribution_logs missing
    if dry_run:
        return "imported"          # passes every gate; no write
    links = [link for link in (entity_links or [])
             if isinstance(link, dict) and isinstance(link.get("url"), str)
             and link["url"].startswith(("https://", "http://"))]
    urls = list(dict.fromkeys(extract_urls(text) +
                            ([url_text] if isinstance(url_text, str) and
                             url_text.startswith(("https://", "http://")) else []) +
                            [link["url"] for link in links if link.get("url")]))
    msg = Message(
        telegram_id=telegram_id,
        channel_id=channel.channel_id,
        channel_config_id=channel.id,
        import_method="file",                  # backfill marker
        wire=channel.wire,
        # Message.timestamp is NOT NULL with no default. Use the parsed post time
        # when available, else now.
        timestamp=posted_at or datetime.now(timezone.utc),
        raw_content=text,
        extracted_urls=urls,
        entity_links=links,
    )
    db.add(msg)
    db.flush()
    db.add(Summary(message_id=msg.id, wire=channel.wire,
                   processing_status=ProcessingStatus.PENDING))
    return "imported"


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry_run = "--dry-run" in sys.argv
    channel_username, jsonl_path = args[0], args[1]
    engine = create_engine(os.environ["DATABASE_URL"])
    counts = {"imported": 0, "duplicate": 0, "empty": 0, "distribution": 0}
    with Session(engine) as db:
        channel = db.query(ChannelConfig).filter(
            ChannelConfig.channel_username == channel_username).first()
        if channel is None:
            print(f"no channel_configs row for {channel_username}"); return 1
        with open(jsonl_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                posted_at = (datetime.fromisoformat(row["posted_at"])
                             if row.get("posted_at") else None)
                r = import_one_post(db, channel, int(row["telegram_id"]),
                                    row.get("text", ""), row.get("url"),
                                    posted_at=posted_at, dry_run=dry_run,
                                    entity_links=row.get("entity_links"))
                # Commit per imported row. is_distribution_post() fails open with
                # db.rollback() on ANY error; a single end-of-loop commit would let
                # one transient guard error discard every prior uncommitted row.
                if r == "imported" and not dry_run:
                    db.commit()
                counts[r] = counts.get(r, 0) + 1
        if dry_run:
            db.rollback()
    print(("DRY-RUN (no writes) " if dry_run else "") + str(counts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
