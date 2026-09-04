"""
Resolution API Router.

Endpoints:
- GET  /resolve/{video_id}               — Resolve a single video, return its stream URL.
- GET  /resolve/{video_id}/manifest.mpd  — Generate a DASH MPD manifest for TV playback
                                           (merges split video+audio into one ExoPlayer URI).
- GET  /resolve/{video_id}/playlist.m3u8 — Legacy M3U8 playlist (kept but not used).
- POST /resolve/batch               — Resolve a batch of pending videos (delegates to
                                      ResolverService.resolve_batch — the same guarded
                                      implementation the scheduler uses: YouTube excluded
                                      while the background switch is off, back-off and
                                      cookie-stale pauses respected, circuit breaker active).
- POST /resolve/stop                — Signal the running batch to stop after current video.
- GET  /resolve/youtube-pause       — Return current YouTube back-off / pause state.
- POST /resolve/youtube-pause       — Manually pause all YouTube yt-dlp activity (default 70 min).
- DELETE /resolve/youtube-pause     — Cancel an active YouTube pause immediately.
- GET  /resolve/cookie-stale        — Return current cookie-stale pause state (auto-set on bot-check).
- DELETE /resolve/cookie-stale      — Manually clear the cookie-stale pause after refreshing cookies.
- GET  /resolve/youtube-background  — Return the YouTube background-resolve switch state.
- POST /resolve/youtube-background  — Enable/disable background YouTube resolving (default OFF;
                                      YouTube resolves on demand at play time).
- POST /resolve/backfill-thumbnails — Fetch missing thumbnails via yt-dlp metadata pass.
- POST /resolve/purge-dash          — Delete all DASH-only videos (unplayable in browser).
- POST /resolve/purge-duplicates    — Delete duplicate CDN files, keeping highest-scored copy.
- POST /resolve/purge-vimeo-403     — Delete + skip-list pending Vimeo videos whose last
                                      resolve error was HTTP 403 (dead/private videos that
                                      camp at the head of the batch queue and trip the
                                      circuit breaker — Session 66). ?dry_run=true counts only.
- POST /resolve/vimeo-404-verify    — Start the background live-verification of pending
                                      Vimeo videos with stored 404 errors (Session 68).
- GET  /resolve/vimeo-404-status    — Poll the verification job (counts only).
- POST /resolve/vimeo-404-purge     — Delete + skip-list the confirmed-dead set from the
                                      last completed verification (two-button flow).

Removed endpoints (Session 63): POST /resolve/reset-failed and POST /resolve/purge-dead.
Both operated on resolution_status == "failed", which no code has written since the
Session 59 poison-write fix — permanent failures auto-delete at resolve time and
transient failures stay pending. The buttons were permanent no-ops against legacy
rows only; any lingering legacy "failed" rows are surfaced by the web UI's Problem
Videos view with per-video delete/skip controls.

Channel filtering:
  /resolve/batch accepts an optional ?channel_ids= param (comma-separated
  channel IDs). When provided, only videos belonging to those channels are
  resolved.

Stop mechanism:
  POST /resolve/stop sets a server-side flag. The running batch checks this
  flag between each video and exits early if set. The flag resets automatically
  when a new batch starts or when the current batch finishes.

TV Audio Architecture (DASH MPD):
  The Android TV app cannot merge split video+audio streams via MergingMediaSource
  (a known Media3 limitation for progressive HTTP sources). Instead, the backend
  generates a lightweight DASH MPD manifest on-the-fly that describes both the
  video and audio URLs. ExoPlayer's native DASH engine handles synchronised
  playback of these two tracks — exactly how YouTube itself serves content.

  Flow:
    1. Android calls GET /resolve/{id}/manifest.mpd
    2. Backend calls resolve_video_for_tv() to get split URLs
    3. Both URLs are proxied through /proxy/stream (so YouTube CDN headers work)
    4. Backend returns DASH XML with two AdaptationSets (video + audio)
    5. Android feeds the manifest URL directly to ExoPlayer as a DashMediaSource
    6. ExoPlayer fetches the manifest, discovers both tracks, plays in sync
"""

import logging
import urllib.parse
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.models import Video, SkipListEntry
from app.encryption import encrypt_value
from app.hashing import hmac_hash
from app.services.resolver import (
    ResolverService,
    activate_youtube_pause,
    cancel_youtube_pause,
    get_youtube_pause_state,
    cancel_cookie_stale_pause,
    get_cookie_stale_state,
    set_youtube_background_resolve,
    get_youtube_background_resolve_state,
    YOUTUBE_BACKOFF_MINUTES,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/resolve", tags=["resolve"])

# Server-side abort flag. Set by POST /resolve/stop, cleared at batch start/end.
_batch_abort_requested = False


def _parse_channel_ids(channel_ids: Optional[str]) -> Optional[list[int]]:
    if channel_ids is None:
        return None
    result = []
    for part in channel_ids.split(","):
        part = part.strip()
        if part.isdigit():
            result.append(int(part))
    return result


def _build_dash_manifest(
    video_url: str,
    audio_url: str,
    title: str = "WatchDawg",
    duration_seconds: Optional[float] = None,
) -> str:
    """
    Generate a minimal MPEG-DASH MPD manifest that points ExoPlayer at two
    separate HTTP streams — one video-only and one audio-only.

    ExoPlayer's DashMediaSource reads this manifest and synchronises playback
    of both tracks natively, which is the correct solution for split streams
    returned by yt-dlp (e.g. YouTube 1080p video-only + m4a audio-only).

    Design notes:
    - type="static"  — both URLs are single-file progressive MP4/m4a, not
                        live/segmented. ExoPlayer handles this correctly.
    - mediaPresentationDuration — set from actual yt-dlp duration when available.
                                  Defaults to PT5H (5 hours) which is a safe
                                  upper bound; ExoPlayer stops when streams end.
    - AdaptationSet 0 — video track (H.264 / avc1). mimeType video/mp4.
    - AdaptationSet 1 — audio track (AAC / mp4a.40.2). mimeType audio/mp4.
    - Codecs are declared explicitly so ExoPlayer does not need to probe them.
    - Bandwidth values are approximate placeholders; ExoPlayer ignores them for
      single-representation sets but the field is required by the DASH spec.
    - BaseURL contains the full proxied URL for each track. ExoPlayer fetches
      the entire file from that URL — no segmenting needed.
    """
    if duration_seconds and duration_seconds > 0:
        # Format as ISO 8601 duration: PTxHxMx.xS
        total = int(duration_seconds)
        hours = total // 3600
        minutes = (total % 3600) // 60
        seconds = duration_seconds - (hours * 3600) - (minutes * 60)
        if hours > 0:
            iso_duration = f"PT{hours}H{minutes}M{seconds:.3f}S"
        elif minutes > 0:
            iso_duration = f"PT{minutes}M{seconds:.3f}S"
        else:
            iso_duration = f"PT{seconds:.3f}S"
    else:
        # Safe upper bound — ExoPlayer stops when the stream EOF is reached
        iso_duration = "PT5H"

    # Escape title for XML attribute safety
    safe_title = title.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")

    manifest = f"""<?xml version="1.0" encoding="UTF-8"?>
<MPD
  xmlns="urn:mpeg:DASH:schema:MPD:2011"
  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
  xsi:schemaLocation="urn:mpeg:DASH:schema:MPD:2011 DASH-MPD.xsd"
  type="static"
  mediaPresentationDuration="{iso_duration}"
  minBufferTime="PT4S"
  profiles="urn:mpeg:dash:profile:isoff-on-demand:2011">
  <ProgramInformation>
    <Title>{safe_title}</Title>
  </ProgramInformation>
  <Period id="0" start="PT0S" duration="{iso_duration}">
    <AdaptationSet
      id="0"
      contentType="video"
      mimeType="video/mp4"
      codecs="avc1.640028"
      frameRate="30"
      segmentAlignment="true"
      startWithSAP="1">
      <Representation
        id="video"
        bandwidth="5000000"
        width="1920"
        height="1080">
        <BaseURL>{video_url}</BaseURL>
      </Representation>
    </AdaptationSet>
    <AdaptationSet
      id="1"
      contentType="audio"
      mimeType="audio/mp4"
      codecs="mp4a.40.2"
      lang="en"
      segmentAlignment="true"
      startWithSAP="1">
      <AudioChannelConfiguration
        schemeIdUri="urn:mpeg:dash:23003:3:audio_channel_configuration:2011"
        value="2"/>
      <Representation
        id="audio"
        bandwidth="128000">
        <BaseURL>{audio_url}</BaseURL>
      </Representation>
    </AdaptationSet>
  </Period>
</MPD>"""

    return manifest


@router.post("/batch")
async def resolve_batch(
    limit: int = Query(10, ge=1, le=500, description="Max videos to resolve in this batch"),
    channel_ids: Optional[str] = Query(
        None,
        description="Comma-separated channel IDs to restrict resolution to. "
                    "If omitted, resolves across all channels."
    ),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Resolve a batch of pending videos.

    Delegates to ResolverService.resolve_batch() — the SAME implementation the
    background scheduler runs — so the web UI's Resolve button gets every
    guard for free:
      - YouTube exclusion while the background-resolve switch is off
        (YouTube resolves on demand at play time; this button never burns
        cookies on it)
      - Rate-limit back-off and cookie-stale pause respected
      - Consecutive-transient-failure circuit breaker (a blocked provider
        stops the batch instead of grinding through every slot)
      - Randomized polite inter-request delays

    This endpoint previously carried its own stale copy of the batch logic
    with NONE of those guards, plus an "expired pass" that re-resolved
    already-resolved videos through the standard resolver — which never
    writes resolved_audio_url, leaving mismatched video/audio URL pairs for
    the TV cache to serve. Pending-only now, by design.

    The server-side abort flag (_batch_abort_requested) is checked between
    each video — if POST /resolve/stop was called, the batch exits after the
    current video finishes (within the 90-second yt-dlp timeout at most).

    When channel_ids is provided, only resolves videos from those channels.
    """
    global _batch_abort_requested
    _batch_abort_requested = False  # Always reset at batch start

    parsed_ids = _parse_channel_ids(channel_ids)
    if parsed_ids is not None and not parsed_ids:
        return {
            "status": "complete",
            "summary": {"total": 0, "resolved": 0, "failed": 0, "deleted": 0},
            "message": "No valid channel IDs provided — nothing to resolve.",
        }

    resolver = ResolverService(db)
    summary = await resolver.resolve_batch(
        limit=limit,
        channel_ids=parsed_ids,
        should_abort=lambda: _batch_abort_requested,
    )

    scope = f"channels {parsed_ids}" if parsed_ids else "all channels"
    stopped_note = " [STOPPED BY USER]" if summary.get("stopped") else ""
    logger.info(
        f"Batch resolve ({scope}){stopped_note}: "
        f"{summary['resolved']} resolved, {summary['failed']} failed, "
        f"{summary['deleted']} deleted out of {summary['total']}"
    )

    # Human-readable summary for the web UI toast — the whole point is
    # visibility into what actually happened behind the scenes.
    parts = [f"Resolved {summary['resolved']} of {summary['total']}"]
    if summary.get("deleted"):
        parts.append(f"{summary['deleted']} removed (source permanently gone)")
    if summary.get("failed"):
        parts.append(f"{summary['failed']} temporary failures (will retry)")
    if summary.get("skipped_youtube_bg"):
        parts.append(f"{summary['skipped_youtube_bg']} YouTube left for on-demand resolve")
    if summary.get("skipped_backoff"):
        parts.append(f"{summary['skipped_backoff']} skipped (YouTube back-off active)")
    if summary.get("skipped_cookie_stale"):
        parts.append(f"{summary['skipped_cookie_stale']} skipped (cookies stale)")
    if summary.get("stopped_early"):
        parts.append("stopped early — provider appears blocked")
    if summary.get("stopped"):
        parts.append("stopped by user")

    _batch_abort_requested = False
    return {"status": "complete", "summary": summary, "message": ". ".join(parts) + "."}



@router.get("/youtube-pause")
async def get_youtube_pause():
    """
    Return the current YouTube back-off / pause state.

    Response fields:
      paused: bool
      minutes_remaining: int | null   — minutes left on the cooldown
      until_utc: str | null           — ISO timestamp when it expires
    """
    return get_youtube_pause_state()


@router.post("/youtube-pause")
async def pause_youtube(
    minutes: int = Query(
        YOUTUBE_BACKOFF_MINUTES,
        description="How many minutes to pause YouTube extractions (default 70).",
        ge=1,
        le=480,
    )
):
    """
    Manually pause all YouTube yt-dlp activity for the given number of minutes.

    The scheduler and play-time resolver both check this flag and skip YouTube
    videos while it is active, preventing further rate-limit bans. Clears
    automatically when the timer expires. Can be cancelled early with
    DELETE /resolve/youtube-pause.
    """
    state = activate_youtube_pause(minutes)
    logger.info(f"YouTube pause activated via API: {minutes} min")
    return {"status": "paused", "message": f"YouTube extractions paused for {minutes} minutes.", **state}


@router.delete("/youtube-pause")
async def resume_youtube():
    """
    Cancel an active YouTube pause immediately and resume normal operation.
    Safe to call even if no pause is active.
    """
    state = cancel_youtube_pause()
    logger.info("YouTube pause cancelled via API.")
    return {"status": "resumed", "message": "YouTube extractions resumed.", **state}


@router.get("/cookie-stale")
async def get_cookie_stale():
    """
    Return the current cookie-stale pause state.

    This pause is separate from the timed YouTube back-off above. It is set
    automatically the first time YouTube rejects a request with a bot-check
    ("Sign in to confirm you're not a bot"), which means the exported cookies
    have expired. While active, all YouTube extractions are skipped and the
    affected videos stay PENDING (never marked failed), so the fail log does
    not fill up over an expired cookie.

    Response fields:
      cookie_stale_paused: bool

    Clears automatically the next time a YouTube extraction succeeds (i.e.
    right after cookies.txt is refreshed), or manually via DELETE.
    """
    return get_cookie_stale_state()


@router.delete("/cookie-stale")
async def resume_cookie_stale():
    """
    Manually clear the cookie-stale pause and resume YouTube extractions.

    Use this after refreshing cookies.txt if you don't want to wait for the
    background warm pass to auto-clear it. Safe to call even if no pause is
    active.
    """
    state = cancel_cookie_stale_pause()
    logger.info("Cookie-stale pause cancelled via API.")
    return {"status": "resumed", "message": "Cookie-stale pause cleared — YouTube extractions resumed.", **state}


@router.get("/youtube-background")
async def get_youtube_background():
    """
    Return the YouTube background-resolve switch state.

    When disabled (the default), the scheduled batch resolve skips YouTube
    entirely — YouTube videos resolve on demand at play time only, which is
    what keeps the cookie alive. When enabled, the scheduled pass processes
    YouTube pendings like any other provider.

    In-memory only: a container restart returns the switch to disabled.
    """
    return get_youtube_background_resolve_state()


@router.post("/youtube-background")
async def set_youtube_background(
    enabled: bool = Query(..., description="True to enable background YouTube resolving, False to disable."),
):
    """
    Enable or disable background (scheduled) YouTube resolving.

    Play-time resolution is never affected — pressing play on a YouTube video
    always resolves it live regardless of this switch.
    """
    state = set_youtube_background_resolve(enabled)
    verb = "enabled" if enabled else "disabled"
    return {
        "status": "ok",
        "message": f"Background YouTube resolving {verb}. "
                   + ("The scheduled pass will now include YouTube videos."
                      if enabled else
                      "YouTube resolves on demand at play time only."),
        **state,
    }


@router.post("/stop")
async def stop_resolve_batch():
    """
    Signal the currently running resolve batch to stop after the current video.

    Sets a server-side flag checked between each video in the batch loop.
    The batch will exit cleanly after the current yt-dlp call finishes
    (at most YTDLP_TIMEOUT_SECONDS = 90 seconds from now).

    Has no effect if no batch is currently running.
    """
    global _batch_abort_requested
    _batch_abort_requested = True
    logger.info("Resolve batch stop requested by user.")
    return {"status": "stop_requested", "message": "Batch will stop after the current video finishes."}


@router.post("/backfill-thumbnails")
async def backfill_thumbnails(
    limit: int = Query(50, ge=1, le=500),
    channel_ids: Optional[str] = Query(
        None,
        description="Comma-separated channel IDs to restrict backfill to. If omitted, runs across all channels."
    ),
    db: AsyncSession = Depends(get_db_session),
):
    """Fetch missing thumbnails via yt-dlp metadata pass (no stream resolution), optionally scoped to channels."""
    parsed_ids = _parse_channel_ids(channel_ids)
    resolver = ResolverService(db)
    summary = await resolver.backfill_thumbnails(limit=limit, channel_ids=parsed_ids)
    return {"status": "complete", "summary": summary}


@router.post("/purge-dash")
async def purge_dash_videos(
    channel_ids: Optional[str] = Query(
        None,
        description="Comma-separated channel IDs to restrict purge to. If omitted, purges across all channels."
    ),
    db: AsyncSession = Depends(get_db_session),
):
    """Delete all videos that resolved to a DASH stream (unplayable in browser), optionally scoped to channels."""
    parsed_ids = _parse_channel_ids(channel_ids)
    resolver = ResolverService(db)
    deleted_count = await resolver.purge_dash_videos(channel_ids=parsed_ids)
    scope = f"channels {parsed_ids}" if parsed_ids else "all channels"
    logger.info(f"Purged {deleted_count} DASH-only videos ({scope})")
    return {
        "status": "purged",
        "deleted_count": deleted_count,
        "message": f"Deleted {deleted_count} DASH-only videos from the database ({scope}).",
    }


@router.post("/purge-duplicates")
async def purge_duplicate_cdn_files(db: AsyncSession = Depends(get_db_session)):
    """
    Detect and remove duplicate videos sharing the same physical CDN file.
    Only Vimeo CDN URLs are fingerprinted. Runs automatically every 6 hours.
    """
    resolver = ResolverService(db)
    summary = await resolver.purge_duplicate_cdn_files()
    logger.info(
        f"Manual purge-duplicates: {summary['deleted_count']} deleted across "
        f"{summary['duplicate_groups_found']} CDN fingerprint groups"
    )
    return {
        "status": "complete",
        "duplicate_groups_found": summary["duplicate_groups_found"],
        "deleted_count": summary["deleted_count"],
        "kept_count": summary["kept_count"],
        "no_fingerprint_count": summary["no_fingerprint_count"],
        "message": (
            f"Found {summary['duplicate_groups_found']} duplicate CDN file groups. "
            f"Deleted {summary['deleted_count']} lower-scored duplicates, "
            f"kept {summary['kept_count']} best copies."
        ),
    }


@router.post("/purge-vimeo-403")
async def purge_vimeo_403(
    dry_run: bool = Query(
        False,
        description="When true, only count matching videos — modify nothing.",
    ),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Purge "poison head-of-queue" Vimeo videos. (Session 66)

    Targets videos that are pending AND whose last resolve error contains
    HTTP 403 AND whose source is Vimeo. After an IP block has lifted, a
    Vimeo 403 means the individual video is private, embed-restricted, or
    deleted — it will never resolve, but its transient classification keeps
    it pending forever. Ordered by score, a handful of these camp at the
    front of the batch queue and trip the 5-consecutive-failure circuit
    breaker every tick, starving all healthy videos behind them (the
    Session 65/66 "provider blocked" false alarm).

    Each purged video is handled exactly like the manual 🚫 skip button:
    an encrypted skip-list entry is written (so a future scrape can never
    re-import it), then the video row is deleted through the ORM session
    so the Session 64 cascade cleans favorites / watch history / watchlist.

    IMPORTANT: do NOT run this during an ACTIVE Vimeo IP block — a 403
    then means Vimeo is blocking YOU, not that the videos are dead, and
    purging would destroy healthy videos. The web UI's confirm dialog
    carries this warning; the dry_run counter lets the UI show an exact
    count before anything is destroyed.

    LOCK DISCIPLINE: response contains counts only, never titles — this
    surfaces on the Settings page, which locked sessions can see.
    """
    stmt = select(Video).where(
        Video.resolution_status == "pending",
        Video.resolution_error.isnot(None),
        Video.resolution_error.contains("403"),
        Video.source_url.contains("vimeo"),
    )
    result = await db.execute(stmt)
    targets = result.scalars().all()

    if dry_run:
        return {"status": "dry_run", "count": len(targets)}

    skip_listed = 0
    already_listed = 0
    for video in targets:
        post_hash = hmac_hash(video.source_post_id)
        existing = await db.execute(
            select(SkipListEntry).where(
                SkipListEntry.source_post_id_hash == post_hash
            )
        )
        if existing.scalar_one_or_none() is None:
            db.add(
                SkipListEntry(
                    source_post_id_encrypted=encrypt_value(video.source_post_id),
                    source_post_id_hash=post_hash,
                    source_provider=video.source_provider,
                )
            )
            skip_listed += 1
        else:
            already_listed += 1
        await db.delete(video)

    await db.commit()
    deleted = skip_listed + already_listed
    logger.info(
        f"Purge Vimeo 403s: {deleted} videos removed "
        f"({skip_listed} newly skip-listed, {already_listed} already on skip list)"
    )
    return {
        "status": "purged",
        "deleted_count": deleted,
        "skip_listed": skip_listed,
        "already_listed": already_listed,
        "message": (
            f"Purged {deleted} dead Vimeo videos (403). "
            f"All are skip-listed and cannot be re-imported."
        ),
    }


# ---------------------------------------------------------------------------
# Vimeo-404 verify-then-purge (Session 68).
#
# Two-button flow by design (deletion is an operator decision):
#   1. POST /resolve/vimeo-404-verify  — start the background live-verify job
#   2. GET  /resolve/vimeo-404-status  — poll progress + counts for the UI
#   3. POST /resolve/vimeo-404-purge   — delete + skip-list the confirmed set
# Verification live-re-extracts every pending Vimeo video with a stored 404
# (canary first; polite 5-10s pacing) — never purges on a stored error alone
# (Session 67: Vimeo intermittently 404s videos that are alive). Counts-only
# responses per lock discipline. Declared with the static routes ABOVE the
# /{video_id} catch-all — route order is load-bearing.
# ---------------------------------------------------------------------------
import asyncio as _asyncio  # noqa: E402 — narrow alias for the verify task


@router.post("/vimeo-404-verify")
async def vimeo_404_verify():
    """Start the background Vimeo-404 live-verification job (idempotent)."""
    from app.services.resolver import (
        get_vimeo404_job_status,
        run_vimeo404_verification,
    )

    status = get_vimeo404_job_status()
    if status["state"] == "verifying":
        return {"status": "already_running", **status}
    _asyncio.create_task(run_vimeo404_verification())
    logger.info("Vimeo-404 verification job started from the web UI.")
    return {"status": "started", **get_vimeo404_job_status()}


@router.get("/vimeo-404-status")
async def vimeo_404_status():
    """Poll the verification job. Counts only — no ids or titles (lock discipline)."""
    from app.services.resolver import get_vimeo404_job_status
    return get_vimeo404_job_status()


@router.post("/vimeo-404-purge")
async def vimeo_404_purge():
    """Purge the confirmed-dead set from the last completed verification.

    Refuses unless a verification completed within the freshness window —
    the service enforces the guards; this endpoint just reports the result.
    """
    from app.services.resolver import run_vimeo404_purge
    result = await run_vimeo404_purge()
    if result.get("status") not in ("purged", "nothing"):
        raise HTTPException(status_code=409, detail=result.get("message", "Not ready"))
    return result


@router.post("/upgrade")
async def upgrade_quality(
    channel_ids: Optional[str] = Query(
        None,
        description="Comma-separated channel IDs to restrict upgrade to. If omitted, upgrades across all channels.",
    ),
    chunk_size: int = Query(25, ge=1, le=200, description="Max videos to check per call"),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Re-resolve low-quality videos (below 720p) and replace their stream URL
    if yt-dlp finds a better resolution. Only upgrades — never downgrades.
    Skips on error and continues to the next video.

    Scoped to specific channels when channel_ids is provided, otherwise
    operates across all channels.
    """
    parsed_ids = _parse_channel_ids(channel_ids)
    resolver = ResolverService(db)
    summary = await resolver.upgrade_low_quality(
        min_height=720,
        chunk_size=chunk_size,
        chunk_offset=0,
        channel_ids=parsed_ids,
    )
    scope = f"channels {parsed_ids}" if parsed_ids else "all channels"
    logger.info(
        f"Manual quality upgrade ({scope}): "
        f"{summary['upgraded']} upgraded, {summary['checked']} checked"
    )
    return {
        "status": "complete",
        "scope": scope,
        "checked": summary["checked"],
        "upgraded": summary["upgraded"],
        "same_or_lower": summary["same_or_lower"],
        "errored": summary["errored"],
        "message": (
            f"Checked {summary['checked']} low-quality videos — "
            f"{summary['upgraded']} upgraded, "
            f"{summary['same_or_lower']} already at best available quality."
        ),
    }


# ---------------------------------------------------------------------------
# ROUTE ORDER IS LOAD-BEARING (Session 64).
#
# All STATIC routes (/batch, /youtube-pause, /cookie-stale,
# /youtube-background, /stop, /backfill-thumbnails, /purge-dash,
# /purge-duplicates, /upgrade) MUST be declared BEFORE the dynamic
# /{video_id} routes below. FastAPI matches routes in declaration order:
# with /{video_id} first, GET /resolve/youtube-background was captured by
# /{video_id}, failed int validation with a 422, and the web UI showed
# "YouTube background resolving: unknown". Same silent capture broke
# GET /youtube-pause and GET /cookie-stale. New endpoints with static
# paths must be added ABOVE this comment, never below the dynamic routes.
# ---------------------------------------------------------------------------

@router.get("/{video_id}")
async def resolve_video(
    video_id: int,
    force: bool = Query(False, description="Force re-resolution, bypass cache"),
    client: str = Query(
        "browser",
        description=(
            "Client hint. 'tv' returns split video_url + audio_url for external player. "
            "'browser' (default) returns a single combined stream URL."
        ),
    ),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Resolve a single video and return its direct stream URL.

    TV clients (client=tv):
      Returns both stream_url (video) and audio_url (audio) as separate fields.
      NOTE: Prefer GET /resolve/{id}/manifest.mpd for TV playback — it packages
      the split streams into a DASH manifest that ExoPlayer handles natively
      with full audio+video sync.

    Browser clients (client=browser, default):
      Returns a single combined stream URL via the standard yt-dlp resolver.

    force=true bypasses the URL cache on BOTH paths (previously it was
    accepted but silently ignored on the TV path).

    Failure responses distinguish the two very different outcomes:
      410 Gone        — the source is permanently unavailable (deleted/private);
                        the resolver auto-deleted the video from the catalog.
                        The card should disappear — the video no longer exists.
      503 Unavailable — a transient problem (rate limit, network, block); the
                        video STAYS in the catalog and will be retried later.
                        The recorded error text is included for visibility.
    """
    resolver = ResolverService(db)

    # Pre-check existence so a ghost card (video already auto-deleted on an
    # earlier attempt, page never refreshed) gets an accurate message instead
    # of a confusing generic failure.
    pre = await db.execute(select(Video).where(Video.id == video_id))
    if pre.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=410,
            detail="This video was already removed from the catalog "
                   "(its source is permanently unavailable). Refresh the page to clear the card.",
        )

    if client == "tv":
        result = await resolver.resolve_video_for_tv(video_id, force=force)
    else:
        result = await resolver.resolve_video(video_id, force=force)

    if result is None:
        # Distinguish auto-deleted (permanent) from still-present (transient).
        check = await db.execute(select(Video).where(Video.id == video_id))
        row = check.scalar_one_or_none()
        if row is None:
            raise HTTPException(
                status_code=410,
                detail="This video is permanently unavailable at its source "
                       "(deleted or private) — it has been removed from the catalog.",
            )
        err = (row.resolution_error or "no error recorded")[:200]
        raise HTTPException(
            status_code=503,
            detail=f"Temporary problem resolving this video — it stays in the "
                   f"catalog and will be retried. Last error: {err}",
        )

    return result


@router.get("/{video_id}/manifest.mpd")
async def resolve_video_dash_manifest(
    video_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Generate a DASH MPD manifest for TV playback that combines split video and
    audio streams into a single URI ExoPlayer can play with full audio+video sync.

    This is the correct solution for playing YouTube 1080p+ content (which yt-dlp
    returns as separate video-only and audio-only URLs) on the Android TV app.

    ExoPlayer's DashMediaSource natively handles the manifest and synchronises
    both tracks — no MergingMediaSource, no VLC, no server-side muxing needed.

    Flow:
      1. Calls resolve_video_for_tv() to extract split URLs via yt-dlp
      2. Proxies both URLs through /proxy/stream (required for YouTube CDN auth)
      3. Generates DASH XML with separate AdaptationSets for video and audio
      4. Returns the manifest with Content-Type: application/dash+xml

    If only a combined stream is available (no split), the manifest contains
    only a video AdaptationSet pointing to the combined URL — ExoPlayer plays it
    normally with embedded audio.

    Android TV usage:
      val mediaItem = MediaItem.Builder()
          .setUri("http://backend/resolve/{id}/manifest.mpd")
          .setMimeType(MimeTypes.APPLICATION_MPD)
          .build()
      val source = DashMediaSource.Factory(dataSourceFactory).createMediaSource(mediaItem)
      player.setMediaSource(source)
      player.prepare()
    """
    from fastapi.responses import Response

    resolver = ResolverService(db)
    result = await resolver.resolve_video_for_tv(video_id)

    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"Video {video_id} could not be resolved for TV playback.",
        )

    video_url = result.get("stream_url", "")
    audio_url = result.get("audio_url")
    title = result.get("title") or "WatchDawg"
    duration_seconds = result.get("duration_seconds")

    if not video_url:
        raise HTTPException(
            status_code=502,
            detail=f"Video {video_id} resolved but returned no stream URL.",
        )

    # Proxy both URLs through the backend so YouTube CDN auth headers are injected
    # correctly by /proxy/stream. ExoPlayer cannot send the required headers to
    # googlevideo.com directly — it would get a 403.
    import os; base_url = os.environ.get("WATCHDAWG_BASE_URL", "").rstrip("/") or str(request.base_url).rstrip("/")
    proxied_video = f"{base_url}/proxy/stream?url={urllib.parse.quote(video_url, safe='')}"

    if audio_url:
        proxied_audio = f"{base_url}/proxy/stream?url={urllib.parse.quote(audio_url, safe='')}"
        logger.info(
            f"DASH manifest: video {video_id} | split=yes | "
            f"duration={duration_seconds}s | title='{title[:50]}'"
        )
        manifest = _build_dash_manifest(
            video_url=proxied_video,
            audio_url=proxied_audio,
            title=title,
            duration_seconds=duration_seconds,
        )
    else:
        # Combined stream — wrap in a minimal single-track manifest so the
        # Android app can always use the same DashMediaSource code path.
        logger.info(
            f"DASH manifest: video {video_id} | split=no (combined stream) | "
            f"duration={duration_seconds}s"
        )
        manifest = _build_dash_manifest(
            video_url=proxied_video,
            audio_url=None,
            title=title,
            duration_seconds=duration_seconds,
        )

    return Response(
        content=manifest,
        media_type="application/dash+xml",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Content-Disposition": f'inline; filename="video_{video_id}.mpd"',
        },
    )


@router.get("/{video_id}/seek")
async def resolve_video_seek(
    video_id: int,
    t: int = Query(0, ge=0, description="Start position in seconds"),
    request: Request = None,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Re-resolve a video and return a fresh manifest URL with a start offset.

    Called by the Android TV app when the user scrubs to a new position.
    YouTube CDN ignores HTTP Range headers, so the only reliable way to
    seek is to restart the player from a fresh resolution with the position
    baked in via the YouTube &range= or by returning a new manifest URL
    that ExoPlayer loads from the beginning (which plays from second 0 of
    the stream, but we set startPositionMs so ExoPlayer fast-forwards).

    Returns:
      {
        "manifest_url": "http://backend/resolve/{id}/manifest.mpd",
        "stream_url":   "...",   // fallback for non-DASH
        "audio_url":    "...",   // null for non-split streams
        "start_ms":     90000,   // t param converted to ms, echoed back
        "title":        "...",
        "thumbnail_url": "..."
      }

    The app calls playerManager.restartAtPosition() with the returned
    manifest_url and start_ms — ExoPlayer reloads from the top of the
    stream but seeks immediately to start_ms before playing.
    """
    resolver = ResolverService(db)
    result = await resolver.resolve_video_for_tv(video_id)

    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"Video {video_id} could not be resolved.",
        )

    video_url = result.get("stream_url", "")
    audio_url = result.get("audio_url")

    if not video_url:
        raise HTTPException(
            status_code=502,
            detail=f"Video {video_id} resolved but returned no stream URL.",
        )

    import os; base_url = os.environ.get("WATCHDAWG_BASE_URL", "").rstrip("/") or str(request.base_url).rstrip("/")
    manifest_url = f"{base_url}/resolve/{video_id}/manifest.mpd"
    start_ms = t * 1000

    logger.info(
        f"SEEK RESOLVE | video={video_id} | t={t}s | "
        f"split={'yes' if audio_url else 'no'}"
    )

    return {
        "manifest_url": manifest_url if audio_url else None,
        "stream_url": video_url,
        "audio_url": audio_url,
        "start_ms": start_ms,
        "title": result.get("title") or "",
        "thumbnail_url": result.get("thumbnail_url"),
    }


@router.get("/{video_id}/playlist.m3u8")
async def resolve_video_playlist(
    video_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Legacy M3U8 playlist endpoint (kept for reference, not used by TV app).

    VLC plays M3U8 entries sequentially, not simultaneously — so this approach
    does not solve the split audio+video problem. Use /manifest.mpd instead.

    Returns an M3U8 playlist with both video and audio URLs proxied through
    the backend. URLs are proxied via /proxy/stream so YouTube CDN headers
    are handled correctly.
    """
    from fastapi.responses import PlainTextResponse

    resolver = ResolverService(db)
    result = await resolver.resolve_video_for_tv(video_id)

    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"Video {video_id} could not be resolved.",
        )

    video_url = result.get("stream_url", "")
    audio_url = result.get("audio_url", "")
    title = result.get("title") or "WatchDawg"

    import os; base_url = os.environ.get("WATCHDAWG_BASE_URL", "").rstrip("/") or str(request.base_url).rstrip("/")
    proxied_video = f"{base_url}/proxy/stream?url={urllib.parse.quote(video_url, safe='')}"
    proxied_audio = (
        f"{base_url}/proxy/stream?url={urllib.parse.quote(audio_url, safe='')}"
        if audio_url else None
    )

    if proxied_audio:
        playlist = (
            f"#EXTM3U\n"
            f"#EXTINF:-1,{title}\n"
            f"{proxied_video}\n"
            f"#EXTINF:-1,Audio\n"
            f"{proxied_audio}\n"
        )
    else:
        playlist = (
            f"#EXTM3U\n"
            f"#EXTINF:-1,{title}\n"
            f"{proxied_video}\n"
        )

    return PlainTextResponse(
        content=playlist,
        media_type="audio/x-mpegurl",
        headers={"Cache-Control": "no-cache"},
    )
