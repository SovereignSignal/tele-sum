"""Guard against re-ingesting wire-room-ops distribution posts.

The sibling app (wire-room-ops) publishes approved entries to the same
Telegram wire channels this app monitors, and records each successful post
in its distribution_logs table (external_id = Telegram message id). Those
entries already exist in summaries via wire-room-ops' approve write-through,
so importing the channel post again would store the same entry twice and
spend an AI call re-summarizing it.

Tele-sum only READS distribution_logs — the table stays in the sibling
app's lane.
"""

import logging
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_DISTRIBUTED_POST_QUERY = text("""
    SELECT 1 FROM distribution_logs
    WHERE channel = 'telegram'
      AND status = 'success'
      AND external_id = :telegram_id
      AND wire = :wire
    LIMIT 1
""")


def is_distribution_post(db: Session, telegram_id: int, wire: Optional[str]) -> bool:
    """True if this channel post was published by the wire-room-ops distributor.

    Fails open: any error (e.g. distribution_logs absent in a dev database)
    rolls back and returns False so the post imports normally.
    """
    if not wire:
        return False
    try:
        row = db.execute(
            _DISTRIBUTED_POST_QUERY,
            {"telegram_id": str(telegram_id), "wire": wire},
        ).first()
        return row is not None
    except Exception as e:
        db.rollback()
        logger.warning(f"⚠️ distribution_logs check failed, importing normally: {e}")
        return False
