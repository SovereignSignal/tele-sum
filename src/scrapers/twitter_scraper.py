"""
Twitter content scraper using FxTwitter/FixTweet API.
Fetches actual tweet content to pass to Grok for accurate summarization.
"""
import httpx
import logging
import re
from typing import Optional, Dict, List
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class TweetContent:
    """Structured tweet content"""
    author_name: str
    author_handle: str
    text: str
    created_at: str
    likes: int
    retweets: int
    replies: int
    media_urls: List[str]
    quoted_tweet: Optional['TweetContent'] = None

    def to_text(self) -> str:
        """Convert to readable text for Grok analysis"""
        parts = [
            f"Tweet by @{self.author_handle} ({self.author_name})",
            f"Posted: {self.created_at}",
            f"",
            f"{self.text}",
            f"",
            f"Engagement: {self.likes:,} likes, {self.retweets:,} retweets, {self.replies:,} replies"
        ]

        if self.media_urls:
            parts.append(f"Media: {len(self.media_urls)} image(s)/video(s) attached")

        if self.quoted_tweet:
            parts.append("")
            parts.append("--- Quoted Tweet ---")
            parts.append(f"@{self.quoted_tweet.author_handle}: {self.quoted_tweet.text}")

        return "\n".join(parts)


class TwitterScraper:
    """
    Scrapes Twitter/X content using FxTwitter API.
    FxTwitter provides tweet data as JSON without authentication.
    """

    def __init__(self):
        self.timeout = 15.0
        # FxTwitter API endpoint - converts twitter.com URLs to JSON
        self.api_base = "https://api.fxtwitter.com"
        self.headers = {
            "User-Agent": "TeleSum/1.0 (Twitter Content Fetcher)",
            "Accept": "application/json"
        }

    def extract_tweet_id(self, url: str) -> Optional[str]:
        """Extract tweet ID from various Twitter/X URL formats"""
        # Handle various URL formats:
        # - https://twitter.com/user/status/123456789
        # - https://x.com/user/status/123456789
        # - https://mobile.twitter.com/user/status/123456789
        # - https://twitter.com/user/status/123456789?s=20

        patterns = [
            r'(?:twitter\.com|x\.com)/\w+/status/(\d+)',
            r'(?:mobile\.twitter\.com)/\w+/status/(\d+)',
        ]

        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)

        return None

    def extract_username(self, url: str) -> Optional[str]:
        """Extract username from Twitter URL"""
        patterns = [
            r'(?:twitter\.com|x\.com)/(\w+)/status/',
            r'(?:mobile\.twitter\.com)/(\w+)/status/',
        ]

        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)

        return None

    async def scrape_tweet(self, url: str) -> Optional[TweetContent]:
        """
        Fetch tweet content from FxTwitter API.
        Returns structured TweetContent or None if failed.
        """
        username = self.extract_username(url)
        tweet_id = self.extract_tweet_id(url)

        if not username or not tweet_id:
            logger.warning(f"Could not parse Twitter URL: {url}")
            return None

        # FxTwitter API endpoint
        api_url = f"{self.api_base}/{username}/status/{tweet_id}"

        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
                response = await client.get(api_url, headers=self.headers)

                if response.status_code != 200:
                    logger.warning(f"FxTwitter API error for {url}: Status {response.status_code}")
                    return await self._try_nitter_fallback(url)

                data = response.json()

                if data.get("code") != 200:
                    logger.warning(f"FxTwitter returned error: {data.get('message', 'Unknown error')}")
                    return await self._try_nitter_fallback(url)

                tweet = data.get("tweet", {})
                return self._parse_tweet_data(tweet)

        except httpx.TimeoutException:
            logger.warning(f"Timeout fetching tweet: {url}")
            return await self._try_nitter_fallback(url)
        except Exception as e:
            logger.error(f"Error fetching tweet {url}: {e}")
            return await self._try_nitter_fallback(url)

    def _parse_tweet_data(self, tweet: dict) -> Optional[TweetContent]:
        """Parse FxTwitter API response into TweetContent"""
        try:
            author = tweet.get("author", {})

            # Get media URLs
            media_urls = []
            media = tweet.get("media", {})
            if media:
                for photo in media.get("photos", []):
                    media_urls.append(photo.get("url", ""))
                for video in media.get("videos", []):
                    media_urls.append(video.get("url", ""))

            # Handle quoted tweet if present
            quoted = None
            if tweet.get("quote"):
                quoted = self._parse_tweet_data(tweet["quote"])

            return TweetContent(
                author_name=author.get("name", "Unknown"),
                author_handle=author.get("screen_name", "unknown"),
                text=tweet.get("text", ""),
                created_at=tweet.get("created_at", ""),
                likes=tweet.get("likes", 0),
                retweets=tweet.get("retweets", 0),
                replies=tweet.get("replies", 0),
                media_urls=media_urls,
                quoted_tweet=quoted
            )
        except Exception as e:
            logger.error(f"Error parsing tweet data: {e}")
            return None

    async def _try_nitter_fallback(self, url: str) -> Optional[TweetContent]:
        """
        Fallback: Try to scrape from a Nitter instance.
        Nitter is an alternative Twitter frontend that doesn't require auth.
        """
        username = self.extract_username(url)
        tweet_id = self.extract_tweet_id(url)

        if not username or not tweet_id:
            return None

        # Try multiple Nitter instances
        nitter_instances = [
            "nitter.privacydev.net",
            "nitter.poast.org",
        ]

        for instance in nitter_instances:
            try:
                nitter_url = f"https://{instance}/{username}/status/{tweet_id}"

                async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                    response = await client.get(nitter_url, headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                    })

                    if response.status_code == 200:
                        return self._parse_nitter_html(response.text, username)

            except Exception as e:
                logger.debug(f"Nitter instance {instance} failed: {e}")
                continue

        return None

    def _parse_nitter_html(self, html: str, username: str) -> Optional[TweetContent]:
        """Parse tweet content from Nitter HTML"""
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, 'lxml')

            # Find the main tweet content
            tweet_content = soup.find("div", class_="tweet-content")
            if not tweet_content:
                return None

            text = tweet_content.get_text(strip=True)

            # Try to get author name
            fullname = soup.find("a", class_="fullname")
            author_name = fullname.get_text(strip=True) if fullname else username

            # Try to get date
            tweet_date = soup.find("span", class_="tweet-date")
            date_str = tweet_date.get_text(strip=True) if tweet_date else ""

            return TweetContent(
                author_name=author_name,
                author_handle=username,
                text=text,
                created_at=date_str,
                likes=0,
                retweets=0,
                replies=0,
                media_urls=[]
            )
        except Exception as e:
            logger.error(f"Error parsing Nitter HTML: {e}")
            return None

    async def scrape_multiple_tweets(self, urls: List[str]) -> Dict[str, str]:
        """
        Fetch content for multiple tweet URLs.
        Returns dict of URL -> tweet text content.
        """
        results = {}

        for url in urls:
            tweet = await self.scrape_tweet(url)
            if tweet:
                results[url] = tweet.to_text()
                logger.info(f"Successfully fetched tweet: @{tweet.author_handle}")
            else:
                logger.warning(f"Could not fetch tweet: {url}")

        return results


# Convenience function for quick testing
async def test_scrape(url: str):
    """Test scraping a single tweet"""
    scraper = TwitterScraper()
    tweet = await scraper.scrape_tweet(url)
    if tweet:
        print(tweet.to_text())
    else:
        print("Failed to fetch tweet")


if __name__ == "__main__":
    import asyncio
    import sys

    if len(sys.argv) > 1:
        asyncio.run(test_scrape(sys.argv[1]))
    else:
        print("Usage: python twitter_scraper.py <tweet_url>")
