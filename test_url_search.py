"""Find actual source URLs via web search before processing"""
import asyncio
import httpx
import json
import sys
sys.path.insert(0, '~/Documents/GitHub/tele-sum')

from src.models import database
from src.models.models import Message, Summary, Category, ProcessingStatus, ConfidenceLevel
from src.config import settings

async def search_for_source_url(content: str) -> dict:
    """Use Grok to search and find the actual source URL"""

    # First, extract key search terms from the content
    search_prompt = f"""Based on this governance content, create a specific search query to find the original source.

Content: {content[:500]}

Extract:
1. Protocol name (e.g., Arbitrum, MakerDAO, Scroll, Optimism)
2. Key identifying terms (proposal name, amounts, specific details)
3. The forum domain to search

Return JSON only:
```json
{{
  "protocol": "protocol name",
  "search_query": "specific search query",
  "forum_domain": "e.g., forum.arbitrum.foundation"
}}
```"""

    headers = {
        "Authorization": f"Bearer {settings.grok_api_key}",
        "Content-Type": "application/json"
    }

    # Grok with live search capability
    payload = {
        "model": settings.grok_model,
        "messages": [
            {
                "role": "system",
                "content": "You have web search capability. Find the actual URL for governance proposals."
            },
            {
                "role": "user",
                "content": f"""Search the web and find the actual forum URL for this governance content:

{content}

Search for the original proposal/discussion thread. Return ONLY valid JSON:
```json
{{
  "found_url": "the actual URL you found (or null if not found)",
  "source_name": "name of the source platform",
  "confidence": "high, medium, or low"
}}
```

Only return a URL if you actually found it via search. Do not guess or construct URLs."""
            }
        ],
        "temperature": 0.1,
        "max_tokens": 500
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{settings.grok_api_url}/chat/completions",
            headers=headers,
            json=payload
        )

        if response.status_code == 200:
            result = response.json()
            content = result["choices"][0]["message"]["content"]

            try:
                if "```json" in content:
                    start = content.find("```json") + 7
                    end = content.find("```", start)
                    content = content[start:end].strip()
                elif "```" in content:
                    start = content.find("```") + 3
                    end = content.find("```", start)
                    content = content[start:end].strip()

                return json.loads(content)
            except:
                return None

    return None

async def process_message_with_url_search(message_id: int, db):
    """Process a message, searching for the actual URL first"""

    message = db.query(Message).filter(Message.id == message_id).first()
    if not message:
        print(f"Message {message_id} not found")
        return None

    print(f"\n{'='*60}")
    print(f"Message ID: {message.id}")
    print(f"Content: {message.raw_content[:200]}...")
    print(f"{'='*60}\n")

    # Step 1: Search for the actual URL
    print("Searching for actual URL...")
    url_result = await search_for_source_url(message.raw_content)

    found_url = None
    if url_result and url_result.get("found_url"):
        found_url = url_result.get("found_url")
        print(f"  Found URL: {found_url}")
        print(f"  Source: {url_result.get('source_name')}")
        print(f"  Confidence: {url_result.get('confidence')}")
    else:
        print("  No URL found via search")

    # Step 2: Process the content
    categories = db.query(Category).filter(Category.user_id == 1).all()
    category_list = "\n".join([f"- {cat.name}: {cat.description}" for cat in categories])

    prompt = f"""Analyze this governance content for The Grant Wire.

## Content
{message.raw_content}

## Tasks
1. Write a clean 3-4 sentence summary (factual, no fluff, never say "blockchain")
2. Classify into ONE category:
{category_list}
3. Extract entities (protocols, amounts, deadlines, key terms)
4. Identify source info (platform name, type, original date from content)

## Response (JSON only)
```json
{{
  "summary": "3-4 sentence summary",
  "category": "exact category name",
  "confidence": "confident or uncertain",
  "source": {{
    "name": "platform name",
    "type": "forum/governance/other",
    "original_date": "YYYY-MM-DD or null"
  }},
  "entities": {{
    "protocols": [],
    "amounts": [],
    "deadlines": [],
    "key_terms": []
  }}
}}
```"""

    headers = {
        "Authorization": f"Bearer {settings.grok_api_key}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": settings.grok_model,
        "messages": [
            {"role": "system", "content": "Content analyst for The Grant Wire. Respond with valid JSON only."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.3,
        "max_tokens": 800
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{settings.grok_api_url}/chat/completions",
            headers=headers,
            json=payload
        )

        if response.status_code == 200:
            result = response.json()
            content = result["choices"][0]["message"]["content"]

            try:
                if "```json" in content:
                    start = content.find("```json") + 7
                    end = content.find("```", start)
                    content = content[start:end].strip()
                elif "```" in content:
                    start = content.find("```") + 3
                    end = content.find("```", start)
                    content = content[start:end].strip()

                parsed = json.loads(content)

                # Add the found URL to source
                if found_url:
                    parsed["source"]["url"] = found_url
                else:
                    parsed["source"]["url"] = None
                    parsed["source"]["note"] = "Original link unavailable"

                print("\nResults:")
                print(f"  Summary: {parsed.get('summary')[:100]}...")
                print(f"  Category: {parsed.get('category')}")
                print(f"  URL: {parsed.get('source', {}).get('url')}")

                return parsed

            except json.JSONDecodeError as e:
                print(f"Parse error: {e}")
                return None

    return None

async def main():
    database.get_engine()
    db = database.SessionLocal()

    try:
        # Test with messages that have no URLs
        test_ids = [190, 192, 195]

        for msg_id in test_ids:
            result = await process_message_with_url_search(msg_id, db)
            print("\n" + "-"*60 + "\n")
            await asyncio.sleep(2)  # Rate limit between API calls

    finally:
        db.close()

if __name__ == "__main__":
    asyncio.run(main())
