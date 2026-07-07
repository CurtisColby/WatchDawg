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
import random
import re
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, Header, Response
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

# Session 50 — last completed mass-download run per channel.
# Lets the web UI show "Last run: X downloaded, Y skipped, Z failed" plus the
# per-item results after the active entry has been cleaned up.
_last_download_results: dict = {}


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

    # --- Local folder (absolute path inside container or bare subfolder name) ---
    # Accepts:
    #   /watchdawg/Private/FolderName
    #   /watchdawg/Public/FolderName
    #   Private/FolderName
    #   FolderName  (resolved under /watchdawg/)
    # The path is normalised to an absolute path under /watchdawg/ at add time.
    stripped = url.strip().rstrip("/")
    if stripped.startswith("/watchdawg/") or (
        not stripped.startswith("http") and "/" not in stripped and not stripped.startswith("r/")
    ):
        # Normalise to absolute path under /watchdawg/
        from app.config import settings as _cfg
        if stripped.startswith("/watchdawg/"):
            abs_path = stripped
        else:
            # Bare subfolder name — check Private first, then Public, then root
            private_candidate = f"{_cfg.private_downloads_path}/{stripped}"
            public_candidate = f"{_cfg.public_downloads_path}/{stripped}"
            root_candidate = f"{_cfg.downloads_path}/{stripped}"
            import os as _os
            if _os.path.isdir(private_candidate):
                abs_path = private_candidate
            elif _os.path.isdir(public_candidate):
                abs_path = public_candidate
            elif _os.path.isdir(root_candidate):
                abs_path = root_candidate
            else:
                abs_path = private_candidate  # Default to Private for new paths
        folder_name = abs_path.rstrip("/").split("/")[-1]
        return {
            "channel_type": "local_folder",
            "url": abs_path,
            "name": folder_name,
            "unique_key": f"local_folder:{abs_path.lower()}",
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

    elif channel.channel_type == "local_folder":
        return LocalFolderProvider(
            folder_path=channel.url,
            channel_name=channel.name,
            channel_id=channel.id,
        )

    else:
        raise ValueError(f"Unknown channel type: {channel.channel_type}")


# ---------------------------------------------------------------------------
# Local Folder Provider — Session 45
#
# Walks a mounted local directory and treats every video file as a discovered
# video. Files are inserted with resolution_status="resolved" and
# source_url=<absolute_file_path> so the resolver never tries to run yt-dlp.
# The stream endpoint serves them via /library/stream/.
#
# Supported extensions: .mp4, .mkv, .webm, .m4v, .avi, .mov
# ---------------------------------------------------------------------------

_LOCAL_VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".m4v", ".avi", ".mov"}


class LocalFolderProvider:
    """
    Provider that scans a local directory for video files.

    Each file becomes a Video record with:
    - source_provider = "local_folder"
    - source_post_id  = relative path from /watchdawg/ (dedup key)
    - source_url      = absolute path to the file (used for streaming)
    - resolution_status pre-set to "resolved" (no yt-dlp needed)
    - resolved_stream_url = /library/stream/<relative_path>

    The scraper's _insert_video() sets resolution_status="pending" by default,
    so we bypass the scraper for local files and insert directly in fetch_posts()
    with resolved status.
    """

    provider_name = "local_folder"

    def __init__(self, folder_path: str, channel_name: str, channel_id: int):
        self.folder_path = folder_path.rstrip("/")
        self.channel_name = channel_name
        self.channel_id = channel_id

    async def fetch_posts(self, limit: int = 5000):
        """
        Walk the folder and return one DiscoveredVideo per video file.
        Sorted alphabetically. Capped at limit.
        """
        from app.providers.base import DiscoveredVideo
        import os

        discovered = []
        if not os.path.isdir(self.folder_path):
            logger.warning(
                f"LocalFolderProvider: folder not found: {self.folder_path}"
            )
            return discovered

        try:
            entries = sorted(os.scandir(self.folder_path), key=lambda e: e.name.lower())
        except OSError as e:
            logger.error(f"LocalFolderProvider: cannot scan {self.folder_path}: {e}")
            return discovered

        from app.config import settings as _cfg
        watchdawg_root = _cfg.downloads_path.rstrip("/")

        for entry in entries:
            if not entry.is_file():
                continue
            _, ext = os.path.splitext(entry.name)
            if ext.lower() not in _LOCAL_VIDEO_EXTS:
                continue

            abs_path = entry.path
            # Relative path from /watchdawg/ root — used as stream path and dedup key
            if abs_path.startswith(watchdawg_root + "/"):
                rel_path = abs_path[len(watchdawg_root) + 1:]
            else:
                rel_path = abs_path

            title = os.path.splitext(entry.name)[0]
            # Build the stream URL — served by /library/stream/
            base_url = "http://192.168.50.42:6868"
            import urllib.parse
            stream_url = f"{base_url}/library/stream/{urllib.parse.quote(rel_path, safe='/')}"

            dv = DiscoveredVideo(
                source_provider="local_folder",
                source_post_id=rel_path,       # unique per file
                source_url=abs_path,           # absolute path for direct access
                title=title,
                artist=None,
                thumbnail_url=None,
                duration_seconds=None,
                score=0,
            )
            # Attach extra fields for local files — used by _insert_local_video
            dv._resolved_stream_url = stream_url
            dv._abs_path = abs_path
            discovered.append(dv)

            if len(discovered) >= limit:
                break

        logger.info(
            f"LocalFolderProvider: found {len(discovered)} video files in {self.folder_path}"
        )
        return discovered

    async def close(self):
        pass


# --- Local folder scrape helper ---

async def _scrape_local_folder_channel(
    channel: "Channel",
    db: AsyncSession,
    limit: int = 5000,
) -> dict:
    """
    Scrape a local_folder channel by scanning its directory and upserting
    Video records directly — bypassing the normal scraper pipeline since
    local files are already resolved and need no yt-dlp processing.

    Returns a dict matching ScrapeResult.to_dict() format.
    """
    from app.models import Video
    from sqlalchemy import select as _select

    provider = LocalFolderProvider(
        folder_path=channel.url,
        channel_name=channel.name,
        channel_id=channel.id,
    )
    discovered = await provider.fetch_posts(limit=limit)

    # Get existing source_post_ids for this channel to avoid duplicates
    existing_stmt = _select(Video.source_post_id).where(
        Video.channel_id == channel.id,
        Video.source_provider == "local_folder",
    )
    existing_result = await db.execute(existing_stmt)
    existing_ids = {row[0] for row in existing_result.fetchall()}

    new_count = 0
    dupe_count = 0

    for dv in discovered:
        if dv.source_post_id in existing_ids:
            dupe_count += 1
            continue

        db_video = Video(
            source_provider="local_folder",
            source_post_id=dv.source_post_id,
            source_url=dv.source_url,
            title=dv.title,
            artist=dv.artist,
            thumbnail_url=dv.thumbnail_url,
            duration_seconds=dv.duration_seconds,
            reddit_score=0,
            resolution_status="resolved",
            resolved_stream_url=dv._resolved_stream_url,
            channel_id=channel.id,
        )
        db.add(db_video)
        existing_ids.add(dv.source_post_id)
        new_count += 1

    await db.commit()

    logger.info(
        f"Local folder scan '{channel.name}': {new_count} new, {dupe_count} already known"
    )
    return {
        "discovered": len(discovered),
        "new": new_count,
        "duplicates": dupe_count,
        "skipped": 0,
        "errors": 0,
        "downloaded": 0,
        "download_skipped": 0,
    }


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

    # Local folder channels bypass the normal scraper — files are already resolved
    if channel.channel_type == "local_folder":
        result_dict = await _scrape_local_folder_channel(channel, db)
        channel.last_scraped_at = datetime.datetime.utcnow()
        channel.last_scrape_count = result_dict["new"]
        await db.commit()
        logger.info(
            f"Local folder scan '{channel.name}': {result_dict['new']} new files"
        )
        return {
            "status": "complete",
            "channel": channel.name,
            "channel_type": channel.channel_type,
            "result": result_dict,
        }

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
        # Session 50 — live per-item reporting for the web UI downloader view
        "current_title":    active.get("current_title") if active else None,
        "current_video_id": active.get("current_video_id") if active else None,
        "quality":          active.get("quality") if active else None,
        "recent":           active.get("recent", []) if active else [],
        "last_run":         _last_download_results.get(channel_id),
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
        # Session 50 — live per-item reporting for the web UI downloader view
        "current_title":    None,
        "current_video_id": None,
        "recent":           [],   # last N items: {video_id, title, status, size_mb}
        "quality":          quality,
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

    def _set_current(video_id, title):
        """Session 50 — mark which file the downloader is grabbing right now."""
        if channel_id in _active_downloads:
            _active_downloads[channel_id]["current_video_id"] = video_id
            _active_downloads[channel_id]["current_title"] = title

    def _record_item(video_id, title, status, size_mb=None):
        """Session 50 — append a finished item to the rolling results list."""
        if channel_id in _active_downloads:
            entry = _active_downloads[channel_id]
            entry["recent"].append({
                "video_id": video_id,
                "title": title,
                "status": status,          # "done" | "skipped" | "failed"
                "size_mb": size_mb,
            })
            # Keep the list bounded so a 500-video run doesn't bloat memory
            if len(entry["recent"]) > 50:
                entry["recent"] = entry["recent"][-50:]
            entry["current_video_id"] = None
            entry["current_title"] = None

    try:
        for video_id, source_url, title in videos:
            out_path = os.path.join(download_dir, f"{video_id}.mp4")

            # Skip already downloaded
            if os.path.exists(out_path) and os.path.getsize(out_path) > 1_000_000:
                skipped += 1
                _update_progress()
                _record_item(video_id, title, "skipped")
                continue

            logger.info(
                f"Mass download [{channel_name}] {completed + skipped + failed + 1}/{total}: "
                f"video {video_id} '{(title or '')[:50]}'"
            )
            _set_current(video_id, title)

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
                        _record_item(video_id, title, "done", size_mb)
                    else:
                        err = stderr.decode(errors="ignore")[:200] if stderr else "unknown"
                        logger.warning(
                            f"Mass download [{channel_name}]: video {video_id} "
                            f"failed (rc={proc.returncode}) — {err}"
                        )
                        failed += 1
                        _update_progress()
                        _record_item(video_id, title, "failed")
                        if os.path.exists(out_path):
                            os.remove(out_path)
                except asyncio.TimeoutError:
                    logger.warning(
                        f"Mass download [{channel_name}]: video {video_id} timed out"
                    )
                    proc.kill()
                    failed += 1
                    _update_progress()
                    _record_item(video_id, title, "failed")
                    if os.path.exists(out_path):
                        os.remove(out_path)
            except Exception as e:
                logger.warning(
                    f"Mass download [{channel_name}]: video {video_id} error — {e}"
                )
                failed += 1
                _update_progress()
                _record_item(video_id, title, "failed")

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
        # Session 50 — preserve a summary of this run so the web UI can show
        # "Last run" results after the active entry is gone.
        import datetime as _dt
        entry = _active_downloads.get(channel_id, {})
        _last_download_results[channel_id] = {
            "completed":   completed,
            "skipped":     skipped,
            "failed":      failed,
            "total":       total,
            "recent":      list(entry.get("recent", [])),
            "finished_at": _dt.datetime.utcnow().isoformat() + "Z",
        }
        # Always clean up the active downloads registry
        _active_downloads.pop(channel_id, None)


# ---------------------------------------------------------------------------
# Local Folder Endpoints — Session 45
# ---------------------------------------------------------------------------

@router.get("/local-folders")
async def list_local_folders():
    """
    List available subfolders under /watchdawg/ that contain video files.
    Used by the web UI Add Local Folder form to populate the folder picker.

    Returns folders from:
    - /watchdawg/Private/  (locked content)
    - /watchdawg/Public/   (unlocked content)
    - /watchdawg/          (root level subfolders)
    """
    import os
    from app.config import settings as _cfg

    folders = []
    roots_to_scan = [
        (_cfg.private_downloads_path, "Private"),
        (_cfg.public_downloads_path, "Public"),
    ]

    for root_path, label in roots_to_scan:
        if not os.path.isdir(root_path):
            continue
        try:
            for entry in sorted(os.scandir(root_path), key=lambda e: e.name.lower()):
                if not entry.is_dir():
                    continue
                abs_path = entry.path
                # Count video files in the folder
                try:
                    file_count = sum(
                        1 for f in os.scandir(abs_path)
                        if f.is_file() and os.path.splitext(f.name)[1].lower()
                        in _LOCAL_VIDEO_EXTS
                    )
                except OSError:
                    file_count = 0
                folders.append({
                    "name": entry.name,
                    "path": abs_path,
                    "label": f"{label}/{entry.name}",
                    "file_count": file_count,
                })
        except OSError:
            continue

    return {"folders": folders}


# ---------------------------------------------------------------------------
# Live M3U Export — Session 45
#
# Every entry points to /channel/stream/{video_id} — resolved on demand at
# play time so CDN token expiry never causes black screens.
#
# group-title: first genre tag if set, otherwise category capitalised.
# Shuffle: DB query uses created_at (fast index), Python shuffles in memory.
#
# ROUTE ORDER IS CRITICAL — literal paths before int path params:
#   /all/live.m3u  →  /stream/{id}  →  /{channel_id}/live.m3u
# ---------------------------------------------------------------------------


def _m3u_group_for_channel(channel) -> str:
    """First genre tag if set, otherwise category capitalised."""
    tags_raw = (channel.genre_tags or "").strip()
    if tags_raw:
        first_tag = tags_raw.split(",")[0].strip()
        if first_tag:
            return first_tag
    return (channel.category or "general").title()


def _is_hls_stream(url: str) -> bool:
    """
    Return True if the URL points to an HLS manifest.

    Handles both direct CDN URLs (path ends in .m3u8) and proxy-wrapped URLs
    where the real CDN URL is URL-encoded inside the ?url= query parameter
    (e.g. http://host/proxy/stream?url=https%3A%2F%2F...media.m3u8%3F...).
    """
    if not url:
        return False
    path = url.split("?")[0].lower()
    if path.endswith(".m3u8") or "m3u8" in path:
        return True
    # Proxy-wrapped URL: the CDN URL lives URL-encoded in the query string.
    # Decode once and check the inner URL's path the same way.
    if "/proxy/stream" in path and "?" in url:
        import urllib.parse as _up
        query = url.split("?", 1)[1]
        params = _up.parse_qs(query)
        inner = (params.get("url") or [""])[0]
        if inner:
            inner_path = inner.split("?")[0].lower()
            return inner_path.endswith(".m3u8") or "m3u8" in inner_path
    return False


@router.get("/all/live.m3u", response_class=Response)
async def export_all_channels_live_m3u(
    db: AsyncSession = Depends(get_db_session),
):
    """
    Export ALL scraped videos across all enabled channels as one combined
    live M3U playlist. group-title driven by genre_tags or category.
    Videos shuffled randomly in Python on every fetch.
    Add http://192.168.50.42:6868/channel/all/live.m3u to TiviMate.
    """
    base_url = "http://192.168.50.42:6868"

    ch_stmt = select(Channel).where(Channel.enabled == True).order_by(Channel.name)
    ch_result = await db.execute(ch_stmt)
    channels = ch_result.scalars().all()

    lines = ["#EXTM3U"]
    for channel in channels:
        group = _m3u_group_for_channel(channel)

        video_stmt = (
            select(Video)
            .where(
                Video.channel_id == channel.id,
                Video.source_url.isnot(None),
                Video.source_url != "",
                Video.resolution_status != "failed",
            )
            .order_by(Video.created_at.desc())
        )
        video_result = await db.execute(video_stmt)
        videos = list(video_result.scalars().all())
        random.shuffle(videos)

        for v in videos:
            duration = v.duration_seconds or -1
            title = (v.title or "Untitled").replace(",", " ")
            logo = v.thumbnail_url or ""
            stream_url = f"{base_url}/channel/stream/{v.id}"
            lines.append(
                f'#EXTINF:{duration} tvg-name="{title}" tvg-logo="{logo}" '
                f'group-title="{group}",{title}'
            )
            lines.append(stream_url)

    content_str = "\n".join(lines)
    return Response(
        content=content_str,
        media_type="application/x-mpegurl",
        headers={"Content-Disposition": 'inline; filename="watchdawg_all.m3u"'},
    )


@router.get("/stream/{video_id}", response_class=Response)
async def stream_video_redirect(
    video_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    """
    On-demand resolve-and-redirect for IPTV clients (TiviMate).

    Routing logic:
    - Split HLS stream (audio_url present AND video stream is HLS): some
      Vimeo videos have no combined rendition — yt-dlp returns separate
      video-only and audio-only HLS streams. Generic IPTV players can't
      sync two tracks themselves, so route to /channel/stream/{id}/muxed
      for a server-side ffmpeg remux into one continuous stream.
    - Combined HLS stream (no separate audio_url): route through /proxy/stream.
      The manifest already contains both audio and video tracks.
    - Split MP4 stream (audio_url present, non-HLS, e.g. YouTube): route to
      DASH manifest so ExoPlayer merges both tracks.
    - Combined stream: route through /proxy/stream.

    Uses resolve_video_for_tv() rather than the standard resolver so a
    separately-resolved audio_url is actually available for this routing
    decision — the standard resolver/cache only ever persists one URL.

    Timeout: 40s hard limit on yt-dlp (raised from 25s — YouTube extraction
    with JS-challenge solving routinely takes 10-30+ s, so 25s clipped real
    successes). This is only the safety net for cache MISSES: the scheduler's
    TV warm pass pre-resolves YouTube in the background, so normal playback
    is an instant cache hit and never waits here at all. Falls back to stale
    cached URL if available rather than returning a 502.
    """
    from app.services.resolver import ResolverService
    import asyncio as _asyncio
    import urllib.parse

    base_url = "http://192.168.50.42:6868"
    proxy_base = f"{base_url}/proxy/stream"

    stmt = select(Video).where(Video.id == video_id)
    result = await db.execute(stmt)
    video = result.scalar_one_or_none()

    if video is None:
        raise HTTPException(status_code=404, detail=f"Video {video_id} not found")
    if not video.source_url:
        raise HTTPException(status_code=404, detail=f"Video {video_id} has no source URL")
    if video.resolution_status == "failed":
        logger.warning(f"STREAM REDIRECT | video {video_id} permanently failed — 404")
        raise HTTPException(status_code=404, detail=f"Video {video_id} is permanently unavailable")

    # Local folder files: serve directly via /library/stream/ — no yt-dlp needed
    if video.source_provider == "local_folder":
        if video.resolved_stream_url:
            logger.info(
                f"STREAM REDIRECT | video {video_id} — local file → {video.resolved_stream_url[:80]}"
            )
            from fastapi.responses import RedirectResponse
            return RedirectResponse(url=video.resolved_stream_url, status_code=302)
        raise HTTPException(
            status_code=404,
            detail=f"Local file not found for video {video_id}"
        )

    logger.info(f"STREAM REDIRECT | video {video_id} | title={(video.title or '')[:60]}")

    resolver = ResolverService(db)
    resolution = None
    try:
        resolution = await _asyncio.wait_for(
            resolver.resolve_video_for_tv(video_id),
            timeout=40.0,
        )
    except _asyncio.TimeoutError:
        logger.warning(f"STREAM REDIRECT | video {video_id} — yt-dlp timed out after 40s")

    # Stale-cache fallback
    if resolution is None and video.resolved_stream_url:
        logger.warning(f"STREAM REDIRECT | video {video_id} — serving stale cached URL")
        resolution = {"stream_url": video.resolved_stream_url, "audio_url": None}

    if resolution is None or not resolution.get("stream_url"):
        logger.warning(f"STREAM REDIRECT | video {video_id} — no stream URL, returning 502")
        raise HTTPException(status_code=502, detail=f"Could not resolve stream for video {video_id}")

    cdn_url = resolution["stream_url"]
    audio_url = resolution.get("audio_url")

    # Split HLS: Vimeo serves separate video-only and audio-only HLS sub-playlists
    # with no combined rendition. Build a synthetic HLS master manifest that
    # declares both renditions so TiviMate can sync them natively — no ffmpeg.
    if audio_url and _is_hls_stream(cdn_url):
        master_url = f"{base_url}/channel/stream/{video_id}/master.m3u8"
        logger.info(f"STREAM REDIRECT | video {video_id} — split HLS → master manifest")
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=master_url, status_code=302)

    # Combined HLS streams (Vimeo): proxy directly — the manifest already
    # contains both audio and video tracks. TiviMate plays it correctly
    # through the proxy (which injects Vimeo Referer headers). This is the
    # same path the Android app uses when selecting HLS — it works.
    # Never use master playlist or DASH for HLS; those only work for split MP4.
    if _is_hls_stream(cdn_url):
        proxy_url = f"{proxy_base}?url={urllib.parse.quote(cdn_url, safe='')}"
        logger.info(f"STREAM REDIRECT | video {video_id} — HLS → proxy → {cdn_url[:80]}")
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=proxy_url, status_code=302)

    # Split MP4 (non-HLS, e.g. YouTube): DASH manifest merges both tracks.
    if audio_url and not _is_hls_stream(audio_url):
        manifest_url = f"{base_url}/resolve/{video_id}/manifest.mpd"
        logger.info(f"STREAM REDIRECT | video {video_id} — split MP4 → DASH manifest")
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=manifest_url, status_code=302)

    # Combined stream: proxy handles CDN headers.
    proxy_url = f"{proxy_base}?url={urllib.parse.quote(cdn_url, safe='')}"
    logger.info(f"STREAM REDIRECT | video {video_id} — combined → proxy → {cdn_url[:80]}")
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url=proxy_url, status_code=302)


@router.get("/stream/{video_id}/master.m3u8")
async def stream_video_master_manifest(
    video_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Generate a synthetic HLS master manifest for split video+audio streams.

    Some Vimeo videos have no combined rendition — yt-dlp returns separate
    video-only and audio-only HLS sub-playlists. A generic IPTV player like
    TiviMate cannot play two separate sub-playlists simultaneously, but it
    CAN play a standard HLS master manifest that declares both renditions via
    #EXT-X-MEDIA and #EXT-X-STREAM-INF tags. The player fetches both
    sub-playlists in parallel and syncs them natively — no ffmpeg needed.

    Both sub-playlist URLs are already proxied through /proxy/stream by
    resolve_video_for_tv(), so Vimeo CDN Referer injection is handled there.

    For combined streams (no separate audio_url), returns a minimal single-
    rendition master manifest that still works correctly — TiviMate treats
    it as a normal HLS stream.
    """
    from app.services.resolver import ResolverService
    import asyncio as _asyncio
    from fastapi.responses import PlainTextResponse

    resolver = ResolverService(db)
    resolution = await resolver.resolve_video_for_tv(video_id)

    if resolution is None or not resolution.get("stream_url"):
        raise HTTPException(
            status_code=502,
            detail=f"Could not resolve video {video_id} for HLS master manifest",
        )

    video_url = resolution["stream_url"]
    audio_url = resolution.get("audio_url")

    # The resolver wraps Vimeo CDN URLs through localhost for internal use —
    # replace localhost with the real PlexServer IP so TiviMate (running on
    # a different device) can actually reach the proxy endpoints.
    base_url = "http://192.168.50.42:6868"
    if video_url:
        video_url = video_url.replace("http://localhost:6868", base_url)
    if audio_url:
        audio_url = audio_url.replace("http://localhost:6868", base_url)

    if audio_url:
        # Split stream: declare audio rendition + video stream referencing it.
        # CODECS omitted intentionally — TiviMate's parser rejects mismatched
        # codec strings; letting it sniff from the sub-playlists is more robust.
        manifest = (
            "#EXTM3U\n"
            f'#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",NAME="Audio",'
            f'DEFAULT=YES,AUTOSELECT=YES,URI="{audio_url}"\n'
            f'#EXT-X-STREAM-INF:BANDWIDTH=4000000,AUDIO="audio"\n'
            f"{video_url}\n"
        )
        logger.info(
            f"MASTER MANIFEST | video {video_id} — split HLS "
            f"(video+audio declared)"
        )
    else:
        # Combined stream: single-rendition master, no audio group needed
        manifest = (
            "#EXTM3U\n"
            "#EXT-X-STREAM-INF:BANDWIDTH=4000000\n"
            f"{video_url}\n"
        )
        logger.info(
            f"MASTER MANIFEST | video {video_id} — combined HLS "
            f"(single rendition)"
        )

    return PlainTextResponse(
        content=manifest,
        media_type="application/vnd.apple.mpegurl",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@router.get("/{channel_id}/live.m3u", response_class=Response)
async def export_channel_live_m3u(
    channel_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Export all scraped videos for a single channel as a live M3U playlist.
    group-title uses first genre tag if set, otherwise category.
    Videos shuffled randomly in Python on every fetch.
    """
    base_url = "http://192.168.50.42:6868"

    stmt = select(Channel).where(Channel.id == channel_id)
    result = await db.execute(stmt)
    channel = result.scalar_one_or_none()
    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")

    video_stmt = (
        select(Video)
        .where(
            Video.channel_id == channel_id,
            Video.source_url.isnot(None),
            Video.source_url != "",
            Video.resolution_status != "failed",
        )
        .order_by(Video.created_at.desc())
    )
    video_result = await db.execute(video_stmt)
    videos = list(video_result.scalars().all())
    random.shuffle(videos)

    group = _m3u_group_for_channel(channel)
    lines = ["#EXTM3U"]
    for v in videos:
        duration = v.duration_seconds or -1
        title = (v.title or "Untitled").replace(",", " ")
        logo = v.thumbnail_url or ""
        stream_url = f"{base_url}/channel/stream/{v.id}"
        lines.append(
            f'#EXTINF:{duration} tvg-name="{title}" tvg-logo="{logo}" '
            f'group-title="{group}",{title}'
        )
        lines.append(stream_url)

    content_str = "\n".join(lines)
    safe_name = re.sub(r"[^\w\-]", "_", channel.name)
    return Response(
        content=content_str,
        media_type="application/x-mpegurl",
        headers={"Content-Disposition": f'inline; filename="{safe_name}.m3u"'},
    )
