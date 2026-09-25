"""Check generated links and derive source metadata from supplied evidence."""

import re
from datetime import date
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

from src.utils.url_extractor import is_twitter_url

_URL = re.compile(r'''https?://[^\s<>"'`\[\]]+''', re.IGNORECASE)


def _text_urls(text):
    for match in _URL.finditer(text):
        url = match.group().rstrip('.,;:')
        # Drop prose/Markdown delimiters, retaining balanced URL parentheses.
        for opening, closing in [('(', ')'), ('{', '}')]:
            while url.endswith(closing) and url.count(closing) > url.count(opening):
                url = url[:-1]
        yield url


def validate_source_urls(value, source_text):
    """Reject invented links anywhere in a response, including notes and handles.

    Compare exact URLs; dedup normalization lowercases paths and is unsuitable
    for checking whether the model faithfully copied a source link.
    """
    allowed = set(_text_urls(source_text))

    def visit(node, field=None):
        if isinstance(node, dict):
            for key, child in node.items():
                visit(child, key)
        elif isinstance(node, list):
            for child in node:
                visit(child, field)
        elif isinstance(node, str):
            if field in ('url', 'urls') and node and node not in allowed:
                raise ValueError('AI response contains a URL absent from the supplied source')
            for url in _text_urls(node):
                if url not in allowed:
                    raise ValueError('AI response contains a URL absent from the supplied source')

    visit(value)


def validate_digest_count(items, message_content):
    """Honor an explicit source total without guessing a count from layout."""
    total = re.search(r'(?im)^\s*Total:\s*(\d+)\s+(?:models|repos|repositories|items)\b', message_content)
    if total and len(items) != int(total[1]):
        raise ValueError('Digest item count does not match the explicit source total')


def source_metadata(source, urls, url_contents):
    """Use the same primary URL as the processor; unknown source types stay other."""
    result = dict(source)
    url = urls[0] if urls else result.get('url')
    result['url'] = url
    host = (urlsplit(url).hostname or '').lower() if url else ''
    if is_twitter_url(url):
        result['type'] = 'twitter'
        # This header is emitted by our scraper, before any quoted tweet text.
        header = re.match(r'Tweet by @([^\s]+) \(([^\n]+)\)\nPosted: ([^\n]+)', url_contents.get(url, ''))
        if header:
            result['name'] = f'{header[2]} (@{header[1]})'
            try:
                result['original_date'] = parsedate_to_datetime(header[3]).date().isoformat()
            except (ValueError, TypeError, OverflowError):
                result['original_date'] = None
        else:
            # A tweet URL carries no publication date by itself.
            result['original_date'] = None
    elif host in ('github.com', 'www.github.com'):
        result['type'] = 'github'
    elif host == 'substack.com' or host.endswith('.substack.com'):
        result['type'] = 'substack'
    else:
        result['type'] = 'other'
    if not is_twitter_url(url):
        # Emitted only from explicit publication metadata by ContentScraper.
        # Arbitrary dates in a page body may belong to another news item.
        header = re.match(r'Source published: (\d{4}-\d{2}-\d{2})\n', url_contents.get(url, ''))
        result['original_date'] = None
        if header:
            try:
                result['original_date'] = date.fromisoformat(header[1]).isoformat()
            except ValueError:
                pass
    return result
