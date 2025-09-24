import httpx
import logging
from typing import Optional, Dict, List
from bs4 import BeautifulSoup
from urllib.parse import urlparse

from src.utils.url_extractor import is_twitter_url

logger = logging.getLogger(__name__)

class ContentScraper:
    def __init__(self):
        self.timeout = 10.0
        self.max_content_length = 5000
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
                
                return self.extract_text(response.text)
                
        except httpx.TimeoutException:
            logger.warning(f"Timeout fetching {url}")
            return None
        except Exception as e:
            logger.error(f"Error fetching {url}: {e}")
            return None
    
    def extract_text(self, html: str) -> str:
        try:
            soup = BeautifulSoup(html, 'lxml')
            
            for script in soup(["script", "style", "nav", "header", "footer"]):
                script.decompose()
            
            article = soup.find("article")
            if article:
                text = article.get_text(separator=" ", strip=True)
            else:
                main = soup.find("main")
                if main:
                    text = main.get_text(separator=" ", strip=True)
                else:
                    body = soup.find("body")
                    if body:
                        text = body.get_text(separator=" ", strip=True)
                    else:
                        text = soup.get_text(separator=" ", strip=True)
            
            text = " ".join(text.split())
            
            if len(text) > self.max_content_length:
                text = text[:self.max_content_length] + "..."
            
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