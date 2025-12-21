from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List
import asyncio
import json
import logging
from datetime import datetime

from src.models.database import get_db
from src.models.models import Message, Summary, SyncStatus, ProcessingStatus
from src.services.telegram import TelegramService
from src.services.processor import MessageProcessor
from src.utils.url_extractor import extract_urls
from src.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

class ImportStatus(BaseModel):
    channel_id: Optional[str] = None
    is_importing: bool = False
    is_initial_complete: bool = False
    total_messages: int = 0
    processed_messages: int = 0
    pending_messages: int = 0
    failed_messages: int = 0

class TelegramSyncRequest(BaseModel):
    channel: str
    limit: int = 100
    start_date: Optional[str] = None
    end_date: Optional[str] = None

class FileImportResponse(BaseModel):
    success: bool
    count: int = 0
    error: Optional[str] = None
    duplicates: int = 0

class TelegramSyncResponse(BaseModel):
    success: bool
    count: int = 0
    error: Optional[str] = None

# Background task tracking
import_in_progress = False

async def run_import(db: Session):
    """Run Telegram import in background"""
    global import_in_progress
    import_in_progress = True
    
    try:
        telegram_service = TelegramService()
        result = await telegram_service.import_channel_history(db)
        
        # Start processing after import
        processor = MessageProcessor()
        await processor.process_batch(db, limit=50)
        
        return result
    except Exception as e:
        logger.error(f"Import failed: {e}")
        raise e
    finally:
        import_in_progress = False

async def run_processing(db: Session):
    """Process pending messages in background"""
    processor = MessageProcessor()
    
    max_iterations = 100  # Prevent infinite loops
    iteration = 0
    
    while iteration < max_iterations:
        pending_count = db.query(Summary).filter(
            Summary.processing_status.in_([
                ProcessingStatus.PENDING,
                ProcessingStatus.RETRY
            ])
        ).count()
        
        if pending_count == 0:
            break
            
        processed = await processor.process_batch(db, limit=10)
        
        if processed == 0:
            break
        
        iteration += 1
        await asyncio.sleep(2)  # Rate limiting
    
    logger.info(f"Background processing completed after {iteration} iterations")

@router.get("/status", response_model=ImportStatus)
async def get_import_status(db: Session = Depends(get_db)):
    """Get current import and processing status"""
    sync_status = db.query(SyncStatus).first()
    
    if not sync_status:
        return ImportStatus()
    
    total = sync_status.total_messages_synced
    
    processed = db.query(Summary).filter(
        Summary.processing_status == ProcessingStatus.SUCCESS
    ).count()
    
    pending = db.query(Summary).filter(
        Summary.processing_status.in_([
            ProcessingStatus.PENDING,
            ProcessingStatus.PROCESSING,
            ProcessingStatus.RETRY
        ])
    ).count()
    
    failed = db.query(Summary).filter(
        Summary.processing_status == ProcessingStatus.FAILED
    ).count()
    
    return ImportStatus(
        channel_id=sync_status.channel_id,
        is_importing=import_in_progress,
        is_initial_complete=sync_status.is_initial_import_complete,
        total_messages=total,
        processed_messages=processed,
        pending_messages=pending,
        failed_messages=failed
    )

@router.post("/start")
async def start_import(
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """Start Telegram channel import using phone authentication"""
    global import_in_progress
    
    if import_in_progress:
        return {
            "message": "Import already in progress",
            "status": "in_progress"
        }
    
    # Check if Telegram is configured
    if not settings.is_telegram_configured():
        raise HTTPException(
            status_code=503,
            detail={
                "error": "Telegram not configured",
                "message": "Telegram API credentials are not configured. Please set the following environment variables: TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_PHONE, TELEGRAM_CHANNEL_USERNAME",
                "required_vars": ["TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_PHONE", "TELEGRAM_CHANNEL_USERNAME"]
            }
        )
    
    sync_status = db.query(SyncStatus).first()
    
    if sync_status and sync_status.is_initial_import_complete:
        return {
            "message": "Initial import already completed. Use sync endpoint for updates.",
            "status": "already_imported"
        }
    
    try:
        result = await run_import(db)
        
        background_tasks.add_task(run_processing, db)
        
        return {
            "message": "Import started successfully",
            "status": "importing",
            "initial_results": result
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/process")
async def trigger_processing(
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """Trigger processing of pending messages"""
    pending = db.query(Summary).filter(
        Summary.processing_status.in_([
            ProcessingStatus.PENDING,
            ProcessingStatus.RETRY
        ])
    ).count()
    
    if pending == 0:
        return {"message": "No messages to process", "pending": 0}
    
    background_tasks.add_task(run_processing, db)
    
    return {
        "message": "Processing started",
        "pending_messages": pending
    }

@router.post("/file", response_model=FileImportResponse)
async def import_from_file(
    file: UploadFile = File(...),
    background_tasks: BackgroundTasks = None,
    db: Session = Depends(get_db)
):
    """Import messages from a JSON file"""
    if not file.filename.endswith('.json'):
        return FileImportResponse(
            success=False,
            error="Only JSON files are supported"
        )
    
    try:
        content = await file.read()
        data = json.loads(content.decode('utf-8'))
        
        # Support different JSON formats
        messages_data = []
        
        if isinstance(data, list):
            messages_data = data
        elif isinstance(data, dict):
            if 'messages' in data:
                messages_data = data['messages']
            else:
                messages_data = [data]
        
        imported = 0
        duplicates = 0
        
        for msg_data in messages_data:
            # Extract message fields (support multiple formats)
            telegram_id = msg_data.get('telegram_id') or msg_data.get('id') or msg_data.get('message_id')
            
            if not telegram_id:
                continue
            
            # Check for duplicate
            existing = db.query(Message).filter(
                Message.telegram_id == telegram_id
            ).first()
            
            if existing:
                duplicates += 1
                continue
            
            # Parse timestamp
            timestamp = None
            timestamp_str = msg_data.get('timestamp') or msg_data.get('date') or msg_data.get('created_at')
            if timestamp_str:
                try:
                    if isinstance(timestamp_str, str):
                        timestamp = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                    elif isinstance(timestamp_str, (int, float)):
                        timestamp = datetime.fromtimestamp(timestamp_str)
                except:
                    timestamp = datetime.utcnow()
            else:
                timestamp = datetime.utcnow()
            
            # Get content
            raw_content = msg_data.get('raw_content') or msg_data.get('text') or msg_data.get('content') or msg_data.get('message') or ''
            
            if not raw_content:
                continue
            
            # Extract URLs
            urls = msg_data.get('extracted_urls') or msg_data.get('urls') or extract_urls(raw_content)
            
            # Get channel ID
            channel_id = msg_data.get('channel_id') or msg_data.get('chat_id') or 'imported'
            
            # Create message
            message = Message(
                telegram_id=telegram_id,
                channel_id=str(channel_id),
                timestamp=timestamp,
                raw_content=raw_content,
                extracted_urls=urls if isinstance(urls, list) else [],
                import_method='file'
            )
            
            db.add(message)
            db.flush()
            
            # Create summary record
            summary = Summary(
                message_id=message.id,
                processing_status=ProcessingStatus.PENDING
            )
            db.add(summary)
            
            imported += 1
        
        db.commit()
        
        # Update or create sync status
        sync_status = db.query(SyncStatus).filter(
            SyncStatus.channel_id == 'imported'
        ).first()
        
        if not sync_status:
            sync_status = SyncStatus(
                channel_id='imported',
                total_messages_synced=imported,
                is_initial_import_complete=True,
                last_sync_at=datetime.utcnow()
            )
            db.add(sync_status)
        else:
            sync_status.total_messages_synced += imported
            sync_status.last_sync_at = datetime.utcnow()
        
        db.commit()
        
        # Start background processing if we have messages
        if imported > 0 and background_tasks:
            background_tasks.add_task(run_processing, db)
        
        return FileImportResponse(
            success=True,
            count=imported,
            duplicates=duplicates
        )
        
    except json.JSONDecodeError as e:
        return FileImportResponse(
            success=False,
            error=f"Invalid JSON format: {str(e)}"
        )
    except Exception as e:
        logger.error(f"File import failed: {e}")
        db.rollback()
        return FileImportResponse(
            success=False,
            error=str(e)
        )

@router.post("/telegram", response_model=TelegramSyncResponse)
async def sync_from_telegram(
    request: TelegramSyncRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """Sync messages from Telegram channel"""
    # Check if Telegram is configured
    if not settings.is_telegram_configured():
        return TelegramSyncResponse(
            success=False,
            error="Telegram not configured. Please set TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_PHONE, and TELEGRAM_CHANNEL_USERNAME environment variables."
        )
    
    try:
        telegram_service = TelegramService()
        
        # If channel is specified, temporarily override the setting
        original_channel = settings.telegram_channel_username
        if request.channel and request.channel != original_channel:
            # Note: This is a simplified approach. In production, you'd want proper channel management
            telegram_service.channel_username = request.channel.lstrip('@')
        
        result = await telegram_service.sync_new_messages(db)
        
        # Start background processing
        background_tasks.add_task(run_processing, db)
        
        return TelegramSyncResponse(
            success=True,
            count=result.get('new_messages', 0) or result.get('total_imported', 0)
        )
        
    except Exception as e:
        logger.error(f"Telegram sync failed: {e}")
        return TelegramSyncResponse(
            success=False,
            error=str(e)
        )

@router.post("/sync")
async def sync_new_messages(
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """Sync new messages from configured Telegram channel"""
    if not settings.is_telegram_configured():
        raise HTTPException(
            status_code=503,
            detail="Telegram not configured"
        )
    
    try:
        telegram_service = TelegramService()
        result = await telegram_service.sync_new_messages(db)
        
        background_tasks.add_task(run_processing, db)
        
        return {
            "message": "Sync completed",
            "new_messages": result.get('new_messages', 0),
            "channel_id": result.get('channel_id')
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
