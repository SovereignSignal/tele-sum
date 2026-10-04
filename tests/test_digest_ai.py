from src.services.ai import _build_digest_prompt, _parse_digest_response


def test_digest_prompt_notes_are_facts_only():
    prompt = _build_digest_prompt(
        message_content="post", urls=[], url_contents={}, prompt_hint=None, links=[],
    )
    assert 'note": "what it is, facts only' in prompt
    assert "why it matters" not in prompt
    assert "what it is / why it matters" not in prompt


def test_digest_prompt_includes_hint_content_and_json_contract():
    prompt = _build_digest_prompt(
        message_content="🦀 ClawBytes Daily ...",
        urls=["https://github.com/openclaw/openclaw"],
        url_contents={"https://github.com/openclaw/openclaw": "OpenClaw repo readme..."},
        prompt_hint="Daily Claw ecosystem digest; categories: release, security, read.",
        links=[],
    )
    assert "Daily Claw ecosystem digest" in prompt
    assert "🦀 ClawBytes Daily" in prompt
    assert '"items"' in prompt          # JSON contract is spelled out
    assert "OpenClaw repo readme" in prompt


def test_parse_digest_response_validates_and_returns_dict():
    raw = '{"title": "T", "summary": "S", "confidence": "confident", "items": [{"title": "A", "url": "https://a.dev", "note": "n"}]}'
    parsed = _parse_digest_response(raw)
    assert parsed is not None
    assert parsed["items"][0]["title"] == "A"


def test_parse_digest_response_rejects_invalid_payload():
    assert _parse_digest_response('{"items": [{"url": "https://no-title.dev"}]}') is None
    assert _parse_digest_response("not json at all") is None


def test_digest_prompt_lists_entity_links():
    prompt = _build_digest_prompt(
        message_content="post", urls=[], url_contents={},
        prompt_hint=None,
        links=[{"text": "Ratty Terminal", "url": "https://github.com/orhun/ratty"}],
    )
    assert "Ratty Terminal" in prompt and "https://github.com/orhun/ratty" in prompt


# --- people extraction (Task A) ---
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


def test_parse_digest_response_carries_people():
    raw = ('{"title":"T","summary":"S","confidence":"confident",'
           '"items":[{"title":"A","url":"https://a.dev","note":"n","org":"Acme",'
           '"people":[{"name":"Jo","role":"founder","handle":"@jo"}]}]}')
    parsed = _parse_digest_response(raw)
    assert parsed["items"][0]["org"] == "Acme"
    assert parsed["items"][0]["people"][0]["name"] == "Jo"
