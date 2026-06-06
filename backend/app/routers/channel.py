"""
Channel Management API Router.

Endpoints:
- GET    /channel                    — List all channels.
- POST   /channel                    — Add a new channel (auto-detects type from URL).
                                       For YouTube @handle or /channel/ID URLs, attempts
                                       full playlist enumeration and creates one Channel
                                       per discovered playlist (Session 38).
- DELETE /channel/{id}               — Remove a channel.
- PATCH  /channel/{id}               — Toggle enabled/disabled.
- PATCH  /channel/{id}/lock          — Toggle locked/unlocked (PIN gate).
- PATCH  /channel/{id}/category      — Set channel category.
- PATCH  /channel/{id}/genre_tags    — Set genre tags (Milestone R-1).
- PATCH  /channel/{id}/rename        — Rename a channel.
- POST   /channel/{id}/scrape        — Scrape a single channel on demand.
- DELETE /channel/{id}/videos        — Clear all videos from a channel.

Milestone B: added category field to channel serializer and new
PATCH /channel/{id}/category endpoint.

Milestone R-1: added genre_tags field to ChannelAddRequest and channel
serializer; new PATCH /channel/{id}/genre_tags endpoint.

Session 38: YouTube full-channel playlist enumeration.
When a YouTube @handle or /channel/ID URL is added, enumerate_channel_playlists()
runs yt-dlp in flat-playlist mode against the channel's /playlists page and
creates one ytdlp_playlist Channel row per discovered playlist, each linked back
to a root channel record via parent_channel_id.  Falls back to the original
single /videos channel if enumeration returns zero playlists or times out.
"""

import asyncio
import datetime
import logging
import os
import re
from typing import Optional, List
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

# Timeout (seconds) for the yt-dlp playlist enumeration call.
# Channels with hundreds of playlists can take a while; 60s is generous
# but keeps the HTTP request from hanging forever.
_ENUM_TIMEOUT_SECONDS = 60


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


class ChannelRenameRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200, description="New display name")


# --- Auto-Detection Logic ---

def detect_channel_type(url: str) -> dict:
    """
    Auto-detect the channel type from a URL or identifier string.

    Returns a dict with:
      - channel_type: The detected type string
      - url: The normalized URL
      - name: A suggested display name
      - unique_key: A dedup key
      - is_yt_channel_root: True when the URL is a YouTube @handle or /channel/ID
                            that should trigger playlist enumeration (Session 38)

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
            "is_yt_channel_root": False,
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
            "is_yt_channel_root": False,
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
            "is_yt_channel_root": False,
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
                "is_yt_channel_root": False,
            }

    # --- YouTube playlist (explicit list=... URL — never enumerate) ---
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
            "is_yt_channel_root": False,
        }

    # --- YouTube channel (@handle or /channel/ID) — enumerate playlists ---
    yt_channel_match = re.match(
        r"https?://(?:www\.)?youtube\.com/(@[A-Za-z0-9_.-]+|channel/[A-Za-z0-9_-]+)(?:/(?:videos|playlists)?)?/?",
        url,
    )
    if yt_channel_match:
        channel_path = yt_channel_match.group(1)
        # Normalise: strip trailing /videos or /playlists — we want the bare handle
        channel_path = channel_path.rstrip("/")
        return {
            "channel_type": "ytdlp_playlist",
            # Root record points at /videos as its fallback scrape URL
            "url": f"https://www.youtube.com/{channel_path}/videos",
            "name": f"YouTube: {channel_path}",
            "unique_key": f"youtube_channel:{channel_path.lower()}",
            "is_yt_channel_root": True,          # triggers enumeration in add_channel
            "channel_path": channel_path,         # used to build the /playlists URL
        }

    # --- Generic URL fallback (no enumeration) ---
    parsed = urlparse(url)
    if parsed.scheme in ("http", "https") and parsed.netloc:
        domain = parsed.netloc.replace("www.", "")
        path_slug = parsed.path.strip("/").replace("/", "-")[:40]
        return {
            "channel_type": "ytdlp_playlist",
            "url": url,
            "name": f"{domain}: {path_slug}" if path_slug else domain,
            "unique_key": f"playlist:{domain}:{path_slug}".lower(),
            "is_yt_channel_root": False,
        }

    raise ValueError(
        f"Could not detect channel type from: {url}\n"
        "Supported formats:\n"
        "  - Reddit: https://reddit.com/r/SubName or r/SubName\n"
        "  - Vimeo: https://vimeo.com/channels/name\n"
        "  - YouTube: https://youtube.com/playlist?list=... or https://youtube.com/@channel\n"
        "  - Any other yt-dlp-compatible playlist URL"
    )


# --- YouTube playlist enumeration (Session 38) ---

def _enumerate_channel_playlists_sync(channel_path: str, cookies_path: Optional[str]) -> List[dict]:
    """
    Synchronous inner function — runs inside a thread executor.

    Uses yt-dlp flat-playlist mode against the channel's /playlists page to
    discover all public playlists without downloading any video data.

    Returns a list of dicts, each with:
      - playlist_id:  str
      - playlist_url: str
      - title:        str
    Returns [] on any error or if no playlists found.
    """
    import yt_dlp

    playlists_url = f"https://www.youtube.com/{channel_path}/playlists"

    ydl_opts = {
        "extract_flat": "in_playlist",
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "ignoreerrors": True,
        "socket_timeout": 30,
        "retries": 2,
    }

    if cookies_path and os.path.isfile(cookies_path):
        ydl_opts["cookiefile"] = cookies_path
        logger.debug(f"[enum] Using cookies from {cookies_path}")

    discovered = []
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(playlists_url, download=False)
            if not info:
                logger.warning(f"[enum] yt-dlp returned nothing for {playlists_url}")
                return []

            entries = info.get("entries") or []
            for entry in entries:
                if not entry:
                    continue
                pid = entry.get("id") or entry.get("playlist_id")
                title = entry.get("title") or entry.get("playlist_title") or pid
                if not pid:
                    continue
                # Skip auto-generated "Videos" pseudo-playlist entries that some
                # channels expose — yt-dlp sometimes returns the uploads feed as
                # a playlist entry under the /playlists tab.
                if title and title.strip().lower() in ("videos", "shorts", "streams", "live"):
                    continue
                discovered.append({
                    "playlist_id": pid,
                    "playlist_url": f"https://www.youtube.com/playlist?list={pid}",
                    "title": title.strip() if title else pid,
                })
    except Exception as exc:
        logger.warning(f"[enum] yt-dlp enumeration error for {playlists_url}: {exc}")

    logger.info(f"[enum] {channel_path}: found {len(discovered)} playlist(s)")
    return discovered


async def enumerate_channel_playlists(channel_path: str, cookies_path: Optional[str]) -> List[dict]:
    """
    Async wrapper — runs _enumerate_channel_playlists_sync in a thread executor
    with a timeout so it never blocks the event loop indefinitely.

    Returns [] on timeout or any error.
    """
    loop = asyncio.get_event_loop()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                _enumerate_channel_playlists_sync,
                channel_path,
                cookies_path,
            ),
            timeout=_ENUM_TIMEOUT_SECONDS,
        )
        return result
    except asyncio.TimeoutError:
        logger.warning(
            f"[enum] Playlist enumeration timed out after {_ENUM_TIMEOUT_SECONDS}s "
            f"for channel path: {channel_path}. Falling back to single /videos source."
        )
        return []
    except Exception as exc:
        logger.warning(f"[enum] Unexpected error during enumeration: {exc}")
        return []


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
        "parent_channel_id": ch.parent_channel_id,
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

    Session 38 — YouTube full-channel enumeration:
    When the URL is a YouTube @handle or /channel/ID, the backend enumerates all
    public playlists on that channel and creates one Channel row per playlist,
    all linked back to a root channel record via parent_channel_id.

    Response shapes:
      Single channel added:   {"status": "added",    "channel": {...}}
      Playlists enumerated:   {"status": "expanded",  "root_channel": {...},
                               "playlists_created": N, "channels": [...]}
      Root already exists,
      new playlists found:    {"status": "expanded",  "root_channel": {...},
                               "playlists_created": N, "channels": [...]}
      Already exists (exact): 409 Conflict
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

    genre_tags = _normalise_genre_tags(request.genre_tags or "")

    # -------------------------------------------------------------------------
    # YouTube channel root — attempt playlist enumeration (Session 38)
    # -------------------------------------------------------------------------
    if detected.get("is_yt_channel_root"):
        channel_path = detected["channel_path"]
        root_unique_key = detected["unique_key"]
        display_name = request.name if request.name else detected["name"]

        # Check whether this root channel already exists
        existing_root_result = await db.execute(
            select(Channel).where(Channel.unique_key == root_unique_key)
        )
        existing_root = existing_root_result.scalar_one_or_none()

        if existing_root is None:
            # Create the root record (always created; acts as the parent anchor)
            # Auto-lock if category is adult (Session 38)
            root_locked = (category == "adult")
            root_channel = Channel(
                name=display_name,
                channel_type="ytdlp_playlist",
                url=detected["url"],         # points at /videos as fallback
                unique_key=root_unique_key,
                enabled=True,
                locked=root_locked,
                category=category,
                genre_tags=genre_tags,
                parent_channel_id=None,      # root has no parent
            )
            db.add(root_channel)
            await db.commit()
            await db.refresh(root_channel)
            logger.info(f"Created YouTube root channel: {root_channel.name} (id={root_channel.id})")
        else:
            root_channel = existing_root
            logger.info(f"YouTube root channel already exists: {root_channel.name} (id={root_channel.id})")

        # Discover playlists (runs yt-dlp in a thread with a timeout)
        cookies_path = os.environ.get("YTDLP_COOKIES_PATH", "/app/cookies.txt")
        playlists = await enumerate_channel_playlists(channel_path, cookies_path)

        if not playlists:
            # Enumeration returned nothing — keep just the root /videos channel
            logger.info(
                f"[enum] No playlists found for {channel_path}; "
                f"root /videos channel kept as sole source."
            )
            return {
                "status": "added",
                "channel": _serialize_channel(root_channel),
                "note": "No public playlists found; added as single /videos source.",
            }

        # Create one child Channel per discovered playlist, skipping duplicates
        created_channels = []
        skipped = 0
        for pl in playlists:
            child_unique_key = f"youtube_playlist:{pl['playlist_id']}"

            existing = await db.execute(
                select(Channel).where(Channel.unique_key == child_unique_key)
            )
            if existing.scalar_one_or_none() is not None:
                skipped += 1
                continue

            child_name = f"{display_name} — {pl['title']}"
            child = Channel(
                name=child_name,
                channel_type="ytdlp_playlist",
                url=pl["playlist_url"],
                unique_key=child_unique_key,
                enabled=True,
                locked=(category == "adult"),   # auto-lock if adult category (Session 38)
                category=category,
                genre_tags=genre_tags,
                parent_channel_id=root_channel.id,
            )
            db.add(child)
            created_channels.append(child)

        await db.commit()

        # Refresh all new children to populate their IDs
        for child in created_channels:
            await db.refresh(child)

        logger.info(
            f"[enum] {channel_path}: created {len(created_channels)} playlist channel(s), "
            f"skipped {skipped} duplicate(s)."
        )

        return {
            "status": "expanded",
            "root_channel": _serialize_channel(root_channel),
            "playlists_found": len(playlists),
            "playlists_created": len(created_channels),
            "playlists_skipped": skipped,
            "channels": [_serialize_channel(ch) for ch in created_channels],
        }

    # -------------------------------------------------------------------------
    # All other channel types — single channel, existing behaviour
    # -------------------------------------------------------------------------
    existing = await db.execute(
        select(Channel).where(Channel.unique_key == detected["unique_key"])
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Channel already exists: {detected['name']}"
        )

    display_name = request.name if request.name else detected["name"]

    channel = Channel(
        name=display_name,
        channel_type=detected["channel_type"],
        url=detected["url"],
        unique_key=detected["unique_key"],
        enabled=True,
        locked=(category == "adult"),   # auto-lock if adult category (Session 38)
        category=category,
        genre_tags=genre_tags,
        parent_channel_id=None,
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


@router.patch("/{channel_id}/rename")
async def rename_channel(
    channel_id: int,
    request: ChannelRenameRequest,
    db: AsyncSession = Depends(get_db_session),
):
    """Rename a channel."""
    stmt = select(Channel).where(Channel.id == channel_id)
    result = await db.execute(stmt)
    channel = result.scalar_one_or_none()

    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")

    old_name = channel.name
    channel.name = request.name.strip()
    await db.commit()

    logger.info(f"Renamed channel {channel_id}: '{old_name}' -> '{channel.name}'")
    return {
        "status": "renamed",
        "channel_id": channel_id,
        "old_name": old_name,
        "name": channel.name,
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
