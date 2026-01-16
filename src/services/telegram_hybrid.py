import logging
from typing import Dict, Optional, Any
from datetime import datetime
from sqlalchemy.orm import Session

from src.services.telegram import TelegramService
from src.services.telegram_bot import TelegramBotService, BotManager
from src.models.models import ChannelConfig, SyncStatus, ImportMode, TelegramBot
from src.config import settings

logger = logging.getLogger(__name__)

class HybridTelegramManager:
    """Manager for hybrid phone/bot Telegram operations"""
    
    def __init__(self):
        self.phone_service = None
        self.bot_manager = BotManager()
        self.active_channels = {}
    
    async def setup_channel(self, db: Session, channel_identifier: str, mode: str = "hybrid") -> Dict[str, Any]:
        """Setup a channel for import with specified mode"""
        try:
            # Check if channel already configured
            existing = db.query(ChannelConfig).filter(
                ChannelConfig.channel_username == channel_identifier
            ).first()
            
            if existing:
                return {
                    "success": False,
                    "error": "Channel already configured",
                    "channel_id": existing.channel_id,
                    "mode": existing.import_mode.value
                }
            
            # Create channel config
            channel_config = ChannelConfig(
                channel_username=channel_identifier,
                import_mode=ImportMode(mode),
                is_active=True
            )
            
            db.add(channel_config)
            db.commit()
            
            return {
                "success": True,
                "channel_config_id": channel_config.id,
                "mode": mode
            }
            
        except Exception as e:
            logger.error(f"Failed to setup channel: {e}")
            db.rollback()
            return {
                "success": False,
                "error": str(e)
            }
    
    async def import_historical(self, db: Session, channel_username: str, 
                               phone: Optional[str] = None,
                               api_id: Optional[int] = None,
                               api_hash: Optional[str] = None) -> Dict[str, Any]:
        """Import complete channel history using phone authentication"""
        try:
            # Get or create channel config
            channel_config = db.query(ChannelConfig).filter(
                ChannelConfig.channel_username == channel_username
            ).first()
            
            if not channel_config:
                channel_config = ChannelConfig(
                    channel_username=channel_username,
                    import_mode=ImportMode.HYBRID,
                    is_active=True
                )
                db.add(channel_config)
                db.flush()
            
            # Check if already imported
            if channel_config.historical_import_complete:
                return {
                    "success": False,
                    "error": "Historical import already completed",
                    "imported_date": channel_config.historical_import_date
                }
            
            # Use provided credentials or fall back to settings
            if not self.phone_service:
                # Temporarily update settings if credentials provided
                original_phone = settings.telegram_phone
                original_api_id = settings.telegram_api_id
                original_api_hash = settings.telegram_api_hash
                
                if phone and api_id and api_hash:
                    settings.telegram_phone = phone
                    settings.telegram_api_id = api_id
                    settings.telegram_api_hash = api_hash
                
                self.phone_service = TelegramService()
                
                # Restore original settings
                if phone and api_id and api_hash:
                    settings.telegram_phone = original_phone
                    settings.telegram_api_id = original_api_id
                    settings.telegram_api_hash = original_api_hash
            
            # Import history
            logger.info(f"Starting historical import for channel: {channel_username}")
            result = await self.phone_service.import_channel_history(db)
            
            # Update channel config
            channel_config.historical_import_complete = True
            channel_config.historical_import_date = datetime.utcnow()
            channel_config.channel_id = result.get("channel_id")
            
            # Get last imported message ID
            sync_status = db.query(SyncStatus).filter(
                SyncStatus.channel_id == result.get("channel_id")
            ).first()
            
            if sync_status:
                channel_config.last_historical_message_id = sync_status.last_message_id
                sync_status.last_phone_sync_id = sync_status.last_message_id
            
            db.commit()
            
            logger.info(f"Historical import completed. Imported {result.get('total_imported')} messages")
            
            return {
                "success": True,
                "total_imported": result.get("total_imported"),
                "channel_id": result.get("channel_id"),
                "last_message_id": channel_config.last_historical_message_id
            }
            
        except Exception as e:
            logger.error(f"Historical import failed: {e}")
            db.rollback()
            return {
                "success": False,
                "error": str(e)
            }
    
    async def setup_bot_monitoring(self, db: Session, channel_id: str, bot_token: str) -> Dict[str, Any]:
        """Setup bot for ongoing monitoring of a channel"""
        try:
            # Register bot if needed
            bot_result = await self.bot_manager.register_bot(bot_token)
            
            if not bot_result["success"]:
                return bot_result
            
            bot_id = bot_result["bot_id"]
            
            # Get bot service
            bot_service = self.bot_manager.bots[bot_id]
            
            # Check channel access
            access_info = await bot_service.check_channel_access(channel_id)

            if not access_info["has_access"]:
                return {
                    "success": False,
                    "error": f"Bot doesn't have access to channel. Please add the bot as an admin to the channel/group.",
                    "bot_username": bot_result["username"]
                }

            resolved_channel_id = access_info.get("chat_id") or channel_id
            
            # Update channel config
            channel_config = db.query(ChannelConfig).filter(
                ChannelConfig.channel_id.in_([resolved_channel_id, channel_id])
            ).first()
            
            if not channel_config:
                channel_config = ChannelConfig(
                    channel_id=resolved_channel_id,
                    channel_username=access_info.get("username"),
                    channel_title=access_info.get("title"),
                    import_mode=ImportMode.BOT,
                    is_active=True
                )
                db.add(channel_config)

            # If an existing config was created with an @username or other alias, normalize to numeric chat_id
            if channel_config.channel_id != resolved_channel_id:
                channel_config.channel_id = resolved_channel_id
            
            channel_config.bot_id = bot_id
            
            # Update sync status for bot transition
            sync_status = db.query(SyncStatus).filter(
                SyncStatus.channel_id == resolved_channel_id
            ).first()
            
            if sync_status:
                sync_status.transition_to_bot_complete = True
                sync_status.last_bot_sync_id = sync_status.last_message_id
            
            db.commit()
            
            # Start bot polling
            await self.bot_manager.start_bot(bot_id)
            
            return {
                "success": True,
                "bot_id": bot_id,
                "bot_username": bot_result["username"],
                "channel_title": access_info.get("title"),
                "is_admin": access_info.get("is_admin", False)
            }
            
        except Exception as e:
            logger.error(f"Failed to setup bot monitoring: {e}")
            db.rollback()
            return {
                "success": False,
                "error": str(e)
            }
    
    async def transition_to_bot(self, db: Session, channel_id: str, bot_token: str) -> Dict[str, Any]:
        """Transition a channel from phone to bot monitoring"""
        try:
            # Get channel config
            channel_config = db.query(ChannelConfig).filter(
                ChannelConfig.channel_id == channel_id
            ).first()
            
            if not channel_config:
                return {
                    "success": False,
                    "error": "Channel not found"
                }
            
            if not channel_config.historical_import_complete:
                return {
                    "success": False,
                    "error": "Historical import not completed. Complete historical import first."
                }
            
            # Setup bot monitoring
            result = await self.setup_bot_monitoring(db, channel_id, bot_token)
            
            if result["success"]:
                # Update import mode
                channel_config.import_mode = ImportMode.HYBRID
                db.commit()
                
                logger.info(f"Successfully transitioned channel {channel_id} to bot monitoring")
            
            return result
            
        except Exception as e:
            logger.error(f"Failed to transition to bot: {e}")
            db.rollback()
            return {
                "success": False,
                "error": str(e)
            }
    
    async def get_channel_status(self, db: Session, channel_id: str) -> Dict[str, Any]:
        """Get comprehensive status of a channel"""
        try:
            channel_config = db.query(ChannelConfig).filter(
                ChannelConfig.channel_id == channel_id
            ).first()
            
            if not channel_config:
                return {
                    "exists": False,
                    "error": "Channel not configured"
                }
            
            sync_status = db.query(SyncStatus).filter(
                SyncStatus.channel_id == channel_id
            ).first()
            
            bot_info = None
            if channel_config.bot_id:
                bot = db.query(TelegramBot).filter(
                    TelegramBot.id == channel_config.bot_id
                ).first()
                if bot:
                    bot_info = {
                        "id": bot.id,
                        "username": bot.bot_username,
                        "is_active": bot.is_active
                    }
            
            return {
                "exists": True,
                "channel_id": channel_id,
                "channel_username": channel_config.channel_username,
                "channel_title": channel_config.channel_title,
                "import_mode": channel_config.import_mode.value,
                "is_active": channel_config.is_active,
                "historical_import": {
                    "complete": channel_config.historical_import_complete,
                    "date": channel_config.historical_import_date,
                    "last_message_id": channel_config.last_historical_message_id
                },
                "bot": bot_info,
                "sync": {
                    "total_messages": sync_status.total_messages_synced if sync_status else 0,
                    "last_sync": sync_status.last_sync_at if sync_status else None,
                    "last_phone_sync_id": sync_status.last_phone_sync_id if sync_status else None,
                    "last_bot_sync_id": sync_status.last_bot_sync_id if sync_status else None,
                    "transition_complete": sync_status.transition_to_bot_complete if sync_status else False
                }
            }
            
        except Exception as e:
            logger.error(f"Failed to get channel status: {e}")
            return {
                "exists": False,
                "error": str(e)
            }
    
    async def shutdown(self):
        """Shutdown all services"""
        logger.info("Shutting down hybrid manager")
        
        # Stop all bots
        await self.bot_manager.stop_all_bots()
        
        # Disconnect phone service if active
        if self.phone_service and hasattr(self.phone_service, 'client'):
            try:
                await self.phone_service.disconnect()
            except:
                pass