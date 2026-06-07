"""
Channel Management API Router.

Endpoints:
- GET    /channel                    — List all channels.
- POST   /channel                    — Add a new channel (auto-detects type from URL).
- DELETE /channel/{id}               — Remove a channel.
- PATCH  /channel/{id}               — Toggle enabled/disabled.
- PATCH  /channel/{id}/lock          — Toggle locked/unlocked (PIN gate).
- PATCH  /channel/{id}/category      — Set channel category.
- PATCH  /channel/{id}/genre_tags    — Set genre tags (Milestone R-1).
- POST   /channel/{id}/scrape        — Scrape a single channel on demand.
- DELETE /channel/{id}/videos        — Clear all videos from a channel.

Milestone B: added category field to channel serializer and new
PATCH /channel/{id}/category endpoint.

Milestone R-1: added genre_tags field to ChannelAddRequest and channel
serializer; new PATCH /channel/{id}/genre_tags endpoint.
"""

import datetime
import logging
import re
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, Header
from pydantic import BaseModel, Field
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.models import Channel, Video, Favorite, VALID_CATEGORIES
from app.services.scraper import ScraperService
from app.routers.auth import is_unlocked

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/channel", tags=["channel"])

# ---------------------------------------------------------------------------
# Active download task registry (Session 43)
# ---------------------------------------------------------------------------
# Keyed by channel_id. Value: asyncio.Task so we can cancel it.
# Also stores progress counters updated by _run_mass_download.
# {channel_id: {"task": Task, "total": int, "completed": int, "skipped": int, "failed": int, "dir": str}}
_active_downloads: dict = {}


# --- Request/Response Models ---

class ChannelAddRequest(BaseModel):
    url: str = Field(..., description="URL or identifier for the channel")
    name: Optional[str] = Field(None, description="Display name (auto-generated if omitted)")
    category: Optional[str] = Field("general", description="Content category")
    genre_tags: Optional[str] = Field("", description="Comma-separated genre tags, e.g. 'Nature,Documentary'")


class ChannelToggleRequest(BaseModel):
    enabled: bool


class ChannelLockRequest(BaseModel):
    locked: bool


class ChannelCategoryRequest(BaseModel):
    category: str = Field(..., description=f"One of: {', '.join(VALID_CATEGORIES)}")


class ChannelGenreTagsRequest(BaseModel):
    genre_tags: str = Field(
        ...,
        description=(
            "Comma-separated free-form genre tags. "
            "Example: 'Nature,Documentary'  "
            "Empty string clears all tags."
        )
    )


# --- Auto-Detection Logic ---

def detect_channel_type(url: str) -> dict:
    """
    Auto-detect the channel type from a URL or identifier string.

    Returns a dict with:
      - channel_type: The detected type string
      - url: The normalized URL
      - name: A suggested display name
      - unique_key: A dedup key

    Raises ValueError if the URL format is not recognized.
    """
    url = url.strip()

    # --- Reddit subreddit ---
    reddit_match = re.match(
        r"(?:https?://(?:www\.)?reddit\.com)?/?r/([A-Za-z0-9_]+)", url
    )
    if reddit_match:
        subreddit = reddit_match.group(1)
        return {
            "channel_type": "reddit_subreddit",
            "url": f"https://www.reddit.com/r/{subreddit}",
            "name": f"r/{subreddit}",
            "unique_key": f"reddit_subreddit:{subreddit.lower()}",
        }

    # --- Vimeo channel ---
    vimeo_channel_match = re.match(
        r"https?://(?:www\.)?vimeo\.com/channels/([A-Za-z0-9_-]+)(?:/videos)?/?",
        url,
    )
    if vimeo_channel_match:
        channel_slug = vimeo_channel_match.group(1)
        return {
            "channel_type": "vimeo_channel",
            # Store WITHOUT /videos — yt-dlp and RSS both prefer the clean URL
            "url": f"https://vimeo.com/channels/{channel_slug}",
            "name": f"Vimeo: {channel_slug}",
            "unique_key": f"vimeo_channel:{channel_slug.lower()}",
        }

    # --- Vimeo group ---
    vimeo_group_match = re.match(
        r"https?://(?:www\.)?vimeo\.com/groups/([A-Za-z0-9_-]+)(?:/videos)?/?",
        url,
    )
    if vimeo_group_match:
        group_slug = vimeo_group_match.group(1)
        return {
            "channel_type": "vimeo_channel",
            "url": f"https://vimeo.com/groups/{group_slug}",
            "name": f"Vimeo Group: {group_slug}",
            "unique_key": f"vimeo_group:{group_slug.lower()}",
        }

    # --- Vimeo user ---
    vimeo_user_match = re.match(
        r"https?://(?:www\.)?vimeo\.com/([A-Za-z0-9_-]+)(?:/videos)?/?$",
        url,
    )
    if vimeo_user_match:
        username = vimeo_user_match.group(1)
        if username.lower() not in (
            "channels", "groups", "categories", "ondemand",
            "features", "blog", "about", "help", "watch",
            "settings", "manage", "upgrade", "join", "log_in",
        ):
            return {
                "channel_type": "vimeo_channel",
                "url": f"https://vimeo.com/{username}",
                "name": f"Vimeo: {username}",
                "unique_key": f"vimeo_user:{username.lower()}",
            }

    # --- YouTube playlist ---
    yt_playlist_match = re.match(
        r"https?://(?:www\.)?youtube\.com/playlist\?list=([A-Za-z0-9_-]+)",
        url,
    )
    if yt_playlist_match:
        playlist_id = yt_playlist_match.group(1)
        return {
            "channel_type": "ytdlp_playlist",
            "url": f"https://www.youtube.com/playlist?list={playlist_id}",
            "name": f"YT Playlist: {playlist_id[:20]}",
            "unique_key": f"youtube_playlist:{playlist_id}",
        }

    # --- YouTube channel (@handle or /channel/ID) ---
    yt_channel_match = re.match(
        r"https?://(?:www\.)?youtube\.com/(@[A-Za-z0-9_-]+|channel/[A-Za-z0-9_-]+)(?:/videos)?/?",
        url,
    )
    if yt_channel_match:
        channel_path = yt_channel_match.group(1)
        return {
            "channel_type": "ytdlp_playlist",
            "url": f"https://www.youtube.com/{channel_path}/videos",
            "name": f"YouTube: {channel_path}",
            "unique_key": f"youtube_channel:{channel_path.lower()}",
        }

    # --- Generic URL fallback ---
    parsed = urlparse(url)
    if parsed.scheme in ("http", "https") and parsed.netloc:
        domain = parsed.netloc.replace("www.", "")
        path_slug = parsed.path.strip("/").replace("/", "-")[:40]
        return {
            "channel_type": "ytdlp_playlist",
            "url": url,
            "name": f"{domain}: {path_slug}" if path_slug else domain,
            "unique_key": f"playlist:{domain}:{path_slug}".lower(),
        }

    raise ValueError(
        f"Could not detect channel type from: {url}\n"
        "Supported formats:\n"
        "  - Reddit: https://reddit.com/r/SubName or r/SubName\n"
        "  - Vimeo: https://vimeo.com/channels/name\n"
        "  - YouTube: https://youtube.com/playlist?list=... or https://youtube.com/@channel\n"
        "  - Any other yt-dlp-compatible playlist URL"
    )


# --- Helper to get the right provider for a channel ---

def get_provider_for_channel(channel: Channel):
    """
    Instantiate the correct provider for a channel based on its type.
    """
    if channel.channel_type == "reddit_subreddit":
        from app.providers.reddit import RedditProvider
        match = re.search(r"/r/([A-Za-z0-9_]+)", channel.url)
        subreddit = match.group(1) if match else channel.url
        return RedditProvider(subreddits=[subreddit])

    elif channel.channel_type == "vimeo_channel":
        # Use RSS provider — avoids Cloudflare blocking
        from app.providers.vimeo_rss import VimeoRSSProvider
        return VimeoRSSProvider(
            channel_url=channel.url,
            channel_name=channel.name,
            channel_id=channel.id,
        )

    elif channel.channel_type == "ytdlp_playlist":
        from app.providers.playlist import PlaylistProvider
        return PlaylistProvider(
            playlist_url=channel.url,
            channel_name=channel.name,
            channel_id=channel.id,
        )

    else:
        raise ValueError(f"Unknown channel type: {channel.channel_type}")


# --- Genre tag normaliser ---

def _normalise_genre_tags(raw: str) -> str:
    """
    Normalise a genre tags string:
    - Split on commas
    - Strip whitespace from each tag
    - Drop empty strings
    - Re-join with ', ' separator for consistent storage

    Returns an empty string if no valid tags remain.
    """
    tags = [t.strip() for t in raw.split(",") if t.strip()]
    return ",".join(tags)


# --- Shared channel serializer ---

def _serialize_channel(ch: Channel, video_count: int = 0) -> dict:
    """Serialize a Channel ORM object to a dict for API responses."""
    return {
        "id": ch.id,
        "name": ch.name,
        "channel_type": ch.channel_type,
        "url": ch.url,
        "enabled": ch.enabled,
        "locked": ch.locked,
        "category": ch.category,
        "genre_tags": ch.genre_tags or "",
        "last_scraped_at": ch.last_scraped_at.isoformat() if ch.last_scraped_at else None,
        "last_scrape_count": ch.last_scrape_count,
        "video_count": video_count,
        "created_at": ch.created_at.isoformat() if ch.created_at else None,
    }


# --- API Endpoints ---

@router.get("")
async def list_channels(
    x_watchdawg_token: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db_session),
):
    """
    List all channels with their video counts.

    PIN lock: The channel list itself (names, URLs, lock states) is always
    returned regardless of lock status — the browser UI needs it to render
    the lock toggles. However the FEED, FAVORITES, and LIBRARY endpoints
    gate their content behind the token.
    """
    stmt = select(Channel).order_by(Channel.created_at.desc())
    result = await db.execute(stmt)
    channels = result.scalars().all()

    channel_list = []
    for ch in channels:
        count_result = await db.execute(
            select(func.count(Video.id)).where(Video.channel_id == ch.id)
        )
        video_count = count_result.scalar() or 0
        channel_list.append(_serialize_channel(ch, video_count))

    return {"channels": channel_list}


@router.post("")
async def add_channel(
    request: ChannelAddRequest,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Add a new channel source.

    Auto-detects channel type from the URL.
    Accepts an optional category (defaults to 'general') and optional genre_tags.
    """
    try:
        detected = detect_channel_type(request.url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Validate category
    category = (request.category or "general").lower()
    if category not in VALID_CATEGORIES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid category '{category}'. Must be one of: {', '.join(VALID_CATEGORIES)}"
        )

    existing = await db.execute(
        select(Channel).where(Channel.unique_key == detected["unique_key"])
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Channel already exists: {detected['name']}"
        )

    display_name = request.name if request.name else detected["name"]
    genre_tags = _normalise_genre_tags(request.genre_tags or "")

    channel = Channel(
        name=display_name,
        channel_type=detected["channel_type"],
        url=detected["url"],
        unique_key=detected["unique_key"],
        enabled=True,
        locked=False,
        category=category,
        genre_tags=genre_tags,
    )
    db.add(channel)
    await db.commit()
    await db.refresh(channel)

    logger.info(
        f"Added channel: {channel.name} ({channel.channel_type}) "
        f"category={category} genre_tags='{genre_tags}'"
    )

    return {
        "status": "added",
        "channel": _serialize_channel(channel),
    }


@router.delete("/{channel_id}")
async def delete_channel(
    channel_id: int,
    remove_videos: bool = Query(
        False,
        description="Also remove all videos discovered from this channel",
    ),
    db: AsyncSession = Depends(get_db_session),
):
    """Remove a channel. Optionally removes all its discovered videos too."""
    stmt = select(Channel).where(Channel.id == channel_id)
    result = await db.execute(stmt)
    channel = result.scalar_one_or_none()

    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")

    channel_name = channel.name

    if remove_videos:
        await _clear_channel_videos(db, channel_id)

    await db.delete(channel)
    await db.commit()

    logger.info(f"Deleted channel: {channel_name}")
    return {"status": "deleted", "channel_name": channel_name}


@router.patch("/{channel_id}")
async def toggle_channel(
    channel_id: int,
    request: ChannelToggleRequest,
    db: AsyncSession = Depends(get_db_session),
):
    """Enable or disable a channel."""
    stmt = select(Channel).where(Channel.id == channel_id)
    result = await db.execute(stmt)
    channel = result.scalar_one_or_none()

    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")

    channel.enabled = request.enabled
    await db.commit()

    status = "enabled" if request.enabled else "disabled"
    logger.info(f"Channel {channel.name} {status}")
    return {"status": status, "channel_id": channel_id, "name": channel.name}


@router.patch("/{channel_id}/lock")
async def toggle_channel_lock(
    channel_id: int,
    request: ChannelLockRequest,
    x_watchdawg_token: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Lock or unlock a channel.

    Locking a channel hides its videos from feed, favorites, and library
    until POST /auth/unlock is called and the token is supplied.

    Requires an active session token so an unauthenticated user cannot
    toggle lock states via the API.
    """
    from app.routers.auth import pin_lock_enabled
    if pin_lock_enabled() and not is_unlocked(x_watchdawg_token):
        raise HTTPException(
            status_code=403,
            detail="PIN required to modify channel lock state.",
        )

    stmt = select(Channel).where(Channel.id == channel_id)
    result = await db.execute(stmt)
    channel = result.scalar_one_or_none()

    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")

    channel.locked = request.locked
    await db.commit()

    action = "locked" if request.locked else "unlocked"
    logger.info(f"Channel '{channel.name}' {action}")
    return {
        "status": action,
        "channel_id": channel_id,
        "name": channel.name,
        "locked": channel.locked,
    }


@router.patch("/{channel_id}/category")
async def set_channel_category(
    channel_id: int,
    request: ChannelCategoryRequest,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Set the content category for a channel.

    Valid categories: general, movies, tv, nature, music, adult, live_tv, vimeo.
    Adding new categories in the future requires no migration — just update
    VALID_CATEGORIES in models.py and the web UI dropdown.
    """
    category = request.category.lower()
    if category not in VALID_CATEGORIES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid category '{category}'. Must be one of: {', '.join(VALID_CATEGORIES)}"
        )

    stmt = select(Channel).where(Channel.id == channel_id)
    result = await db.execute(stmt)
    channel = result.scalar_one_or_none()

    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")

    old_category = channel.category
    channel.category = category
    await db.commit()

    logger.info(f"Channel '{channel.name}' category: {old_category} -> {category}")
    return {
        "status": "updated",
        "channel_id": channel_id,
        "name": channel.name,
        "category": channel.category,
    }


@router.patch("/{channel_id}/genre_tags")
async def set_channel_genre_tags(
    channel_id: int,
    request: ChannelGenreTagsRequest,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Set the genre tags for a channel.

    Tags are free-form comma-separated strings — no enum, no validation list.
    Any string is valid. Empty string clears all tags.

    Examples:
      {"genre_tags": "Nature,Documentary"}
      {"genre_tags": "Country,Classic Country"}
      {"genre_tags": ""}   ← clears tags
    """
    stmt = select(Channel).where(Channel.id == channel_id)
    result = await db.execute(stmt)
    channel = result.scalar_one_or_none()

    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")

    old_tags = channel.genre_tags or ""
    new_tags = _normalise_genre_tags(request.genre_tags)
    channel.genre_tags = new_tags
    await db.commit()

    logger.info(f"Channel '{channel.name}' genre_tags: '{old_tags}' -> '{new_tags}'")
    return {
        "status": "updated",
        "channel_id": channel_id,
        "name": channel.name,
        "genre_tags": channel.genre_tags,
    }


@router.delete("/{channel_id}/videos")
async def clear_channel_videos(
    channel_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Delete all videos discovered from this channel.

    Useful when disabling a channel and wanting to clean up its feed entries.
    Favorites linked to these videos are also removed.
    """
    stmt = select(Channel).where(Channel.id == channel_id)
    result = await db.execute(stmt)
    channel = result.scalar_one_or_none()

    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")

    count = await _clear_channel_videos(db, channel_id)
    await db.commit()

    logger.info(f"Cleared {count} videos from channel '{channel.name}'")
    return {
        "status": "cleared",
        "channel_name": channel.name,
        "videos_removed": count,
    }


async def _clear_channel_videos(db: AsyncSession, channel_id: int) -> int:
    """
    Internal helper — delete all videos for a channel, including their favorites.
    Returns the count of videos deleted.
    """
    # Get video IDs for this channel
    video_stmt = select(Video.id).where(Video.channel_id == channel_id)
    video_result = await db.execute(video_stmt)
    video_ids = [row[0] for row in video_result.fetchall()]

    if not video_ids:
        return 0

    # Delete favorites linked to these videos first (FK constraint)
    for vid_id in video_ids:
        fav_stmt = select(Favorite).where(Favorite.video_id == vid_id)
        fav_result = await db.execute(fav_stmt)
        fav = fav_result.scalar_one_or_none()
        if fav:
            await db.delete(fav)

    # Delete the videos
    for vid_id in video_ids:
        vid_stmt = select(Video).where(Video.id == vid_id)
        vid_result = await db.execute(vid_stmt)
        video = vid_result.scalar_one_or_none()
        if video:
            await db.delete(video)

    return len(video_ids)


@router.post("/{channel_id}/scrape")
async def scrape_channel(
    channel_id: int,
    # Raised ceiling to 5000 to support large Vimeo channels (some have 2000+ videos).
    # Default stays at 500 for normal scrapes; pass a higher value to deep-scrape.
    limit: int = Query(500, ge=1, le=5000, description="Max videos to fetch"),
    db: AsyncSession = Depends(get_db_session),
):
    """Scrape a single channel on demand."""
    stmt = select(Channel).where(Channel.id == channel_id)
    result = await db.execute(stmt)
    channel = result.scalar_one_or_none()

    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")

    if not channel.enabled:
        raise HTTPException(status_code=400, detail="Channel is disabled")

    try:
        provider = get_provider_for_channel(channel)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    scraper = ScraperService(db)

    try:
        scrape_result = await scraper.run(
            provider, limit=limit, channel_id=channel.id
        )
    finally:
        if hasattr(provider, "close"):
            await provider.close()

    channel.last_scraped_at = datetime.datetime.utcnow()
    channel.last_scrape_count = scrape_result.new
    await db.commit()

    logger.info(f"Scraped channel '{channel.name}': {scrape_result.new} new videos")

    return {
        "status": "complete",
        "channel": channel.name,
        "channel_type": channel.channel_type,
        "result": scrape_result.to_dict(),
    }



@router.get("/{channel_id}/download-status")
async def get_download_status(
    channel_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Session 43 — Return download status and local file count/size for a channel.

    Returns:
      - is_downloading: bool — whether a mass download task is currently running
      - file_count: int — number of video files on disk in the channel folder
      - total_size_mb: float — total size of downloaded files in MB
      - completed: int — files downloaded in the current run (if active)
      - total: int — total videos queued in the current run (if active)
      - download_dir: str — path to the channel's download folder
    """
    import os

    # Get channel info to determine folder path
    stmt = select(Channel).where(Channel.id == channel_id)
    result = await db.execute(stmt)
    channel = result.scalar_one_or_none()
    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")

    safe_name = re.sub(r'[^\w\-]', '_', channel.name)[:50]
    folder_type = "Private" if channel.locked else "Public"
    download_dir = f"/watchdawg/{folder_type}/{channel_id}_{safe_name}"

    # Count files and total size on disk
    video_exts = {".mp4", ".mkv", ".webm", ".m4v", ".avi", ".mov"}
    file_count = 0
    total_bytes = 0
    if os.path.isdir(download_dir):
        for fname in os.listdir(download_dir):
            _, ext = os.path.splitext(fname)
            if ext.lower() in video_exts:
                try:
                    fsize = os.path.getsize(os.path.join(download_dir, fname))
                    if fsize > 100_000:  # skip tiny/partial files
                        file_count += 1
                        total_bytes += fsize
                except OSError:
                    pass

    # Active download progress
    active = _active_downloads.get(channel_id)
    is_downloading = bool(active and not active["task"].done())

    return {
        "channel_id":    channel_id,
        "channel_name":  channel.name,
        "is_downloading": is_downloading,
        "file_count":    file_count,
        "total_size_mb": round(total_bytes / 1_048_576, 1),
        "download_dir":  download_dir,
        "completed":     active["completed"] if active else 0,
        "skipped":       active["skipped"]   if active else 0,
        "failed":        active["failed"]    if active else 0,
        "total":         active["total"]     if active else 0,
    }


@router.post("/{channel_id}/mass-download/stop")
async def stop_mass_download(channel_id: int):
    """
    Session 43 — Cancel an in-progress mass download for a channel.
    Cancels the asyncio task. Any file currently being downloaded by yt-dlp
    may leave a partial file — the next download run will retry it.
    """
    active = _active_downloads.get(channel_id)
    if not active or active["task"].done():
        return {"status": "not_running", "channel_id": channel_id}

    active["task"].cancel()
    _active_downloads.pop(channel_id, None)
    logger.info(f"Mass download stopped by user: channel {channel_id}")
    return {"status": "stopped", "channel_id": channel_id}


@router.post("/{channel_id}/mass-download")
async def mass_download_channel(
    channel_id: int,
    quality: str = Query(
        "720",
        description="Max download quality: '720', '1080', or 'best'",
        regex="^(720|1080|best)$",
    ),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Session 42 — Mass download all videos from a channel to local storage.

    Downloads every video in the channel to:
      /watchdawg/Public/{channel_id}_{channel_name_safe}/   — unlocked channels
      /watchdawg/Private/{channel_id}_{channel_name_safe}/  — locked channels

    Files are named {video_id}.mp4 so the resolver can find them by video_id
    without any DB lookup — just a predictable path check.

    Uses yt-dlp with quality cap. Skips videos already downloaded.
    Runs as a background task — returns immediately with a job status.
    Downloads are queued; a separate status endpoint is not provided —
    check the logs or the download folder directly.

    The resolver automatically prefers local files over yt-dlp resolution,
    so downloaded videos play instantly from any pill, screen, or EPG channel
    that includes this source.
    """
    import asyncio

    stmt = select(Channel).where(Channel.id == channel_id)
    result = await db.execute(stmt)
    channel = result.scalar_one_or_none()

    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")

    # Pull all video IDs + source URLs for this channel
    vid_stmt = select(Video.id, Video.source_url, Video.title).where(
        Video.channel_id == channel_id,
        Video.resolution_status != "failed",
        Video.source_url.isnot(None),
    )
    vid_result = await db.execute(vid_stmt)
    videos = vid_result.fetchall()

    if not videos:
        return {
            "status": "nothing_to_download",
            "channel": channel.name,
            "message": "No videos found for this channel.",
        }

    # Build safe folder name: {channel_id}_{sanitized_name}
    safe_name = re.sub(r'[^\w\-]', '_', channel.name)[:50]
    folder_type = "Private" if channel.locked else "Public"
    download_dir = f"/watchdawg/{folder_type}/{channel_id}_{safe_name}"

    # Quality format string
    if quality == "720":
        fmt = "best[height<=720][ext=mp4]/best[height<=720]/best[ext=mp4]/best"
    elif quality == "1080":
        fmt = "best[height<=1080][ext=mp4]/best[height<=1080]/best[ext=mp4]/best"
    else:
        fmt = "best[ext=mp4]/best"

    total = len(videos)
    logger.info(
        f"Mass download: channel {channel_id} '{channel.name}' — "
        f"{total} videos → {download_dir} (quality={quality})"
    )

    # Fire background task — register in active downloads so status endpoint can track it
    task = asyncio.ensure_future(
        _run_mass_download(videos, download_dir, fmt, channel_id, channel.name)
    )
    _active_downloads[channel_id] = {
        "task":      task,
        "total":     total,
        "completed": 0,
        "skipped":   0,
        "failed":    0,
        "dir":       download_dir,
    }

    return {
        "status": "started",
        "channel": channel.name,
        "channel_id": channel_id,
        "download_dir": download_dir,
        "total_videos": total,
        "quality": quality,
        "message": (
            f"Downloading {total} videos to {download_dir} in the background. "
            f"Files named {{video_id}}.mp4. Check logs for progress."
        ),
    }


async def _run_mass_download(
    videos: list,
    download_dir: str,
    fmt: str,
    channel_id: int,
    channel_name: str,
) -> None:
    """
    Background mass downloader. Downloads each video sequentially using yt-dlp.
    Skips files already present and >1MB. Logs progress per video.
    Updates _active_downloads counters so the status endpoint can report live progress.
    Cleans up _active_downloads entry on completion or cancellation.
    """
    import asyncio
    import os

    os.makedirs(download_dir, exist_ok=True)

    cookies_path = "/config/cookies.txt"
    completed = 0
    skipped = 0
    failed = 0
    total = len(videos)

    def _update_progress():
        if channel_id in _active_downloads:
            _active_downloads[channel_id]["completed"] = completed
            _active_downloads[channel_id]["skipped"]   = skipped
            _active_downloads[channel_id]["failed"]    = failed

    try:
        for video_id, source_url, title in videos:
            out_path = os.path.join(download_dir, f"{video_id}.mp4")

            # Skip already downloaded
            if os.path.exists(out_path) and os.path.getsize(out_path) > 1_000_000:
                skipped += 1
                _update_progress()
                continue

            logger.info(
                f"Mass download [{channel_name}] {completed + skipped + failed + 1}/{total}: "
                f"video {video_id} '{(title or '')[:50]}'"
            )

            cmd = [
                "yt-dlp",
                "-f", fmt,
                "--no-playlist",
                "--no-warnings",
                "--quiet",
                "-o", out_path,
            ]
            if cookies_path and os.path.isfile(cookies_path):
                cmd += ["--cookies", cookies_path]
            cmd.append(source_url)

            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    _, stderr = await asyncio.wait_for(proc.communicate(), timeout=1800)
                    if proc.returncode == 0 and os.path.exists(out_path):
                        size_mb = os.path.getsize(out_path) // 1_000_000
                        logger.info(
                            f"Mass download [{channel_name}]: video {video_id} "
                            f"complete — {size_mb}MB"
                        )
                        completed += 1
                        _update_progress()
                    else:
                        err = stderr.decode(errors="ignore")[:200] if stderr else "unknown"
                        logger.warning(
                            f"Mass download [{channel_name}]: video {video_id} "
                            f"failed (rc={proc.returncode}) — {err}"
                        )
                        failed += 1
                        _update_progress()
                        if os.path.exists(out_path):
                            os.remove(out_path)
                except asyncio.TimeoutError:
                    logger.warning(
                        f"Mass download [{channel_name}]: video {video_id} timed out"
                    )
                    proc.kill()
                    failed += 1
                    if os.path.exists(out_path):
                        os.remove(out_path)
            except Exception as e:
                logger.warning(
                    f"Mass download [{channel_name}]: video {video_id} error — {e}"
                )
                failed += 1

            # Brief pause between downloads to avoid hammering the source
            await asyncio.sleep(0.5)

        logger.info(
            f"Mass download [{channel_name}] complete: "
            f"{completed} downloaded, {skipped} already existed, {failed} failed "
            f"out of {total} total"
        )
    except asyncio.CancelledError:
        logger.info(f"Mass download [{channel_name}] cancelled by user after {completed} completed.")
    finally:
        # Always clean up the active downloads registry
        _active_downloads.pop(channel_id, None)
