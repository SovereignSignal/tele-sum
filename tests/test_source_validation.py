import json

import pytest
from pydantic import ValidationError

from src.config import Settings
from src.services.ai import AIService, ProviderConfig, _parse_digest_response
from src.services.source_validation import source_metadata, validate_digest_count, validate_source_urls


@pytest.mark.parametrize('raw,expected', [('false', False), ('true', True), ('low', 'low'), ('medium', 'medium'), ('high', 'high'), ('max', 'max')])
def test_thinking_setting_parses_environment(monkeypatch, raw, expected):
    monkeypatch.setenv('AI_OLLAMA_THINK', raw)
    assert Settings(_env_file=None).ai_ollama_think == expected


def test_unsupported_thinking_setting_is_rejected(monkeypatch):
    monkeypatch.setenv('AI_OLLAMA_THINK', 'automatic')
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize('output', [
    {'items': [{'url': 'https://github.com/ejfkev/ddc'}]},
    {'items': [{'people': [{'handle': 'https://example.org/guessed-profile'}]}]},
    {'summary': 'Apply at https://example.org/guessed-route.'},
    {'url': 'https://github.com/ejfkdev'},  # prefix of a valid URL is not evidence
    {'url': 'javascript:alert(1)'},
])
def test_rejects_invented_links_in_any_field(output):
    with pytest.raises(ValueError, match='URL absent'):
        validate_source_urls(output, 'Source: https://github.com/ejfkdev/ddc')


def test_preserves_exact_case_queries_and_balanced_parentheses():
    url = 'https://example.org/Project_(API)?token=CaseSensitive'
    validate_source_urls({'url': url, 'note': f'Read [docs]({url}).'}, f'[docs]({url}).')
    with pytest.raises(ValueError):
        validate_source_urls({'url': url.lower()}, url)


def test_catches_missing_footer_against_explicit_count_only():
    with pytest.raises(ValueError, match='source total'):
        validate_digest_count([{}] * 7, 'Footer\nTotal: 12 models tracked today')
    validate_digest_count([{}] * 12, 'Total: 12 models tracked today')
    validate_digest_count([], 'No items provided')


def test_empty_json_cannot_become_a_successful_digest():
    assert _parse_digest_response('{}') is None


def test_unknown_source_type_stays_other_and_known_hosts_are_exact():
    assert source_metadata({'type': 'blog'}, [], {})['type'] == 'other'
    assert source_metadata({'type': 'blog'}, ['https://github.com/org/repo'], {})['type'] == 'github'
    assert source_metadata({'type': 'twitter'}, ['https://x.com.evil.example/post'], {})['type'] == 'other'


def test_tweet_metadata_comes_from_scraper_header_not_quoted_post():
    url = 'https://x.com/example/status/123'
    preview = 'Tweet by @example (Example Author)\nPosted: Fri Sep 25 12:00:00 +0000 2026\n\nUpdate\n--- Quoted Tweet ---\nOlder announcement'
    source = source_metadata({'name': 'Guessed', 'type': 'blog', 'original_date': '2026-01-01'}, [url], {url: preview})
    assert source == {'name': 'Example Author (@example)', 'type': 'twitter', 'original_date': '2026-09-25', 'url': url}
    assert source_metadata({'original_date': '2026-01-01'}, [url], {})['original_date'] is None


async def test_digest_service_rejects_valid_json_with_wrong_link(monkeypatch):
    service = AIService()
    service._config = ProviderConfig('ollama', 'test', 'test', 'https://example.org')

    async def response(*args, **kwargs):
        return json.dumps({'title': 'Repos', 'summary': 'One repo', 'confidence': 'confident', 'items': [
            {'title': 'DDC', 'url': 'https://github.com/ejfkev/ddc'}]}), 10, 10, 1

    monkeypatch.setattr(service, '_call_ollama', response)
    assert await service.analyze_digest('DDC', [], {}, links=[{'text': 'DDC', 'url': 'https://github.com/ejfkdev/ddc'}]) is None


async def test_grant_service_returns_grounded_source_metadata(monkeypatch):
    service = AIService()
    service._config = ProviderConfig('ollama', 'test', 'test', 'https://example.org')

    async def response(*args, **kwargs):
        return json.dumps({'title': 'Grants', 'summary': 'No link supplied.', 'category': None,
            'confidence': 'uncertain', 'source': {'type': 'blog'}, 'entities': {}}), 10, 10, 1

    monkeypatch.setattr(service, '_call_ollama', response)
    result = await service.analyze_content('No link supplied.', [], {}, [])
    assert result['source']['type'] == 'other'
