#!/usr/bin/env python3
"""
Backfill titles for existing summaries.
Generates 4-5 word titles from existing summary text using Grok.
"""
import asyncio
import httpx
import sys
import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

GROK_API_KEY = os.getenv('GROK_API_KEY')
GROK_API_URL = os.getenv('GROK_API_URL', 'https://api.x.ai/v1')
GROK_MODEL = os.getenv('GROK_MODEL', 'grok-4-fast-reasoning')

async def generate_title(summary_text: str, entities: dict) -> str:
    """Generate a 4-5 word title from a summary using Grok."""
    # Extract helpful context from entities
    protocols = entities.get('protocols', []) if entities else []
    source_name = entities.get('source', {}).get('name', '') if entities else ''

    prompt = f"""Generate a short, descriptive title (4-5 words max) for this content.

Summary: {summary_text}

{f"Protocols mentioned: {', '.join(protocols)}" if protocols else ""}
{f"Source: {source_name}" if source_name else ""}

Rules:
- 4-5 words maximum
- Use title case
- No punctuation at the end
- IMPORTANT: Distinguish between NEWS vs OPINION content:
  - NEWS (official announcements, vote results, grant rounds): Describe the action (e.g., "Optimism Grants Council Results", "Arbitrum Treasury Vote Passes")
  - OPINION/ANALYSIS (someone sharing views, lessons, threads): Credit the source + topic (e.g., "Sovereign Signal on Grant Design", "Polynya Analyzes L2 Economics", "Vitalik on Protocol Governance")
- If the summary mentions "the author" or personal opinions/learnings, this is OPINION content - use the source name in the title

Respond with ONLY the title, nothing else."""

    headers = {
        "Authorization": f"Bearer {GROK_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": GROK_MODEL,
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.3,
        "max_tokens": 50
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{GROK_API_URL}/chat/completions",
                headers=headers,
                json=payload
            )

            if response.status_code == 200:
                result = response.json()
                title = result["choices"][0]["message"]["content"].strip()
                # Clean up any quotes or extra punctuation
                title = title.strip('"\'').strip()
                # Ensure it's not too long
                words = title.split()
                if len(words) > 6:
                    title = ' '.join(words[:5])
                return title
            elif response.status_code == 429:
                print("  Rate limit hit")
                return None
            else:
                print(f"  API error: {response.status_code}")
                return None
    except Exception as e:
        print(f"  Error: {e}")
        return None

async def backfill_batch(start_id: int, end_id: int, chunk_size: int = 25):
    """Backfill titles for a range of summaries."""
    engine = create_engine(os.getenv('DATABASE_URL'))

    print(f"\n{'='*60}")
    print(f"Backfilling titles for IDs {start_id} to {end_id}")
    print(f"{'='*60}\n")

    current_id = start_id
    processed = 0

    while current_id <= end_id:
        # Get summaries without titles
        with engine.connect() as conn:
            result = conn.execute(text(f"""
                SELECT s.id, s.summary, s.entities
                FROM summaries s
                WHERE s.id BETWEEN {current_id} AND {min(current_id + chunk_size - 1, end_id)}
                AND s.summary IS NOT NULL AND s.summary <> ''
                AND (s.title IS NULL OR s.title = '')
                ORDER BY s.id
            """)).fetchall()

            if not result:
                current_id += chunk_size
                continue

            for row in result:
                summary_id, summary_text, entities = row

                print(f"  Processing summary {summary_id}...", end=" ")

                title = await generate_title(summary_text, entities)

                if title:
                    conn.execute(text(f"""
                        UPDATE summaries SET title = :title WHERE id = :id
                    """), {"title": title, "id": summary_id})
                    conn.commit()
                    print(f"'{title}'")
                    processed += 1
                else:
                    print("skipped")

                # Rate limiting
                await asyncio.sleep(1)

        current_id += chunk_size

        # Progress update
        with engine.connect() as conn:
            done = conn.execute(text(f"""
                SELECT COUNT(*) FROM summaries
                WHERE id BETWEEN {start_id} AND {end_id}
                AND title IS NOT NULL AND title <> ''
            """)).fetchone()[0]
            total = conn.execute(text(f"""
                SELECT COUNT(*) FROM summaries
                WHERE id BETWEEN {start_id} AND {end_id}
                AND summary IS NOT NULL AND summary <> ''
            """)).fetchone()[0]
            print(f"\n  Progress: {done}/{total} ({round(done/total*100, 1) if total > 0 else 0}%)\n")

    engine.dispose()
    print(f"\n*** COMPLETE: {processed} titles generated ***")

def get_backfill_stats():
    """Get current backfill status."""
    engine = create_engine(os.getenv('DATABASE_URL'))
    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN summary IS NOT NULL AND summary <> '' THEN 1 ELSE 0 END) as with_summary,
                SUM(CASE WHEN title IS NOT NULL AND title <> '' THEN 1 ELSE 0 END) as with_title
            FROM summaries
        """)).fetchone()

        total, with_summary, with_title = result
        print(f"\nBackfill Status:")
        print(f"  Total summaries: {total}")
        print(f"  With summary text: {with_summary}")
        print(f"  With title: {with_title}")
        print(f"  Need title: {with_summary - with_title}")
    engine.dispose()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 backfill_titles.py status")
        print("  python3 backfill_titles.py <start_id> <end_id> [chunk_size]")
        print("\nExample:")
        print("  python3 backfill_titles.py 1 100 25")
        sys.exit(1)

    if sys.argv[1] == "status":
        get_backfill_stats()
    else:
        start_id = int(sys.argv[1])
        end_id = int(sys.argv[2])
        chunk_size = int(sys.argv[3]) if len(sys.argv) > 3 else 25

        asyncio.run(backfill_batch(start_id, end_id, chunk_size))
