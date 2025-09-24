from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Dict, Optional, Any
import os

from src.config import settings
from src.models.database import check_database_health

router = APIRouter()

class ConfigStatus(BaseModel):
    database: Dict[str, Any]
    telegram: Dict[str, Any]
    grok: Dict[str, Any]
    app: Dict[str, Any]
    environment_hints: Dict[str, str]

@router.get("/status", response_model=ConfigStatus)
async def get_config_status():
    """Get the current configuration status and provide setup guidance"""
    
    # Check which environment variables are set
    env_vars = {
        "DATABASE_URL": bool(os.getenv("DATABASE_URL")),
        "PGHOST": bool(os.getenv("PGHOST")),  # Railway PostgreSQL
        "TELEGRAM_API_ID": bool(os.getenv("TELEGRAM_API_ID")),
        "TELEGRAM_API_HASH": bool(os.getenv("TELEGRAM_API_HASH")),
        "TELEGRAM_PHONE": bool(os.getenv("TELEGRAM_PHONE")),
        "TELEGRAM_CHANNEL_USERNAME": bool(os.getenv("TELEGRAM_CHANNEL_USERNAME")),
        "GROK_API_KEY": bool(os.getenv("GROK_API_KEY")),
        "SECRET_KEY": bool(os.getenv("SECRET_KEY")),
        "PORT": bool(os.getenv("PORT"))
    }
    
    status = {
        "database": {
            "configured": settings.is_database_configured(),
            "url_set": env_vars["DATABASE_URL"],
            "railway_pg_detected": env_vars["PGHOST"],
            "message": "Database configured and ready" if settings.is_database_configured() 
                      else "Database not configured. Add PostgreSQL service in Railway or set DATABASE_URL"
        },
        "telegram": {
            "configured": settings.is_telegram_configured(),
            "api_id_set": env_vars["TELEGRAM_API_ID"],
            "api_hash_set": env_vars["TELEGRAM_API_HASH"],
            "phone_set": env_vars["TELEGRAM_PHONE"],
            "channel_set": env_vars["TELEGRAM_CHANNEL_USERNAME"],
            "message": "Telegram configured and ready" if settings.is_telegram_configured()
                      else "Telegram not configured. Import features disabled"
        },
        "grok": {
            "configured": settings.is_grok_configured(),
            "api_key_set": env_vars["GROK_API_KEY"],
            "message": "Grok AI configured and ready" if settings.is_grok_configured()
                      else "Grok AI not configured. AI summary features disabled"
        },
        "app": {
            "environment": settings.app_env,
            "port": settings.port,
            "secret_key_set": env_vars["SECRET_KEY"],
            "message": "App configuration complete" if env_vars["SECRET_KEY"]
                      else "Using auto-generated secret key. Set SECRET_KEY for production"
        },
        "environment_hints": {}
    }
    
    # Add helpful hints for missing configuration
    if not settings.is_database_configured():
        status["environment_hints"]["database"] = (
            "In Railway: Add a PostgreSQL service from the dashboard. "
            "Railway will automatically set DATABASE_URL. "
            "Or manually set DATABASE_URL=postgresql://user:password@host:port/dbname"
        )
    
    if not settings.is_telegram_configured():
        status["environment_hints"]["telegram"] = (
            "Get your API credentials from https://my.telegram.org. "
            "Set: TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_PHONE, TELEGRAM_CHANNEL_USERNAME"
        )
    
    if not settings.is_grok_configured():
        status["environment_hints"]["grok"] = (
            "Get your API key from https://x.ai. "
            "Set: GROK_API_KEY=your-api-key"
        )
    
    if not env_vars["SECRET_KEY"]:
        status["environment_hints"]["secret"] = (
            "Generate a secure secret key for production. "
            "Set: SECRET_KEY=your-secure-random-string"
        )
    
    return ConfigStatus(**status)

@router.get("/required-vars")
async def get_required_vars():
    """List all environment variables and their current status"""
    
    required = {
        "critical": {
            "DATABASE_URL": "PostgreSQL connection string (or Railway auto-configures with PG service)",
            "PORT": "Server port (Railway provides this automatically)"
        },
        "features": {
            "TELEGRAM_API_ID": "Telegram API ID from https://my.telegram.org",
            "TELEGRAM_API_HASH": "Telegram API Hash from https://my.telegram.org",  
            "TELEGRAM_PHONE": "Your phone number for Telegram authentication",
            "TELEGRAM_CHANNEL_USERNAME": "Username of the channel to import from",
            "GROK_API_KEY": "Grok AI API key from https://x.ai"
        },
        "recommended": {
            "SECRET_KEY": "Secret key for session security",
            "APP_ENV": "Environment (development/production)",
            "SYNC_INTERVAL_HOURS": "Hours between automatic syncs (default: 6)",
            "MAX_GROK_REQUESTS_PER_HOUR": "Rate limit for Grok API (default: 100)"
        }
    }
    
    current_status = {}
    for category, vars in required.items():
        current_status[category] = {}
        for var, description in vars.items():
            current_status[category][var] = {
                "description": description,
                "is_set": bool(os.getenv(var)),
                "value": "***" if os.getenv(var) else None
            }
    
    return {
        "environment_variables": current_status,
        "configuration_complete": all([
            settings.is_database_configured(),
            settings.is_telegram_configured(), 
            settings.is_grok_configured()
        ])
    }

@router.get("/health/db")
async def get_database_health():
    """
    Check database connectivity and health status.
    Returns detailed information about the database connection and pool status.
    """
    if not settings.is_database_configured():
        raise HTTPException(
            status_code=503, 
            detail="Database not configured. Please set DATABASE_URL in Railway environment variables."
        )
    
    health = check_database_health()
    
    if health["status"] == "unhealthy":
        raise HTTPException(
            status_code=503,
            detail=f"Database is unhealthy: {health.get('error', 'Unknown error')}"
        )
    
    return health