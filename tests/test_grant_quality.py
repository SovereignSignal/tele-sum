import json
from types import SimpleNamespace

import pytest

from src.schemas.entities import EntitiesPayload
from src.scrapers.content_scraper import ContentScraper
from src.services.ai import AIService, ProviderConfig, _build_prompt
from src.services.source_validation import (
    clean_grant_entities, funding_application_evidence, monetary_amount, restore_source_urls,
)
from src.utils.source_text import MAX_SOURCE_CHARS, source_excerpt


@pytest.mark.parametrize('value', ['$500', '€300,000 total', 'USD 10,000', '6.29M OP', '500USDC', '224,659.40 USD', '100 euros', '$6M+ in credits'])
def test_cash_and_token_units_are_preserved(value):
    assert monetary_amount(value)


@pytest.mark.parametrize('value', ['six months of Pro', '6 months of Pro', '40 founding members', '10,000 grants', '100 API credits', '20% APR', '2 years', 'abundant compute', '1 MILLION', '5 GRANTS', '6 MONTHS'])
def test_counts_and_unpriced_benefits_are_not_amounts(value):
    assert not monetary_amount(value)


def test_entity_cleanup_preserves_people_and_benefits_in_their_own_fields():
    entities = EntitiesPayload(orgs=['Jane Doe', 'jane_doe', 'Jane Labs', 'Foundation'],
        people=['Jane Doe (@jane_doe)'], amounts=['$100', 'six months of Pro'], key_terms=[])
    clean_grant_entities(entities)
    assert entities.orgs == ['Jane Labs', 'Foundation']
    assert entities.people == ['Jane Doe (@jane_doe)']
    assert entities.amounts == ['$100']
    assert entities.key_terms == ['six months of Pro']


def test_missing_url_scheme_restored_without_guessing_or_changing_existing_links():
    url = 'http://example.org/Apply?track=Case'
    text = f'Apply: example.org/Apply?track=Case. Original {url} remains exact.'
    assert restore_source_urls(text, url) == f'Apply: {url}. Original {url} remains exact.'
    assert restore_source_urls('example.org/apply', url) == 'example.org/apply'
    assert restore_source_urls('example.org/Apply?track=Case', url + ' ' + url.replace('http:', 'https:')) == 'example.org/Apply?track=Case'
    assert restore_source_urls('person@example.org', 'https://example.org') == 'person@example.org'
    assert restore_source_urls('example.org.evil/path', 'https://example.org') == 'example.org.evil/path'


def test_late_application_table_and_real_link_survive_bounded_scraping():
    html = '<main><h1>Research Program</h1>' + '<p>Background information. </p>' * 300
    html += '<h2>Timetable</h2><table><tr><td>September 30, 2026</td><td>Application deadline</td></tr></table>'
    html += '<p>Submit your proposal <a href="/Apply?Track=AI">here</a>.</p></main>'
    evidence = ContentScraper().extract_text(html, 'https://example.org/program')
    assert len(evidence) <= MAX_SOURCE_CHARS
    assert 'Research Program' in evidence
    assert 'September 30, 2026' in evidence
    assert 'https://example.org/Apply?Track=AI' in evidence
    assert '[Source section omitted]' in evidence
    url = 'https://example.org/program'
    prompt = _build_prompt('Unsupported deadline', [url], {url: evidence}, [])
    assert 'September 30, 2026' in prompt
    assert funding_application_evidence('', [url], {url: evidence})
    assert source_excerpt(evidence) == evidence


def test_quoted_tweet_is_labelled_as_background_and_retains_eligibility():
    url = 'https://x.com/example/status/123'
    prompt = _build_prompt('', [url], {url: 'Current update.\n--- Quoted Tweet ---\nOlder update. Only eligible maintainers qualify.'}, [])
    assert 'CURRENT SOURCE UPDATE' in prompt and 'QUOTED BACKGROUND' in prompt
    assert prompt.index('Current update.') < prompt.index('Older update.')
    assert 'Only eligible maintainers qualify.' in prompt


@pytest.mark.parametrize('source', [
    'Send us your proposal by Friday.', 'Email your application to the program.',
    'Call for proposals: research grants.', 'Proposals are welcome.',
    'Submit your idea for the fellowship.', 'Applications are currently accepted.',
])
def test_more_explicit_invitations_are_recognized(source):
    assert funding_application_evidence(source, [], {}) == source


def test_excluded_applicants_do_not_close_an_otherwise_open_call():
    source = 'The foundation invites innovative research proposals.\n\nPost-doctoral candidates are not eligible to apply.\n\nApply here: https://example.org/form'
    assert funding_application_evidence(source, [], {}) == 'The foundation invites innovative research proposals.'
    assert funding_application_evidence('Applications are closed.\n\nApply here: https://example.org/form', [], {}) is None


@pytest.mark.parametrize('source', [
    'This is not a call for proposals.', 'No call for applications is planned.',
    'We are not accepting applications. Send your proposal next year.',
    'The program is closed. Applications are welcome in the archived notice.',
    'Applications open next year. Email your application later.',
    'Applications are not currently accepted.', 'Do not submit your idea.',
])
def test_negation_and_closure_override_invitation_language(source):
    assert funding_application_evidence(source, [], {}) is None


async def test_runtime_enforces_missing_source_uncertainty_and_entity_cleanup(monkeypatch):
    service = AIService()
    service._config = ProviderConfig('ollama', 'test', 'test', 'https://example.org')
    monkeypatch.setattr(service.rate_limiter, 'reserve', lambda db: None)
    async def response(*args, **kwargs):
        return json.dumps({'title':'Program', 'summary':'Unverified program announcement.',
            'category': 'Funding Opportunities', 'confidence': 'confident', 'source': {},
            'entities': {'amounts': ['6 months of Pro'], 'orgs': ['Jane'], 'people': ['Jane']}}), 1, 1, 1
    monkeypatch.setattr(service, '_call_ollama', response)
    out = await service.analyze_content('Apply now.', ['https://example.org/missing'], {},
        [SimpleNamespace(name='Funding Opportunities',description='',keywords=[])])
    assert out['confidence'] == 'uncertain' and out['category'] is None
    assert '1 of 1 unavailable' in out['source']['note']
    assert out['entities']['amounts'] == [] and out['entities']['orgs'] == []
