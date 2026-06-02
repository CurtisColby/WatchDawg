"""
WatchDawg Backend — Application Entry Point.

This is the FastAPI application root. It:
1. Initializes the database on startup.
2. Seeds default channels from .env if the channels table is empty.
3. Registers all API routers.
4. Serves the web test UI at the root URL.
5. Starts the background scheduler for periodic scraping.
6. Configures CORS for local development.
"""

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, func

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
    await init_db()
    logger.info("Database initialized.")
    logger.info(f"Environment: {settings.app_env}")

    await seed_channels_from_env()
    start_scheduler()

    yield

    stop_scheduler()
    logger.info("WatchDawg backend shutting down.")


app = FastAPI(
    title="WatchDawg",
    description="Secure media aggregation and streaming backend.",
    version="0.2.0",
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
from app.routers.feed import router as feed_router          # noqa: E402
from app.routers.resolve import router as resolve_router    # noqa: E402
from app.routers.skip import router as skip_router          # noqa: E402
from app.routers.favorite import router as favorite_router  # noqa: E402
from app.routers.channel import router as channel_router    # noqa: E402
from app.routers.library import router as library_router    # noqa: E402
from app.routers.web_ui import router as web_ui_router      # noqa: E402

app.include_router(health_router)
app.include_router(feed_router)
app.include_router(resolve_router)
app.include_router(skip_router)
app.include_router(favorite_router)
app.include_router(channel_router)
app.include_router(library_router)

# Web UI must be registered LAST so its "/" route doesn't shadow the API
app.include_router(web_ui_router)
