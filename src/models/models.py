"""
Database Models

This module defines all SQLAlchemy ORM models for the Grant Wire database.

Core Tables:
    - Message: Raw Telegram messages (import source)
    - Summary: AI-generated summaries and metadata
    - Category: User-defined classification categories
    - ReviewQueue: Entries needing manual URL correction

Supporting Tables:
    - User: Multi-tenant user support
    - TelegramBot: Bot configuration for monitoring
    - ChannelConfig: Channel import settings
    - SyncStatus: Telegram sync tracking
    - GrokUsage: API usage monitoring

Key Enums:
    - ProcessingStatus: PENDING -> PROCESSING -> SUCCESS/RETRY/FAILED
    - DataQuality: COMPLETE (production-ready) or INCOMPLETE (missing URL)
    - ConfidenceLevel: AI classification confidence

Note: Wire is NOT an enum. It is a user-defined string slug (e.g. crypto/ai/oss/repos).
Any lowercase slug is valid; wire values are deployment config, not schema.

See DATABASE_SCHEMA.md for complete documentation and query examples.
"""

from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, JSON, Boolean, Float, Enum as SQLEnum, Index, UniqueConstraint
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

class DataQuality(enum.Enum):
    COMPLETE = "complete"      # Full data including verified source URL
    INCOMPLETE = "incomplete"  # Missing or unverified source URL

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
    keywords = Column(JSON, default=list)  # List of keywords for AI classification hints
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    user = relationship("User", back_populates="categories")
    summaries = relationship("Summary", back_populates="category")

class Message(Base):
    __tablename__ = "messages"
    
    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(Integer, index=True)
    channel_id = Column(String(255), index=True)
    channel_config_id = Column(Integer, ForeignKey("channel_configs.id"), nullable=True, index=True)  # Link to channel metadata
    bot_id = Column(Integer, ForeignKey("telegram_bots.id"), nullable=True, index=True)  # Track which bot imported
    import_method = Column(String(20), default="phone")  # "phone", "bot", or "file"
    wire = Column(String(32), nullable=True, index=True)  # vertical label — user-defined config, e.g. crypto/ai/oss/repos
    timestamp = Column(DateTime(timezone=True), nullable=False, index=True)
    raw_content = Column(Text, nullable=False)
    extracted_urls = Column(JSON, default=list)
    entity_links = Column(JSON, nullable=True)  # [{text, url}] from Telegram hyperlink entities
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    summary = relationship("Summary", back_populates="message", uselist=False, cascade="all, delete-orphan")
    bot = relationship("TelegramBot")
    channel_config = relationship("ChannelConfig", back_populates="messages")
    
    # Composite indexes for common query patterns
    __table_args__ = (
        UniqueConstraint('channel_id', 'telegram_id', name='uq_messages_channel_telegram_id'),
        Index('ix_messages_channel_timestamp', 'channel_id', 'timestamp'),
        Index('ix_messages_bot_timestamp', 'bot_id', 'timestamp'),
    )

class Summary(Base):
    __tablename__ = "summaries"

    id = Column(Integer, primary_key=True, index=True)
    message_id = Column(Integer, ForeignKey("messages.id"), unique=True, nullable=False)
    title = Column(String(100), nullable=True)  # Short 4-5 word title for the entry
    summary = Column(Text, nullable=True)
    entities = Column(JSON, default=dict)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    confidence = Column(SQLEnum(ConfidenceLevel), nullable=True)
    wire = Column(String(32), nullable=True, index=True)  # vertical label — user-defined config, e.g. crypto/ai/oss/repos
    processing_status = Column(SQLEnum(ProcessingStatus), default=ProcessingStatus.PENDING, index=True)
    error_log = Column(Text, nullable=True)
    processing_attempts = Column(Integer, default=0)
    last_processed_at = Column(DateTime(timezone=True), nullable=True)
    data_quality = Column(String(20), default="complete", index=True)  # 'complete' or 'incomplete'
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    message = relationship("Message", back_populates="summary")
    category = relationship("Category", back_populates="summaries")

    # Composite indexes for retry and processing queries
    __table_args__ = (
        Index('ix_summaries_status_processed', 'processing_status', 'last_processed_at'),
        Index('ix_summaries_category_created', 'category_id', 'created_at'),
    )

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
    wire = Column(String(32), nullable=True, index=True)  # vertical label — user-defined config, e.g. crypto/ai/oss/repos
    extraction_profile = Column(String(32), nullable=False, default="grant", server_default="grant")  # 'grant' | 'digest'
    prompt_hint = Column(Text, nullable=True)          # free-text hint passed to digest extraction
    notion_database_id = Column(String(64), nullable=True)  # Notion target; null = Notion sync off
    import_mode = Column(SQLEnum(ImportMode), default=ImportMode.BOT)
    historical_import_complete = Column(Boolean, default=False)
    historical_import_date = Column(DateTime(timezone=True), nullable=True)
    last_historical_message_id = Column(Integer, nullable=True)
    bot_id = Column(Integer, ForeignKey("telegram_bots.id"), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    bot = relationship("TelegramBot", back_populates="channel_configs")
    messages = relationship("Message", back_populates="channel_config")
    sync_status = relationship("SyncStatus", back_populates="channel_config", uselist=False, cascade="all, delete-orphan")

class NotionPage(Base):
    """One row per Notion page created from an extracted digest item.
    (summary_id, item_index) is the idempotency key for the notion_sync job."""
    __tablename__ = "notion_pages"

    id = Column(Integer, primary_key=True)
    summary_id = Column(Integer, ForeignKey("summaries.id", ondelete="CASCADE"), nullable=False, index=True)
    item_index = Column(Integer, nullable=False)
    page_id = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("summary_id", "item_index", name="uq_notion_pages_summary_item"),)


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

class ReviewStatus(enum.Enum):
    PENDING = "pending"        # Needs review
    IN_PROGRESS = "in_progress"  # Being worked on
    CORRECTED = "corrected"    # Fixed and verified
    SKIPPED = "skipped"        # Intentionally skipped (e.g., not recoverable)

class ReviewQueue(Base):
    """
    Track entries that need manual review/correction.
    Used for x23 entries with mismatched or missing source URLs.
    Entries are removed from this table once corrected.
    """
    __tablename__ = "review_queue"

    id = Column(Integer, primary_key=True, index=True)
    summary_id = Column(Integer, ForeignKey("summaries.id"), unique=True, nullable=False)

    # Review categorization
    issue_type = Column(String(50), nullable=False, index=True)  # 'url_mismatch', 'url_missing', 'search_failed', 'unknown_protocol', 'no_forum'

    # Details about the issue
    our_title = Column(String(255), nullable=True)  # The title we generated
    matched_url = Column(Text, nullable=True)  # The URL we matched (if any)
    matched_page_title = Column(String(255), nullable=True)  # Title of the matched page
    protocol = Column(String(100), nullable=True)  # Detected protocol
    notes = Column(Text, nullable=True)  # Additional notes or context

    # Tracking
    status = Column(SQLEnum(ReviewStatus), default=ReviewStatus.PENDING, index=True)
    corrected_url = Column(Text, nullable=True)  # The manually corrected URL
    reviewed_by = Column(String(100), nullable=True)  # Who reviewed it
    reviewed_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    summary = relationship("Summary")

    __table_args__ = (
        Index('ix_review_queue_status_type', 'status', 'issue_type'),
    )


class AIRequestReservation(Base):
    """Pre-request budget slots; shared across workers and retained for one hour."""
    __tablename__ = "ai_request_reservations"

    id = Column(Integer, primary_key=True)
    reserved_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)


class GrokUsage(Base):
    """Track Grok API usage for rate limiting and cost monitoring"""
    __tablename__ = "grok_usage"
    
    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    tokens_used = Column(Integer, default=0)
    request_type = Column(String(50), default="analysis")  # analysis, retry, etc.
    success = Column(Boolean, default=True)
    error_message = Column(Text, nullable=True)
    
    __table_args__ = (
        Index('ix_grok_usage_timestamp_success', 'timestamp', 'success'),
    )

# Default categories to seed for new users
DEFAULT_CATEGORIES = [
    {
        "name": "Announcements",
        "description": "Official announcements, launches, and major updates",
        "keywords": ["announcement", "launch", "release", "update", "news"]
    },
    {
        "name": "Funding & Grants",
        "description": "Funding rounds, grants, investments, and financial news",
        "keywords": ["funding", "grant", "investment", "raise", "seed", "series", "million", "billion"]
    },
    {
        "name": "Technical",
        "description": "Technical content, development updates, and engineering",
        "keywords": ["technical", "development", "engineering", "code", "protocol", "smart contract"]
    },
    {
        "name": "Research",
        "description": "Research papers, analysis, and deep dives",
        "keywords": ["research", "analysis", "paper", "study", "report", "findings"]
    },
    {
        "name": "Events",
        "description": "Conferences, meetups, AMAs, and scheduled events",
        "keywords": ["event", "conference", "meetup", "ama", "webinar", "summit"]
    },
    {
        "name": "Partnerships",
        "description": "Partnership announcements and collaborations",
        "keywords": ["partnership", "collaboration", "integration", "alliance", "joint"]
    },
    {
        "name": "Market & Trading",
        "description": "Market updates, trading, and price-related content",
        "keywords": ["market", "trading", "price", "token", "listing", "exchange"]
    },
    {
        "name": "Community",
        "description": "Community updates, governance, and social content",
        "keywords": ["community", "governance", "vote", "proposal", "dao"]
    },
    {
        "name": "Security",
        "description": "Security updates, audits, and vulnerability disclosures",
        "keywords": ["security", "audit", "vulnerability", "hack", "exploit", "fix"]
    },
    {
        "name": "Other",
        "description": "Content that doesn't fit other categories",
        "keywords": []
    }
]
