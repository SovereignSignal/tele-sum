from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.exc import OperationalError
from src.config import settings
import logging

logger = logging.getLogger(__name__)

# Create engine with error handling
try:
    if settings.is_database_configured():
        engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=20
        )
        logger.info("✅ Database engine created successfully")
    else:
        # Create a dummy engine that will fail on actual use
        # This allows the app to start and show configuration instructions
        engine = create_engine(
            settings.database_url,
            pool_pre_ping=False,
            pool_size=1,
            max_overflow=0
        )
        logger.warning("⚠️  Using dummy database configuration. Database operations will fail.")
except Exception as e:
    logger.error(f"Failed to create database engine: {e}")
    # Create minimal engine to allow app to start
    engine = create_engine("sqlite:///./temp.db")
    logger.warning("Using temporary SQLite database")

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    if not settings.is_database_configured():
        logger.error("Database not configured. Please set DATABASE_URL environment variable.")
        raise OperationalError("Database not configured", None, None)
    
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()