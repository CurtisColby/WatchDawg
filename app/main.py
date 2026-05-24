"""
WatchDawg Backend — Application Entry Point.

This is the FastAPI application root. It:
1. Initializes the database on startup.
2. Seeds default channels from .env if the channels table is empty.
3. Registers all API routers.
4. Serves the web test UI at the root URL.
5. Starts the background scheduler for periodic scraping.
6. Configures CORS for local development.
7. Installs the in-memory log ring buffer so /debug/logs works.
8. Runs the locked column migration if it doesn't already exist.
9. Logs a warning if WATCHDAWG_PIN is not set (PIN lock disabled).
"""

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, func, text

from app.config import settings
from app.database import init_db, async_session_factory
from app.models import Channel
from app.tasks.scheduler import start_scheduler, stop_scheduler

# Configure logging
logging.basicConfig(
    level=logging.DEBUG if settings.app_env == "development" else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def _run_migrations():
    """
    Apply any schema migrations that aren't handled by init_db().

    Currently handles:
    - Adding the 'locked' column to the channels table (PIN lock feature).
      Safe to run on every startup — uses IF NOT EXISTS / try-except pattern.
    """
    async with async_session_factory() as db:
        try:
            # SQLite: check if 'locked' column exists by querying PRAGMA
            result = await db.execute(text("PRAGMA table_info(channels)"))
            columns = [row[1] for row in result.fetchall()]
            if "locked" not in columns:
                await db.execute(
                    text("ALTER TABLE channels ADD COLUMN locked INTEGER DEFAULT 0 NOT NULL")
                )
                await db.commit()
                logger.info("Migration applied: added 'locked' column to channels table.")
            else:
                logger.debug("Migration check: 'locked' column already exists — skipping.")
        except Exception as e:
            logger.warning(f"Migration check failed (non-fatal): {e}")


async def seed_channels_from_env():
    """
    If the channels table is empty, seed it with subreddits from .env.
    This provides backwards compatibility — existing users who have
    REDDIT_SUBREDDITS in their .env will get those auto-imported as
    channels on first boot after the upgrade.
    """
    async with async_session_factory() as db:
        count_result = await db.execute(select(func.count(Channel.id)))
        count = count_result.scalar()

        if count > 0:
            logger.info(f"Channels table has {count} entries — skipping seed.")
            return

        from app.routers.channel import detect_channel_type

        seeded = 0
        for subreddit in settings.subreddit_list:
            try:
                detected = detect_channel_type(f"r/{subreddit}")
                channel = Channel(
                    name=detected["name"],
                    channel_type=detected["channel_type"],
                    url=detected["url"],
                    unique_key=detected["unique_key"],
                    enabled=True,
                    locked=False,
                )
                db.add(channel)
                seeded += 1
            except Exception as e:
                logger.warning(f"Failed to seed subreddit '{subreddit}': {e}")

        if seeded > 0:
            await db.commit()
            logger.info(
                f"Seeded {seeded} channel(s) from REDDIT_SUBREDDITS env var."
            )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle events."""
    logger.info("WatchDawg backend starting up...")

    # Install the in-memory log buffer FIRST so all subsequent log calls
    # are captured and available via /debug/logs in the browser UI.
    from app.routers.proxy import install_log_buffer
    install_log_buffer()
    logger.info("In-memory log buffer installed — /debug/logs is active.")

    await init_db()
    logger.info("Database initialized.")
    logger.info(f"Environment: {settings.app_env}")

    # Run schema migrations (safe to run on every boot)
    await _run_migrations()

    # PIN lock startup diagnostic
    if settings.watchdawg_pin:
        logger.info(
            "PIN lock is ENABLED. Locked channels will be hidden until "
            "POST /auth/unlock is called with the correct PIN."
        )
    else:
        logger.warning(
            "WATCHDAWG_PIN is not set in .env — PIN lock is DISABLED. "
            "All content is visible to anyone who can reach the API. "
            "Set WATCHDAWG_PIN in .env to enable channel locking."
        )

    await seed_channels_from_env()
    start_scheduler()

    yield

    stop_scheduler()
    logger.info("WatchDawg backend shutting down.")


app = FastAPI(
    title="WatchDawg",
    description="Secure media aggregation and streaming backend.",
    version="0.4.0",
    lifespan=lifespan,
)

# CORS — allow the web test UI and local Android emulator
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.app_env == "development" else [],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Register Routers ---
from app.routers.health import router as health_router      # noqa: E402
from app.routers.auth import router as auth_router          # noqa: E402
from app.routers.feed import router as feed_router          # noqa: E402
from app.routers.resolve import router as resolve_router    # noqa: E402
from app.routers.skip import router as skip_router          # noqa: E402
from app.routers.favorite import router as favorite_router  # noqa: E402
from app.routers.channel import router as channel_router    # noqa: E402
from app.routers.library import router as library_router    # noqa: E402
from app.routers.proxy import router as proxy_router        # noqa: E402
from app.routers.web_ui import router as web_ui_router      # noqa: E402

app.include_router(health_router)
app.include_router(auth_router)
app.include_router(feed_router)
app.include_router(resolve_router)
app.include_router(skip_router)
app.include_router(favorite_router)
app.include_router(channel_router)
app.include_router(library_router)
app.include_router(proxy_router)

# Web UI must be registered LAST so its "/" route doesn't shadow the API
app.include_router(web_ui_router)
