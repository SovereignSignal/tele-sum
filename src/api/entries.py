"""
Public API for Grant Wire entries.

This module provides clean, production-ready endpoints for accessing
Grant Wire data. All endpoints filter by data_quality='complete' by default.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import desc, func, or_, and_, text
from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel

from src.models.database import get_db
from src.models.models import Message, Summary, Category, ProcessingStatus

router = APIRouter()


# =============================================================================
# Response Models
# =============================================================================

class SourceInfo(BaseModel):
    url: Optional[str] = None
    name: Optional[str] = None
    type: Optional[str] = None

class EntryResponse(BaseModel):
    """A single Grant Wire entry"""
    id: int
    title: Optional[str] = None
    summary: Optional[str] = None
    category: Optional[str] = None
    source: SourceInfo
    url: Optional[str] = None
    protocols: List[str] = []
    amounts: List[str] = []
    published_at: datetime

    class Config:
        from_attributes = True

class EntryListResponse(BaseModel):
    """Paginated list of entries"""
    total: int
    page: int
    per_page: int
    pages: int
    entries: List[EntryResponse]

class ProtocolResponse(BaseModel):
    """Protocol with entry count"""
    name: str
    count: int

class CategoryResponse(BaseModel):
    """Category with entry count"""
    id: int
    name: str
    description: Optional[str] = None
    count: int

class StatsResponse(BaseModel):
    """Database statistics"""
    total_entries: int
    complete: int
    incomplete: int
    categories: List[CategoryResponse]
    recent_entries: List[EntryResponse]


# =============================================================================
# Helper Functions
# =============================================================================

def entry_from_message(msg: Message) -> EntryResponse:
    """Convert a Message/Summary to EntryResponse"""
    source_info = SourceInfo()
    url = None
    protocols = []
    amounts = []

    if msg.summary and msg.summary.entities:
        entities = msg.summary.entities

        # Extract source info
        source = entities.get('source', {})
        if isinstance(source, dict):
            source_info = SourceInfo(
                url=source.get('url'),
                name=source.get('name'),
                type=source.get('type')
            )
            url = source.get('url')

        # Extract protocols and amounts
        protocols = entities.get('protocols', [])
        amounts = entities.get('amounts', [])

    # Get URL from extracted_urls if not in entities
    if not url and msg.extracted_urls:
        url = msg.extracted_urls[0] if msg.extracted_urls else None

    return EntryResponse(
        id=msg.id,
        title=msg.summary.title if msg.summary else None,
        summary=msg.summary.summary if msg.summary else None,
        category=msg.summary.category.name if msg.summary and msg.summary.category else None,
        source=source_info,
        url=url,
        protocols=protocols if isinstance(protocols, list) else [],
        amounts=amounts if isinstance(amounts, list) else [],
        published_at=msg.timestamp
    )


# =============================================================================
# Endpoints
# =============================================================================

@router.get("/", response_model=EntryListResponse)
async def list_entries(
    q: Optional[str] = Query(default=None, description="Search in title and summary"),
    category: Optional[str] = Query(default=None, description="Filter by category name"),
    protocol: Optional[str] = Query(default=None, description="Filter by protocol name"),
    date_from: Optional[datetime] = Query(default=None, description="Filter from date (ISO format)"),
    date_to: Optional[datetime] = Query(default=None, description="Filter to date (ISO format)"),
    page: int = Query(default=1, ge=1, description="Page number"),
    per_page: int = Query(default=20, ge=1, le=100, description="Entries per page"),
    db: Session = Depends(get_db)
):
    """
    List all production-ready Grant Wire entries.

    Returns paginated entries filtered by data_quality='complete'.
    """
    # Base query - only complete entries
    query = db.query(Message).join(Summary).options(
        joinedload(Message.summary).joinedload(Summary.category)
    ).filter(
        Summary.processing_status == ProcessingStatus.SUCCESS,
        Summary.data_quality == 'complete'
    )

    # Search filter
    if q:
        search_term = f"%{q}%"
        query = query.filter(
            or_(
                Summary.title.ilike(search_term),
                Summary.summary.ilike(search_term),
                Message.raw_content.ilike(search_term)
            )
        )

    # Category filter
    if category:
        query = query.join(Category).filter(Category.name.ilike(category))

    # Protocol filter (search in entities JSON)
    if protocol:
        query = query.filter(
            Summary.entities['protocols'].astext.ilike(f'%{protocol}%')
        )

    # Date filters
    if date_from:
        query = query.filter(Message.timestamp >= date_from)
    if date_to:
        query = query.filter(Message.timestamp <= date_to)

    # Get total count
    total = query.count()

    # Calculate pagination
    pages = (total + per_page - 1) // per_page
    offset = (page - 1) * per_page

    # Get entries
    messages = query.order_by(desc(Message.timestamp)).offset(offset).limit(per_page).all()

    return EntryListResponse(
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
        entries=[entry_from_message(msg) for msg in messages]
    )


@router.get("/search", response_model=EntryListResponse)
async def search_entries(
    q: str = Query(..., min_length=2, description="Search query (required)"),
    category: Optional[str] = Query(default=None, description="Filter by category"),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db)
):
    """
    Full-text search across titles, summaries, and content.
    """
    search_term = f"%{q}%"

    query = db.query(Message).join(Summary).options(
        joinedload(Message.summary).joinedload(Summary.category)
    ).filter(
        Summary.processing_status == ProcessingStatus.SUCCESS,
        Summary.data_quality == 'complete',
        or_(
            Summary.title.ilike(search_term),
            Summary.summary.ilike(search_term),
            Message.raw_content.ilike(search_term)
        )
    )

    if category is not None:
        query = query.join(Category).filter(Category.name.ilike(category))

    total = query.count()
    pages = (total + per_page - 1) // per_page
    offset = (page - 1) * per_page

    messages = query.order_by(desc(Message.timestamp)).offset(offset).limit(per_page).all()

    return EntryListResponse(
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
        entries=[entry_from_message(msg) for msg in messages]
    )


@router.get("/recent", response_model=List[EntryResponse])
async def recent_entries(
    limit: int = Query(10, ge=1, le=50, description="Number of entries"),
    db: Session = Depends(get_db)
):
    """
    Get the most recent entries.
    """
    messages = db.query(Message).join(Summary).options(
        joinedload(Message.summary).joinedload(Summary.category)
    ).filter(
        Summary.processing_status == ProcessingStatus.SUCCESS,
        Summary.data_quality == 'complete'
    ).order_by(desc(Message.timestamp)).limit(limit).all()

    return [entry_from_message(msg) for msg in messages]


@router.get("/stats", response_model=StatsResponse)
async def get_stats(db: Session = Depends(get_db)):
    """
    Get database statistics.
    """
    # Count totals
    total = db.query(Summary).filter(
        Summary.processing_status == ProcessingStatus.SUCCESS
    ).count()

    complete = db.query(Summary).filter(
        Summary.processing_status == ProcessingStatus.SUCCESS,
        Summary.data_quality == 'complete'
    ).count()

    incomplete = db.query(Summary).filter(
        Summary.processing_status == ProcessingStatus.SUCCESS,
        Summary.data_quality == 'incomplete'
    ).count()

    # Category breakdown
    categories = db.query(Category).filter(Category.user_id == 1).all()
    category_stats = []

    for cat in categories:
        count = db.query(Summary).filter(
            Summary.category_id == cat.id,
            Summary.processing_status == ProcessingStatus.SUCCESS,
            Summary.data_quality == 'complete'
        ).count()

        category_stats.append(CategoryResponse(
            id=cat.id,
            name=cat.name,
            description=cat.description,
            count=count
        ))

    # Sort by count descending
    category_stats.sort(key=lambda x: x.count, reverse=True)

    # Recent entries
    recent = db.query(Message).join(Summary).options(
        joinedload(Message.summary).joinedload(Summary.category)
    ).filter(
        Summary.processing_status == ProcessingStatus.SUCCESS,
        Summary.data_quality == 'complete'
    ).order_by(desc(Message.timestamp)).limit(5).all()

    return StatsResponse(
        total_entries=total,
        complete=complete,
        incomplete=incomplete,
        categories=category_stats,
        recent_entries=[entry_from_message(msg) for msg in recent]
    )


@router.get("/protocols", response_model=List[ProtocolResponse])
async def list_protocols(
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db)
):
    """
    List all protocols mentioned in entries with counts.

    Note: This extracts protocols from the entities JSON field.
    """
    # Get all summaries with protocols
    summaries = db.query(Summary).filter(
        Summary.processing_status == ProcessingStatus.SUCCESS,
        Summary.data_quality == 'complete',
        Summary.entities.isnot(None)
    ).all()

    # Count protocols
    protocol_counts = {}
    for summary in summaries:
        protocols = summary.entities.get('protocols', []) if summary.entities else []
        for protocol in protocols:
            if protocol:
                protocol_counts[protocol] = protocol_counts.get(protocol, 0) + 1

    # Sort by count and return
    sorted_protocols = sorted(
        protocol_counts.items(),
        key=lambda x: x[1],
        reverse=True
    )[:limit]

    return [
        ProtocolResponse(name=name, count=count)
        for name, count in sorted_protocols
    ]


@router.get("/categories", response_model=List[CategoryResponse])
async def list_categories(db: Session = Depends(get_db)):
    """
    List all categories with entry counts.
    """
    categories = db.query(Category).filter(Category.user_id == 1).all()

    result = []
    for cat in categories:
        count = db.query(Summary).filter(
            Summary.category_id == cat.id,
            Summary.processing_status == ProcessingStatus.SUCCESS,
            Summary.data_quality == 'complete'
        ).count()

        result.append(CategoryResponse(
            id=cat.id,
            name=cat.name,
            description=cat.description,
            count=count
        ))

    result.sort(key=lambda x: x.count, reverse=True)
    return result


@router.get("/category/{category_name}", response_model=EntryListResponse)
async def entries_by_category(
    category_name: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db)
):
    """
    Get all entries in a specific category.
    """
    category = db.query(Category).filter(
        Category.name.ilike(category_name)
    ).first()

    if not category:
        raise HTTPException(status_code=404, detail=f"Category '{category_name}' not found")

    query = db.query(Message).join(Summary).options(
        joinedload(Message.summary).joinedload(Summary.category)
    ).filter(
        Summary.category_id == category.id,
        Summary.processing_status == ProcessingStatus.SUCCESS,
        Summary.data_quality == 'complete'
    )

    total = query.count()
    pages = (total + per_page - 1) // per_page
    offset = (page - 1) * per_page

    messages = query.order_by(desc(Message.timestamp)).offset(offset).limit(per_page).all()

    return EntryListResponse(
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
        entries=[entry_from_message(msg) for msg in messages]
    )


@router.get("/{entry_id}", response_model=EntryResponse)
async def get_entry(
    entry_id: int,
    db: Session = Depends(get_db)
):
    """
    Get a single entry by ID.
    """
    message = db.query(Message).options(
        joinedload(Message.summary).joinedload(Summary.category)
    ).filter(Message.id == entry_id).first()

    if not message:
        raise HTTPException(status_code=404, detail="Entry not found")

    if not message.summary or message.summary.data_quality != 'complete':
        raise HTTPException(status_code=404, detail="Entry not available (incomplete data)")

    return entry_from_message(message)
