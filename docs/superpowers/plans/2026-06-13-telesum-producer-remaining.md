# Tele-Sum Producer — Remaining Work Implementation Plan
> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax.
**Goal:** Make tele-sum (the content producer) surface named **people** in digest extraction, stop overloading the `org` field, cut junk orgs at the source, backfill the under-populated `repos` (interestingrepos) channel, and write down the producer/consumer swimlane boundary — so wire-room-ops can source contacts cheaply from `summaries.entities` instead of re-deriving them with paid web search.
**Architecture:** Tele-sum polls Telegram channels (bot mode), summarizes each post with a provider-agnostic AI service, validates the response against a pydantic schema, and stores the result in `summaries.entities` (JSONB). The digest profile (`channel_configs.extraction_profile = 'digest'`) emits one post → many `DigestItem`s. This plan extends the `DigestItem` schema and the digest prompt, mirrors the new field into the per-feed Notion DB, adds a one-off backfill script, and documents the boundary. The contract surface for the sibling app is `summaries.entities->'items'`; no API, no shared write path.
**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, pydantic 2, python-telegram-bot, APScheduler (tele-sum stack)

---

## Scope boundary

Tele-sum is the **content producer**. Its job ends at producing `summaries` (`title`/`summary`/`category`/`confidence`) plus a validated `entities` JSON blob. Everything downstream of that blob — **CRM org/contact creation, the `qualifiesAsDigestOrg` quality gate, org dedup, web enrichment, the `org_profiles` / `digest_item_orgs` / `discovered_contacts` / `monitored_accounts` tables, the SimpleCRM Notion sync, and people-monitor reconciliation** — lives in **wire-room-ops** and has already been handed off (Phase 1 + Phase 2 shipped; Phase 3 contacts planned). This plan touches **only tele-sum's own swimlane**: its schema, its prompt, its per-feed Notion publish, its ingest, and its docs. It deliberately does **not** implement anything on the wire-room-ops side; where a consumer contract changes, the plan documents the new field and notes what wire-room-ops *would later* read, and stops there.

**Inventory items addressed:**
- **A** — people field in `DigestItem` + prompt disambiguation (Tasks 1–4).
- **A (Notion surfacing)** — `People` property on the digest Notion DB + mapping (Tasks 5–7). *Kept in-scope because `src/services/notion.py` and `scripts/create_notion_databases.py` are tele-sum's own per-feed publishing code (the `notion_pages` lane), distinct from wire-room-ops' CRM sync.*
- **B** — backfill the `repos` channel history (Task 8, operational).
- **C** — document the swimlane boundary in `CLAUDE.md` (Task 9).
- **D** — extraction-quality guidance to cut junk orgs upstream — **folded into Task 3** (it is the same prompt edit; splitting it would be make-work).

**Items deliberately dropped / not implemented here:**
- Any wire-room-ops Phase 3 code (contact discovery, `discovered_contacts`, `monitored_accounts` reconcile) — sibling-owned, handed off.
- A schema validator that asserts "this `org` string is a real organization" — impossible to enforce in pydantic; org-vs-junk steering is prompt-only (Task 3) and the consumer keeps its `qualifiesAsDigestOrg` gate as the safety net.

> [!IMPORTANT] **Open decision — model `people` as `List[str]` vs a structured `Person` object.**
> This is the one genuine product choice in the plan. **Recommendation: structured `Person` (name + optional role + optional handle), each field validated, the whole item droppable when `name` is blank.**
> Rationale: the entire point of this work (per the gap inventory) is to let wire-room-ops Phase 3 source contacts from `summaries.entities` *without* a second LLM round trip. Its `passesContactBar` gate already wants a **name, a role, and an X handle or profile URL**. A bare `List[str]` of names throws away exactly the two fields (`role`, `handle`) that make a person *contactable*, forcing wire-room-ops back to web search — defeating the purpose. The structured form costs ~6 extra lines of schema and one prompt clause now, and is forward-compatible: a missing `role`/`handle` is simply `None`, so the model is free to emit name-only people when that's all the post gives. `List[str]` would be cheaper today but would have to be re-migrated to a struct the moment the consumer needs role/handle — which is *already* the documented requirement. We pay the small structured cost once. **This plan implements the structured `Person` form.** If the owner prefers `List[str]`, only Tasks 1–4 change shape (drop `role`/`handle`, make `people: List[str]`); Tasks 5–9 are unaffected in spirit.

---

## Task 1 — Add a `Person` model + `people` field to `DigestItem`

**File:** `src/schemas/digest.py`

- [ ] Write a failing test in `tests/test_digest_schema.py` for the new `Person` model and `DigestItem.people`:

```python
# --- people field (Task 1) ---
from src.schemas.digest import Person


def test_person_parses_full_and_strips():
    p = Person(name="  Ada Lovelace  ", role="maintainer", handle="@ada")
    assert p.name == "Ada Lovelace"
    assert p.role == "maintainer"
    assert p.handle == "@ada"


def test_person_role_and_handle_optional_and_blank_is_none():
    p = Person(name="Grace", role="", handle="")
    assert p.role is None
    assert p.handle is None


def test_person_requires_name():
    with pytest.raises(ValidationError):
        Person(role="founder")


def test_digest_item_defaults_people_to_empty_list():
    item = DigestItem(title="Thing")
    assert item.people == []
```

- [ ] Run it — fails (`ImportError: cannot import name 'Person'`):

```bash
python3 -m pytest tests/test_digest_schema.py -q
```

- [ ] Implement. Add `Person` above `DigestItem` and the `people` field + validator inside `DigestItem` in `src/schemas/digest.py`:

```python
class Person(BaseModel):
    """A named individual mentioned in a digest item (author/founder/maintainer)."""
    name: str = Field(min_length=1)
    role: Optional[str] = None          # e.g. founder, maintainer, author
    handle: Optional[str] = None        # @x_handle or profile url, as the post gives it

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, v):
        return str(v).strip() if v is not None else v

    @field_validator("role", "handle", mode="before")
    @classmethod
    def blank_is_none(cls, v):
        v = (str(v).strip() if v is not None else "")
        return v or None
```

Then add to `DigestItem` (after the `org` field, line 20). `Person` is defined above `DigestItem` in the same module, so use the bare type (no quoted forward-ref), matching the file's existing style:

```python
    people: List[Person] = Field(default_factory=list)
```

And add a before-validator to `DigestItem` (mirrors `coerce_items` on the analysis: wrap a lone dict, drop names-less entries, tolerate `None`):

```python
    @field_validator("people", mode="before")
    @classmethod
    def coerce_people(cls, v):
        if v is None:
            return []
        if isinstance(v, dict):
            v = [v]
        if not isinstance(v, list):
            return []
        out = []
        for x in v:
            if isinstance(x, str):          # tolerate a bare name string
                x = {"name": x}
            if isinstance(x, dict) and str(x.get("name") or "").strip():
                out.append(x)               # drop entries with no usable name
        return out
```

- [ ] Run it — passes:

```bash
python3 -m pytest tests/test_digest_schema.py -q
```

- [ ] Commit: `feat(digest): add Person model and people[] field to DigestItem`

---

## Task 2 — Verify `people` round-trips through `DigestAnalysis` and the existing parse path

**File:** `src/schemas/digest.py` (no change expected) + `tests/test_digest_schema.py`

This task proves the existing `processor.py` storage (which dumps `result["items"]` verbatim) carries `people` with zero processor changes — so we don't touch `processor.py`.

- [ ] Write a failing test in `tests/test_digest_schema.py`:

```python
def test_people_round_trip_through_analysis_model_dump():
    payload = {
        "title": "Repo roundup",
        "summary": "Notable repos this week.",
        "confidence": "confident",
        "items": [
            {"title": "ratty", "url": "https://github.com/orhun/ratty", "note": "TUI",
             "org": "Ratatui", "people": [{"name": "Orhun", "role": "maintainer", "handle": "@orhun"}]},
            {"title": "no people here", "note": "n"},          # people omitted -> []
        ],
    }
    dumped = DigestAnalysis(**payload).model_dump()
    assert dumped["items"][0]["people"][0]["name"] == "Orhun"
    assert dumped["items"][0]["people"][0]["handle"] == "@orhun"
    assert dumped["items"][1]["people"] == []
```

- [ ] Run it — fails only if Task 1 is incomplete; if Task 1 is correct this should pass on first run. Run to confirm the round trip:

```bash
python3 -m pytest tests/test_digest_schema.py::test_people_round_trip_through_analysis_model_dump -q
```

- [ ] If it passes, no implementation needed — this is the verification that storage needs no change. If it fails, fix `Person`/`coerce_people` in Task 1 until green.
- [ ] Commit: `test(digest): assert people[] survives DigestAnalysis.model_dump (no processor change)`

---

## Task 3 — Disambiguate `org` vs `people` in the digest prompt + add junk-org guidance (folds in item D)

**File:** `src/services/ai.py` (`_build_digest_prompt`, lines 260–270)

- [ ] Write a failing test in `tests/test_digest_ai.py`:

```python
def test_digest_prompt_separates_org_from_people_and_guards_junk_orgs():
    prompt = _build_digest_prompt(
        message_content="post", urls=[], url_contents={}, prompt_hint=None, links=[],
    )
    # org is disambiguated from people
    assert '"people"' in prompt
    assert "org/author" not in prompt          # the overloaded phrasing is gone
    # org now means an organization/project, never a person
    assert "organization or project" in prompt
    # junk-org guidance (folded item D)
    assert "never a person" in prompt
    # people carries name/role/handle
    assert "role" in prompt and "handle" in prompt
```

- [ ] Run it — fails:

```bash
python3 -m pytest tests/test_digest_ai.py -q
```

- [ ] Implement. Replace the items-shape line and the rules block in `_build_digest_prompt`. Change line 265–266 (the `items` JSON shape) and the `Rules:` text (267–269). New block:

```python
    parts.append(
        "\n## Your task\n"
        "Return ONLY a JSON object, no prose, shaped exactly like:\n"
        '{"title": "post title, 4-6 words", "summary": "1-2 sentence overview of the post",\n'
        ' "confidence": "confident" or "uncertain",\n'
        ' "items": [{"title": "item name", "url": "item link or null", "note": "what it is / why it matters, 1-2 lines",\n'
        '            "category": "the section label the post itself uses, or null", "tags": ["short", "tags"],\n'
        '            "org": "the organization or project that owns this item, or null",\n'
        '            "people": [{"name": "person named in the item", "role": "their role or null", "handle": "@handle or profile url or null"}]}]}\n'
        "Rules: one items[] entry per distinct item in the post. Use the post's own links "
        "(prefer the item's primary link). Never invent URLs. Keep notes factual. "
        "Attach each item's url by matching the links above to the item — the link's anchor text is usually the item's title.\n"
        "org = the organization, project, or repo OWNER behind the item — never a person, never a section "
        "label (e.g. 'Releases'), never a generic word; use null when unclear. "
        "people = named human individuals (authors, founders, maintainers) tied to the item; "
        "put a person in people[], NEVER in org. Use [] when no person is named. Do not invent people."
    )
```

- [ ] Run it — passes:

```bash
python3 -m pytest tests/test_digest_ai.py -q
```

- [ ] Commit: `feat(digest-prompt): split org from people, guard against junk orgs`

---

## Task 4 — Full digest-extraction unit pass (regression + new field together)

**File:** `tests/test_digest_ai.py`, `tests/test_digest_schema.py`

- [ ] Add one end-to-end parse test in `tests/test_digest_ai.py` proving `_parse_digest_response` accepts and returns `people`:

```python
def test_parse_digest_response_carries_people():
    raw = ('{"title":"T","summary":"S","confidence":"confident",'
           '"items":[{"title":"A","url":"https://a.dev","note":"n","org":"Acme",'
           '"people":[{"name":"Jo","role":"founder","handle":"@jo"}]}]}')
    parsed = _parse_digest_response(raw)
    assert parsed["items"][0]["org"] == "Acme"
    assert parsed["items"][0]["people"][0]["name"] == "Jo"
```

- [ ] Run the whole suite — confirm nothing regressed and the new test passes:

```bash
python3 -m pytest tests/ -q
```

Expected: all prior tests (42) plus the new ones green.

- [ ] Commit: `test(digest): end-to-end people extraction through parse path`

---

## Task 5 — Add a `People` property to the digest Notion DB schema

**File:** `src/services/notion.py` (`DIGEST_DB_PROPERTIES`, lines 25–35)

- [ ] Write a failing test in `tests/test_notion_service.py`:

```python
def test_digest_db_template_has_people_property():
    from src.services.notion import DIGEST_DB_PROPERTIES
    assert "People" in DIGEST_DB_PROPERTIES
    assert DIGEST_DB_PROPERTIES["People"] == {"multi_select": {}}
```

- [ ] Run it — fails:

```bash
python3 -m pytest tests/test_notion_service.py -q
```

- [ ] Implement. Add to `DIGEST_DB_PROPERTIES` (after `"Org"`):

```python
    "People": {"multi_select": {}},
```

- [ ] Run it — passes.
- [ ] Commit: `feat(notion): add People multi_select to digest DB template`

---

## Task 6 — Map `item.people` onto the Notion `People` property

**File:** `src/services/notion.py` (`build_item_properties`, lines 81–109)

- [ ] Write a failing test in `tests/test_notion_service.py`:

```python
def test_build_item_properties_emits_people_names():
    from src.services.notion import build_item_properties
    item = {"title": "ratty", "org": "Ratatui",
            "people": [{"name": "Orhun", "role": "maintainer", "handle": "@orhun"},
                       {"name": ""}]}            # blank name dropped
    props = build_item_properties(item, posted_at=None, source_link=None, wire=None)
    names = [o["name"] for o in props["People"]["multi_select"]]
    assert names == ["Orhun"]


def test_build_item_properties_omits_people_when_absent():
    from src.services.notion import build_item_properties
    props = build_item_properties({"title": "x"}, None, None, None)
    assert "People" not in props
```

- [ ] Run it — fails:

```bash
python3 -m pytest tests/test_notion_service.py -q
```

- [ ] Implement. In `build_item_properties`, after the `org` block (line 102), add a guarded People block. A Notion `multi_select` option name must be non-empty and is capped at 100 chars; we flatten each `Person` to a display name (`name` plus `(role)` when present, truncated):

```python
    people = item.get("people") or []
    options = []
    for p in people[:10]:
        name = str((p or {}).get("name") or "").strip()
        if not name:
            continue
        role = str((p or {}).get("role") or "").strip()
        label = f"{name} ({role})" if role else name
        options.append({"name": label[:100]})
    if options:
        props["People"] = {"multi_select": options}
```

- [ ] Run it — passes.
- [ ] Commit: `feat(notion): map digest item.people onto Notion People property`

---

## Task 7 — Document that existing Notion DBs need the `People` property added once (operational note)

**File:** `scripts/create_notion_databases.py` (docstring) + `docs/NOTION_SYNC.md`

Notion **ignores** unknown props on database *create* but **rejects writes** to a property that does not exist on the target DB. The three live digest DBs (clawbytes/modelbytes/interestingrepos) were created before `People` existed, so writing `People` to them will 400 until the property is added. New DBs created via the script will get it automatically (Task 5). This is an ops note, not code.

- [ ] Add a note to the `create_notion_databases.py` module docstring (after the existing usage block):

```
Note: databases created BEFORE the People property was added (clawbytes,
modelbytes, interestingrepos) need "People" (multi_select) added by hand in
Notion once, or item writes that include people will be rejected. Re-running
this script does NOT alter existing DBs — it only creates missing ones.
```

- [ ] Add the same operational step to `docs/NOTION_SYNC.md` under a short "Schema changes" heading so the owner has a checklist (add the `People` multi_select column to the three existing digest databases by hand, or via the Notion API `PATCH /databases/{id}`).
- [ ] No test (doc + comment only). Verify the script still imports cleanly:

```bash
python3 -c "import ast; ast.parse(open('scripts/create_notion_databases.py').read()); print('ok')"
```

- [ ] Commit: `docs(notion): note People property must be added to pre-existing digest DBs`

---

## Task 8 — Backfill the `repos` (interestingrepos) channel history (operational)

**Files:** new `scripts/import_digest_posts.py`; references `src/services/distribution_guard.py`, `src/services/processor.py`

**Why this is needed and how it's bounded.** The `repos` wire has only **3 live summaries** vs claw (27) and models (11) because the channel was added after the others and never backfilled. Tele-sum is **bot-mode only**; the Telegram Bot API's `getUpdates` cannot fetch channel history — only the removed Telethon path could. So the *source of historical text* must come from **outside the app**, exactly as the prior claw/models backfills did: the public web preview at **`https://t.me/s/interestingrepos`** (Telegram's "preview" pages expose past posts as HTML when the channel is public) was used as the source whenever `posted_digests` lacked stored text. Sourcing that raw text is producer-adjacent work and still on **tele-sum's side** of the boundary (it produces `messages`/`summaries`); it is **not** wire-room-ops'. The realistic in-lane deliverable is a small **idempotent import script** that takes already-exported post text + Telegram message ids and inserts `messages` + `PENDING` `summaries`, reusing the live ingest's dedup and distribution-guard logic, then lets the existing processor summarize them on its normal cadence.

### 8a — Write the idempotent importer (with tests)

- [ ] Write failing tests in a new file `tests/test_import_digest_posts.py` (use the same in-memory/session pattern as `tests/test_processor_digest_integration.py`):

```python
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.models.models import Base, ChannelConfig, Message, Summary, ProcessingStatus
from src.scripts_import import import_one_post  # see implementation note


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
```

- [ ] Run it — fails (module missing):

```bash
python3 -m pytest tests/test_import_digest_posts.py -q
```

- [ ] Implement `scripts/import_digest_posts.py`. Mirror the live ingest path in `telegram_bot.py` lines 184–238 exactly (dedup on `telegram_id + channel_id`, distribution-guard skip, `import_method="file"`, inherit `wire` from the channel, create a `PENDING` `Summary`). Put the unit-testable core in an importable function:

```python
"""One-off backfill: import already-exported digest posts as messages + PENDING
summaries, then let the normal processor summarize them.

Source of post text: the public web preview https://t.me/s/<channel> (the same
source the claw/models backfills used when posted_digests lacked stored text).
Export rows as JSONL: {"telegram_id": int, "text": str, "url": "optional"}.

Usage:
  DATABASE_URL=... python3 scripts/import_digest_posts.py interestingrepos posts.jsonl
"""
import json, os, sys
from datetime import datetime, timezone
from typing import Optional
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.models.models import (ChannelConfig, Message, Summary, ProcessingStatus)
from src.services.distribution_guard import is_distribution_post
from src.utils.url_extractor import extract_urls


def import_one_post(db: Session, channel: ChannelConfig, telegram_id: int,
                    text: str, url_text, posted_at: Optional[datetime] = None):
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
    urls = extract_urls(text)
    msg = Message(
        telegram_id=telegram_id,
        channel_id=channel.channel_id,
        channel_config_id=channel.id,
        import_method="file",                  # backfill marker
        wire=channel.wire,
        # Message.timestamp is NOT NULL with no default. Backfilled posts may not have
        # a reliable original date, so use the parsed post time when available, else now.
        timestamp=posted_at or datetime.now(timezone.utc),
        raw_content=text,
        extracted_urls=urls,
        entity_links=[],
    )
    db.add(msg)
    db.flush()
    db.add(Summary(message_id=msg.id, wire=channel.wire,
                   processing_status=ProcessingStatus.PENDING))
    return "imported"


def main() -> int:
    channel_username, jsonl_path = sys.argv[1], sys.argv[2]
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
                                    posted_at=posted_at)
                # Commit per imported row. is_distribution_post() fails open with
                # db.rollback() on ANY error; a single end-of-loop commit would let
                # one transient guard error discard every prior uncommitted row.
                # Per-row commit keeps the import crash-safe and idempotent on re-run.
                if r == "imported":
                    db.commit()
                counts[r] = counts.get(r, 0) + 1
    print(counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

> Implementation note for tests: the test imports `from src.scripts_import import import_one_post`. Either (a) add a thin re-export module `src/scripts_import.py` that does `from scripts.import_digest_posts import import_one_post` (preferred — keeps `scripts/` import-friendly without a package), or (b) make `tests/test_import_digest_posts.py` import directly from `scripts.import_digest_posts` and add an empty `scripts/__init__.py`. Pick whichever matches the repo's existing test import style; the existing `conftest.py` already puts the repo root on `sys.path`.

- [ ] Run it — passes:

```bash
python3 -m pytest tests/test_import_digest_posts.py -q
```

- [ ] Commit: `feat(scripts): idempotent digest-post backfill importer (file import_method)`

### 8b — Run the backfill (operational; owner-gated)

These are run against **production** with the live `DATABASE_URL`; treat as the destructive-adjacent step the brain rules gate on. Do **not** run without owner sign-off.

- [ ] Source the post text. Add the parser `scripts/parse_tme_preview.py` (BeautifulSoup4 is already a dependency) — it turns a `t.me/s/<channel>` preview into the importer's exact JSONL contract, including the `posted_at` the importer consumes:

```python
"""Parse a t.me/s/<channel> preview HTML into importer JSONL on stdout.
One row per post: {"telegram_id": int, "text": str, "url": str|null, "posted_at": iso8601|null}
Usage: python3 scripts/parse_tme_preview.py /tmp/preview.html > /tmp/repos_posts.jsonl
"""
import json, sys
from bs4 import BeautifulSoup

with open(sys.argv[1], encoding="utf-8") as f:
    soup = BeautifulSoup(f.read(), "html.parser")

for w in soup.select(".tgme_widget_message"):
    post = w.get("data-post", "")                  # "<channel>/<id>"
    tid = post.rsplit("/", 1)[1] if "/" in post else ""
    if not tid.isdigit():
        continue
    text_el = w.select_one(".tgme_widget_message_text")
    text = text_el.get_text("\n", strip=True) if text_el else ""
    if not text.strip():
        continue
    link_el = text_el.select_one("a[href]") if text_el else None
    time_el = w.select_one("time[datetime]")
    print(json.dumps({
        "telegram_id": int(tid),
        "text": text,
        "url": link_el["href"] if link_el else None,
        "posted_at": time_el["datetime"] if time_el else None,
    }))
```

```bash
# the same source prior backfills used; t.me/s/<channel> renders past posts as HTML
curl -s "https://t.me/s/interestingrepos" -o /tmp/interestingrepos_preview.html
python3 scripts/parse_tme_preview.py /tmp/interestingrepos_preview.html > /tmp/repos_posts.jsonl
# NOTE: one preview fetch returns only the most recent ~20 posts. For deeper history,
# page back with the ?before=<oldest_id> query param and concatenate the JSONL files.
```

- [ ] Dry-count what would import (idempotent — safe to run repeatedly; existing 3 live posts will report as `duplicate`):

```bash
DATABASE_URL="$PROD_DATABASE_URL" python3 scripts/import_digest_posts.py interestingrepos /tmp/repos_posts.jsonl
# expect: {'imported': N, 'duplicate': 3, 'empty': 0, 'distribution': M}
```

- [ ] Let the in-process scheduler's `process_pending` job (every 2 min) summarize the new `PENDING` rows on its normal cadence, or trigger it manually:

```bash
curl -X POST "$URL/api/trigger-process" -H "X-API-Key: $API_KEY"
```

- [ ] Verify the channel is populated and clean:

```sql
SELECT m.wire, s.processing_status, COUNT(*)
FROM summaries s JOIN messages m ON m.id = s.message_id
WHERE m.wire = 'repos'
GROUP BY 1, 2 ORDER BY 1, 2;
-- expect repos SUCCESS count to jump from 3 toward parity with claw/models
```

- [ ] (No commit — this is operational. Record the run + counts in the workstream `log.md`.)

> Feasibility honesty: if `t.me/s/interestingrepos` is not public (no preview) and no Telethon export of the text exists, this backfill is **not achievable in-lane** — close it as blocked rather than building a Telethon path into a bot-only app. The importer (8a) still ships and remains the supported mechanism for any future text export.

---

## Task 9 — Document the producer/consumer swimlane boundary in `CLAUDE.md`

**File:** `~/Documents/Sovereign Signal/repos/tele-sum/CLAUDE.md`

- [ ] Add a new subsection under the **Database — tele-sum's tables** section (after the "Other tables on the same Postgres instance…" paragraph) titled **Producer contract & sibling-owned tables**. It must state:
  1. **Direction:** tele-sum is the **producer**; its job ends at writing `summaries` (`title`/`summary`/`category`/`confidence`) + validated `entities` JSON. It **never reads back** CRM/org/contact state.
  2. **The contract surface** is `summaries.entities` (untyped JSONB in DB, validated on tele-sum's side before storage). Consumers read it directly via SQL, never via an API.
  3. **Discriminator:** grant-profile rows have **no** `entities.profile` key; digest-profile rows set `entities.profile = 'digest'`.
  4. **Grant shape:** `entities = { source, orgs: [str], amounts: [str], deadlines: [str], key_terms: [str], urls: [str] }` (read `entities->>'orgs'`; the `protocols` key was removed).
  5. **Digest shape:** `entities = { profile: 'digest', items: [DigestItem...], urls: [str], confidence }`, where each item is `{ title, url, note, category, tags, org, people:[{name, role, handle}] }`. **Document the new `people` field explicitly** and that it is optional/backward-compatible (old rows have no `people` key; consumers must treat it as optional).
  6. **Positional key:** wire-room-ops links items back by `(summary_id, item_index)`; reordering or splitting `entities.items` breaks that mapping — keep item order stable.
  7. **Sibling-owned (wire-room-ops), do NOT read or write:** name them — `org_profiles`, `digest_item_orgs`, `discovered_contacts`, `monitored_accounts`, `scan_entries`, `grant_entries`, `org_profiles`-derived CRM Notion sync. Cross-lane touch points stay exactly as already documented: tele-sum **reads** `api_providers` + `distribution_logs`, **writes** `api_usage_log`, and nothing else.

- [ ] Suggested text to insert:

```markdown
### Producer contract & sibling-owned tables

Tele-sum is the **producer**. Its job ends at writing `summaries`
(`title`/`summary`/`category`/`confidence`) plus a validated `entities` JSONB
blob. It **never reads back** CRM/org/contact state. The contract surface is
`summaries.entities`, consumed by wire-room-ops directly via SQL (no API).

- **Discriminator:** grant rows have no `entities.profile`; digest rows set
  `entities.profile = 'digest'`.
- **Grant shape:** `{ source, orgs:[str], amounts:[str], deadlines:[str],
  key_terms:[str], urls:[str] }` — read `entities->>'orgs'`.
- **Digest shape:** `{ profile:'digest', items:[item...], urls:[str], confidence }`
  where each item is `{ title, url, note, category, tags, org,
  people:[{name, role, handle}] }`. `org` = an organization/project (never a
  person). `people` = named individuals (author/founder/maintainer); **optional
  and backward-compatible** — pre-existing rows have no `people` key, so
  consumers MUST treat it as optional. This `people` field is the cheap source
  for wire-room-ops contact sourcing (Phase 3): read it instead of re-deriving
  contacts via web search.
- **Positional key:** consumers link items by `(summary_id, item_index)`. Do not
  reorder or split `entities.items` — it breaks that mapping.

**Sibling-owned (wire-room-ops) — do NOT read or write from tele-sum:**
`org_profiles`, `digest_item_orgs`, `discovered_contacts`, `monitored_accounts`,
`scan_entries`, `grant_entries`, and the SimpleCRM Notion sync. Cross-lane
touch points are unchanged: tele-sum READS `api_providers` and
`distribution_logs`, WRITES `api_usage_log`, nothing else.
```

- [ ] No test (doc only). Verify the file still reads as valid Markdown and the new section is present:

```bash
grep -n "Producer contract & sibling-owned tables" "~/Documents/Sovereign Signal/repos/tele-sum/CLAUDE.md"
```

- [ ] Also update `docs/WIRES_AND_PROFILES.md` to add `people` to the documented digest item shape so the two docs agree (source-of-truth hygiene).
- [ ] Commit: `docs(claude-md): document producer contract, people field, sibling-owned tables`

---

## Self-Review

Before calling this plan done, the executing worker MUST confirm each:

- [ ] **Every task is TDD-shaped** where code changes (Tasks 1–6, 8a): a failing test was written and seen to fail *before* implementation, then seen to pass after. Doc/ops tasks (7, 8b, 9) have an explicit verification step instead.
- [ ] **Full suite green:** `python3 -m pytest tests/ -q` passes (≥ the original 42 tests plus the new ones), run from the repo root so `conftest.py` is picked up.
- [ ] **No processor change was needed** for people to persist — Task 2 proves storage dumps `result["items"]` verbatim. If a `processor.py` edit crept in, stop and re-justify it.
- [ ] **Backward compatibility:** existing `summaries` rows with no `people` key still validate and read; `people` defaults to `[]`. No migration was added (entities is schemaless JSONB).
- [ ] **Boundary respected:** no file under `wire-room-ops/` was touched; no write to `org_profiles`/`digest_item_orgs`/`discovered_contacts`/`monitored_accounts`/`scan_entries`/`grant_entries`; the only cross-lane I/O remains read `api_providers`+`distribution_logs`, write `api_usage_log`.
- [ ] **Open decision recorded:** the `Person`-struct vs `List[str]` choice was made (structured `Person`) with the owner aware; if the owner overrode it, Tasks 1–4 were re-shaped and 5–9 re-checked.
- [ ] **Backfill (Task 8b) is owner-gated** and idempotent; the existing 3 live `repos` rows report as `duplicate`, not re-imported; counts recorded in the workstream `log.md`.
- [ ] **Notion ops note shipped:** the three pre-existing digest DBs (clawbytes/modelbytes/interestingrepos) need the `People` property added by hand once before people writes succeed — captured in `create_notion_databases.py` and `docs/NOTION_SYNC.md`.

---

## Execution Handoff

Choose one path to execute this plan:

- **Subagent-driven (recommended for the code tasks 1–6, 8a):** use `superpowers:subagent-driven-development`. Dispatch each task to a fresh subagent with the exact file paths and snippets above; the subagent writes the failing test, confirms red, implements, confirms green, and commits. Tasks are mostly independent (1→2 sequential; 3, 4 follow 1; 5→6 sequential; 8a independent; 7, 9 doc-only) so several can run in parallel after Task 1 lands.
- **Inline (recommended for ops/doc tasks 7, 8b, 9):** use `superpowers:executing-plans` in this session with review checkpoints. Task 8b in particular runs against production `DATABASE_URL` and is owner-gated — keep it inline and confirm counts with the owner before and after.

Suggested order: 1 → 2 → (3 ∥ 4) → 5 → 6 → 7 → 9 inline; then 8a (subagent) → 8b (inline, owner-gated) last, since the backfill is the only step that depends on an external text source and live production data.
