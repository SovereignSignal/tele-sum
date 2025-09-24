from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
import asyncio

from src.models.database import get_db
from src.models.models import SyncStatus, ProcessingStatus, Summary
from src.services.telegram import TelegramService
from src.services.processor import MessageProcessor

router = APIRouter()

class ImportStatus(BaseModel):
    channel_id: Optional[str] = None
    is_importing: bool = False
    is_initial_complete: bool = False
    total_messages: int = 0
    processed_messages: int = 0
    pending_messages: int = 0
    failed_messages: int = 0

async def run_import(db: Session):
    telegram_service = TelegramService()
    try:
        result = await telegram_service.import_channel_history(db)
        
        processor = MessageProcessor()
        await processor.process_batch(db, limit=50)
        
        return result
    except Exception as e:
        raise e

async def run_processing(db: Session):
    processor = MessageProcessor()
    
    while True:
        pending_count = db.query(Summary).filter(
            Summary.processing_status == ProcessingStatus.PENDING
        ).count()
        
        if pending_count == 0:
            break
            
        processed = await processor.process_batch(db, limit=10)
        
        if processed == 0:
            break
        
        await asyncio.sleep(2)

@router.get("/status", response_model=ImportStatus)
async def get_import_status(db: Session = Depends(get_db)):
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
        is_importing=False,
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