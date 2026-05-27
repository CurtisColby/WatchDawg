"""
Resolution API Router.

Endpoints:
- GET  /resolve/{video_id}               — Resolve a single video, return its stream URL.
- GET  /resolve/{video_id}/manifest.mpd  — Generate a DASH MPD manifest for TV playback
                                           (merges split video+audio into one ExoPlayer URI).
- GET  /resolve/{video_id}/playlist.m3u8 — Legacy M3U8 playlist (kept but not used).
- POST /resolve/batch               — Resolve a batch of pending videos.
- POST /resolve/stop                — Signal the running batch to stop after current video.
- POST /resolve/reset-failed        — Reset failed videos back to pending for retry.
- POST /resolve/backfill-thumbnails — Fetch missing thumbnails via yt-dlp metadata pass.
- POST /resolve/purge-dash          — Delete all DASH-only videos (unplayable in browser).
- POST /resolve/purge-dead          — Delete all failed videos from the database entirely.
- POST /resolve/purge-duplicates    — Delete duplicate CDN files, keeping highest-scored copy.

Channel filtering:
  Both /resolve/batch and /resolve/reset-failed accept an optional
  ?channel_ids= param (comma-separated channel IDs). When provided, only
  videos belonging to those channels are affected.

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

import asyncio
import logging
import urllib.parse
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.models import Video
from app.services.resolver import ResolverService

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
    """
    resolver = ResolverService(db)

    if client == "tv":
        result = await resolver.resolve_video_for_tv(video_id)
        if result is None:
            raise HTTPException(
                status_code=404,
                detail=f"Video {video_id} could not be resolved for TV playback.",
            )
        return result

    result = await resolver.resolve_video(video_id, force=force)

    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"Video {video_id} could not be resolved. It may be unavailable, "
            "private, geo-blocked, or was identified as a lower-scored duplicate.",
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
    base_url = str(request.base_url).rstrip("/")
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

    base_url = str(request.base_url).rstrip("/")
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

    base_url = str(request.base_url).rstrip("/")
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
    Resolve a batch of pending or expired videos.

    Checks the server-side abort flag (_batch_abort_requested) between each
    video — if POST /resolve/stop was called, the batch exits after the current
    video finishes (within the 90-second yt-dlp timeout at most).

    When channel_ids is provided, only resolves videos from those channels.
    """
    global _batch_abort_requested
    _batch_abort_requested = False  # Always reset at batch start

    parsed_ids = _parse_channel_ids(channel_ids)
    resolver = ResolverService(db)

    RESOLUTION_TTL_HOURS = 3

    # Build video list — channel-scoped or global
    if parsed_ids is not None:
        if not parsed_ids:
            return {"status": "complete", "summary": {"total": 0, "resolved": 0, "failed": 0, "deleted": 0}}

        import datetime
        pending_stmt = (
            select(Video)
            .where(
                Video.resolution_status == "pending",
                Video.channel_id.in_(parsed_ids),
            )
            .order_by(Video.reddit_score.desc().nullslast())
            .limit(limit)
        )
        pending_result = await db.execute(pending_stmt)
        pending_videos = pending_result.scalars().all()

        expiry_cutoff = datetime.datetime.utcnow() - datetime.timedelta(hours=RESOLUTION_TTL_HOURS)
        expired_stmt = (
            select(Video)
            .where(
                Video.resolution_status == "resolved",
                Video.resolved_at < expiry_cutoff,
                Video.channel_id.in_(parsed_ids),
            )
            .order_by(Video.reddit_score.desc().nullslast())
            .limit(max(0, limit - len(pending_videos)))
        )
        expired_result = await db.execute(expired_stmt)
        expired_videos = expired_result.scalars().all()
        all_videos = list(pending_videos) + list(expired_videos)

    else:
        import datetime
        pending_stmt = (
            select(Video)
            .where(Video.resolution_status == "pending")
            .order_by(Video.reddit_score.desc().nullslast())
            .limit(limit)
        )
        pending_result = await db.execute(pending_stmt)
        pending_videos = pending_result.scalars().all()

        expiry_cutoff = datetime.datetime.utcnow() - datetime.timedelta(hours=RESOLUTION_TTL_HOURS)
        expired_stmt = (
            select(Video)
            .where(
                Video.resolution_status == "resolved",
                Video.resolved_at < expiry_cutoff,
            )
            .order_by(Video.reddit_score.desc().nullslast())
            .limit(max(0, limit - len(pending_videos)))
        )
        expired_result = await db.execute(expired_stmt)
        expired_videos = expired_result.scalars().all()
        all_videos = list(pending_videos) + list(expired_videos)

    summary = {"total": len(all_videos), "resolved": 0, "failed": 0, "deleted": 0, "stopped": False}

    for video in all_videos:
        if _batch_abort_requested:
            logger.info("Batch resolve: stop requested — exiting early.")
            summary["stopped"] = True
            break

        video_id = video.id
        result = await resolver.resolve_video(video_id, force=True)
        if result is not None:
            summary["resolved"] += 1
        else:
            check = await db.execute(select(Video).where(Video.id == video_id))
            if check.scalar_one_or_none() is None:
                summary["deleted"] += 1
            else:
                summary["failed"] += 1
        await asyncio.sleep(1.0)

    scope = f"channels {parsed_ids}" if parsed_ids else "all channels"
    stopped_note = " [STOPPED BY USER]" if summary["stopped"] else ""
    logger.info(
        f"Batch resolve ({scope}){stopped_note}: "
        f"{summary['resolved']} resolved, {summary['failed']} failed, "
        f"{summary['deleted']} deleted out of {summary['total']}"
    )

    _batch_abort_requested = False
    return {"status": "complete", "summary": summary}


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


@router.post("/reset-failed")
async def reset_failed_videos(
    channel_ids: Optional[str] = Query(
        None,
        description="Comma-separated channel IDs to restrict reset to. "
                    "If omitted, resets ALL failed videos."
    ),
    db: AsyncSession = Depends(get_db_session),
):
    """Reset failed videos back to pending. Optionally scoped to specific channels."""
    parsed_ids = _parse_channel_ids(channel_ids)

    stmt = select(Video).where(Video.resolution_status == "failed")
    if parsed_ids is not None:
        if not parsed_ids:
            return {"status": "reset", "reset_count": 0, "message": "No channel IDs provided."}
        stmt = stmt.where(Video.channel_id.in_(parsed_ids))

    result = await db.execute(stmt)
    failed_videos = result.scalars().all()

    count = len(failed_videos)
    for video in failed_videos:
        video.resolution_status = "pending"
        video.resolved_stream_url = None
        video.resolved_at = None
        video.resolution_error = None

    await db.commit()

    scope = f"channels {parsed_ids}" if parsed_ids else "all channels"
    logger.info(f"Reset {count} failed videos to pending ({scope})")
    return {
        "status": "reset",
        "reset_count": count,
        "message": f"Reset {count} failed videos to pending ({scope}). Use Resolve All to retry.",
    }


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


@router.post("/purge-dead")
async def purge_dead_videos(
    channel_ids: Optional[str] = Query(
        None,
        description="Comma-separated channel IDs to restrict purge to. If omitted, purges across all channels."
    ),
    db: AsyncSession = Depends(get_db_session),
):
    """Delete failed videos from the database, optionally scoped to channels."""
    parsed_ids = _parse_channel_ids(channel_ids)
    resolver = ResolverService(db)
    deleted_count = await resolver.purge_dead_videos(channel_ids=parsed_ids)
    scope = f"channels {parsed_ids}" if parsed_ids else "all channels"
    logger.info(f"Purged {deleted_count} dead videos ({scope})")
    return {
        "status": "purged",
        "deleted_count": deleted_count,
        "message": f"Deleted {deleted_count} failed videos from the database ({scope}).",
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
