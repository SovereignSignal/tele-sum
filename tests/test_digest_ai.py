from src.services.ai import _build_digest_prompt, _parse_digest_response


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
