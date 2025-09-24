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
    query = build_query(db, q, category_id, confidence, status, date_from, date_to)
    messages = query.all()
    
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "id", "telegram_id", "timestamp", "raw_content", 
            "urls", "summary", "category", "confidence", 
            "processing_status", "entities"
        ]
    )
    writer.writeheader()
    
    for msg in messages:
        row = {
            "id": msg.id,
            "telegram_id": msg.telegram_id,
            "timestamp": msg.timestamp.isoformat(),
            "raw_content": msg.raw_content,
            "urls": "|".join(msg.extracted_urls or []),
            "summary": "",
            "category": "",
            "confidence": "",
            "processing_status": "pending",
            "entities": ""
        }
        
        if msg.summary:
            row["summary"] = msg.summary.summary or ""
            row["processing_status"] = msg.summary.processing_status.value
            row["confidence"] = msg.summary.confidence.value if msg.summary.confidence else ""
            row["entities"] = json.dumps(msg.summary.entities or {})
            
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
    query = build_query(db, q, category_id, confidence, status, date_from, date_to)
    messages = query.all()
    
    data = []
    for msg in messages:
        item = {
            "id": msg.id,
            "telegram_id": msg.telegram_id,
            "timestamp": msg.timestamp.isoformat(),
            "raw_content": msg.raw_content,
            "urls": msg.extracted_urls or [],
            "summary": None,
            "category": None,
            "confidence": None,
            "processing_status": "pending",
            "entities": {}
        }
        
        if msg.summary:
            item["summary"] = msg.summary.summary
            item["processing_status"] = msg.summary.processing_status.value
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