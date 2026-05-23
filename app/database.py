"""
WatchDawg Database Engine & Session Management.

Uses async SQLAlchemy with aiosqlite for non-blocking database access.
Tables are created automatically on first startup.
The database file is stored in /app/data/ which is volume-mounted
for persistence across container restarts.
"""

import logging
import os
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from app.config import settings
from app.models import Base

logger = logging.getLogger(__name__)

# Ensure the data directory exists on the host side
_db_dir = os.path.dirname(settings.database_url.replace("sqlite+aiosqlite:///", "/"))
if _db_dir and not os.path.exists(_db_dir):
    os.makedirs(_db_dir, exist_ok=True)

# Create the async engine
# For SQLite: check_same_thread=False is required for async usage.
# journal_mode=WAL gives better concurrent read performance.
engine = create_async_engine(
    settings.database_url,
    echo=(settings.app_env == "development"),
    connect_args={"check_same_thread": False} if "sqlite" in settings.database_url else {},
)

# Session factory — use this for dependency injection in FastAPI routes
async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def init_db() -> None:
    """
    Create all tables if they don't exist.
    Called once at application startup.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables initialized successfully.")


async def get_db_session() -> AsyncSession:
    """
    FastAPI dependency that yields an async database session.

    Usage in routes:
        @router.get("/example")
        async def example(db: AsyncSession = Depends(get_db_session)):
            ...
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
