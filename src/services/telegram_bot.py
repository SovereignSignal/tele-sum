import logging
import asyncio
from typing import List, Optional, Dict, Any
from datetime import datetime
from telegram import Bot, Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters
from telegram.error import TelegramError
from sqlalchemy.orm import Session

from src.config import settings
from src.models.models import Message, Summary, SyncStatus, ProcessingStatus, TelegramBot, ChannelConfig, ImportMode
from src.utils.url_extractor import extract_urls

logger = logging.getLogger(__name__)

class TelegramBotService:
    """Service for handling bot-based Telegram message collection"""
    
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
    
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE, db: Session):
        """Handle incoming messages from Telegram"""
        if not update.message:
            return
        
        try:
            msg = update.message
            channel_id = str(msg.chat_id)
            
            # Check if we're tracking this channel
            channel_config = db.query(ChannelConfig).filter(
                ChannelConfig.channel_id == channel_id,
                ChannelConfig.is_active == True
            ).first()
            
            if not channel_config:
                logger.debug(f"Ignoring message from untracked channel: {channel_id}")
                return
            
            # Check for duplicate
            existing = db.query(Message).filter(
                Message.telegram_id == msg.message_id
            ).first()
            
            if existing:
                logger.debug(f"Skipping duplicate message: {msg.message_id}")
                return
            
            # Extract message content
            message_text = msg.text or msg.caption or ""
            if not message_text:
                logger.debug(f"Skipping message without text content: {msg.message_id}")
                return
            
            # Extract URLs
            urls = extract_urls(message_text)
            
            # Create message record
            message = Message(
                telegram_id=msg.message_id,
                channel_id=channel_id,
                bot_id=self.bot_id,
                import_method="bot",
                timestamp=msg.date,
                raw_content=message_text,
                extracted_urls=urls
            )
            
            db.add(message)
            db.flush()
            
            # Create summary record for processing
            summary = Summary(
                message_id=message.id,
                processing_status=ProcessingStatus.PENDING
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
            
            db.commit()
            logger.info(f"Imported message {msg.message_id} from channel {channel_id}")
            
        except Exception as e:
            logger.error(f"Failed to handle message: {e}")
            db.rollback()
    
    async def start_polling(self, db: Session):
        """Start polling for messages"""
        if self.is_running:
            logger.warning("Bot polling is already running")
            return
        
        try:
            # Build application
            self.application = Application.builder().token(self.bot_token).build()
            
            # Add message handler with db session
            async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
                await self.handle_message(update, context, db)
            
            self.application.add_handler(
                MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler)
            )
            
            # Initialize and start polling
            await self.application.initialize()
            await self.application.start()
            await self.application.updater.start_polling()
            
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
    
    async def register_bot(self, db: Session, bot_token: str) -> Dict[str, Any]:
        """Register a new bot in the system"""
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
                return {
                    "success": False,
                    "error": "Bot already registered",
                    "bot_id": existing.id
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
    
    async def start_bot(self, db: Session, bot_id: int) -> bool:
        """Start a bot's polling"""
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
            
            # Start polling
            await self.bots[bot_id].start_polling(db)
            self.active_bots.add(bot_id)
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to start bot {bot_id}: {e}")
            return False
    
    async def stop_bot(self, bot_id: int) -> bool:
        """Stop a bot's polling"""
        try:
            if bot_id not in self.active_bots:
                logger.warning(f"Bot {bot_id} is not active")
                return True
            
            if bot_id in self.bots:
                await self.bots[bot_id].stop_polling()
                self.active_bots.remove(bot_id)
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to stop bot {bot_id}: {e}")
            return False
    
    async def stop_all_bots(self):
        """Stop all active bots"""
        for bot_id in list(self.active_bots):
            await self.stop_bot(bot_id)