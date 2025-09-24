from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from contextlib import asynccontextmanager
import logging

from src.api import messages, categories, imports, exports, config
from src.models.database import engine, Base
from src.services.scheduler import start_scheduler, shutdown_scheduler
from src.config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Only create tables if database is properly configured
    if settings.is_database_configured():
        try:
            Base.metadata.create_all(bind=engine)
            logger.info("✅ Database tables created/verified")
        except Exception as e:
            logger.error(f"❌ Failed to create database tables: {e}")
            logger.warning("App will start but database operations will fail")
    else:
        logger.warning("⚠️  Database not configured. Skipping table creation")
    
    # Only start scheduler if both database and Telegram are configured
    if settings.app_env == "production" and settings.is_database_configured() and settings.is_telegram_configured():
        start_scheduler()
        logger.info("✅ Background scheduler started")
    else:
        logger.info("⏭️  Background scheduler skipped (missing configuration or not in production)")
    
    yield
    
    if settings.app_env == "production" and settings.is_database_configured() and settings.is_telegram_configured():
        shutdown_scheduler()
        logger.info("Background scheduler stopped")

app = FastAPI(
    title="Tele-Sum",
    description="Telegram channel content summarization and organization system",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

app.include_router(messages.router, prefix="/api/messages", tags=["messages"])
app.include_router(categories.router, prefix="/api/categories", tags=["categories"]) 
app.include_router(imports.router, prefix="/api/import", tags=["import"])
app.include_router(exports.router, prefix="/api/export", tags=["export"])
app.include_router(config.router, prefix="/api/config", tags=["configuration"])

@app.get("/")
async def root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/health")
async def health_check():
    return {"status": "healthy", "environment": settings.app_env}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "src.main:app",
        host="0.0.0.0",
        port=settings.port,
        reload=settings.app_env == "development"
    )