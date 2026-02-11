import logging
import re
import os
from typing import List, Optional, Dict
from datetime import datetime
from telethon import TelegramClient
from telethon.tl.functions.messages import GetHistoryRequest
from telethon.tl.types import Message as TelegramMessage
from sqlalchemy.orm import Session

from src.config import settings
from src.models.models import Message, Summary, SyncStatus, ProcessingStatus, ChannelConfig
from src.utils.url_extractor import extract_urls

logger = logging.getLogger(__name__)

class TelegramService:
    def __init__(self):
        # Use configurable session path for persistence across deployments
        session_path = settings.get_session_path()
        
        # Ensure session directory exists
        session_dir = os.path.dirname(session_path)
        if session_dir and not os.path.exists(session_dir):
            try:
                os.makedirs(session_dir, exist_ok=True)
                logger.info(f"Created session directory: {session_dir}")
            except Exception as e:
                logger.warning(f"Could not create session directory: {e}")
                # Fall back to current directory
                session_path = 'telesum_session'
        
        self.client = TelegramClient(
            session_path,
            settings.telegram_api_id,
            settings.telegram_api_hash
        )
        self.channel_username = settings.telegram_channel_username
        logger.info(f"TelegramService initialized with session: {session_path}")
        
    async def connect(self):
        """Connect to Telegram with phone authentication"""
        try:
            await self.client.start(phone=settings.telegram_phone)
            logger.info("Connected to Telegram")
        except Exception as e:
            logger.error(f"Failed to connect to Telegram: {e}")
            raise
        
    async def disconnect(self):
        """Disconnect from Telegram"""
        try:
            await self.client.disconnect()
            logger.info("Disconnected from Telegram")
        except Exception as e:
            logger.warning(f"Error disconnecting from Telegram: {e}")
    
    async def is_connected(self) -> bool:
        """Check if client is connected"""
        return self.client.is_connected()
    
    async def get_channel(self, channel_username: Optional[str] = None):
        """Get channel entity by username"""
        username = channel_username or self.channel_username
        try:
            channel = await self.client.get_entity(username)
            return channel
        except Exception as e:
            logger.error(f"Failed to get channel {username}: {e}")
            raise
    
    async def fetch_messages(
        self,
        limit: int = 100,
        min_id: int = 0,
        offset_id: int = 0,
        channel_username: Optional[str] = None
    ) -> List[TelegramMessage]:
        """Fetch messages from channel"""
        channel = await self.get_channel(channel_username)
        
        messages = []
        async for message in self.client.iter_messages(
            channel,
            limit=limit,
            min_id=min_id,
            offset_id=offset_id
        ):
            if isinstance(message, TelegramMessage) and message.message:
                messages.append(message)
        
        return messages
    
    async def import_channel_history(
        self,
        db: Session,
        channel_username: Optional[str] = None,
        limit: Optional[int] = None
    ) -> Dict:
        """Import full channel history"""
        await self.connect()
        
        try:
            channel = await self.get_channel(channel_username)
            channel_id = str(channel.id)
            
            # Look up channel config for wire assignment
            channel_config = db.query(ChannelConfig).filter(
                ChannelConfig.channel_id == channel_id,
                ChannelConfig.is_active == True
            ).first()
            # Also try by username if not found
            if not channel_config:
                channel_config = db.query(ChannelConfig).filter(
                    ChannelConfig.channel_username == channel_username,
                    ChannelConfig.is_active == True
                ).first()
            wire_value = channel_config.wire if channel_config else None
            
            sync_status = db.query(SyncStatus).filter(
                SyncStatus.channel_id == channel_id
            ).first()
            
            if not sync_status:
                sync_status = SyncStatus(
                    channel_id=channel_id,
                    total_messages_synced=0
                )
                db.add(sync_status)
                db.commit()
            
            total_imported = 0
            duplicates = 0
            offset_id = 0
            min_id = sync_status.last_message_id or 0
            max_messages = limit or 10000  # Default max for full import
            
            while total_imported < max_messages:
                batch_limit = min(100, max_messages - total_imported)
                
                telegram_messages = await self.fetch_messages(
                    limit=batch_limit,
                    min_id=min_id,
                    offset_id=offset_id,
                    channel_username=channel_username
                )
                
                if not telegram_messages:
                    break
                
                for tg_msg in telegram_messages:
                    # Check for duplicate (must check both channel_id and telegram_id since telegram_id is only unique per channel)
                    existing = db.query(Message).filter(
                        Message.telegram_id == tg_msg.id,
                        Message.channel_id == channel_id
                    ).first()
                    
                    if existing:
                        duplicates += 1
                        continue
                    
                    urls = extract_urls(tg_msg.message)
                    
                    message = Message(
                        telegram_id=tg_msg.id,
                        channel_id=channel_id,
                        import_method="phone",
                        wire=wire_value,  # Set wire from channel configuration
                        timestamp=tg_msg.date,
                        raw_content=tg_msg.message,
                        extracted_urls=urls
                    )
                    
                    db.add(message)
                    db.flush()
                    
                    summary = Summary(
                        message_id=message.id,
                        wire=wire_value,  # Set wire from channel configuration
                        processing_status=ProcessingStatus.PENDING
                    )
                    db.add(summary)
                    
                    total_imported += 1
                    
                    if tg_msg.id > (sync_status.last_message_id or 0):
                        sync_status.last_message_id = tg_msg.id
                
                db.commit()
                logger.info(f"Imported batch: {len(telegram_messages)} messages, total: {total_imported}")
                
                if len(telegram_messages) < batch_limit:
                    break
                    
                offset_id = telegram_messages[-1].id
            
            sync_status.total_messages_synced += total_imported
            sync_status.last_sync_at = datetime.utcnow()
            sync_status.is_initial_import_complete = True
            db.commit()
            
            return {
                "success": True,
                "total_imported": total_imported,
                "duplicates": duplicates,
                "channel_id": channel_id
            }
            
        except Exception as e:
            logger.error(f"Import failed: {e}")
            db.rollback()
            return {
                "success": False,
                "error": str(e)
            }
        finally:
            await self.disconnect()
    
    async def sync_new_messages(
        self,
        db: Session,
        channel_username: Optional[str] = None,
        limit: int = 50
    ) -> Dict:
        """Sync only new messages since last sync"""
        await self.connect()
        
        try:
            channel = await self.get_channel(channel_username)
            channel_id = str(channel.id)
            
            # Look up channel config for wire assignment
            channel_config = db.query(ChannelConfig).filter(
                ChannelConfig.channel_id == channel_id,
                ChannelConfig.is_active == True
            ).first()
            if not channel_config:
                channel_config = db.query(ChannelConfig).filter(
                    ChannelConfig.channel_username == channel_username,
                    ChannelConfig.is_active == True
                ).first()
            wire_value = channel_config.wire if channel_config else None
            
            sync_status = db.query(SyncStatus).filter(
                SyncStatus.channel_id == channel_id
            ).first()
            
            if not sync_status:
                # First sync - do initial import
                return await self.import_channel_history(db, channel_username, limit)
            
            min_id = sync_status.last_message_id or 0
            telegram_messages = await self.fetch_messages(
                limit=limit,
                min_id=min_id,
                channel_username=channel_username
            )
            
            new_messages = 0
            
            for tg_msg in telegram_messages:
                # Check for duplicate (must check both channel_id and telegram_id since telegram_id is only unique per channel)
                existing = db.query(Message).filter(
                    Message.telegram_id == tg_msg.id,
                    Message.channel_id == channel_id
                ).first()
                
                if existing:
                    continue
                
                urls = extract_urls(tg_msg.message)
                
                message = Message(
                    telegram_id=tg_msg.id,
                    channel_id=channel_id,
                    import_method="phone",
                    wire=wire_value,  # Set wire from channel configuration
                    timestamp=tg_msg.date,
                    raw_content=tg_msg.message,
                    extracted_urls=urls
                )
                
                db.add(message)
                db.flush()
                
                summary = Summary(
                    message_id=message.id,
                    wire=wire_value,  # Set wire from channel configuration
                    processing_status=ProcessingStatus.PENDING
                )
                db.add(summary)
                
                new_messages += 1
                
                if tg_msg.id > sync_status.last_message_id:
                    sync_status.last_message_id = tg_msg.id
            
            sync_status.total_messages_synced += new_messages
            sync_status.last_sync_at = datetime.utcnow()
            db.commit()
            
            logger.info(f"Synced {new_messages} new messages")
            
            return {
                "success": True,
                "new_messages": new_messages,
                "channel_id": channel_id
            }
            
        except Exception as e:
            logger.error(f"Sync failed: {e}")
            db.rollback()
            return {
                "success": False,
                "error": str(e)
            }
        finally:
            await self.disconnect()
    
    async def get_channel_info(self, channel_username: Optional[str] = None) -> Dict:
        """Get information about a channel"""
        await self.connect()
        
        try:
            channel = await self.get_channel(channel_username)
            
            return {
                "success": True,
                "id": str(channel.id),
                "title": getattr(channel, 'title', None),
                "username": getattr(channel, 'username', None),
                "participants_count": getattr(channel, 'participants_count', None)
            }
        except Exception as e:
            logger.error(f"Failed to get channel info: {e}")
            return {
                "success": False,
                "error": str(e)
            }
        finally:
            await self.disconnect()
