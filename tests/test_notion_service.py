import json

import httpx
import pytest

from src.services.notion import NotionClient, build_item_properties, DIGEST_DB_PROPERTIES


def make_client(handler):
    transport = httpx.MockTransport(handler)
    return NotionClient(api_key="secret-token", transport=transport)


async def test_create_page_posts_to_notion_and_returns_page_id():
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "page-123"})

    client = make_client(handler)
    page_id = await client.create_page("db-1", {"Title": {"title": [{"text": {"content": "X"}}]}})
    assert page_id == "page-123"
    assert captured["url"] == "https://api.notion.com/v1/pages"
    assert captured["auth"] == "Bearer secret-token"
    assert captured["body"]["parent"] == {"database_id": "db-1"}


async def test_create_page_returns_none_on_api_error():
    def handler(request):
        return httpx.Response(400, json={"message": "validation_error"})

    client = make_client(handler)
    assert await client.create_page("db-1", {}) is None


async def test_create_database_builds_template_under_parent():
    captured = {}

    def handler(request):
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "db-99"})

    client = make_client(handler)
    db_id = await client.create_database("parent-page", "Interesting Repos")
    assert db_id == "db-99"
    assert captured["body"]["parent"] == {"type": "page_id", "page_id": "parent-page"}
    assert "Title" in captured["body"]["properties"]
    assert captured["body"]["properties"]["Tags"] == {"multi_select": {}}


def test_build_item_properties_maps_all_fields():
    item = {"title": "openclaw v2.1", "url": "https://github.com/openclaw", "note": "Sandboxed plugins.",
            "category": "release", "tags": ["runtime", "sandbox"], "org": "OpenClaw"}
    props = build_item_properties(item, posted_at="2026-06-12", source_link="https://t.me/clawbytes/42", wire="claw")
    assert props["Title"]["title"][0]["text"]["content"] == "openclaw v2.1"
    assert props["URL"]["url"] == "https://github.com/openclaw"
    assert props["Category"]["select"]["name"] == "release"
    assert {t["name"] for t in props["Tags"]["multi_select"]} == {"runtime", "sandbox"}
    assert props["Posted"]["date"]["start"] == "2026-06-12"
    assert props["Source post"]["url"] == "https://t.me/clawbytes/42"
    assert props["Wire"]["select"]["name"] == "claw"


def test_build_item_properties_omits_empty_optionals():
    props = build_item_properties({"title": "Bare"}, posted_at=None, source_link=None, wire=None)
    assert "URL" not in props and "Category" not in props and "Org" not in props
    assert "Posted" not in props and "Source post" not in props and "Wire" not in props
    assert props["Note"]["rich_text"] == []
