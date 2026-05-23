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
  - Dedup sweep: every 6 hours, no call limit (reads DB only, no network).
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
DEDUP_INTERVAL_HOURS = 6

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
    Periodic CDN fingerprint dedup sweep. Runs every DEDUP_INTERVAL_HOURS.

    Scans all resolved videos for duplicate CDN fingerprints — identical
    physical files served under different Vimeo video IDs (common when the
    same video is curated across multiple Vimeo channels).

    Keeps the highest-scored copy per fingerprint group and deletes the rest.

    The domain guard in extract_cdn_fingerprint() ensures only true Vimeo CDN
    URLs are fingerprinted — YouTube/Reddit URLs are always skipped, so there
    is no risk of false-positive dedup across different source platforms.

    This complements the per-resolve auto-dedup in ResolverService — that
    catches duplicates as they're resolved, while this sweep catches any
    that slipped through (e.g. videos resolved before this feature was added).
    """
    logger.info("Scheduled dedup sweep starting...")
    async with async_session_factory() as db:
        try:
            resolver = ResolverService(db)
            summary = await resolver.purge_duplicate_cdn_files()
            logger.info(
                f"Scheduled dedup complete: "
                f"{summary['duplicate_groups_found']} groups found, "
                f"{summary['deleted_count']} deleted, "
                f"{summary['kept_count']} kept, "
                f"{summary['no_fingerprint_count']} without fingerprint (skipped)"
            )
        except Exception as e:
            logger.error(f"Scheduled dedup sweep failed: {e}")


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

    # Dedup sweep — runs every 6 hours, independently of scrape/resolve
    scheduler.add_job(
        scheduled_dedup,
        trigger=IntervalTrigger(hours=DEDUP_INTERVAL_HOURS),
        id="dedup_job",
        name="CDN Duplicate Sweep",
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
        f"Jobs will run on the next interval tick (trigger manually for the first run)."
    )


def stop_scheduler():
    """Stop the background scheduler. Call during FastAPI shutdown."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Background scheduler stopped.")
