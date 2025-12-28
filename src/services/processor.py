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
from typing import Optional, List, Dict
from datetime import datetime, timedelta
from sqlalchemy.orm import Session

from src.models.models import Message, Summary, Category, ProcessingStatus, ConfidenceLevel
from src.services.grok import GrokService
from src.scrapers.content_scraper import ContentScraper
from src.scrapers.twitter_scraper import TwitterScraper
from src.utils.url_extractor import is_twitter_url
from src.config import settings

logger = logging.getLogger(__name__)

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
        self.grok_service = GrokService()
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

        # STATE CHECK: Already processed successfully - skip
        if summary.processing_status == ProcessingStatus.SUCCESS:
            return True

        # STATE TRANSITION: Check if max retries exceeded -> FAILED
        # This prevents infinite retry loops for persistently failing messages
        if summary.processing_attempts >= self.max_retries:
            summary.processing_status = ProcessingStatus.FAILED
            summary.error_log = f"Max retries ({self.max_retries}) exceeded"
            db.commit()
            return False

        # STATE TRANSITION: PENDING/RETRY -> PROCESSING
        # Increment attempt counter and update timestamp for tracking
        summary.processing_status = ProcessingStatus.PROCESSING
        summary.processing_attempts += 1
        summary.last_processed_at = datetime.utcnow()
        db.commit()
        
        try:
            # Get categories for classification
            categories = db.query(Category).filter(Category.user_id == 1).all()
            
            # Separate URLs by type
            urls = message.extracted_urls or []
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

            # Call Grok API
            result = await self.grok_service.analyze_content(
                message.raw_content,
                urls,
                url_contents,
                categories,
                db
            )
            
            if result:
                # Update summary with results
                summary.title = result.get("title")
                summary.summary = result.get("summary")
                # Merge source info into entities for storage
                entities = result.get("entities", {})
                source = result.get("source", {})
                # Get URLs from message
                urls = message.extracted_urls or []
                # Use message URL if available, otherwise use Grok's source URL (if any)
                if urls:
                    source["url"] = urls[0]
                elif source.get("url") and source.get("url") != "null":
                    # Keep Grok's URL if it provided one
                    pass
                else:
                    source["url"] = None
                    source["note"] = "Original source link unavailable"
                entities["source"] = source
                entities["urls"] = urls
                summary.entities = entities
                summary.confidence = result.get("confidence")
                
                # Match category
                category_name = result.get("category")
                if category_name:
                    category = db.query(Category).filter(
                        Category.user_id == 1,
                        Category.name == category_name
                    ).first()
                    if category:
                        summary.category_id = category.id
                
                # STATE TRANSITION: PROCESSING -> SUCCESS
                summary.processing_status = ProcessingStatus.SUCCESS
                summary.error_log = None
                db.commit()

                logger.info(f"Successfully processed message {message.id}")
                return True
            else:
                # STATE TRANSITION: PROCESSING -> RETRY
                # Grok returned None - typically means rate limit hit or API unavailable
                # Message will be picked up again in the next processing cycle
                summary.processing_status = ProcessingStatus.RETRY
                summary.error_log = "Grok API returned no result - will retry"
                db.commit()

                logger.warning(f"Message {message.id} queued for retry (attempt {summary.processing_attempts})")
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
        
        # Get rate limit status
        rate_limit = self.grok_service.get_rate_limit_status()
        
        return {
            "total_messages": total,
            "pending": pending,
            "processing": processing,
            "success": success,
            "failed": failed,
            "retry_queued": retry,
            "success_rate": round(success / total * 100, 1) if total > 0 else 0,
            "grok_rate_limit": rate_limit
        }


async def process_message(message_id: int, db: Session) -> bool:
    """Convenience function to process a single message by ID"""
    message = db.query(Message).filter(Message.id == message_id).first()
    if not message:
        logger.error(f"Message {message_id} not found")
        return False
    
    processor = MessageProcessor()
    return await processor.process_message(message, db)
