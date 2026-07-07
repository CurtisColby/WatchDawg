"""
WatchDawg Background Scheduler.

Runs periodic tasks inside the FastAPI process using APScheduler:
1. Scrape job         — fetches new posts from all enabled channels.
2. Resolve job        — resolves pending videos (standard path) and keeps the
                        TV-path URL cache warm for TiviMate (TV warm pass).
3. Dedup job          — sweeps all resolved videos for CDN fingerprint duplicates.
4. Quality upgrade    — re-resolves low-quality videos in chunks.
5. Live TV probe      — probes live stream URLs for online status (every 15 min).

The scheduler starts when the FastAPI app starts and stops on shutdown.
Intervals are configurable via environment variables.

Batch size rationale (updated):
  - Scheduled pending resolve: 200 per tick (was 50).
    Each yt-dlp call takes ~2-5s. 200 calls = 7-17 min worst case,
    well within the 30-min tick window. Keeps new channels resolving fast.
  - Scheduled TV cache warm: 100 per tick.
    Pre-resolves YouTube videos via the TV path (split video + audio URLs)
    so TiviMate playback is always an instant cache hit. YouTube extraction
    takes 10-30+ s each, so 100 per tick is the throughput ceiling; the
    first ticks after deploy work through the backlog, then it's only
    refreshing URLs older than the 3 h token TTL.
  - Dedup sweep: every 2 hours, no call limit (reads DB only, no network).
    Two-pass: source URL dedup (all statuses) + CDN fingerprint (resolved only).
  - Live TV probe: every 15 minutes, HEAD request per channel.
    Fast — no yt-dlp calls. 100 channels = ~8s at PROBE_TIMEOUT=8.
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
DEDUP_INTERVAL_HOURS = 2

# Quality upgrade job — interval and chunk config.
QUALITY_UPGRADE_INTERVAL_HOURS = 6
QUALITY_UPGRADE_CHUNK_SIZE = 25
QUALITY_UPGRADE_MIN_HEIGHT = 720  # upgrade anything below 720p

# Rotating offset — incremented each tick so we walk through the full DB
_quality_upgrade_offset = 0

# Pending resolve batch size per scheduler tick.
SCHEDULED_PENDING_LIMIT = 200

# TV cache warm batch size per scheduler tick — YouTube videos whose TV-path
# URL pair (resolved_stream_url + resolved_audio_url) is missing or stale.
# Warming these in the background is what makes TiviMate playback an instant
# cache hit instead of a 25-40s yt-dlp wait (the YouTube 502 fix).
SCHEDULED_TV_WARM_LIMIT = 100

# Live TV health probe interval (minutes)
LIVE_TV_PROBE_INTERVAL_MINUTES = 15

# EPG schedule rebuild interval (hours). Gap-fill logic in the pseudo
# scheduler means only channels with schedule windows shorter than 48h
# actually write new rows — a fully covered rebuild is nearly free.
EPG_REBUILD_INTERVAL_HOURS = 2


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


# Locked-channel resolve quota per tick — resolved before general content.
# Locked channels = adult/PIN-gated content. Pre-warming their cache means
# TiviMate playback hits a warm cache hit (~instant) instead of waiting for
# yt-dlp to run at play time.
SCHEDULED_LOCKED_PENDING_LIMIT = 100   # pending locked videos per tick
SCHEDULED_LOCKED_EXPIRED_LIMIT = 60    # expired locked videos per tick


async def _resolve_locked_channels_priority(resolver: "ResolverService", db) -> dict:
    """
    Pre-warm the resolver cache for locked (adult/PIN-gated) channel content.

    Runs BEFORE the general resolve pass each tick so locked content is
    always warm in cache when TiviMate requests it.

    Resolves:
    - Pending videos from locked channels (up to SCHEDULED_LOCKED_PENDING_LIMIT)
    - Expired resolved videos from locked channels (up to SCHEDULED_LOCKED_EXPIRED_LIMIT)
    """
    from app.models import Video
    import datetime as _dt

    # Get IDs of all locked channels
    locked_ch_stmt = select(Channel).where(Channel.locked == True, Channel.enabled == True)
    locked_ch_result = await db.execute(locked_ch_stmt)
    locked_channels = locked_ch_result.scalars().all()

    if not locked_channels:
        return {"locked_pending": 0, "locked_expired": 0}

    locked_ids = [ch.id for ch in locked_channels]
    logger.info(
        f"Scheduled resolve (locked priority): {len(locked_ids)} locked channels — "
        f"pre-warming cache (pending={SCHEDULED_LOCKED_PENDING_LIMIT}, "
        f"expired={SCHEDULED_LOCKED_EXPIRED_LIMIT})"
    )

    # Pending videos from locked channels
    pending_stmt = (
        select(Video)
        .where(
            Video.channel_id.in_(locked_ids),
            Video.resolution_status == "pending",
        )
        .order_by(Video.reddit_score.desc().nullslast())
        .limit(SCHEDULED_LOCKED_PENDING_LIMIT)
    )
    pending_result = await db.execute(pending_stmt)
    pending_videos = pending_result.scalars().all()

    # Resolved locked videos needing a TV-path refresh: either never resolved
    # via the TV path (resolved_audio_url NULL) or URLs older than 2h.
    from sqlalchemy import or_ as _or
    expiry_cutoff = _dt.datetime.utcnow() - _dt.timedelta(hours=2)
    expired_stmt = (
        select(Video)
        .where(
            Video.channel_id.in_(locked_ids),
            Video.resolution_status == "resolved",
            _or(
                Video.resolved_audio_url.is_(None),
                Video.resolved_at.is_(None),
                Video.resolved_at < expiry_cutoff,
            ),
        )
        .order_by(Video.reddit_score.desc().nullslast())
        .limit(SCHEDULED_LOCKED_EXPIRED_LIMIT)
    )
    expired_result = await db.execute(expired_stmt)
    expired_videos = expired_result.scalars().all()

    # Pending locked videos go through the STANDARD resolver (metadata
    # backfill, permanent-failure marking, dedup). Already-resolved locked
    # videos are refreshed through the TV path, which writes BOTH the video
    # and audio URLs together — the standard resolver only writes the video
    # URL and would leave a stale audio URL paired with it.
    expired_ids = {v.id for v in expired_videos}
    all_priority = list(pending_videos) + list(expired_videos)
    resolved_count = 0
    for video in all_priority:
        try:
            if video.id in expired_ids:
                result = await resolver.resolve_video_for_tv(video.id)
            else:
                result = await resolver.resolve_video(video.id, force=True)
            if result is not None:
                resolved_count += 1
        except Exception as e:
            logger.warning(f"Locked priority resolve failed for video {video.id}: {e}")
        import asyncio as _asyncio
        await _asyncio.sleep(0.5)

    logger.info(
        f"Scheduled resolve (locked priority): {resolved_count}/{len(all_priority)} resolved "
        f"({len(pending_videos)} pending + {len(expired_videos)} expired)"
    )
    return {"locked_pending": len(pending_videos), "locked_expired": len(expired_videos), "resolved": resolved_count}


async def scheduled_resolve():
    """
    Periodic resolve job. Runs every SCRAPE_INTERVAL_MINUTES alongside scrape.

    Three-pass strategy:
    0. Locked priority pass — pre-warm cache for locked (adult) channel content
       FIRST so TiviMate playback always hits a warm cache.
    1. Pending pass  — resolve new pending videos via the standard resolver
       (up to SCHEDULED_PENDING_LIMIT). Handles metadata backfill, permanent
       failure marking, and dedup for brand-new videos.
    2. TV warm pass  — resolve_video_for_tv() for YouTube videos whose
       TV URL pair is missing (resolved_audio_url NULL) or stale, so
       TiviMate playback always hits a warm cache (the YouTube 502 fix).
       Replaces the old standard-path expired refresh, which left stale
       audio URLs paired with fresh video URLs.
    3. DASH purge    — auto-purge any DASH-only videos that slipped through.
    """
    logger.info(
        f"Scheduled batch resolve starting "
        f"(locked_pending={SCHEDULED_LOCKED_PENDING_LIMIT}, "
        f"locked_expired={SCHEDULED_LOCKED_EXPIRED_LIMIT}, "
        f"pending limit={SCHEDULED_PENDING_LIMIT}, tv warm limit={SCHEDULED_TV_WARM_LIMIT})..."
    )
    async with async_session_factory() as db:
        try:
            resolver = ResolverService(db)

            # Pass 0: locked channel priority pre-warm
            locked_summary = await _resolve_locked_channels_priority(resolver, db)
            logger.info(f"Scheduled resolve (locked priority pass): {locked_summary}")

            # Pass 1: pending videos (general)
            summary = await resolver.resolve_batch(limit=SCHEDULED_PENDING_LIMIT)
            logger.info(f"Scheduled resolve (pending pass): {summary}")

            # Pass 2: TV cache warm — pre-resolve YouTube via the TV path so
            # resolved_stream_url + resolved_audio_url are ready before play
            warm_summary = await resolver.warm_tv_cache(limit=SCHEDULED_TV_WARM_LIMIT)
            logger.info(f"Scheduled resolve (TV warm pass): {warm_summary}")

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


async def scheduled_epg_rebuild():
    """
    Periodic EPG schedule rebuild. Runs every EPG_REBUILD_INTERVAL_HOURS (2h).

    Ported from the orphaned app/scheduler.py (Session 53) — it was never
    merged into this live scheduler, so EPG schedules only rebuilt on manual
    trigger. Rebuilds all enabled EPG channels; gap-fill logic means only
    channels with schedule windows shorter than 48h actually write new rows,
    so a fully covered rebuild is nearly free.
    """
    logger.info("Scheduled EPG rebuild starting...")
    try:
        from app.routers.epg import rebuild_all_epg_schedules
        await rebuild_all_epg_schedules()
        logger.info("Scheduled EPG rebuild complete.")
    except Exception as e:
        logger.error(f"Scheduled EPG rebuild failed: {e}")


def start_scheduler():
    """
    Start the background scheduler with configured intervals.
    Call this during FastAPI startup.
    """
    interval_minutes = settings.scrape_interval_minutes

    # Scrape job
    # NOTE: no next_run_time override. The old code passed next_run_time=None
    # intending "don't run immediately on startup" — but in APScheduler that
    # actually adds the job PAUSED, so it never ran at all. With no override,
    # an IntervalTrigger fires first at startup + interval, which is the
    # behavior the original comment wanted.
    scheduler.add_job(
        scheduled_scrape,
        trigger=IntervalTrigger(minutes=interval_minutes),
        id="scrape_job",
        name="Channel Scrape (All)",
        replace_existing=True,
    )

    # Resolve job — runs on the same interval
    scheduler.add_job(
        scheduled_resolve,
        trigger=IntervalTrigger(minutes=interval_minutes),
        id="resolve_job",
        name="Batch Resolve",
        replace_existing=True,
    )

    # Dedup sweep — runs every DEDUP_INTERVAL_HOURS
    scheduler.add_job(
        scheduled_dedup,
        trigger=IntervalTrigger(hours=DEDUP_INTERVAL_HOURS),
        id="dedup_job",
        name="CDN Duplicate Sweep",
        replace_existing=True,
    )

    # Quality upgrade job
    scheduler.add_job(
        scheduled_quality_upgrade,
        trigger=IntervalTrigger(hours=QUALITY_UPGRADE_INTERVAL_HOURS),
        id="quality_upgrade_job",
        name="Quality Upgrade (Low-Res Re-resolve)",
        replace_existing=True,
    )

    # Live TV health probe — every 15 minutes
    scheduler.add_job(
        scheduled_live_tv_probe,
        trigger=IntervalTrigger(minutes=LIVE_TV_PROBE_INTERVAL_MINUTES),
        id="live_tv_probe_job",
        name="Live TV Health Probe",
        replace_existing=True,
    )

    # EPG schedule rebuild — every EPG_REBUILD_INTERVAL_HOURS.
    # Keeps the 48h rolling schedules topped up for TiviMate's guide.
    scheduler.add_job(
        scheduled_epg_rebuild,
        trigger=IntervalTrigger(hours=EPG_REBUILD_INTERVAL_HOURS),
        id="epg_rebuild_job",
        name="EPG Schedule Rebuild",
        replace_existing=True,
    )

    scheduler.start()
    logger.info(
        f"Background scheduler started. "
        f"Scrape/resolve interval: {interval_minutes} minutes. "
        f"Pending resolve limit: {SCHEDULED_PENDING_LIMIT}/tick. "
        f"TV cache warm limit: {SCHEDULED_TV_WARM_LIMIT}/tick. "
        f"Dedup sweep interval: {DEDUP_INTERVAL_HOURS} hours. "
        f"Quality upgrade interval: {QUALITY_UPGRADE_INTERVAL_HOURS} hours "
        f"(chunk={QUALITY_UPGRADE_CHUNK_SIZE}, min={QUALITY_UPGRADE_MIN_HEIGHT}p). "
        f"Live TV probe interval: {LIVE_TV_PROBE_INTERVAL_MINUTES} minutes. "
        f"EPG rebuild interval: {EPG_REBUILD_INTERVAL_HOURS} hours. "
        f"All jobs fire automatically, first run one interval after startup."
    )


def stop_scheduler():
    """Stop the background scheduler. Call during FastAPI shutdown."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Background scheduler stopped.")
