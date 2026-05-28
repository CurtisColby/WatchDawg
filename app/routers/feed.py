"""
Feed API Router.

Endpoints:
- GET  /feed          — Return discovered videos, filtered to enabled channels only.
- POST /feed/scrape   — Trigger an on-demand scrape of ALL enabled channels.

Videos from disabled channels are hidden from the feed automatically.
Videos with no channel_id (legacy data) are always shown.

Videos with resolution_status="downloaded" are Reddit auto-downloads that
live in the Library only — they are permanently excluded from the feed and
the shuffle ID list so they never appear as unresolvable pending items.

Filtering:
- provider:    filter by source provider name (e.g. 'vimeo', 'youtube', 'reddit')
- channel_ids: comma-separated list of channel IDs to include (sidebar filter)
- category:    filter by channel category (e.g. 'movies', 'music', 'adult')
               Omitting returns all categories — fully backward compatible.
- status:      filter by resolution status

Milestone B: added optional category filter param to GET /feed and GET /feed/ids.
Android app continues working unchanged — category param is optional and additive.
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
    category: Optional[str] = Query(None, description="Filter by channel category (movies/tv/music/nature/adult/vimeo/general/live_tv)"),
    status: Optional[str] = Query(None, description="Filter by resolution status"),
    limit: int = Query(100, ge=1, le=1000, description="Max results to return"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Return discovered videos from the database.

    Only shows videos from ENABLED channels (or legacy videos with no channel).
    Excludes videos with resolution_status="downloaded" — those are Reddit
    auto-downloads that live in the Library only and are not feed-playable.
    Ordered by score descending.

    The channel_ids param accepts a comma-separated list of channel IDs
    (e.g. ?channel_ids=1,3,5) and restricts the feed to only those channels.
    Used by the sidebar filter. If channel_ids is provided, legacy videos
    with no channel_id are NOT included — only the explicitly selected channels.
    If channel_ids is empty string or results in no valid IDs, returns empty feed.

    The category param filters by channel category (e.g. ?category=movies).
    Omitting the param returns all categories — fully backward compatible.
    """
    # Build enabled channel query — apply category filter at this level if provided
    enabled_stmt = select(Channel.id).where(Channel.enabled == True)
    if category:
        enabled_stmt = enabled_stmt.where(Channel.category == category)
    enabled_result = await db.execute(enabled_stmt)
    enabled_channel_ids = [row[0] for row in enabled_result.fetchall()]

    # If category filter applied and no channels match, return empty
    if category and not enabled_channel_ids:
        return {"total": 0, "offset": offset, "limit": limit, "videos": []}

    # Parse sidebar channel_ids filter if provided
    selected_ids: Optional[List[int]] = None
    if channel_ids is not None:
        parsed = []
        for part in channel_ids.split(','):
            part = part.strip()
            if part.isdigit():
                parsed.append(int(part))
        selected_ids = parsed  # may be empty list — means show nothing

    # Build the base WHERE clause
    if selected_ids is not None:
        if not selected_ids:
            return {"total": 0, "offset": offset, "limit": limit, "videos": []}
        effective_ids = [i for i in selected_ids if i in enabled_channel_ids]
        if not effective_ids:
            return {"total": 0, "offset": offset, "limit": limit, "videos": []}
        base_filter = Video.channel_id.in_(effective_ids)
    else:
        if category:
            # Category filter active — only show videos from matched channels,
            # not legacy orphan videos (those have no category context)
            base_filter = Video.channel_id.in_(enabled_channel_ids)
        else:
            base_filter = or_(
                Video.channel_id.in_(enabled_channel_ids),
                Video.channel_id.is_(None),
            )

    stmt = (
        select(Video)
        .where(base_filter)
        # Exclude Reddit auto-downloads — they live in Library only, not the feed
        .where(Video.resolution_status != "downloaded")
        .order_by(Video.reddit_score.desc().nullslast())
    )

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
    count_stmt = (
        select(func.count(Video.id))
        .where(base_filter)
        .where(Video.resolution_status != "downloaded")
    )
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
                "tmdb_poster_url": v.tmdb_poster_url,
                "tmdb_year": v.tmdb_year,
                "tmdb_rating": v.tmdb_rating,
                "created_at": v.created_at.isoformat() if v.created_at else None,
            }
            for v in videos
        ],
    }


@router.get("/ids")
async def get_feed_ids(
    channel_ids: Optional[str] = Query(None, description="Comma-separated channel IDs to include (sidebar filter)"),
    category: Optional[str] = Query(None, description="Filter by channel category"),
    provider: Optional[str] = Query(None, description="Filter by provider name"),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Return only video IDs for the current sidebar filter — no metadata, no thumbnails.

    Used by the Shuffle All button to instantly build a complete play queue across
    all videos in the database without loading card thumbnails into the DOM first.

    Respects the same channel_ids and category filters as GET /feed.
    Excludes resolution_status="downloaded" — Library-only Reddit auto-downloads
    are not playable from the feed and must not appear in the shuffle queue.

    Ordered by score descending (same as feed), then shuffled client-side.
    Returns all matching IDs in a single call — no pagination needed since IDs
    are tiny (4-8 bytes each) and 10,000 IDs is still only ~80KB of JSON.
    """
    # Build enabled channel query — apply category filter if provided
    enabled_stmt = select(Channel.id).where(Channel.enabled == True)
    if category:
        enabled_stmt = enabled_stmt.where(Channel.category == category)
    enabled_result = await db.execute(enabled_stmt)
    enabled_channel_ids = [row[0] for row in enabled_result.fetchall()]

    if category and not enabled_channel_ids:
        return {"ids": [], "total": 0}

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
        if category:
            base_filter = Video.channel_id.in_(enabled_channel_ids)
        else:
            base_filter = or_(
                Video.channel_id.in_(enabled_channel_ids),
                Video.channel_id.is_(None),
            )

    stmt = (
        select(Video.id, Video.title)
        .where(base_filter)
        # Exclude Reddit auto-downloads from shuffle queue
        .where(Video.resolution_status != "downloaded")
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
    limit: int = Query(2000, ge=1, le=5000, description="Max posts to fetch per channel"),
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
