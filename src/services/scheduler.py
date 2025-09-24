import logging
import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.orm import Session

from src.config import settings
from src.models.database import SessionLocal
from src.models.models import ChannelConfig, TelegramBot, ImportMode
from src.services.telegram import TelegramService
from src.services.processor import MessageProcessor

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()

async def sync_and_process():
    """Hybrid sync task that handles both phone and bot-based syncing"""
    logger.info("Starting scheduled sync and process task")
    
    db = SessionLocal()
    try:
        # Check what sync methods are available
        has_phone = settings.is_telegram_configured()
        has_bots = settings.is_bot_configured()
        
        # Handle phone-based sync if configured
        if has_phone:
            try:
                # Get channels configured for phone sync
                phone_channels = db.query(ChannelConfig).filter(
                    ChannelConfig.import_mode.in_([ImportMode.PHONE, ImportMode.HYBRID]),
                    ChannelConfig.is_active == True
                ).all()
                
                if phone_channels:
                    telegram_service = TelegramService()
                    result = await telegram_service.sync_new_messages(db)
                    logger.info(f"Phone sync completed: {result}")
            except Exception as e:
                logger.error(f"Error in phone sync: {e}")
        
        # Handle bot-based monitoring if configured
        if has_bots:
            try:
                # Get active bots
                active_bots = db.query(TelegramBot).filter(
                    TelegramBot.is_active == True
                ).all()
                
                for bot in active_bots:
                    logger.info(f"Processing bot {bot.id} (@{bot.bot_username})")
                    # Bot polling is handled separately by the bot manager
                    # This is just for processing pending messages
            except Exception as e:
                logger.error(f"Error in bot sync: {e}")
        
        # Process pending messages regardless of source
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