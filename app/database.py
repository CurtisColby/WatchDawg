"""
WatchDawg Database Engine & Session Management.

Uses async SQLAlchemy with aiosqlite for non-blocking database access.
Tables are created automatically on first startup.
The database file is stored in /app/data/ which is volume-mounted
for persistence across container restarts.

Milestone R-1: run_migrations() performs safe ALTER TABLE additions for
columns added after initial deployment. Each migration is wrapped in a
try/except so it is safe to run on a DB that already has the column.
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


async def run_migrations() -> None:
    """
    Apply incremental ALTER TABLE migrations that cannot be expressed via
    SQLAlchemy's create_all (which only creates missing tables, never adds
    columns to existing ones).

    Each statement is wrapped in its own try/except so a column that already
    exists simply logs a debug message and continues — safe to call on every
    startup regardless of DB age.
    """
    migrations = [
        # Milestone B
        ("channels", "locked",     "ALTER TABLE channels ADD COLUMN locked INTEGER DEFAULT 0"),
        ("channels", "category",   "ALTER TABLE channels ADD COLUMN category TEXT DEFAULT 'general'"),
        ("videos",   "tmdb_poster_url",  "ALTER TABLE videos ADD COLUMN tmdb_poster_url TEXT"),
        ("videos",   "tmdb_description", "ALTER TABLE videos ADD COLUMN tmdb_description TEXT"),
        ("videos",   "tmdb_year",        "ALTER TABLE videos ADD COLUMN tmdb_year INTEGER"),
        ("videos",   "tmdb_rating",      "ALTER TABLE videos ADD COLUMN tmdb_rating REAL"),
        ("videos",   "tmdb_id",          "ALTER TABLE videos ADD COLUMN tmdb_id INTEGER"),
        # Milestone R-1
        ("channels", "genre_tags",  "ALTER TABLE channels ADD COLUMN genre_tags TEXT DEFAULT ''"),
    ]

    async with engine.begin() as conn:
        for table, column, sql in migrations:
            try:
                await conn.execute(__import__("sqlalchemy").text(sql))
                logger.info(f"Migration applied: {table}.{column}")
            except Exception as exc:
                # OperationalError: duplicate column name — expected on re-run
                logger.debug(f"Migration skipped ({table}.{column}): {exc}")


async def init_db() -> None:
    """
    Create all tables if they don't exist, then run incremental column migrations.
    Called once at application startup.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables initialized successfully.")
    await run_migrations()


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
