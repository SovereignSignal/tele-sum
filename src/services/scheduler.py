import logging
import asyncio
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session

from src.config import settings
from src.models.database import SessionLocal, get_engine
from src.models.models import ChannelConfig, TelegramBot, ImportMode, Summary, ProcessingStatus
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
            batch_processed = await processor.process_batch(db, limit=batch_size, include_retries=True)
            processed += batch_processed
            
            if batch_processed < batch_size:
                break
            
            await asyncio.sleep(5)
        
        logger.info(f"Processed {processed} messages")
        
    except Exception as e:
        logger.error(f"Error in scheduled task: {e}")
    finally:
        db.close()

async def retry_failed_messages():
    """Queue failed messages for retry"""
    logger.info("Checking for failed messages to retry...")
    
    db = SessionLocal()
    try:
        processor = MessageProcessor()
        count = await processor.retry_failed_messages(
            db,
            max_age_hours=24,
            limit=50
        )
        if count > 0:
            logger.info(f"Queued {count} failed messages for retry")
    except Exception as e:
        logger.error(f"Error retrying failed messages: {e}")
    finally:
        db.close()

async def cleanup_stale_processing():
    """Reset messages stuck in 'processing' status"""
    logger.info("Cleaning up stale processing status...")
    
    db = SessionLocal()
    try:
        # Find messages stuck in processing for more than 10 minutes
        cutoff = datetime.utcnow() - timedelta(minutes=10)
        
        stale_summaries = db.query(Summary).filter(
            Summary.processing_status == ProcessingStatus.PROCESSING,
            Summary.last_processed_at < cutoff
        ).all()
        
        count = 0
        for summary in stale_summaries:
            summary.processing_status = ProcessingStatus.RETRY
            summary.error_log = "Reset from stale processing status"
            count += 1
        
        db.commit()
        
        if count > 0:
            logger.info(f"Reset {count} stale processing messages to retry")
    except Exception as e:
        logger.error(f"Error cleaning up stale processing: {e}")
    finally:
        db.close()

async def log_daily_stats():
    """Log daily processing statistics"""
    logger.info("Generating daily statistics...")
    
    db = SessionLocal()
    try:
        processor = MessageProcessor()
        stats = await processor.get_processing_stats(db)
        
        logger.info(f"""
📊 Daily Statistics:
   Total Messages: {stats['total_messages']}
   Processed: {stats['success']} ({stats['success_rate']}%)
   Pending: {stats['pending']}
   Failed: {stats['failed']}
   Retry Queue: {stats['retry_queued']}
   Grok API: {stats['grok_rate_limit']['remaining']}/{stats['grok_rate_limit']['max_per_hour']} requests remaining
""")
    except Exception as e:
        logger.error(f"Error logging daily stats: {e}")
    finally:
        db.close()

def start_scheduler():
    """Start the scheduler with all jobs"""
    # Main sync and process job
    scheduler.add_job(
        sync_and_process,
        trigger=IntervalTrigger(hours=settings.sync_interval_hours),
        id="sync_job",
        name="Sync and Process Messages",
        replace_existing=True
    )
    
    # Process pending messages more frequently (every 2 minutes)
    scheduler.add_job(
        process_pending_only,
        trigger=IntervalTrigger(minutes=2),
        id="process_pending",
        name="Process Pending Messages",
        replace_existing=True
    )
    
    # Retry failed messages every 15 minutes
    scheduler.add_job(
        retry_failed_messages,
        trigger=IntervalTrigger(minutes=15),
        id="retry_failed",
        name="Retry Failed Messages",
        replace_existing=True
    )
    
    # Cleanup stale processing every hour
    scheduler.add_job(
        cleanup_stale_processing,
        trigger=IntervalTrigger(hours=1),
        id="cleanup_stale",
        name="Cleanup Stale Processing",
        replace_existing=True
    )
    
    # Daily stats at midnight
    scheduler.add_job(
        log_daily_stats,
        trigger=CronTrigger(hour=0, minute=0),
        id="daily_stats",
        name="Log Daily Statistics",
        replace_existing=True
    )
    
    scheduler.start()
    logger.info(f"✅ Scheduler started with {settings.sync_interval_hours} hour sync interval")

async def process_pending_only():
    """Process only pending messages without syncing"""
    db = SessionLocal()
    try:
        processor = MessageProcessor()
        processed = await processor.process_batch(db, limit=10, include_retries=True)
        if processed > 0:
            logger.info(f"Processed {processed} pending messages")
    except Exception as e:
        logger.error(f"Error processing pending messages: {e}")
    finally:
        db.close()

def shutdown_scheduler():
    if scheduler.running:
        scheduler.shutdown()
        logger.info("Scheduler stopped")

def get_scheduler_status():
    """Get status of all scheduled jobs"""
    jobs = []
    for job in scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "name": job.name,
            "next_run": str(job.next_run_time) if job.next_run_time else None,
            "trigger": str(job.trigger)
        })
    return {
        "is_running": scheduler.running,
        "jobs": jobs
    }
