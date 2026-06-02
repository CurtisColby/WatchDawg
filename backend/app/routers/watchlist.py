"""
Watchlist (Watch Later) API Router.

Endpoints:
- POST   /watchlist/{video_id}  — Add a video to the watchlist.
- DELETE /watchlist/{video_id}  — Remove a video from the watchlist.
- GET    /watchlist             — Return all watchlist entries with video metadata.

Rules:
- No PIN required to read or write the watchlist.
- Adult-category channel videos cannot be added (enforced at API level).
  This keeps the watchlist always safe to display without authentication.
- One entry per video (unique constraint). Adding twice is idempotent.
"""

import datetime
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.models import Video, Watchlist, Channel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/watchlist", tags=["watchlist"])


@router.post("/{video_id}")
async def add_to_watchlist(
    video_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Add a video to the Watch Later watchlist.

    Blocked for adult-category channel videos — the watchlist is always
    visible without PIN, so adult content must never appear in it.
    Idempotent — returns 'already_in_watchlist' if already present.
    """
    # Look up the video
    stmt = select(Video).where(Video.id == video_id)
    result = await db.execute(stmt)
    video = result.scalar_one_or_none()

    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")

    # Block adult-category content from ever entering the watchlist
    if video.channel_id:
        ch_stmt = select(Channel).where(Channel.id == video.channel_id)
        ch_result = await db.execute(ch_stmt)
        channel = ch_result.scalar_one_or_none()
        if channel and channel.category == "adult":
            raise HTTPException(
                status_code=403,
                detail="Adult content cannot be added to Watch Later."
            )

    # Check for existing entry (idempotent)
    existing = await db.execute(
        select(Watchlist).where(Watchlist.video_id == video_id)
    )
    if existing.scalar_one_or_none() is not None:
        return {"status": "already_in_watchlist", "video_id": video_id}

    entry = Watchlist(video_id=video_id, added_at=datetime.datetime.utcnow())
    db.add(entry)
    await db.commit()

    logger.info(f"Watchlist: added video_id={video_id} '{video.title}'")
    return {
        "status": "added",
        "video_id": video_id,
        "title": video.title,
    }


@router.delete("/{video_id}")
async def remove_from_watchlist(
    video_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    """Remove a video from the watchlist by video_id."""
    stmt = select(Watchlist).where(Watchlist.video_id == video_id)
    result = await db.execute(stmt)
    entry = result.scalar_one_or_none()

    if entry is None:
        raise HTTPException(status_code=404, detail="Video not in watchlist")

    await db.delete(entry)
    await db.commit()

    logger.info(f"Watchlist: removed video_id={video_id}")
    return {"status": "removed", "video_id": video_id}


@router.get("")
async def get_watchlist(
    db: AsyncSession = Depends(get_db_session),
):
    """
    Return all watchlist entries with full video metadata.

    Ordered by added_at descending (most recently added first).
    Adult-category videos are excluded unconditionally even if somehow
    present — belt-and-suspenders safety layer on top of the add-time block.
    """
    stmt = (
        select(Watchlist, Video, Channel)
        .join(Video, Watchlist.video_id == Video.id)
        .outerjoin(Channel, Video.channel_id == Channel.id)
        .order_by(Watchlist.added_at.desc())
    )
    result = await db.execute(stmt)
    rows = result.all()

    items = []
    for entry, video, channel in rows:
        # Safety: skip adult content even if it somehow got in
        if channel and channel.category == "adult":
            continue

        items.append({
            "id": entry.id,
            "video_id": video.id,
            "title": video.title,
            "artist": video.artist,
            "thumbnail_url": video.thumbnail_url,
            "source_provider": video.source_provider,
            "source_url": video.source_url,
            "channel_id": video.channel_id,
            "channel_name": channel.name if channel else None,
            "duration_seconds": video.duration_seconds,
            "resolution_status": video.resolution_status,
            "tmdb_poster_url": video.tmdb_poster_url,
            "tmdb_year": video.tmdb_year,
            "added_at": entry.added_at.isoformat(),
        })

    return {"watchlist": items, "total": len(items)}
