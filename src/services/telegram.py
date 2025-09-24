import logging
import re
from typing import List, Optional, Dict
from datetime import datetime
from telethon import TelegramClient
from telethon.tl.functions.messages import GetHistoryRequest
from telethon.tl.types import Message as TelegramMessage
from sqlalchemy.orm import Session

from src.config import settings
from src.models.models import Message, Summary, SyncStatus, ProcessingStatus
from src.utils.url_extractor import extract_urls

logger = logging.getLogger(__name__)

class TelegramService:
    def __init__(self):
        self.client = TelegramClient(
            'telesum_session',
            settings.telegram_api_id,
            settings.telegram_api_hash
        )
        self.channel_username = settings.telegram_channel_username
        
    async def connect(self):
        await self.client.start(phone=settings.telegram_phone)
        logger.info("Connected to Telegram")
        
    async def disconnect(self):
        await self.client.disconnect()
        logger.info("Disconnected from Telegram")
    
    async def get_channel(self):
        try:
            channel = await self.client.get_entity(self.channel_username)
            return channel
        except Exception as e:
            logger.error(f"Failed to get channel {self.channel_username}: {e}")
            raise
    
    async def fetch_messages(
        self,
        limit: int = 100,
        min_id: int = 0,
        offset_id: int = 0
    ) -> List[TelegramMessage]:
        channel = await self.get_channel()
        
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
    
    async def import_channel_history(self, db: Session) -> Dict:
        await self.connect()
        
        try:
            channel = await self.get_channel()
            channel_id = str(channel.id)
            
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
            
            while True:
                telegram_messages = await self.fetch_messages(
                    limit=100,
                    min_id=min_id,
                    offset_id=offset_id
                )
                
                if not telegram_messages:
                    break
                
                for tg_msg in telegram_messages:
                    existing = db.query(Message).filter(
                        Message.telegram_id == tg_msg.id
                    ).first()
                    
                    if existing:
                        duplicates += 1
                        continue
                    
                    urls = extract_urls(tg_msg.message)
                    
                    message = Message(
                        telegram_id=tg_msg.id,
                        channel_id=channel_id,
                        timestamp=tg_msg.date,
                        raw_content=tg_msg.message,
                        extracted_urls=urls
                    )
                    
                    db.add(message)
                    db.flush()
                    
                    summary = Summary(
                        message_id=message.id,
                        processing_status=ProcessingStatus.PENDING
                    )
                    db.add(summary)
                    
                    total_imported += 1
                    
                    if tg_msg.id > (sync_status.last_message_id or 0):
                        sync_status.last_message_id = tg_msg.id
                
                db.commit()
                logger.info(f"Imported batch: {len(telegram_messages)} messages, total: {total_imported}")
                
                if len(telegram_messages) < 100:
                    break
                    
                offset_id = telegram_messages[-1].id
            
            sync_status.total_messages_synced += total_imported
            sync_status.last_sync_at = datetime.utcnow()
            sync_status.is_initial_import_complete = True
            db.commit()
            
            return {
                "total_imported": total_imported,
                "duplicates": duplicates,
                "channel_id": channel_id
            }
            
        except Exception as e:
            logger.error(f"Import failed: {e}")
            db.rollback()
            raise
        finally:
            await self.disconnect()
    
    async def sync_new_messages(self, db: Session) -> Dict:
        await self.connect()
        
        try:
            channel = await self.get_channel()
            channel_id = str(channel.id)
            
            sync_status = db.query(SyncStatus).filter(
                SyncStatus.channel_id == channel_id
            ).first()
            
            if not sync_status:
                return await self.import_channel_history(db)
            
            min_id = sync_status.last_message_id or 0
            telegram_messages = await self.fetch_messages(
                limit=50,
                min_id=min_id
            )
            
            new_messages = 0
            
            for tg_msg in telegram_messages:
                existing = db.query(Message).filter(
                    Message.telegram_id == tg_msg.id
                ).first()
                
                if existing:
                    continue
                
                urls = extract_urls(tg_msg.message)
                
                message = Message(
                    telegram_id=tg_msg.id,
                    channel_id=channel_id,
                    timestamp=tg_msg.date,
                    raw_content=tg_msg.message,
                    extracted_urls=urls
                )
                
                db.add(message)
                db.flush()
                
                summary = Summary(
                    message_id=message.id,
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
                "new_messages": new_messages,
                "channel_id": channel_id
            }
            
        except Exception as e:
            logger.error(f"Sync failed: {e}")
            db.rollback()
            raise
        finally:
            await self.disconnect()