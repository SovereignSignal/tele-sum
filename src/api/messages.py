from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import or_, and_, desc
from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel

from src.models.database import get_db
from src.models.models import Message, Summary, Category, ProcessingStatus, ConfidenceLevel
from src.services.processor import process_message

router = APIRouter()
templates = Jinja2Templates(directory="templates")

class MessageResponse(BaseModel):
    id: int
    telegram_id: int
    timestamp: datetime
    raw_content: str
    extracted_urls: List[str]
    summary: Optional[str] = None
    entities: dict = {}
    category_name: Optional[str] = None
    confidence: Optional[str] = None
    processing_status: str
    
    class Config:
        from_attributes = True

def build_message_query(
    db: Session,
    q: Optional[str] = None,
    category_id: Optional[int] = None,
    confidence: Optional[str] = None,
    status: Optional[str] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None
):
    """Build the base query for messages with filters"""
    query = db.query(Message).options(
        joinedload(Message.summary).joinedload(Summary.category)
    )
    
    if q:
        search_filter = or_(
            Message.raw_content.ilike(f"%{q}%"),
            Summary.summary.ilike(f"%{q}%")
        )
        query = query.join(Summary, isouter=True).filter(search_filter)
    
    if category_id:
        query = query.join(Summary).filter(Summary.category_id == category_id)
    
    if confidence:
        try:
            conf = ConfidenceLevel(confidence)
            query = query.join(Summary).filter(Summary.confidence == conf)
        except ValueError:
            pass
    
    if status:
        try:
            proc_status = ProcessingStatus(status)
            query = query.join(Summary).filter(Summary.processing_status == proc_status)
        except ValueError:
            pass
    
    if date_from:
        query = query.filter(Message.timestamp >= date_from)
    
    if date_to:
        query = query.filter(Message.timestamp <= date_to)
    
    return query.order_by(desc(Message.timestamp))

def message_to_response(msg: Message) -> MessageResponse:
    """Convert a Message model to MessageResponse"""
    result = MessageResponse(
        id=msg.id,
        telegram_id=msg.telegram_id,
        timestamp=msg.timestamp,
        raw_content=msg.raw_content,
        extracted_urls=msg.extracted_urls or [],
        processing_status=msg.summary.processing_status.value if msg.summary else "pending"
    )
    
    if msg.summary:
        result.summary = msg.summary.summary
        result.entities = msg.summary.entities or {}
        result.confidence = msg.summary.confidence.value if msg.summary.confidence else None
        
        if msg.summary.category:
            result.category_name = msg.summary.category.name
    
    return result

@router.get("/html", response_class=HTMLResponse)
async def get_messages_html(
    request: Request,
    q: Optional[str] = Query(None, description="Search query"),
    category_id: Optional[int] = Query(None, description="Filter by category"),
    confidence: Optional[str] = Query(None, description="Filter by confidence level"),
    status: Optional[str] = Query(None, description="Filter by processing status"),
    date_from: Optional[datetime] = Query(None, description="Filter from date"),
    date_to: Optional[datetime] = Query(None, description="Filter to date"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db)
):
    """Get messages as HTML partial for HTMX"""
    query = build_message_query(db, q, category_id, confidence, status, date_from, date_to)
    
    # Get total count for pagination
    total = query.count()
    
    # Get paginated messages
    messages = query.offset(offset).limit(limit).all()
    
    # Convert to response format
    message_list = []
    for msg in messages:
        msg_dict = {
            "id": msg.id,
            "telegram_id": msg.telegram_id,
            "timestamp": msg.timestamp,
            "raw_content": msg.raw_content,
            "extracted_urls": msg.extracted_urls or [],
            "processing_status": msg.summary.processing_status.value if msg.summary else "pending",
            "summary": None,
            "entities": {},
            "confidence": None,
            "category_name": None
        }
        
        if msg.summary:
            msg_dict["summary"] = msg.summary.summary
            msg_dict["entities"] = msg.summary.entities or {}
            msg_dict["confidence"] = msg.summary.confidence.value if msg.summary.confidence else None
            
            if msg.summary.category:
                msg_dict["category_name"] = msg.summary.category.name
        
        message_list.append(msg_dict)
    
    has_more = offset + limit < total
    
    return templates.TemplateResponse(
        "partials/messages.html",
        {
            "request": request,
            "messages": message_list,
            "has_more": has_more,
            "offset": offset,
            "limit": limit,
            "query": q,
            "category_id": category_id,
            "confidence": confidence,
            "status": status
        }
    )

@router.get("/", response_model=List[MessageResponse])
async def search_messages(
    q: Optional[str] = Query(None, description="Search query"),
    category_id: Optional[int] = Query(None, description="Filter by category"),
    confidence: Optional[str] = Query(None, description="Filter by confidence level"),
    status: Optional[str] = Query(None, description="Filter by processing status"),
    date_from: Optional[datetime] = Query(None, description="Filter from date"),
    date_to: Optional[datetime] = Query(None, description="Filter to date"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db)
):
    """Get messages as JSON"""
    query = build_message_query(db, q, category_id, confidence, status, date_from, date_to)
    messages = query.offset(offset).limit(limit).all()
    
    return [message_to_response(msg) for msg in messages]

@router.get("/stats")
async def get_message_stats(db: Session = Depends(get_db)):
    """Get message statistics"""
    total = db.query(Message).count()
    
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
    
    confident = db.query(Summary).filter(
        Summary.confidence == ConfidenceLevel.CONFIDENT
    ).count()
    
    uncertain = db.query(Summary).filter(
        Summary.confidence == ConfidenceLevel.UNCERTAIN
    ).count()
    
    return {
        "total": total,
        "processed": processed,
        "pending": pending,
        "failed": failed,
        "confident": confident,
        "uncertain": uncertain
    }

@router.get("/{message_id}", response_model=MessageResponse)
async def get_message(
    message_id: int,
    db: Session = Depends(get_db)
):
    message = db.query(Message).options(
        joinedload(Message.summary).joinedload(Summary.category)
    ).filter(Message.id == message_id).first()
    
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    
    return message_to_response(message)

@router.post("/{message_id}/reprocess")
async def reprocess_message(
    message_id: int,
    db: Session = Depends(get_db)
):
    message = db.query(Message).filter(Message.id == message_id).first()
    
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    
    if message.summary:
        message.summary.processing_status = ProcessingStatus.RETRY
        message.summary.processing_attempts = 0  # Reset attempts for manual retry
        message.summary.error_log = None
        db.commit()
    else:
        summary = Summary(
            message_id=message.id,
            processing_status=ProcessingStatus.RETRY
        )
        db.add(summary)
        db.commit()
    
    try:
        await process_message(message_id, db)
        return {"message": "Message reprocessed successfully", "status": "success"}
    except Exception as e:
        return {"message": f"Reprocessing queued, will be processed in background: {str(e)}", "status": "queued"}

@router.post("/reprocess-failed")
async def reprocess_all_failed(
    db: Session = Depends(get_db)
):
    """Queue all failed messages for reprocessing"""
    failed_summaries = db.query(Summary).filter(
        Summary.processing_status == ProcessingStatus.FAILED
    ).all()
    
    count = 0
    for summary in failed_summaries:
        summary.processing_status = ProcessingStatus.RETRY
        summary.processing_attempts = 0
        summary.error_log = None
        count += 1
    
    db.commit()
    
    return {
        "message": f"Queued {count} failed messages for reprocessing",
        "count": count
    }
