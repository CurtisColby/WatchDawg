"""
WatchDawg Background Scheduler.

Runs periodic tasks inside the FastAPI process using APScheduler:
1. Scrape job         — fetches new posts from all enabled channels.
2. Resolve job        — resolves pending videos (pending-only by design; URL
                        refreshes happen at play time via the TV path).
3. Dedup job          — sweeps all resolved videos for CDN fingerprint duplicates.
4. Quality upgrade    — re-resolves low-quality videos in chunks.
5. Live TV probe      — probes live stream URLs for online status (every 15 min).
6. Thumbnail pass     — fills missing thumbnails automatically (every 30 min):
                        ffmpeg frame-grabs for local files + yt-dlp metadata
                        backfill for scraped videos. (Session 58)
7. yt-dlp auto-update — upgrades yt-dlp daily; restarts the container on change.
8. EPG rebuild        — rebuilds all enabled EPG pseudo-channel schedules every
                        6 hours. (Session 63) Schedules only cover 48 hours, so
                        without this job Live TV silently goes blank ~2 days
                        after the last manual rebuild. Lock discipline is
                        enforced inside build_channel_schedule itself: adult
                        channels pull ONLY from locked sources, main channels
                        NEVER do — a bulk rebuild cannot cross-contaminate.
9. XMLTV refresh      — re-fetches enabled XMLTV guide sources every 2 hours
                        (the cadence refresh_all_xmltv_sources() always
                        documented but was never actually scheduled for).

Job run registry (Session 63):
  Every job is wrapped by @_track_job, which records last start/end time,
  duration, a short counts-only result summary, last error, and running state
  in an in-memory registry. get_scheduler_status() merges this with
  APScheduler's live job list (including next_run_time) for the Settings-page
  scheduler dashboard, served via GET /health/scheduler. run_job_now() lets
  the dashboard trigger any job immediately.

  LOCK DISCIPLINE: result summaries contain COUNTS ONLY — never video or
  channel titles — because the dashboard renders on the Settings page, which
  is visible to locked (public) web sessions.

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
from app.routers.channel import get_provider_for_channel, _scrape_local_folder_channel
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
# Session 59: lowered 200 → 25. At 200/tick every 30 min, background resolving
# could hit Vimeo up to ~9,600 times/day — a burst pattern that (combined with
# the old 1.0s inter-request delay in resolve_batch) is the likely trigger for
# the July 2026 Vimeo 403 IP-block. 25/tick = ~1,200/day max, a slow steady
# drip that drains the backlog in about a week without looking like a scraper.
# resolve_batch also now paces non-YouTube requests at 5-10s and stops early
# on 5 consecutive transient failures (provider-block circuit breaker).
SCHEDULED_PENDING_LIMIT = 25

# Expired re-resolve batch size per scheduler tick.
SCHEDULED_EXPIRED_LIMIT = 100

# Live TV health probe interval (minutes)
LIVE_TV_PROBE_INTERVAL_MINUTES = 15

# yt-dlp auto-update interval (hours). Checks once at startup then every N hours.
YTDLP_UPDATE_INTERVAL_HOURS = 24

# Thumbnail pass — interval and per-tick batch sizes. (Session 58)
# Local pass: ffmpeg frame-grabs are fast and disk-only, so 50/tick is cheap.
# Backfill pass: yt-dlp metadata calls hit the network, so 25/tick keeps each
# tick well under the 30-min window even with the 60s per-video timeout.
THUMBNAIL_PASS_INTERVAL_MINUTES = 30
THUMBNAIL_LOCAL_LIMIT = 50
THUMBNAIL_BACKFILL_LIMIT = 25

# EPG pseudo-channel schedule rebuild interval (hours). (Session 63)
# Schedules are built 48 hours out; a 6-hour rebuild keeps the window rolling
# with plenty of margin. Matches the cadence rebuild_all_epg_schedules()
# documented from the start.
EPG_REBUILD_INTERVAL_HOURS = 6

# XMLTV guide source refresh interval (hours). (Session 63)
# Matches the cadence refresh_all_xmltv_sources() documented from the start.
XMLTV_REFRESH_INTERVAL_HOURS = 2


# ---------------------------------------------------------------------------
# Job run registry (Session 63).
#
# In-memory record of every job's last run, powering the Settings-page
# scheduler dashboard (GET /health/scheduler). APScheduler knows the FUTURE
# (next_run_time) but forgets the past the moment a run ends — this registry
# is the past: when each job last started and finished, how long it took,
# what it accomplished (counts only — see lock discipline note in the module
# docstring), and whether it errored.
#
# In-memory only: a container restart clears history. That's fine — every
# job fires at startup anyway, so the dashboard repopulates within minutes.
# ---------------------------------------------------------------------------
_job_history: dict = {}


def _track_job(job_id: str):
    """
    Decorator that records run history for a scheduled job.

    Captures start/end timestamps, duration, the job's returned summary
    string (counts only), and any uncaught exception. Jobs keep their own
    internal try/except blocks — this is a belt-and-suspenders outer guard
    so a job can never kill the scheduler loop AND the failure is visible
    on the dashboard instead of only in logs.
    """
    def decorator(fn):
        import functools

        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            rec = _job_history.setdefault(job_id, {
                "run_count": 0, "error_count": 0,
                "last_start": None, "last_end": None,
                "last_duration_seconds": None,
                "last_result": None, "last_error": None,
                "running": False,
            })
            start = datetime.datetime.utcnow()
            rec["running"] = True
            rec["last_start"] = start.isoformat() + "Z"
            rec["run_count"] += 1
            try:
                result = await fn(*args, **kwargs)
                rec["last_result"] = result if isinstance(result, str) else "completed"
                rec["last_error"] = None
            except Exception as e:
                rec["error_count"] += 1
                rec["last_error"] = str(e)[:300]
                rec["last_result"] = "failed"
                logger.error(f"Scheduled job '{job_id}' raised: {e}")
            finally:
                end = datetime.datetime.utcnow()
                rec["running"] = False
                rec["last_end"] = end.isoformat() + "Z"
                rec["last_duration_seconds"] = round((end - start).total_seconds(), 1)
        return wrapper
    return decorator


def get_scheduler_status() -> dict:
    """
    Merge APScheduler's live job list with the run-history registry.

    Returns everything the Settings-page dashboard needs: per job — id,
    display name, next run time, interval, running-now flag, last run
    start/end/duration, a counts-only result summary, last error, and
    lifetime run/error counts.
    """
    jobs = []
    for job in scheduler.get_jobs():
        rec = _job_history.get(job.id, {})
        jobs.append({
            "id": job.id,
            "name": job.name,
            "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
            "interval": str(job.trigger),
            "running": rec.get("running", False),
            "last_start": rec.get("last_start"),
            "last_end": rec.get("last_end"),
            "last_duration_seconds": rec.get("last_duration_seconds"),
            "last_result": rec.get("last_result"),
            "last_error": rec.get("last_error"),
            "run_count": rec.get("run_count", 0),
            "error_count": rec.get("error_count", 0),
        })
    return {
        "scheduler_running": scheduler.running,
        "server_time": datetime.datetime.now(
            scheduler.timezone if scheduler.running else None
        ).isoformat(),
        "jobs": jobs,
    }


def run_job_now(job_id: str) -> bool:
    """
    Trigger a scheduled job to run immediately (the dashboard's Run Now
    button). Returns False if no such job exists. The job's regular interval
    continues unchanged afterward.
    """
    job = scheduler.get_job(job_id)
    if job is None:
        return False
    job.modify(next_run_time=datetime.datetime.now(scheduler.timezone))
    logger.info(f"Job '{job_id}' triggered to run now via dashboard.")
    return True


@_track_job("scrape_job")
async def scheduled_scrape():
    """
    Periodic scrape job. Runs every SCRAPE_INTERVAL_MINUTES.
    Iterates through all enabled channels and scrapes each one.
    Creates its own database session since it runs outside of a request.
    Returns a counts-only summary string for the scheduler dashboard.
    """
    logger.info("Scheduled scrape starting...")
    async with async_session_factory() as db:
        try:
            stmt = select(Channel).where(Channel.enabled == True)
            result = await db.execute(stmt)
            channels = result.scalars().all()

            if not channels:
                logger.info("No enabled channels found — skipping scheduled scrape.")
                return "no enabled channels"

            total_new = 0
            errored = 0
            for channel in channels:
                try:
                    # Session 58: local_folder channels MUST bypass the generic
                    # scraper pipeline. The generic path (a) capped discovery
                    # at limit=50 — the same first 50 files alphabetically every
                    # tick, so files 51+ could never be ingested — and (b)
                    # inserted records as resolution_status='pending', which the
                    # resolve job then fed to yt-dlp as raw disk paths, marking
                    # them 'failed'. The dedicated helper scans recursively at
                    # full limit and inserts records born-resolved.
                    if channel.channel_type == "local_folder":
                        result_dict = await _scrape_local_folder_channel(channel, db)
                        channel.last_scraped_at = datetime.datetime.utcnow()
                        channel.last_scrape_count = result_dict["new"]
                        total_new += result_dict["new"]
                        logger.info(
                            f"Scheduled scrape (local) '{channel.name}': "
                            f"{result_dict['new']} new / {result_dict['discovered']} discovered"
                        )
                        continue

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
                    errored += 1
                    continue

            await db.commit()
            logger.info(
                f"Scheduled scrape complete: {len(channels)} channels, "
                f"{total_new} total new videos"
            )
            summary = f"{len(channels)} channels scraped, {total_new} new videos"
            if errored:
                summary += f", {errored} channels errored"
            return summary

        except Exception as e:
            logger.error(f"Scheduled scrape failed: {e}")
            return f"failed: {str(e)[:120]}"


@_track_job("resolve_job")
async def scheduled_resolve():
    """
    Periodic resolve job. Runs every SCRAPE_INTERVAL_MINUTES alongside scrape.

    Two-pass strategy:
    1. Pending pass  — resolve new pending videos (up to SCHEDULED_PENDING_LIMIT).
    2. DASH purge    — auto-purge any DASH-only videos that slipped through.

    Returns a counts-only summary string for the scheduler dashboard.

    NOTE (Session 56): the old "expired pass" (resolver.resolve_expired) was
    removed. That method no longer exists on ResolverService — it was replaced
    during an earlier refactor, but the call site here was
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

            parts = [f"resolved {summary['resolved']}/{summary['total']}"]
            if summary.get("deleted"):
                parts.append(f"{summary['deleted']} dead removed")
            if summary.get("failed"):
                parts.append(f"{summary['failed']} transient failures")
            if summary.get("skipped_youtube_bg"):
                parts.append(f"{summary['skipped_youtube_bg']} YouTube deferred to on-demand")
            if summary.get("skipped_backoff"):
                parts.append(f"{summary['skipped_backoff']} skipped (back-off)")
            if summary.get("skipped_cookie_stale"):
                parts.append(f"{summary['skipped_cookie_stale']} skipped (cookies stale)")
            if summary.get("stopped_early"):
                parts.append("stopped early (provider blocked)")
            if dash_purged:
                parts.append(f"{dash_purged} DASH purged")
            return ", ".join(parts)

        except Exception as e:
            logger.error(f"Scheduled resolve failed: {e}")
            return f"failed: {str(e)[:120]}"


@_track_job("dedup_job")
async def scheduled_dedup():
    """
    Periodic CDN fingerprint dedup sweep. Runs every DEDUP_INTERVAL_HOURS (2h).

    Two-pass sweep:
    Pass 1 — Source URL dedup across ALL video statuses.
    Pass 2 — CDN fingerprint dedup on resolved videos only.

    Returns a counts-only summary string for the scheduler dashboard.
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
            total_deleted = summary.get("source_url_deleted", 0) + summary.get("cdn_fingerprint_deleted", 0)
            return f"{total_deleted} duplicates removed"
        except Exception as e:
            logger.error(f"Scheduled dedup sweep failed: {e}")
            return f"failed: {str(e)[:120]}"


@_track_job("quality_upgrade_job")
async def scheduled_quality_upgrade():
    """
    Quality upgrade job. Runs every QUALITY_UPGRADE_INTERVAL_HOURS.

    Re-resolves a chunk of low-quality videos (below QUALITY_UPGRADE_MIN_HEIGHT)
    and replaces their stream URL only if yt-dlp finds a better resolution.
    Never deletes videos. Skips on error and moves to the next one.

    Returns a counts-only summary string for the scheduler dashboard.
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
            return (
                f"{summary.get('upgraded', 0)} upgraded, "
                f"{summary.get('same_or_lower', 0)} unchanged "
                f"of {summary.get('checked', 0)} checked"
            )
        except Exception as e:
            logger.error(f"Scheduled quality upgrade failed: {e}")
            return f"failed: {str(e)[:120]}"


@_track_job("live_tv_probe_job")
async def scheduled_live_tv_probe():
    """
    Live TV health probe. Runs every LIVE_TV_PROBE_INTERVAL_MINUTES (15 min).

    Probes all live TV channel stream URLs for online status.
    Uses HEAD requests — fast, minimal data transfer.

    Returns an online/total count summary for the scheduler dashboard
    (probe_all_channels itself logs but returns nothing, so the counts are
    read back from the DB after the probe).
    """
    logger.info("Scheduled live TV health probe starting...")
    try:
        from app.routers.live_tv import probe_all_channels
        await probe_all_channels()

        from app.models import LiveTvChannel
        async with async_session_factory() as db:
            result = await db.execute(select(LiveTvChannel))
            channels = result.scalars().all()
        online = sum(1 for ch in channels if ch.is_online)
        return f"{online}/{len(channels)} channels online"
    except Exception as e:
        logger.error(f"Scheduled live TV probe failed: {e}")
        return f"failed: {str(e)[:120]}"


@_track_job("ytdlp_update_job")
async def scheduled_ytdlp_update():
    """
    yt-dlp auto-update job. Runs once at startup then every YTDLP_UPDATE_INTERVAL_HOURS.

    Runs 'pip install --upgrade yt-dlp yt-dlp-ejs' inside the container.
    If a new version was installed, restarts the container so the update takes effect.
    If already up-to-date, logs and does nothing.

    Returns a version summary string for the scheduler dashboard.

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
            return f"upgraded to {new_version} — container restarting"

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
            return f"already up-to-date ({installed_version})"

    except Exception as e:
        logger.error(f"yt-dlp auto-update check failed: {e}")
        return f"failed: {str(e)[:120]}"


@_track_job("thumbnail_pass_job")
async def scheduled_thumbnail_pass():
    """
    Automatic thumbnail pass. Runs every THUMBNAIL_PASS_INTERVAL_MINUTES. (Session 58)

    Two passes, each with its own DB session so one failing can't roll back
    the other:

    Pass 1 — Local files: reuses the /library/generate-thumbnails logic.
      ffmpeg frame-grab for local_folder videos with no thumbnail, writing the
      /library/thumb/... URL back to the record. Skips files that already have
      a sidecar (links them instead — no regeneration).

    Pass 2 — Scraped videos: ResolverService.backfill_thumbnails().
      yt-dlp metadata-only fetch for non-local videos with no thumbnail.
      Outcome-aware (Session 58): transient failures (rate limits, 403 blocks)
      are deferred for retry on a later tick, never stamped 'unavailable', so
      running through an outage is harmless — the backlog just drains slower.

    Both passes are no-ops (fast empty queries) once everything has a
    thumbnail, so the recurring job costs nothing at steady state.
    """
    logger.info("Scheduled thumbnail pass starting...")

    # Pass 1: local files (ffmpeg frame-grabs)
    local_note = "0"
    async with async_session_factory() as db:
        try:
            from app.routers.library import generate_thumbnails as _generate_local_thumbnails
            result = await _generate_local_thumbnails(limit=THUMBNAIL_LOCAL_LIMIT, db=db)
            logger.info(f"Scheduled thumbnail pass (local): {result.get('summary', result)}")
            _s = result.get("summary", {}) if isinstance(result, dict) else {}
            local_note = str(_s.get("generated", "?"))
        except Exception as e:
            logger.error(f"Scheduled thumbnail pass (local) failed: {e}")
            local_note = "errored"

    # Pass 2: scraped videos (yt-dlp metadata backfill)
    backfill_note = "0"
    async with async_session_factory() as db:
        try:
            resolver = ResolverService(db)
            summary = await resolver.backfill_thumbnails(limit=THUMBNAIL_BACKFILL_LIMIT)
            logger.info(f"Scheduled thumbnail pass (backfill): {summary}")
            backfill_note = str(summary.get("filled", "?"))
        except Exception as e:
            logger.error(f"Scheduled thumbnail pass (backfill) failed: {e}")
            backfill_note = "errored"

    return f"{local_note} local generated, {backfill_note} scraped backfilled"


@_track_job("epg_rebuild_job")
async def scheduled_epg_rebuild():
    """
    EPG pseudo-channel schedule rebuild. Runs every EPG_REBUILD_INTERVAL_HOURS
    (6h) and once at startup. (Session 63)

    Schedules are built 48 hours out by build_channel_schedule; without a
    recurring rebuild they simply run out and Live TV goes blank ~2 days
    after the last manual rebuild or channel edit. This job is the recurring
    rebuild that rebuild_all_epg_schedules() always documented.

    LOCK DISCIPLINE (verified Session 63): safe by construction. Rebuilding
    is a server-side DB write — it serves nothing. Content separation is
    enforced INSIDE build_channel_schedule's source query: adult EPG channels
    pull ONLY from locked (c.locked = 1) sources; main EPG channels NEVER do
    (c.locked = 0). Serving-side separation is enforced independently by the
    Xtream layer, which maps the public profile to epg_type 'main' and the
    private profile to 'adult'. This job cannot cross-contaminate either
    direction, and its dashboard summary is a count only.
    """
    logger.info("Scheduled EPG rebuild starting...")
    try:
        from sqlalchemy import text as _text
        async with async_session_factory() as db:
            result = await db.execute(
                _text("SELECT COUNT(*) FROM epg_channels WHERE enabled = 1")
            )
            channel_count = result.scalar() or 0

        if channel_count == 0:
            logger.info("Scheduled EPG rebuild: no enabled EPG channels.")
            return "no enabled EPG channels"

        from app.routers.epg import rebuild_all_epg_schedules
        await rebuild_all_epg_schedules()
        return f"{channel_count} channel schedules rebuilt (48h window)"
    except Exception as e:
        logger.error(f"Scheduled EPG rebuild failed: {e}")
        return f"failed: {str(e)[:120]}"


@_track_job("xmltv_refresh_job")
async def scheduled_xmltv_refresh():
    """
    XMLTV guide source refresh. Runs every XMLTV_REFRESH_INTERVAL_HOURS (2h)
    and once at startup. (Session 63)

    Re-fetches all enabled external XMLTV guide sources and rebuilds their
    schedules — the cadence refresh_all_xmltv_sources() documented from the
    start but was never actually scheduled for. No-op (one fast count query)
    when no XMLTV sources are configured.
    """
    logger.info("Scheduled XMLTV refresh starting...")
    try:
        from sqlalchemy import text as _text
        async with async_session_factory() as db:
            result = await db.execute(
                _text("SELECT COUNT(*) FROM epg_xmltv_sources WHERE enabled = 1")
            )
            source_count = result.scalar() or 0

        if source_count == 0:
            return "no enabled XMLTV sources"

        from app.routers.epg import refresh_all_xmltv_sources
        await refresh_all_xmltv_sources()
        return f"{source_count} guide sources refreshed"
    except Exception as e:
        logger.error(f"Scheduled XMLTV refresh failed: {e}")
        return f"failed: {str(e)[:120]}"


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

    # Thumbnail pass — every 30 minutes, fires immediately at startup. (Session 58)
    scheduler.add_job(
        scheduled_thumbnail_pass,
        trigger=IntervalTrigger(minutes=THUMBNAIL_PASS_INTERVAL_MINUTES),
        id="thumbnail_pass_job",
        name="Automatic Thumbnail Pass",
        replace_existing=True,
        next_run_time=datetime.datetime.now(),
    )

    # EPG pseudo-channel schedule rebuild — every 6 hours, fires immediately
    # at startup so a restart always heals stale/expired schedules. (Session 63)
    scheduler.add_job(
        scheduled_epg_rebuild,
        trigger=IntervalTrigger(hours=EPG_REBUILD_INTERVAL_HOURS),
        id="epg_rebuild_job",
        name="EPG Schedule Rebuild",
        replace_existing=True,
        next_run_time=datetime.datetime.now(),
    )

    # XMLTV guide source refresh — every 2 hours, fires at startup. (Session 63)
    scheduler.add_job(
        scheduled_xmltv_refresh,
        trigger=IntervalTrigger(hours=XMLTV_REFRESH_INTERVAL_HOURS),
        id="xmltv_refresh_job",
        name="XMLTV Guide Refresh",
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
        f"Thumbnail pass interval: {THUMBNAIL_PASS_INTERVAL_MINUTES} minutes "
        f"(local={THUMBNAIL_LOCAL_LIMIT}, backfill={THUMBNAIL_BACKFILL_LIMIT} per tick). "
        f"EPG rebuild interval: {EPG_REBUILD_INTERVAL_HOURS} hours (runs at startup). "
        f"XMLTV refresh interval: {XMLTV_REFRESH_INTERVAL_HOURS} hours (runs at startup). "
        f"All jobs run immediately at startup then repeat on their intervals. "
        f"Job run history: GET /health/scheduler."
    )


def stop_scheduler():
    """Stop the background scheduler. Call during FastAPI shutdown."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Background scheduler stopped.")
