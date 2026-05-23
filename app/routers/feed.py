"""
Feed API Router.

Endpoints:
- GET  /feed          — Return discovered videos, filtered to enabled channels only.
- POST /feed/scrape   — Trigger an on-demand scrape of ALL enabled channels.

Videos from disabled channels are hidden from the feed automatically.
Videos with no channel_id (legacy data) are always shown.

Filtering:
- provider: filter by source provider name (e.g. 'vimeo', 'youtube', 'reddit')
- channel_ids: comma-separated list of channel IDs to include (sidebar filter)
- status: filter by resolution status
"""

import datetime
import logging
from typing import Optional, List

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.models import Video, Channel
from app.services.scraper import ScraperService
from app.routers.channel import get_provider_for_channel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/feed", tags=["feed"])


@router.get("")
async def get_feed(
    provider: Optional[str] = Query(None, description="Filter by provider name"),
    channel_id: Optional[int] = Query(None, description="Filter by single channel ID"),
    channel_ids: Optional[str] = Query(None, description="Comma-separated channel IDs to include (sidebar filter)"),
    status: Optional[str] = Query(None, description="Filter by resolution status"),
    limit: int = Query(100, ge=1, le=1000, description="Max results to return"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Return discovered videos from the database.

    Only shows videos from ENABLED channels (or legacy videos with no channel).
    Ordered by score descending.

    The channel_ids param accepts a comma-separated list of channel IDs
    (e.g. ?channel_ids=1,3,5) and restricts the feed to only those channels.
    Used by the sidebar filter. If channel_ids is provided, legacy videos
    with no channel_id are NOT included — only the explicitly selected channels.
    If channel_ids is empty string or results in no valid IDs, returns empty feed.
    """
    # Get IDs of all enabled channels
    enabled_stmt = select(Channel.id).where(Channel.enabled == True)
    enabled_result = await db.execute(enabled_stmt)
    enabled_channel_ids = [row[0] for row in enabled_result.fetchall()]

    # Parse sidebar channel_ids filter if provided
    selected_ids: Optional[List[int]] = None
    if channel_ids is not None:
        # Parse comma-separated IDs, ignore non-integers
        parsed = []
        for part in channel_ids.split(','):
            part = part.strip()
            if part.isdigit():
                parsed.append(int(part))
        selected_ids = parsed  # may be empty list — means show nothing

    # Build the base WHERE clause
    if selected_ids is not None:
        # Sidebar filter active: only show videos from the selected channels.
        # If selected_ids is empty (all unchecked), return no videos.
        if not selected_ids:
            return {"total": 0, "offset": offset, "limit": limit, "videos": []}
        # Intersect selected_ids with enabled_channel_ids so disabled channels
        # can't be forced back in by the sidebar.
        effective_ids = [i for i in selected_ids if i in enabled_channel_ids]
        if not effective_ids:
            return {"total": 0, "offset": offset, "limit": limit, "videos": []}
        base_filter = Video.channel_id.in_(effective_ids)
    else:
        # No sidebar filter: show all enabled channels + legacy (no channel)
        base_filter = or_(
            Video.channel_id.in_(enabled_channel_ids),
            Video.channel_id.is_(None),
        )

    stmt = select(Video).where(base_filter).order_by(Video.reddit_score.desc().nullslast())

    if provider:
        stmt = stmt.where(Video.source_provider == provider)
    if channel_id:
        stmt = stmt.where(Video.channel_id == channel_id)
    if status:
        stmt = stmt.where(Video.resolution_status == status)

    stmt = stmt.offset(offset).limit(limit)
    rows = await db.execute(stmt)
    videos = rows.scalars().all()

    # Total count with same filters
    count_stmt = select(func.count(Video.id)).where(base_filter)
    if provider:
        count_stmt = count_stmt.where(Video.source_provider == provider)
    if channel_id:
        count_stmt = count_stmt.where(Video.channel_id == channel_id)
    if status:
        count_stmt = count_stmt.where(Video.resolution_status == status)
    total = (await db.execute(count_stmt)).scalar()

    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "videos": [
            {
                "id": v.id,
                "source_provider": v.source_provider,
                "source_post_id": v.source_post_id,
                "source_url": v.source_url,
                "channel_id": v.channel_id,
                "title": v.title,
                "artist": v.artist,
                "thumbnail_url": v.thumbnail_url,
                "reddit_score": v.reddit_score,
                "resolution_status": v.resolution_status,
                "resolution_error": v.resolution_error,
                "resolved_stream_url": v.resolved_stream_url,
                "created_at": v.created_at.isoformat() if v.created_at else None,
            }
            for v in videos
        ],
    }




@router.get("/ids")
async def get_feed_ids(
    channel_ids: Optional[str] = Query(None, description="Comma-separated channel IDs to include (sidebar filter)"),
    provider: Optional[str] = Query(None, description="Filter by provider name"),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Return only video IDs for the current sidebar filter — no metadata, no thumbnails.

    Used by the Shuffle All button to instantly build a complete play queue across
    all videos in the database without loading card thumbnails into the DOM first.

    Respects the same channel_ids filter as GET /feed so the sidebar selection is
    honored — if the user has only Vimeo checked, only Vimeo IDs are returned.

    Ordered by score descending (same as feed), then shuffled client-side.
    Returns all matching IDs in a single call — no pagination needed since IDs
    are tiny (4-8 bytes each) and 10,000 IDs is still only ~80KB of JSON.
    """
    # Get IDs of all enabled channels
    enabled_stmt = select(Channel.id).where(Channel.enabled == True)
    enabled_result = await db.execute(enabled_stmt)
    enabled_channel_ids = [row[0] for row in enabled_result.fetchall()]

    # Parse sidebar channel_ids filter
    selected_ids: Optional[List[int]] = None
    if channel_ids is not None:
        parsed = []
        for part in channel_ids.split(','):
            part = part.strip()
            if part.isdigit():
                parsed.append(int(part))
        selected_ids = parsed

    # Build filter (mirrors get_feed logic)
    if selected_ids is not None:
        if not selected_ids:
            return {"ids": [], "total": 0}
        effective_ids = [i for i in selected_ids if i in enabled_channel_ids]
        if not effective_ids:
            return {"ids": [], "total": 0}
        base_filter = Video.channel_id.in_(effective_ids)
    else:
        base_filter = or_(
            Video.channel_id.in_(enabled_channel_ids),
            Video.channel_id.is_(None),
        )

    stmt = (
        select(Video.id, Video.title)
        .where(base_filter)
        .order_by(Video.reddit_score.desc().nullslast())
    )

    if provider:
        stmt = stmt.where(Video.source_provider == provider)

    rows = await db.execute(stmt)
    results = rows.fetchall()

    return {
        "ids": [{"id": r[0], "title": r[1]} for r in results],
        "total": len(results),
    }

@router.post("/scrape")
async def trigger_scrape(
    limit: int = Query(200, ge=1, le=500, description="Max posts to fetch per channel"),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Trigger an on-demand scrape of ALL enabled channels.
    """
    stmt = select(Channel).where(Channel.enabled == True).order_by(Channel.created_at)
    result = await db.execute(stmt)
    channels = result.scalars().all()

    if not channels:
        return {
            "status": "complete",
            "message": "No enabled channels found. Add channels via the Channel manager.",
            "results": [],
        }

    all_results = []
    total_new = 0
    total_discovered = 0

    for channel in channels:
        try:
            provider = get_provider_for_channel(channel)
            scraper = ScraperService(db)
            scrape_result = await scraper.run(
                provider, limit=limit, channel_id=channel.id
            )

            channel.last_scraped_at = datetime.datetime.utcnow()
            channel.last_scrape_count = scrape_result.new

            all_results.append({
                "channel": channel.name,
                "channel_type": channel.channel_type,
                "result": scrape_result.to_dict(),
            })

            total_new += scrape_result.new
            total_discovered += scrape_result.discovered

            logger.info(
                f"Scraped '{channel.name}': {scrape_result.new} new / "
                f"{scrape_result.discovered} discovered"
            )

            if hasattr(provider, "close"):
                await provider.close()

        except Exception as e:
            logger.error(f"Failed to scrape channel '{channel.name}': {e}")
            all_results.append({
                "channel": channel.name,
                "channel_type": channel.channel_type,
                "result": {"error": str(e)},
            })

    await db.commit()

    return {
        "status": "complete",
        "channels_scraped": len(channels),
        "total_new": total_new,
        "total_discovered": total_discovered,
        "results": all_results,
    }
