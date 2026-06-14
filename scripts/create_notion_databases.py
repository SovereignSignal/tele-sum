"""One-off: create a digest Notion database per channel and store its id.

Usage:
  DATABASE_URL=... NOTION_API_KEY=... PARENT_PAGE_ID=... \
  python3 scripts/create_notion_databases.py clawbytes:"Claw Bytes" modelbytes:"Model Bytes"

Each arg is <channel_username>:<Database Title>. Idempotent: channels that
already have notion_database_id are skipped. The Notion token may come from
NOTION_API_KEY or, if absent, the enabled 'notion' row in api_providers.

Note: databases created BEFORE the "People" property was added (clawbytes,
modelbytes, interestingrepos) need "People" (multi_select) added by hand in
Notion once, or item writes that include people will be rejected (400). Re-running
this script does NOT alter existing DBs — it only creates missing ones.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, text  # noqa: E402

from src.services.notion import NotionClient  # noqa: E402


async def main() -> int:
    db_url = os.environ["DATABASE_URL"]
    parent = os.environ["PARENT_PAGE_ID"]
    engine = create_engine(db_url)

    api_key = os.environ.get("NOTION_API_KEY")
    if not api_key:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT api_key FROM api_providers WHERE provider='notion' AND enabled=true LIMIT 1")
            ).first()
            api_key = row[0] if row else None
    if not api_key:
        print("No Notion token (set NOTION_API_KEY or add an enabled 'notion' api_providers row)")
        return 1

    client = NotionClient(api_key)
    for arg in sys.argv[1:]:
        username, _, title = arg.partition(":")
        with engine.begin() as conn:
            existing = conn.execute(
                text("SELECT notion_database_id FROM channel_configs WHERE channel_username = :u"),
                {"u": username},
            ).first()
            if existing is None:
                print(f"{username}: no channel_configs row — seed the channel first; skipping")
                continue
            if existing[0]:
                print(f"{username}: already configured -> {existing[0]}")
                continue
            db_id = await client.create_database(parent, title or username)
            if not db_id:
                print(f"{username}: FAILED to create database")
                continue
            conn.execute(
                text("UPDATE channel_configs SET notion_database_id = :d WHERE channel_username = :u"),
                {"d": db_id, "u": username},
            )
            print(f"{username}: created '{title}' -> {db_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
