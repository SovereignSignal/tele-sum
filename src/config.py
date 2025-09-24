from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator
from typing import Optional
import secrets
import logging
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class Settings(BaseSettings):
    # Railway/deployment settings - PORT env var from Railway
    port: int = Field(default=8000, alias='PORT')
    
    # Database - Optional with fallback
    database_url: Optional[str] = Field(default=None, alias='DATABASE_URL')
    
    # Telegram API - Optional for initial deployment
    telegram_api_id: Optional[int] = Field(default=None, alias='TELEGRAM_API_ID')
    telegram_api_hash: Optional[str] = Field(default=None, alias='TELEGRAM_API_HASH')
    telegram_phone: Optional[str] = Field(default=None, alias='TELEGRAM_PHONE')
    telegram_channel_username: Optional[str] = Field(default=None, alias='TELEGRAM_CHANNEL_USERNAME')
    
    # Grok AI - Optional (graceful degradation supported)
    grok_api_key: Optional[str] = Field(default=None, alias='GROK_API_KEY')
    grok_api_url: str = Field(default="https://api.x.ai/v1", alias='GROK_API_URL')
    
    # App Settings
    app_env: str = Field(default="production", alias='APP_ENV')
    secret_key: Optional[str] = Field(default=None, alias='SECRET_KEY')
    
    # Sync settings
    sync_interval_hours: int = Field(default=6, alias='SYNC_INTERVAL_HOURS')
    max_grok_requests_per_hour: int = Field(default=100, alias='MAX_GROK_REQUESTS_PER_HOUR')
    
    @field_validator('secret_key', mode='before')
    @classmethod
    def generate_secret_key(cls, v, info):
        if v is None or v == '':
            generated_key = secrets.token_urlsafe(32)
            logger.warning("⚠️  No SECRET_KEY provided. Generated temporary key. Please set SECRET_KEY in production!")
            return generated_key
        return v
    
    @field_validator('database_url', mode='before')
    @classmethod
    def validate_database_url(cls, v, info):
        if v is None or v == '':
            # Check for common Railway PostgreSQL pattern
            if os.getenv('PGHOST'):
                # Construct from Railway's PostgreSQL env vars
                pg_user = os.getenv('PGUSER', 'postgres')
                pg_password = os.getenv('PGPASSWORD', '')
                pg_host = os.getenv('PGHOST', 'localhost')
                pg_port = os.getenv('PGPORT', '5432')
                pg_database = os.getenv('PGDATABASE', 'railway')
                constructed_url = f"postgresql://{pg_user}:{pg_password}@{pg_host}:{pg_port}/{pg_database}"
                logger.info("✓ Constructed DATABASE_URL from Railway PostgreSQL variables")
                return constructed_url
            
            logger.error("❌ DATABASE_URL is required. Please set it in Railway environment variables or add PostgreSQL service.")
            # Provide a dummy URL to allow app to start and show error page
            return "postgresql://user:password@localhost:5432/telesum"
        return v
    
    def is_telegram_configured(self) -> bool:
        """Check if Telegram configuration is complete"""
        return all([
            self.telegram_api_id,
            self.telegram_api_hash,
            self.telegram_phone,
            self.telegram_channel_username
        ])
    
    def is_grok_configured(self) -> bool:
        """Check if Grok API is configured"""
        return bool(self.grok_api_key)
    
    def is_database_configured(self) -> bool:
        """Check if database is properly configured"""
        return self.database_url and 'localhost' not in self.database_url
    
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
        """Log configuration status for debugging"""
        logger.info("=" * 60)
        logger.info("🚀 Tele-Sum Configuration Status")
        logger.info("=" * 60)
        logger.info(f"📍 Environment: {self.app_env}")
        logger.info(f"🔌 Port: {self.port}")
        logger.info(f"🗄️  Database: {'✅ Configured' if self.is_database_configured() else '❌ Not configured (using dummy URL)'}")
        logger.info(f"💬 Telegram: {'✅ Configured' if self.is_telegram_configured() else '⚠️  Not configured (import features disabled)'}")
        logger.info(f"🤖 Grok AI: {'✅ Configured' if self.is_grok_configured() else '⚠️  Not configured (AI features disabled)'}")
        logger.info("=" * 60)
        
        if not self.is_database_configured():
            logger.warning("⚠️  Add PostgreSQL service in Railway and DATABASE_URL will be auto-configured")
        if not self.is_telegram_configured():
            logger.warning("⚠️  Telegram import features disabled. Add TELEGRAM_* variables to enable.")
        if not self.is_grok_configured():
            logger.warning("⚠️  Grok AI features disabled. Add GROK_API_KEY to enable AI summaries.")

# Create settings instance with better error handling
logger.info("Initializing Tele-Sum settings...")
logger.info(f"Environment variables available: {list(os.environ.keys())}")

try:
    settings = Settings()
    settings.log_configuration_status()
except Exception as e:
    logger.error(f"Failed to initialize settings: {e}")
    logger.error(f"Error type: {type(e).__name__}")
    
    # Log environment variables for debugging
    logger.info("Environment variables:")
    for key in ['PORT', 'DATABASE_URL', 'TELEGRAM_API_ID', 'TELEGRAM_API_HASH', 
                'TELEGRAM_PHONE', 'TELEGRAM_CHANNEL_USERNAME', 'GROK_API_KEY', 'SECRET_KEY']:
        value = os.getenv(key)
        if value:
            logger.info(f"  {key}: {'***' if 'SECRET' in key or 'KEY' in key or 'HASH' in key else value[:20] + '...' if len(value) > 20 else value}")
        else:
            logger.info(f"  {key}: Not set")
    
    # Create minimal fallback settings
    logger.info("Creating fallback settings with defaults...")
    settings = Settings(
        port=int(os.getenv('PORT', '8000')),
        database_url=os.getenv('DATABASE_URL'),
        telegram_api_id=int(os.getenv('TELEGRAM_API_ID')) if os.getenv('TELEGRAM_API_ID') else None,
        telegram_api_hash=os.getenv('TELEGRAM_API_HASH'),
        telegram_phone=os.getenv('TELEGRAM_PHONE'),
        telegram_channel_username=os.getenv('TELEGRAM_CHANNEL_USERNAME'),
        grok_api_key=os.getenv('GROK_API_KEY'),
        secret_key=os.getenv('SECRET_KEY'),
        app_env=os.getenv('APP_ENV', 'production'),
        grok_api_url=os.getenv('GROK_API_URL', 'https://api.x.ai/v1'),
        sync_interval_hours=int(os.getenv('SYNC_INTERVAL_HOURS', '6')),
        max_grok_requests_per_hour=int(os.getenv('MAX_GROK_REQUESTS_PER_HOUR', '100'))
    )
    settings.log_configuration_status()