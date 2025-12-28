#!/usr/bin/env python3
"""
Backfill source URLs for entries that don't have them.
These entries came from x23's summaries of discourse forum posts.
This script attempts to find the original forum posts.
"""
import asyncio
import httpx
import re
import sys
import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
from urllib.parse import quote

load_dotenv()

# Known discourse forum URLs for major protocols
FORUM_URLS = {
    'Arbitrum': 'https://forum.arbitrum.foundation',
    'ArbitrumDAO': 'https://forum.arbitrum.foundation',
    'Optimism': 'https://gov.optimism.io',
    'MakerDAO': 'https://forum.makerdao.com',
    'Maker': 'https://forum.makerdao.com',
    'Spark': 'https://forum.makerdao.com',  # Spark is part of Maker ecosystem
    'Balancer': 'https://forum.balancer.fi',
    'Gitcoin': 'https://gov.gitcoin.co',
    'ENS': 'https://discuss.ens.domains',
    'Uniswap': 'https://gov.uniswap.org',
    'Gnosis': 'https://forum.gnosis.io',
    'GnosisDAO': 'https://forum.gnosis.io',
    'Aave': 'https://governance.aave.com',
    'Compound': 'https://www.comp.xyz',
    'Lido': 'https://research.lido.fi',
    'LIDO': 'https://research.lido.fi',
    'LEGO': 'https://research.lido.fi',  # LEGO is Lido Ecosystem Grants Org
    'LidoDAO': 'https://research.lido.fi',
    'Scroll': 'https://forum.scroll.io',
    'Safe': 'https://forum.safe.global',
    'SafeDAO': 'https://forum.safe.global',
    'Reserve': 'https://forum.reserve.org',
    'Cosmos': 'https://forum.cosmos.network',
    'dYdX': 'https://dydx.forum',
    'Curve': 'https://gov.curve.fi',
    'Frax': 'https://gov.frax.finance',
    'Morpho': 'https://forum.morpho.org',
    'Nouns': 'https://discourse.nouns.wtf',
    'Venus': 'https://community.venus.io',
    'Aavegotchi': 'https://dao.aavegotchi.com',
    'Starknet': 'https://community.starknet.io',
    'zkSync': 'https://forum.zknation.io',
    'ZKSync': 'https://forum.zknation.io',
    'Polygon': 'https://forum.polygon.technology',
    'TON': 'https://github.com/ton-society/grants-and-bounties',  # TON uses GitHub
    '1inch': 'https://gov.1inch.io',
    'Synthetix': 'https://governance.synthetix.io',
    'Yearn': 'https://gov.yearn.fi',
    'Sushi': 'https://forum.sushi.com',
    'Hop': 'https://forum.hop.exchange',
    'Rocket Pool': 'https://dao.rocketpool.net',
    'RocketPool': 'https://dao.rocketpool.net',
    'The Graph': 'https://forum.thegraph.com',
    'Graph': 'https://forum.thegraph.com',
    'CoW': 'https://forum.cow.fi',
    'CoW DAO': 'https://forum.cow.fi',
    'Aavegotchi': 'https://dao.aavegotchi.com',
    'Gotchi': 'https://dao.aavegotchi.com',
    # Additional protocols
    'GMX': 'https://gov.gmx.io',
    'Superfluid': 'https://forum.superfluid.org',
    'Aura': 'https://forum.aura.finance',
    'Radiant': 'https://community.radiant.capital',
    'Pendle': 'https://gov.pendle.finance',
    'Euler': 'https://forum.euler.finance',
    'Instadapp': 'https://forum.instadapp.io',
    'Angle': 'https://gov.angle.money',
    'Beefy': 'https://forum.beefy.finance',
    'Paladin': 'https://gov.paladin.vote',
    'Moonwell': 'https://forum.moonwell.fi',
    'Celestia': 'https://forum.celestia.org',
    'Spectra': 'https://gov.spectra.finance',
    'Flashbots': 'https://collective.flashbots.net',
    'Celo': 'https://forum.celo.org',
}

# Protocols that don't have Discourse forums - source URLs cannot be backfilled
NO_FORUM_PROTOCOLS = [
    'Jupiter',  # Uses their own platform (jup.ag/vote)
    'JupDAO',
    'Drift',    # Uses Realms/Solana governance
    'Treehouse',
    'Ethproofs',
]


def identify_protocol(title: str, content: str) -> str:
    """Identify the protocol from title or content.

    Strategy: Check title first (more reliable), then content.
    Use word boundaries for short names to avoid false matches.
    """
    import re
    title_lower = title.lower() if title else ''
    content_lower = content[:500].lower() if content else ''

    # Priority order - check more specific/longer names first to avoid false matches
    # Note: We check title first, then content, to prioritize explicit mentions
    priority_order = [
        # Multi-word names first
        'ArbitrumDAO', 'Arbitrum',
        'GnosisDAO', 'Gnosis',
        'MakerDAO', 'Maker',
        'RocketPool', 'Rocket Pool',
        'The Graph',
        'CoW DAO', 'CoW',
        'Aavegotchi', 'Gotchi',
        'Superfluid',
        'Instadapp',
        'LidoDAO',
        'SafeDAO',
        'Flashbots',
        'Celestia',
        'Moonwell',
        'Paladin',
        'Spectra',
        'Optimism',
        'Balancer',
        'Gitcoin',
        'Uniswap',
        'Compound',
        'Morpho',
        'zkSync', 'ZKSync',
        'Starknet',
        'Polygon',
        'Synthetix',
        'Jupiter', 'JupDAO',  # No forum but we identify for marking
        'Drift',  # No forum
        'Treehouse',  # No forum
        'Ethproofs',  # No forum
        'Reserve',
        'Cosmos',
        'Venus',
        'Scroll',
        'Curve',
        'Yearn',
        'Sushi',
        'Frax',
        'Safe',
        'dYdX',
        'Lido', 'LIDO', 'LEGO',
        'Aave',
        '1inch',
        'Hop',
        'TON',
        'Nouns',
        'Euler',
        'Angle',
        'Beefy',
        'Radiant',
        'Pendle',
        'Aura',
        'GMX',
        'Celo',  # Celo ecosystem
        'Graph',  # After "The Graph" to avoid matching "graph" loosely
        'ENS',  # ENS last because it can match inside words
    ]

    # Protocols that need word boundary matching (short names prone to false matches)
    word_boundary_protocols = {'ENS', 'GMX', 'CoW', 'TON', 'Hop', 'Aave', 'Safe', 'dYdX',
                               'Lido', 'LIDO', 'LEGO', '1inch', 'Frax', 'Aura', 'Graph'}

    def check_protocol(protocol: str, text: str) -> bool:
        """Check if protocol is mentioned in text with appropriate matching."""
        if protocol in FORUM_URLS or protocol in NO_FORUM_PROTOCOLS:
            if protocol in word_boundary_protocols or len(protocol) <= 4:
                pattern = rf'\b{re.escape(protocol.lower())}\b'
                return bool(re.search(pattern, text))
            else:
                return protocol.lower() in text
        return False

    # FIRST: Check title only (more reliable for identifying the main subject)
    for protocol in priority_order:
        if check_protocol(protocol, title_lower):
            return protocol

    # SECOND: Check for protocol-specific keyword patterns in title
    # These catch entries that don't explicitly name the protocol

    # Optimism-related keywords (Retro Funding, RPGF, etc.)
    optimism_patterns = [
        r'\bretro\s*funding\b', r'\brpgf\d*\b', r'\bsuperchain\b',
        r'\bop\s+budget\b', r'\bop\s+grant', r'\bop\s+funding',
        r'\bop\s+stablecoin\b', r'\bop\s+tech\b',  # OP Stablecoin, OP Tech
        r'\bop\s+\d+k\b',  # "OP 60k" etc
        r'\b\d+k?\s+op\b',  # "200K OP"
        r'\b\d+\.?\d*m?\s+op\b',  # "1.86M OP"
        r'\bgrants\s+council\b',  # Grants Council is Optimism-specific
        r'\bseason\s+\d+\b.*\bgrant',  # Season X grants
        r'\bm&m\s+council\b', r'\bmilestones?\s+and\s+metrics\b',  # M&M Council
        r'\bmilestones?\s+council\b',  # Milestones Council
        r'\bmid-season\s+grants\b',  # Mid-Season Grants Report
    ]
    for pattern in optimism_patterns:
        if re.search(pattern, title_lower):
            return 'Optimism'

    # Arbitrum-related keywords
    arbitrum_patterns = [
        r'\bstip\b', r'\bstip\.b\b',  # STIP grants program
        r'\bstylus\b',  # Stylus Sprint
        r'\bseedgov\b',  # SEEDGov delegate
        r'\bavi\s+proposal\b', r'\bavi\s+budget\b',  # AVI proposals
        r'\barb\s+\d+[km]?\b', r'\b\d+[km]?\s+arb\b',  # ARB token amounts
    ]
    for pattern in arbitrum_patterns:
        if re.search(pattern, title_lower):
            return 'Arbitrum'

    # Balancer-related keywords
    balancer_patterns = [
        r'\bbip-\d+\b',  # BIP proposals (BIP-787, etc.)
        r'\bmaxis\b',  # Balancer Maxis
        r'\bbal\s+\d+[km]?\b', r'\b\d+[km]?\s+bal\b',  # BAL token amounts
        r'\bmaxis\s+multisig\b',  # Maxis Multisig
    ]
    for pattern in balancer_patterns:
        if re.search(pattern, title_lower):
            return 'Balancer'

    # Gitcoin-related keywords
    gitcoin_patterns = [
        r'\bgtc\b',  # GTC token
        r'\bgg\d+\b',  # GG22, GG21 etc (Gitcoin Grants rounds)
        r'\bgitcoin\s+grants?\b',
        r'\bgrc\b',  # GRC (Gitcoin Grants Review Committee)
        r'\boctant\b',  # Octant is Gitcoin ecosystem
    ]
    for pattern in gitcoin_patterns:
        if re.search(pattern, title_lower):
            return 'Gitcoin'

    # Ethereum-related keywords (use Flashbots forum for general Ethereum topics)
    ethereum_patterns = [
        r'\bpectra\b',  # Pectra upgrade
        r'\bepf\d*\b',  # EPF (Ethereum Protocol Fellowship)
        r'\bbeacon\s+chain\b',
        r'\beth\.limo\b',
        r'\bdevconnect\b',
        r'\beip-\d+\b',  # EIP proposals
        r'\berc-\d+\b',  # ERC standards
    ]
    for pattern in ethereum_patterns:
        if re.search(pattern, title_lower):
            return 'Flashbots'  # Use Flashbots forum for Ethereum topics

    # Paladin-related keywords
    paladin_patterns = [
        r'\bquest\s+board\b', r'\bpaladin\s+quest\b',
        r'\bpal\s+grant\b', r'\b\d+[km]?\s+pal\b',  # PAL token amounts
    ]
    for pattern in paladin_patterns:
        if re.search(pattern, title_lower):
            return 'Paladin'

    # CowDAO-related keywords
    cow_patterns = [
        r'\bcowdao\b', r'\bcowswap\b',
        r'\bcow\s+protocol\b',
    ]
    for pattern in cow_patterns:
        if re.search(pattern, title_lower):
            return 'CoW'

    # Gnosis-related keywords
    gnosis_patterns = [
        r'\bxdai\b',  # xDAI
        r'\bgip-\d+\b',  # GIP proposals
        r'\bgno\s+\d+[km]?\b', r'\b\d+[km]?\s+gno\b',  # GNO token amounts
    ]
    for pattern in gnosis_patterns:
        if re.search(pattern, title_lower):
            return 'Gnosis'

    # RocketPool-related keywords
    rocketpool_patterns = [
        r'\bgmc\b',  # GMC (Grants Management Committee)
        r'\breth\b',  # rETH token
        r'\brpl\s+\d+[km]?\b', r'\b\d+[km]?\s+rpl\b',  # RPL token amounts
    ]
    for pattern in rocketpool_patterns:
        if re.search(pattern, title_lower):
            return 'RocketPool'

    # Celo-related keywords
    celo_patterns = [
        r'\bclabs\b', r'\bcelo\b',
    ]
    for pattern in celo_patterns:
        if re.search(pattern, title_lower):
            return 'Celo'

    # Jupiter-related keywords (no forum)
    jupiter_patterns = [
        r'\bjup\s+dao\b', r'\bjupiter\b', r'\bxjup\b',
        r'\bjup\s+collab\b',
    ]
    for pattern in jupiter_patterns:
        if re.search(pattern, title_lower):
            return 'Jupiter'

    # THIRD: Check content, but only if title didn't match
    for protocol in priority_order:
        if check_protocol(protocol, content_lower):
            return protocol

    return None


def extract_search_terms(title: str, content: str) -> str:
    """Extract good search terms from the title/content."""
    # Use the title primarily
    search_text = title

    # Remove protocol names and common prefixes that don't help search
    for word in ['proposal', 'proposes', 'proposed', 'announces', 'launches', 'seeks', 'requests', 'dao']:
        search_text = re.sub(rf'\b{word}\b', '', search_text, flags=re.IGNORECASE)

    # Remove special characters like $ that mess up search
    search_text = re.sub(r'[$#@%&*]', '', search_text)

    # Remove common suffixes
    search_text = re.sub(r'\bDAO\b', '', search_text, flags=re.IGNORECASE)

    # Clean up extra whitespace
    search_text = ' '.join(search_text.split())

    # Take first 4-5 meaningful words for better matching
    words = search_text.split()[:5]
    return ' '.join(words)


async def search_discourse_forum(forum_url: str, search_query: str) -> list:
    """Search a Discourse forum for matching topics."""
    search_url = f"{forum_url}/search.json"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                search_url,
                params={'q': search_query},
                headers={'Accept': 'application/json'}
            )

            if response.status_code == 200:
                data = response.json()
                topics = data.get('topics', [])
                return [
                    {
                        'id': t.get('id'),
                        'title': t.get('title'),
                        'slug': t.get('slug'),
                        'url': f"{forum_url}/t/{t.get('slug')}/{t.get('id')}"
                    }
                    for t in topics[:5]  # Return top 5 matches
                ]
            else:
                return []
    except Exception as e:
        print(f"  Error searching {forum_url}: {e}")
        return []


def extract_alternative_search_terms(title: str, content: str) -> list:
    """Generate multiple search query variations to improve matching."""
    queries = []

    # Strategy 1: Full title (cleaned)
    clean_title = re.sub(r'[$#@%&*]', '', title)
    clean_title = re.sub(r'\bDAO\b', '', clean_title, flags=re.IGNORECASE)
    queries.append(' '.join(clean_title.split()[:6]))

    # Strategy 2: Key nouns from title (remove common words)
    words = title.split()
    key_words = [w for w in words if len(w) > 3 and w.lower() not in
                 ['proposal', 'proposes', 'proposed', 'announces', 'launches',
                  'seeks', 'requests', 'with', 'from', 'that', 'this', 'the',
                  'and', 'for', 'dao', 'grant', 'grants', 'funding']][:4]
    if key_words:
        queries.append(' '.join(key_words))

    # Strategy 3: Extract amounts or specific terms
    amount_match = re.search(r'\$?\d+[KMB]?\b|\d+,\d+', title)
    if amount_match:
        # Search with the amount
        queries.append(f"{amount_match.group()} {' '.join(key_words[:2])}")

    # Strategy 4: Names/usernames (often have dots or underscores)
    name_match = re.search(r'\b[\w]+\.[\w]+\b|\b[\w]+_[\w]+\b', title)
    if name_match:
        queries.append(name_match.group())

    return queries


async def find_source_url(summary_id: int, title: str, content: str) -> dict:
    """Try to find the source URL for an entry using multiple search strategies."""
    protocol = identify_protocol(title, content)

    if not protocol:
        return {'id': summary_id, 'protocol': None, 'url': None, 'reason': 'Unknown protocol'}

    # Check if protocol has no known forum
    if protocol in NO_FORUM_PROTOCOLS:
        return {'id': summary_id, 'protocol': protocol, 'url': None, 'reason': 'No forum exists'}

    if protocol not in FORUM_URLS:
        return {'id': summary_id, 'protocol': protocol, 'url': None, 'reason': 'Forum not configured'}

    forum_url = FORUM_URLS[protocol]

    # Try multiple search strategies
    search_queries = [extract_search_terms(title, content)]
    search_queries.extend(extract_alternative_search_terms(title, content))

    # Remove duplicates and empty queries
    seen = set()
    unique_queries = []
    for q in search_queries:
        q = q.strip()
        if q and q not in seen:
            seen.add(q)
            unique_queries.append(q)

    for search_terms in unique_queries[:3]:  # Try up to 3 different queries
        print(f"  Searching {protocol} forum for: {search_terms[:50]}...")

        results = await search_discourse_forum(forum_url, search_terms)

        if results:
            # Return the best match
            best = results[0]
            return {
                'id': summary_id,
                'protocol': protocol,
                'url': best['url'],
                'matched_title': best['title'],
                'reason': 'Found'
            }

        # Small delay between search attempts
        await asyncio.sleep(0.2)

    return {'id': summary_id, 'protocol': protocol, 'url': None, 'reason': 'No matches found'}


def get_x23_entries_without_source_urls(limit: int = 100):
    """Get x23 entries that don't have source URLs in entities."""
    engine = create_engine(os.getenv('DATABASE_URL'))
    try:
        with engine.connect() as conn:
            result = conn.execute(text(f'''
                SELECT s.id, s.title, m.raw_content
                FROM messages m
                JOIN summaries s ON s.message_id = m.id
                WHERE m.import_method = 'x23'
                  AND (s.entities->'source'->>'url' IS NULL
                       OR s.entities->'source'->>'url' = 'null')
                ORDER BY s.id
                LIMIT {limit}
            ''')).fetchall()
            return result
    finally:
        engine.dispose()


def get_entries_without_urls(limit: int = 100):
    """Get entries that don't have source URLs (legacy, for backwards compat)."""
    return get_x23_entries_without_source_urls(limit)


def get_unavailable_entries(limit: int = 500):
    """Get x23 entries that were marked as unavailable (for retry with improved logic)."""
    engine = create_engine(os.getenv('DATABASE_URL'))
    try:
        with engine.connect() as conn:
            result = conn.execute(text(f'''
                SELECT s.id, s.title, m.raw_content
                FROM messages m
                JOIN summaries s ON s.message_id = m.id
                WHERE m.import_method = 'x23'
                  AND s.entities->'source'->>'url' = 'unavailable'
                ORDER BY s.id
                LIMIT {limit}
            ''')).fetchall()
            return result
    finally:
        engine.dispose()


def reset_unavailable_entries():
    """Reset all unavailable entries to NULL so they can be retried."""
    import json
    engine = create_engine(os.getenv('DATABASE_URL'))
    try:
        with engine.connect() as conn:
            # Get count first
            count = conn.execute(text('''
                SELECT COUNT(*)
                FROM messages m
                JOIN summaries s ON s.message_id = m.id
                WHERE m.import_method = 'x23'
                  AND s.entities->'source'->>'url' = 'unavailable'
            ''')).fetchone()[0]

            # Update all unavailable entries - remove the source.url field
            result = conn.execute(text('''
                UPDATE summaries s
                SET entities = entities - 'source'
                FROM messages m
                WHERE s.message_id = m.id
                  AND m.import_method = 'x23'
                  AND s.entities->'source'->>'url' = 'unavailable'
            '''))
            conn.commit()
            print(f"Reset {count} unavailable entries for retry")
            return count
    finally:
        engine.dispose()


def update_source_url(summary_id: int, url: str):
    """Update the entities.source.url for a summary."""
    import json
    engine = create_engine(os.getenv('DATABASE_URL'))
    try:
        with engine.connect() as conn:
            # Get current entities
            result = conn.execute(text(
                'SELECT entities FROM summaries WHERE id = :id'
            ), {'id': summary_id}).fetchone()

            entities = result[0] if result and result[0] else {}

            # Update source.url
            if 'source' not in entities:
                entities['source'] = {}
            entities['source']['url'] = url

            # Also add to urls list
            if 'urls' not in entities:
                entities['urls'] = []
            if url not in entities['urls']:
                entities['urls'].append(url)

            # Save back using parameterized query to handle special chars
            entities_json = json.dumps(entities)
            conn.execute(text(
                'UPDATE summaries SET entities = CAST(:entities AS json) WHERE id = :id'
            ), {'entities': entities_json, 'id': summary_id})
            conn.commit()
            return True
    except Exception as e:
        print(f"  Error updating {summary_id}: {e}")
        return False
    finally:
        engine.dispose()


def mark_source_unavailable(summary_id: int, protocol: str, reason: str):
    """Mark an entry as having no available source URL (x23 legacy data)."""
    import json
    engine = create_engine(os.getenv('DATABASE_URL'))
    try:
        with engine.connect() as conn:
            # Get current entities
            result = conn.execute(text(
                'SELECT entities FROM summaries WHERE id = :id'
            ), {'id': summary_id}).fetchone()

            entities = result[0] if result and result[0] else {}

            # Mark source as unavailable with reason
            if 'source' not in entities:
                entities['source'] = {}
            entities['source']['url'] = 'unavailable'
            entities['source']['unavailable_reason'] = reason
            if protocol:
                entities['source']['protocol'] = protocol

            # Save back using parameterized query to handle special chars
            entities_json = json.dumps(entities)
            conn.execute(text(
                'UPDATE summaries SET entities = CAST(:entities AS json) WHERE id = :id'
            ), {'entities': entities_json, 'id': summary_id})
            conn.commit()
            return True
    except Exception as e:
        print(f"  Error marking {summary_id}: {e}")
        return False
    finally:
        engine.dispose()


async def backfill_batch(limit: int = 50, mark_unavailable: bool = False):
    """Find and backfill source URLs for entries.

    Args:
        limit: Max entries to process
        mark_unavailable: If True, mark entries without found URLs as 'unavailable'
    """
    entries = get_entries_without_urls(limit)

    print(f"\n{'='*60}")
    print(f"Processing {len(entries)} entries without URLs")
    if mark_unavailable:
        print(f"Mode: Mark unfound entries as 'unavailable'")
    print(f"{'='*60}\n")

    found = 0
    not_found = 0
    no_forum = 0
    unknown_protocol = 0
    marked_unavailable = 0

    for row in entries:
        summary_id, title, content = row
        print(f"ID {summary_id}: {title[:50]}...")

        result = await find_source_url(summary_id, title, content)

        if result['url']:
            print(f"  ✓ Found: {result['url']}")
            update_source_url(summary_id, result['url'])
            found += 1
        elif result['reason'] == 'No forum exists':
            print(f"  ⊘ No forum for {result['protocol']}")
            no_forum += 1
            if mark_unavailable:
                mark_source_unavailable(summary_id, result['protocol'], 'no_forum')
                marked_unavailable += 1
        elif result['reason'] == 'Unknown protocol':
            print(f"  ? Unknown protocol")
            unknown_protocol += 1
            if mark_unavailable:
                mark_source_unavailable(summary_id, None, 'unknown_protocol')
                marked_unavailable += 1
        else:
            print(f"  ✗ Not found ({result['protocol']})")
            not_found += 1
            if mark_unavailable:
                mark_source_unavailable(summary_id, result['protocol'], 'search_failed')
                marked_unavailable += 1

        # Rate limit
        await asyncio.sleep(0.5)

    print(f"\n{'='*60}")
    print(f"Results: {found} found, {not_found} not found, {no_forum} no forum, {unknown_protocol} unknown protocol")
    if mark_unavailable:
        print(f"Marked {marked_unavailable} as unavailable")
    print(f"{'='*60}")


def get_stats():
    """Get current stats on URL coverage."""
    engine = create_engine(os.getenv('DATABASE_URL'))
    try:
        with engine.connect() as conn:
            total = conn.execute(text('SELECT COUNT(*) FROM summaries')).fetchone()[0]

            # x23 entries stats
            x23_total = conn.execute(text('''
                SELECT COUNT(*)
                FROM messages m
                JOIN summaries s ON s.message_id = m.id
                WHERE m.import_method = 'x23'
            ''')).fetchone()[0]

            x23_with_source_url = conn.execute(text('''
                SELECT COUNT(*)
                FROM messages m
                JOIN summaries s ON s.message_id = m.id
                WHERE m.import_method = 'x23'
                  AND s.entities->'source'->>'url' IS NOT NULL
                  AND s.entities->'source'->>'url' != 'null'
                  AND s.entities->'source'->>'url' != 'unavailable'
            ''')).fetchone()[0]

            x23_marked_unavailable = conn.execute(text('''
                SELECT COUNT(*)
                FROM messages m
                JOIN summaries s ON s.message_id = m.id
                WHERE m.import_method = 'x23'
                  AND s.entities->'source'->>'url' = 'unavailable'
            ''')).fetchone()[0]

            # Phone import stats
            phone_total = conn.execute(text('''
                SELECT COUNT(*)
                FROM messages m
                WHERE m.import_method LIKE 'phone%'
            ''')).fetchone()[0]

            remaining = x23_total - x23_with_source_url - x23_marked_unavailable

            print(f"\nURL Coverage Stats:")
            print(f"  Total entries: {total}")
            print(f"  ")
            print(f"  x23 entries: {x23_total}")
            print(f"    With backfilled source URLs: {x23_with_source_url}")
            print(f"    Marked as unavailable: {x23_marked_unavailable}")
            print(f"    Remaining to process: {remaining}")
            print(f"  ")
            print(f"  Phone import entries: {phone_total}")
            print(f"    (these already have URLs from messages)")
    finally:
        engine.dispose()


async def retry_unavailable_batch(limit: int = 500, mark_unavailable: bool = True):
    """Retry unavailable entries with improved protocol detection."""
    entries = get_unavailable_entries(limit)

    print(f"\n{'='*60}")
    print(f"Retrying {len(entries)} unavailable entries with improved logic")
    print(f"{'='*60}\n")

    found = 0
    not_found = 0
    no_forum = 0
    unknown_protocol = 0
    marked_unavailable = 0

    for row in entries:
        summary_id, title, content = row
        print(f"ID {summary_id}: {title[:50]}...")

        result = await find_source_url(summary_id, title, content)

        if result['url']:
            print(f"  ✓ Found: {result['url']}")
            update_source_url(summary_id, result['url'])
            found += 1
        elif result['reason'] == 'No forum exists':
            print(f"  ⊘ No forum for {result['protocol']}")
            no_forum += 1
            # Already marked, no need to re-mark
        elif result['reason'] == 'Unknown protocol':
            print(f"  ? Unknown protocol")
            unknown_protocol += 1
        else:
            print(f"  ✗ Not found ({result['protocol']})")
            not_found += 1

        # Rate limit
        await asyncio.sleep(0.5)

    print(f"\n{'='*60}")
    print(f"Results: {found} found, {not_found} not found, {no_forum} no forum, {unknown_protocol} unknown protocol")
    print(f"{'='*60}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 backfill_source_urls.py stats")
        print("  python3 backfill_source_urls.py run [limit]")
        print("  python3 backfill_source_urls.py finalize [limit]   # Run + mark unfound as unavailable")
        print("  python3 backfill_source_urls.py retry [limit]      # Retry unavailable entries with improved logic")
        print("  python3 backfill_source_urls.py reset              # Reset unavailable entries for fresh retry")
        print("  python3 backfill_source_urls.py test <summary_id>")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "stats":
        get_stats()
    elif cmd == "run":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 50
        asyncio.run(backfill_batch(limit, mark_unavailable=False))
    elif cmd == "finalize":
        # Final pass: try to find URLs, mark remaining as unavailable
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 500
        print("FINALIZE MODE: Will mark unfound entries as 'unavailable'")
        asyncio.run(backfill_batch(limit, mark_unavailable=True))
    elif cmd == "retry":
        # Retry unavailable entries with improved protocol detection
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 500
        print("RETRY MODE: Retrying unavailable entries with improved logic")
        asyncio.run(retry_unavailable_batch(limit))
    elif cmd == "reset":
        # Reset unavailable entries so they can be retried
        reset_unavailable_entries()
    elif cmd == "test":
        if len(sys.argv) < 3:
            print("Usage: python3 backfill_source_urls.py test <summary_id>")
            sys.exit(1)
        summary_id = int(sys.argv[2])
        entries = get_entries_without_urls(1000)
        entry = next((e for e in entries if e[0] == summary_id), None)
        if entry:
            result = asyncio.run(find_source_url(entry[0], entry[1], entry[2]))
            print(f"Result: {result}")
        else:
            # Also check unavailable entries
            entries = get_unavailable_entries(1000)
            entry = next((e for e in entries if e[0] == summary_id), None)
            if entry:
                result = asyncio.run(find_source_url(entry[0], entry[1], entry[2]))
                print(f"Result: {result}")
            else:
                print(f"Entry {summary_id} not found or already has URL")
    else:
        print(f"Unknown command: {cmd}")
