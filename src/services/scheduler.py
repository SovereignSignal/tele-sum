import logging
import asyncio
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session

from src.config import settings
from src.models import database
from src.models.models import Summary, ProcessingStatus
from src.services.processor import MessageProcessor
from src.services.notion import sync_pending_to_notion

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()

# =============================================================================
# BACKGROUND JOBS
#
# The scheduler runs 6 jobs to keep the system processing:
#   1. sync_and_process    - Full Telegram sync + batch processing (every N hours)
#   2. process_pending_only - Process pending messages only (every 2 min)
#   3. retry_failed_messages - Re-queue failed messages for retry (every 15 min)
#   4. cleanup_stale_processing - Reset stuck PROCESSING status (every 1 hour)
#   5. log_daily_stats     - Log statistics at midnight
#   6. notion_sync_job     - Push unsynced digest items to Notion (every 5 min)
#
# See PROCESSING.md for full documentation.
# =============================================================================


async def sync_and_process():
    """
    Backlog drain: process up to 200 PENDING/RETRY messages in batches of 20.

    Bot ingest is handled by start_bot_manager()'s continuous getUpdates polling
    (in src/services/telegram_bot.py). This job complements process_pending_only
    (which handles 10 at a time every 2 min) for clearing larger backlogs after
    AI/Telegram outages clear.

    Runs every sync_interval_hours (default 6h).
    """
    logger.info("Starting scheduled backlog drain")
    database.get_engine()
    db = database.SessionLocal()
    try:
        processor = MessageProcessor()
        processed = 0
        batch_size = 20
        max_batches = 10  # 20 * 10 = 200 messages max per run
        for _ in range(max_batches):
            batch_processed = await processor.process_batch(db, limit=batch_size, include_retries=True)
            processed += batch_processed
            # Another worker may claim part of a batch. Partial progress does
            # not mean the queue is empty; stop when no further work succeeds
            # (including when the processor reaches its request limit).
            if batch_processed == 0:
                break
            await asyncio.sleep(5)
        logger.info(f"Drained {processed} messages")
    except Exception as e:
        logger.error(f"Error in scheduled drain: {e}")
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
    
    database.get_engine()
    db = database.SessionLocal()
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
        
        ai = stats.get('ai', {})
        logger.info(
            "\n📊 Daily Statistics:"
            f"\n   Total Messages: {stats['total_messages']}"
            f"\n   Processed: {stats['success']} ({stats['success_rate']}%)"
            f"\n   Pending: {stats['pending']}"
            f"\n   Failed: {stats['failed']}"
            f"\n   Retry Queue: {stats['retry_queued']}"
            f"\n   AI: {ai.get('provider')} / {ai.get('model')}"
            f" — {ai.get('rate_limit_remaining')}/{ai.get('rate_limit_max')} hourly remaining"
        )
    except Exception as e:
        logger.error(f"Error logging daily stats: {e}")
    finally:
        db.close()

async def notion_sync_job():
    """
    Push unsynced digest items to their Notion databases.

    Runs every 5 minutes. Each (summary_id, item_index) pair is idempotent:
    already-created pages are tracked in notion_pages and skipped on re-runs.
    Failures are left unsynced and retried on the next cycle.
    """
    database.get_engine()
    db = database.SessionLocal()
    try:
        result = await sync_pending_to_notion(db)
        if result.get("synced", 0) > 0:
            logger.info(f"✅ Notion sync job: {result['synced']} items pushed to Notion")
    except Exception as e:
        logger.error(f"❌ Error in notion_sync_job: {e}")
        db.rollback()
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
    - notion_sync: Push unsynced digest items to Notion (every 5 min)
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

    # JOB 6: Notion sync - push unsynced digest items to Notion databases
    if settings.enable_notion_sync:
        scheduler.add_job(
            notion_sync_job,
            trigger=IntervalTrigger(minutes=5),
            id="notion_sync",
            name="Notion Sync",
            replace_existing=True
        )
    elif scheduler.get_job("notion_sync"):
        scheduler.remove_job("notion_sync")

    # Only start if not already running (prevents issues on container restarts)
    if not scheduler.running:
        scheduler.start()
        logger.info(f"✅ Scheduler started with {settings.sync_interval_hours} hour sync interval")
    else:
        logger.info(f"✅ Scheduler already running with {settings.sync_interval_hours} hour sync interval")

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
