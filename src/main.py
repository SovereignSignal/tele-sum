from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from contextlib import asynccontextmanager
import logging
import os

from src.api import messages, categories, imports, exports, config, hybrid
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
    
    # Start scheduler if database is configured and any Telegram mode is available
    if settings.app_env == "production" and settings.is_database_configured() and settings.has_any_telegram_config():
        start_scheduler()
        logger.info("✅ Background scheduler started")
    else:
        logger.info("⏭️  Background scheduler skipped (missing configuration or not in production)")
    
    yield
    
    if settings.app_env == "production" and settings.is_database_configured() and settings.has_any_telegram_config():
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

# Mount static files only if directory exists
if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")
    logger.info("✅ Static files directory mounted")
else:
    logger.warning("⚠️  Static directory not found, skipping static files mount")

templates = Jinja2Templates(directory="templates")

app.include_router(messages.router, prefix="/api/messages", tags=["messages"])
app.include_router(categories.router, prefix="/api/categories", tags=["categories"]) 
app.include_router(imports.router, prefix="/api/import", tags=["import"])
app.include_router(exports.router, prefix="/api/export", tags=["export"])
app.include_router(config.router, prefix="/api/config", tags=["configuration"])
app.include_router(hybrid.router, prefix="/api/hybrid", tags=["hybrid"])

@app.get("/")
async def root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/bots")
async def bots_page(request: Request):
    return templates.TemplateResponse("bots.html", {"request": request})

@app.get("/categories")
async def categories_page(request: Request):
    return templates.TemplateResponse("categories.html", {"request": request})

@app.get("/import")
async def import_page(request: Request):
    return templates.TemplateResponse("import.html", {"request": request})

@app.get("/settings")
async def settings_page(request: Request):
    return templates.TemplateResponse("settings.html", {"request": request})

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