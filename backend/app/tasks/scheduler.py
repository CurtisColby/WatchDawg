"""
WatchDawg Background Scheduler.

Runs periodic tasks inside the FastAPI process using APScheduler:
1. Scrape job         — fetches new posts from all enabled channels.
2. Resolve job        — resolves pending videos and refreshes expired URLs.
3. Dedup job          — sweeps all resolved videos for CDN fingerprint duplicates.
4. Quality upgrade    — re-resolves low-quality videos in chunks.
5. Live TV probe      — probes live stream URLs for online status (every 15 min).

The scheduler starts when the FastAPI app starts and stops on shutdown.
Intervals are configurable via environment variables.

Batch size rationale (updated):
  - Scheduled pending resolve: 200 per tick (was 50).
    Each yt-dlp call takes ~2-5s. 200 calls = 7-17 min worst case,
    well within the 30-min tick window. Keeps new channels resolving fast.
  - Scheduled expired resolve: 100 per tick (unchanged).
    Expired re-resolve is lower priority — CDN tokens are still valid for
    a few hours, so we don't need to rush these.
  - Dedup sweep: every 2 hours, no call limit (reads DB only, no network).
    Two-pass: source URL dedup (all statuses) + CDN fingerprint (resolved only).
  - Live TV probe: every 15 minutes, HEAD request per channel.
    Fast — no yt-dlp calls. 100 channels = ~8s at PROBE_TIMEOUT=8.
"""

import asyncio
import datetime
import logging
import subprocess
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from sqlalchemy import select

from app.config import settings
from app.database import async_session_factory
from app.models import Channel
from app.routers.channel import get_provider_for_channel
from app.services.scraper import ScraperService
from app.services.resolver import ResolverService

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()

# How often the dedup sweep runs, in hours.
DEDUP_INTERVAL_HOURS = 2

# Quality upgrade job — interval and chunk config.
QUALITY_UPGRADE_INTERVAL_HOURS = 6
QUALITY_UPGRADE_CHUNK_SIZE = 25
QUALITY_UPGRADE_MIN_HEIGHT = 720  # upgrade anything below 720p

# Rotating offset — incremented each tick so we walk through the full DB
_quality_upgrade_offset = 0

# Pending resolve batch size per scheduler tick.
SCHEDULED_PENDING_LIMIT = 200

# Expired re-resolve batch size per scheduler tick.
SCHEDULED_EXPIRED_LIMIT = 100

# Live TV health probe interval (minutes)
LIVE_TV_PROBE_INTERVAL_MINUTES = 15

# yt-dlp auto-update interval (hours). Checks once at startup then every N hours.
YTDLP_UPDATE_INTERVAL_HOURS = 24


async def scheduled_scrape():
    """
    Periodic scrape job. Runs every SCRAPE_INTERVAL_MINUTES.
    Iterates through all enabled channels and scrapes each one.
    Creates its own database session since it runs outside of a request.
    """
    logger.info("Scheduled scrape starting...")
    async with async_session_factory() as db:
        try:
            stmt = select(Channel).where(Channel.enabled == True)
            result = await db.execute(stmt)
            channels = result.scalars().all()

            if not channels:
                logger.info("No enabled channels found — skipping scheduled scrape.")
                return

            total_new = 0
            for channel in channels:
                try:
                    provider = get_provider_for_channel(channel)
                    scraper = ScraperService(db)
                    scrape_result = await scraper.run(
                        provider, limit=50, channel_id=channel.id
                    )

                    channel.last_scraped_at = datetime.datetime.utcnow()
                    channel.last_scrape_count = scrape_result.new
                    total_new += scrape_result.new

                    logger.info(
                        f"Scheduled scrape '{channel.name}': "
                        f"{scrape_result.new} new / {scrape_result.discovered} discovered"
                    )

                    if hasattr(provider, "close"):
                        await provider.close()

                except Exception as e:
                    logger.error(
                        f"Scheduled scrape failed for channel '{channel.name}': {e}"
                    )
                    continue

            await db.commit()
            logger.info(
                f"Scheduled scrape complete: {len(channels)} channels, "
                f"{total_new} total new videos"
            )

        except Exception as e:
            logger.error(f"Scheduled scrape failed: {e}")


async def scheduled_resolve():
    """
    Periodic resolve job. Runs every SCRAPE_INTERVAL_MINUTES alongside scrape.

    Two-pass strategy:
    1. Pending pass  — resolve new pending videos (up to SCHEDULED_PENDING_LIMIT).
    2. DASH purge    — auto-purge any DASH-only videos that slipped through.

    NOTE (Session 56): the old "expired pass" (resolver.resolve_expired) was
    removed. That method no longer exists on ResolverService — it was replaced
    by warm_tv_cache() during an earlier refactor, but the call site here was
    never updated. It threw AttributeError every tick, which the except block
    below caught and ROLLED BACK — discarding Pass 1's committed work along with
    it. That silent rollback was starving Vimeo background resolving. Removing
    the dead pass lets Pass 1 (pending) actually commit. Stale-CDN-URL refresh
    is a separate future task (Vimeo HLS tokens expire ~20 min and warrant a
    dedicated re-resolver, not this general pass).
    """
    logger.info(
        f"Scheduled batch resolve starting "
        f"(pending limit={SCHEDULED_PENDING_LIMIT})..."
    )
    async with async_session_factory() as db:
        try:
            resolver = ResolverService(db)

            # Pass 1: pending videos
            summary = await resolver.resolve_batch(limit=SCHEDULED_PENDING_LIMIT)
            logger.info(f"Scheduled resolve (pending pass): {summary}")

            # Pass 2: purge any DASH-only videos that slipped through.
            dash_purged = await resolver.purge_dash_videos()
            if dash_purged:
                logger.info(f"Scheduled resolve (DASH purge): {dash_purged} DASH-only videos removed")

        except Exception as e:
            logger.error(f"Scheduled resolve failed: {e}")


async def scheduled_dedup():
    """
    Periodic CDN fingerprint dedup sweep. Runs every DEDUP_INTERVAL_HOURS (2h).

    Two-pass sweep:
    Pass 1 — Source URL dedup across ALL video statuses.
    Pass 2 — CDN fingerprint dedup on resolved videos only.
    """
    logger.info("Scheduled dedup sweep starting...")
    async with async_session_factory() as db:
        try:
            resolver = ResolverService(db)
            summary = await resolver.purge_duplicate_cdn_files()
            logger.info(
                f"Scheduled dedup complete: "
                f"Pass 1 (source URL): {summary.get('source_url_groups', 0)} groups, "
                f"{summary.get('source_url_deleted', 0)} deleted | "
                f"Pass 2 (CDN fingerprint): {summary.get('cdn_fingerprint_groups', 0)} groups, "
                f"{summary.get('cdn_fingerprint_deleted', 0)} deleted"
            )
        except Exception as e:
            logger.error(f"Scheduled dedup sweep failed: {e}")


async def scheduled_quality_upgrade():
    """
    Quality upgrade job. Runs every QUALITY_UPGRADE_INTERVAL_HOURS.

    Re-resolves a chunk of low-quality videos (below QUALITY_UPGRADE_MIN_HEIGHT)
    and replaces their stream URL only if yt-dlp finds a better resolution.
    Never deletes videos. Skips on error and moves to the next one.
    """
    global _quality_upgrade_offset

    logger.info(
        f"Scheduled quality upgrade starting "
        f"(chunk_size={QUALITY_UPGRADE_CHUNK_SIZE}, "
        f"min_height={QUALITY_UPGRADE_MIN_HEIGHT}p, "
        f"offset={_quality_upgrade_offset})..."
    )
    async with async_session_factory() as db:
        try:
            resolver = ResolverService(db)
            summary = await resolver.upgrade_low_quality(
                min_height=QUALITY_UPGRADE_MIN_HEIGHT,
                chunk_size=QUALITY_UPGRADE_CHUNK_SIZE,
                chunk_offset=_quality_upgrade_offset,
            )
            logger.info(f"Scheduled quality upgrade complete: {summary}")
            _quality_upgrade_offset += QUALITY_UPGRADE_CHUNK_SIZE
            if _quality_upgrade_offset > 10_000:
                _quality_upgrade_offset = 0
        except Exception as e:
            logger.error(f"Scheduled quality upgrade failed: {e}")


async def scheduled_live_tv_probe():
    """
    Live TV health probe. Runs every LIVE_TV_PROBE_INTERVAL_MINUTES (15 min).

    Probes all live TV channel stream URLs for online status.
    Uses HEAD requests — fast, minimal data transfer.
    """
    logger.info("Scheduled live TV health probe starting...")
    try:
        from app.routers.live_tv import probe_all_channels
        await probe_all_channels()
    except Exception as e:
        logger.error(f"Scheduled live TV probe failed: {e}")


async def scheduled_ytdlp_update():
    """
    yt-dlp auto-update job. Runs once at startup then every YTDLP_UPDATE_INTERVAL_HOURS.

    Runs 'pip install --upgrade yt-dlp yt-dlp-ejs' inside the container.
    If a new version was installed, restarts the container so the update takes effect.
    If already up-to-date, logs and does nothing.

    Why this matters: YouTube constantly updates bot-detection. yt-dlp releases
    counter-updates very frequently. Running a stale yt-dlp is a primary cause of
    cookie bans even when the cookie itself is valid.
    """
    logger.info("yt-dlp auto-update check starting...")
    try:
        loop = asyncio.get_event_loop()

        # Run pip upgrade in a thread so we don't block the event loop.
        # pip can take a few seconds to check PyPI.
        result = await loop.run_in_executor(
            None,
            lambda: subprocess.run(
                ["pip", "install", "--upgrade", "yt-dlp", "yt-dlp-ejs"],
                capture_output=True,
                text=True,
            ),
        )

        output = result.stdout + result.stderr

        # pip prints "Successfully installed yt-dlp-YYYY.M.D" when it upgrades.
        # It prints "already satisfied" / "already up-to-date" when nothing changed.
        if "Successfully installed yt-dlp" in output:
            # Extract the new version string from pip's output for the log.
            new_version = "unknown"
            for token in output.split():
                if token.startswith("yt-dlp-") and not token.startswith("yt-dlp-ejs"):
                    new_version = token.replace("yt-dlp-", "")
                    break

            logger.info(
                f"yt-dlp upgraded to {new_version}. "
                f"Restarting container to load new version..."
            )

            # Restart the container. Docker will bring it straight back up.
            # We fire-and-forget via a separate thread — the restart kills this
            # process so we can't wait for it to return anyway.
            await loop.run_in_executor(
                None,
                lambda: subprocess.Popen(
                    ["docker", "restart", "watchdawg-backend"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                ),
            )

        else:
            # Already on the latest version — nothing to do.
            # Pull the installed version from pip output for the log.
            installed_version = "current"
            for line in output.splitlines():
                if "already satisfied" in line.lower() and "yt-dlp" in line:
                    # e.g. "Requirement already satisfied: yt-dlp in ... (2026.7.4)"
                    if "(" in line and ")" in line:
                        installed_version = line.split("(")[-1].rstrip(")")
                    break
            logger.info(
                f"yt-dlp is already up-to-date ({installed_version}). No restart needed."
            )

    except Exception as e:
        logger.error(f"yt-dlp auto-update check failed: {e}")


def start_scheduler():
    """
    Start the background scheduler with configured intervals.
    Call this during FastAPI startup.
    """
    interval_minutes = settings.scrape_interval_minutes

    # Scrape job
    scheduler.add_job(
        scheduled_scrape,
        trigger=IntervalTrigger(minutes=interval_minutes),
        id="scrape_job",
        name="Channel Scrape (All)",
        replace_existing=True,
        next_run_time=datetime.datetime.now(),
    )

    # Resolve job — runs on the same interval
    scheduler.add_job(
        scheduled_resolve,
        trigger=IntervalTrigger(minutes=interval_minutes),
        id="resolve_job",
        name="Batch Resolve",
        replace_existing=True,
        next_run_time=datetime.datetime.now(),
    )

    # Dedup sweep — runs every DEDUP_INTERVAL_HOURS
    scheduler.add_job(
        scheduled_dedup,
        trigger=IntervalTrigger(hours=DEDUP_INTERVAL_HOURS),
        id="dedup_job",
        name="CDN Duplicate Sweep",
        replace_existing=True,
        next_run_time=datetime.datetime.now(),
    )

    # Quality upgrade job
    scheduler.add_job(
        scheduled_quality_upgrade,
        trigger=IntervalTrigger(hours=QUALITY_UPGRADE_INTERVAL_HOURS),
        id="quality_upgrade_job",
        name="Quality Upgrade (Low-Res Re-resolve)",
        replace_existing=True,
        next_run_time=datetime.datetime.now(),
    )

    # Live TV health probe — every 15 minutes
    scheduler.add_job(
        scheduled_live_tv_probe,
        trigger=IntervalTrigger(minutes=LIVE_TV_PROBE_INTERVAL_MINUTES),
        id="live_tv_probe_job",
        name="Live TV Health Probe",
        replace_existing=True,
        next_run_time=datetime.datetime.now(),
    )

    # yt-dlp auto-update — runs immediately at startup, then every 24 hours.
    # next_run_time=datetime.datetime.now() means "fire on first scheduler tick"
    # (within seconds of startup), then repeat on the interval.
    scheduler.add_job(
        scheduled_ytdlp_update,
        trigger=IntervalTrigger(hours=YTDLP_UPDATE_INTERVAL_HOURS),
        id="ytdlp_update_job",
        name="yt-dlp Auto-Update",
        replace_existing=True,
        next_run_time=datetime.datetime.now(),
    )

    scheduler.start()
    logger.info(
        f"Background scheduler started. "
        f"Scrape/resolve interval: {interval_minutes} minutes. "
        f"Pending resolve limit: {SCHEDULED_PENDING_LIMIT}/tick. "
        f"Dedup sweep interval: {DEDUP_INTERVAL_HOURS} hours. "
        f"Quality upgrade interval: {QUALITY_UPGRADE_INTERVAL_HOURS} hours "
        f"(chunk={QUALITY_UPGRADE_CHUNK_SIZE}, min={QUALITY_UPGRADE_MIN_HEIGHT}p). "
        f"Live TV probe interval: {LIVE_TV_PROBE_INTERVAL_MINUTES} minutes. "
        f"yt-dlp auto-update interval: {YTDLP_UPDATE_INTERVAL_HOURS} hours (runs at startup). "
        f"All jobs run immediately at startup then repeat on their intervals."
    )


def stop_scheduler():
    """Stop the background scheduler. Call during FastAPI shutdown."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Background scheduler stopped.")
