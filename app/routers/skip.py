"""
Skip List API Router.

Endpoints:
- POST /skip            — Add a video to the skip list (never show again).
- GET  /skip/count      — Return how many entries are on the skip list.

When a user flags a video on the TV, the client sends the video ID here.
The backend encrypts the source_post_id and stores the HMAC hash for
fast future lookups. The video is then filtered out of all future feeds.
"""

import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.models import Video, SkipListEntry
from app.encryption import encrypt_value
from app.hashing import hmac_hash

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/skip", tags=["skip"])


class SkipRequest(BaseModel):
    video_id: int


@router.post("")
async def skip_video(
    request: SkipRequest,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Add a video to the skip list.

    The video's source_post_id is encrypted before storage and an
    HMAC hash is stored alongside for fast lookups. The video record
    itself is also deleted from the videos table so it doesn't clutter
    the feed.
    """
    # Look up the video
    stmt = select(Video).where(Video.id == request.video_id)
    result = await db.execute(stmt)
    video = result.scalar_one_or_none()

    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")

    # Check if already on the skip list
    post_hash = hmac_hash(video.source_post_id)
    existing = await db.execute(
        select(SkipListEntry).where(
            SkipListEntry.source_post_id_hash == post_hash
        )
    )
    if existing.scalar_one_or_none() is not None:
        # Already skipped — just remove the video record if still present
        await db.delete(video)
        await db.commit()
        return {"status": "already_skipped", "video_id": request.video_id}

    # Encrypt the source_post_id and create the skip list entry
    encrypted_id = encrypt_value(video.source_post_id)

    skip_entry = SkipListEntry(
        source_post_id_encrypted=encrypted_id,
        source_post_id_hash=post_hash,
        source_provider=video.source_provider,
    )
    db.add(skip_entry)

    # Remove the video from the videos table
    await db.delete(video)
    await db.commit()

    logger.info(f"Skipped video {request.video_id} ({video.title[:40]})")

    return {
        "status": "skipped",
        "video_id": request.video_id,
        "title": video.title,
    }


@router.get("/count")
async def skip_list_count(
    db: AsyncSession = Depends(get_db_session),
):
    """Return the total number of entries on the skip list."""
    result = await db.execute(select(func.count(SkipListEntry.id)))
    count = result.scalar()
    return {"skip_list_count": count}


@router.post("/clear")
async def clear_skip_list(
    db: AsyncSession = Depends(get_db_session),
):
    """
    Clear all entries from the skip list.

    Use this when you want previously blocked videos to reappear on the
    next channel scrape. Does not restore any deleted video records or
    files — it only removes the block so future scrapes can rediscover them.
    """
    result = await db.execute(select(SkipListEntry))
    entries = result.scalars().all()
    count = len(entries)

    for entry in entries:
        await db.delete(entry)

    await db.commit()
    logger.info(f"Cleared {count} entries from skip list")
    return {
        "status": "cleared",
        "cleared_count": count,
        "message": f"Removed {count} entries from the blocklist. Videos will reappear on next scrape.",
    }
