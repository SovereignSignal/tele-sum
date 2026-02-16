"""
Channels API for Grant Wire.

Provides endpoints for listing and managing Telegram channels,
including message counts and channel-filtered queries.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import desc, func
from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel

from src.models.database import get_db
from src.models.models import Message, Summary, ChannelConfig, ProcessingStatus, Wire

router = APIRouter()


# =============================================================================
# Response Models
# =============================================================================

class ChannelResponse(BaseModel):
    """Channel with message statistics"""
    id: int
    channel_id: str
    username: Optional[str] = None
    title: Optional[str] = None
    wire: Optional[str] = None
    is_active: bool
    total_messages: int
    complete_entries: int
    pending_entries: int
    latest_message_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True


class SetWireRequest(BaseModel):
    """Request to set wire value on a channel"""
    wire: str  # 'crypto', 'ai', or 'oss'


class ChannelListResponse(BaseModel):
    """List of channels"""
    total: int
    channels: List[ChannelResponse]


class ChannelStatsResponse(BaseModel):
    """Detailed statistics for a channel"""
    channel: ChannelResponse
    categories: dict  # category_name -> count
    daily_volume: List[dict]  # last 30 days


# =============================================================================
# Endpoints
# =============================================================================

@router.get("/", response_model=ChannelListResponse)
async def list_channels(
    include_inactive: bool = Query(default=False, description="Include inactive channels"),
    db: Session = Depends(get_db)
):
    """
    List all channels with message counts.
    
    Returns channels sorted by total message count (most active first).
    """
    query = db.query(ChannelConfig)
    
    if not include_inactive:
        query = query.filter(ChannelConfig.is_active == True)
    
    channel_configs = query.all()
    
    channels = []
    for config in channel_configs:
        # Count total messages for this channel
        total_messages = db.query(Message).filter(
            Message.channel_id == config.channel_id
        ).count()
        
        # Count complete entries
        complete_entries = db.query(Message).join(Summary).filter(
            Message.channel_id == config.channel_id,
            Summary.processing_status == ProcessingStatus.SUCCESS,
            Summary.data_quality == 'complete'
        ).count()
        
        # Count pending entries
        pending_entries = db.query(Message).join(Summary).filter(
            Message.channel_id == config.channel_id,
            Summary.processing_status.in_([
                ProcessingStatus.PENDING,
                ProcessingStatus.PROCESSING,
                ProcessingStatus.RETRY
            ])
        ).count()
        
        # Get latest message timestamp
        latest_msg = db.query(Message).filter(
            Message.channel_id == config.channel_id
        ).order_by(desc(Message.timestamp)).first()
        
        channels.append(ChannelResponse(
            id=config.id,
            channel_id=config.channel_id,
            username=config.channel_username,
            title=config.channel_title,
            wire=config.wire.value if config.wire else None,
            is_active=config.is_active,
            total_messages=total_messages,
            complete_entries=complete_entries,
            pending_entries=pending_entries,
            latest_message_at=latest_msg.timestamp if latest_msg else None
        ))
    
    # Sort by total messages descending
    channels.sort(key=lambda x: x.total_messages, reverse=True)
    
    return ChannelListResponse(
        total=len(channels),
        channels=channels
    )


@router.get("/discover")
async def discover_channels(db: Session = Depends(get_db)):
    """
    Discover channels that have messages but no ChannelConfig.
    
    Useful for finding channels that need to be configured or backfilled.
    Returns unique channel_ids from messages that don't have a ChannelConfig.
    """
    # Get all unique channel_ids from messages
    message_channels = db.query(Message.channel_id).distinct().all()
    message_channel_ids = {c[0] for c in message_channels}
    
    # Get all configured channel_ids
    config_channels = db.query(ChannelConfig.channel_id).all()
    config_channel_ids = {c[0] for c in config_channels}
    
    # Find unconfigured channels
    unconfigured = message_channel_ids - config_channel_ids
    
    result = []
    for channel_id in unconfigured:
        count = db.query(Message).filter(Message.channel_id == channel_id).count()
        latest = db.query(Message).filter(
            Message.channel_id == channel_id
        ).order_by(desc(Message.timestamp)).first()
        
        result.append({
            "channel_id": channel_id,
            "message_count": count,
            "latest_message_at": latest.timestamp.isoformat() if latest else None
        })
    
    result.sort(key=lambda x: x["message_count"], reverse=True)
    
    return {
        "unconfigured_channels": len(result),
        "channels": result
    }


@router.get("/{channel_id}/stats", response_model=ChannelStatsResponse)
async def get_channel_stats(
    channel_id: str,
    db: Session = Depends(get_db)
):
    """
    Get detailed statistics for a specific channel.
    """
    # Try to find by config ID first, then by channel_id string
    if channel_id.isdigit():
        config = db.query(ChannelConfig).filter(ChannelConfig.id == int(channel_id)).first()
    else:
        config = db.query(ChannelConfig).filter(ChannelConfig.channel_id == channel_id).first()
    
    if not config:
        raise HTTPException(status_code=404, detail=f"Channel '{channel_id}' not found")
    
    # Get basic counts
    total_messages = db.query(Message).filter(
        Message.channel_id == config.channel_id
    ).count()
    
    complete_entries = db.query(Message).join(Summary).filter(
        Message.channel_id == config.channel_id,
        Summary.processing_status == ProcessingStatus.SUCCESS,
        Summary.data_quality == 'complete'
    ).count()
    
    pending_entries = db.query(Message).join(Summary).filter(
        Message.channel_id == config.channel_id,
        Summary.processing_status.in_([
            ProcessingStatus.PENDING,
            ProcessingStatus.PROCESSING,
            ProcessingStatus.RETRY
        ])
    ).count()
    
    latest_msg = db.query(Message).filter(
        Message.channel_id == config.channel_id
    ).order_by(desc(Message.timestamp)).first()
    
    channel_response = ChannelResponse(
        id=config.id,
        channel_id=config.channel_id,
        username=config.channel_username,
        title=config.channel_title,
        is_active=config.is_active,
        total_messages=total_messages,
        complete_entries=complete_entries,
        pending_entries=pending_entries,
        latest_message_at=latest_msg.timestamp if latest_msg else None
    )
    
    # Get category breakdown for this channel
    from src.models.models import Category
    categories = db.query(Category).filter(Category.user_id == 1).all()
    category_counts = {}
    
    for cat in categories:
        count = db.query(Message).join(Summary).filter(
            Message.channel_id == config.channel_id,
            Summary.category_id == cat.id,
            Summary.processing_status == ProcessingStatus.SUCCESS
        ).count()
        if count > 0:
            category_counts[cat.name] = count
    
    # Get daily volume for last 30 days
    from datetime import timedelta
    daily_volume = []
    today = datetime.utcnow().date()
    
    for i in range(30):
        day = today - timedelta(days=i)
        day_start = datetime.combine(day, datetime.min.time())
        day_end = datetime.combine(day, datetime.max.time())
        
        count = db.query(Message).filter(
            Message.channel_id == config.channel_id,
            Message.timestamp >= day_start,
            Message.timestamp <= day_end
        ).count()
        
        daily_volume.append({
            "date": day.isoformat(),
            "count": count
        })
    
    daily_volume.reverse()  # Oldest first
    
    return ChannelStatsResponse(
        channel=channel_response,
        categories=category_counts,
        daily_volume=daily_volume
    )


@router.post("/auto-create")
async def auto_create_channel_configs(db: Session = Depends(get_db)):
    """
    Automatically create ChannelConfig entries for all channels
    that have messages but no configuration.
    
    This is useful for retroactively tagging existing messages.
    """
    # Get all unique channel_ids from messages
    message_channels = db.query(Message.channel_id).distinct().all()
    message_channel_ids = {c[0] for c in message_channels}
    
    # Get all configured channel_ids
    config_channels = db.query(ChannelConfig.channel_id).all()
    config_channel_ids = {c[0] for c in config_channels}
    
    # Find unconfigured channels
    unconfigured = message_channel_ids - config_channel_ids
    
    created = []
    for channel_id in unconfigured:
        # Create a basic config
        config = ChannelConfig(
            channel_id=channel_id,
            channel_title=f"Channel {channel_id}",  # Placeholder title
            is_active=True
        )
        db.add(config)
        created.append(channel_id)
    
    db.commit()
    
    return {
        "created": len(created),
        "channel_ids": created
    }


@router.patch("/{channel_id}/wire")
async def set_channel_wire(
    channel_id: str,
    request: SetWireRequest,
    db: Session = Depends(get_db)
):
    """
    Set the wire vertical (crypto, ai, oss) for a channel.
    
    After setting wire on channels, call /backfill_wire to propagate
    the wire value to existing messages and summaries.
    """
    # Find channel config by ID or channel_id string
    config = None
    if channel_id.isdigit():
        config = db.query(ChannelConfig).filter(ChannelConfig.id == int(channel_id)).first()
    
    if not config:
        config = db.query(ChannelConfig).filter(ChannelConfig.channel_id == channel_id).first()
    
    if not config:
        raise HTTPException(status_code=404, detail=f"Channel not found: {channel_id}")
    
    # Validate and set wire value
    try:
        wire_enum = Wire(request.wire.lower())
        config.wire = wire_enum
        db.commit()
        
        return {
            "success": True,
            "channel_id": config.channel_id,
            "channel_title": config.channel_title,
            "wire": wire_enum.value
        }
    except ValueError:
        raise HTTPException(
            status_code=400, 
            detail=f"Invalid wire value: {request.wire}. Must be 'crypto', 'ai', or 'oss'"
        )


@router.post("/backfill")
async def backfill_channel_links(db: Session = Depends(get_db)):
    """
    Full backfill operation:
    1. Ensures channel_config_id column exists (via raw SQL)
    2. Creates ChannelConfig entries for unconfigured channels
    3. Links all messages to their ChannelConfig
    
    Call this endpoint after deploying channel segmentation updates.
    """
    from sqlalchemy import text
    
    results = {
        "schema_updated": False,
        "configs_created": 0,
        "messages_linked": 0,
        "channels": []
    }
    
    # Step 1: Ensure column exists (safe to run multiple times)
    try:
        db.execute(text("""
            ALTER TABLE messages 
            ADD COLUMN IF NOT EXISTS channel_config_id INTEGER REFERENCES channel_configs(id)
        """))
        db.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_messages_channel_config_id 
            ON messages(channel_config_id)
        """))
        db.commit()
        results["schema_updated"] = True
    except Exception as e:
        # Column might already exist or DB doesn't support IF NOT EXISTS
        db.rollback()
        results["schema_note"] = str(e)
    
    # Step 2: Create missing ChannelConfigs
    message_channels = db.query(Message.channel_id).distinct().all()
    message_channel_ids = {c[0] for c in message_channels if c[0]}
    
    config_channels = db.query(ChannelConfig.channel_id).all()
    config_channel_ids = {c[0] for c in config_channels}
    
    unconfigured = message_channel_ids - config_channel_ids
    
    for channel_id in unconfigured:
        config = ChannelConfig(
            channel_id=channel_id,
            channel_title=f"Channel {channel_id}",
            is_active=True
        )
        db.add(config)
        results["configs_created"] += 1
    
    db.commit()
    
    # Step 3: Link messages to configs
    configs = db.query(ChannelConfig).all()
    config_map = {c.channel_id: c.id for c in configs}
    
    for channel_id, config_id in config_map.items():
        # Count and update unlinked messages
        unlinked_count = db.query(Message).filter(
            Message.channel_id == channel_id,
            Message.channel_config_id.is_(None)
        ).count()
        
        if unlinked_count > 0:
            db.query(Message).filter(
                Message.channel_id == channel_id,
                Message.channel_config_id.is_(None)
            ).update(
                {Message.channel_config_id: config_id},
                synchronize_session=False
            )
            results["messages_linked"] += unlinked_count
            results["channels"].append({
                "channel_id": channel_id,
                "config_id": config_id,
                "messages_linked": unlinked_count
            })
    
    db.commit()
    
    return results


@router.post("/backfill_wire")
async def backfill_wire_values(db: Session = Depends(get_db)):
    """
    Backfill wire values from ChannelConfig to Messages and Summaries.
    
    This endpoint:
    1. Ensures wire column exists on messages and summaries tables
    2. Updates messages.wire based on their channel_config.wire
    3. Updates summaries.wire based on their message.wire
    
    Call this endpoint after setting wire values on channel configs.
    """
    from sqlalchemy import text
    
    results = {
        "schema_updated": False,
        "messages_updated": 0,
        "summaries_updated": 0,
        "channels_processed": []
    }
    
    # Step 1: Ensure wire columns exist (safe to run multiple times)
    try:
        # Check if wire enum type exists, create if not
        db.execute(text("""
            DO $$ BEGIN
                CREATE TYPE wire AS ENUM ('crypto', 'ai', 'oss');
            EXCEPTION
                WHEN duplicate_object THEN null;
            END $$;
        """))
        
        # Add wire column to messages if not exists
        db.execute(text("""
            ALTER TABLE messages 
            ADD COLUMN IF NOT EXISTS wire wire
        """))
        db.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_messages_wire ON messages(wire)
        """))
        
        # Add wire column to summaries if not exists
        db.execute(text("""
            ALTER TABLE summaries 
            ADD COLUMN IF NOT EXISTS wire wire
        """))
        db.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_summaries_wire ON summaries(wire)
        """))
        
        # Add wire column to channel_configs if not exists
        db.execute(text("""
            ALTER TABLE channel_configs 
            ADD COLUMN IF NOT EXISTS wire wire
        """))
        db.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_channel_configs_wire ON channel_configs(wire)
        """))
        
        db.commit()
        results["schema_updated"] = True
    except Exception as e:
        db.rollback()
        results["schema_note"] = str(e)
    
    # Step 2: Update messages.wire from channel_config.wire
    configs_with_wire = db.query(ChannelConfig).filter(
        ChannelConfig.wire.isnot(None)
    ).all()
    
    for config in configs_with_wire:
        # Update messages linked to this channel config
        updated = db.query(Message).filter(
            Message.channel_config_id == config.id,
            Message.wire.is_(None)
        ).update(
            {Message.wire: config.wire},
            synchronize_session=False
        )
        
        if updated > 0:
            results["messages_updated"] += updated
            results["channels_processed"].append({
                "channel_id": config.channel_id,
                "channel_title": config.channel_title,
                "wire": config.wire.value,
                "messages_updated": updated
            })
    
    db.commit()
    
    # Step 3: Update summaries.wire from their message.wire
    summaries_updated = db.execute(text("""
        UPDATE summaries 
        SET wire = messages.wire
        FROM messages 
        WHERE summaries.message_id = messages.id 
        AND messages.wire IS NOT NULL 
        AND summaries.wire IS NULL
    """))
    
    results["summaries_updated"] = summaries_updated.rowcount
    
    db.commit()
    
    return results
