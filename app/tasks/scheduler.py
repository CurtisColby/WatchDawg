"""
WatchDawg Background Scheduler.

Runs periodic tasks inside the FastAPI process using APScheduler:
1. Scrape job    — fetches new posts from all enabled channels.
2. Resolve job   — resolves pending videos and refreshes expired URLs.
3. Dedup job     — sweeps all resolved videos for CDN fingerprint duplicates
                   and removes lower-scored copies automatically.

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
"""

import datetime
import logging
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
# Reduced from 6h -> 2h: the sweep is pure DB (no network calls), costs
# milliseconds even at 9k+ videos, and catches source-URL duplicates
# (pending videos scraped from multiple channels) much faster.
DEDUP_INTERVAL_HOURS = 2

# Quality upgrade job — interval and chunk config.
# Runs every 6 hours. Each pass re-resolves up to 25 low-quality videos.
# chunk_offset rotates by QUALITY_CHUNK_SIZE each tick so different videos
# are checked each run without repeating the same top-N every time.
QUALITY_UPGRADE_INTERVAL_HOURS = 6
QUALITY_UPGRADE_CHUNK_SIZE = 25
QUALITY_UPGRADE_MIN_HEIGHT = 720  # upgrade anything below 720p

# Rotating offset — incremented each tick so we walk through the full DB
# set across multiple runs without always re-checking the same top rows.
_quality_upgrade_offset = 0

# Pending resolve batch size per scheduler tick.
# Raised from 50 → 200 so new channels resolve within 1-2 ticks instead of
# potentially sitting for hours behind a large backlog.
SCHEDULED_PENDING_LIMIT = 200

# Expired re-resolve batch size per scheduler tick.
# Kept at 100 — expired videos still play (token hasn't fully died yet),
# so this is lower urgency than pending.
SCHEDULED_EXPIRED_LIMIT = 100


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
    1. Pending pass  — resolve new pending videos (up to SCHEDULED_PENDING_LIMIT),
                       prioritised by reddit_score so high-value content resolves
                       first. Raised to 200 so new channels clear their backlog
                       within 1-2 scheduler ticks instead of many hours.
    2. Expired pass  — re-resolve 'resolved' videos whose cached stream URL is
                       older than 4 hours (CDN tokens expire). Capped at
                       SCHEDULED_EXPIRED_LIMIT to avoid hammering platforms.
    3. DASH purge    — auto-purge any DASH-only videos that slipped through.
                       DASH is unplayable in browsers; this keeps the feed clean.
    """
    logger.info(
        f"Scheduled batch resolve starting "
        f"(pending limit={SCHEDULED_PENDING_LIMIT}, expired limit={SCHEDULED_EXPIRED_LIMIT})..."
    )
    async with async_session_factory() as db:
        try:
            resolver = ResolverService(db)

            # Pass 1: pending videos
            summary = await resolver.resolve_batch(limit=SCHEDULED_PENDING_LIMIT)
            logger.info(f"Scheduled resolve (pending pass): {summary}")

            # Pass 2: expired resolved videos — re-resolve stale CDN URLs
            expired_summary = await resolver.resolve_expired(limit=SCHEDULED_EXPIRED_LIMIT)
            logger.info(f"Scheduled resolve (expired pass): {expired_summary}")

            # Pass 3: purge any DASH-only videos that slipped through.
            dash_purged = await resolver.purge_dash_videos()
            if dash_purged:
                logger.info(f"Scheduled resolve (DASH purge): {dash_purged} DASH-only videos removed")

        except Exception as e:
            logger.error(f"Scheduled resolve failed: {e}")


async def scheduled_dedup():
    """
    Periodic CDN fingerprint dedup sweep. Runs every DEDUP_INTERVAL_HOURS (2h).

    Two-pass sweep:

    Pass 1 — Source URL dedup across ALL video statuses (pending, failed, resolved).
      Extracts the Vimeo numeric ID from source_url and groups videos by it. This
      catches the common case where the same Vimeo video was scraped from multiple
      channels — one copy is pending, another is already resolved. The pending
      duplicate is deleted without ever needing a yt-dlp resolution call. This is
      why pending videos were "disappearing" when manually resolved: resolve() was
      catching them via dedup_after_resolve(), but the scheduled sweep was missing
      them because it only looked at resolved videos. Now the sweep catches them
      proactively before anyone tries to play them.

    Pass 2 — CDN fingerprint dedup on resolved videos only.
      Fingerprints the physical CDN storage path from resolved_stream_url and
      removes lower-scored copies that physically share the same CDN file
      (re-uploads, mirrors, same video on multiple Vimeo accounts). Domain-gated
      to Vimeo CDN URLs only — YouTube/Reddit never fingerprinted.
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

    Uses a rotating chunk_offset so successive ticks check different rows
    rather than always hammering the same top-scored low-quality videos.
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
            # Advance offset for next tick. If we've walked past a reasonable
            # ceiling (10k rows), reset to 0 to start the cycle again.
            _quality_upgrade_offset += QUALITY_UPGRADE_CHUNK_SIZE
            if _quality_upgrade_offset > 10_000:
                _quality_upgrade_offset = 0
        except Exception as e:
            logger.error(f"Scheduled quality upgrade failed: {e}")


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
        next_run_time=None,  # Don't run immediately — user can trigger manually first
    )

    # Resolve job — runs on the same interval
    scheduler.add_job(
        scheduled_resolve,
        trigger=IntervalTrigger(minutes=interval_minutes),
        id="resolve_job",
        name="Batch Resolve",
        replace_existing=True,
        next_run_time=None,
    )

    # Dedup sweep — runs every DEDUP_INTERVAL_HOURS, independently of scrape/resolve
    scheduler.add_job(
        scheduled_dedup,
        trigger=IntervalTrigger(hours=DEDUP_INTERVAL_HOURS),
        id="dedup_job",
        name="CDN Duplicate Sweep",
        replace_existing=True,
        next_run_time=None,
    )

    # Quality upgrade job — re-resolves low-quality videos in chunks across the day
    scheduler.add_job(
        scheduled_quality_upgrade,
        trigger=IntervalTrigger(hours=QUALITY_UPGRADE_INTERVAL_HOURS),
        id="quality_upgrade_job",
        name="Quality Upgrade (Low-Res Re-resolve)",
        replace_existing=True,
        next_run_time=None,
    )

    scheduler.start()
    logger.info(
        f"Background scheduler started. "
        f"Scrape/resolve interval: {interval_minutes} minutes. "
        f"Pending resolve limit: {SCHEDULED_PENDING_LIMIT}/tick. "
        f"Expired resolve limit: {SCHEDULED_EXPIRED_LIMIT}/tick. "
        f"Dedup sweep interval: {DEDUP_INTERVAL_HOURS} hours. "
        f"Quality upgrade interval: {QUALITY_UPGRADE_INTERVAL_HOURS} hours "
        f"(chunk={QUALITY_UPGRADE_CHUNK_SIZE}, min={QUALITY_UPGRADE_MIN_HEIGHT}p). "
        f"Jobs will run on the next interval tick (trigger manually for the first run)."
    )


def stop_scheduler():
    """Stop the background scheduler. Call during FastAPI shutdown."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Background scheduler stopped.")
