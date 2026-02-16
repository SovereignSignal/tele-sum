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
from src.models.models import Message, Summary, ChannelConfig, ProcessingStatus

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
    is_active: bool
    total_messages: int
    complete_entries: int
    pending_entries: int
    latest_message_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True


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
