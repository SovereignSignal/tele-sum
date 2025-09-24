from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, JSON, Boolean, Float, Enum as SQLEnum
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from datetime import datetime
import enum

from .database import Base

class ProcessingStatus(enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCESS = "success"
    FAILED = "failed"
    RETRY = "retry"

class ConfidenceLevel(enum.Enum):
    CONFIDENT = "confident"
    UNCERTAIN = "uncertain"

class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(255), unique=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    categories = relationship("Category", back_populates="user", cascade="all, delete-orphan")
    
class Category(Base):
    __tablename__ = "categories"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    user = relationship("User", back_populates="categories")
    summaries = relationship("Summary", back_populates="category")

class Message(Base):
    __tablename__ = "messages"
    
    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(Integer, unique=True, index=True)
    channel_id = Column(String(255), index=True)
    bot_id = Column(Integer, ForeignKey("telegram_bots.id"), nullable=True, index=True)  # Track which bot imported
    import_method = Column(String(20), default="phone")  # "phone" or "bot"
    timestamp = Column(DateTime(timezone=True), nullable=False, index=True)
    raw_content = Column(Text, nullable=False)
    extracted_urls = Column(JSON, default=list)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    summary = relationship("Summary", back_populates="message", uselist=False, cascade="all, delete-orphan")
    bot = relationship("TelegramBot")

class Summary(Base):
    __tablename__ = "summaries"
    
    id = Column(Integer, primary_key=True, index=True)
    message_id = Column(Integer, ForeignKey("messages.id"), unique=True, nullable=False)
    summary = Column(Text, nullable=True)
    entities = Column(JSON, default=dict)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    confidence = Column(SQLEnum(ConfidenceLevel), nullable=True)
    processing_status = Column(SQLEnum(ProcessingStatus), default=ProcessingStatus.PENDING, index=True)
    error_log = Column(Text, nullable=True)
    processing_attempts = Column(Integer, default=0)
    last_processed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    message = relationship("Message", back_populates="summary")
    category = relationship("Category", back_populates="summaries")

class TelegramBot(Base):
    __tablename__ = "telegram_bots"
    
    id = Column(Integer, primary_key=True, index=True)
    bot_token = Column(String(500), unique=True)  # Encrypted in production
    bot_username = Column(String(100), nullable=True)
    bot_name = Column(String(100), nullable=True)
    is_active = Column(Boolean, default=True)
    webhook_url = Column(String(500), nullable=True)
    last_update_id = Column(Integer, nullable=True)  # For polling mode
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    channel_configs = relationship("ChannelConfig", back_populates="bot", cascade="all, delete-orphan")

class ImportMode(enum.Enum):
    PHONE = "phone"
    BOT = "bot"
    HYBRID = "hybrid"

class ChannelConfig(Base):
    __tablename__ = "channel_configs"
    
    id = Column(Integer, primary_key=True, index=True)
    channel_id = Column(String(255), unique=True, index=True)
    channel_username = Column(String(100), nullable=True)
    channel_title = Column(String(255), nullable=True)
    import_mode = Column(SQLEnum(ImportMode), default=ImportMode.BOT)
    historical_import_complete = Column(Boolean, default=False)
    historical_import_date = Column(DateTime(timezone=True), nullable=True)
    last_historical_message_id = Column(Integer, nullable=True)
    bot_id = Column(Integer, ForeignKey("telegram_bots.id"), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    bot = relationship("TelegramBot", back_populates="channel_configs")
    sync_status = relationship("SyncStatus", back_populates="channel_config", uselist=False, cascade="all, delete-orphan")

class SyncStatus(Base):
    __tablename__ = "sync_status"
    
    id = Column(Integer, primary_key=True, index=True)
    channel_id = Column(String(255), unique=True, index=True)
    channel_config_id = Column(Integer, ForeignKey("channel_configs.id"), nullable=True)
    last_message_id = Column(Integer, nullable=True)
    last_phone_sync_id = Column(Integer, nullable=True)  # Track phone imports
    last_bot_sync_id = Column(Integer, nullable=True)    # Track bot imports
    last_sync_at = Column(DateTime(timezone=True), nullable=True)
    total_messages_synced = Column(Integer, default=0)
    is_initial_import_complete = Column(Boolean, default=False)
    transition_to_bot_complete = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    channel_config = relationship("ChannelConfig", back_populates="sync_status")