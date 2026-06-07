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
8. Runs schema migrations on every boot (safe, additive only).
9. Logs a warning if WATCHDAWG_PIN is not set (PIN lock disabled).

Milestone B migrations added:
- channels.category column
- channels.last_scrape_error column
- videos.tmdb_poster_url, tmdb_description, tmdb_year, tmdb_rating, tmdb_id columns
- watch_history table
- watchlist table
- live_tv_channels table

Session 35 migrations added:
- live_tv_sources table
- live_tv_channels.is_favorite column
- live_tv_channels.sort_order column
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

    All migrations are additive (ADD COLUMN / CREATE TABLE IF NOT EXISTS).
    Safe to run on every startup — each block is guarded by existence checks.
    """
    async with async_session_factory() as db:
        try:
            # ----------------------------------------------------------------
            # channels table migrations
            # ----------------------------------------------------------------
            result = await db.execute(text("PRAGMA table_info(channels)"))
            channel_columns = [row[1] for row in result.fetchall()]

            if "locked" not in channel_columns:
                await db.execute(
                    text("ALTER TABLE channels ADD COLUMN locked INTEGER DEFAULT 0 NOT NULL")
                )
                logger.info("Migration applied: channels.locked")

            if "category" not in channel_columns:
                await db.execute(
                    text("ALTER TABLE channels ADD COLUMN category TEXT DEFAULT 'general' NOT NULL")
                )
                logger.info("Migration applied: channels.category")

            # ----------------------------------------------------------------
            # videos table migrations
            # ----------------------------------------------------------------
            result = await db.execute(text("PRAGMA table_info(videos)"))
            video_columns = [row[1] for row in result.fetchall()]

            tmdb_cols = {
                "tmdb_poster_url": "TEXT",
                "tmdb_description": "TEXT",
                "tmdb_year": "INTEGER",
                "tmdb_rating": "REAL",
                "tmdb_id": "INTEGER",
            }
            for col, col_type in tmdb_cols.items():
                if col not in video_columns:
                    await db.execute(
                        text(f"ALTER TABLE videos ADD COLUMN {col} {col_type}")
                    )
                    logger.info(f"Migration applied: videos.{col}")

            # ----------------------------------------------------------------
            # New tables — CREATE TABLE IF NOT EXISTS (fully safe to re-run)
            # ----------------------------------------------------------------
            await db.execute(text("""
                CREATE TABLE IF NOT EXISTS watch_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    video_id INTEGER NOT NULL UNIQUE REFERENCES videos(id) ON DELETE CASCADE,
                    position_seconds REAL NOT NULL DEFAULT 0.0,
                    duration_seconds REAL,
                    completed INTEGER NOT NULL DEFAULT 0,
                    last_watched_at DATETIME NOT NULL
                )
            """))

            await db.execute(text("""
                CREATE INDEX IF NOT EXISTS ix_watch_history_video_id
                ON watch_history (video_id)
            """))

            await db.execute(text("""
                CREATE TABLE IF NOT EXISTS watchlist (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    video_id INTEGER NOT NULL UNIQUE REFERENCES videos(id) ON DELETE CASCADE,
                    added_at DATETIME NOT NULL
                )
            """))

            await db.execute(text("""
                CREATE INDEX IF NOT EXISTS ix_watchlist_video_id
                ON watchlist (video_id)
            """))

            await db.execute(text("""
                CREATE TABLE IF NOT EXISTS live_tv_channels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    logo_url TEXT,
                    stream_url TEXT,
                    group_name TEXT,
                    channel_type TEXT NOT NULL DEFAULT 'real',
                    is_online INTEGER,
                    last_checked DATETIME,
                    source_m3u TEXT,
                    created_at DATETIME NOT NULL
                )
            """))

            # ----------------------------------------------------------------
            # Session 35: live_tv_channels new columns
            # ----------------------------------------------------------------
            result = await db.execute(text("PRAGMA table_info(live_tv_channels)"))
            live_tv_columns = [row[1] for row in result.fetchall()]

            if "is_favorite" not in live_tv_columns:
                await db.execute(
                    text("ALTER TABLE live_tv_channels ADD COLUMN is_favorite INTEGER NOT NULL DEFAULT 0")
                )
                logger.info("Migration applied: live_tv_channels.is_favorite")

            if "sort_order" not in live_tv_columns:
                await db.execute(
                    text("ALTER TABLE live_tv_channels ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 999")
                )
                logger.info("Migration applied: live_tv_channels.sort_order")

            # ----------------------------------------------------------------
            # Session 35: live_tv_sources table
            # ----------------------------------------------------------------
            await db.execute(text("""
                CREATE TABLE IF NOT EXISTS live_tv_sources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    label TEXT NOT NULL,
                    url TEXT NOT NULL UNIQUE,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    channel_count INTEGER NOT NULL DEFAULT 0,
                    created_at DATETIME NOT NULL,
                    last_imported_at DATETIME
                )
            """))
            logger.info("Migration checked: live_tv_sources table")

            # Session 44: live_tv_sources.group_filter column
            # ----------------------------------------------------------------
            result = await db.execute(text("PRAGMA table_info(live_tv_sources)"))
            lts_cols = {row[1] for row in result.fetchall()}
            if "group_filter" not in lts_cols:
                await db.execute(
                    text("ALTER TABLE live_tv_sources ADD COLUMN group_filter TEXT DEFAULT NULL")
                )
                logger.info("Migration applied: live_tv_sources.group_filter")

            # ----------------------------------------------------------------
            # Session 39: plex_config table
            # ----------------------------------------------------------------
            await db.execute(text("""
                CREATE TABLE IF NOT EXISTS plex_config (
                    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                    plex_url_encrypted  TEXT NOT NULL,
                    token_encrypted     TEXT NOT NULL,
                    server_name         TEXT,
                    connected_at        DATETIME NOT NULL,
                    last_verified_at    DATETIME
                )
            """))
            logger.info("Migration checked: plex_config table")

            # ----------------------------------------------------------------
            # Session 39: epg_channels table
            # ----------------------------------------------------------------
            await db.execute(text("""
                CREATE TABLE IF NOT EXISTS epg_channels (
                    id                INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_number    INTEGER NOT NULL UNIQUE,
                    name              TEXT NOT NULL,
                    epg_type          TEXT NOT NULL DEFAULT 'main',
                    source_type       TEXT NOT NULL,
                    plex_library_key  TEXT,
                    genre_filter      TEXT,
                    episodes_per_day  INTEGER NOT NULL DEFAULT 2,
                    rotation_style    TEXT NOT NULL DEFAULT 'shuffle',
                    primetime_boost   INTEGER NOT NULL DEFAULT 0,
                    logo_url          TEXT,
                    enabled           INTEGER NOT NULL DEFAULT 1,
                    sort_order        INTEGER NOT NULL DEFAULT 101,
                    created_at        DATETIME NOT NULL
                )
            """))
            await db.execute(text("""
                CREATE INDEX IF NOT EXISTS ix_epg_channels_epg_type
                ON epg_channels (epg_type)
            """))
            await db.execute(text("""
                CREATE INDEX IF NOT EXISTS ix_epg_channels_number
                ON epg_channels (channel_number)
            """))
            logger.info("Migration checked: epg_channels table")

            # ----------------------------------------------------------------
            # Session 39: epg_schedules table
            # ----------------------------------------------------------------
            await db.execute(text("""
                CREATE TABLE IF NOT EXISTS epg_schedules (
                    id                INTEGER PRIMARY KEY AUTOINCREMENT,
                    epg_channel_id    INTEGER NOT NULL REFERENCES epg_channels(id) ON DELETE CASCADE,
                    title             TEXT NOT NULL DEFAULT '',
                    subtitle          TEXT DEFAULT '',
                    description       TEXT DEFAULT '',
                    thumbnail_url     TEXT DEFAULT '',
                    stream_url        TEXT NOT NULL DEFAULT '',
                    source_type       TEXT NOT NULL DEFAULT '',
                    source_id         TEXT DEFAULT '',
                    start_time        DATETIME NOT NULL,
                    end_time          DATETIME NOT NULL,
                    duration_seconds  INTEGER NOT NULL DEFAULT 0,
                    created_at        DATETIME NOT NULL
                )
            """))
            await db.execute(text("""
                CREATE INDEX IF NOT EXISTS ix_epg_schedules_channel_time
                ON epg_schedules (epg_channel_id, start_time, end_time)
            """))
            logger.info("Migration checked: epg_schedules table")

            # ----------------------------------------------------------------
            # Session 39: epg_tv_pointers table
            # Tracks episode advancement per (channel, show) across rebuilds
            # ----------------------------------------------------------------
            await db.execute(text("""
                CREATE TABLE IF NOT EXISTS epg_tv_pointers (
                    id                INTEGER PRIMARY KEY AUTOINCREMENT,
                    epg_channel_id    INTEGER NOT NULL,
                    show_rating_key   TEXT NOT NULL,
                    episode_index     INTEGER NOT NULL DEFAULT 0,
                    updated_at        DATETIME NOT NULL,
                    UNIQUE(epg_channel_id, show_rating_key)
                )
            """))
            logger.info("Migration checked: epg_tv_pointers table")

            # Session 42: epg_movie_pointers table
            # Tracks which movie index each channel left off at across rebuilds
            # so the full library is cycled through before repeating.
            # pointer_index is the position in the sorted library list (by ratingKey
            # ascending) where the next schedule build should start pulling from.
            # ----------------------------------------------------------------
            await db.execute(text("""
                CREATE TABLE IF NOT EXISTS epg_movie_pointers (
                    id                INTEGER PRIMARY KEY AUTOINCREMENT,
                    epg_channel_id    INTEGER NOT NULL UNIQUE,
                    pointer_index     INTEGER NOT NULL DEFAULT 0,
                    library_size      INTEGER NOT NULL DEFAULT 0,
                    updated_at        DATETIME NOT NULL
                )
            """))
            logger.info("Migration checked: epg_movie_pointers table")

            # ----------------------------------------------------------------
            # Session 44: epg_xmltv_sources table
            # Stores XMLTV feed URLs (e.g. Tunarr) and their epg_type
            # ----------------------------------------------------------------
            await db.execute(text("""
                CREATE TABLE IF NOT EXISTS epg_xmltv_sources (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    label           TEXT NOT NULL,
                    url             TEXT NOT NULL UNIQUE,
                    epg_type        TEXT NOT NULL DEFAULT 'main',
                    enabled         INTEGER NOT NULL DEFAULT 1,
                    channel_filter  TEXT DEFAULT NULL,
                    last_imported_at DATETIME,
                    created_at      DATETIME NOT NULL
                )
            """))
            logger.info("Migration checked: epg_xmltv_sources table")

            # Session 44: epg_xmltv_sources.channel_filter column
            result = await db.execute(text("PRAGMA table_info(epg_xmltv_sources)"))
            xmltv_cols = {row[1] for row in result.fetchall()}
            if "channel_filter" not in xmltv_cols:
                await db.execute(
                    text("ALTER TABLE epg_xmltv_sources ADD COLUMN channel_filter TEXT DEFAULT NULL")
                )
                logger.info("Migration applied: epg_xmltv_sources.channel_filter")

            # Session 44: epg_channels.xmltv_channel_id column
            # Stores the tvg-id from the XMLTV so schedule rebuilds can match
            # programmes back to the correct epg_channels row.
            result = await db.execute(text("PRAGMA table_info(epg_channels)"))
            epg_ch_cols = {row[1] for row in result.fetchall()}
            if "xmltv_channel_id" not in epg_ch_cols:
                await db.execute(
                    text("ALTER TABLE epg_channels ADD COLUMN xmltv_channel_id TEXT DEFAULT NULL")
                )
                logger.info("Migration applied: epg_channels.xmltv_channel_id")

            await db.commit()
            logger.info("All migrations complete.")

        except Exception as e:
            logger.warning(f"Migration error (non-fatal): {e}")


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
                    category="general",
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

    # TMDb startup diagnostic
    if settings.tmdb_api_key:
        logger.info("TMDb integration is ENABLED. Movie/TV channels will get metadata enrichment.")
    else:
        logger.info("TMDB_API_KEY not set — TMDb metadata enrichment is disabled.")

    await seed_channels_from_env()
    start_scheduler()

    yield

    stop_scheduler()
    logger.info("WatchDawg backend shutting down.")


app = FastAPI(
    title="WatchDawg",
    description="Secure media aggregation and streaming backend.",
    version="0.5.0",
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
from app.routers.health import router as health_router          # noqa: E402
from app.routers.auth import router as auth_router              # noqa: E402
from app.routers.feed import router as feed_router              # noqa: E402
from app.routers.resolve import router as resolve_router        # noqa: E402
from app.routers.skip import router as skip_router              # noqa: E402
from app.routers.favorite import router as favorite_router      # noqa: E402
from app.routers.channel import router as channel_router        # noqa: E402
from app.routers.library import router as library_router        # noqa: E402
from app.routers.proxy import router as proxy_router            # noqa: E402
from app.routers.watchlist import router as watchlist_router    # noqa: E402
from app.routers.history import router as history_router        # noqa: E402
from app.routers.live_tv import router as live_tv_router        # noqa: E402
from app.routers.plex import router as plex_router              # noqa: E402
from app.routers.epg import router as epg_router                # noqa: E402
from app.routers.web_ui import router as web_ui_router          # noqa: E402

app.include_router(health_router)
app.include_router(auth_router)
app.include_router(feed_router)
app.include_router(resolve_router)
app.include_router(skip_router)
app.include_router(favorite_router)
app.include_router(channel_router)
app.include_router(library_router)
app.include_router(proxy_router)
app.include_router(watchlist_router)
app.include_router(history_router)
app.include_router(live_tv_router)
app.include_router(plex_router)
app.include_router(epg_router)

# Web UI must be registered LAST so its "/" route doesn't shadow the API
app.include_router(web_ui_router)
