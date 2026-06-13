"""
Provider-agnostic AI summarization service.

Provider config is resolved in this order:
    1. Env vars (AI_PROVIDER + AI_API_KEY [+ AI_BASE_URL, AI_MODEL])
    2. The api_providers DB table (shared with the sibling app on this DB)

Supported providers (selected by name):
    - anthropic            (native /v1/messages API)
    - ollama               (Ollama Cloud / native /api/chat — base_url ends in /api)
    - any OpenAI-compatible provider (grok, openai, openrouter, perplexity,
      groq, together, ...) — uses /chat/completions

Validation: AI responses are parsed into pydantic AIAnalysis (src/schemas/entities.py).
Telemetry: each call writes one row to api_usage_log (raw SQL — owned by the sibling app).
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.config import settings
from src.models.models import Category
from src.schemas.entities import AIAnalysis, EntitiesPayload, SourceInfo
from src.schemas.digest import DigestAnalysis
from src.utils.url_extractor import is_twitter_url

logger = logging.getLogger(__name__)


@dataclass
class ProviderConfig:
    provider: str           # e.g. "anthropic", "grok", "openai", "perplexity", "ollama"
    api_key: str
    model: str
    base_url: Optional[str] = None  # None for anthropic; required for OpenAI-compat


class _RateLimiter:
    """Hourly request cap. In-memory only; resets on process restart."""

    def __init__(self, max_per_hour: int):
        self.max = max_per_hour
        self.timestamps: List[datetime] = []

    def _prune(self):
        cutoff = datetime.utcnow() - timedelta(hours=1)
        self.timestamps = [t for t in self.timestamps if t > cutoff]

    def can_request(self) -> bool:
        self._prune()
        return len(self.timestamps) < self.max

    def record(self):
        self.timestamps.append(datetime.utcnow())

    def remaining(self) -> int:
        self._prune()
        return max(0, self.max - len(self.timestamps))


def _resolve_config_from_env() -> Optional[ProviderConfig]:
    """Env override path. Returns None if env doesn't fully specify a provider."""
    if not (settings.ai_provider and settings.ai_api_key):
        return None
    return ProviderConfig(
        provider=settings.ai_provider.lower(),
        api_key=settings.ai_api_key,
        model=settings.ai_model or "",
        base_url=settings.ai_base_url,
    )


def _resolve_config_from_db(db: Session, prefer_provider: Optional[str] = None) -> Optional[ProviderConfig]:
    """Read from api_providers table. If prefer_provider is set, look up that row;
    otherwise pick the first enabled non-tool row (excludes firecrawl/resend etc.)."""
    EXCLUDE = {"firecrawl", "resend"}  # not LLMs

    if prefer_provider:
        row = db.execute(
            text("SELECT provider, api_key, base_url, default_model "
                 "FROM api_providers WHERE provider = :p AND enabled = true LIMIT 1"),
            {"p": prefer_provider.lower()},
        ).first()
    else:
        row = db.execute(
            text("SELECT provider, api_key, base_url, default_model "
                 "FROM api_providers WHERE enabled = true AND provider NOT IN :ex "
                 "ORDER BY created_at LIMIT 1"),
            {"ex": tuple(EXCLUDE)},
        ).first()

    if not row:
        return None
    provider, api_key, base_url, default_model = row
    return ProviderConfig(
        provider=provider.lower(),
        api_key=api_key,
        base_url=base_url,
        model=settings.ai_model or default_model or "",
    )


def _build_prompt(
    message_content: str,
    urls: List[str],
    url_contents: Dict[str, str],
    categories: List[Category],
) -> str:
    twitter_urls = [u for u in urls if is_twitter_url(u)]
    other_urls = [u for u in urls if not is_twitter_url(u)]
    has_urls = bool(twitter_urls or other_urls)

    parts: List[str] = []
    if has_urls:
        parts.append(
            "You are analyzing content shared via a Telegram channel. "
            "The Telegram message itself is just a pointer — focus on the LINKED CONTENT, not the wrapper."
        )
        parts.append(f"\n## Telegram Message (context only)\n{message_content}")
    else:
        parts.append(
            "You are analyzing pre-summarized governance/grants content. "
            "The original source link is not available."
        )
        parts.append(f"\n## Content to Analyze\n{message_content}")
        parts.append("\nNOTE: Do NOT fabricate URLs. Set source.url to null when the link is unavailable.")

    if twitter_urls:
        parts.append("\n## Twitter/X Content")
        for url in twitter_urls:
            parts.append(f"- {url}")
            tweet = url_contents.get(url)
            if tweet:
                parts.append(f"  Tweet content:\n{tweet}")
            else:
                parts.append("  (Could not scrape — try reading directly if possible)")
        parts.append("\nIMPORTANT: Base your summary on the ACTUAL tweet content shown above. Do not guess.")

    if other_urls:
        parts.append("\n## Other Links")
        for url in other_urls:
            parts.append(f"- {url}")
            content = url_contents.get(url)
            if content:
                parts.append(f"  Content preview: {content[:2000]}...")

    parts.append("\n## Your Task")
    parts.append("1. **Title**: 4-5 words max, title case, no trailing punctuation. Specific (include protocol/project name when relevant).")
    parts.append("2. **Summary**: 3-4 factual sentences about the LINKED CONTENT. Include specific amounts/dates/percentages. Never use the word 'blockchain' — say 'crypto', 'onchain', or name the chain. Distinguish LAUNCHES vs CLOSURES/RESULTS carefully.")

    if categories:
        parts.append("\n3. **Classification**: Pick exactly ONE of:")
        for cat in categories:
            keywords = f" (keywords: {', '.join(cat.keywords)})" if cat.keywords else ""
            desc = f" — {cat.description}" if cat.description else ""
            parts.append(f"   - **{cat.name}**{desc}{keywords}")
        parts.append("4. **Confidence**: 'confident' or 'uncertain'.")
    else:
        parts.append("\n3. **Classification**: Suggest a general category.")
        parts.append("4. **Confidence**: Mark 'uncertain' since no categories are defined.")

    parts.append("\n## Response Format")
    parts.append("Respond with valid JSON only, using these EXACT field names:")
    parts.append("""```json
{
  "title": "Short 4-5 Word Title",
  "summary": "Your 3-4 sentence summary",
  "category": "Exactly one of the listed category names",
  "confidence": "confident OR uncertain",
  "source": {
    "name": "Descriptive source name",
    "type": "forum | twitter | blog | governance | news | substack | mirror | medium | github | discord | other",
    "original_date": "YYYY-MM-DD (the source publication date, NOT today)",
    "url": "the source URL or null"
  },
  "entities": {
    "orgs": ["organizations/projects mentioned"],
    "amounts": ["funding amounts with currency"],
    "deadlines": ["YYYY-MM-DD deadlines"],
    "key_terms": ["important terms"]
  }
}
```""")
    return "\n".join(parts)


SYSTEM_PROMPT = (
    "You are a content analyst for The Grant Wire — a crypto/AI/OSS grants and funding aggregator. "
    "You summarize linked content about governance votes, treasury decisions, grant rounds, "
    "funding opportunities, and ecosystem research. Always respond with valid JSON only."
)


def _extract_json(text_blob: str) -> Optional[dict]:
    """Best-effort JSON extraction. Handles ```json fenced and bare."""
    try:
        return json.loads(text_blob)
    except json.JSONDecodeError:
        pass
    cleaned = text_blob.strip()
    if "```json" in cleaned:
        start = cleaned.find("```json") + 7
        end = cleaned.find("```", start)
        if end > start:
            cleaned = cleaned[start:end].strip()
    elif "```" in cleaned:
        start = cleaned.find("```") + 3
        end = cleaned.find("```", start)
        if end > start:
            cleaned = cleaned[start:end].strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        logger.error(f"Could not parse JSON from AI response (len={len(text_blob)})")
        return None


def _normalize_category(name: Optional[str], categories: List[Category]) -> Optional[str]:
    if not name or not categories:
        return name
    by_lower = {c.name.lower(): c.name for c in categories}
    return by_lower.get(name.lower().strip(), None)


def _build_digest_prompt(
    message_content: str,
    urls: List[str],
    url_contents: Dict[str, str],
    prompt_hint: Optional[str],
    links: Optional[List[dict]] = None,
) -> str:
    parts: List[str] = [
        "You are extracting structured items from a Telegram channel digest post. "
        "One post typically lists SEVERAL distinct items — return one entry per item.",
    ]
    if prompt_hint:
        parts.append(f"\n## About this channel\n{prompt_hint}")
    parts.append(f"\n## The post\n{message_content}")
    if urls:
        parts.append("\n## Linked content")
        for url in urls:
            parts.append(f"- {url}")
            content = url_contents.get(url)
            if content:
                parts.append(f"  Preview: {content[:1500]}")
    if links:
        parts.append("\n## Links in the post (anchor text → URL) — use these to fill each item's url")
        for l in links[:50]:
            parts.append(f"- {l.get('text','')} → {l.get('url','')}")
    parts.append(
        "\n## Your task\n"
        "Return ONLY a JSON object, no prose, shaped exactly like:\n"
        '{"title": "post title, 4-6 words", "summary": "1-2 sentence overview of the post",\n'
        ' "confidence": "confident" or "uncertain",\n'
        ' "items": [{"title": "item name", "url": "item link or null", "note": "what it is / why it matters, 1-2 lines",\n'
        '            "category": "the section label the post itself uses, or null", "tags": ["short", "tags"], "org": "org/author or null"}]}\n'
        "Rules: one items[] entry per distinct item in the post. Use the post's own links "
        "(prefer the item's primary link). Never invent URLs. Keep notes factual. "
        "Attach each item's url by matching the links above to the item — the link's anchor text is usually the item's title."
    )
    return "\n".join(parts)


def _parse_digest_response(content: str) -> Optional[dict]:
    """Validate a raw model response against DigestAnalysis. None on any failure."""
    parsed = _extract_json(content)
    if parsed is None:
        return None
    try:
        analysis = DigestAnalysis(**parsed)
    except Exception as e:
        logger.error(f"Digest response failed validation: {e} | head: {content[:300]}")
        return None
    return analysis.model_dump()


def attach_entity_urls(items: List[dict], links: List[dict]) -> List[dict]:
    """Fill missing item URLs from Telegram entity links by matching the link's
    anchor text to the item title — exact (case-insensitive) first, then containment.
    Producers wrap each item's title in its link, so anchor text == title."""
    if not links:
        return items
    by_text = {}
    for l in links:
        t = (l.get("text") or "").strip().lower()
        u = l.get("url")
        if t and u and t not in by_text:
            by_text[t] = u
    for item in items:
        if item.get("url"):
            continue
        title = (item.get("title") or "").strip().lower()
        if not title:
            continue
        if title in by_text:
            item["url"] = by_text[title]
            continue
        for t, u in by_text.items():
            if title in t or t in title:
                item["url"] = u
                break
    return items


class AIService:
    def __init__(self, db_for_init: Optional[Session] = None):
        # Try env override first; fall back to DB lookup at request time if needed
        self._config: Optional[ProviderConfig] = _resolve_config_from_env()
        self._db_lookup_attempted = False
        self.rate_limiter = _RateLimiter(settings.max_ai_requests_per_hour)
        if self._config:
            logger.info(f"AI provider (env): {self._config.provider} / {self._config.model}")
        else:
            logger.info("AI provider: will resolve from api_providers table on first call")

    def _ensure_config(self, db: Session) -> Optional[ProviderConfig]:
        if self._config:
            return self._config
        if self._db_lookup_attempted:
            return None
        self._db_lookup_attempted = True
        self._config = _resolve_config_from_db(db, prefer_provider=settings.ai_provider)
        if self._config:
            logger.info(f"AI provider (DB): {self._config.provider} / {self._config.model}")
        else:
            logger.warning("⚠️  No AI provider configured. Set AI_PROVIDER+AI_API_KEY or add a row to api_providers.")
        return self._config

    @property
    def is_configured(self) -> bool:
        return self._config is not None and bool(self._config.api_key)

    def get_status(self) -> dict:
        return {
            "is_configured": self.is_configured,
            "provider": self._config.provider if self._config else None,
            "model": self._config.model if self._config else None,
            "rate_limit_remaining": self.rate_limiter.remaining(),
            "rate_limit_max": self.rate_limiter.max,
        }

    async def analyze_content(
        self,
        message_content: str,
        urls: List[str],
        url_contents: Dict[str, str],
        categories: List[Category],
        db: Optional[Session] = None,
        wire: Optional[str] = None,
    ) -> Optional[dict]:
        """Analyze content with the configured provider. Returns a dict shaped like the
        legacy GrokService output (title, summary, category, confidence, source, entities)."""
        cfg = self._ensure_config(db) if db else self._config
        if not cfg or not cfg.api_key:
            logger.info("AI not configured — skipping content analysis")
            return None

        if not self.rate_limiter.can_request():
            logger.warning(f"AI rate limit reached. Remaining: {self.rate_limiter.remaining()}")
            return None

        prompt = _build_prompt(message_content, urls, url_contents, categories)

        try:
            if cfg.provider == "anthropic":
                content, tokens_in, tokens_out, duration_ms = await self._call_anthropic(cfg, prompt)
            elif cfg.provider == "ollama":
                content, tokens_in, tokens_out, duration_ms = await self._call_ollama(cfg, prompt)
            else:
                content, tokens_in, tokens_out, duration_ms = await self._call_openai_compat(cfg, prompt)
        except Exception as e:
            logger.error(f"AI call failed ({cfg.provider}): {e}")
            self._log_usage(db, cfg, success=False, error=str(e)[:500], wire=wire)
            return None

        self.rate_limiter.record()
        self._log_usage(
            db, cfg, success=True,
            tokens_in=tokens_in, tokens_out=tokens_out,
            duration_ms=duration_ms, wire=wire,
        )

        parsed = _extract_json(content)
        if parsed is None:
            return None

        # Pydantic-validate. Drop into legacy dict shape that processor.py expects.
        try:
            analysis = AIAnalysis(**parsed)
        except Exception as e:
            logger.error(f"AI response failed validation: {e} | response head: {content[:300]}")
            return None

        # Normalize category against available categories (case-insensitive match)
        category_name = _normalize_category(analysis.category, categories)
        if analysis.category and category_name is None:
            logger.warning(f"AI selected unknown category: {analysis.category}")

        return {
            "title": analysis.title,
            "summary": analysis.summary,
            "category": category_name,
            "confidence": analysis.confidence,  # str: "confident" | "uncertain"
            "source": analysis.source.model_dump(exclude_none=False),
            "entities": analysis.entities.model_dump(),
        }

    async def analyze_digest(
        self,
        message_content: str,
        urls: List[str],
        url_contents: Dict[str, str],
        prompt_hint: Optional[str] = None,
        db: Optional[Session] = None,
        wire: Optional[str] = None,
        links: Optional[List[dict]] = None,
    ) -> Optional[dict]:
        """Digest profile: explode one post into a validated item list.
        Returns DigestAnalysis.model_dump() or None (caller's state machine retries)."""
        cfg = self._ensure_config(db) if db else self._config
        if not cfg or not cfg.api_key:
            logger.info("AI not configured — skipping digest analysis")
            return None
        if not self.rate_limiter.can_request():
            logger.warning(f"AI rate limit reached. Remaining: {self.rate_limiter.remaining()}")
            return None

        prompt = _build_digest_prompt(message_content, urls, url_contents, prompt_hint, links=links)
        # digests list many items; the grant-sized 800-token cap would truncate mid-JSON
        try:
            if cfg.provider == "anthropic":
                content, tokens_in, tokens_out, duration_ms = await self._call_anthropic(cfg, prompt, max_tokens=2500)
            elif cfg.provider == "ollama":
                content, tokens_in, tokens_out, duration_ms = await self._call_ollama(cfg, prompt, max_tokens=2500)
            else:
                content, tokens_in, tokens_out, duration_ms = await self._call_openai_compat(cfg, prompt, max_tokens=2500)
        except Exception as e:
            logger.error(f"AI call failed ({cfg.provider}): {e}")
            self._log_usage(db, cfg, success=False, error=str(e)[:500], wire=wire, purpose="digest")
            return None

        self.rate_limiter.record()
        self._log_usage(db, cfg, success=True, tokens_in=tokens_in,
                        tokens_out=tokens_out, duration_ms=duration_ms, wire=wire, purpose="digest")
        result = _parse_digest_response(content)
        if result is not None:
            result["items"] = attach_entity_urls(result.get("items", []), links or [])
        return result

    # ---------- providers ----------

    async def _call_openai_compat(
        self, cfg: ProviderConfig, prompt: str, max_tokens: int = 800
    ) -> Tuple[str, int, int, int]:
        if not cfg.base_url:
            raise ValueError(f"Provider '{cfg.provider}' requires base_url")
        url = f"{cfg.base_url.rstrip('/')}/chat/completions"
        payload = {
            "model": cfg.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.3,
            "max_tokens": max_tokens,
        }
        headers = {"Authorization": f"Bearer {cfg.api_key}", "Content-Type": "application/json"}
        t0 = time.monotonic()
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(url, headers=headers, json=payload)
        duration_ms = int((time.monotonic() - t0) * 1000)
        if r.status_code != 200:
            raise RuntimeError(f"{cfg.provider} HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage") or {}
        return content, int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0), duration_ms

    async def _call_ollama(
        self, cfg: ProviderConfig, prompt: str, max_tokens: int = 800
    ) -> Tuple[str, int, int, int]:
        """Ollama Cloud native /api/chat endpoint. Different shape from OpenAI-compat."""
        if not cfg.base_url:
            raise ValueError("Provider 'ollama' requires base_url (e.g. https://ollama.com/api)")
        url = f"{cfg.base_url.rstrip('/')}/chat"
        payload = {
            "model": cfg.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "options": {"temperature": 0.3, "num_predict": max_tokens},
            "format": "json",  # ask the server to constrain to JSON
        }
        headers = {"Authorization": f"Bearer {cfg.api_key}", "Content-Type": "application/json"}
        t0 = time.monotonic()
        async with httpx.AsyncClient(timeout=120.0) as client:  # bigger models can be slow
            r = await client.post(url, headers=headers, json=payload)
        duration_ms = int((time.monotonic() - t0) * 1000)
        if r.status_code != 200:
            raise RuntimeError(f"ollama HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
        content = (data.get("message") or {}).get("content", "")
        return (
            content,
            int(data.get("prompt_eval_count") or 0),
            int(data.get("eval_count") or 0),
            duration_ms,
        )

    async def _call_anthropic(
        self, cfg: ProviderConfig, prompt: str, max_tokens: int = 800
    ) -> Tuple[str, int, int, int]:
        url = (cfg.base_url.rstrip('/') if cfg.base_url else "https://api.anthropic.com") + "/v1/messages"
        payload = {
            "model": cfg.model,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0.3,
        }
        headers = {
            "x-api-key": cfg.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        t0 = time.monotonic()
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(url, headers=headers, json=payload)
        duration_ms = int((time.monotonic() - t0) * 1000)
        if r.status_code != 200:
            raise RuntimeError(f"anthropic HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
        content = "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text")
        usage = data.get("usage") or {}
        return content, int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0), duration_ms

    # ---------- telemetry ----------

    def _log_usage(
        self,
        db: Optional[Session],
        cfg: ProviderConfig,
        *,
        success: bool,
        tokens_in: int = 0,
        tokens_out: int = 0,
        duration_ms: int = 0,
        wire: Optional[str] = None,
        error: Optional[str] = None,
        purpose: str = "summarize",
    ) -> None:
        """Write to the shared api_usage_log table. Never block the caller on failure."""
        if db is None:
            return
        try:
            db.execute(
                text("""
                    INSERT INTO api_usage_log
                        (provider, model, purpose, tokens_in, tokens_out,
                         wire, duration_ms, success, error_message, created_at)
                    VALUES (:provider, :model, :purpose, :tin, :tout,
                            :wire, :dur, :success, :err, NOW())
                """),
                {
                    "provider": cfg.provider,
                    "model": cfg.model,
                    "purpose": purpose,
                    "tin": tokens_in,
                    "tout": tokens_out,
                    "wire": wire,
                    "dur": duration_ms,
                    "success": success,
                    "err": error,
                },
            )
            db.commit()
        except Exception as e:
            logger.warning(f"api_usage_log write failed (non-fatal): {e}")
            db.rollback()
