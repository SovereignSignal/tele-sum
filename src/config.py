from pydantic_settings import BaseSettings
from typing import Optional

class Settings(BaseSettings):
    database_url: str
    
    telegram_api_id: int
    telegram_api_hash: str
    telegram_phone: str
    telegram_channel_username: str
    
    grok_api_key: str
    grok_api_url: str = "https://api.x.ai/v1"
    
    app_env: str = "development"
    secret_key: str
    port: int = 8000
    
    sync_interval_hours: int = 6
    max_grok_requests_per_hour: int = 100
    
    class Config:
        env_file = ".env"
        case_sensitive = False

settings = Settings()