import pytest
from pydantic import ValidationError

from src.schemas.digest import DigestAnalysis, DigestItem, Person


def test_digest_parses_full_response():
    payload = {
        "title": "ClawBytes Daily — Jun 12",
        "summary": "Releases and security notes across the Claw ecosystem.",
        "confidence": "confident",
        "items": [
            {"title": "openclaw v2.1", "url": "https://github.com/openclaw/openclaw/releases/tag/v2.1",
             "note": "Adds sandboxed plugin runtime.", "category": "release", "tags": ["runtime"], "org": "OpenClaw"},
            {"title": "CVE in moltbook parser", "url": "https://example.org/advisory",
             "note": "Patch before Friday.", "category": "security"},
        ],
    }
    parsed = DigestAnalysis(**payload)
    assert len(parsed.items) == 2
    assert parsed.items[0].org == "OpenClaw"
    assert parsed.items[1].tags == []


def test_item_coerces_null_collections_and_strips_blank_urls():
    item = DigestItem(title="Thing", url="", note=None, tags=None)
    assert item.url is None
    assert item.note == ""
    assert item.tags == []


def test_items_without_title_are_rejected():
    with pytest.raises(ValidationError):
        DigestItem(url="https://x.com/post")


def test_empty_digest_is_valid_but_has_no_items():
    parsed = DigestAnalysis(title="Quiet day", summary="", items=[])
    assert parsed.items == []


# Fix 1 — items coercion
def test_items_single_dict_is_wrapped_and_null_is_empty():
    assert DigestAnalysis(items={"title": "Solo"}).items[0].title == "Solo"
    assert DigestAnalysis(items=None).items == []


# Fix 4 — confidence case coercion
def test_confidence_coercion():
    assert DigestAnalysis(confidence="Confident").confidence == "confident"
    assert DigestAnalysis(confidence="WILD").confidence == "uncertain"


# Fix 5 — DigestItem title strip
def test_title_strip():
    assert DigestItem(title="  X  ").title == "X"


def test_whitespace_only_title_raises():
    with pytest.raises(ValidationError):
        DigestItem(title="   ")


# --- people extraction (Task A) ---
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
        Person(role="founder")        # name omitted
    with pytest.raises(ValidationError):
        Person(name=None)             # explicit None -> "" -> min_length fails cleanly
    with pytest.raises(ValidationError):
        Person(name="   ")            # whitespace-only -> "" -> min_length fails


def test_digest_item_defaults_people_to_empty_list():
    item = DigestItem(title="Thing")
    assert item.people == []


def test_digest_item_coerces_people_forms_and_drops_nameless():
    # a lone dict is wrapped; a bare string becomes a name; entries without a
    # usable name are dropped; None is tolerated as empty.
    item = DigestItem(
        title="T",
        people=[{"name": "Jo", "role": "founder"}, {"role": "no name"}, "Bare Name", None],
    )
    assert [p.name for p in item.people] == ["Jo", "Bare Name"]
    assert item.people[0].role == "founder"
    assert DigestItem(title="T", people=None).people == []
    assert DigestItem(title="T", people={"name": "Solo"}).people[0].name == "Solo"


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
