import logging
import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.orm import Session

from src.config import settings
from src.models.database import SessionLocal
from src.services.telegram import TelegramService
from src.services.processor import MessageProcessor

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()

async def sync_and_process():
    logger.info("Starting scheduled sync and process task")
    
    db = SessionLocal()
    try:
        telegram_service = TelegramService()
        result = await telegram_service.sync_new_messages(db)
        logger.info(f"Sync completed: {result}")
        
        processor = MessageProcessor()
        processed = 0
        batch_size = 20
        max_batches = 10
        
        for _ in range(max_batches):
            batch_processed = await processor.process_batch(db, limit=batch_size)
            processed += batch_processed
            
            if batch_processed < batch_size:
                break
            
            await asyncio.sleep(5)
        
        logger.info(f"Processed {processed} messages")
        
    except Exception as e:
        logger.error(f"Error in scheduled task: {e}")
    finally:
        db.close()

def start_scheduler():
    scheduler.add_job(
        sync_and_process,
        trigger=IntervalTrigger(hours=settings.sync_interval_hours),
        id="sync_job",
        name="Sync and Process Messages",
        replace_existing=True
    )
    
    scheduler.start()
    logger.info(f"Scheduler started with {settings.sync_interval_hours} hour interval")

def shutdown_scheduler():
    if scheduler.running:
        scheduler.shutdown()
        logger.info("Scheduler stopped")