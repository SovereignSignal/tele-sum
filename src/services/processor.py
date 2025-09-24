import logging
import asyncio
from typing import Optional, List
from datetime import datetime
from sqlalchemy.orm import Session

from src.models.models import Message, Summary, Category, ProcessingStatus, ConfidenceLevel
from src.services.grok import GrokService
from src.scrapers.content_scraper import ContentScraper
from src.utils.url_extractor import is_twitter_url

logger = logging.getLogger(__name__)

class MessageProcessor:
    def __init__(self):
        self.grok_service = GrokService()
        self.scraper = ContentScraper()
        self.max_retries = 3
        
    async def process_batch(self, db: Session, limit: int = 10) -> int:
        messages = db.query(Message).join(Summary).filter(
            Summary.processing_status.in_([
                ProcessingStatus.PENDING,
                ProcessingStatus.RETRY
            ]),
            Summary.processing_attempts < self.max_retries
        ).limit(limit).all()
        
        processed = 0
        for message in messages:
            try:
                await self.process_single_message(message, db)
                processed += 1
            except Exception as e:
                logger.error(f"Failed to process message {message.id}: {e}")
        
        return processed
    
    async def process_single_message(self, message: Message, db: Session):
        if not message.summary:
            message.summary = Summary(
                message_id=message.id,
                processing_status=ProcessingStatus.PROCESSING
            )
            db.add(message.summary)
        else:
            message.summary.processing_status = ProcessingStatus.PROCESSING
        
        message.summary.processing_attempts += 1
        message.summary.last_processed_at = datetime.utcnow()
        db.commit()
        
        try:
            categories = db.query(Category).filter(Category.user_id == 1).all()
            
            urls = message.extracted_urls or []
            twitter_urls = [url for url in urls if is_twitter_url(url)]
            other_urls = [url for url in urls if not is_twitter_url(url)]
            
            scraped_contents = {}
            if other_urls:
                scraped_contents = await self.scraper.scrape_multiple_urls(other_urls)
            
            result = await self.grok_service.analyze_content(
                message.raw_content,
                urls,
                scraped_contents,
                categories
            )
            
            if result:
                message.summary.summary = result["summary"]
                message.summary.entities = result["entities"]
                message.summary.confidence = result["confidence"]
                
                if result["category"] and categories:
                    for cat in categories:
                        if cat.name.lower() == result["category"].lower():
                            message.summary.category_id = cat.id
                            break
                
                message.summary.processing_status = ProcessingStatus.SUCCESS
                logger.info(f"Successfully processed message {message.id}")
            else:
                message.summary.processing_status = ProcessingStatus.FAILED
                message.summary.error_log = "Failed to get valid response from Grok"
                logger.warning(f"Failed to get Grok analysis for message {message.id}")
            
        except Exception as e:
            message.summary.processing_status = ProcessingStatus.FAILED
            message.summary.error_log = str(e)
            logger.error(f"Error processing message {message.id}: {e}")
        
        db.commit()

async def process_message(message_id: int, db: Session):
    processor = MessageProcessor()
    message = db.query(Message).filter(Message.id == message_id).first()
    
    if not message:
        raise ValueError(f"Message {message_id} not found")
    
    await processor.process_single_message(message, db)