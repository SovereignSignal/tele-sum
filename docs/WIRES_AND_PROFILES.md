# Wires & Extraction Profiles

## Wires

**Wire** — a free-form text slug labeling a vertical (`crypto`, `ai`, `oss`, `repos`, `your-niche`). Set per channel in `channel_configs.wire`; copied onto every `messages` row and every `summaries` row at ingest time.

Wire is pure deployment config — no code or schema changes are needed to add a new wire. Any lowercase slug is valid; the uniqueness constraint is per-(channel_id, telegram_id), so you can run multiple channels on the same wire.

## Extraction profiles

**Extraction profile** — how the AI reads a channel's posts. Set per channel in `channel_configs.extraction_profile`:

| Profile | For | AI schema | Output shape |
|---------|-----|-----------|-------------|
| `grant` (default) | Channels where one post = one fundable item | `AIAnalysis` | `title`, `summary`, `category`, `confidence`, `entities` (orgs, amounts, deadlines, key_terms, source) |
| `digest` | Channels where one post lists many items (news roundups, repo digests, release notes, model drops) | `DigestAnalysis` | post-level `title`/`summary` + `items[]` (title, url, note, category, tags, org, `people[]` of `{name, role, handle}`) |

Unknown profile values fall back to `grant` so a misconfigured row can never stall ingestion (see `resolve_profile()` in `src/services/processor.py`).

### `prompt_hint` (digest only)

`channel_configs.prompt_hint` is an optional free-text field passed to the AI along with the post content. Use it to tell the model what the channel is about and which fields matter. Example:

```
Daily GitHub repo digest; emphasize language and why each repo is interesting.
```

For `grant` channels, `prompt_hint` is ignored.

## Adding a channel

Insert a row into `channel_configs`:

```sql
INSERT INTO channel_configs
  (channel_id, channel_username, channel_title, wire, extraction_profile, prompt_hint, is_active)
VALUES
  ('@my_channel',         -- use @username as placeholder; resolved to numeric id on first post
   'my_channel',
   'My Channel',
   'repos',               -- your wire slug
   'digest',              -- or 'grant'
   'Weekly OSS repo roundup; note each project''s language and use-case.',
   true);
```

The bot matches incoming posts against `channel_configs` first by numeric `channel_id`, then by `channel_username`. If your row uses `@username` as the `channel_id` placeholder, the bot will update it to the real numeric id on the first message it receives from that channel. Posts from channels with no matching row are silently ignored.

## Setting the Notion target (digest channels)

To push digest items to Notion, also set `notion_database_id` on the row. See [NOTION_SYNC.md](NOTION_SYNC.md).
