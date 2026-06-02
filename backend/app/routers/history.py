"""
Watch History API Router.

Endpoints:
- POST   /history/{video_id}  — Upsert watch position for a video.
- GET    /history             — Return continue-watching list.
- DELETE /history/{video_id}  — Remove a single history entry.

Design:
  The Android app calls POST /history/{id} every 10 seconds during HLS playback,
  and once on completion for split-stream playback.

  A video is marked completed=True when position >= 95% of duration.

  GET /history returns videos ordered by last_watched_at descending.
  Locked/adult channel content is UNCONDITIONALLY excluded — the continue
  watching list is always safe to display regardless of PIN state.
  This is enforced at query time, not at write time, so the history record
  still exists in the DB (useful for smart shuffle) but never surfaces in
  the continue-watching UI.
"""

import datetime
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.models import Video, WatchHistory, Channel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/history", tags=["history"])

# A video is considered "completed" when playback reaches this fraction
COMPLETION_THRESHOLD = 0.95


class HistoryUpdateRequest(BaseModel):
    position_seconds: float = Field(..., ge=0, description="Current playback position in seconds")
    duration_seconds: Optional[float] = Field(None, ge=0, description="Total video duration in seconds")


@router.post("/{video_id}")
async def update_history(
    video_id: int,
    request: HistoryUpdateRequest,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Upsert the watch position for a video.

    Called every 10 seconds during HLS playback.
    Called once at end of split-stream playback (position=duration).

    Automatically marks completed=True when position >= 95% of duration.
    Creates the history record if it doesn't exist yet.
    """
    # Verify video exists
    video_stmt = select(Video).where(Video.id == video_id)
    video_result = await db.execute(video_stmt)
    video = video_result.scalar_one_or_none()

    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")

    # Determine completed state
    completed = False
    if request.duration_seconds and request.duration_seconds > 0:
        completed = (request.position_seconds / request.duration_seconds) >= COMPLETION_THRESHOLD

    now = datetime.datetime.utcnow()

    # Upsert — update if exists, create if not
    stmt = select(WatchHistory).where(WatchHistory.video_id == video_id)
    result = await db.execute(stmt)
    history = result.scalar_one_or_none()

    if history is None:
        history = WatchHistory(
            video_id=video_id,
            position_seconds=request.position_seconds,
            duration_seconds=request.duration_seconds,
            completed=completed,
            last_watched_at=now,
        )
        db.add(history)
    else:
        history.position_seconds = request.position_seconds
        if request.duration_seconds is not None:
            history.duration_seconds = request.duration_seconds
        # Once completed, stay completed — don't un-complete if position goes back
        if completed:
            history.completed = True
        history.last_watched_at = now

    await db.commit()

    return {
        "status": "updated",
        "video_id": video_id,
        "position_seconds": request.position_seconds,
        "completed": history.completed,
    }


@router.get("")
async def get_history(
    limit: int = 50,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Return the continue-watching list.

    Ordered by last_watched_at descending (most recently watched first).

    SAFETY RULE: locked/adult channel content is UNCONDITIONALLY excluded.
    This list is shown without PIN — adult content must never appear here.
    Enforcement is at query time so history records still exist in the DB
    for smart shuffle scoring, but are invisible in the continue-watching UI.
    """
    stmt = (
        select(WatchHistory, Video, Channel)
        .join(Video, WatchHistory.video_id == Video.id)
        .outerjoin(Channel, Video.channel_id == Channel.id)
        .order_by(WatchHistory.last_watched_at.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    rows = result.all()

    items = []
    for history, video, channel in rows:
        # Unconditionally exclude locked or adult-category content
        if channel and (channel.locked or channel.category == "adult"):
            continue

        # Calculate progress percentage for UI progress bar
        progress_pct = None
        if history.duration_seconds and history.duration_seconds > 0:
            progress_pct = min(
                100.0,
                (history.position_seconds / history.duration_seconds) * 100.0
            )

        items.append({
            "video_id": video.id,
            "title": video.title,
            "artist": video.artist,
            "thumbnail_url": video.thumbnail_url,
            "source_provider": video.source_provider,
            "channel_id": video.channel_id,
            "channel_name": channel.name if channel else None,
            "duration_seconds": history.duration_seconds,
            "position_seconds": history.position_seconds,
            "progress_pct": progress_pct,
            "completed": history.completed,
            "last_watched_at": history.last_watched_at.isoformat(),
            "tmdb_poster_url": video.tmdb_poster_url,
            "tmdb_year": video.tmdb_year,
        })

    return {"history": items, "total": len(items)}


@router.delete("/{video_id}")
async def delete_history(
    video_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    """Remove a single watch history entry by video_id."""
    stmt = select(WatchHistory).where(WatchHistory.video_id == video_id)
    result = await db.execute(stmt)
    history = result.scalar_one_or_none()

    if history is None:
        raise HTTPException(status_code=404, detail="No history found for this video")

    await db.delete(history)
    await db.commit()

    logger.info(f"Watch history deleted for video_id={video_id}")
    return {"status": "deleted", "video_id": video_id}
