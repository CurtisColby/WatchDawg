"""
Feed API Router.

Endpoints:
- GET  /feed              — Return discovered videos, filtered to enabled channels only.
- GET  /feed/ids          — Return only video IDs for Play All / Shuffle All.
- GET  /feed/series       — Return one series card per TV channel (Milestone F).
- GET  /feed/episodes     — Return all episodes for a single TV channel (Milestone F).
- POST /feed/scrape       — Trigger an on-demand scrape of ALL enabled channels.

Videos from disabled channels are hidden from the feed automatically.
Videos with no channel_id (legacy data) are always shown when not filtering.

Videos with resolution_status="downloaded" are Reddit auto-downloads that
live in the Library only — they are permanently excluded from the feed and
the shuffle ID list so they never appear as unresolvable pending items.

Filtering:
- provider:    filter by source provider name (e.g. 'vimeo', 'youtube', 'reddit')
- channel_ids: comma-separated list of channel IDs to include (sidebar filter)
- category:    filter by channel category (e.g. 'movies', 'music', 'adult')
               Omitting returns all categories — fully backward compatible.
- status:      filter by resolution status

PIN lock (Milestone D):
  When no valid token is present, locked channels (channel.locked=True) are
  excluded from the feed and the ID list. This means adult/private content is
  never returned to an unauthenticated client regardless of category param.
  The Android app hides adult category pills when locked as a UX layer, but
  the real enforcement is here at the query level.

Milestone F (TV Series):
  GET /feed/series  — Groups TV-category videos by channel, returns one card per
                      series with episode count, newest thumbnail, and best-effort
                      TMDb metadata. TMDb lookup is fully wrapped — any failure
                      returns null fields without affecting the response.
  GET /feed/episodes — Returns all non-downloaded videos for a single channel,
                       ordered title ASC, created_at ASC (tiebreak). Enforces
                       the same PIN lock as GET /feed.
"""

import datetime
import logging
from typing import Optional, List

from fastapi import APIRouter, Depends, Query, Header, HTTPException
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.models import Video, Channel
from app.services.scraper import ScraperService
from app.routers.channel import get_provider_for_channel
from app.routers.auth import is_unlocked
from app.services.tmdb import TmdbService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/feed", tags=["feed"])


# ---------------------------------------------------------------------------
# Helper — shared video dict serialiser (keeps all three feed endpoints DRY)
# ---------------------------------------------------------------------------

def _video_dict(v: Video) -> dict:
    return {
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


# ---------------------------------------------------------------------------
# GET /feed
# ---------------------------------------------------------------------------

@router.get("")
async def get_feed(
    provider: Optional[str] = Query(None, description="Filter by provider name"),
    channel_id: Optional[int] = Query(None, description="Filter by single channel ID"),
    channel_ids: Optional[str] = Query(None, description="Comma-separated channel IDs to include (sidebar filter)"),
    category: Optional[str] = Query(None, description="Filter by channel category (movies/tv/music/nature/adult/vimeo/general/live_tv)"),
    status: Optional[str] = Query(None, description="Filter by resolution status"),
    limit: int = Query(100, ge=1, le=5000, description="Max results to return"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    x_watchdawg_token: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Return discovered videos from the database.

    Only shows videos from ENABLED channels (or legacy videos with no channel).
    Excludes videos with resolution_status="downloaded" (Library-only).
    Ordered by score descending.

    PIN lock: locked channels are excluded when no valid token is present.
    This is the primary enforcement layer — the Android category pill hiding
    is a UX convenience on top of this backend gate.
    """
    unlocked = is_unlocked(x_watchdawg_token)

    # Build enabled channel query — apply category and lock filters
    enabled_stmt = select(Channel.id).where(Channel.enabled == True)
    if category:
        enabled_stmt = enabled_stmt.where(Channel.category == category)
    if not unlocked:
        # Exclude locked channels when session is not authenticated
        enabled_stmt = enabled_stmt.where(Channel.locked == False)
    enabled_result = await db.execute(enabled_stmt)
    enabled_channel_ids = [row[0] for row in enabled_result.fetchall()]

    # If category filter applied and no channels match, return empty
    if category and not enabled_channel_ids:
        return {
            "total": 0,
            "offset": offset,
            "limit": limit,
            "locked_channels_hidden": not unlocked,
            "videos": [],
        }

    # Parse sidebar channel_ids filter if provided
    selected_ids: Optional[List[int]] = None
    if channel_ids is not None:
        parsed = []
        for part in channel_ids.split(','):
            part = part.strip()
            if part.isdigit():
                parsed.append(int(part))
        selected_ids = parsed

    # Build the base WHERE clause
    if selected_ids is not None:
        if not selected_ids:
            return {
                "total": 0,
                "offset": offset,
                "limit": limit,
                "locked_channels_hidden": not unlocked,
                "videos": [],
            }
        # Intersect selected IDs with enabled (and lock-filtered) channel IDs
        effective_ids = [i for i in selected_ids if i in enabled_channel_ids]
        if not effective_ids:
            return {
                "total": 0,
                "offset": offset,
                "limit": limit,
                "locked_channels_hidden": not unlocked,
                "videos": [],
            }
        base_filter = Video.channel_id.in_(effective_ids)
    else:
        if category:
            # Category filter active — only matched channels, not legacy orphans
            base_filter = Video.channel_id.in_(enabled_channel_ids)
        else:
            # No category filter — include legacy orphan videos (no channel_id)
            # only when unlocked, since we can't verify their lock state
            if unlocked:
                base_filter = or_(
                    Video.channel_id.in_(enabled_channel_ids),
                    Video.channel_id.is_(None),
                )
            else:
                # Locked: only show videos from verified unlocked channels
                base_filter = Video.channel_id.in_(enabled_channel_ids)

    stmt = (
        select(Video)
        .where(base_filter)
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
        "locked_channels_hidden": not unlocked,
        "videos": [_video_dict(v) for v in videos],
    }


# ---------------------------------------------------------------------------
# GET /feed/ids
# ---------------------------------------------------------------------------

@router.get("/ids")
async def get_feed_ids(
    channel_ids: Optional[str] = Query(None, description="Comma-separated channel IDs to include (sidebar filter)"),
    category: Optional[str] = Query(None, description="Filter by channel category"),
    provider: Optional[str] = Query(None, description="Filter by provider name"),
    x_watchdawg_token: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Return only video IDs for the current sidebar filter — used for Play All / Shuffle All.

    Applies the same lock filter as GET /feed so the shuffle queue never
    contains IDs from locked channels when the session is unauthenticated.
    """
    unlocked = is_unlocked(x_watchdawg_token)

    enabled_stmt = select(Channel.id).where(Channel.enabled == True)
    if category:
        enabled_stmt = enabled_stmt.where(Channel.category == category)
    if not unlocked:
        enabled_stmt = enabled_stmt.where(Channel.locked == False)
    enabled_result = await db.execute(enabled_stmt)
    enabled_channel_ids = [row[0] for row in enabled_result.fetchall()]

    if category and not enabled_channel_ids:
        return {"ids": [], "total": 0}

    selected_ids: Optional[List[int]] = None
    if channel_ids is not None:
        parsed = []
        for part in channel_ids.split(','):
            part = part.strip()
            if part.isdigit():
                parsed.append(int(part))
        selected_ids = parsed

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
            if unlocked:
                base_filter = or_(
                    Video.channel_id.in_(enabled_channel_ids),
                    Video.channel_id.is_(None),
                )
            else:
                base_filter = Video.channel_id.in_(enabled_channel_ids)

    stmt = (
        select(Video.id, Video.title)
        .where(base_filter)
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


# ---------------------------------------------------------------------------
# GET /feed/series  (Milestone F)
# ---------------------------------------------------------------------------

@router.get("/series")
async def get_series(
    x_watchdawg_token: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Return one series card per enabled TV-category channel.

    Each card includes:
    - channel_id, channel_name
    - episode_count (non-downloaded videos in the channel)
    - latest_thumbnail (thumbnail of the most-recently scraped episode)
    - tmdb_poster_url, tmdb_description, tmdb_year, tmdb_rating
      (best-effort TMDb lookup by channel name — null on any failure)

    PIN lock: locked TV channels are excluded when the session is not
    authenticated, consistent with all other feed endpoints.

    TMDb is fully wrapped — no TMDb failure can raise an exception or
    alter the response shape. Null fields are always safe for the client.
    """
    unlocked = is_unlocked(x_watchdawg_token)

    # Fetch all enabled TV channels, respecting the PIN lock
    chan_stmt = (
        select(Channel)
        .where(Channel.enabled == True)
        .where(Channel.category == "tv")
        .order_by(Channel.name)
    )
    if not unlocked:
        chan_stmt = chan_stmt.where(Channel.locked == False)

    chan_result = await db.execute(chan_stmt)
    channels = chan_result.scalars().all()

    if not channels:
        return {"series": [], "total": 0, "locked_channels_hidden": not unlocked}

    tmdb = TmdbService()
    series_list = []

    for channel in channels:
        # ── Episode count ────────────────────────────────────────────────────
        count_stmt = (
            select(func.count(Video.id))
            .where(Video.channel_id == channel.id)
            .where(Video.resolution_status != "downloaded")
        )
        episode_count = (await db.execute(count_stmt)).scalar() or 0

        # Skip channels with zero episodes — nothing to show
        if episode_count == 0:
            continue

        # ── Latest thumbnail — most-recently scraped episode ─────────────────
        thumb_stmt = (
            select(Video.thumbnail_url, Video.created_at)
            .where(Video.channel_id == channel.id)
            .where(Video.resolution_status != "downloaded")
            .where(Video.thumbnail_url.isnot(None))
            .order_by(Video.created_at.desc())
            .limit(1)
        )
        thumb_row = (await db.execute(thumb_stmt)).fetchone()
        latest_thumbnail = thumb_row[0] if thumb_row else None

        # ── TMDb series-level lookup (best-effort, fully wrapped) ─────────────
        tmdb_poster_url = None
        tmdb_description = None
        tmdb_year = None
        tmdb_rating = None

        try:
            tmdb_result = await tmdb.lookup(channel.name, media_type="tv")
            if tmdb_result:
                tmdb_poster_url = tmdb_result.get("poster_url")
                tmdb_description = tmdb_result.get("description")
                tmdb_year = tmdb_result.get("year")
                tmdb_rating = tmdb_result.get("rating")
        except Exception as exc:
            # Belt-and-suspenders: TmdbService.lookup() already swallows all
            # exceptions internally, but this outer guard ensures no edge case
            # can bubble up and break the series response.
            logger.warning(
                f"Series TMDb lookup failed for channel '{channel.name}': {exc}"
            )

        series_list.append({
            "channel_id": channel.id,
            "channel_name": channel.name,
            "episode_count": episode_count,
            "latest_thumbnail": latest_thumbnail,
            "tmdb_poster_url": tmdb_poster_url,
            "tmdb_description": tmdb_description,
            "tmdb_year": tmdb_year,
            "tmdb_rating": tmdb_rating,
        })

    return {
        "series": series_list,
        "total": len(series_list),
        "locked_channels_hidden": not unlocked,
    }


# ---------------------------------------------------------------------------
# GET /feed/episodes  (Milestone F)
# ---------------------------------------------------------------------------

@router.get("/episodes")
async def get_episodes(
    channel_id: int = Query(..., description="Channel ID to fetch episodes for"),
    x_watchdawg_token: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Return all non-downloaded episodes for a single TV channel.

    Ordered by title ASC (primary sort — works well for S01E01 naming),
    with created_at ASC as a tiebreak for episodes with identical titles.

    PIN lock: if the channel is locked and the session is not authenticated,
    returns 403. The Android client should never reach this endpoint for a
    locked channel in that state, but the check is here as the authoritative
    server-side gate.
    """
    unlocked = is_unlocked(x_watchdawg_token)

    # Verify the channel exists and is enabled
    chan_stmt = (
        select(Channel)
        .where(Channel.id == channel_id)
        .where(Channel.enabled == True)
    )
    chan_result = await db.execute(chan_stmt)
    channel = chan_result.scalar_one_or_none()

    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found or disabled.")

    # Enforce PIN lock at the server layer
    if channel.locked and not unlocked:
        raise HTTPException(
            status_code=403,
            detail="This channel requires authentication.",
        )

    # Fetch episodes — title ASC, created_at ASC tiebreak
    ep_stmt = (
        select(Video)
        .where(Video.channel_id == channel_id)
        .where(Video.resolution_status != "downloaded")
        .order_by(Video.title.asc(), Video.created_at.asc())
    )
    ep_result = await db.execute(ep_stmt)
    episodes = ep_result.scalars().all()

    return {
        "channel_id": channel_id,
        "channel_name": channel.name,
        "total": len(episodes),
        "episodes": [_video_dict(v) for v in episodes],
    }


# ---------------------------------------------------------------------------
# POST /feed/scrape
# ---------------------------------------------------------------------------

@router.post("/scrape")
async def trigger_scrape(
    limit: int = Query(2000, ge=1, le=5000, description="Max posts to fetch per channel"),
    channel_ids: Optional[str] = Query(None, description="Comma-separated channel IDs to scope the scrape"),
    db: AsyncSession = Depends(get_db_session),
):
    """Trigger an on-demand scrape of all enabled channels (or a scoped subset)."""
    stmt = select(Channel).where(Channel.enabled == True).order_by(Channel.created_at)

    # Scope to specific channel IDs if provided
    if channel_ids:
        parsed = []
        for part in channel_ids.split(','):
            part = part.strip()
            if part.isdigit():
                parsed.append(int(part))
        if parsed:
            stmt = stmt.where(Channel.id.in_(parsed))

    result = await db.execute(stmt)
    channels = result.scalars().all()

    if not channels:
        return {
            "status": "complete",
            "message": "No enabled channels found.",
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
