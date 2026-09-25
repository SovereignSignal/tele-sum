import re
from typing import List
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

# Tracking params stripped during normalization. Mirrors wire-room-ops/src/utils/url.ts.
_TRACKING_PARAMS = {
    "s", "t", "ref", "ref_src",
    "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term",
    "fbclid", "gclid",
}


def normalize_url(url: str) -> str:
    """Canonical URL form for deduplication.

    Mirrors the sibling app's `normalizeUrl` (wire-room-ops src/utils/url.ts) so the
    shared `seen_urls` table sees the same key from both apps.

    Rules: strip markdown link artifacts and trailing punctuation, collapse
    twitter.com/www.x.com to x.com, drop tracking params, drop trailing slashes,
    lowercase. Falls back to raw lowercase on parse error.
    """
    if not url:
        return ""

    # "url](url" markdown artifact — keep the part before "](".
    md = url.find("](")
    if md != -1:
        url = url[:md]
    # Trailing markdown/punctuation junk.
    url = re.sub(r"[\])\.,;:]+$", "", url)

    try:
        u = urlparse(url)
        host = (u.hostname or "").lower()
        if host in ("twitter.com", "www.twitter.com"):
            host = "x.com"
        elif host == "www.x.com":
            host = "x.com"

        # Reassemble netloc (preserve port if present).
        if u.port:
            netloc = f"{host}:{u.port}"
        else:
            netloc = host

        # Strip tracking params, preserve order/dupes for non-tracking ones.
        params = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True) if k.lower() not in _TRACKING_PARAMS]
        query = urlencode(params)

        path = u.path.rstrip("/")
        normalized = urlunparse((u.scheme.lower(), netloc, path, u.params, query, ""))
        return normalized.lower()
    except Exception:
        return url.lower().strip()

def extract_urls(text: str) -> List[str]:
    if not text:
        return []
    
    url_pattern = re.compile(
        r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+',
        re.IGNORECASE
    )
    
    urls = url_pattern.findall(text)
    
    t_co_pattern = re.compile(r'https?://t\.co/[a-zA-Z0-9]+')
    t_co_urls = t_co_pattern.findall(text)
    urls.extend(t_co_urls)
    
    telegram_pattern = re.compile(r'(?:t\.me|telegram\.me)/[a-zA-Z0-9_/]+')
    telegram_urls = telegram_pattern.findall(text)
    urls.extend([f"https://{url}" if not url.startswith("http") else url for url in telegram_urls])
    
    unique_urls = list(dict.fromkeys(urls))
    
    validated_urls = []
    for url in unique_urls:
        try:
            result = urlparse(url)
            if result.scheme and result.netloc:
                validated_urls.append(url)
        except:
            continue
    
    return validated_urls

def is_twitter_url(url: str) -> bool:
    if not url:
        return False
    
    twitter_domains = [
        'twitter.com',
        'x.com',
        't.co',
        'mobile.twitter.com',
        'mobile.x.com'
    ]
    
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower().replace('www.', '')
        return domain in twitter_domains
    except:
        return False

def embedded_urls(links) -> List[str]:
    """Recover exact HTTP links already stored as Telegram text entities."""
    result = []
    for link in links or []:
        url = link.get("url") if isinstance(link, dict) else None
        if not isinstance(url, str):
            continue
        try:
            parsed = urlparse(url)
            if parsed.scheme in ("http", "https") and parsed.hostname and url not in result:
                result.append(url)
        except ValueError:
            continue
    return result


def clean_url(url: str) -> str:
    if not url:
        return url
    
    url = re.sub(r'\?.*$', '', url)
    
    url = re.sub(r'#.*$', '', url)
    
    url = url.rstrip('/')
    
    return url
