"""
Message Processing Pipeline

This module handles the core processing of Telegram messages through Grok AI.
It manages the complete lifecycle from PENDING -> PROCESSING -> SUCCESS/RETRY/FAILED.

Key components:
- MessageProcessor: Main class that orchestrates message processing
- process_message(): Convenience function for single message processing

Processing Flow:
1. Message enters as PENDING
2. URLs are extracted and categorized (Twitter vs other)
3. Non-Twitter URLs are scraped for content preview
4. Twitter URLs are scraped via FxTwitter API
5. Content is sent to Grok AI for analysis
6. Results (summary, category, entities) are stored
7. Status is updated to SUCCESS, RETRY, or FAILED

See PROCESSING.md for the full state machine and retry logic documentation.
"""

import logging
import asyncio
from typing import Optional, List, Dict, Tuple
from datetime import datetime, timedelta
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.models.models import Message, Summary, Category, ProcessingStatus, ConfidenceLevel
from src.services.ai import AIService
from src.services.ai_rate_limit import AIRequestDeferred
from src.services.distribution_guard import is_distribution_post
from src.scrapers.content_scraper import ContentScraper
from src.scrapers.twitter_scraper import TwitterScraper
from src.utils.url_extractor import is_twitter_url, normalize_url
from src.config import settings

logger = logging.getLogger(__name__)


def _check_seen(db: Session, urls: List[str], wire_value: Optional[str]) -> Optional[Dict]:
    """Look up message URLs in the shared seen_urls table (per-wire scoped).

    Returns a dict describing the first match, or None.
    No-ops without a wire (the unique index requires wire NOT NULL).
    """
    if not urls or not wire_value:
        return None
    for raw in urls:
        norm = normalize_url(raw)
        if not norm:
            continue
        try:
            row = db.execute(
                text(
                    "SELECT id, first_seen_entry_id, first_seen_date "
                    "FROM seen_urls WHERE normalized_url = :n AND wire = :w LIMIT 1"
                ),
                {"n": norm, "w": wire_value},
            ).first()
        except Exception:
            # Standalone deployment without the shared seen_urls table — grant
            # URL dedup is a cross-app feature, not a requirement. Fail open.
            db.rollback()
            return None
        if row:
            return {
                "seen_urls_id": row[0],
                "first_seen_entry_id": row[1],
                "first_seen_date": str(row[2]) if row[2] else None,
                "matched_url": raw,
                "normalized_url": norm,
            }
    return None


def _record_seen(db: Session, urls: List[str], wire_value: Optional[str]) -> None:
    """Insert message URLs into seen_urls (idempotent via unique index)."""
    if not urls or not wire_value:
        return
    for raw in urls:
        norm = normalize_url(raw)
        if not norm:
            continue
        try:
            db.execute(
                text(
                    "INSERT INTO seen_urls (url, normalized_url, wire, first_seen_date) "
                    "VALUES (:url, :norm, :wire, CURRENT_DATE) "
                    "ON CONFLICT (normalized_url, wire) DO NOTHING"
                ),
                {"url": raw, "norm": norm, "wire": wire_value},
            )
        except Exception as e:
            logger.warning(f"seen_urls insert failed for {norm} (non-fatal): {e}")
            db.rollback()

KNOWN_PROFILES = {"grant", "digest"}


def resolve_profile(channel_config) -> Tuple[str, Optional[str]]:
    """(profile, prompt_hint) for a channel. Unknown/missing -> the grant default,
    so a bad config row can never stall ingestion."""
    profile = (getattr(channel_config, "extraction_profile", None) or "").strip().lower()
    if profile not in KNOWN_PROFILES:
        return ("grant", None)
    hint = getattr(channel_config, "prompt_hint", None) if profile == "digest" else None
    return (profile, hint)


class MessageProcessor:
    """
    Handles the processing of Telegram messages through Grok AI.

    Processing Status State Machine:
        PENDING -> PROCESSING -> SUCCESS (happy path)
        PENDING -> PROCESSING -> RETRY (Grok API error, will retry)
        PENDING -> PROCESSING -> FAILED (max retries exceeded)
        RETRY -> PROCESSING -> SUCCESS/RETRY/FAILED (on next processing cycle)

    See PROCESSING.md for full documentation.
    """

    def __init__(self):
        self.ai_service = AIService()
        self.content_scraper = ContentScraper()
        self.twitter_scraper = TwitterScraper()
        self.max_retries = settings.max_processing_retries  # Default: 3 attempts
        self.retry_delay_minutes = 5

    async def process_message(self, message: Message, db: Session) -> bool:
        """
        Process a single message with Grok AI.

        Returns True if processing succeeded, False otherwise.
        Updates the message's Summary record with status, summary text,
        category, entities, and error log (if failed).
        """
        if not message.summary:
            message.summary = Summary(
                message_id=message.id,
                processing_status=ProcessingStatus.PENDING
            )
            db.add(message.summary)
            db.commit()
        
        summary = message.summary
        db.refresh(summary)

        # STATE CHECK: Already processed successfully - skip
        if summary.processing_status == ProcessingStatus.SUCCESS:
            return True
        if summary.processing_status == ProcessingStatus.PROCESSING:
            return False

        # STATE TRANSITION: Check if max retries exceeded -> FAILED
        # This prevents infinite retry loops for persistently failing messages
        if summary.processing_attempts >= self.max_retries:
            summary.processing_status = ProcessingStatus.FAILED
            summary.error_log = f"Max retries ({self.max_retries}) exceeded"
            db.commit()
            return False

        # ROUND-TRIP GUARD, processor-time: the bot-time check races
        # wire-room-ops' post-send log write — Telegram delivers the channel
        # post to our long-poll before the sibling app commits its
        # distribution_logs success row, so fresh echoes slip past import.
        # By processing time (>=2 min later) the row exists; re-check here
        # before spending an AI call.
        if is_distribution_post(db, message.telegram_id, message.wire):
            summary.entities = {"distribution_echo": True, "urls": message.extracted_urls or []}
            summary.data_quality = "duplicate"
            summary.processing_status = ProcessingStatus.SUCCESS
            summary.error_log = None
            db.commit()
            logger.info(
                f"Message {message.id} (TG#{message.telegram_id}, {message.wire} wire) "
                f"is a wire-room-ops distribution echo; skipping AI"
            )
            return True

        # STATE TRANSITION: PENDING/RETRY -> PROCESSING
        # Increment attempt counter and update timestamp for tracking
        previous_status = summary.processing_status
        previous_processed_at = summary.last_processed_at
        previous_error = summary.error_log
        claimed = db.query(Summary).filter(
            Summary.id == summary.id,
            Summary.processing_status.in_([ProcessingStatus.PENDING, ProcessingStatus.RETRY]),
        ).update({
            Summary.processing_status: ProcessingStatus.PROCESSING,
            Summary.processing_attempts: Summary.processing_attempts + 1,
            Summary.last_processed_at: datetime.utcnow(),
        }, synchronize_session=False)
        db.commit()
        db.refresh(summary)
        if not claimed:
            return summary.processing_status == ProcessingStatus.SUCCESS

        # Resolve per-channel extraction profile BEFORE seen_urls dedup.
        profile, prompt_hint = resolve_profile(message.channel_config)

        # DEDUP: any URL already in seen_urls (per-wire scoped)? If yes, skip AI.
        # Digest channels are not part of the grants seen-URL space; skip the check for them.
        urls = message.extracted_urls or []
        wire_value = message.wire
        if profile == "grant":
            seen = _check_seen(db, urls, wire_value)
            if seen:
                summary.entities = {"duplicate_of": seen, "urls": urls}
                summary.data_quality = "duplicate"
                summary.processing_status = ProcessingStatus.SUCCESS
                summary.error_log = None
                db.commit()
                logger.info(
                    f"Message {message.id} is a duplicate of seen_urls#{seen['seen_urls_id']} "
                    f"(matched {seen['matched_url']}); skipping AI"
                )
                return True

        try:
            # Get categories for classification
            categories = db.query(Category).filter(Category.user_id == 1).all()

            # Separate URLs by type
            twitter_urls = [url for url in urls if is_twitter_url(url)]
            other_urls = [url for url in urls if not is_twitter_url(url)]

            # Scrape all URLs - both Twitter and non-Twitter
            url_contents = {}

            # Scrape Twitter URLs using our dedicated scraper
            if twitter_urls:
                twitter_contents = await self.twitter_scraper.scrape_multiple_tweets(twitter_urls)
                url_contents.update(twitter_contents)
                logger.info(f"Scraped {len(twitter_contents)}/{len(twitter_urls)} Twitter URLs")

            # Scrape non-Twitter URLs
            if other_urls:
                other_contents = await self.content_scraper.scrape_multiple_urls(other_urls)
                url_contents.update(other_contents)

            # Branch: dispatch to the appropriate AI extractor based on channel profile.
            if profile == "digest":
                result = await self.ai_service.analyze_digest(
                    message_content=message.raw_content,
                    urls=urls,
                    url_contents=url_contents,
                    prompt_hint=prompt_hint,
                    db=db,
                    wire=wire_value,
                    links=message.entity_links,
                )
                if result is not None:
                    summary.title = result.get("title")
                    summary.summary = result.get("summary")
                    summary.entities = {
                        "profile": "digest",
                        "items": result.get("items", []),
                        "urls": urls,
                        "confidence": result.get("confidence"),
                    }
                    summary.confidence = (
                        ConfidenceLevel.CONFIDENT
                        if result.get("confidence") == "confident"
                        else ConfidenceLevel.UNCERTAIN
                    )
            else:
                # Grant profile (default): existing analyze_content path — unchanged.
                result = await self.ai_service.analyze_content(
                    message.raw_content,
                    urls,
                    url_contents,
                    categories,
                    db,
                    wire=wire_value,
                )
                if result:
                    summary.title = result.get("title")
                    summary.summary = result.get("summary")
                    entities = result.get("entities", {}) or {}
                    source = result.get("source", {}) or {}
                    urls = message.extracted_urls or []
                    if urls:
                        source["url"] = urls[0]
                    elif not source.get("url") or source.get("url") == "null":
                        source["url"] = None
                        source["note"] = "Original source link unavailable"
                    entities["source"] = source
                    entities["urls"] = urls
                    summary.entities = entities

                    # confidence comes back as a string ("confident"/"uncertain") from AIService.
                    conf_str = (result.get("confidence") or "").lower()
                    summary.confidence = ConfidenceLevel.CONFIDENT if conf_str == "confident" else ConfidenceLevel.UNCERTAIN

                    # Match category
                    category_name = result.get("category")
                    summary.category_id = None
                    if category_name:
                        category = db.query(Category).filter(
                            Category.user_id == 1,
                            Category.name == category_name
                        ).first()
                        if category:
                            summary.category_id = category.id

            # SHARED: state-machine outcome — same for both profiles.
            if result:
                # STATE TRANSITION: PROCESSING -> SUCCESS
                summary.processing_status = ProcessingStatus.SUCCESS
                summary.error_log = None
                db.commit()

                # Register URLs in the shared seen_urls table for cross-app dedup (grant only).
                if profile == "grant":
                    _record_seen(db, urls, wire_value)
                    db.commit()

                logger.info(f"Successfully processed message {message.id}")
                return True
            else:
                summary.processing_status = ProcessingStatus.RETRY
                summary.error_log = "AI provider returned no result - will retry"
                db.commit()

                logger.warning(f"Message {message.id} queued for retry (attempt {summary.processing_attempts})")
                return False

        except AIRequestDeferred as e:
            # Another worker may take the last slot after the batch precheck.
            # No request was sent, so restore the original queue state and budget.
            summary.processing_status = previous_status
            summary.processing_attempts -= 1
            summary.last_processed_at = previous_processed_at
            summary.error_log = previous_error
            db.commit()
            logger.info("Message %s deferred: %s", message.id, e)
            return False

        except Exception as e:
            logger.error(f"Error processing message {message.id}: {e}")

            # STATE TRANSITION: PROCESSING -> RETRY or FAILED
            # If we've exhausted retries, mark as FAILED (requires manual intervention)
            # Otherwise, mark as RETRY for automatic retry in next cycle
            if summary.processing_attempts >= self.max_retries:
                summary.processing_status = ProcessingStatus.FAILED
            else:
                summary.processing_status = ProcessingStatus.RETRY

            summary.error_log = str(e)[:1000]  # Truncate long error messages
            db.commit()
            return False
    
    async def process_batch(
        self,
        db: Session,
        limit: int = 10,
        include_retries: bool = True
    ) -> int:
        """
        Process a batch of pending messages.

        Selection priority:
        1. PENDING messages first (never been processed)
        2. RETRY messages second (failed but eligible for retry)
        3. Within each status, oldest messages first (by timestamp)

        Returns the number of successfully processed messages.
        """
        # Build status filter - always include PENDING, optionally include RETRY
        status_filter = [ProcessingStatus.PENDING]
        if include_retries:
            status_filter.append(ProcessingStatus.RETRY)

        # Query messages ordered by priority:
        # - processing_status.desc() puts PENDING before RETRY (alphabetically P > R when desc)
        # - timestamp.asc() ensures oldest messages are processed first within each status
        messages_to_process = db.query(Message).join(Summary).filter(
            Summary.processing_status.in_(status_filter)
        ).order_by(
            Summary.processing_status.desc(),  # PENDING before RETRY
            Message.timestamp.asc()            # Oldest first
        ).limit(limit).all()
        
        # Also get messages without summaries
        messages_without_summary = db.query(Message).outerjoin(Summary).filter(
            Summary.id == None
        ).limit(limit).all()
        
        # Create summaries for messages without them
        for msg in messages_without_summary:
            if msg not in messages_to_process:
                summary = Summary(
                    message_id=msg.id,
                    processing_status=ProcessingStatus.PENDING
                )
                db.add(summary)
                messages_to_process.append(msg)
        
        db.commit()
        
        if not messages_to_process:
            logger.info("No messages to process")
            return 0
        
        logger.info(f"Processing batch of {len(messages_to_process)} messages")
        
        processed = 0
        for message in messages_to_process[:limit]:
            try:
                if not self.ai_service.rate_limiter.can_request(db):
                    logger.info("Pausing batch at the AI request limit; pending work keeps its attempts")
                    break
            except AIRequestDeferred as e:
                logger.warning("Pausing batch: %s", e)
                break
            # Refresh message to get updated summary
            db.refresh(message)
            
            success = await self.process_message(message, db)
            if success:
                processed += 1

            # Rate limiting: 1 second delay between Grok API calls
            # This helps stay under the 100 requests/hour limit and avoids API throttling
            await asyncio.sleep(1)
        
        logger.info(f"Batch processing complete: {processed}/{len(messages_to_process)} successful")
        return processed
    
    async def retry_failed_messages(
        self,
        db: Session,
        max_age_hours: int = 24,
        limit: int = 50
    ) -> int:
        """
        Re-queue FAILED messages for another retry attempt.

        Only re-queues messages that:
        - Have FAILED status
        - Were last processed within max_age_hours (default: 24h)
        - Have not exceeded max_retries

        This is called by the scheduler's retry_failed_messages job every 15 minutes.
        """
        cutoff = datetime.utcnow() - timedelta(hours=max_age_hours)
        
        # Find failed messages that haven't exceeded retry limit
        failed_summaries = db.query(Summary).filter(
            Summary.processing_status == ProcessingStatus.FAILED,
            Summary.processing_attempts < self.max_retries,
            Summary.last_processed_at > cutoff
        ).limit(limit).all()
        
        count = 0
        for summary in failed_summaries:
            summary.processing_status = ProcessingStatus.RETRY
            count += 1
        
        db.commit()
        
        logger.info(f"Queued {count} failed messages for retry")
        return count
    
    async def get_processing_stats(self, db: Session) -> Dict:
        """Get processing statistics"""
        total = db.query(Message).count()
        
        pending = db.query(Summary).filter(
            Summary.processing_status == ProcessingStatus.PENDING
        ).count()
        
        processing = db.query(Summary).filter(
            Summary.processing_status == ProcessingStatus.PROCESSING
        ).count()
        
        success = db.query(Summary).filter(
            Summary.processing_status == ProcessingStatus.SUCCESS
        ).count()
        
        failed = db.query(Summary).filter(
            Summary.processing_status == ProcessingStatus.FAILED
        ).count()
        
        retry = db.query(Summary).filter(
            Summary.processing_status == ProcessingStatus.RETRY
        ).count()
        
        ai_status = self.ai_service.get_status(db)

        return {
            "total_messages": total,
            "pending": pending,
            "processing": processing,
            "success": success,
            "failed": failed,
            "retry_queued": retry,
            "success_rate": round(success / total * 100, 1) if total > 0 else 0,
            "ai": ai_status,
        }


async def process_message(message_id: int, db: Session) -> bool:
    """Convenience function to process a single message by ID"""
    message = db.query(Message).filter(Message.id == message_id).first()
    if not message:
        logger.error(f"Message {message_id} not found")
        return False
    
    processor = MessageProcessor()
    return await processor.process_message(message, db)
