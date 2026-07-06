"""
Favorite / Download API Router.

Endpoints:
- POST   /favorite/{video_id}/bookmark  — Bookmark only, NO download.
- POST   /favorite/{video_id}           — Bookmark AND trigger background download.
- GET    /favorite                       — List favorites (filtered by lock state).
- DELETE /favorite/{favorite_id}         — Remove favorite; deletes file if downloaded.
- POST   /favorite/{favorite_id}/retry  — Retry a failed/pending download.

PIN lock (Milestone D):
  GET /favorite filters by token — locked-channel favorites hidden when unauthenticated.
  DELETE /favorite always deletes the physical file when download_status=complete
  so Library stays in sync and no orphan files appear as blank cards.

Download routing:
- Locked channel   → settings.private_downloads_path  (/watchdawg/Private/{channel}/)
- Unlocked channel → settings.public_downloads_path   (/watchdawg/Public/{channel}/)
- No channel       → settings.public_downloads_path   (/watchdawg/Public/Uncategorized/)

R-4 change:
  Added `channel_locked` boolean to the GET /favorite serialized response.
  The Android client uses this field to split favorites into two piles:
    - channel_locked=False  → shown in main Favorites screen (always, no PIN needed)
    - channel_locked=True   → shown only in Adult screen → Favorites pill (PIN gated)
  The backend still filters out locked-channel favorites when unauthenticated —
  that server-side gate is unchanged. The new field is additive.
"""

import asyncio
import datetime
import logging
import os
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session, async_session_factory
from app.models import Video, Favorite, Channel
from app.config import settings
from app.routers.auth import is_unlocked

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/favorite", tags=["favorite"])


@router.post("/{video_id}/bookmark")
async def bookmark_video(
    video_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Bookmark a video without downloading it.

    Creates a Favorite with download_status='none' — appears in Favorites
    tab but NO file is written to disk. This is the ♥ Fav action.
    """
    stmt = select(Video).where(Video.id == video_id)
    result = await db.execute(stmt)
    video = result.scalar_one_or_none()

    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")

    existing = await db.execute(
        select(Favorite).where(Favorite.video_id == video_id)
    )
    if existing.scalar_one_or_none() is not None:
        return {"status": "already_favorited", "video_id": video_id}

    favorite = Favorite(video_id=video_id, download_status="none")
    db.add(favorite)
    await db.commit()

    logger.info(f"Bookmarked video {video_id}: {video.title} (no download)")
    return {
        "status": "bookmarked",
        "video_id": video_id,
        "title": video.title,
        "download_status": "none",
    }


@router.post("/{video_id}")
async def favorite_video(
    video_id: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Bookmark AND trigger a background yt-dlp download (⬇ Save action).

    If already bookmarked with download_status='none' or 'failed',
    upgrades to a download without creating a duplicate record.
    Routes to Public/ or Private/ based on channel.locked.
    """
    stmt = select(Video).where(Video.id == video_id)
    result = await db.execute(stmt)
    video = result.scalar_one_or_none()

    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")

    channel_name = None
    channel_locked = False
    if video.channel_id:
        ch_stmt = select(Channel).where(Channel.id == video.channel_id)
        ch_result = await db.execute(ch_stmt)
        ch = ch_result.scalar_one_or_none()
        if ch:
            channel_name = ch.name
            channel_locked = ch.locked

    existing_result = await db.execute(
        select(Favorite).where(Favorite.video_id == video_id)
    )
    existing = existing_result.scalar_one_or_none()

    if existing is not None:
        if existing.download_status in ("none", "failed"):
            existing.download_status = "pending"
            existing.download_error = None
            await db.commit()
            background_tasks.add_task(
                _download_video_task,
                video_id=video.id,
                source_url=video.source_url,
                title=video.title,
                artist=video.artist,
                channel_name=channel_name,
                channel_locked=channel_locked,
            )
            return {
                "status": "download_queued",
                "video_id": video_id,
                "title": video.title,
                "download_status": "pending",
            }
        return {"status": "already_favorited", "video_id": video_id}

    favorite = Favorite(video_id=video_id, download_status="pending")
    db.add(favorite)
    await db.commit()

    background_tasks.add_task(
        _download_video_task,
        video_id=video.id,
        source_url=video.source_url,
        title=video.title,
        artist=video.artist,
        channel_name=channel_name,
        channel_locked=channel_locked,
    )

    logger.info(f"Save+download video {video_id}: {video.title} (locked={channel_locked})")
    return {
        "status": "favorited",
        "video_id": video_id,
        "title": video.title,
        "download_status": "pending",
    }


@router.get("")
async def list_favorites(
    x_watchdawg_token: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db_session),
):
    """
    List favorited videos, filtered by lock state.

    PIN lock: when no valid token, favorites from locked channels are excluded.
    This ensures adult bookmarks are invisible to unauthenticated clients,
    consistent with how the feed and library endpoints behave.

    R-4: Added `channel_locked` boolean to each item so the Android client can
    split favorites into two piles without an additional API call:
      channel_locked=False → main Favorites screen (always visible, no PIN)
      channel_locked=True  → Adult screen Favorites pill (PIN required)

    download_status values:
      'none'        — bookmark only, no file on disk
      'pending'     — queued for download
      'downloading' — yt-dlp running
      'complete'    — file on disk, stream_url populated
      'failed'      — download failed, download_error populated
    """
    unlocked = is_unlocked(x_watchdawg_token)

    stmt = (
        select(Favorite, Video, Channel)
        .join(Video, Favorite.video_id == Video.id)
        .outerjoin(Channel, Video.channel_id == Channel.id)
    )
    result = await db.execute(stmt)
    rows = result.all()

    downloads_dir = settings.downloads_path

    favorites = []
    for fav, video, channel in rows:
        # Filter out locked-channel favorites when session is not authenticated.
        # Server-side gate is unchanged — channel_locked field is additive.
        if not unlocked and channel and channel.locked:
            continue

        stream_url = None
        if fav.local_file_path and os.path.isfile(fav.local_file_path):
            rel = os.path.relpath(fav.local_file_path, downloads_dir)
            import urllib.parse
            stream_url = f"/library/stream/{urllib.parse.quote(rel, safe='/')}"

        # R-4: include channel_locked so client can route to correct screen
        channel_locked = channel.locked if channel else False

        favorites.append({
            "id": fav.id,
            "video_id": fav.video_id,
            "title": video.title,
            "artist": video.artist,
            "source_url": video.source_url,
            "source_provider": video.source_provider,
            "thumbnail_url": video.thumbnail_url,
            "channel_id": video.channel_id,
            "channel_name": channel.name if channel else None,
            "channel_locked": channel_locked,           # ← R-4 addition
            "download_status": fav.download_status,
            "download_error": fav.download_error,
            "local_file_path": fav.local_file_path,
            "stream_url": stream_url,
            "downloaded_at": fav.downloaded_at.isoformat() if fav.downloaded_at else None,
            "created_at": fav.created_at.isoformat() if fav.created_at else None,
        })

    return {"favorites": favorites}


@router.delete("/{favorite_id}")
async def remove_favorite(
    favorite_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Remove a favorite and always delete the physical file if one was downloaded.

    File deletion is unconditional when download_status='complete' — this keeps
    Library in sync. An orphaned file (favorite deleted but file left on disk)
    shows up in Library as a blank unremovable card. Deleting the file on
    favorite removal prevents that entirely.

    Bookmark-only favorites (download_status='none') have no file to delete.
    """
    stmt = select(Favorite).where(Favorite.id == favorite_id)
    result = await db.execute(stmt)
    favorite = result.scalar_one_or_none()

    if favorite is None:
        raise HTTPException(status_code=404, detail="Favorite not found")

    deleted_file = False
    # Always delete the physical file for completed downloads to keep Library in sync
    if favorite.download_status == "complete" and favorite.local_file_path:
        try:
            if os.path.isfile(favorite.local_file_path):
                os.remove(favorite.local_file_path)
                deleted_file = True
                logger.info(f"Deleted file on favorite removal: {favorite.local_file_path}")
        except Exception as e:
            logger.error(f"Failed to delete file {favorite.local_file_path}: {e}")

    await db.delete(favorite)
    await db.commit()

    logger.info(f"Removed favorite {favorite_id} (file_deleted={deleted_file})")
    return {"status": "removed", "favorite_id": favorite_id, "file_deleted": deleted_file}


@router.post("/{favorite_id}/retry")
async def retry_download(
    favorite_id: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db_session),
):
    """Retry a failed or pending download."""
    stmt = select(Favorite).where(Favorite.id == favorite_id)
    result = await db.execute(stmt)
    favorite = result.scalar_one_or_none()

    if favorite is None:
        raise HTTPException(status_code=404, detail="Favorite not found")

    if favorite.download_status == "complete":
        return {"status": "already_complete", "favorite_id": favorite_id}
    if favorite.download_status == "downloading":
        return {"status": "already_downloading", "favorite_id": favorite_id}

    video_stmt = select(Video).where(Video.id == favorite.video_id)
    video_result = await db.execute(video_stmt)
    video = video_result.scalar_one_or_none()

    if video is None:
        raise HTTPException(status_code=404, detail="Video record not found")

    channel_name = None
    channel_locked = False
    if video.channel_id:
        ch_stmt = select(Channel).where(Channel.id == video.channel_id)
        ch_result = await db.execute(ch_stmt)
        ch = ch_result.scalar_one_or_none()
        if ch:
            channel_name = ch.name
            channel_locked = ch.locked

    favorite.download_status = "pending"
    favorite.download_error = None
    await db.commit()

    background_tasks.add_task(
        _download_video_task,
        video_id=video.id,
        source_url=video.source_url,
        title=video.title,
        artist=video.artist,
        channel_name=channel_name,
        channel_locked=channel_locked,
    )

    return {"status": "retrying", "favorite_id": favorite_id, "title": video.title}


async def _download_video_task(
    video_id: int,
    source_url: str,
    title: str,
    artist: Optional[str],
    channel_name: Optional[str] = None,
    channel_locked: bool = False,
):
    """
    Background task — downloads via yt-dlp.
    Routes to Private/ or Public/ based on channel_locked.
    """
    logger.info(
        f"Download task started: video_id={video_id} url={source_url} "
        f"channel={channel_name!r} locked={channel_locked}"
    )

    async with async_session_factory() as db:
        favorite = None
        try:
            fav_stmt = select(Favorite).where(Favorite.video_id == video_id)
            result = await db.execute(fav_stmt)
            favorite = result.scalar_one_or_none()

            if favorite is None:
                logger.error(f"Download task: no Favorite record for video_id={video_id}")
                return

            favorite.download_status = "downloading"
            favorite.download_error = None
            await db.commit()

            base_dir = settings.private_downloads_path if channel_locked else settings.public_downloads_path

            if channel_name:
                safe_channel = re.sub(r'[<>:"/\\|?*]', '', channel_name).strip()
                safe_channel = re.sub(r'\s+', ' ', safe_channel)
                output_dir = os.path.join(base_dir, safe_channel) if safe_channel else base_dir
            else:
                output_dir = os.path.join(base_dir, "Uncategorized")

            logger.info(f"Download task: output_dir={output_dir} (locked={channel_locked})")

            if not os.path.exists(output_dir):
                try:
                    os.makedirs(output_dir, exist_ok=True)
                except Exception as e:
                    error = f"Cannot create output directory: {output_dir}: {e}"
                    logger.error(f"Download task: {error}")
                    favorite.download_status = "failed"
                    favorite.download_error = error
                    await db.commit()
                    return

            if not os.access(output_dir, os.W_OK):
                error = f"Output directory not writable: {output_dir}"
                logger.error(f"Download task: {error}")
                favorite.download_status = "failed"
                favorite.download_error = error
                await db.commit()
                return

            filename = _build_filename(title, artist)
            output_path = os.path.join(output_dir, filename)

            existing_path = _find_downloaded_file(output_path)
            if existing_path:
                logger.info(f"Download task: file already exists at {existing_path}")
                favorite.download_status = "complete"
                favorite.local_file_path = existing_path
                favorite.downloaded_at = datetime.datetime.utcnow()
                favorite.download_error = None
                await db.commit()
                return

            success, error_msg = await asyncio.to_thread(
                _download_sync, source_url, output_path
            )

            if success:
                actual_path = _find_downloaded_file(output_path)
                favorite.download_status = "complete"
                favorite.local_file_path = actual_path or output_path
                favorite.downloaded_at = datetime.datetime.utcnow()
                favorite.download_error = None
                logger.info(f"Download task: COMPLETE -> {favorite.local_file_path}")
            else:
                favorite.download_status = "failed"
                favorite.download_error = error_msg or "Unknown error"
                logger.error(f"Download task: FAILED video_id={video_id}: {error_msg}")

            await db.commit()

        except Exception as e:
            error_str = str(e)
            logger.error(
                f"Download task: unhandled exception video_id={video_id}: {error_str}",
                exc_info=True,
            )
            try:
                if favorite is not None:
                    favorite.download_status = "failed"
                    favorite.download_error = f"Exception: {error_str[:300]}"
                    await db.commit()
            except Exception:
                pass


def _find_downloaded_file(expected_path: str) -> Optional[str]:
    if os.path.isfile(expected_path):
        return expected_path
    base = os.path.splitext(expected_path)[0]
    for ext in [".mp4", ".mkv", ".webm", ".m4v"]:
        candidate = base + ext
        if os.path.isfile(candidate):
            return candidate
    return None


def _download_sync(source_url: str, output_path: str) -> tuple:
    """Synchronous yt-dlp download. Exported for scraper.py."""
    import yt_dlp

    output_template = os.path.splitext(output_path)[0]

    ydl_opts = {
        "format": (
            "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/"
            "bestvideo[height<=1080]+bestaudio/"
            "best[height<=1080]/"
            "best"
        ),
        "outtmpl": output_template + ".%(ext)s",
        "merge_output_format": "mp4",
        "quiet": False,
        "no_warnings": False,
        "retries": 3,
        "fragment_retries": 3,
        "socket_timeout": 60,
        "writethumbnail": False,
        "postprocessors": [
            {"key": "FFmpegVideoConvertor", "preferedformat": "mp4"},
        ],
    }

    cookies_path = settings.ytdlp_cookies_path
    if cookies_path and os.path.isfile(cookies_path):
        ydl_opts["cookiefile"] = cookies_path
    else:
        logger.warning(f"Download: no cookies.txt at {cookies_path}")

    logger.info(f"Download: starting yt-dlp for {source_url}")
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([source_url])
        return True, None
    except Exception as e:
        error_msg = str(e)[:500]
        logger.error(f"Download: yt-dlp FAILED for {source_url}: {error_msg}")
        return False, error_msg


def _build_filename(title: str, artist: Optional[str]) -> str:
    """Build a safe filename. Exported for scraper.py."""
    if artist and artist.lower() not in title.lower():
        name = f"{artist} - {title}"
    else:
        name = title
    name = re.sub(r'[<>:"/\\|?*]', "", name)
    name = re.sub(r"\s+", " ", name).strip()
    if len(name) > 200:
        name = name[:200]
    return f"{name}.mp4"
