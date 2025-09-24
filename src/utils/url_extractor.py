import re
from typing import List
from urllib.parse import urlparse

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

def clean_url(url: str) -> str:
    if not url:
        return url
    
    url = re.sub(r'\?.*$', '', url)
    
    url = re.sub(r'#.*$', '', url)
    
    url = url.rstrip('/')
    
    return url