from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse, JSONResponse
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, or_
from typing import Optional
from datetime import datetime
import csv
import io
import json

from src.models.database import get_db
from src.models.models import Message, Summary, Category, ProcessingStatus, ConfidenceLevel

router = APIRouter()

def build_query(
    db: Session,
    q: Optional[str] = None,
    category_id: Optional[int] = None,
    confidence: Optional[str] = None,
    status: Optional[str] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None
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
    
    return query.order_by(Message.timestamp.desc())

def flatten_entities(entities: dict) -> dict:
    """Flatten entities dictionary for CSV export"""
    if not entities:
        return {
            "protocols": "",
            "amounts": "",
            "dates": "",
            "key_terms": ""
        }
    
    return {
        "protocols": "; ".join(entities.get("protocols", [])),
        "amounts": "; ".join(entities.get("amounts", [])),
        "dates": "; ".join(entities.get("dates", [])),
        "key_terms": "; ".join(entities.get("key_terms", []))
    }

@router.get("/csv")
async def export_csv(
    q: Optional[str] = Query(None),
    category_id: Optional[int] = Query(None),
    confidence: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    db: Session = Depends(get_db)
):
    """Export messages as CSV with flattened entities"""
    query = build_query(db, q, category_id, confidence, status, date_from, date_to)
    messages = query.all()
    
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "id", "telegram_id", "channel_id", "timestamp", "raw_content", 
            "urls", "summary", "category", "confidence", 
            "processing_status", "protocols", "amounts", "dates", "key_terms"
        ]
    )
    writer.writeheader()
    
    for msg in messages:
        entities_flat = flatten_entities(msg.summary.entities if msg.summary else None)
        
        row = {
            "id": msg.id,
            "telegram_id": msg.telegram_id,
            "channel_id": msg.channel_id,
            "timestamp": msg.timestamp.isoformat() if msg.timestamp else "",
            "raw_content": msg.raw_content or "",
            "urls": "; ".join(msg.extracted_urls or []),
            "summary": "",
            "category": "",
            "confidence": "",
            "processing_status": "pending",
            **entities_flat
        }
        
        if msg.summary:
            row["summary"] = msg.summary.summary or ""
            row["processing_status"] = msg.summary.processing_status.value if msg.summary.processing_status else ""
            row["confidence"] = msg.summary.confidence.value if msg.summary.confidence else ""
            
            if msg.summary.category:
                row["category"] = msg.summary.category.name
        
        writer.writerow(row)
    
    output.seek(0)
    
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode('utf-8')),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=telesum_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        }
    )

@router.get("/json")
async def export_json(
    q: Optional[str] = Query(None),
    category_id: Optional[int] = Query(None),
    confidence: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    db: Session = Depends(get_db)
):
    """Export messages as JSON"""
    query = build_query(db, q, category_id, confidence, status, date_from, date_to)
    messages = query.all()
    
    data = []
    for msg in messages:
        item = {
            "id": msg.id,
            "telegram_id": msg.telegram_id,
            "channel_id": msg.channel_id,
            "timestamp": msg.timestamp.isoformat() if msg.timestamp else None,
            "raw_content": msg.raw_content,
            "urls": msg.extracted_urls or [],
            "import_method": msg.import_method,
            "summary": None,
            "category": None,
            "confidence": None,
            "processing_status": "pending",
            "entities": {}
        }
        
        if msg.summary:
            item["summary"] = msg.summary.summary
            item["processing_status"] = msg.summary.processing_status.value if msg.summary.processing_status else None
            item["confidence"] = msg.summary.confidence.value if msg.summary.confidence else None
            item["entities"] = msg.summary.entities or {}
            
            if msg.summary.category:
                item["category"] = {
                    "id": msg.summary.category.id,
                    "name": msg.summary.category.name,
                    "description": msg.summary.category.description
                }
        
        data.append(item)
    
    return JSONResponse(
        content={
            "export_date": datetime.now().isoformat(),
            "total_messages": len(data),
            "filters": {
                "query": q,
                "category_id": category_id,
                "confidence": confidence,
                "status": status,
                "date_from": date_from.isoformat() if date_from else None,
                "date_to": date_to.isoformat() if date_to else None
            },
            "messages": data
        },
        headers={
            "Content-Disposition": f"attachment; filename=telesum_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        }
    )

@router.get("/stats")
async def export_stats(
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    db: Session = Depends(get_db)
):
    """Get export statistics"""
    query = db.query(Message)
    
    if date_from:
        query = query.filter(Message.timestamp >= date_from)
    if date_to:
        query = query.filter(Message.timestamp <= date_to)
    
    total = query.count()
    
    # Get category breakdown
    categories = db.query(Category).filter(Category.user_id == 1).all()
    category_stats = []
    
    for cat in categories:
        count = db.query(Summary).filter(Summary.category_id == cat.id).count()
        category_stats.append({
            "name": cat.name,
            "count": count,
            "percentage": round(count / total * 100, 1) if total > 0 else 0
        })
    
    # Get confidence breakdown
    confident = db.query(Summary).filter(
        Summary.confidence == ConfidenceLevel.CONFIDENT
    ).count()
    
    uncertain = db.query(Summary).filter(
        Summary.confidence == ConfidenceLevel.UNCERTAIN
    ).count()
    
    # Get status breakdown
    success = db.query(Summary).filter(
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
    
    return {
        "total_messages": total,
        "categories": category_stats,
        "confidence": {
            "confident": confident,
            "uncertain": uncertain
        },
        "processing": {
            "success": success,
            "pending": pending,
            "failed": failed
        }
    }
