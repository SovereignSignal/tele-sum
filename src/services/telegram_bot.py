import logging
import asyncio
from typing import List, Optional, Dict, Any
from datetime import datetime
from telegram import Bot, Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters
from telegram.error import TelegramError
from sqlalchemy.orm import Session

from src.config import settings
from src.models import database
from src.models.models import Message, Summary, SyncStatus, ProcessingStatus, TelegramBot, ChannelConfig, ImportMode
from src.services.distribution_guard import is_distribution_post
from src.utils.url_extractor import extract_urls

logger = logging.getLogger(__name__)


def _extract_entity_links(msg) -> list:
    """Telegram hyperlink entities as [{"text": anchor, "url": href}].
    Uses parse_entities/parse_caption_entities (UTF-16-safe). Fails open to []."""
    links = []
    for attr in ("parse_entities", "parse_caption_entities"):
        fn = getattr(msg, attr, None)
        if fn is None:
            continue
        if attr == "parse_caption_entities" and not getattr(msg, "caption", None):
            continue
        try:
            for ent, text in fn(types=["text_link", "url"]).items():
                url = getattr(ent, "url", None) if ent.type == "text_link" else text
                if url:
                    links.append({"text": text, "url": url})
        except Exception:
            continue
    return links


class TelegramBotService:
    """Service for handling bot-based Telegram message collection with proper session management"""
    
    def __init__(self, bot_token: str, bot_id: Optional[int] = None):
        self.bot_token = bot_token
        self.bot_id = bot_id
        self.bot = Bot(token=bot_token)
        self.application = None
        self.is_running = False
        
    async def validate_token(self) -> Dict[str, Any]:
        """Validate bot token and get bot info"""
        try:
            bot_info = await self.bot.get_me()
            logger.info(f"Bot validated: @{bot_info.username} ({bot_info.first_name})")
            return {
                "valid": True,
                "username": bot_info.username,
                "name": bot_info.first_name,
                "id": bot_info.id,
                "can_read_all_group_messages": bot_info.can_read_all_group_messages
            }
        except TelegramError as e:
            logger.error(f"Failed to validate bot token: {e}")
            return {
                "valid": False,
                "error": str(e)
            }
    
    async def get_available_chats(self) -> List[Dict[str, Any]]:
        """Get list of chats the bot has access to"""
        try:
            updates = await self.bot.get_updates()
            chats = {}
            
            for update in updates:
                if update.message and update.message.chat:
                    chat = update.message.chat
                    chat_id = str(chat.id)
                    if chat_id not in chats:
                        chats[chat_id] = {
                            "id": chat_id,
                            "title": chat.title or chat.username or "Private Chat",
                            "type": chat.type,
                            "username": chat.username
                        }
            
            return list(chats.values())
        except TelegramError as e:
            logger.error(f"Failed to get available chats: {e}")
            return []
    
    async def check_channel_access(self, channel_id: str) -> Dict[str, Any]:
        """Check if bot has access to a specific channel"""
        try:
            chat = await self.bot.get_chat(channel_id)
            
            # Try to get bot's member status in the chat
            try:
                member = await self.bot.get_chat_member(channel_id, self.bot.id)
                is_admin = member.status in ['administrator', 'creator']
                can_delete = getattr(member, 'can_delete_messages', False) if is_admin else False
            except:
                is_admin = False
                can_delete = False
            
            return {
                "has_access": True,
                "chat_id": str(chat.id),
                "title": chat.title or chat.username,
                "username": chat.username,
                "type": chat.type,
                "is_admin": is_admin,
                "can_delete_messages": can_delete,
                "member_count": getattr(chat, 'member_count', None)
            }
        except TelegramError as e:
            logger.error(f"Bot doesn't have access to channel {channel_id}: {e}")
            return {
                "has_access": False,
                "error": str(e)
            }
    
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle incoming messages from Telegram with fresh database session"""
        # Channel posts come as channel_post, not message
        msg = update.channel_post or update.message
        if not msg:
            return

        logger.info(f"Received {'channel_post' if update.channel_post else 'message'} TG#{msg.message_id} from {msg.chat_id}")

        # Create a fresh database session for each message to avoid stale connections
        database.get_engine()
        db = database.SessionLocal()
        try:
            await self._process_message(msg, db)
        except Exception as e:
            logger.error(f"Failed to handle message: {e}")
            db.rollback()
        finally:
            db.close()

    async def _process_message(self, msg, db: Session):
        """Process a single message with the provided database session"""
        channel_id = str(msg.chat_id)
        
        # Check if we're tracking this channel - first by numeric channel_id
        channel_config = db.query(ChannelConfig).filter(
            ChannelConfig.channel_id == channel_id,
            ChannelConfig.is_active == True
        ).first()
        
        # Fallback: check by username if channel_id didn't match
        # This handles auto-created configs that use @username as placeholder
        chat_username = getattr(msg.chat, 'username', '') or ''
        if not channel_config and chat_username:
            channel_config = db.query(ChannelConfig).filter(
                ChannelConfig.channel_username == chat_username,
                ChannelConfig.is_active == True
            ).first()
            if channel_config:
                # Update to real numeric channel_id for future lookups
                old_id = channel_config.channel_id
                channel_config.channel_id = channel_id
                channel_config.channel_title = getattr(msg.chat, 'title', chat_username)
                db.commit()
                logger.info(f"Updated channel_id for @{chat_username}: {old_id} -> {channel_id}")
        
        # Also check if channel_id starts with @ (placeholder from auto-create)
        if not channel_config:
            channel_config = db.query(ChannelConfig).filter(
                ChannelConfig.channel_id == f"@{chat_username}",
                ChannelConfig.is_active == True
            ).first()
            if channel_config:
                channel_config.channel_id = channel_id
                channel_config.channel_title = getattr(msg.chat, 'title', chat_username)
                db.commit()
                logger.info(f"Resolved placeholder @{chat_username} -> {channel_id}")
        
        if not channel_config:
            logger.debug(f"Ignoring message from untracked channel: {channel_id} (@{chat_username})")
            return
        
        # Check for duplicate
        # Include channel_id in duplicate check for multi-channel support
        existing = db.query(Message).filter(
            Message.telegram_id == msg.message_id,
            Message.channel_id == channel_id
        ).first()
        
        if existing:
            logger.debug(f"Skipping duplicate message: {msg.message_id}")
            return

        # Posts published to this channel by wire-room-ops already exist in
        # summaries via its approve write-through — importing them again would
        # duplicate the entry and burn an AI call (the round-trip loop).
        wire_value = channel_config.wire
        if is_distribution_post(db, msg.message_id, wire_value):
            logger.info(
                f"⏭️ Skipping wire-room-ops distribution post TG#{msg.message_id} "
                f"({wire_value} wire) — already in summaries via write-through"
            )
            return

        # Extract message content
        message_text = msg.text or msg.caption or ""
        if not message_text:
            logger.debug(f"Skipping message without text content: {msg.message_id}")
            return
        
        # Extract URLs
        urls = extract_urls(message_text)
        entity_links = _extract_entity_links(msg)

        # Create message record (inherit wire from channel config)
        message = Message(
            telegram_id=msg.message_id,
            channel_id=channel_id,
            channel_config_id=channel_config.id,
            bot_id=self.bot_id,
            import_method="bot",
            wire=channel_config.wire,
            timestamp=msg.date,
            raw_content=message_text,
            extracted_urls=urls,
            entity_links=entity_links,
        )

        db.add(message)
        db.flush()

        summary = Summary(
            message_id=message.id,
            wire=channel_config.wire,
            processing_status=ProcessingStatus.PENDING,
        )
        db.add(summary)
        
        # Update sync status
        sync_status = db.query(SyncStatus).filter(
            SyncStatus.channel_id == channel_id
        ).first()
        
        if sync_status:
            sync_status.last_bot_sync_id = msg.message_id
            sync_status.last_sync_at = datetime.utcnow()
            sync_status.total_messages_synced += 1
        else:
            # Create new sync status if doesn't exist
            sync_status = SyncStatus(
                channel_id=channel_id,
                last_bot_sync_id=msg.message_id,
                last_sync_at=datetime.utcnow(),
                total_messages_synced=1
            )
            db.add(sync_status)
        
        db.commit()
        logger.info(f"Imported message {msg.message_id} from channel {channel_id}")
    
    async def start_polling(self):
        """Start polling for messages - no db session passed, fresh sessions created per message"""
        if self.is_running:
            logger.warning("Bot polling is already running")
            return
        
        try:
            # Build application
            self.application = Application.builder().token(self.bot_token).build()
            
            # Add message handler - uses self.handle_message which creates fresh sessions
            # Handle ALL updates including channel posts with captions/media
            self.application.add_handler(
                MessageHandler(filters.ALL & ~filters.COMMAND, self.handle_message)
            )
            
            # Initialize and start polling
            await self.application.initialize()
            await self.application.start()
            await self.application.updater.start_polling(
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=False
            )
            
            self.is_running = True
            logger.info(f"Started bot polling for bot ID: {self.bot_id}")
            
        except Exception as e:
            logger.error(f"Failed to start polling: {e}")
            raise
    
    async def stop_polling(self):
        """Stop polling for messages"""
        if not self.is_running:
            logger.warning("Bot polling is not running")
            return
        
        try:
            if self.application:
                await self.application.updater.stop()
                await self.application.stop()
                await self.application.shutdown()
            
            self.is_running = False
            logger.info(f"Stopped bot polling for bot ID: {self.bot_id}")
            
        except Exception as e:
            logger.error(f"Failed to stop polling: {e}")
    
    async def setup_webhook(self, webhook_url: str) -> bool:
        """Setup webhook for receiving updates"""
        try:
            result = await self.bot.set_webhook(url=webhook_url)
            logger.info(f"Webhook set to: {webhook_url}")
            return result
        except TelegramError as e:
            logger.error(f"Failed to set webhook: {e}")
            return False
    
    async def delete_webhook(self) -> bool:
        """Remove webhook"""
        try:
            result = await self.bot.delete_webhook()
            logger.info("Webhook deleted")
            return result
        except TelegramError as e:
            logger.error(f"Failed to delete webhook: {e}")
            return False
    
    async def get_updates_count(self) -> int:
        """Get count of pending updates"""
        try:
            updates = await self.bot.get_updates()
            return len(updates)
        except TelegramError as e:
            logger.error(f"Failed to get updates: {e}")
            return 0


class BotManager:
    """Manager for multiple bot instances"""
    
    def __init__(self):
        self.bots: Dict[int, TelegramBotService] = {}
        self.active_bots: set = set()
    
    async def register_bot(self, bot_token: str) -> Dict[str, Any]:
        """Register a new bot in the system"""
        database.get_engine()
        db = database.SessionLocal()
        try:
            # Validate token
            temp_service = TelegramBotService(bot_token)
            bot_info = await temp_service.validate_token()
            
            if not bot_info["valid"]:
                return {
                    "success": False,
                    "error": bot_info.get("error", "Invalid bot token")
                }
            
            # Check if bot already exists
            existing = db.query(TelegramBot).filter(
                TelegramBot.bot_token == bot_token
            ).first()
            
            if existing:
                # Bot already registered - return success with existing bot_id
                # so setup_bot_monitoring can proceed to configure the channel
                if existing.id not in self.bots:
                    self.bots[existing.id] = TelegramBotService(bot_token, existing.id)
                return {
                    "success": True,
                    "bot_id": existing.id,
                    "username": existing.bot_username,
                    "name": existing.bot_name,
                    "already_registered": True
                }
            
            # Create bot record
            bot_record = TelegramBot(
                bot_token=bot_token,
                bot_username=bot_info["username"],
                bot_name=bot_info["name"],
                is_active=True
            )
            
            db.add(bot_record)
            db.commit()
            
            # Create service instance
            service = TelegramBotService(bot_token, bot_record.id)
            self.bots[bot_record.id] = service
            
            return {
                "success": True,
                "bot_id": bot_record.id,
                "username": bot_info["username"],
                "name": bot_info["name"]
            }
            
        except Exception as e:
            logger.error(f"Failed to register bot: {e}")
            db.rollback()
            return {
                "success": False,
                "error": str(e)
            }
        finally:
            db.close()
    
    async def start_bot(self, bot_id: int) -> bool:
        """Start a bot's polling"""
        database.get_engine()
        db = database.SessionLocal()
        try:
            if bot_id in self.active_bots:
                logger.warning(f"Bot {bot_id} is already active")
                return True
            
            # Get bot from database
            bot_record = db.query(TelegramBot).filter(
                TelegramBot.id == bot_id
            ).first()
            
            if not bot_record:
                logger.error(f"Bot {bot_id} not found")
                return False
            
            # Create service if not exists
            if bot_id not in self.bots:
                self.bots[bot_id] = TelegramBotService(bot_record.bot_token, bot_id)
            
            # Start polling - no db session passed
            await self.bots[bot_id].start_polling()
            self.active_bots.add(bot_id)
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to start bot {bot_id}: {e}")
            return False
        finally:
            db.close()
    
    async def stop_bot(self, bot_id: int) -> bool:
        """Stop a bot's polling"""
        try:
            if bot_id not in self.active_bots:
                logger.warning(f"Bot {bot_id} is not active")
                return True
            
            if bot_id in self.bots:
                await self.bots[bot_id].stop_polling()
                self.active_bots.discard(bot_id)
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to stop bot {bot_id}: {e}")
            return False
    
    async def stop_all_bots(self):
        """Stop all active bots"""
        for bot_id in list(self.active_bots):
            await self.stop_bot(bot_id)
    
    async def start_all_active_bots(self):
        """Start all active bots from database"""
        database.get_engine()
        db = database.SessionLocal()
        try:
            active_bots = db.query(TelegramBot).filter(
                TelegramBot.is_active == True
            ).all()
            
            for bot in active_bots:
                if bot.id not in self.bots:
                    self.bots[bot.id] = TelegramBotService(bot.bot_token, bot.id)
                await self.start_bot(bot.id)
                
        finally:
            db.close()
    
    def get_status(self) -> Dict[str, Any]:
        """Get status of all managed bots"""
        return {
            "total_bots": len(self.bots),
            "active_count": len(self.active_bots),
            "bots": [
                {
                    "id": bot_id,
                    "is_running": bot.is_running
                }
                for bot_id, bot in self.bots.items()
            ]
        }


# Global bot manager instance
bot_manager = BotManager()

async def start_bot_manager():
    """Start the global bot manager"""
    await bot_manager.start_all_active_bots()

async def stop_bot_manager():
    """Stop the global bot manager"""
    await bot_manager.stop_all_bots()

def get_bot_manager_status():
    """Get bot manager status"""
    return bot_manager.get_status()
