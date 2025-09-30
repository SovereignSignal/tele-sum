from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.exc import OperationalError
from src.config import settings
import logging
import time

logger = logging.getLogger(__name__)

def create_db_engine(max_retries=5, initial_wait=1):
    """
    Create database engine with retry logic for Railway deployments.
    PostgreSQL might not be immediately available on cold starts.
    """
    if not settings.is_database_configured():
        raise ValueError("Database not configured. Please set DATABASE_URL in Railway environment variables.")
    
    wait_time = initial_wait
    
    for attempt in range(max_retries):
        try:
            engine = create_engine(
                settings.database_url,
                pool_pre_ping=True,
                pool_size=10,
                max_overflow=20,
                pool_recycle=3600,  # Recycle connections after 1 hour
                echo=False  # Set to True for debugging
            )
            
            # Test the connection
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
                conn.commit()
            
            logger.info("✅ Database connection established successfully")
            return engine
            
        except OperationalError as e:
            if attempt < max_retries - 1:
                logger.warning(f"Database connection attempt {attempt + 1}/{max_retries} failed. Retrying in {wait_time}s...")
                logger.debug(f"Error: {e}")
                time.sleep(wait_time)
                wait_time *= 2  # Exponential backoff
            else:
                logger.error(f"❌ Failed to connect to database after {max_retries} attempts")
                raise
    
    raise OperationalError("Could not establish database connection", None, None)

# Lazy engine creation - only create when database is configured
engine = None
SessionLocal = None
Base = declarative_base()

def get_engine():
    """
    Get or create database engine lazily.
    Only creates the engine when actually needed and database is configured.
    """
    global engine, SessionLocal

    if engine is None:
        if not settings.is_database_configured():
            raise ValueError("Database not configured. Please set DATABASE_URL in Railway environment variables.")

        try:
            engine = create_db_engine()
            SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
            logger.info("✅ Database engine created successfully")
        except Exception as e:
            logger.error(f"❌ Critical database error: {e}")
            logger.error("Please ensure PostgreSQL is provisioned in Railway and DATABASE_URL is set correctly")
            raise

    return engine

def get_db():
    """
    Database dependency for FastAPI routes.
    Ensures proper session management with automatic cleanup.
    """
    if not settings.is_database_configured():
        logger.error("Database not configured. Please set DATABASE_URL environment variable.")
        raise OperationalError("Database not configured", None, None)

    # Ensure engine is created
    get_engine()

    db = SessionLocal()
    try:
        yield db
    except Exception as e:
        logger.error(f"Database session error: {e}")
        db.rollback()
        raise
    finally:
        db.close()

def check_database_health():
    """
    Check database connectivity and health.
    Returns a dictionary with health status information.
    """
    if not settings.is_database_configured():
        return {
            "status": "not_configured",
            "error": "DATABASE_URL not configured",
            "connected": False
        }

    try:
        db_engine = get_engine()
        with db_engine.connect() as conn:
            # Check basic connectivity
            result = conn.execute(text("SELECT 1"))
            result.fetchone()

            # Get PostgreSQL version
            version_result = conn.execute(text("SELECT version()"))
            pg_version = version_result.fetchone()[0]

            # Get connection pool stats
            pool_status = {
                "size": db_engine.pool.size(),
                "checked_in": db_engine.pool.checkedin(),
                "overflow": db_engine.pool.overflow(),
                "total": db_engine.pool.checkedout()
            }

            return {
                "status": "healthy",
                "database": "PostgreSQL",
                "version": pg_version,
                "pool_status": pool_status,
                "connected": True
            }
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        return {
            "status": "unhealthy",
            "error": str(e),
            "connected": False
        }