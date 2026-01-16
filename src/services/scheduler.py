import logging
import asyncio
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session

from src.config import settings
from src.models import database
from src.models.models import ChannelConfig, TelegramBot, ImportMode, Summary, ProcessingStatus, Message
from src.services.telegram import TelegramService
from src.services.processor import MessageProcessor

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()

# =============================================================================
# BACKGROUND JOBS
#
# The scheduler runs 5 jobs to keep the system processing:
#   1. sync_and_process    - Full Telegram sync + batch processing (every N hours)
#   2. process_pending_only - Process pending messages only (every 2 min)
#   3. retry_failed_messages - Re-queue failed messages for retry (every 15 min)
#   4. cleanup_stale_processing - Reset stuck PROCESSING status (every 1 hour)
#   5. log_daily_stats     - Log statistics at midnight
#
# See PROCESSING.md for full documentation.
# =============================================================================


async def sync_and_process():
    """
    Main sync job: Sync new messages from Telegram and process them.

    Runs every sync_interval_hours (default: 6 hours).
    1. Syncs new messages from Telegram (phone or bot mode)
    2. Processes messages in batches of 20
    3. Processes up to 10 batches (200 messages max per run)
    4. 5 second pause between batches to avoid overwhelming Grok API
    """
    logger.info("Starting scheduled sync and process task")
    
    database.get_engine()
    db = database.SessionLocal()
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
                import httpx
                from src.utils.url_extractor import extract_urls
                from datetime import datetime

                bot_tokens = settings.get_bot_tokens()
                for bot_token in bot_tokens:
                    # Persist per-bot update offsets to avoid replaying old updates every run
                    bot_record = db.query(TelegramBot).filter(
                        TelegramBot.bot_token == bot_token
                    ).first()

                    if not bot_record:
                        bot_record = TelegramBot(
                            bot_token=bot_token,
                            is_active=True
                        )
                        db.add(bot_record)
                        db.commit()
                        db.refresh(bot_record)

                    offset = (bot_record.last_update_id or 0) + 1

                    async with httpx.AsyncClient(timeout=30.0) as client:
                        # Get updates from Telegram Bot API
                        response = await client.get(
                            f'https://api.telegram.org/bot{bot_token}/getUpdates',
                            params={'limit': 100, 'offset': offset}
                        )
                        updates = response.json()

                        if not updates.get('ok'):
                            logger.error(f"Bot API error: {updates}")
                            continue

                        imported = 0
                        last_update_id = None

                        for update in updates['result']:
                            last_update_id = update['update_id']

                            post = update.get('channel_post') or update.get('message')
                            if not post:
                                continue

                            telegram_id = post['message_id']
                            chat_id = str(post['chat']['id'])
                            text_content = post.get('text') or post.get('caption') or ''
                            timestamp = post['date']

                            # Only import from channels configured in the database
                            channel_config = db.query(ChannelConfig).filter(
                                ChannelConfig.channel_id == chat_id,
                                ChannelConfig.is_active == True
                            ).first()

                            if not channel_config:
                                continue

                            # Check if already exists
                            existing = db.query(Message).filter(
                                Message.telegram_id == telegram_id,
                                Message.channel_id == chat_id
                            ).first()

                            if existing:
                                continue

                            # Extract URLs and create message
                            urls = extract_urls(text_content) if text_content else []

                            new_msg = Message(
                                telegram_id=telegram_id,
                                channel_id=chat_id,
                                bot_id=bot_record.id,
                                import_method='bot',
                                timestamp=datetime.utcfromtimestamp(timestamp),
                                raw_content=text_content,
                                extracted_urls=urls
                            )
                            db.add(new_msg)
                            db.flush()

                            # Create summary with PENDING status
                            summary = Summary(
                                message_id=new_msg.id,
                                processing_status=ProcessingStatus.PENDING,
                                processing_attempts=0
                            )
                            db.add(summary)
                            imported += 1
                            logger.info(f"Bot imported TG#{telegram_id}")

                        db.commit()

                        # Persist update offset so subsequent runs don't replay old updates
                        if last_update_id is not None:
                            bot_record.last_update_id = last_update_id
                            db.commit()

                        if imported > 0:
                            logger.info(f"Bot sync completed: {imported} new messages")

            except Exception as e:
                logger.error(f"Error in bot sync: {e}")
        
        # Process pending messages regardless of source (phone or bot)
        processor = MessageProcessor()
        processed = 0
        batch_size = 20   # Messages per batch
        max_batches = 10  # Maximum batches per sync (20 * 10 = 200 messages max)

        # Process in batches until we run out of pending messages or hit max_batches
        for _ in range(max_batches):
            batch_processed = await processor.process_batch(db, limit=batch_size, include_retries=True)
            processed += batch_processed

            # If batch wasn't full, we've processed all pending messages
            if batch_processed < batch_size:
                break

            # Pause between batches to avoid rate limiting Grok API
            await asyncio.sleep(5)
        
        logger.info(f"Processed {processed} messages")
        
    except Exception as e:
        logger.error(f"Error in scheduled task: {e}")
    finally:
        db.close()

async def retry_failed_messages():
    """
    Re-queue FAILED messages for another attempt.

    Runs every 15 minutes.

    Calls processor.retry_failed_messages() which changes FAILED -> RETRY
    for messages that are within 24 hours old and haven't exceeded max_retries.
    """
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
    """
    Reset messages stuck in PROCESSING status.

    Runs every 1 hour.

    If a message has been in PROCESSING status for >10 minutes, it's likely
    the process crashed or timed out. Reset to RETRY so it can be picked up
    by the next processing cycle.
    """
    logger.info("Cleaning up stale processing status...")

    database.get_engine()
    db = database.SessionLocal()
    try:
        # 10-minute timeout: if a message is still PROCESSING after this, something went wrong
        cutoff = datetime.utcnow() - timedelta(minutes=30)
        
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
    
    database.get_engine()
    db = database.SessionLocal()
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
    """
    Start the APScheduler with all background jobs.

    Jobs registered (see PROCESSING.md for full documentation):
    - sync_job: Full Telegram sync + process (every N hours)
    - process_pending: Process pending messages (every 2 min)
    - retry_failed: Re-queue failed messages (every 15 min)
    - cleanup_stale: Reset stuck PROCESSING status (every 1 hour)
    - daily_stats: Log statistics (midnight)
    """
    # Ensure the DB engine/session factory is initialized before any jobs run.
    # This is critical in environments where importing SessionLocal too early would freeze it as None.
    database.get_engine()

    # JOB 1: Main sync - fetch new messages from Telegram and process them
    scheduler.add_job(
        sync_and_process,
        trigger=IntervalTrigger(hours=settings.sync_interval_hours),
        id="sync_job",
        name="Sync and Process Messages",
        replace_existing=True
    )

    # JOB 2: Process pending - keep queue moving between main syncs
    scheduler.add_job(
        process_pending_only,
        trigger=IntervalTrigger(minutes=2),
        id="process_pending",
        name="Process Pending Messages",
        replace_existing=True
    )

    # JOB 3: Retry failed - give failed messages another chance
    scheduler.add_job(
        retry_failed_messages,
        trigger=IntervalTrigger(minutes=15),
        id="retry_failed",
        name="Retry Failed Messages",
        replace_existing=True
    )

    # JOB 4: Cleanup stale - reset messages stuck in PROCESSING (crashed jobs)
    scheduler.add_job(
        cleanup_stale_processing,
        trigger=IntervalTrigger(hours=1),
        id="cleanup_stale",
        name="Cleanup Stale Processing",
        replace_existing=True
    )

    # JOB 5: Daily stats - log summary at midnight for monitoring
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
    """
    Process pending messages without syncing from Telegram.

    Runs every 2 minutes.

    This keeps the processing queue moving between the main sync_and_process
    jobs. Processes only 10 messages per run to avoid long-running jobs.
    """
    database.get_engine()
    db = database.SessionLocal()
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
