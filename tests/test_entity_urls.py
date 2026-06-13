from src.services.ai import attach_entity_urls


def test_fills_url_by_exact_title_match_case_insensitive():
    items = [{"title": "Openclaude Runtime", "url": None}, {"title": "Ratty Terminal", "url": None}]
    links = [{"text": "openclaude runtime", "url": "https://github.com/Gitlawb/openclaude"},
             {"text": "Ratty Terminal", "url": "https://github.com/orhun/ratty"}]
    out = attach_entity_urls(items, links)
    assert out[0]["url"] == "https://github.com/Gitlawb/openclaude"
    assert out[1]["url"] == "https://github.com/orhun/ratty"


def test_keeps_existing_url_and_ignores_when_no_links():
    items = [{"title": "X", "url": "https://keep.me"}]
    assert attach_entity_urls(items, [])[0]["url"] == "https://keep.me"
    assert attach_entity_urls([{"title": "Y", "url": None}], [])[0]["url"] is None


def test_containment_fallback_when_not_exact():
    items = [{"title": "Petdex", "url": None}]
    links = [{"text": "Petdex Gallery", "url": "https://github.com/crafter-station/petdex"}]
    assert attach_entity_urls(items, links)[0]["url"] == "https://github.com/crafter-station/petdex"


def test_no_title_or_no_match_leaves_url_none():
    assert attach_entity_urls([{"title": "", "url": None}], [{"text": "a", "url": "u"}])[0]["url"] is None
    assert attach_entity_urls([{"title": "Zzz", "url": None}], [{"text": "abc", "url": "u"}])[0]["url"] is None
