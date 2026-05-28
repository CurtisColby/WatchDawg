"""
Favorite / Download API Router.

Milestone B: Favorite and Download are now separate actions.

Endpoints:
- POST   /favorite/{video_id}          — Bookmark a video as a favorite (no download).
- POST   /favorite/{video_id}/download — Trigger yt-dlp download for a favorited video.
- GET    /favorite                      — List all favorited videos.
- DELETE /favorite/{favorite_id}        — Remove a favorite (optionally delete downloaded file).
- POST   /favorite/{favorite_id}/retry  — Retry a failed/pending download.

Design:
  Favorite = bookmark only. The heart button on the TV/web UI adds the video to
  the favorites list without triggering a download. This lets users build a
  favorites collection without filling up the NAS.

  Download = explicit NAS save. A separate download button triggers yt-dlp.
  Can be called on any favorited video regardless of its current download_status
  (will re-download if already complete, giving a refresh mechanism).

  This split means:
  - Locked state: neither button visible (Milestone E enforces this on Android)
  - Unlocked state: heart + download button both visible, independent actions
  - Adult content: both buttons require PIN (channel lock enforced server-side)
"""

import asyncio
import datetime
import logging
import os
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session, async_session_factory
from app.models import Video, Favorite, Channel
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/favorite", tags=["favorite"])


# ---------------------------------------------------------------------------
# POST /favorite/{video_id} — Bookmark only, no download
# ---------------------------------------------------------------------------

@router.post("/{video_id}")
async def favorite_video(
    video_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Bookmark a video as a favorite.

    Creates a Favorite record with download_status='none'.
    Does NOT trigger a download — use POST /favorite/{video_id}/download for that.
    Idempotent — returns 'already_favorited' if already bookmarked.
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

    logger.info(f"Favorited video {video_id}: {video.title}")
    return {
        "status": "favorited",
        "video_id": video_id,
        "title": video.title,
        "download_status": "none",
    }


# ---------------------------------------------------------------------------
# POST /favorite/{video_id}/download — Trigger yt-dlp download
# ---------------------------------------------------------------------------

@router.post("/{video_id}/download")
async def download_video(
    video_id: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Trigger a yt-dlp download for a video.

    If the video is not yet favorited, favorites it first then starts the download.
    If already favorited, starts or restarts the download regardless of current status.
    This gives a clean "re-download" path if the file was deleted or corrupted.
    """
    stmt = select(Video).where(Video.id == video_id)
    result = await db.execute(stmt)
    video = result.scalar_one_or_none()

    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")

    # Get or create the Favorite record
    fav_result = await db.execute(
        select(Favorite).where(Favorite.video_id == video_id)
    )
    favorite = fav_result.scalar_one_or_none()

    if favorite is None:
        favorite = Favorite(video_id=video_id, download_status="pending")
        db.add(favorite)
    else:
        if favorite.download_status == "downloading":
            return {"status": "already_downloading", "video_id": video_id}
        favorite.download_status = "pending"
        favorite.download_error = None

    await db.commit()

    background_tasks.add_task(
        _download_video_task,
        video_id=video.id,
        source_url=video.source_url,
        title=video.title,
        artist=video.artist,
    )

    logger.info(f"Download queued for video {video_id}: {video.title}")
    return {
        "status": "download_queued",
        "video_id": video_id,
        "title": video.title,
        "download_status": "pending",
    }


# ---------------------------------------------------------------------------
# GET /favorite — List all favorites
# ---------------------------------------------------------------------------

@router.get("")
async def list_favorites(db: AsyncSession = Depends(get_db_session)):
    """
    List all favorited videos with their download status.

    Includes channel_id, channel_name, and source_provider so the web UI
    can build the Favorites sidebar filter grouped by channel.
    Also returns stream_url for completed local downloads so the player
    can stream directly without resolving.
    """
    # Left-join Channel so we get the friendly channel name even when
    # a video's channel has been deleted (channel_id becomes null).
    stmt = (
        select(Favorite, Video, Channel)
        .join(Video, Favorite.video_id == Video.id)
        .outerjoin(Channel, Video.channel_id == Channel.id)
    )
    result = await db.execute(stmt)
    rows = result.all()

    music_dir = settings.music_videos_path

    favorites = []
    for fav, video, channel in rows:
        # Build stream_url for files that are confirmed on disk
        stream_url = None
        if fav.local_file_path and os.path.isfile(fav.local_file_path):
            rel = os.path.relpath(fav.local_file_path, music_dir)
            import urllib.parse
            stream_url = f"/library/stream/{urllib.parse.quote(rel, safe='/')}"

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
            "download_status": fav.download_status,
            "download_error": fav.download_error,
            "local_file_path": fav.local_file_path,
            "stream_url": stream_url,
            "downloaded_at": fav.downloaded_at.isoformat() if fav.downloaded_at else None,
            "created_at": fav.created_at.isoformat() if fav.created_at else None,
        })

    return {"favorites": favorites}


# ---------------------------------------------------------------------------
# DELETE /favorite/{favorite_id} — Remove a favorite
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# POST /favorite/{favorite_id}/retry — Retry a failed download
# ---------------------------------------------------------------------------

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
    )

    logger.info(f"Retrying download for favorite {favorite_id}: {video.title}")
    return {"status": "retrying", "favorite_id": favorite_id, "title": video.title}


# ---------------------------------------------------------------------------
# Background download task
# ---------------------------------------------------------------------------

async def _download_video_task(
    video_id: int,
    source_url: str,
    title: str,
    artist: Optional[str],
):
    """
    Background task that downloads a video using yt-dlp.

    Uses asyncio.to_thread() to run the synchronous yt-dlp call — this is
    the correct approach for FastAPI background tasks (avoids the deprecated
    asyncio.get_event_loop() pattern which can fail in background task context).
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

            # Look up channel name for subfolder
            video_stmt = select(Video).where(Video.id == video_id)
            video_result = await db.execute(video_stmt)
            video_rec = video_result.scalar_one_or_none()

            channel_name = None
            if video_rec and video_rec.channel_id:
                ch_stmt = select(Channel).where(Channel.id == video_rec.channel_id)
                ch_result = await db.execute(ch_stmt)
                ch = ch_result.scalar_one_or_none()
                if ch:
                    channel_name = ch.name

            favorite.download_status = "downloading"
            favorite.download_error = None
            await db.commit()
            logger.info(f"Download task: status -> downloading (video_id={video_id})")

            # Determine output directory: channel subfolder or Uncategorized
            base_dir = settings.music_videos_path
            if channel_name:
                # Sanitize channel name for filesystem use
                safe_channel = re.sub(r'[<>:"/\\|?*]', '', channel_name).strip()
                safe_channel = re.sub(r'\s+', ' ', safe_channel)
                output_dir = os.path.join(base_dir, safe_channel) if safe_channel else base_dir
            else:
                output_dir = os.path.join(base_dir, "Uncategorized")

            logger.info(f"Download task: output_dir={output_dir}")

            if not os.path.exists(output_dir):
                try:
                    os.makedirs(output_dir, exist_ok=True)
                    logger.info(f"Download task: created directory {output_dir}")
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
            logger.info(f"Download task: target={output_path}")

            # Duplicate file check — if the file already exists on disk, skip yt-dlp
            existing_path = _find_downloaded_file(output_path)
            if existing_path:
                logger.info(
                    f"Download task: file already exists at {existing_path} — "
                    "marking complete without re-downloading"
                )
                favorite.download_status = "complete"
                favorite.local_file_path = existing_path
                favorite.downloaded_at = datetime.datetime.utcnow()
                favorite.download_error = None
                await db.commit()
                return

            # Use asyncio.to_thread() — correct for FastAPI background tasks
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
    """Find the actual downloaded file — yt-dlp may change the extension."""
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
