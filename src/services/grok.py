"""
Grok AI Integration Service

This module provides the interface to the Grok AI API (x.ai) for content analysis.
Grok is used because it can directly access Twitter/X content that other AI models cannot see.

Key components:
- GrokRateLimiter: Tracks API usage to stay under rate limits
- GrokService: Main service for analyzing content via Grok API

Usage:
    grok = GrokService()
    result = await grok.analyze_content(message, urls, url_contents, categories, db)

Configuration (via environment variables):
- GROK_API_KEY: Your Grok API key from x.ai
- GROK_API_URL: API endpoint (default: https://api.x.ai/v1)
- GROK_MODEL: Model to use (default: grok-3)
- MAX_GROK_REQUESTS_PER_HOUR: Rate limit (default: 100)

See PROCESSING.md for the full processing pipeline documentation.
"""

import httpx
import logging
import json
from typing import Optional, Dict, List
from datetime import datetime, timedelta
from sqlalchemy.orm import Session

from src.config import settings
from src.models.models import Category, ConfidenceLevel, GrokUsage
from src.utils.url_extractor import is_twitter_url

logger = logging.getLogger(__name__)

class GrokRateLimiter:
    """Simple rate limiter for Grok API calls"""
    
    def __init__(self, max_requests_per_hour: int):
        self.max_requests = max_requests_per_hour
        self.requests = []
    
    def can_make_request(self) -> bool:
        """Check if we can make another request"""
        now = datetime.utcnow()
        hour_ago = now - timedelta(hours=1)
        
        # Clean old requests
        self.requests = [r for r in self.requests if r > hour_ago]
        
        return len(self.requests) < self.max_requests
    
    def record_request(self):
        """Record a request"""
        self.requests.append(datetime.utcnow())
    
    def requests_remaining(self) -> int:
        """Get number of requests remaining in current window"""
        now = datetime.utcnow()
        hour_ago = now - timedelta(hours=1)
        self.requests = [r for r in self.requests if r > hour_ago]
        return max(0, self.max_requests - len(self.requests))

class GrokService:
    def __init__(self):
        self.api_key = settings.grok_api_key
        self.api_url = settings.grok_api_url
        self.is_configured = settings.is_grok_configured()
        self.rate_limiter = GrokRateLimiter(settings.max_grok_requests_per_hour)
        
        if self.is_configured:
            self.headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
        else:
            self.headers = {}
            logger.warning("⚠️  Grok API not configured. AI features will be disabled.")
    
    def build_prompt(
        self,
        message_content: str,
        urls: List[str],
        url_contents: Dict[str, str],
        categories: List[Category]
    ) -> str:
        """Build the analysis prompt for Grok"""
        prompt_parts = []

        # Identify Twitter URLs
        twitter_urls = [url for url in urls if is_twitter_url(url)]
        other_urls = [url for url in urls if not is_twitter_url(url)]

        # Check if we have any URLs at all
        has_urls = bool(twitter_urls or other_urls)

        if has_urls:
            prompt_parts.append("You are analyzing content that was curated and shared via a Telegram channel. The Telegram message itself is just a pointer - focus entirely on the LINKED CONTENT, not the message wrapper.")
            prompt_parts.append(f"\n## Telegram Message (just context, focus on the links)\n{message_content}")
        else:
            # No URLs - this is pre-summarized content from an external source
            prompt_parts.append("You are analyzing pre-summarized governance/grants content for The Grant Wire. The original source link is not available.")
            prompt_parts.append(f"\n## Content to Analyze\n{message_content}")
            prompt_parts.append("\nNOTE: Do NOT fabricate or guess URLs. Set source.url to null since the original link is unavailable.")
        
        # Handle Twitter/X URLs - use scraped content if available
        if twitter_urls:
            prompt_parts.append("\n## Twitter/X Content")
            for url in twitter_urls:
                prompt_parts.append(f"- {url}")
                if url in url_contents and url_contents[url]:
                    # We have scraped tweet content - use it
                    tweet_text = url_contents[url]
                    prompt_parts.append(f"  Tweet content:\n{tweet_text}")
                else:
                    # No scraped content - ask Grok to try reading directly
                    prompt_parts.append("  (Could not scrape - try reading directly if possible)")
            prompt_parts.append("\nIMPORTANT: Base your summary on the ACTUAL tweet content shown above. Do NOT guess or use your training data.")
        
        # Handle other URLs with scraped content
        if other_urls:
            prompt_parts.append("\n## Other Links")
            for url in other_urls:
                prompt_parts.append(f"- {url}")
                if url in url_contents and url_contents[url]:
                    content_preview = url_contents[url][:2000]
                    prompt_parts.append(f"  Content preview: {content_preview}...")
        
        # Instructions
        prompt_parts.append("\n## Your Task")
        prompt_parts.append("1. **Title**: Create a short, descriptive title (4-5 words max):")
        prompt_parts.append("   - NEWS (official announcements, vote results, grant rounds): Describe the action (e.g., 'Optimism Grants Council Results', 'Arbitrum Treasury Vote Passes')")
        prompt_parts.append("   - OPINION/ANALYSIS (someone sharing views, lessons, threads): Credit the author/source + topic (e.g., 'Chase Chapman on Grant Design', 'Polynya Analyzes L2 Economics')")
        prompt_parts.append("   - Use title case, no punctuation at the end")
        prompt_parts.append("   - Be specific - include protocol/project name when relevant")
        prompt_parts.append("")
        prompt_parts.append("2. **Summary**: Write a 3-4 sentence summary of the LINKED CONTENT (not the Telegram message):")
        prompt_parts.append("   - Focus on the substance: what is being announced, proposed, or reported")
        prompt_parts.append("   - Be detailed and informative - include specific numbers, amounts, percentages, requirements")
        prompt_parts.append("   - Concise but complete - no fluff, no flowery adjectives, just facts")
        prompt_parts.append("   - NEVER use the word 'blockchain' - say 'crypto', 'onchain', or name the specific chain (Ethereum, Optimism, etc.)")
        prompt_parts.append("   - Do NOT frame this as 'shared via' or 'posted on' - just describe the content directly")
        prompt_parts.append("   - IMPORTANT: For dates, use the ACTUAL date from the source content. Verify dates from the linked content, not today's date.")
        prompt_parts.append("   - CRITICAL: Distinguish between LAUNCHES vs CLOSURES/RESULTS. If the content says 'applications closed', 'wrap-up', 'results', or 'funded' - this is announcing OUTCOMES, not a new program launch. Read carefully!")
        
        # Category classification with keywords
        if categories:
            prompt_parts.append("\n3. **Classification**: Categorize this into ONE of these categories:")
            for cat in categories:
                keywords_hint = f" (keywords: {', '.join(cat.keywords)})" if cat.keywords else ""
                description = f" - {cat.description}" if cat.description else ""
                prompt_parts.append(f"   - **{cat.name}**{description}{keywords_hint}")
            prompt_parts.append("\n4. **Confidence**: Rate your categorization confidence as 'confident' or 'uncertain'")
        else:
            prompt_parts.append("\n3. **Classification**: Suggest a general category for this content")
            prompt_parts.append("4. **Confidence**: Mark as 'uncertain' since no categories are defined")
        
        prompt_parts.append("\n## Response Format")
        prompt_parts.append("Respond ONLY with valid JSON. Use EXACTLY these field names (they are strict), but values can vary:")
        prompt_parts.append("""```json
{
  "title": "Short 4-5 Word Title",
  "summary": "Your 3-4 sentence summary",
  "category": "MUST be one of the categories listed above - use exact name",
  "confidence": "confident OR uncertain (exactly one of these)",
  "source": {
    "name": "Descriptive source name (e.g., 'Optimism Governance Forum', '@username on X', 'Blog Name')",
    "type": "MUST be one of: forum, twitter, blog, governance, news, substack, mirror, medium, github, discord, other",
    "original_date": "YYYY-MM-DD format - the date the source published this, NOT today's date",
    "url": "null if no URL available, otherwise the source URL"
  },
  "entities": {
    "orgs": ["organizations/projects mentioned - use official names"],
    "amounts": ["funding amounts with currency, e.g., '$5M', '100,000 OP'"],
    "deadlines": ["specific deadlines in YYYY-MM-DD format when possible"],
    "key_terms": ["important domain-specific terms"]
  }
}
```""")
        prompt_parts.append("\nSTRICT RULES:")
        prompt_parts.append("- category: MUST exactly match one of the categories above")
        prompt_parts.append("- confidence: MUST be exactly 'confident' or 'uncertain'")
        prompt_parts.append("- source.type: MUST be one of the allowed types listed")
        prompt_parts.append("- All other values: use your judgment based on the content")
        
        return "\n".join(prompt_parts)
    
    async def analyze_content(
        self,
        message_content: str,
        urls: List[str],
        url_contents: Dict[str, str],
        categories: List[Category],
        db: Optional[Session] = None
    ) -> Optional[Dict]:
        """Analyze content using Grok API"""
        if not self.is_configured:
            logger.info("Grok API not configured, skipping content analysis")
            return None
        
        # Check rate limit
        if not self.rate_limiter.can_make_request():
            logger.warning(f"Grok rate limit reached. Remaining: {self.rate_limiter.requests_remaining()}")
            return None
        
        prompt = self.build_prompt(message_content, urls, url_contents, categories)
        
        payload = {
            "model": settings.grok_model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a content analyst for The Grant Wire, a crypto grants and funding news aggregator. Your job is to summarize linked content about: governance votes, treasury decisions, grant program results, funding opportunities, incentive programs, and grants ecosystem research/tools. You can access Twitter/X URLs directly. Always respond with valid JSON only, no additional text."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "temperature": 0.3,
            "max_tokens": 800
        }
        
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{self.api_url}/chat/completions",
                    headers=self.headers,
                    json=payload
                )
                
                # Record the request
                self.rate_limiter.record_request()
                
                # Log usage if db session provided
                if db:
                    usage = GrokUsage(
                        request_type="analysis",
                        success=response.status_code == 200
                    )
                    if response.status_code != 200:
                        usage.error_message = f"Status {response.status_code}: {response.text[:500]}"
                    db.add(usage)
                    db.commit()
                
                if response.status_code == 200:
                    result = response.json()
                    content = result["choices"][0]["message"]["content"]
                    
                    # Track token usage
                    if db and "usage" in result:
                        usage = db.query(GrokUsage).order_by(GrokUsage.id.desc()).first()
                        if usage:
                            usage.tokens_used = result["usage"].get("total_tokens", 0)
                            db.commit()
                    
                    return self.parse_response(content, categories)
                    
                elif response.status_code == 429:
                    logger.warning("Grok API rate limit reached (429)")
                    return None
                else:
                    logger.error(f"Grok API error: {response.status_code} - {response.text}")
                    return None
                    
        except httpx.TimeoutException:
            logger.error("Grok API request timed out")
            if db:
                usage = GrokUsage(
                    request_type="analysis",
                    success=False,
                    error_message="Request timed out"
                )
                db.add(usage)
                db.commit()
            return None
        except Exception as e:
            logger.error(f"Unexpected error calling Grok API: {e}")
            if db:
                usage = GrokUsage(
                    request_type="analysis",
                    success=False,
                    error_message=str(e)[:500]
                )
                db.add(usage)
                db.commit()
            return None
    
    def parse_response(self, content: str, categories: List[Category]) -> Optional[Dict]:
        """Parse and validate Grok response"""
        try:
            # Try direct JSON parse
            parsed = json.loads(content)
        except json.JSONDecodeError:
            # Try to extract JSON from markdown code blocks
            content_clean = content.strip()
            
            # Remove markdown code block markers
            if "```json" in content_clean:
                start = content_clean.find("```json") + 7
                end = content_clean.find("```", start)
                if end > start:
                    content_clean = content_clean[start:end].strip()
            elif "```" in content_clean:
                start = content_clean.find("```") + 3
                end = content_clean.find("```", start)
                if end > start:
                    content_clean = content_clean[start:end].strip()
            
            try:
                parsed = json.loads(content_clean)
            except json.JSONDecodeError:
                logger.error(f"Failed to parse Grok response: {content[:500]}")
                return None
        
        return self.validate_response(parsed, categories)
    
    def validate_response(self, response: Dict, categories: List[Category]) -> Optional[Dict]:
        """Validate and normalize the parsed response"""
        validated = {
            "title": response.get("title", ""),
            "summary": response.get("summary", ""),
            "category": response.get("category"),
            "confidence": None,
            "source": response.get("source", {}),
            "entities": response.get("entities", {})
        }
        
        # Validate summary
        if not validated["summary"] or len(validated["summary"]) < 10:
            logger.warning("Invalid or too short summary in Grok response")
            return None
        
        # Parse confidence
        confidence_str = str(response.get("confidence", "")).lower().strip()
        if confidence_str == "confident":
            validated["confidence"] = ConfidenceLevel.CONFIDENT
        else:
            validated["confidence"] = ConfidenceLevel.UNCERTAIN
        
        # Validate category against available categories
        if categories and validated["category"]:
            category_names_lower = {cat.name.lower(): cat.name for cat in categories}
            selected_lower = validated["category"].lower().strip()
            
            if selected_lower in category_names_lower:
                # Use the exact case from the database
                validated["category"] = category_names_lower[selected_lower]
            else:
                # Category doesn't match any defined category
                logger.warning(f"Grok selected unknown category: {validated['category']}")
                validated["confidence"] = ConfidenceLevel.UNCERTAIN
        
        # Normalize source
        source = validated["source"]
        if not isinstance(source, dict):
            source = {}

        # Validate source type against allowed values
        allowed_source_types = {"forum", "twitter", "blog", "governance", "news", "substack", "mirror", "medium", "github", "discord", "other"}
        source_type = source.get("type", "other").lower()
        if source_type not in allowed_source_types:
            source_type = "other"

        validated["source"] = {
            "name": source.get("name", "Unknown"),
            "type": source_type,
            "original_date": source.get("original_date")
        }

        # Normalize entities
        entities = validated["entities"]
        if not isinstance(entities, dict):
            entities = {}

        # Ensure all entity lists are actually lists
        for key in ["orgs", "amounts", "deadlines", "key_terms"]:
            if key not in entities:
                entities[key] = []
            elif not isinstance(entities[key], list):
                entities[key] = [entities[key]] if entities[key] else []
        
        validated["entities"] = entities
        
        return validated
    
    async def analyze_twitter_content(
        self,
        message_content: str,
        twitter_urls: List[str],
        categories: List[Category],
        db: Optional[Session] = None
    ) -> Optional[Dict]:
        """Analyze content that includes Twitter URLs"""
        # For Twitter URLs, we pass them directly and let Grok fetch them
        return await self.analyze_content(
            message_content,
            twitter_urls,
            {},  # No pre-scraped content for Twitter
            categories,
            db
        )
    
    def get_rate_limit_status(self) -> Dict:
        """Get current rate limit status"""
        return {
            "max_per_hour": self.rate_limiter.max_requests,
            "remaining": self.rate_limiter.requests_remaining(),
            "is_configured": self.is_configured
        }
