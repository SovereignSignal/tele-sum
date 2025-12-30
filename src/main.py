"""
Tele-Sum: Telegram Content Sync & AI Processing

Features:
- Automatic sync from Telegram on schedule
- Grok AI processing of messages (especially Twitter/X links)
- API key protected web interface for monitoring
- Protected API endpoints for data access
"""

from fastapi import FastAPI, Request, HTTPException, Depends, Response, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse, HTMLResponse
from starlette.middleware.sessions import SessionMiddleware
from contextlib import asynccontextmanager
import logging
import os
import secrets

from src.api import messages, categories, imports, exports, config, hybrid, entries
from src.models.database import get_engine, Base
from src.services.scheduler import start_scheduler, shutdown_scheduler, get_scheduler_status
from src.config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# Authentication dependency
async def require_auth(request: Request):
    """Check if user is authenticated via session or API key header"""
    # Check session first (for browser access)
    if request.session.get("authenticated"):
        return True
    
    # Check API key header (for programmatic access)
    api_key = request.headers.get("X-API-Key")
    if api_key and settings.api_key and api_key == settings.api_key:
        return True
    
    # Check query parameter (for simple access)
    api_key = request.query_params.get("api_key")
    if api_key and settings.api_key and api_key == settings.api_key:
        return True
    
    # If no API key is configured, allow access (for initial setup)
    if not settings.api_key:
        logger.warning("⚠️  No API_KEY configured - access is unprotected!")
        return True
    
    return False


async def auth_required(request: Request):
    """Dependency that raises 401 if not authenticated"""
    if not await require_auth(request):
        raise HTTPException(status_code=401, detail="Authentication required")
    return True


async def seed_default_categories_if_empty():
    """Seed default categories if none exist"""
    from src.models.models import Category
    from src.models import database

    # Must use database.SessionLocal (not the imported one) since it's set after get_engine()
    db = database.SessionLocal()
    try:
        existing = db.query(Category).filter(Category.user_id == 1).count()
        if existing == 0:
            logger.info("No categories found - seeding defaults...")
            
            defaults = [
                {"name": "Market Analysis", "description": "Price movements, market trends, trading analysis", "keywords": ["price", "market", "trading", "bull", "bear", "pump", "dump"]},
                {"name": "Protocol Updates", "description": "Project announcements, upgrades, launches", "keywords": ["launch", "upgrade", "mainnet", "testnet", "release", "update"]},
                {"name": "Security Alerts", "description": "Hacks, exploits, vulnerabilities, scams", "keywords": ["hack", "exploit", "vulnerability", "scam", "rug", "drain"]},
                {"name": "Regulatory News", "description": "Government actions, legal developments, compliance", "keywords": ["sec", "regulation", "legal", "lawsuit", "compliance", "ban"]},
                {"name": "DeFi/Yield", "description": "DeFi protocols, yield farming, liquidity", "keywords": ["defi", "yield", "apy", "liquidity", "farming", "stake"]},
                {"name": "NFT/Gaming", "description": "NFT drops, gaming, metaverse", "keywords": ["nft", "gaming", "metaverse", "mint", "collection"]},
                {"name": "Airdrops/Opportunities", "description": "Airdrops, incentives, opportunities", "keywords": ["airdrop", "incentive", "reward", "claim", "eligible"]},
                {"name": "General", "description": "Other crypto-related content", "keywords": []},
            ]
            
            for cat_data in defaults:
                cat = Category(
                    user_id=1,
                    name=cat_data["name"],
                    description=cat_data["description"],
                    keywords=cat_data["keywords"]
                )
                db.add(cat)
            
            db.commit()
            logger.info(f"✅ Seeded {len(defaults)} default categories")
    except Exception as e:
        logger.warning(f"Could not seed categories: {e}")
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle"""
    
    logger.info("=" * 60)
    logger.info("🚀 Tele-Sum Starting")
    logger.info("=" * 60)
    
    # Initialize database
    if settings.is_database_configured():
        try:
            db_engine = get_engine()
            Base.metadata.create_all(bind=db_engine)
            logger.info("✅ Database tables created/verified")
        except Exception as e:
            logger.error(f"❌ Failed to create database tables: {e}")
    else:
        logger.error("❌ DATABASE_URL not configured")
    
    # Seed default categories
    if settings.is_database_configured():
        await seed_default_categories_if_empty()
    
    # Start background scheduler for automatic sync
    if settings.is_database_configured() and settings.has_any_telegram_config():
        start_scheduler()
        logger.info("✅ Background scheduler started")
        logger.info(f"   - Telegram sync: every {settings.sync_interval_hours} hours")
        logger.info(f"   - AI processing: every 2 minutes")
        logger.info(f"   - Retry failed: every 15 minutes")
    else:
        logger.warning("⚠️  Scheduler not started - missing Telegram config")
    
    # Log status
    logger.info("-" * 60)
    logger.info(f"Telegram Phone: {'✅' if settings.is_telegram_configured() else '❌'}")
    logger.info(f"Telegram Bot: {'✅' if settings.is_bot_configured() else '❌'}")
    logger.info(f"Grok AI: {'✅' if settings.is_grok_configured() else '❌'}")
    logger.info(f"API Key: {'✅ Protected' if settings.api_key else '⚠️  UNPROTECTED'}")
    logger.info("=" * 60)
    
    yield
    
    shutdown_scheduler()
    logger.info("Tele-Sum stopped")


app = FastAPI(
    title="Tele-Sum",
    description="Telegram channel content summarization system",
    version="2.0.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
)

# Session middleware for login persistence
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key or secrets.token_urlsafe(32),
    session_cookie="telesum_session",
    max_age=86400 * 7,  # 7 days
    same_site="lax",
    https_only=settings.app_env == "production"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files
if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="templates")


# ============================================
# Public Routes
# ============================================

@app.get("/health")
async def health_check():
    """Health check for Railway"""
    return {
        "status": "healthy",
        "scheduler": get_scheduler_status()
    }


@app.get("/login")
async def login_page(request: Request):
    """Login page"""
    # If no API key configured, redirect to home
    if not settings.api_key:
        return RedirectResponse(url="/", status_code=302)
    
    # If already authenticated, redirect to home
    if request.session.get("authenticated"):
        return RedirectResponse(url="/", status_code=302)
    
    return templates.TemplateResponse("login.html", {
        "request": request,
        "error": None
    })


@app.post("/login")
async def login_submit(request: Request, api_key: str = Form(...)):
    """Handle login form submission"""
    if settings.api_key and api_key == settings.api_key:
        request.session["authenticated"] = True
        return RedirectResponse(url="/", status_code=302)
    
    return templates.TemplateResponse("login.html", {
        "request": request,
        "error": "Invalid API key"
    })


@app.get("/logout")
async def logout(request: Request):
    """Logout and clear session"""
    request.session.clear()
    return RedirectResponse(url="/login", status_code=302)


# ============================================
# Protected Frontend Routes
# ============================================

async def protected_page(request: Request, template: str):
    """Helper to render protected pages"""
    if not await require_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(template, {"request": request})


@app.get("/")
async def root(request: Request):
    return await protected_page(request, "index.html")


@app.get("/categories")
async def categories_page(request: Request):
    return await protected_page(request, "categories.html")


@app.get("/import")
async def import_page(request: Request):
    return await protected_page(request, "import.html")


@app.get("/settings")
async def settings_page(request: Request):
    return await protected_page(request, "settings.html")


# ============================================
# Protected API Routes
# ============================================

# Include API routers with authentication
app.include_router(
    messages.router, 
    prefix="/api/messages", 
    tags=["messages"],
    dependencies=[Depends(auth_required)]
)
app.include_router(
    categories.router, 
    prefix="/api/categories", 
    tags=["categories"],
    dependencies=[Depends(auth_required)]
)
app.include_router(
    imports.router, 
    prefix="/api/import", 
    tags=["import"],
    dependencies=[Depends(auth_required)]
)
app.include_router(
    exports.router, 
    prefix="/api/export", 
    tags=["export"],
    dependencies=[Depends(auth_required)]
)
app.include_router(
    config.router, 
    prefix="/api/config", 
    tags=["configuration"],
    dependencies=[Depends(auth_required)]
)
app.include_router(
    hybrid.router,
    prefix="/api/hybrid",
    tags=["hybrid"],
    dependencies=[Depends(auth_required)]
)

# Public API for entries (read-only, production data)
# Protected by API key but provides clean public interface
app.include_router(
    entries.router,
    prefix="/api/entries",
    tags=["entries"],
    dependencies=[Depends(auth_required)]
)


# ============================================
# Additional Protected Endpoints
# ============================================

@app.get("/api/stats", dependencies=[Depends(auth_required)])
async def get_stats():
    """Get processing statistics"""
    from src.models.models import Message, Summary, ProcessingStatus
    from src.models import database

    db = database.SessionLocal()
    try:
        total = db.query(Message).count()
        success = db.query(Summary).filter(Summary.processing_status == ProcessingStatus.SUCCESS).count()
        pending = db.query(Summary).filter(Summary.processing_status == ProcessingStatus.PENDING).count()
        failed = db.query(Summary).filter(Summary.processing_status == ProcessingStatus.FAILED).count()
        
        return {
            "total_messages": total,
            "processed": success,
            "pending": pending,
            "failed": failed,
            "success_rate": round(success / total * 100, 1) if total > 0 else 0,
            "scheduler": get_scheduler_status()
        }
    finally:
        db.close()


@app.post("/api/trigger-sync", dependencies=[Depends(auth_required)])
async def trigger_sync():
    """Manually trigger a sync"""
    from src.services.scheduler import sync_and_process
    import asyncio
    
    asyncio.create_task(sync_and_process())
    return {"message": "Sync triggered in background"}


@app.post("/api/trigger-process", dependencies=[Depends(auth_required)])
async def trigger_process():
    """Manually trigger processing"""
    from src.services.processor import MessageProcessor
    from src.models import database

    processor = MessageProcessor()
    db = database.SessionLocal()
    try:
        processed = await processor.process_batch(db, limit=20, include_retries=True)
        return {"message": f"Processed {processed} messages"}
    finally:
        db.close()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "src.main:app",
        host="0.0.0.0",
        port=settings.port,
        reload=settings.app_env == "development"
    )
