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
        
        prompt_parts.append("You are analyzing a message from a Telegram channel. Your task is to summarize the content and categorize it.")
        prompt_parts.append(f"\n## Original Message\n{message_content}")
        
        # Handle Twitter/X URLs - explicitly tell Grok to read them
        if twitter_urls:
            prompt_parts.append("\n## Twitter/X Links (IMPORTANT: Please access and read these tweets directly)")
            for url in twitter_urls:
                prompt_parts.append(f"- {url}")
            prompt_parts.append("\nYou have the ability to access Twitter/X content. Please read the tweets at the URLs above and include their content in your analysis.")
        
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
        prompt_parts.append("1. **Summary**: Write a 2-3 sentence summary explaining:")
        prompt_parts.append("   - What this content is about")
        prompt_parts.append("   - Why it matters or what the key insight is")
        prompt_parts.append("   - Explicitly mention any: protocols/projects, funding amounts, deadlines, or key dates")
        
        # Category classification with keywords
        if categories:
            prompt_parts.append("\n2. **Classification**: Categorize this into ONE of these categories:")
            for cat in categories:
                keywords_hint = f" (keywords: {', '.join(cat.keywords)})" if cat.keywords else ""
                description = f" - {cat.description}" if cat.description else ""
                prompt_parts.append(f"   - **{cat.name}**{description}{keywords_hint}")
            prompt_parts.append("\n3. **Confidence**: Rate your categorization confidence as 'confident' or 'uncertain'")
        else:
            prompt_parts.append("\n2. **Classification**: Suggest a general category for this content")
            prompt_parts.append("3. **Confidence**: Mark as 'uncertain' since no categories are defined")
        
        prompt_parts.append("\n## Response Format")
        prompt_parts.append("Respond ONLY with valid JSON in this exact format:")
        prompt_parts.append("""```json
{
  "summary": "Your 2-3 sentence summary here",
  "category": "Selected category name",
  "confidence": "confident or uncertain",
  "entities": {
    "protocols": ["list of protocols/projects mentioned"],
    "amounts": ["funding amounts like $X million"],
    "dates": ["specific dates or deadlines"],
    "key_terms": ["important technical terms"]
  }
}
```""")
        
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
            "model": "grok-2",
            "messages": [
                {
                    "role": "system",
                    "content": "You are a content analysis assistant specialized in analyzing Telegram channel messages. You can access Twitter/X URLs directly to read tweet content. Always respond with valid JSON only, no additional text."
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
            "summary": response.get("summary", ""),
            "category": response.get("category"),
            "confidence": None,
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
        
        # Normalize entities
        entities = validated["entities"]
        if not isinstance(entities, dict):
            entities = {}
        
        # Ensure all entity lists are actually lists
        for key in ["protocols", "amounts", "dates", "key_terms"]:
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
