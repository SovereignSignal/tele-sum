from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import or_, and_, desc
from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel

from src.models.database import get_db
from src.models.models import Message, Summary, Category, ProcessingStatus, ConfidenceLevel
from src.services.processor import process_message

router = APIRouter()

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
    
    query = query.order_by(desc(Message.timestamp))
    messages = query.offset(offset).limit(limit).all()
    
    results = []
    for msg in messages:
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
        
        results.append(result)
    
    return results

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
    
    result = MessageResponse(
        id=message.id,
        telegram_id=message.telegram_id,
        timestamp=message.timestamp,
        raw_content=message.raw_content,
        extracted_urls=message.extracted_urls or [],
        processing_status=message.summary.processing_status.value if message.summary else "pending"
    )
    
    if message.summary:
        result.summary = message.summary.summary
        result.entities = message.summary.entities or {}
        result.confidence = message.summary.confidence.value if message.summary.confidence else None
        
        if message.summary.category:
            result.category_name = message.summary.category.name
    
    return result

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
        message.summary.processing_attempts += 1
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
        return {"message": "Message queued for reprocessing"}
    except Exception as e:
        return {"message": f"Failed to queue message: {str(e)}"}