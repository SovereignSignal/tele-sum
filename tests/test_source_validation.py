import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.config import Settings
from src.services.ai import AIService, ProviderConfig, _build_prompt, _parse_digest_response
from src.scrapers.content_scraper import ContentScraper
from src.services.source_validation import (
    funding_application_evidence, source_metadata, validate_digest_count, validate_source_urls,
)


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
    monkeypatch.setattr(service.rate_limiter, 'reserve', lambda db: None)

    async def response(*args, **kwargs):
        return json.dumps({'title': 'Repos', 'summary': 'One repo', 'confidence': 'confident', 'items': [
            {'title': 'DDC', 'url': 'https://github.com/ejfkev/ddc'}]}), 10, 10, 1

    monkeypatch.setattr(service, '_call_ollama', response)
    assert await service.analyze_digest('DDC', [], {}, links=[{'text': 'DDC', 'url': 'https://github.com/ejfkdev/ddc'}]) is None


async def test_grant_service_returns_grounded_source_metadata(monkeypatch):
    service = AIService()
    service._config = ProviderConfig('ollama', 'test', 'test', 'https://example.org')
    monkeypatch.setattr(service.rate_limiter, 'reserve', lambda db: None)

    async def response(*args, **kwargs):
        return json.dumps({'title': 'Grants', 'summary': 'No link supplied.', 'category': None,
            'confidence': 'uncertain', 'source': {'type': 'blog'}, 'entities': {}}), 10, 10, 1

    monkeypatch.setattr(service, '_call_ollama', response)
    result = await service.analyze_content('No link supplied.', [], {}, [])
    assert result['source']['type'] == 'other'


@pytest.mark.parametrize('evidence', ['Mar 17. Results from 2025.', '2026 results', '', 'Published March 17, 2025'])
def test_non_tweet_date_cannot_supply_a_missing_or_different_year(evidence):
    url = 'https://example.org/news'
    source = source_metadata({'original_date': '2026-03-17'}, [url], {url: evidence})
    assert source['original_date'] is None


@pytest.mark.parametrize('evidence', ['Published March 17, 2026', 'Research: 17 March 2026', 'Next item Mar. 17th, 2026', 'Published 2026-03-17'])
def test_page_body_dates_cannot_supply_publication_metadata(evidence):
    url = 'https://example.org/news'
    source = source_metadata({'original_date': '2026-03-17'}, [url], {url: evidence})
    assert source['original_date'] is None


@pytest.mark.parametrize('value,expected', [('2026-03-17T12:00:00Z', '2026-03-17'), ('2026-02-30', None), ('March 2026', None)])
def test_webpage_publication_metadata_is_validated_and_overrides_model(value, expected):
    html = f'<html><head><meta property="article:published_time" content="{value}"></head><body><article>Grant awarded. Next news item: August 28, 2026.</article></body></html>'
    evidence = ContentScraper().extract_text(html)
    url = 'https://example.org/news'
    source = source_metadata({'original_date': '2026-08-28'}, [url], {url: evidence})
    assert source['original_date'] == expected


def test_grant_prompt_excludes_unverified_wrapper_when_source_is_available():
    url = 'https://example.org/news'
    prompt = _build_prompt('Unsupported prize of $900,000', [url], {url: 'A prototype hackathon.'}, [])
    assert '$900,000' not in prompt
    assert 'A prototype hackathon.' in prompt
    assert '"original_date"' not in prompt
    fallback = _build_prompt('Unverified program announcement', [url], {}, [])
    assert 'Unverified program announcement' in fallback
    assert 'linked source is unavailable' in fallback


def test_webpage_preserves_update_boundaries_and_inline_evidence():
    html = '''<main><!-- Old draft: grants awarded in August --><div><div><span>Sept 2026</span><span>Funding</span></div>
      <h3>Awarded <strong>$1.6M</strong> in grants</h3><p>Build forecasting tools.</p></div>
      <div><div>August 28, 2026 Research</div><h3>A forecasting paper</h3></div>
      <p>Apply <a href="/apply">here</a> by
      September 30.</p></main>'''
    text = ContentScraper().extract_text(html)
    assert 'Old draft' not in text
    assert 'Sept 2026 Funding\n\nAwarded $1.6M in grants\n\nBuild forecasting tools.' in text
    assert 'tools.\n\nAugust 28, 2026 Research\n\nA forecasting paper' in text
    assert 'Apply here by September 30.' in text


@pytest.mark.parametrize('source,expected', [
    ('Applications are now open.', True),
    ('Apply today', True),
    ('Apply here: https://example.org/apply', True),
    ('Submit your proposal by September 30.', True),
    ('An open RFP category for infrastructure.', True),
    ('Register for the hackathon.', True),
    ('The foundation invites innovative research proposals.', True),
    ('The fund opens a security round featuring requests for proposals.', True),
    ('A new partnership supports voice infrastructure.', False),
    ('Deadline: September 30.', False),
    ('We are not accepting applications.', False),
    ('Do not apply here.', False),
    ('Applications are closed. The old notice said: Apply now.', False),
    ('Applications are not open. Apply here when we reopen.', False),
    ('Applications open next month.', False),
])
def test_funding_requires_supported_current_invitation(source, expected):
    url = 'https://example.org/program'
    evidence = funding_application_evidence('Apply now.', [url], {url: source})
    assert bool(evidence) is expected
    if expected:
        assert evidence in source


def test_wrapper_quoted_tweet_and_unseen_page_tail_cannot_supply_application_evidence():
    tweet = 'https://x.com/example/status/123'
    page = 'https://example.org/program'
    assert not funding_application_evidence('Apply now.', [tweet], {})
    assert not funding_application_evidence('', [tweet], {
        tweet: 'The round is over.\n--- Quoted Tweet ---\nApply now.',
    })
    assert not funding_application_evidence('', [page], {page: 'x' * 2001 + 'Apply now.'})
    assert funding_application_evidence('Apply here.', [], {}) == 'Apply here.'


@pytest.mark.parametrize('source,expected', [('A partnership.', None), ('Apply here.', 'Funding Opportunities')])
async def test_unsupported_funding_label_retains_summary_without_retry(monkeypatch, source, expected):
    service = AIService()
    service._config = ProviderConfig('ollama', 'test', 'test', 'https://example.org')
    monkeypatch.setattr(service.rate_limiter, 'reserve', lambda db: None)

    async def response(*args, **kwargs):
        return json.dumps({'title': 'Program Update', 'summary': 'A partnership.',
            'category': 'Funding Opportunities', 'confidence': 'confident', 'source': {},
            'entities': {'application_evidence': 'An invented application quote'}}), 10, 10, 1

    monkeypatch.setattr(service, '_call_ollama', response)
    url = 'https://example.org/program'
    categories = [SimpleNamespace(name='Funding Opportunities', description='', keywords=[])]
    result = await service.analyze_content('', [url], {url: source}, categories)
    assert result['summary'] == 'A partnership.'
    assert result['category'] == expected
    assert result['confidence'] == ('confident' if expected else 'uncertain')
    assert result['entities']['application_evidence'] == (source if expected else None)
