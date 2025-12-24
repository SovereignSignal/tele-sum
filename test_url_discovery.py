"""Process messages without URLs - extract info but don't fabricate URLs"""
import asyncio
import httpx
import json
import sys
sys.path.insert(0, '~/Documents/GitHub/tele-sum')

from src.models import database
from src.models.models import Message, Summary, Category, ProcessingStatus, ConfidenceLevel
from src.config import settings

async def process_no_url_message(message_id: int, db):
    """Process a message that has no extracted URLs"""

    message = db.query(Message).filter(Message.id == message_id).first()
    if not message:
        print(f"Message {message_id} not found")
        return None

    print(f"\n{'='*60}")
    print(f"Message ID: {message.id}")
    print(f"Content: {message.raw_content[:200]}...")
    print(f"{'='*60}\n")

    categories = db.query(Category).filter(Category.user_id == 1).all()
    category_list = "\n".join([f"- {cat.name}: {cat.description}" for cat in categories])

    # Prompt that doesn't ask for URL construction
    prompt = f"""You are analyzing content for The Grant Wire, a crypto grants and funding news aggregator.

This content was pre-summarized from an external source. The original link is no longer available.

## Content
{message.raw_content}

## Your Tasks

1. **Summary**: Rewrite this as a clean 3-4 sentence summary:
   - Focus on substance: what is announced, proposed, or reported
   - Include specific numbers, amounts, percentages, requirements
   - Concise and factual - no fluff or flowery language
   - NEVER use "blockchain" - use "crypto", "onchain", or specific chain names

2. **Source Info**: Identify the original source platform:
   - What protocol's governance forum did this come from?
   - What type of source (forum, governance, etc.)
   - What was the original date from the content?
   - DO NOT make up or guess URLs

3. **Classification**: Categorize into ONE of these:
{category_list}

4. **Entities**: Extract protocols, amounts, deadlines, key terms

## Response Format
Respond ONLY with valid JSON:
```json
{{
  "summary": "Your 3-4 sentence summary",
  "category": "MUST match one category name exactly",
  "confidence": "confident OR uncertain",
  "source": {{
    "name": "Platform name (e.g., 'Arbitrum Governance Forum')",
    "type": "forum OR governance OR other",
    "original_date": "YYYY-MM-DD if mentioned in content, otherwise null",
    "url": null
  }},
  "entities": {{
    "protocols": ["protocols mentioned"],
    "amounts": ["funding amounts"],
    "deadlines": ["specific dates"],
    "key_terms": ["important terms"]
  }}
}}
```

IMPORTANT: Set url to null - do not fabricate URLs."""

    headers = {
        "Authorization": f"Bearer {settings.grok_api_key}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": settings.grok_model,
        "messages": [
            {
                "role": "system",
                "content": "You are a content analyst for The Grant Wire. Summarize governance and grants content accurately. Never fabricate URLs. Always respond with valid JSON only."
            },
            {
                "role": "user",
                "content": prompt
            }
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

                print("Results:")
                print(f"  Summary: {parsed.get('summary')}")
                print(f"  Category: {parsed.get('category')}")
                print(f"  Source: {parsed.get('source', {}).get('name')}")
                print(f"  Date: {parsed.get('source', {}).get('original_date')}")
                print(f"  Protocols: {parsed.get('entities', {}).get('protocols')}")
                print(f"  Amounts: {parsed.get('entities', {}).get('amounts')}")

                return parsed

            except json.JSONDecodeError as e:
                print(f"Failed to parse: {e}")
                print(f"Raw: {content}")
                return None
        else:
            print(f"API Error: {response.status_code} - {response.text}")
            return None

async def process_and_save(message_id: int):
    """Process a message and save to database"""
    database.get_engine()
    db = database.SessionLocal()

    try:
        result = await process_no_url_message(message_id, db)

        if result:
            message = db.query(Message).filter(Message.id == message_id).first()

            if not message.summary:
                summary = Summary(
                    message_id=message.id,
                    processing_status=ProcessingStatus.PENDING
                )
                db.add(summary)
                db.commit()
            else:
                summary = message.summary

            summary.summary = result.get("summary")

            entities = result.get("entities", {})
            source = result.get("source", {})
            # Mark that original URL is not available
            source["url"] = None
            source["note"] = "Original source link unavailable (imported from x23.ai)"
            entities["source"] = source
            entities["urls"] = []
            summary.entities = entities

            confidence_str = str(result.get("confidence", "")).lower()
            summary.confidence = ConfidenceLevel.CONFIDENT if confidence_str == "confident" else ConfidenceLevel.UNCERTAIN

            category_name = result.get("category")
            if category_name:
                category = db.query(Category).filter(
                    Category.user_id == 1,
                    Category.name == category_name
                ).first()
                if category:
                    summary.category_id = category.id

            summary.processing_status = ProcessingStatus.SUCCESS
            summary.processing_attempts += 1
            db.commit()

            print(f"\nSaved to database!")
            return True

    finally:
        db.close()

    return False

async def main():
    # Test with messages 189-202 that have no URLs
    test_ids = [190, 192, 195]

    for msg_id in test_ids:
        await process_and_save(msg_id)
        print("\n" + "-"*60 + "\n")
        await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(main())
