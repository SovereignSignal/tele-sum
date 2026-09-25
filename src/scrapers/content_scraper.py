import httpx
import logging
from datetime import date
from typing import Optional, Dict, List
from bs4 import BeautifulSoup, Comment
from urllib.parse import urljoin, urlsplit

from src.utils.url_extractor import is_twitter_url
from src.utils.source_text import MAX_SOURCE_CHARS, source_excerpt

logger = logging.getLogger(__name__)

class ContentScraper:
    def __init__(self):
        self.timeout = 10.0
        self.max_content_length = MAX_SOURCE_CHARS
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
    
    async def scrape_url(self, url: str) -> Optional[str]:
        if is_twitter_url(url):
            return None
        
        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
                response = await client.get(url, headers=self.headers)
                
                if response.status_code != 200:
                    logger.warning(f"Failed to fetch {url}: Status {response.status_code}")
                    return None
                
                content_type = response.headers.get("content-type", "").lower()
                if "text/html" not in content_type:
                    logger.info(f"Skipping non-HTML content from {url}")
                    return None
                
                return self.extract_text(response.text, base_url=str(response.url))
                
        except httpx.TimeoutException:
            logger.warning(f"Timeout fetching {url}")
            return None
        except Exception as e:
            logger.error(f"Error fetching {url}: {e}")
            return None
    
    def extract_text(self, html: str, base_url: Optional[str] = None) -> str:
        try:
            soup = BeautifulSoup(html, 'lxml')
            published_at = None
            published = soup.find('meta', attrs={'property': 'article:published_time'})
            if published and published.get('content'):
                try:
                    published_at = date.fromisoformat(published['content'][:10]).isoformat()
                except ValueError:
                    pass
            
            for script in soup(["script", "style", "nav", "header", "footer"]):
                script.decompose()
            
            content = soup.find("article") or soup.find("main") or soup.find("body") or soup
            # Retain actual link targets, including relative application links.
            for link in content.find_all("a", href=True):
                target = urljoin(base_url or "", link["href"])
                if urlsplit(target).scheme in ("https", "http") and target not in link.get_text():
                    link.append(f" ({target})")
            # Keep headings, dates and neighboring updates on separate lines.
            # Flattening the page can attach the next timeline date to this item.
            # Inline markup stays inline so amounts and application quotes survive.
            for node in content.find_all(string=True):
                if isinstance(node, Comment):
                    node.extract()
                else:
                    node.replace_with(" ".join(node.split()))
            for block in content.find_all([
                "div", "section", "article", "p", "h1", "h2", "h3", "h4",
                "h5", "h6", "li", "ul", "ol", "tr", "blockquote", "br",
            ]):
                block.insert_before("\n\n")
                block.insert_after("\n\n")
            text = "\n\n".join(
                line for raw in content.get_text(separator=" ", strip=False).splitlines()
                if (line := " ".join(raw.split()))
            )
            
            text = source_excerpt(text, self.max_content_length)
            
            if published_at:
                text = f"Source published: {published_at}\n\n{text}"
            return text
            
        except Exception as e:
            logger.error(f"Error extracting text: {e}")
            return ""
    
    async def scrape_multiple_urls(self, urls: List[str]) -> Dict[str, str]:
        results = {}
        
        non_twitter_urls = [url for url in urls if not is_twitter_url(url)]
        
        for url in non_twitter_urls:
            content = await self.scrape_url(url)
            if content:
                results[url] = content
        
        return results
