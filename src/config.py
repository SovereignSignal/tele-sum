from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator
from typing import Optional
import secrets
import logging
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class Settings(BaseSettings):
    # Railway/deployment settings
    port: int = Field(default=8000, alias='PORT')
    
    # Database
    database_url: Optional[str] = Field(default=None, alias='DATABASE_URL')
    
    # API Key for authentication (protects frontend and API)
    api_key: Optional[str] = Field(default=None, alias='API_KEY')
    
    # Telegram API - Phone auth (for historical import)
    telegram_api_id: Optional[int] = Field(default=None, alias='TELEGRAM_API_ID')
    telegram_api_hash: Optional[str] = Field(default=None, alias='TELEGRAM_API_HASH')
    telegram_phone: Optional[str] = Field(default=None, alias='TELEGRAM_PHONE')
    telegram_channel_username: Optional[str] = Field(default=None, alias='TELEGRAM_CHANNEL_USERNAME')
    
    # Telegram Session Persistence
    telegram_session_path: str = Field(default="/app/data/telegram_session", alias='TELEGRAM_SESSION_PATH')
    
    # Telegram Bot Configuration (for ongoing monitoring)
    telegram_bot_tokens: Optional[str] = Field(default=None, alias='TELEGRAM_BOT_TOKENS')
    enable_bot_mode: bool = Field(default=True, alias='ENABLE_BOT_MODE')
    enable_phone_import: bool = Field(default=True, alias='ENABLE_PHONE_IMPORT')
    bot_polling_interval: int = Field(default=2, alias='BOT_POLLING_INTERVAL')
    webhook_base_url: Optional[str] = Field(default=None, alias='WEBHOOK_BASE_URL')
    
    # Grok AI
    grok_api_key: Optional[str] = Field(default=None, alias='GROK_API_KEY')
    grok_api_url: str = Field(default="https://api.x.ai/v1", alias='GROK_API_URL')
    
    # App Settings
    app_env: str = Field(default="production", alias='APP_ENV')
    secret_key: Optional[str] = Field(default=None, alias='SECRET_KEY')
    
    # Sync settings
    sync_interval_hours: int = Field(default=6, alias='SYNC_INTERVAL_HOURS')
    max_grok_requests_per_hour: int = Field(default=100, alias='MAX_GROK_REQUESTS_PER_HOUR')
    
    # Processing settings
    max_processing_retries: int = Field(default=3, alias='MAX_PROCESSING_RETRIES')
    processing_batch_size: int = Field(default=10, alias='PROCESSING_BATCH_SIZE')
    
    @field_validator('secret_key', mode='before')
    @classmethod
    def generate_secret_key(cls, v, info):
        if v is None or v == '':
            generated_key = secrets.token_urlsafe(32)
            logger.warning("⚠️  No SECRET_KEY provided - generated temporary key")
            return generated_key
        return v
    
    @field_validator('database_url', mode='before')
    @classmethod
    def validate_database_url(cls, v, info):
        if v is None or v == '':
            if os.getenv('PGHOST'):
                pg_user = os.getenv('PGUSER', 'postgres')
                pg_password = os.getenv('PGPASSWORD', '')
                pg_host = os.getenv('PGHOST', 'localhost')
                pg_port = os.getenv('PGPORT', '5432')
                pg_database = os.getenv('PGDATABASE', 'railway')
                constructed_url = f"postgresql://{pg_user}:{pg_password}@{pg_host}:{pg_port}/{pg_database}"
                logger.info("✓ Constructed DATABASE_URL from Railway PostgreSQL variables")
                return constructed_url
            
            logger.error("❌ DATABASE_URL is required")
            return "postgresql://user:password@localhost:5432/telesum"
        return v
    
    @field_validator('telegram_session_path', mode='before')
    @classmethod
    def validate_session_path(cls, v, info):
        if v:
            session_dir = os.path.dirname(v) if '/' in v else '.'
            if session_dir and not os.path.exists(session_dir):
                try:
                    os.makedirs(session_dir, exist_ok=True)
                except Exception as e:
                    logger.warning(f"Could not create session directory: {e}")
        return v
    
    def is_telegram_configured(self) -> bool:
        return all([
            self.telegram_api_id,
            self.telegram_api_hash,
            self.telegram_phone,
            self.telegram_channel_username
        ])
    
    def is_bot_configured(self) -> bool:
        return bool(self.telegram_bot_tokens and self.enable_bot_mode)
    
    def has_any_telegram_config(self) -> bool:
        return self.is_telegram_configured() or self.is_bot_configured()
    
    def get_bot_tokens(self) -> list:
        if not self.telegram_bot_tokens:
            return []
        return [token.strip() for token in self.telegram_bot_tokens.split(',') if token.strip()]
    
    def is_grok_configured(self) -> bool:
        return bool(self.grok_api_key)
    
    def is_database_configured(self) -> bool:
        return self.database_url and 'localhost' not in self.database_url
    
    def get_session_path(self) -> str:
        return self.telegram_session_path
    
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
        logger.info(f"Telegram Phone: {'✅' if self.is_telegram_configured() else '❌'}")
        logger.info(f"Telegram Bot: {'✅' if self.is_bot_configured() else '❌'}")
        logger.info(f"Grok AI: {'✅' if self.is_grok_configured() else '❌'}")
        logger.info(f"Sync Interval: {self.sync_interval_hours} hours")
        logger.info(f"Max Retries: {self.max_processing_retries}")
        logger.info("=" * 60)


# Initialize settings
logger.info("Initializing Tele-Sum settings...")

try:
    settings = Settings()
    settings.log_configuration_status()
except Exception as e:
    logger.error(f"Failed to initialize settings: {e}")
    settings = Settings(
        port=int(os.getenv('PORT', '8000')),
        database_url=os.getenv('DATABASE_URL'),
        api_key=os.getenv('API_KEY'),
        telegram_api_id=int(os.getenv('TELEGRAM_API_ID')) if os.getenv('TELEGRAM_API_ID') else None,
        telegram_api_hash=os.getenv('TELEGRAM_API_HASH'),
        telegram_phone=os.getenv('TELEGRAM_PHONE'),
        telegram_channel_username=os.getenv('TELEGRAM_CHANNEL_USERNAME'),
        grok_api_key=os.getenv('GROK_API_KEY'),
        secret_key=os.getenv('SECRET_KEY'),
        app_env=os.getenv('APP_ENV', 'production'),
        grok_api_url=os.getenv('GROK_API_URL', 'https://api.x.ai/v1'),
        sync_interval_hours=int(os.getenv('SYNC_INTERVAL_HOURS', '6')),
        max_grok_requests_per_hour=int(os.getenv('MAX_GROK_REQUESTS_PER_HOUR', '100')),
        max_processing_retries=int(os.getenv('MAX_PROCESSING_RETRIES', '3'))
    )
    settings.log_configuration_status()
