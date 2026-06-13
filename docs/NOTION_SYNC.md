# Notion Sync (digest channels)

Lands one Notion row per extracted digest item, approximately 5 minutes after the post arrives.

Only channels using the `digest` extraction profile and with `notion_database_id` set participate in Notion sync. Grant-profile channels are unaffected.

## Setup

1. Create a Notion **internal integration** (notion.so → Settings → Integrations → New), copy the secret.

2. Provide the token: `export NOTION_API_KEY=ntn_...` — or store it as a row in `api_providers` (`provider='notion'`, `api_key`, `enabled=true`) if you run the shared-table setup. The env var takes precedence.

3. Share a parent page with the integration, then create a database per channel from tele-sum's template (`scripts/create_notion_databases.py`), or point at an existing database with compatible properties:

   | Property | Notion type |
   |----------|-------------|
   | Title | title |
   | URL | url |
   | Note | rich_text |
   | Category | select |
   | Tags | multi_select |
   | Org | select |
   | Posted | date |
   | Source post | url |
   | Wire | select |

4. Set `channel_configs.notion_database_id` for each channel that should sync. Channels without it simply don't sync.

## Behavior

- The `notion_sync` APScheduler job runs every 5 minutes.
- Per-item state is tracked in the `notion_pages` table (`summary_id`, `item_index`, `page_id`). The `(summary_id, item_index)` pair is the idempotency key — already-created pages are skipped on re-runs.
- Failures (network errors, Notion API errors) are logged and the item is left unsynced; the next cycle retries it automatically.
- A missing or invalid token disables sync with a warning — ingestion is never blocked.
- Sync is one-way: Postgres is the source of truth. Edits made directly in Notion do not flow back.

## Troubleshooting

**No items appearing in Notion** — check the app logs for `⚠️ Notion` lines. Common causes: `NOTION_API_KEY` not set or invalid; the integration has not been shared with the parent page; `channel_configs.notion_database_id` is null or wrong; the channel's `extraction_profile` is not `digest`.

**Duplicate items** — should not happen due to the `uq_notion_pages_summary_item` constraint. If you see them, check whether a row was deleted from `notion_pages` without deleting the corresponding Notion page.
