"""
Export API for Grant Wire data.

Provides CSV, JSON, and RSS/Atom feeds for exporting entries.
"""

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse, JSONResponse, Response
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, or_, desc
from typing import Optional
from datetime import datetime
import csv
import io
import json

from src.models.database import get_db
from src.models.models import Message, Summary, Category, ChannelConfig, ProcessingStatus, ConfidenceLevel

router = APIRouter()


def get_source_url(msg: Message) -> str:
    """Extract the source URL from message entities or extracted_urls"""
    if msg.summary and msg.summary.entities:
        source = msg.summary.entities.get('source', {})
        if isinstance(source, dict) and source.get('url'):
            return source['url']
    if msg.extracted_urls:
        return msg.extracted_urls[0]
    return ""

def get_channel_filter(db: Session, channel: str):
    """Get channel_id from channel parameter (ID, username, or title)"""
    from sqlalchemy import or_
    config = db.query(ChannelConfig).filter(
        or_(
            ChannelConfig.channel_id == channel,
            ChannelConfig.channel_username.ilike(channel.lstrip('@')),
            ChannelConfig.channel_title.ilike(f"%{channel}%")
        )
    ).first()
    return config.channel_id if config else channel


def build_query(
    db: Session,
    q: Optional[str] = None,
    category_id: Optional[int] = None,
    channel: Optional[str] = None,
    wire: Optional[str] = None,
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
    
    if channel:
        channel_id = get_channel_filter(db, channel)
        query = query.filter(Message.channel_id == channel_id)
    
    if wire:
        wire_slug = wire.lower().strip()
        query = query.join(Summary, isouter=True).filter(Summary.wire == wire_slug)
    
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
            "orgs": "",
            "amounts": "",
            "dates": "",
            "key_terms": ""
        }
    
    # Dual-read: prefer orgs, fallback to protocols for migration
    orgs = entities.get("orgs", []) or entities.get("protocols", [])
    return {
        "orgs": "; ".join(orgs) if orgs else "",
        "amounts": "; ".join(entities.get("amounts", [])),
        "dates": "; ".join(entities.get("dates", [])),
        "key_terms": "; ".join(entities.get("key_terms", []))
    }

@router.get("/csv")
async def export_csv(
    q: Optional[str] = Query(None),
    category_id: Optional[int] = Query(None),
    channel: Optional[str] = Query(None, description="Filter by channel (ID, username, or title)"),
    wire: Optional[str] = Query(None, description="Filter by wire vertical (crypto, ai, oss)"),
    confidence: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    db: Session = Depends(get_db)
):
    """Export messages as CSV with flattened entities"""
    query = build_query(db, q, category_id, channel, wire, confidence, status, date_from, date_to)
    messages = query.all()
    
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "id", "telegram_id", "channel_id", "timestamp", "raw_content", 
            "urls", "summary", "category", "confidence", 
            "processing_status", "orgs", "amounts", "dates", "key_terms"
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
    channel: Optional[str] = Query(None, description="Filter by channel (ID, username, or title)"),
    wire: Optional[str] = Query(None, description="Filter by wire vertical (crypto, ai, oss)"),
    confidence: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    db: Session = Depends(get_db)
):
    """Export messages as JSON"""
    query = build_query(db, q, category_id, channel, wire, confidence, status, date_from, date_to)
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


# =============================================================================
# RSS/Atom Feeds
# =============================================================================

@router.get("/rss")
async def export_rss(
    request: Request,
    category: Optional[str] = Query(default=None, description="Filter by category name"),
    channel: Optional[str] = Query(default=None, description="Filter by channel (ID, username, or title)"),
    wire: Optional[str] = Query(default=None, description="Filter by wire vertical (crypto, ai, oss)"),
    limit: int = Query(default=50, ge=1, le=200, description="Number of entries"),
    db: Session = Depends(get_db)
):
    """
    RSS 2.0 feed of recent Grant Wire entries.

    Subscribe to this feed in your RSS reader to get updates on new grants.
    """
    # Get base URL for links
    base_url = str(request.base_url).rstrip('/')

    # Query production-ready entries
    query = db.query(Message).join(Summary).options(
        joinedload(Message.summary).joinedload(Summary.category)
    ).filter(
        Summary.processing_status == ProcessingStatus.SUCCESS,
        Summary.data_quality == 'complete'
    )

    if channel:
        channel_id = get_channel_filter(db, channel)
        query = query.filter(Message.channel_id == channel_id)

    if wire:
        query = query.filter(Summary.wire == wire.lower().strip())

    if category:
        query = query.join(Category).filter(Category.name.ilike(category))

    messages = query.order_by(desc(Message.timestamp)).limit(limit).all()

    # Build RSS XML
    rss_items = []
    for msg in messages:
        title = msg.summary.title if msg.summary and msg.summary.title else "Untitled"
        description = msg.summary.summary if msg.summary and msg.summary.summary else msg.raw_content or ""
        source_url = get_source_url(msg)
        pub_date = msg.timestamp.strftime("%a, %d %b %Y %H:%M:%S +0000") if msg.timestamp else ""
        category_name = msg.summary.category.name if msg.summary and msg.summary.category else ""

        item = f"""    <item>
      <title><![CDATA[{title}]]></title>
      <description><![CDATA[{description}]]></description>
      <link>{source_url}</link>
      <guid isPermaLink="false">grantwire-{msg.id}</guid>
      <pubDate>{pub_date}</pubDate>
      <category>{category_name}</category>
    </item>"""
        rss_items.append(item)

    rss_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>Grant Wire - Crypto Grants Feed</title>
    <link>{base_url}</link>
    <description>Latest crypto grants, funding opportunities, and governance proposals from The Grant Wire</description>
    <language>en-us</language>
    <lastBuildDate>{datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S +0000")}</lastBuildDate>
    <atom:link href="{base_url}/api/export/rss" rel="self" type="application/rss+xml"/>
{chr(10).join(rss_items)}
  </channel>
</rss>"""

    return Response(
        content=rss_content,
        media_type="application/rss+xml",
        headers={"Content-Type": "application/rss+xml; charset=utf-8"}
    )


@router.get("/atom")
async def export_atom(
    request: Request,
    category: Optional[str] = Query(default=None, description="Filter by category name"),
    channel: Optional[str] = Query(default=None, description="Filter by channel (ID, username, or title)"),
    wire: Optional[str] = Query(default=None, description="Filter by wire vertical (crypto, ai, oss)"),
    limit: int = Query(default=50, ge=1, le=200, description="Number of entries"),
    db: Session = Depends(get_db)
):
    """
    Atom 1.0 feed of recent Grant Wire entries.

    Subscribe to this feed in your RSS reader to get updates on new grants.
    """
    base_url = str(request.base_url).rstrip('/')

    query = db.query(Message).join(Summary).options(
        joinedload(Message.summary).joinedload(Summary.category)
    ).filter(
        Summary.processing_status == ProcessingStatus.SUCCESS,
        Summary.data_quality == 'complete'
    )

    if channel:
        channel_id = get_channel_filter(db, channel)
        query = query.filter(Message.channel_id == channel_id)

    if wire:
        query = query.filter(Summary.wire == wire.lower().strip())

    if category:
        query = query.join(Category).filter(Category.name.ilike(category))

    messages = query.order_by(desc(Message.timestamp)).limit(limit).all()

    # Build Atom XML
    atom_entries = []
    for msg in messages:
        title = msg.summary.title if msg.summary and msg.summary.title else "Untitled"
        summary = msg.summary.summary if msg.summary and msg.summary.summary else msg.raw_content or ""
        source_url = get_source_url(msg)
        updated = msg.timestamp.strftime("%Y-%m-%dT%H:%M:%SZ") if msg.timestamp else ""
        category_name = msg.summary.category.name if msg.summary and msg.summary.category else ""

        entry = f"""  <entry>
    <title><![CDATA[{title}]]></title>
    <summary><![CDATA[{summary}]]></summary>
    <link href="{source_url}"/>
    <id>urn:grantwire:{msg.id}</id>
    <updated>{updated}</updated>
    <category term="{category_name}"/>
  </entry>"""
        atom_entries.append(entry)

    atom_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Grant Wire - Crypto Grants Feed</title>
  <link href="{base_url}"/>
  <link href="{base_url}/api/export/atom" rel="self"/>
  <id>urn:grantwire:feed</id>
  <updated>{datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")}</updated>
  <subtitle>Latest crypto grants, funding opportunities, and governance proposals from The Grant Wire</subtitle>
{chr(10).join(atom_entries)}
</feed>"""

    return Response(
        content=atom_content,
        media_type="application/atom+xml",
        headers={"Content-Type": "application/atom+xml; charset=utf-8"}
    )


# =============================================================================
# Production-Ready Exports
# =============================================================================

@router.get("/production/csv")
async def export_production_csv(
    category: Optional[str] = Query(default=None, description="Filter by category name"),
    channel: Optional[str] = Query(default=None, description="Filter by channel (ID, username, or title)"),
    wire: Optional[str] = Query(default=None, description="Filter by wire vertical (crypto, ai, oss)"),
    org: Optional[str] = Query(default=None, description="Filter by organization name"),
    date_from: Optional[datetime] = Query(default=None, description="Filter from date"),
    date_to: Optional[datetime] = Query(default=None, description="Filter to date"),
    db: Session = Depends(get_db)
):
    """
    Export production-ready entries as CSV.

    Only includes entries with data_quality='complete'.
    Includes title and source_url fields.
    """
    query = db.query(Message).join(Summary).options(
        joinedload(Message.summary).joinedload(Summary.category)
    ).filter(
        Summary.processing_status == ProcessingStatus.SUCCESS,
        Summary.data_quality == 'complete'
    )

    if channel:
        channel_id = get_channel_filter(db, channel)
        query = query.filter(Message.channel_id == channel_id)

    if wire:
        query = query.filter(Summary.wire == wire.lower().strip())

    if category:
        query = query.join(Category).filter(Category.name.ilike(category))

    # Org filter (dual-read for migration)
    if org:
        query = query.filter(
            or_(
                Summary.entities['orgs'].astext.ilike(f'%{org}%'),
                Summary.entities['protocols'].astext.ilike(f'%{org}%')
            )
        )

    if date_from:
        query = query.filter(Message.timestamp >= date_from)
    if date_to:
        query = query.filter(Message.timestamp <= date_to)

    messages = query.order_by(desc(Message.timestamp)).all()

    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "id", "title", "summary", "category", "source_url", "source_name",
            "source_type", "orgs", "amounts", "published_at"
        ]
    )
    writer.writeheader()

    for msg in messages:
        source = {}
        orgs = []
        amounts = []

        if msg.summary and msg.summary.entities:
            source = msg.summary.entities.get('source', {}) or {}
            # Dual-read: prefer orgs, fallback to protocols
            orgs = msg.summary.entities.get('orgs', []) or msg.summary.entities.get('protocols', []) or []
            amounts = msg.summary.entities.get('amounts', []) or []

        row = {
            "id": msg.id,
            "title": msg.summary.title if msg.summary else "",
            "summary": msg.summary.summary if msg.summary else "",
            "category": msg.summary.category.name if msg.summary and msg.summary.category else "",
            "source_url": source.get('url', '') if isinstance(source, dict) else get_source_url(msg),
            "source_name": source.get('name', '') if isinstance(source, dict) else "",
            "source_type": source.get('type', '') if isinstance(source, dict) else "",
            "orgs": "; ".join(orgs) if isinstance(orgs, list) else "",
            "amounts": "; ".join(amounts) if isinstance(amounts, list) else "",
            "published_at": msg.timestamp.isoformat() if msg.timestamp else ""
        }
        writer.writerow(row)

    output.seek(0)

    return StreamingResponse(
        io.BytesIO(output.getvalue().encode('utf-8')),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=grantwire_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        }
    )


@router.get("/production/json")
async def export_production_json(
    category: Optional[str] = Query(default=None, description="Filter by category name"),
    channel: Optional[str] = Query(default=None, description="Filter by channel (ID, username, or title)"),
    wire: Optional[str] = Query(default=None, description="Filter by wire vertical (crypto, ai, oss)"),
    org: Optional[str] = Query(default=None, description="Filter by organization name"),
    date_from: Optional[datetime] = Query(default=None, description="Filter from date"),
    date_to: Optional[datetime] = Query(default=None, description="Filter to date"),
    db: Session = Depends(get_db)
):
    """
    Export production-ready entries as JSON.

    Only includes entries with data_quality='complete'.
    Clean format matching the entries API.
    """
    query = db.query(Message).join(Summary).options(
        joinedload(Message.summary).joinedload(Summary.category)
    ).filter(
        Summary.processing_status == ProcessingStatus.SUCCESS,
        Summary.data_quality == 'complete'
    )

    if channel:
        channel_id = get_channel_filter(db, channel)
        query = query.filter(Message.channel_id == channel_id)

    if wire:
        query = query.filter(Summary.wire == wire.lower().strip())

    if category:
        query = query.join(Category).filter(Category.name.ilike(category))

    # Org filter (dual-read for migration)
    if org:
        query = query.filter(
            or_(
                Summary.entities['orgs'].astext.ilike(f'%{org}%'),
                Summary.entities['protocols'].astext.ilike(f'%{org}%')
            )
        )

    if date_from:
        query = query.filter(Message.timestamp >= date_from)
    if date_to:
        query = query.filter(Message.timestamp <= date_to)

    messages = query.order_by(desc(Message.timestamp)).all()

    entries = []
    for msg in messages:
        source = {}
        orgs = []
        amounts = []

        if msg.summary and msg.summary.entities:
            source = msg.summary.entities.get('source', {}) or {}
            # Dual-read: prefer orgs, fallback to protocols
            orgs = msg.summary.entities.get('orgs', []) or msg.summary.entities.get('protocols', []) or []
            amounts = msg.summary.entities.get('amounts', []) or []

        entry = {
            "id": msg.id,
            "title": msg.summary.title if msg.summary else None,
            "summary": msg.summary.summary if msg.summary else None,
            "category": msg.summary.category.name if msg.summary and msg.summary.category else None,
            "source": {
                "url": source.get('url') if isinstance(source, dict) else get_source_url(msg),
                "name": source.get('name') if isinstance(source, dict) else None,
                "type": source.get('type') if isinstance(source, dict) else None
            },
            "orgs": orgs if isinstance(orgs, list) else [],
            "amounts": amounts if isinstance(amounts, list) else [],
            "published_at": msg.timestamp.isoformat() if msg.timestamp else None
        }
        entries.append(entry)

    return JSONResponse(
        content={
            "export_date": datetime.utcnow().isoformat(),
            "total_entries": len(entries),
            "filters": {
                "category": category,
                "org": org,
                "date_from": date_from.isoformat() if date_from else None,
                "date_to": date_to.isoformat() if date_to else None
            },
            "entries": entries
        },
        headers={
            "Content-Disposition": f"attachment; filename=grantwire_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        }
    )
