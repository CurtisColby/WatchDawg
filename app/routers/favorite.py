"""
Favorite / Download API Router.

Endpoints:
- POST   /favorite/{video_id}         -- Mark a video as favorite and start download.
- GET    /favorite                     -- List all favorited videos.
- DELETE /favorite/{favorite_id}       -- Remove a favorite (optionally delete downloaded file).
- POST   /favorite/{favorite_id}/retry -- Retry a failed/pending download.

Downloads are organised into channel-named subfolders under /music_videos so
content from different channels stays separate (e.g. /music_videos/Exymodels/).
Videos with no channel go to /music_videos/Uncategorized/.

PIN Lock:
- GET /favorite returns an empty list (with locked_hidden=True) if PIN lock
  is configured and no valid token is supplied.
- POST /favorite/{video_id} and DELETE work regardless of lock state so that
  the Android TV client can still favorite while unlocked without needing
  to replay the token on every action — but on the browser UI these actions
  are only reachable when unlocked anyway.
"""

import asyncio
import datetime
import logging
import os
import re
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session, async_session_factory
from app.models import Video, Favorite, Channel
from app.config import settings
from app.routers.auth import is_unlocked

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/favorite", tags=["favorite"])


@router.post("/{video_id}")
async def favorite_video(
    video_id: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db_session),
):
    """Mark a video as a favorite and trigger a background download."""
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

    favorite = Favorite(video_id=video_id, download_status="pending")
    db.add(favorite)
    await db.commit()

    background_tasks.add_task(
        _download_video_task,
        video_id=video.id,
        source_url=video.source_url,
        title=video.title,
        artist=video.artist,
        channel_id=video.channel_id,
    )

    logger.info(f"Favorited video {video_id}: {video.title}")
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
    List all favorited videos with their download status.

    PIN lock: Returns an empty list with locked_hidden=True when PIN lock
    is configured and no valid session token is supplied.
    """
    unlocked = is_unlocked(x_watchdawg_token)

    if not unlocked:
        # Return graceful empty response — UI will show PIN bar
        return {
            "favorites": [],
            "locked_hidden": True,
        }

    stmt = select(Favorite, Video).join(Video, Favorite.video_id == Video.id)
    result = await db.execute(stmt)
    rows = result.all()

    def _derive_stream_url(local_file_path: Optional[str]) -> Optional[str]:
        """
        Derive a /library/stream/ URL from the local file path so the
        Favorites tab can play downloaded files directly without re-resolving.
        The filename is percent-encoded to handle spaces and unicode chars.
        """
        if not local_file_path:
            return None
        filename = os.path.basename(local_file_path)
        if not filename:
            return None
        return f"/library/stream/{quote(filename, safe='')}"

    return {
        "favorites": [
            {
                "id": fav.id,
                "video_id": fav.video_id,
                "title": video.title,
                "artist": video.artist,
                "source_url": video.source_url,
                "thumbnail_url": video.thumbnail_url,
                "download_status": fav.download_status,
                "download_error": fav.download_error,
                "local_file_path": fav.local_file_path,
                "stream_url": _derive_stream_url(fav.local_file_path),
                "downloaded_at": fav.downloaded_at.isoformat() if fav.downloaded_at else None,
                "created_at": fav.created_at.isoformat() if fav.created_at else None,
            }
            for fav, video in rows
        ],
        "locked_hidden": False,
    }


@router.delete("/{favorite_id}")
async def remove_favorite(
    favorite_id: int,
    delete_file: bool = Query(False, description="Also delete the downloaded file from NAS"),
    db: AsyncSession = Depends(get_db_session),
):
    """Remove a video from favorites. Optionally deletes the downloaded file."""
    stmt = select(Favorite).where(Favorite.id == favorite_id)
    result = await db.execute(stmt)
    favorite = result.scalar_one_or_none()

    if favorite is None:
        raise HTTPException(status_code=404, detail="Favorite not found")

    deleted_file = False
    if delete_file and favorite.local_file_path:
        try:
            if os.path.isfile(favorite.local_file_path):
                os.remove(favorite.local_file_path)
                deleted_file = True
                logger.info(f"Deleted file: {favorite.local_file_path}")
        except Exception as e:
            logger.error(f"Failed to delete file {favorite.local_file_path}: {e}")

    await db.delete(favorite)
    await db.commit()

    logger.info(f"Removed favorite {favorite_id}")
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

    favorite.download_status = "pending"
    favorite.download_error = None
    await db.commit()

    background_tasks.add_task(
        _download_video_task,
        video_id=video.id,
        source_url=video.source_url,
        title=video.title,
        artist=video.artist,
        channel_id=video.channel_id,
    )

    logger.info(f"Retrying download for favorite {favorite_id}: {video.title}")
    return {"status": "retrying", "favorite_id": favorite_id, "title": video.title}


async def _download_video_task(
    video_id: int,
    source_url: str,
    title: str,
    artist: Optional[str],
    channel_id: Optional[int] = None,
):
    """
    Background task that downloads a video using yt-dlp.

    Downloads into a channel-named subfolder under music_videos so content
    from different channels stays organised (e.g. /music_videos/Exymodels/).
    Falls back to /music_videos/Uncategorized/ for videos with no channel.
    """
    logger.info(f"Download task started: video_id={video_id} url={source_url}")

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
            logger.info(f"Download task: status -> downloading (video_id={video_id})")

            # Resolve channel subfolder
            base_dir = settings.music_videos_path
            subfolder_name = None
            if channel_id:
                ch_result = await db.execute(
                    select(Channel).where(Channel.id == channel_id)
                )
                ch = ch_result.scalar_one_or_none()
                if ch:
                    subfolder_name = _safe_folder_name(ch.name)

            if subfolder_name:
                output_dir = os.path.join(base_dir, subfolder_name)
            else:
                output_dir = os.path.join(base_dir, "Uncategorized")

            logger.info(f"Download task: output_dir={output_dir} (channel={subfolder_name or 'none'})")

            if not os.path.exists(base_dir):
                error = f"Base output directory does not exist: {base_dir}"
                logger.error(f"Download task: {error}")
                favorite.download_status = "failed"
                favorite.download_error = error
                await db.commit()
                return

            # Create channel subfolder if needed
            os.makedirs(output_dir, exist_ok=True)

            if not os.access(output_dir, os.W_OK):
                error = f"Output directory not writable: {output_dir}"
                logger.error(f"Download task: {error}")
                favorite.download_status = "failed"
                favorite.download_error = error
                await db.commit()
                return

            filename = _build_filename(title, artist)
            output_path = os.path.join(output_dir, filename)
            logger.info(f"Download task: target={output_path}")

            # Duplicate file check
            existing_path = _find_downloaded_file(output_path)
            if existing_path:
                logger.info(
                    f"Download task: file already exists at {existing_path} -- "
                    "marking complete without re-downloading"
                )
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


def _safe_folder_name(name: str) -> str:
    """Convert a channel name into a safe directory name."""
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name)
    safe = re.sub(r"\s+", " ", safe).strip()
    safe = safe.strip(". ")
    if len(safe) > 50:
        safe = safe[:50].strip()
    return safe or "Uncategorized"


def _find_downloaded_file(expected_path: str) -> Optional[str]:
    """Find the actual downloaded file -- yt-dlp may change the extension."""
    if os.path.isfile(expected_path):
        return expected_path
    base = os.path.splitext(expected_path)[0]
    for ext in [".mp4", ".mkv", ".webm", ".m4v"]:
        candidate = base + ext
        if os.path.isfile(candidate):
            return candidate
    return None


def _download_sync(source_url: str, output_path: str) -> tuple:
    """
    Synchronous yt-dlp download. Runs in a thread via asyncio.to_thread().
    Returns: (success: bool, error_message: str or None)
    """
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
        logger.info(f"Download: using cookies from {cookies_path}")
    else:
        logger.warning(f"Download: no cookies.txt at {cookies_path}")

    logger.info(f"Download: starting yt-dlp for {source_url}")
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([source_url])
        logger.info(f"Download: yt-dlp finished OK for {source_url}")
        return True, None
    except Exception as e:
        error_msg = str(e)[:500]
        logger.error(f"Download: yt-dlp FAILED for {source_url}: {error_msg}")
        return False, error_msg


def _build_filename(title: str, artist: Optional[str]) -> str:
    """Build a safe filename from title and artist."""
    if artist and artist.lower() not in title.lower():
        name = f"{artist} - {title}"
    else:
        name = title
    name = re.sub(r'[<>:"/\\|?*]', "", name)
    name = re.sub(r"\s+", " ", name).strip()
    if len(name) > 200:
        name = name[:200]
    return f"{name}.mp4"
