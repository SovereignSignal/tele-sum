from pydantic_settings import BaseSettings
from pydantic import Field, validator
from typing import Optional
import secrets
import logging
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class Settings(BaseSettings):
    # Railway/deployment settings - PORT env var from Railway
    port: int = Field(default=8000, validation_alias='PORT')
    
    # Database - Required but with fallback
    database_url: Optional[str] = Field(default=None, validation_alias='DATABASE_URL')
    
    # Telegram API - Optional for initial deployment
    telegram_api_id: Optional[int] = Field(default=None, validation_alias='TELEGRAM_API_ID')
    telegram_api_hash: Optional[str] = Field(default=None, validation_alias='TELEGRAM_API_HASH')
    telegram_phone: Optional[str] = Field(default=None, validation_alias='TELEGRAM_PHONE')
    telegram_channel_username: Optional[str] = Field(default=None, validation_alias='TELEGRAM_CHANNEL_USERNAME')
    
    # Grok AI - Optional (graceful degradation supported)
    grok_api_key: Optional[str] = Field(default=None, validation_alias='GROK_API_KEY')
    grok_api_url: str = Field(default="https://api.x.ai/v1", validation_alias='GROK_API_URL')
    
    # App Settings
    app_env: str = Field(default="production", validation_alias='APP_ENV')
    secret_key: Optional[str] = Field(default=None, validation_alias='SECRET_KEY')
    
    # Sync settings
    sync_interval_hours: int = Field(default=6, validation_alias='SYNC_INTERVAL_HOURS')
    max_grok_requests_per_hour: int = Field(default=100, validation_alias='MAX_GROK_REQUESTS_PER_HOUR')
    
    @validator('secret_key', pre=True, always=True)
    def generate_secret_key(cls, v):
        if not v:
            generated_key = secrets.token_urlsafe(32)
            logger.warning("⚠️  No SECRET_KEY provided. Generated temporary key. Please set SECRET_KEY in production!")
            return generated_key
        return v
    
    @validator('database_url', pre=True)
    def validate_database_url(cls, v):
        if not v:
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
    
    class Config:
        env_file = ".env"
        case_sensitive = False
        # Allow both uppercase and lowercase env vars
        populate_by_name = True

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

# Create settings instance with error handling
try:
    settings = Settings()
    settings.log_configuration_status()
except Exception as e:
    logger.error(f"Failed to load settings: {e}")
    # Create minimal settings to allow app to start
    settings = Settings(
        port=int(os.getenv('PORT', '8000')),
        database_url="postgresql://user:password@localhost:5432/telesum",
        app_env=os.getenv('APP_ENV', 'production')
    )
    logger.warning("Using minimal fallback configuration")