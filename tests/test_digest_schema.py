import pytest
from pydantic import ValidationError

from src.schemas.digest import DigestAnalysis, DigestItem


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
