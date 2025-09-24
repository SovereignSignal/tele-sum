import httpx
import logging
import json
from typing import Optional, Dict, List
from datetime import datetime

from src.config import settings
from src.models.models import Category, ConfidenceLevel
from src.utils.url_extractor import is_twitter_url

logger = logging.getLogger(__name__)

class GrokService:
    def __init__(self):
        self.api_key = settings.grok_api_key
        self.api_url = settings.grok_api_url
        self.is_configured = settings.is_grok_configured()
        
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
        prompt_parts = []
        
        prompt_parts.append("Analyze the following message and any linked content:")
        prompt_parts.append(f"\nOriginal Message: {message_content}")
        
        if urls:
            prompt_parts.append("\nLinked URLs:")
            for url in urls:
                prompt_parts.append(f"- {url}")
                if url in url_contents and url_contents[url]:
                    prompt_parts.append(f"  Content: {url_contents[url][:1000]}...")
        
        prompt_parts.append("\n\nProvide the following analysis:")
        prompt_parts.append("1. A 2-3 sentence summary explaining what this content is about and why it matters.")
        prompt_parts.append("   - Explicitly mention any protocols, projects, or companies discussed")
        prompt_parts.append("   - Include any funding amounts, deadlines, or key dates if present")
        prompt_parts.append("   - Focus on the main value or insight")
        
        if categories:
            category_names = [cat.name for cat in categories]
            prompt_parts.append(f"\n2. Classify this content into ONE of these categories: {', '.join(category_names)}")
            prompt_parts.append("3. Indicate your confidence level as either 'confident' or 'uncertain'")
        else:
            prompt_parts.append("\n2. Suggest a general category for this content")
            prompt_parts.append("3. Mark confidence as 'uncertain' since no categories are defined")
        
        prompt_parts.append("\n\nRespond in JSON format:")
        prompt_parts.append("""
{
  "summary": "Your 2-3 sentence summary here",
  "category": "Selected category name",
  "confidence": "confident" or "uncertain",
  "entities": {
    "protocols": ["list", "of", "protocols"],
    "amounts": ["$X funding", "Y tokens"],
    "dates": ["specific dates or deadlines mentioned"],
    "key_terms": ["important technical terms or concepts"]
  }
}""")
        
        return "\n".join(prompt_parts)
    
    async def analyze_content(
        self,
        message_content: str,
        urls: List[str],
        url_contents: Dict[str, str],
        categories: List[Category]
    ) -> Optional[Dict]:
        if not self.is_configured:
            logger.info("Grok API not configured, skipping content analysis")
            return None
            
        prompt = self.build_prompt(message_content, urls, url_contents, categories)
        
        payload = {
            "model": "grok-2",
            "messages": [
                {
                    "role": "system",
                    "content": "You are a content analysis assistant. Analyze messages and linked content to provide summaries and categorization. Always respond in valid JSON format."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "temperature": 0.3,
            "max_tokens": 500
        }
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.api_url}/chat/completions",
                    headers=self.headers,
                    json=payload
                )
                
                if response.status_code == 200:
                    result = response.json()
                    content = result["choices"][0]["message"]["content"]
                    
                    try:
                        parsed = json.loads(content)
                        return self.validate_response(parsed, categories)
                    except json.JSONDecodeError:
                        content_clean = content.strip()
                        if content_clean.startswith("```json"):
                            content_clean = content_clean[7:]
                        if content_clean.endswith("```"):
                            content_clean = content_clean[:-3]
                        
                        try:
                            parsed = json.loads(content_clean.strip())
                            return self.validate_response(parsed, categories)
                        except:
                            logger.error(f"Failed to parse Grok response: {content}")
                            return None
                            
                elif response.status_code == 429:
                    logger.warning("Grok API rate limit reached")
                    return None
                else:
                    logger.error(f"Grok API error: {response.status_code} - {response.text}")
                    return None
                    
        except httpx.TimeoutException:
            logger.error("Grok API request timed out")
            return None
        except Exception as e:
            logger.error(f"Unexpected error calling Grok API: {e}")
            return None
    
    def validate_response(self, response: Dict, categories: List[Category]) -> Dict:
        validated = {
            "summary": response.get("summary", ""),
            "category": response.get("category"),
            "confidence": None,
            "entities": response.get("entities", {})
        }
        
        confidence_str = response.get("confidence", "").lower()
        if confidence_str == "confident":
            validated["confidence"] = ConfidenceLevel.CONFIDENT
        elif confidence_str == "uncertain":
            validated["confidence"] = ConfidenceLevel.UNCERTAIN
        else:
            validated["confidence"] = ConfidenceLevel.UNCERTAIN
        
        if categories:
            category_names = [cat.name.lower() for cat in categories]
            selected_category = validated["category"]
            
            if selected_category and selected_category.lower() not in category_names:
                validated["confidence"] = ConfidenceLevel.UNCERTAIN
        
        if not validated["summary"]:
            return None
            
        return validated
    
    async def analyze_twitter_content(
        self,
        message_content: str,
        twitter_urls: List[str],
        categories: List[Category]
    ) -> Optional[Dict]:
        return await self.analyze_content(
            message_content,
            twitter_urls,
            {},
            categories
        )