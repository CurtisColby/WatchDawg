"""
WatchDawg Library Router.

Serves the local NAS /watchdawg directory as a browsable media library.
The downloads root contains two subfolders:
  Public/  — downloads from unlocked channels
  Private/ — downloads from locked channels

PIN-aware content filtering (Session 22 content model):
  Locked (no token):  scan Public/ only  — private content invisible
  Unlocked (token):   scan full root     — Public/ + Private/ both visible

All files here are streamable directly — no yt-dlp resolution needed.

Endpoints:
- GET    /library                           — List all video files (recursive).
- GET    /library/stream/{filename:path}    — Stream a local file (range-capable).
- GET    /library/thumb/{filename:path}     — Serve a generated thumbnail jpg.
- POST   /library/generate-thumbnails       — ffmpeg frame-grab for unmatched files.
- DELETE /library/file                      — Delete file + add to skip list.
"""

import asyncio
import logging
import os
import subprocess
import urllib.parse
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Header
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.models import Favorite, Video, SkipListEntry
from app.config import settings
from app.encryption import encrypt_value
from app.hashing import hmac_hash
from app.routers.auth import is_unlocked

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/library", tags=["library"])

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".webm", ".m4v", ".avi", ".mov"}
THUMB_SUFFIX = ".watchdawg_thumb.jpg"
FFMPEG_GRAB_SECOND = 5


def _human_size(size_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def _safe_real_path(relative_path: str, downloads_dir: str) -> str:
    """Resolve relative path safely — raises 400 on path traversal attempt."""
    joined = os.path.join(downloads_dir, relative_path)
    real_path = os.path.realpath(joined)
    real_dir = os.path.realpath(downloads_dir)
    if not real_path.startswith(real_dir + os.sep):
        raise HTTPException(status_code=400, detail="Invalid file path")
    return real_path


def _thumb_path_for(video_path: str) -> str:
    return video_path + THUMB_SUFFIX


def _generate_thumb_sync(video_path: str, thumb_path: str) -> bool:
    cmd = [
        "ffmpeg",
        "-ss", str(FFMPEG_GRAB_SECOND),
        "-i", video_path,
        "-frames:v", "1",
        "-q:v", "4",
        "-vf", "scale=480:-1",
        "-y",
        thumb_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        if result.returncode == 0 and os.path.isfile(thumb_path):
            return True
        logger.warning(
            f"ffmpeg failed for {video_path}: "
            f"{result.stderr.decode(errors='replace')[:200]}"
        )
        return False
    except subprocess.TimeoutExpired:
        logger.warning(f"ffmpeg timed out for {video_path}")
        return False
    except Exception as e:
        logger.warning(f"ffmpeg error for {video_path}: {e}")
        return False


@router.get("")
async def list_library(
    genre: Optional[str] = Query(None, description="Filter by genre tag (case-insensitive, partial match)"),
    x_watchdawg_token: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Recursively scan the downloads directory and return all video files.

    PIN-aware scan root (Session 22 content model):
      Locked  → scan Public/ only  (private content physically invisible)
      Unlocked → scan full root    (Public/ + Private/ both returned)

    The Library tab is always visible in the nav rail. The content changes
    based on lock state — no empty-list response, no locked_hidden flag needed.
    A locked user sees their public downloads. An unlocked user sees everything.

    stream_url paths are relative to the downloads root so they work correctly
    regardless of which subfolder the file lives in.
    """
    unlocked = is_unlocked(x_watchdawg_token)

    # Determine scan root based on lock state.
    # Locked: Public/ only. Unlocked: full downloads root (Public/ + Private/).
    downloads_dir = settings.downloads_path
    scan_dir = downloads_dir if unlocked else settings.public_downloads_path

    if not os.path.isdir(scan_dir):
        return {
            "total": 0,
            "files": [],
            "directory": scan_dir,
            "locked_hidden": False,
        }

    # Build DB enrichment lookup keyed by full local_file_path.
    # Previously keyed by basename which caused collisions when two channels
    # had files with the same name (e.g. Public/ChanA/Song.mp4 and
    # Private/ChanB/Song.mp4) — only the last entry won and the other
    # appeared as a blank unenrichable card.
    stmt = (
        select(Favorite, Video)
        .join(Video, Favorite.video_id == Video.id)
        .where(Favorite.local_file_path.isnot(None))
    )
    result = await db.execute(stmt)
    rows = result.all()

    # Key by full absolute path for exact matching
    known_files: dict[str, tuple] = {}
    for fav, video in rows:
        if fav.local_file_path:
            known_files[fav.local_file_path] = (fav, video)

    # Session 42: build channel genre_tags lookup so Local tab can filter by pill.
    # Keys: channel_id → genre_tags string
    from app.models import Channel as ChannelModel
    from sqlalchemy import select as sa_select
    ch_result = await db.execute(sa_select(ChannelModel.id, ChannelModel.genre_tags, ChannelModel.name))
    channel_info: dict[int, dict] = {
        row[0]: {"genre_tags": row[1] or "", "name": row[2]}
        for row in ch_result.fetchall()
    }

    files = []

    for dirpath, _dirnames, filenames in os.walk(scan_dir):
        for filename in sorted(filenames):
            if filename.endswith(THUMB_SUFFIX):
                continue
            _, ext = os.path.splitext(filename)
            if ext.lower() not in VIDEO_EXTENSIONS:
                continue

            full_path = os.path.join(dirpath, filename)
            # stream_url and relative_path are always relative to the full
            # downloads root so the stream endpoint resolves correctly whether
            # the file is in Public/ or Private/.
            relative_path = os.path.relpath(full_path, downloads_dir)

            try:
                stat = os.stat(full_path)
            except OSError:
                continue

            # subfolder: first path component under downloads_dir.
            # Public/ChannelName/file.mp4  → subfolder = "Public"
            # Private/ChannelName/file.mp4 → subfolder = "Private"
            path_parts = relative_path.split(os.sep)
            subfolder = path_parts[0] if len(path_parts) > 1 else ""

            # Session 62: locally-generated sidecar thumbnails are preferred
            # for ALL files — matched and unmatched. Previously the sidecar
            # check only ran for files WITHOUT a DB record, so Reddit
            # auto-downloads (which always have one) could never display a
            # generated thumbnail: they were stuck with whatever Reddit
            # provided at scrape time — often nothing. Disk sidecar first
            # (local, always loads), DB-provided URL as the fallback.
            thumb_path = _thumb_path_for(full_path)
            if os.path.isfile(thumb_path):
                thumb_rel = os.path.relpath(thumb_path, downloads_dir)
                sidecar_url = f"/library/thumb/{urllib.parse.quote(thumb_rel, safe='/')}"
            else:
                sidecar_url = None

            match = known_files.get(full_path)
            if match:
                fav, video = match
                title = video.title
                artist = video.artist
                thumbnail_url = sidecar_url or video.thumbnail_url
                favorite_id = fav.id
                video_id = video.id
                # Session 42: include channel genre_tags for pill filtering
                ch = channel_info.get(video.channel_id) if video.channel_id else None
                genre_tags = ch["genre_tags"] if ch else ""
                channel_name = ch["name"] if ch else ""
            else:
                title = os.path.splitext(filename)[0]
                artist = None
                favorite_id = None
                video_id = None
                genre_tags = ""
                channel_name = ""
                thumbnail_url = sidecar_url

            # Session 42: apply genre filter if provided
            if genre and genre_tags:
                if genre.lower() not in genre_tags.lower():
                    continue
            elif genre and not genre_tags:
                continue  # no tags → doesn't match any genre filter

            files.append({
                "filename": filename,
                "relative_path": relative_path,
                "subfolder": subfolder,
                "title": title,
                "artist": artist,
                "thumbnail_url": thumbnail_url,
                "size_bytes": stat.st_size,
                "size_human": _human_size(stat.st_size),
                "modified_at": stat.st_mtime,
                "stream_url": f"/library/stream/{urllib.parse.quote(relative_path, safe='/')}",
                "favorite_id": favorite_id,
                "video_id": video_id,
                "genre_tags": genre_tags,
                "channel_name": channel_name,
            })

    files.sort(key=lambda f: f["modified_at"], reverse=True)

    logger.info(
        f"Library scan: {len(files)} video files in {scan_dir} "
        f"(unlocked={unlocked}, genre={genre or 'all'})"
    )
    return {
        "total": len(files),
        "directory": scan_dir,
        "files": files,
        "locked_hidden": False,
    }


@router.get("/genres")
async def list_library_genres(
    x_watchdawg_token: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Session 42 — Return distinct genre tags available in the local library.

    Scans all videos in the library, joins to their channels, and returns
    a deduplicated sorted list of genre tags for pill filter UI.

    PIN-aware: locked channel genres only returned when unlocked.
    """
    unlocked = is_unlocked(x_watchdawg_token)

    from app.models import Channel as ChannelModel
    from sqlalchemy import select as sa_select

    stmt = (
        sa_select(Favorite, Video)
        .join(Video, Favorite.video_id == Video.id)
        .where(Favorite.local_file_path.isnot(None))
    )
    result = await db.execute(stmt)
    rows = result.all()

    # Collect channel IDs from known files
    channel_ids = set()
    for fav, video in rows:
        if video.channel_id:
            channel_ids.add(video.channel_id)

    if not channel_ids:
        return {"genres": [], "total": 0}

    ch_stmt = sa_select(ChannelModel.genre_tags, ChannelModel.locked).where(
        ChannelModel.id.in_(list(channel_ids))
    )
    ch_result = await db.execute(ch_stmt)

    tags = set()
    for genre_tags, locked in ch_result.fetchall():
        if locked and not unlocked:
            continue
        if genre_tags:
            for tag in genre_tags.split(","):
                tag = tag.strip()
                if tag:
                    tags.add(tag)

    sorted_tags = sorted(tags)
    return {"genres": sorted_tags, "total": len(sorted_tags)}


@router.post("/generate-thumbnails")
async def generate_thumbnails(
    limit: int = Query(20, ge=1, le=200, description="Max files to process per run"),
    db: AsyncSession = Depends(get_db_session),
):
    """Generate thumbnail images for library files that have no thumbnail.

    Two passes, sharing one per-run limit:

    Pass 1 (DB-driven, unchanged): Video records with
    source_provider='local_folder' and no thumbnail_url — runs ffmpeg
    frame-grab and writes the /library/thumb/... URL back to the record so
    the Catalog page can display it immediately.

    Pass 2 (Session 62, filesystem walk): any video file in the download
    folders that lacks a sidecar thumbnail — Reddit auto-downloads, bulk
    channel downloads, Save-button downloads. No DB write-back needed: the
    Files on Disk listing prefers sidecar thumbnails for every file
    (Session 62 listing fix), so generated thumbnails appear on the next
    page load.
    """
    from sqlalchemy import or_

    downloads_dir = settings.downloads_path

    # Find local_folder videos missing thumbnails — DB is the source of truth.
    stmt = (
        select(Video)
        .where(
            Video.source_provider == "local_folder",
            or_(Video.thumbnail_url.is_(None), Video.thumbnail_url == ""),
        )
        .order_by(Video.id.asc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    videos = result.scalars().all()

    summary = {"total": len(videos), "generated": 0, "failed": 0, "skipped": 0}

    for video in videos:
        video_path = video.source_url  # absolute path e.g. /watchdawg/Private/Folder/file.mp4
        if not video_path or not os.path.isfile(video_path):
            summary["skipped"] += 1
            logger.warning(f"Local thumb: file not found for video {video.id}: {video_path}")
            continue

        thumb_path = _thumb_path_for(video_path)

        # If sidecar already exists on disk, just update the DB record.
        if os.path.isfile(thumb_path):
            thumb_rel = os.path.relpath(thumb_path, downloads_dir)
            video.thumbnail_url = f"/library/thumb/{urllib.parse.quote(thumb_rel, safe='/')}"
            summary["generated"] += 1
            continue

        success = await asyncio.to_thread(_generate_thumb_sync, video_path, thumb_path)
        if success:
            thumb_rel = os.path.relpath(thumb_path, downloads_dir)
            video.thumbnail_url = f"/library/thumb/{urllib.parse.quote(thumb_rel, safe='/')}"
            summary["generated"] += 1
            logger.info(f"Generated thumbnail: video {video.id} -> {thumb_path}")
        else:
            summary["failed"] += 1
        await asyncio.sleep(0.1)

    await db.commit()

    # ------------------------------------------------------------------
    # Pass 2 (Session 62): walk the download folders and generate sidecar
    # thumbnails for ANY video file that lacks one. This is what covers
    # Reddit auto-downloads and bulk channel downloads — their DB records
    # are keyed by post/source, not file path, so the DB-driven pass above
    # never sees them. Shares the same per-run limit budget with pass 1.
    # ------------------------------------------------------------------
    remaining = limit - summary["total"]
    walk_candidates = []
    if remaining > 0 and os.path.isdir(downloads_dir):
        for dirpath, _dirnames, filenames in os.walk(downloads_dir):
            for filename in sorted(filenames):
                if filename.endswith(THUMB_SUFFIX):
                    continue
                _, ext = os.path.splitext(filename)
                if ext.lower() not in VIDEO_EXTENSIONS:
                    continue
                full_path = os.path.join(dirpath, filename)
                walk_thumb = _thumb_path_for(full_path)
                if not os.path.isfile(walk_thumb):
                    walk_candidates.append((full_path, walk_thumb))
                if len(walk_candidates) >= remaining:
                    break
            if len(walk_candidates) >= remaining:
                break

    summary["total"] += len(walk_candidates)
    for video_path, thumb_path in walk_candidates:
        success = await asyncio.to_thread(_generate_thumb_sync, video_path, thumb_path)
        if success:
            summary["generated"] += 1
            logger.info(f"Generated sidecar thumbnail: {thumb_path}")
        else:
            summary["failed"] += 1
        await asyncio.sleep(0.1)

    logger.info(
        f"Thumbnail generation complete: {summary['generated']} generated, "
        f"{summary['failed']} failed, {summary['skipped']} skipped "
        f"out of {summary['total']} "
        f"(pass 2 folder walk: {len(walk_candidates)} candidates)"
    )
    return {"status": "complete", "summary": summary}


@router.post("/purge-missing-files")
async def purge_missing_files(
    db: AsyncSession = Depends(get_db_session),
):
    """Delete local_folder video records whose files no longer exist on disk.

    Scans all local_folder videos, checks if the source_url path exists,
    and deletes the DB record (plus any sidecar thumbnail) for missing files.
    Prevents dead records from clogging the thumbnail queue and catalog.
    """
    stmt = select(Video).where(Video.source_provider == "local_folder")
    result = await db.execute(stmt)
    local_videos = result.scalars().all()

    summary = {"checked": len(local_videos), "deleted": 0, "kept": 0}

    for video in local_videos:
        if video.source_url and os.path.isfile(video.source_url):
            summary["kept"] += 1
            continue

        # File is gone — clean up sidecar thumbnail if it exists
        if video.source_url:
            thumb_path = _thumb_path_for(video.source_url)
            if os.path.isfile(thumb_path):
                try:
                    os.remove(thumb_path)
                except OSError:
                    pass

        logger.info(f"Purging missing local file: video {video.id} ({video.source_url})")
        await db.delete(video)
        summary["deleted"] += 1

    await db.commit()

    logger.info(
        f"Purge missing files complete: {summary['deleted']} deleted, "
        f"{summary['kept']} kept out of {summary['checked']} checked"
    )
    return {
        "status": "complete",
        "summary": summary,
        "message": f"Removed {summary['deleted']} local videos whose files no longer exist on disk.",
    }


@router.get("/thumb/{filename:path}")
async def serve_thumbnail(filename: str):
    """Serve a generated sidecar thumbnail jpg."""
    downloads_dir = settings.downloads_path
    real_path = _safe_real_path(filename, downloads_dir)
    if not os.path.isfile(real_path):
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    return FileResponse(path=real_path, media_type="image/jpeg")


@router.delete("/file")
async def delete_library_file(
    relative_path: str = Query(..., description="Relative path within the downloads root"),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Delete a video file from the NAS library directory.

    Full delete flow:
    1. Validates path stays within downloads_path (no path traversal).
    2. Looks up the associated Video record via the Favorite link.
    3. Adds the video's source_post_id to the skip list.
    4. Deletes the Favorite and Video DB records.
    5. Deletes the actual file and any sidecar thumbnail from disk.
    """
    downloads_dir = settings.downloads_path
    real_path = _safe_real_path(relative_path, downloads_dir)

    if not os.path.isfile(real_path):
        raise HTTPException(status_code=404, detail=f"File not found: {relative_path}")

    filename = os.path.basename(real_path)
    skip_added = False
    favorite_cleaned = False
    video_deleted = False

    fav_stmt = select(Favorite).where(Favorite.local_file_path == real_path)
    fav_result = await db.execute(fav_stmt)
    favorite = fav_result.scalar_one_or_none()

    if favorite is None:
        all_favs_result = await db.execute(
            select(Favorite).where(Favorite.local_file_path.isnot(None))
        )
        for fav in all_favs_result.scalars().all():
            if os.path.basename(fav.local_file_path or "") == filename:
                favorite = fav
                break

    if favorite is not None:
        video_stmt = select(Video).where(Video.id == favorite.video_id)
        video_result = await db.execute(video_stmt)
        video = video_result.scalar_one_or_none()

        if video is not None:
            post_hash = hmac_hash(video.source_post_id)
            existing_skip = await db.execute(
                select(SkipListEntry).where(
                    SkipListEntry.source_post_id_hash == post_hash
                )
            )
            if existing_skip.scalar_one_or_none() is None:
                db.add(SkipListEntry(
                    source_post_id_encrypted=encrypt_value(video.source_post_id),
                    source_post_id_hash=post_hash,
                    source_provider=video.source_provider,
                ))
                skip_added = True
                logger.info(f"Added {video.source_post_id} to skip list")

            await db.delete(favorite)
            await db.delete(video)
            video_deleted = True
            favorite_cleaned = True
        else:
            await db.delete(favorite)
            favorite_cleaned = True

        await db.commit()

    try:
        os.remove(real_path)
        logger.info(f"Deleted library file: {real_path}")
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete file: {e}")

    thumb_path = _thumb_path_for(real_path)
    if os.path.isfile(thumb_path):
        try:
            os.remove(thumb_path)
            logger.info(f"Deleted sidecar thumbnail: {thumb_path}")
        except OSError:
            pass

    return {
        "status": "deleted",
        "filename": filename,
        "relative_path": relative_path,
        "skip_added": skip_added,
        "favorite_cleaned": favorite_cleaned,
        "video_deleted": video_deleted,
    }


@router.get("/stream/{filename:path}")
async def stream_file(filename: str):
    """Stream a local video file with range request support."""
    downloads_dir = settings.downloads_path
    real_path = _safe_real_path(filename, downloads_dir)

    if not os.path.isfile(real_path):
        raise HTTPException(status_code=404, detail=f"File not found: {filename}")

    _, ext = os.path.splitext(filename)
    media_type_map = {
        ".mp4": "video/mp4",
        ".mkv": "video/x-matroska",
        ".webm": "video/webm",
        ".m4v": "video/mp4",
        ".avi": "video/x-msvideo",
        ".mov": "video/quicktime",
    }
    media_type = media_type_map.get(ext.lower(), "video/mp4")

    logger.info(f"Streaming local file: {real_path}")
    return FileResponse(
        path=real_path,
        media_type=media_type,
        filename=os.path.basename(filename),
    )
