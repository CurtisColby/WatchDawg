"""
Feed API Router.

Endpoints:
- GET  /feed              — Return discovered videos, filtered to enabled channels only.
- GET  /feed/ids          — Return only video IDs for Play All / Shuffle All.
- GET  /feed/series       — Return one series card per TV channel (Milestone F).
- GET  /feed/episodes     — Return all episodes for a single TV channel (Milestone F).
- GET  /feed/genres       — Return distinct genre tags for a category (Milestone R-1).
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
- genre_tag:   filter by a single genre tag (e.g. 'Nature', 'Documentary')
               Applies a LIKE '%tag%' match on channel.genre_tags.
               Omitting returns all genre tags — fully backward compatible.
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

Milestone R-1 (Genre Tags):
  GET /feed/genres  — Returns the sorted list of distinct genre tags that exist
                      for all enabled channels in a given category. The Android
                      app calls this once per section load to build the genre
                      pill bar dynamically — no app update needed to add tags.

Session 33 (Smart Shuffle):
  GET /feed/ids gains optional order_by=least_watched param.
  When set, IDs are ordered by watch_history.last_watched_at ASC NULLS FIRST
  so never-watched and least-recently-watched content surfaces first.
  This is the cross-session weighting layer of the hybrid Smart Shuffle model.
  The in-memory played-bit Set in the Android ViewModels handles within-session
  uniqueness on top of this ordering.
  Existing behavior is fully preserved when order_by is absent or any other value.
"""

import datetime
import logging
from typing import Optional, List

from fastapi import APIRouter, Depends, Query, Header, HTTPException
from sqlalchemy import select, func, or_, text
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
# Helper — build enabled channel ID list with optional category + genre filter
# ---------------------------------------------------------------------------

async def _enabled_channel_ids(
    db: AsyncSession,
    *,
    unlocked: bool,
    category: Optional[str] = None,
    genre_tag: Optional[str] = None,
) -> List[int]:
    """
    Return IDs of enabled channels, optionally filtered by category and/or
    genre_tag. PIN lock is enforced when unlocked=False.
    """
    stmt = select(Channel.id).where(Channel.enabled == True)
    if category:
        stmt = stmt.where(Channel.category == category)
    if genre_tag:
        # Simple contains match — genre_tags stored as "Nature,Documentary"
        stmt = stmt.where(Channel.genre_tags.contains(genre_tag))
    if not unlocked:
        stmt = stmt.where(Channel.locked == False)
    result = await db.execute(stmt)
    return [row[0] for row in result.fetchall()]


# ---------------------------------------------------------------------------
# GET /feed
# ---------------------------------------------------------------------------

@router.get("")
async def get_feed(
    provider: Optional[str] = Query(None, description="Filter by provider name"),
    channel_id: Optional[int] = Query(None, description="Filter by single channel ID"),
    channel_ids: Optional[str] = Query(None, description="Comma-separated channel IDs to include (sidebar filter)"),
    category: Optional[str] = Query(None, description="Filter by channel category (movies/tv/music/nature/adult/vimeo/general/live_tv)"),
    genre_tag: Optional[str] = Query(None, description="Filter by genre tag (e.g. 'Nature', 'Documentary'). Omit to return all genres."),
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

    genre_tag (R-1): when supplied, only videos from channels whose genre_tags
    field contains the tag string are returned. Omitting returns all genres —
    fully backward compatible with the existing Android APK.
    """
    unlocked = is_unlocked(x_watchdawg_token)

    # Build enabled channel IDs — apply category, genre, and lock filters
    enabled_ids = await _enabled_channel_ids(
        db, unlocked=unlocked, category=category, genre_tag=genre_tag
    )

    # If category or genre filter applied and no channels match, return empty
    if (category or genre_tag) and not enabled_ids:
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
        # Intersect selected IDs with enabled (and lock/genre-filtered) channel IDs
        effective_ids = [i for i in selected_ids if i in enabled_ids]
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
        if category or genre_tag:
            # Category or genre filter active — only matched channels, not legacy orphans
            base_filter = Video.channel_id.in_(enabled_ids)
        else:
            # No category/genre filter — include legacy orphan videos (no channel_id)
            # only when unlocked, since we can't verify their lock state
            if unlocked:
                base_filter = or_(
                    Video.channel_id.in_(enabled_ids),
                    Video.channel_id.is_(None),
                )
            else:
                # Locked: only show videos from verified unlocked channels
                base_filter = Video.channel_id.in_(enabled_ids)

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
    genre_tag: Optional[str] = Query(None, description="Filter by genre tag"),
    provider: Optional[str] = Query(None, description="Filter by provider name"),
    order_by: Optional[str] = Query(
        None,
        description=(
            "Sort order for returned IDs. "
            "'least_watched' orders by watch_history.last_watched_at ASC NULLS FIRST "
            "so never-watched and stale content surfaces first — used by Smart Shuffle. "
            "Omit (or any other value) to use the default reddit_score DESC ordering."
        ),
    ),
    x_watchdawg_token: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Return only video IDs for the current sidebar filter — used for Play All / Shuffle All.

    Applies the same lock filter as GET /feed so the shuffle queue never
    contains IDs from locked channels when the session is unauthenticated.

    Session 33 — Smart Shuffle cross-session weighting:
      When order_by=least_watched, IDs are returned ordered by the most recent
      watch_history.last_watched_at for that video_id, ASC NULLS FIRST.
      Videos never watched (no history row) sort before recently-watched videos.
      The Android ViewModel applies its in-memory played-bit Set on top of this
      ordering to guarantee within-session no-repeat behaviour.
    """
    unlocked = is_unlocked(x_watchdawg_token)

    enabled_ids = await _enabled_channel_ids(
        db, unlocked=unlocked, category=category, genre_tag=genre_tag
    )

    if (category or genre_tag) and not enabled_ids:
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
        effective_ids = [i for i in selected_ids if i in enabled_ids]
        if not effective_ids:
            return {"ids": [], "total": 0}
        base_filter = Video.channel_id.in_(effective_ids)
    else:
        if category or genre_tag:
            base_filter = Video.channel_id.in_(enabled_ids)
        else:
            if unlocked:
                base_filter = or_(
                    Video.channel_id.in_(enabled_ids),
                    Video.channel_id.is_(None),
                )
            else:
                base_filter = Video.channel_id.in_(enabled_ids)

    # ── Smart Shuffle cross-session ordering ──────────────────────────────────────────────
    # When order_by=least_watched we execute raw SQL directly via the async
    # session to avoid SQLAlchemy ORM join/subquery complications with SQLite.
    # Raw SQL LEFT JOINs watch_history and orders by MAX(last_watched_at)
    # ASC NULLS FIRST — never-watched videos come first, most-recently-watched last.
    # All other order_by values fall back to the ORM path with reddit_score DESC.
    if order_by == "least_watched":
        if not enabled_ids:
            return {"ids": [], "total": 0}
        placeholders = ",".join(str(i) for i in enabled_ids)
        raw_sql = (
            "SELECT v.id, v.title "
            "FROM videos v "
            "LEFT JOIN watch_history wh ON wh.video_id = v.id "
            f"WHERE v.channel_id IN ({placeholders}) "
            "AND v.resolution_status != 'downloaded' "
            "GROUP BY v.id, v.title "
            "ORDER BY MAX(wh.last_watched_at) ASC NULLS FIRST"
        )
        rows = await db.execute(text(raw_sql))
        results = rows.fetchall()
        return {
            "ids": [{"id": r[0], "title": r[1]} for r in results],
            "total": len(results),
        }
    else:
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
    genre_tag: Optional[str] = Query(None, description="Filter by genre tag"),
    x_watchdawg_token: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Return one series card per enabled TV-category channel.

    Each card carries: channel_id, channel_name, genre_tags, episode_count,
    latest_thumbnail, and best-effort TMDb metadata (poster_url, description,
    year, rating). TMDb lookup failures are silently swallowed — a missing
    poster never breaks the response.

    PIN lock: locked channels excluded when unauthenticated.
    genre_tag filter: when supplied, only channels whose genre_tags contains
    the tag are included — mirrors the /feed genre_tag behaviour.
    """
    unlocked = is_unlocked(x_watchdawg_token)
    tmdb = TmdbService()

    # Fetch enabled TV channels, optionally filtered by genre_tag
    stmt = (
        select(Channel)
        .where(Channel.enabled == True)
        .where(Channel.category == "tv")
    )
    if genre_tag:
        stmt = stmt.where(Channel.genre_tags.contains(genre_tag))
    if not unlocked:
        stmt = stmt.where(Channel.locked == False)

    result = await db.execute(stmt)
    channels = result.scalars().all()

    series_list = []

    for channel in channels:
        # ── Episode count — excludes downloaded/library-only videos ──────────
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
            "genre_tags": channel.genre_tags or "",
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
        "genre_tags": channel.genre_tags or "",
        "total": len(episodes),
        "episodes": [_video_dict(v) for v in episodes],
    }


# ---------------------------------------------------------------------------
# GET /feed/genres  (Milestone R-1)
# ---------------------------------------------------------------------------

@router.get("/genres")
async def get_genres(
    category: str = Query(..., description="Channel category to return tags for (e.g. 'tv', 'movies', 'music', 'adult')"),
    x_watchdawg_token: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Return the sorted list of distinct genre tags for all enabled channels
    in the given category.

    The Android app calls this once per section load (TV, Movies, Music, etc.)
    to build the genre pill bar dynamically. Adding a new tag to any channel
    in the web UI immediately makes that tag available as a pill on the next
    app load — no APK update required.

    PIN lock: locked channels are excluded when the session is not authenticated,
    so adult genre tags never leak to unauthenticated clients.

    Response:
        {
            "category": "tv",
            "tags": ["Comedy", "Documentary", "Drama", "Nature"]
        }

    An empty tags list means no channels in this category have genre tags set.
    The Android app should hide the genre pill bar (or show only "All") in that case.
    """
    unlocked = is_unlocked(x_watchdawg_token)

    stmt = (
        select(Channel.genre_tags)
        .where(Channel.enabled == True)
        .where(Channel.category == category)
    )
    if not unlocked:
        stmt = stmt.where(Channel.locked == False)

    result = await db.execute(stmt)
    rows = result.fetchall()

    # Collect all tags across all channels in this category
    all_tags: set[str] = set()
    for row in rows:
        raw = row[0]
        if raw:
            for tag in raw.split(","):
                tag = tag.strip()
                if tag:
                    all_tags.add(tag)

    return {
        "category": category,
        "tags": sorted(all_tags),
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
