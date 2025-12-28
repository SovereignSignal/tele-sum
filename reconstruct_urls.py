#!/usr/bin/env python3
"""
Reconstruct source URLs from x23.ai URLs.

The x23.ai URLs contain the original forum topic ID and slug, which can be used
to construct the actual forum URL.

Format:
  x23 URL: https://app.x23.ai/{protocol}/discussions/topic/{id}/{slug}
  Forum URL: https://{forum_domain}/t/{slug}/{id}

Run: python3 reconstruct_urls.py [command]

Commands:
  analyze   - Analyze what can be reconstructed
  test      - Test a few reconstructed URLs
  run       - Reconstruct and update URLs for review queue entries
"""
import sys
import os
import re
import asyncio
import aiohttp
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv('DATABASE_URL')

# Forum URL mapping
FORUM_URLS = {
    'arbitrum': 'https://forum.arbitrum.foundation',
    'optimism': 'https://gov.optimism.io',
    'makerdao': 'https://forum.makerdao.com',
    'ens': 'https://discuss.ens.domains',
    'uniswap': 'https://gov.uniswap.org',
    'aave': 'https://governance.aave.com',
    'compound': 'https://www.comp.xyz',
    'lido': 'https://research.lido.fi',
    'gnosis': 'https://forum.gnosis.io',
    'gnosisdao': 'https://forum.gnosis.io',
    'gitcoin': 'https://gov.gitcoin.co',
    'balancer': 'https://forum.balancer.fi',
    'cowdao': 'https://forum.cow.fi',
    'cow': 'https://forum.cow.fi',
    'dydx': 'https://dydx.forum',
    'rocketpool': 'https://dao.rocketpool.net',
    'scroll': 'https://forum.scroll.io',
    'euler': 'https://forum.euler.finance',
    'morpho': 'https://forum.morpho.org',
    'aavegotchi': 'https://dao.aavegotchi.com',
    'gmx': 'https://gov.gmx.io',
    'safedao': 'https://forum.safe.global',
    'aura': 'https://forum.balancer.fi',  # Aura uses Balancer forum
    'instadapp': 'https://forum.instadapp.io',
    'celo': 'https://forum.celo.org',
}


def parse_x23_url(x23_url):
    """Parse x23 URL and extract components."""
    # Discussion format: https://app.x23.ai/{protocol}/discussions/topic/{id}/{slug}
    discussion_match = re.search(
        r'app\.x23\.ai/([^/]+)/discussions/topic/(\d+)/(.+)',
        x23_url
    )
    if discussion_match:
        return {
            'type': 'discussion',
            'protocol': discussion_match.group(1).lower(),
            'topic_id': discussion_match.group(2),
            'slug': discussion_match.group(3)
        }

    # Voting format: https://app.x23.ai/{protocol}/voting/snapshot/{hash}/{slug}
    voting_match = re.search(
        r'app\.x23\.ai/([^/]+)/voting/snapshot/([^/]+)/(.+)',
        x23_url
    )
    if voting_match:
        return {
            'type': 'voting',
            'protocol': voting_match.group(1).lower(),
            'snapshot_hash': voting_match.group(2),
            'slug': voting_match.group(3)
        }

    return None


def reconstruct_url(parsed):
    """Reconstruct the forum URL from parsed x23 data."""
    if parsed['type'] != 'discussion':
        return None  # Can't reconstruct voting URLs automatically

    protocol = parsed['protocol']
    if protocol not in FORUM_URLS:
        return None

    forum_base = FORUM_URLS[protocol]
    return f"{forum_base}/t/{parsed['slug']}/{parsed['topic_id']}"


async def verify_url(session, url):
    """Verify if a URL is accessible."""
    try:
        async with session.head(url, allow_redirects=True, timeout=10) as response:
            return response.status == 200
    except Exception:
        try:
            async with session.get(url, allow_redirects=True, timeout=10) as response:
                return response.status == 200
        except Exception:
            return False


def analyze():
    """Analyze what URLs can be reconstructed."""
    engine = create_engine(DATABASE_URL)

    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT
                rq.id,
                rq.summary_id,
                rq.issue_type,
                rq.protocol,
                rq.our_title,
                rq.matched_url
            FROM review_queue rq
            WHERE rq.status = 'pending'
              AND rq.matched_url IS NOT NULL
              AND rq.matched_url LIKE 'https://app.x23.ai%'
        """)).fetchall()

        can_reconstruct = []
        cannot_reconstruct = []
        voting_type = []

        for row in result:
            x23_url = row[5]
            parsed = parse_x23_url(x23_url)

            if not parsed:
                cannot_reconstruct.append((row, 'Could not parse x23 URL'))
                continue

            if parsed['type'] == 'voting':
                voting_type.append((row, parsed))
                continue

            reconstructed = reconstruct_url(parsed)
            if reconstructed:
                can_reconstruct.append((row, parsed, reconstructed))
            else:
                cannot_reconstruct.append((row, f"No forum mapping for {parsed['protocol']}"))

        print("=" * 70)
        print("URL RECONSTRUCTION ANALYSIS")
        print("=" * 70)
        print(f"\nTotal review queue entries with x23 URLs: {len(result)}")
        print(f"\nCan reconstruct (discussion type): {len(can_reconstruct)}")
        print(f"Cannot reconstruct: {len(cannot_reconstruct)}")
        print(f"Voting/Snapshot type (need manual): {len(voting_type)}")

        print("\n--- CAN RECONSTRUCT (first 10) ---")
        for row, parsed, url in can_reconstruct[:10]:
            print(f"\nID {row[0]}: {row[4][:50]}...")
            print(f"  Protocol: {parsed['protocol']}")
            print(f"  Reconstructed: {url}")

        print("\n--- CANNOT RECONSTRUCT ---")
        for row, reason in cannot_reconstruct[:10]:
            print(f"\nID {row[0]}: {row[4][:50]}...")
            print(f"  Reason: {reason}")

        print("\n--- VOTING TYPE (by protocol) ---")
        protocols = {}
        for row, parsed in voting_type:
            p = parsed['protocol']
            protocols[p] = protocols.get(p, 0) + 1
        for p, count in sorted(protocols.items(), key=lambda x: -x[1]):
            print(f"  {p}: {count}")

    engine.dispose()


async def test_urls():
    """Test a few reconstructed URLs to verify they work."""
    engine = create_engine(DATABASE_URL)

    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT
                rq.id,
                rq.summary_id,
                rq.our_title,
                rq.matched_url
            FROM review_queue rq
            WHERE rq.status = 'pending'
              AND rq.matched_url IS NOT NULL
              AND rq.matched_url LIKE '%/discussions/topic/%'
            LIMIT 20
        """)).fetchall()

    engine.dispose()

    print("Testing reconstructed URLs...")
    print("=" * 70)

    async with aiohttp.ClientSession() as session:
        for row in result:
            x23_url = row[3]
            parsed = parse_x23_url(x23_url)

            if not parsed or parsed['type'] != 'discussion':
                continue

            reconstructed = reconstruct_url(parsed)
            if not reconstructed:
                continue

            is_valid = await verify_url(session, reconstructed)
            status = "✓" if is_valid else "✗"

            print(f"\n{status} ID {row[0]}: {row[2][:40]}...")
            print(f"  URL: {reconstructed}")

            await asyncio.sleep(0.5)  # Rate limit


async def run_reconstruction():
    """Reconstruct URLs and update the database."""
    engine = create_engine(DATABASE_URL)

    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT
                rq.id,
                rq.summary_id,
                rq.our_title,
                rq.matched_url
            FROM review_queue rq
            WHERE rq.status = 'pending'
              AND rq.matched_url IS NOT NULL
              AND rq.matched_url LIKE '%/discussions/topic/%'
        """)).fetchall()

    print(f"Found {len(result)} entries to process")
    print("=" * 70)

    valid_urls = []
    invalid_urls = []

    async with aiohttp.ClientSession() as session:
        for row in result:
            x23_url = row[3]
            parsed = parse_x23_url(x23_url)

            if not parsed or parsed['type'] != 'discussion':
                continue

            reconstructed = reconstruct_url(parsed)
            if not reconstructed:
                print(f"  ⊘ ID {row[0]}: No forum mapping for {parsed['protocol']}")
                continue

            is_valid = await verify_url(session, reconstructed)

            if is_valid:
                valid_urls.append((row[0], row[1], reconstructed))
                print(f"  ✓ ID {row[0]}: {reconstructed}")
            else:
                invalid_urls.append((row[0], reconstructed))
                print(f"  ✗ ID {row[0]}: {reconstructed} (not accessible)")

            await asyncio.sleep(0.3)  # Rate limit

    print()
    print("=" * 70)
    print(f"Valid URLs: {len(valid_urls)}")
    print(f"Invalid URLs: {len(invalid_urls)}")

    if valid_urls:
        print(f"\nUpdating {len(valid_urls)} entries in database...")

        with engine.connect() as conn:
            for review_id, summary_id, url in valid_urls:
                # Update review_queue
                conn.execute(text("""
                    UPDATE review_queue
                    SET corrected_url = :url,
                        status = 'corrected',
                        notes = COALESCE(notes, '') || E'\nReconstructed from x23.ai URL',
                        reviewed_at = NOW()
                    WHERE id = :id
                """), {'url': url, 'id': review_id})

                # Update summaries
                conn.execute(text("""
                    UPDATE summaries
                    SET entities = jsonb_set(
                        COALESCE(entities::jsonb, '{}'::jsonb),
                        '{source,url}',
                        to_jsonb(CAST(:url AS text))
                    )
                    WHERE id = :summary_id
                """), {'url': url, 'summary_id': summary_id})

            conn.commit()

        print("Done!")

    engine.dispose()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "analyze":
        analyze()
    elif cmd == "test":
        asyncio.run(test_urls())
    elif cmd == "run":
        asyncio.run(run_reconstruction())
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
