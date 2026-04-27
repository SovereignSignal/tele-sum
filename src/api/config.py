from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Dict, Any
import os

from src.config import settings
from src.models.database import check_database_health
from src.services.scheduler import get_scheduler_status

router = APIRouter()


class ConfigStatus(BaseModel):
    database: Dict[str, Any]
    bot: Dict[str, Any]
    ai: Dict[str, Any]
    scheduler: Dict[str, Any]
    app: Dict[str, Any]
    environment_hints: Dict[str, str]


@router.get("/status", response_model=ConfigStatus)
async def get_config_status():
    """Current configuration status. AI provider config lives in the api_providers DB table; env vars are overrides."""
    env = {
        "DATABASE_URL": bool(os.getenv("DATABASE_URL")),
        "PGHOST": bool(os.getenv("PGHOST")),
        "TELEGRAM_BOT_TOKENS": bool(os.getenv("TELEGRAM_BOT_TOKENS")),
        "AI_PROVIDER": bool(os.getenv("AI_PROVIDER")),
        "AI_API_KEY": bool(os.getenv("AI_API_KEY")),
        "SECRET_KEY": bool(os.getenv("SECRET_KEY")),
    }
    sched = get_scheduler_status()

    status = {
        "database": {
            "configured": settings.is_database_configured(),
            "url_set": env["DATABASE_URL"],
            "railway_pg_detected": env["PGHOST"],
        },
        "bot": {
            "configured": settings.is_bot_configured(),
            "tokens_set": env["TELEGRAM_BOT_TOKENS"],
            "bot_count": len(settings.get_bot_tokens()),
        },
        "ai": {
            "env_override": settings.is_ai_configured(),
            "provider": settings.ai_provider,
            "model": settings.ai_model,
            "note": "When env override is false, provider/model/key are read from api_providers table.",
        },
        "scheduler": {
            "running": sched["is_running"],
            "sync_interval_hours": settings.sync_interval_hours,
            "jobs": sched["jobs"],
        },
        "app": {
            "environment": settings.app_env,
            "port": settings.port,
            "secret_key_set": env["SECRET_KEY"],
        },
        "environment_hints": {},
    }

    if not settings.is_database_configured():
        status["environment_hints"]["database"] = "Set DATABASE_URL or Railway PG* vars."
    if not settings.is_bot_configured():
        status["environment_hints"]["bot"] = "Set TELEGRAM_BOT_TOKENS (comma-separated)."
    if not env["SECRET_KEY"]:
        status["environment_hints"]["secret"] = "Set SECRET_KEY for production session stability."

    return ConfigStatus(**status)


@router.get("/health/db")
async def get_database_health():
    if not settings.is_database_configured():
        raise HTTPException(status_code=503, detail="Database not configured")
    health = check_database_health()
    if health["status"] == "unhealthy":
        raise HTTPException(status_code=503, detail=f"Database unhealthy: {health.get('error')}")
    return health
