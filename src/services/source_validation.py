"""Check generated links and derive source metadata from supplied evidence."""

import re
from datetime import date
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

from src.utils.url_extractor import is_twitter_url

_URL = re.compile(r'''https?://[^\s<>"'`\[\]]+''', re.IGNORECASE)

_APPLICATION_INVITATION = re.compile(
    r"\b(?:apply\s*(?:[:→]|\b(?:now|today|here|at|via|by|for|to)\b)"
    r"|submit\s+(?:(?:your|an?|the)\s+)?(?:application|proposal|project|entry)\b"
    r"|(?:accepting|invites?|inviting)\s+(?:\w+\s+){0,3}(?:applications?|proposals?|submissions?)\b"
    r"|(?:applications?|submissions?|registration|RFPs?)(?:\s+(?:are|is|now|currently))*\s+open\b"
    r"|open\s+(?:RFP|call\s+for\s+(?:applications|proposals|submissions))\b"
    r"|opens\b[^.!?\n]{0,200}\brequests?\s+for\s+proposals\b"
    r"|register\s*(?:[:→]|\b(?:now|today|here|at|via|by|for)\b))",
    re.IGNORECASE,
)
_APPLICATION_CLOSED = re.compile(
    r"\b(?:not|never|no\s+longer|cannot|can't|do\s+not|don't)\s+(?:\w+\s+){0,3}"
    r"(?:apply|submit|accepting|inviting|register|open)\b"
    r"|\b(?:applications?|submissions?|registration|round|call|program|programme)\b"
    r"(?:\W+\w+){0,4}\W+(?:closed|paused|ended|suspended|not\s+open)\b"
    r"|\b(?:will\s+open|opening\s+soon|open\s+next)\b",
    re.IGNORECASE,
)


def funding_application_evidence(message_content, urls, url_contents):
    """Require a current, source-backed invitation before labeling an open round.

    This conservative gate can leave an ambiguous item unclassified. It never
    drops the summary or spends another model call trying to force a category.
    """
    sources = [
        (url_contents.get(url) or "").split("--- Quoted Tweet ---", 1)[0]
        if is_twitter_url(url) else (url_contents.get(url) or "")[:2000]
        for url in urls
    ] if urls else [message_content]
    for source in sources:
        if _APPLICATION_CLOSED.search(" ".join(source.split())):
            continue
        # Extract the evidence ourselves: the model may paraphrase a quote or
        # merge a real invitation with an older quoted announcement.
        for paragraph in source.splitlines():
            paragraph = " ".join(paragraph.split())
            if _APPLICATION_INVITATION.search(paragraph):
                return paragraph
    return None


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
