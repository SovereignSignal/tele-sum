"""
Application Configuration

Loads from environment variables (and optional .env). Bot-mode only.
AI provider config is read from the api_providers DB table by default;
env vars below are overrides for local testing.

Environment Variables:
    Database:
        DATABASE_URL or PG* (Railway auto-provides PGHOST/PGPORT/etc)

    Telegram (Bot mode only):
        TELEGRAM_BOT_TOKENS: Comma-separated bot tokens
        ENABLE_BOT_MODE: default true
        BOT_POLLING_INTERVAL: seconds, default 2

    AI Provider (override; otherwise read from api_providers table):
        AI_PROVIDER: e.g. grok, anthropic, openai, perplexity, ollama
        AI_API_KEY: provider API key
        AI_BASE_URL: provider base URL
        AI_MODEL: model id

    App:
        API_KEY: protects /api/* and the web UI (recommended)
        SECRET_KEY: session encryption (auto-generated if unset)
        APP_ENV: development | production

    Processing:
        SYNC_INTERVAL_HOURS: default 6
        MAX_AI_REQUESTS_PER_HOUR: rate limit, default 100
        MAX_PROCESSING_RETRIES: default 3
        PROCESSING_BATCH_SIZE: default 10
        RUN_SYNC_ON_STARTUP: default true
"""

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator
from typing import Literal, Optional, Union
import secrets
import logging
import os

logging.basicConfig(level=logging.INFO)

# httpx logs every request line at INFO, and the Telegram Bot API carries the
# bot token in the URL path -- so INFO httpx logging writes
#   HTTP Request: POST https://api.telegram.org/bot<TOKEN>/getUpdates
# into the platform log on every poll, roughly every 20 seconds, forever.
# WARNING keeps real failures visible and drops the request lines.
# Applies to every entrypoint: src.config is imported before any of them
# configure logging themselves.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

class Settings(BaseSettings):
    port: int = Field(default=8000, alias='PORT')

    database_url: Optional[str] = Field(default=None, alias='DATABASE_URL')

    api_key: Optional[str] = Field(default=None, alias='API_KEY')

    # Telegram - bot mode
    telegram_bot_tokens: Optional[str] = Field(default=None, alias='TELEGRAM_BOT_TOKENS')
    enable_bot_mode: bool = Field(default=True, alias='ENABLE_BOT_MODE')
    bot_polling_interval: int = Field(default=2, alias='BOT_POLLING_INTERVAL')
    webhook_base_url: Optional[str] = Field(default=None, alias='WEBHOOK_BASE_URL')

    # AI provider override (otherwise loaded from api_providers DB table)
    ai_provider: Optional[str] = Field(default=None, alias='AI_PROVIDER')
    ai_api_key: Optional[str] = Field(default=None, alias='AI_API_KEY')
    ai_base_url: Optional[str] = Field(default=None, alias='AI_BASE_URL')
    ai_model: Optional[str] = Field(default=None, alias='AI_MODEL')
    ai_ollama_think: Optional[Union[bool, Literal['low', 'medium', 'high', 'max']]] = Field(
        default=None, alias='AI_OLLAMA_THINK'
    )

    # App
    app_env: str = Field(default="production", alias='APP_ENV')
    secret_key: Optional[str] = Field(default=None, alias='SECRET_KEY')

    # Sync / processing
    sync_interval_hours: int = Field(default=6, alias='SYNC_INTERVAL_HOURS')
    max_ai_requests_per_hour: int = Field(default=100, alias='MAX_AI_REQUESTS_PER_HOUR')
    run_sync_on_startup: bool = Field(default=True, alias='RUN_SYNC_ON_STARTUP')
    max_processing_retries: int = Field(default=3, alias='MAX_PROCESSING_RETRIES')
    processing_batch_size: int = Field(default=10, alias='PROCESSING_BATCH_SIZE')
    enable_notion_sync: bool = Field(default=True, alias='ENABLE_NOTION_SYNC')

    @field_validator('secret_key', mode='before')
    @classmethod
    def generate_secret_key(cls, v, info):
        if v is None or v == '':
            generated = secrets.token_urlsafe(32)
            logger.warning("⚠️  No SECRET_KEY provided - generated temporary key")
            return generated
        return v

    @field_validator('database_url', mode='before')
    @classmethod
    def validate_database_url(cls, v, info):
        if v:
            return v
        if os.getenv('PGHOST'):
            user = os.getenv('PGUSER', 'postgres')
            password = os.getenv('PGPASSWORD', '')
            host = os.getenv('PGHOST', 'localhost')
            port = os.getenv('PGPORT', '5432')
            dbname = os.getenv('PGDATABASE', 'railway')
            logger.info("✓ Constructed DATABASE_URL from PG* env vars")
            return f"postgresql://{user}:{password}@{host}:{port}/{dbname}"
        logger.error("❌ DATABASE_URL is required")
        return None

    def is_bot_configured(self) -> bool:
        return bool(self.telegram_bot_tokens and self.enable_bot_mode)

    def get_bot_tokens(self) -> list:
        if not self.telegram_bot_tokens:
            return []
        return [t.strip() for t in self.telegram_bot_tokens.split(',') if t.strip()]

    def is_ai_configured(self) -> bool:
        # AIService also checks the api_providers DB table; this only checks env overrides.
        return bool(self.ai_api_key) or bool(self.ai_provider)

    def is_database_configured(self) -> bool:
        return bool(self.database_url) and 'localhost' not in (self.database_url or '')

    model_config = SettingsConfigDict(
        env_file='.env' if os.path.exists('.env') else None,
        env_file_encoding='utf-8',
        case_sensitive=False,
        use_enum_values=True,
        validate_default=True,
        extra='ignore',
        populate_by_name=True
    )

    def log_configuration_status(self):
        logger.info("=" * 60)
        logger.info("🚀 Tele-Sum Configuration")
        logger.info("=" * 60)
        logger.info(f"Environment: {self.app_env}")
        logger.info(f"Port: {self.port}")
        logger.info(f"Database: {'✅' if self.is_database_configured() else '❌'}")
        logger.info(f"API Key: {'✅ Protected' if self.api_key else '⚠️  UNPROTECTED'}")
        logger.info(f"Telegram Bot: {'✅' if self.is_bot_configured() else '❌'}")
        logger.info(f"AI override: {'env' if self.is_ai_configured() else 'DB (api_providers)'}")
        logger.info(f"Sync Interval: {self.sync_interval_hours} hours")
        logger.info("=" * 60)


logger.info("Initializing Tele-Sum settings...")
settings = Settings()
settings.log_configuration_status()
