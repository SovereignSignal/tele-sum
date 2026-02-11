from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import logging

from src.models.database import get_db
from src.models.models import TelegramBot, ChannelConfig, SyncStatus
from src.services.telegram_hybrid import HybridTelegramManager
from src.services.processor import MessageProcessor
from src.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

# Global manager instance
hybrid_manager = HybridTelegramManager()

class BotRegisterRequest(BaseModel):
    bot_token: str
    
class BotSetupRequest(BaseModel):
    bot_token: str
    channel_identifier: str  # Can be @username or chat_id
    wire: Optional[str] = None  # Optional: 'crypto', 'ai', or 'oss'

class HistoricalImportRequest(BaseModel):
    channel_username: str
    phone: Optional[str] = None
    api_id: Optional[int] = None
    api_hash: Optional[str] = None
    
class TransitionRequest(BaseModel):
    channel_id: str
    bot_token: str

class BotInfo(BaseModel):
    id: int
    username: str
    name: str
    is_active: bool
    channels: List[Dict[str, Any]]

@router.post("/bot/register")
async def register_bot(
    request: BotRegisterRequest,
    db: Session = Depends(get_db)
):
    """Register a new bot in the system"""
    result = await hybrid_manager.bot_manager.register_bot(request.bot_token)
    
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result.get("error", "Failed to register bot"))
    
    return {
        "message": "Bot registered successfully",
        "bot_id": result["bot_id"],
        "username": result["username"],
        "name": result["name"]
    }

@router.post("/bot/validate")
async def validate_bot(request: BotRegisterRequest):
    """Validate a bot token without registering"""
    from src.services.telegram_bot import TelegramBotService
    
    service = TelegramBotService(request.bot_token)
    result = await service.validate_token()
    
    if not result["valid"]:
        raise HTTPException(status_code=400, detail=result.get("error", "Invalid bot token"))
    
    return {
        "valid": True,
        "username": result["username"],
        "name": result["name"],
        "can_read_all_group_messages": result.get("can_read_all_group_messages", False)
    }

@router.post("/bot/check-access")
async def check_bot_access(
    channel_id: str,
    bot_token: str
):
    """Check if a bot has access to a channel"""
    from src.services.telegram_bot import TelegramBotService
    
    service = TelegramBotService(bot_token)
    result = await service.check_channel_access(channel_id)
    
    if not result["has_access"]:
        raise HTTPException(
            status_code=403, 
            detail=f"Bot doesn't have access to channel. Error: {result.get('error', 'Unknown error')}"
        )
    
    return result

@router.get("/bots", response_model=List[BotInfo])
async def list_bots(db: Session = Depends(get_db)):
    """List all registered bots"""
    bots = db.query(TelegramBot).filter(TelegramBot.is_active == True).all()
    
    bot_list = []
    for bot in bots:
        channels = []
        for config in bot.channel_configs:
            channels.append({
                "id": config.channel_id,
                "username": config.channel_username,
                "title": config.channel_title,
                "is_active": config.is_active
            })
        
        bot_list.append(BotInfo(
            id=bot.id,
            username=bot.bot_username or "Unknown",
            name=bot.bot_name or "Unknown",
            is_active=bot.is_active,
            channels=channels
        ))
    
    return bot_list

@router.post("/import/historical")
async def import_historical(
    request: HistoricalImportRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """Import complete channel history using phone authentication"""
    
    # Check if phone auth is enabled
    if not settings.enable_phone_import:
        raise HTTPException(
            status_code=503,
            detail="Phone import is disabled. Set ENABLE_PHONE_IMPORT=true to enable."
        )
    
    # Check if we have phone credentials
    if not request.phone and not settings.is_telegram_configured():
        raise HTTPException(
            status_code=400,
            detail="Phone credentials required. Provide phone, api_id, and api_hash, or set them in environment variables."
        )
    
    # Run import
    result = await hybrid_manager.import_historical(
        db,
        request.channel_username,
        request.phone,
        request.api_id,
        request.api_hash
    )
    
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result.get("error", "Import failed"))
    
    # Start processing in background
    async def process_messages():
        processor = MessageProcessor()
        await processor.process_batch(db, limit=50)
    
    background_tasks.add_task(process_messages)
    
    return {
        "message": "Historical import completed successfully",
        "total_imported": result["total_imported"],
        "channel_id": result["channel_id"],
        "last_message_id": result["last_message_id"]
    }

@router.post("/bot/setup-monitoring")
async def setup_bot_monitoring(
    request: BotSetupRequest,
    db: Session = Depends(get_db)
):
    """Setup bot for ongoing channel monitoring"""
    
    if not settings.enable_bot_mode:
        raise HTTPException(
            status_code=503,
            detail="Bot mode is disabled. Set ENABLE_BOT_MODE=true to enable."
        )
    
    result = await hybrid_manager.setup_bot_monitoring(
        db,
        request.channel_identifier,
        request.bot_token,
        wire=request.wire
    )
    
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result.get("error", "Setup failed"))
    
    return {
        "message": "Bot monitoring setup successfully",
        "bot_id": result["bot_id"],
        "bot_username": result["bot_username"],
        "channel_title": result["channel_title"],
        "is_admin": result["is_admin"]
    }

@router.post("/transition-to-bot")
async def transition_to_bot(
    request: TransitionRequest,
    db: Session = Depends(get_db)
):
    """Transition a channel from phone to bot monitoring"""
    
    result = await hybrid_manager.transition_to_bot(
        db,
        request.channel_id,
        request.bot_token
    )
    
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result.get("error", "Transition failed"))
    
    return {
        "message": "Successfully transitioned to bot monitoring",
        "bot_id": result["bot_id"],
        "bot_username": result["bot_username"]
    }

@router.get("/channels/{channel_id}/status")
async def get_channel_status(
    channel_id: str,
    db: Session = Depends(get_db)
):
    """Get comprehensive status of a channel"""
    
    status = await hybrid_manager.get_channel_status(db, channel_id)
    
    if not status.get("exists"):
        raise HTTPException(
            status_code=404, 
            detail=status.get("error", "Channel not found")
        )
    
    return status

@router.get("/channels")
async def list_channels(db: Session = Depends(get_db)):
    """List all configured channels"""
    
    channels = db.query(ChannelConfig).all()
    
    channel_list = []
    for channel in channels:
        sync_status = db.query(SyncStatus).filter(
            SyncStatus.channel_id == channel.channel_id
        ).first()
        
        channel_list.append({
            "id": channel.channel_id,
            "username": channel.channel_username,
            "title": channel.channel_title,
            "mode": channel.import_mode.value if channel.import_mode else "unknown",
            "is_active": channel.is_active,
            "historical_complete": channel.historical_import_complete,
            "has_bot": channel.bot_id is not None,
            "total_messages": sync_status.total_messages_synced if sync_status else 0,
            "last_sync": sync_status.last_sync_at if sync_status else None
        })
    
    return channel_list

@router.post("/bot/{bot_id}/start")
async def start_bot(
    bot_id: int,
    db: Session = Depends(get_db)
):
    """Start a bot's polling"""
    
    success = await hybrid_manager.bot_manager.start_bot(bot_id)
    
    if not success:
        raise HTTPException(status_code=400, detail="Failed to start bot")
    
    return {"message": f"Bot {bot_id} started successfully"}

@router.post("/bot/{bot_id}/stop")
async def stop_bot(bot_id: int):
    """Stop a bot's polling"""
    
    success = await hybrid_manager.bot_manager.stop_bot(bot_id)
    
    if not success:
        raise HTTPException(status_code=400, detail="Failed to stop bot")
    
    return {"message": f"Bot {bot_id} stopped successfully"}

@router.delete("/bot/{bot_id}")
async def delete_bot(
    bot_id: int,
    db: Session = Depends(get_db)
):
    """Delete a bot from the system"""
    
    # Stop bot if running
    await hybrid_manager.bot_manager.stop_bot(bot_id)
    
    # Delete from database
    bot = db.query(TelegramBot).filter(TelegramBot.id == bot_id).first()
    
    if not bot:
        raise HTTPException(status_code=404, detail="Bot not found")
    
    bot.is_active = False
    db.commit()
    
    return {"message": f"Bot {bot_id} deleted successfully"}

@router.get("/setup/wizard")
async def setup_wizard_status():
    """Get setup wizard status and recommendations"""
    
    status = {
        "database": settings.is_database_configured(),
        "phone_auth": settings.is_telegram_configured(),
        "bot_mode": settings.is_bot_configured(),
        "grok_ai": settings.is_grok_configured(),
        "recommendations": []
    }
    
    if not status["database"]:
        status["recommendations"].append({
            "step": 1,
            "action": "Add PostgreSQL to Railway",
            "description": "Database is required for all operations"
        })
    
    if not status["phone_auth"] and not status["bot_mode"]:
        status["recommendations"].append({
            "step": 2,
            "action": "Configure Telegram",
            "description": "Add either phone auth for historical import or bot tokens for monitoring"
        })
    elif not status["phone_auth"]:
        status["recommendations"].append({
            "step": 2,
            "action": "Optional: Add phone auth",
            "description": "Enable historical message import with TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_PHONE"
        })
    elif not status["bot_mode"]:
        status["recommendations"].append({
            "step": 2,
            "action": "Add bot tokens",
            "description": "Enable ongoing monitoring with TELEGRAM_BOT_TOKENS"
        })
    
    if not status["grok_ai"]:
        status["recommendations"].append({
            "step": 3,
            "action": "Optional: Add Grok AI",
            "description": "Enable AI summaries with GROK_API_KEY"
        })
    
    return status