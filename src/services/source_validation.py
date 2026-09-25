"""Check generated links and derive source metadata from supplied evidence."""

import re
from datetime import date
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

from src.utils.url_extractor import is_twitter_url
from src.utils.source_text import source_excerpt

_URL = re.compile(r'''https?://[^\s<>"'`\[\]]+''', re.IGNORECASE)

_APPLICATION_INVITATION = re.compile(
    r"\b(?:apply\s*(?:[:→]|\b(?:now|today|here|at|via|by|for|to)\b)"
    r"|submit\s+(?:(?:your|an?|the)\s+)?(?:application|proposal|project|entry|idea|work)\b"
    r"|(?:send|email)\s+(?:us\s+)?(?:(?:your|an?|the)\s+)?(?:application|proposal|submission)\b"
    r"|(?:call|request)\s+for\s+(?:applications|proposals|submissions)\b"
    r"|(?:applications?|proposals?|submissions?)(?:\s+(?:are|is|now|currently))*\s+(?:welcome|accepted)\b"
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
_CLOSED_CALL_STATUS = re.compile(
    r"\b(?:applications?|submissions?|registration|round|call|program|programme)\b"
    r"(?:\W+\w+){0,4}\W+(?:closed|paused|ended|suspended|not\s+open)\b"
    r"|\b(?:not|no\s+longer)\s+(?:currently\s+)?(?:accepting|inviting)\s+(?:\w+\s+){0,3}(?:applications?|proposals?|submissions?)\b"
    r"|\b(?:applications?|submissions?|registration|round|call|program|programme)\b[^.!?\n]{0,80}\b(?:will\s+open|opening\s+soon|open\s+next)\b",
    re.I,
)


def funding_application_evidence(message_content, urls, url_contents):
    """Require a current, source-backed invitation before labeling an open round.

    This conservative gate can leave an ambiguous item unclassified. It never
    drops the summary or spends another model call trying to force a category.
    """
    sources = [
        (url_contents.get(url) or "").split("--- Quoted Tweet ---", 1)[0]
        if is_twitter_url(url) else source_excerpt(url_contents.get(url) or "")
        for url in urls
    ] if urls else [message_content]
    for source in sources:
        flattened = " ".join(source.split())
        if _CLOSED_CALL_STATUS.search(source) or re.search(
            r"\b(?:no|not\s+an?|not\s+issuing)\s+(?:open\s+)?(?:call|request)\s+for\s+(?:applications|proposals|submissions)\b",
            flattened, re.I,
        ):
            continue
        # Extract the evidence ourselves: the model may paraphrase a quote or
        # merge a real invitation with an older quoted announcement.
        for paragraph in source.splitlines():
            paragraph = " ".join(paragraph.split())
            if _APPLICATION_INVITATION.search(paragraph) and not _APPLICATION_CLOSED.search(paragraph):
                return paragraph
    return None


def restore_source_urls(value, source_text):
    """Restore a missing scheme only when an exact supplied URL is unambiguous."""
    candidates = {}
    for url in _text_urls(source_text):
        bare = url.split("://", 1)[1]
        candidates.setdefault(bare, set()).add(url)
    for bare, urls in sorted(candidates.items(), key=lambda item: -len(item[0])):
        if len(urls) == 1:
            value = re.sub(
                r"(?<![\w/@:.-])" + re.escape(bare) + r"(?![\w/?#=&%-]|\.[\w])",
                lambda _: next(iter(urls)), value,
            )
    return value


_NUMBER = r"\d[\d,.]*(?:\s*(?:[kKmMbB](?![A-Za-z])|million\b|billion\b|thousand\b))?"
_SYMBOL_AMOUNT = re.compile(r"[$€£¥₹₩₽₺₴₪₦₱₫₿]\s*" + _NUMBER)
_UNIT_AMOUNT = re.compile(
    _NUMBER + r"\s*([A-Z][A-Z0-9]{1,5}|dollars?|euros?|pounds?|yen|bitcoin|ether)\b",
)
_CODE_AMOUNT = re.compile(r"\b(?:USD|EUR|GBP|JPY|CAD|AUD|CHF|INR|USDC|USDT|DAI|ETH|BTC)\s*" + _NUMBER)
_NON_CURRENCY_UNITS = {
    "AI", "API", "OSS", "TVL", "APR", "APY", "STEM", "PHD", "GRANTS",
    "DAY", "DAYS", "WEEK", "WEEKS", "MONTH", "MONTHS", "YEAR", "YEARS", "HOURS",
    "JAN", "FEB", "MAR", "APRIL", "MAY", "JUN", "JUL", "AUG", "SEP", "SEPT", "OCT", "NOV", "DEC",
}


def monetary_amount(value):
    """Retain quantified cash/token values; preserve other benefits elsewhere."""
    return bool(_SYMBOL_AMOUNT.search(value) or _CODE_AMOUNT.search(value) or any(
        match[1] not in _NON_CURRENCY_UNITS for match in _UNIT_AMOUNT.finditer(value)
    ))


def clean_grant_entities(entities):
    def identities(value):
        parts = [value, *re.findall(r"@([\w]+)|\(([^)]+)\)", value)]
        names = set()
        for part in parts:
            for item in part if isinstance(part, tuple) else [part]:
                if item:
                    names.add(item.strip().lstrip("@").casefold())
        # A combined "Full Name (@handle)" supplies both identities.
        names.add(value.split("(", 1)[0].strip().lstrip("@").casefold())
        return names - {""}

    people = set().union(*(identities(person) for person in entities.people)) if entities.people else set()
    entities.orgs = [org for org in entities.orgs if not identities(org) & people]
    kept = []
    for amount in entities.amounts:
        if monetary_amount(amount):
            kept.append(amount)
        elif amount not in entities.key_terms:
            entities.key_terms.append(amount)
    entities.amounts = kept


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
