"""
Resolution API Router.

Endpoints:
- GET  /resolve/{video_id}          — Resolve a single video, return its stream URL.
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
"""

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException
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


@router.get("/{video_id}")
async def resolve_video(
    video_id: int,
    force: bool = Query(False, description="Force re-resolution, bypass cache"),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Resolve a single video and return its direct stream URL.

    Cache check → yt-dlp (hard 90s timeout) → dedup → result.
    If this video is a lower-scored CDN duplicate, playback is transparently
    redirected to the keeper so the user never sees an error.
    """
    resolver = ResolverService(db)
    result = await resolver.resolve_video(video_id, force=force)

    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"Video {video_id} could not be resolved. It may be unavailable, "
            "private, geo-blocked, or was identified as a lower-scored duplicate.",
        )

    return result


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
        # Global batch via service method — still interleaves abort check
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
        # Check abort flag before each video
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

    _batch_abort_requested = False  # Reset flag after batch completes
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
    return {"status": "purged", "deleted_count": deleted_count,
            "message": f"Deleted {deleted_count} DASH-only videos from the database ({scope})."}


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
    return {"status": "purged", "deleted_count": deleted_count,
            "message": f"Deleted {deleted_count} failed videos from the database ({scope})."}


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
