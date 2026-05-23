"""
WatchDawg Resolution Engine — yt-dlp Wrapper.

Converts source URLs (YouTube, Vimeo, Reddit, etc.) into direct
streamable URLs that the client can play via ExoPlayer or a browser
video element.

Key behaviors:
- Caching: Direct MP4 URLs are cached for 3 hours. HLS (m3u8) and DASH
  (mpd / http_dash_segments) URLs are cached for only 20 minutes because
  their signed CDN tokens expire in ~15-30 minutes on Vimeo.
- Format selection: Prefers direct HTTP MP4 up to 1080p. Explicitly blocks
  both HLS (m3u8) and DASH (http_dash_segments) protocols. Falls back to HLS
  only as a last resort — never to DASH, since browsers cannot play .mpd.
- Error handling: Dead links, geo-blocks, DMCA takedowns, and private videos
  are caught, flagged as permanent failures, and auto-deleted from the feed.
- Auto-dedup: After successful resolution, the CDN fingerprint is checked
  against all other resolved Vimeo videos. If a duplicate physical file is
  found, the lower-scored copy is deleted and playback is transparently
  redirected to the keeper — the user never sees an error.
- Hard timeout: Each yt-dlp call is capped at YTDLP_TIMEOUT_SECONDS (90s)
  using a ProcessPoolExecutor. If yt-dlp hangs (e.g. on a stalled YouTube
  connection), the process is killed and the video is marked failed rather
  than blocking the entire batch indefinitely.
"""

import asyncio
import concurrent.futures
import datetime
import logging
import os
import re
from typing import Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Video, Favorite

logger = logging.getLogger(__name__)

# Silence chatty third-party loggers
for _noisy in (
    "aiosqlite", "httpcore", "httpx",
    "sqlalchemy.engine", "sqlalchemy.pool", "apscheduler",
):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

# TTL for direct MP4 URLs — Vimeo/YouTube tokens last ~4-6h; we use 3h.
RESOLUTION_TTL_HOURS = 3

# Short TTL for HLS/DASH adaptive URLs — Vimeo signed tokens expire ~15-30min.
ADAPTIVE_TTL_MINUTES = 20

# Hard wall-clock timeout for a single yt-dlp extraction call.
# If yt-dlp hasn't returned within this many seconds, the subprocess is killed
# and the video is marked failed (transient). This prevents one hung YouTube
# video from blocking the entire batch queue for minutes.
YTDLP_TIMEOUT_SECONDS = 90

# Reusable process pool for yt-dlp calls — one worker per call, capped at 4
# concurrent extractions to avoid hammering platforms.
_process_pool = concurrent.futures.ProcessPoolExecutor(max_workers=4)

FORMAT_SELECTOR = (
    "best[height<=1080][ext=mp4][vcodec!*=none][acodec!*=none][protocol^=http]/"
    "best[ext=mp4][vcodec!*=none][acodec!*=none][protocol^=http]/"
    "best[vcodec!*=none][acodec!*=none][protocol^=http]/"
    "best[vcodec!*=none][acodec!*=none][protocol!=http_dash_segments]/"
    "best[vcodec!*=none][acodec!*=none]/"
    "best[protocol!=http_dash_segments]/"
    "best"
)

PERMANENT_ERROR_KEYWORDS = [
    "http error 404",
    "not found",
    "video unavailable",
    "private video",
    "removed by the uploader",
    "account terminated",
    "copyright claim",
    "violates youtube",
    "content warning",
    "sign in to confirm your age",
    "join this channel to get access",
    "members-only content",
    "this video has been removed",
    "video is no longer available",
    "this video does not exist",
    "page not found",
]

# Vimeo CDN domains — fingerprinting ONLY fires for URLs from these domains.
# This prevents the broad /video/{hash}/ pattern from false-matching YouTube
# CDN paths and incorrectly deleting unrelated videos as duplicates.
VIMEO_CDN_DOMAINS = (
    "vimeocdn.com",
    "vimeo.com",
    "vod-progressive-ak.vimeocdn.com",
    "vod-adaptive-ak.vimeocdn.com",
    "skyfire.vimeo.com",
    "clips.vimeo.com",
)


def _is_hls_url(url: str) -> bool:
    if not url:
        return False
    path = url.split("?")[0].lower()
    return path.endswith(".m3u8") or "m3u8" in path


def _is_dash_url(url: str) -> bool:
    if not url:
        return False
    path = url.split("?")[0].lower()
    return path.endswith(".mpd") or "playlist.mpd" in path or "/playlist/av/primary" in url.lower()


def _is_adaptive_url(url: str) -> bool:
    return _is_hls_url(url) or _is_dash_url(url)


def _is_vimeo_cdn_url(url: str) -> bool:
    """Return True only for known Vimeo CDN domains."""
    if not url:
        return False
    url_lower = url.lower()
    return any(domain in url_lower for domain in VIMEO_CDN_DOMAINS)


def extract_cdn_fingerprint(url: str) -> Optional[str]:
    """
    Extract a stable CDN file fingerprint from a resolved Vimeo stream URL.

    Domain-gated: only fires for Vimeo CDN URLs. Non-Vimeo URLs (YouTube,
    Reddit, etc.) always return None and are never deduped against each other.

    Patterns (priority order):
      /sep/video/{hash}/           — standard Vimeo progressive CDN
      /video/{hash}/               — alternate Vimeo CDN layout (Vimeo-only, domain-gated)
      id=o-{fingerprint}           — signed CDN token query param
      vimeo-transcode-storage-*/…/{file_id}.mp4  — vod-progressive-ak CDN
    """
    if not url:
        return None

    if not _is_vimeo_cdn_url(url):
        return None

    m = re.search(r'/sep/video/([A-Za-z0-9_-]+)/', url)
    if m:
        return m.group(1)

    m = re.search(r'/video/([A-Za-z0-9_-]{20,})/', url)
    if m:
        return m.group(1)

    m = re.search(r'[?&]id=(o-[A-Za-z0-9_-]+)', url)
    if m:
        return m.group(1)

    m = re.search(r'vimeo-transcode-storage-[^/]+/(?:[^/]+/){3,}(\d+)\.mp4', url)
    if m:
        return f"vts_{m.group(1)}"

    return None


# ---------------------------------------------------------------------------
# Module-level extraction function — must be at top level so ProcessPoolExecutor
# can pickle it across process boundaries.
# ---------------------------------------------------------------------------

def _extract_sync_worker(url: str, cookies_path: Optional[str]) -> Tuple[Optional[dict], Optional[str], bool]:
    """
    Synchronous yt-dlp extraction. Runs in a subprocess via ProcessPoolExecutor.

    Returns (stream_info_dict | None, error_msg | None, is_permanent: bool).
    Using a dict instead of StreamInfo so it survives pickling across processes.
    """
    import yt_dlp

    ydl_opts = {
        "format": FORMAT_SELECTOR,
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
        "skip_download": True,
        "simulate": True,
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,
    }

    if cookies_path and os.path.isfile(cookies_path):
        ydl_opts["cookiefile"] = cookies_path

    permanent_keywords = [
        "http error 404", "not found", "video unavailable", "private video",
        "removed by the uploader", "account terminated", "copyright claim",
        "violates youtube", "content warning", "sign in to confirm your age",
        "join this channel to get access", "members-only content",
        "this video has been removed", "video is no longer available",
        "this video does not exist", "page not found",
    ]

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        if info is None:
            return None, "yt-dlp returned no info", False

        stream_url = info.get("url")
        if not stream_url:
            formats = info.get("requested_formats", [])
            if formats:
                stream_url = formats[0].get("url")
        if not stream_url:
            all_formats = info.get("formats", [])
            if all_formats:
                stream_url = all_formats[-1].get("url")
        if not stream_url:
            return None, "No stream URL found in yt-dlp output", False

        ext = info.get("ext", "unknown")
        height = info.get("height") or (
            info.get("requested_formats", [{}])[0].get("height")
            if info.get("requested_formats") else None
        )
        format_note = f"{ext}/{height}p" if height else ext

        return {
            "stream_url": stream_url,
            "format_note": format_note,
            "width": info.get("width"),
            "height": height,
            "duration": info.get("duration"),
            "thumbnail": info.get("thumbnail"),
            "title": info.get("title"),
            "uploader": info.get("uploader"),
            "protocol": info.get("protocol", "unknown"),
            "ext": ext,
        }, None, False

    except Exception as e:
        error_msg = str(e)
        error_lower = error_msg.lower()
        for keyword in permanent_keywords:
            if keyword in error_lower:
                return None, f"Permanent: {error_msg[:300]}", True
        return None, f"Transient: {error_msg[:300]}", False


def _fetch_thumbnail_sync_worker(url: str, cookies_path: Optional[str]) -> Optional[str]:
    """Module-level thumbnail fetch — picklable for ProcessPoolExecutor."""
    import yt_dlp

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "simulate": True,
        "extract_flat": False,
        "socket_timeout": 20,
        "retries": 2,
    }
    if cookies_path and os.path.isfile(cookies_path):
        ydl_opts["cookiefile"] = cookies_path

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        if info is None:
            return None
        thumbnails = info.get("thumbnails") or []
        if thumbnails:
            best = thumbnails[-1].get("url")
            if best:
                return best
        return info.get("thumbnail")
    except Exception:
        return None


class StreamInfo:
    """Result of a successful resolution."""

    def __init__(self, stream_url, format_note="", width=None, height=None,
                 duration=None, thumbnail=None, title=None, uploader=None):
        self.stream_url = stream_url
        self.format_note = format_note
        self.width = width
        self.height = height
        self.duration = duration
        self.thumbnail = thumbnail
        self.title = title
        self.uploader = uploader

    @classmethod
    def from_dict(cls, d: dict) -> "StreamInfo":
        return cls(
            stream_url=d["stream_url"],
            format_note=d.get("format_note", ""),
            width=d.get("width"),
            height=d.get("height"),
            duration=d.get("duration"),
            thumbnail=d.get("thumbnail"),
            title=d.get("title"),
            uploader=d.get("uploader"),
        )


class ResolverService:
    """Resolves source URLs into direct playable stream URLs using yt-dlp."""

    def __init__(self, db_session: AsyncSession):
        self._db = db_session
        self._cookies_path = settings.ytdlp_cookies_path

    async def resolve_video(self, video_id: int, force: bool = False) -> Optional[dict]:
        """
        Resolve a video by its database ID.

        Cache check → yt-dlp extraction (with hard timeout) → dedup → result.

        If this video resolves but is a lower-scored CDN duplicate, playback is
        transparently redirected to the keeper's stream URL instead of failing.
        """
        stmt = select(Video).where(Video.id == video_id)
        result = await self._db.execute(stmt)
        video = result.scalar_one_or_none()

        if video is None:
            logger.warning(f"Video ID {video_id} not found in database")
            return None

        self._db.expire(video)
        await self._db.refresh(video)

        # Return cached resolution if still valid
        if not force and self._is_cache_valid(video):
            url_type = "HLS" if _is_hls_url(video.resolved_stream_url) else \
                       "DASH" if _is_dash_url(video.resolved_stream_url) else "MP4"
            logger.info(f"Cache hit for video {video_id} [{url_type}]: {video.title[:60]}")
            return self._build_result(video)

        # Skip permanently failed unless forced
        if not force and video.resolution_status == "failed":
            logger.debug(f"Skipping permanently failed video {video_id}: {video.title}")
            return None

        # Resolve with yt-dlp (hard timeout via ProcessPoolExecutor)
        logger.info(f"Re-resolving video {video_id} (source: {video.source_url[:80]})")
        stream_info, error_msg, is_permanent = await self._extract_with_ytdlp(video.source_url)

        if stream_info is not None:
            url_type = "HLS" if _is_hls_url(stream_info.stream_url) else \
                       "DASH" if _is_dash_url(stream_info.stream_url) else "MP4"
            logger.info(
                f"Resolved video {video_id} [{url_type}]: {stream_info.format_note} "
                f"| url_preview={stream_info.stream_url[:80]}"
            )

            video.resolved_stream_url = stream_info.stream_url
            video.resolved_format = stream_info.format_note
            video.resolved_at = datetime.datetime.utcnow()
            video.resolution_status = "resolved"
            video.resolution_error = None

            if stream_info.thumbnail and not video.thumbnail_url:
                video.thumbnail_url = stream_info.thumbnail
            if stream_info.duration and not video.duration_seconds:
                video.duration_seconds = stream_info.duration
            if stream_info.uploader and not video.artist:
                video.artist = stream_info.uploader

            await self._db.commit()

            # Auto-dedup: if this is the lower-scored duplicate, redirect to keeper
            try:
                keeper_result = await self.dedup_after_resolve(video)
                if keeper_result is not None:
                    logger.info(
                        f"Video {video_id} was lower-scored CDN duplicate — "
                        "deleted. Redirecting playback to keeper."
                    )
                    return keeper_result
            except Exception as exc:
                logger.warning(f"Auto-dedup check failed for video {video_id}: {exc}")

            return self._build_result(video)

        elif is_permanent:
            logger.warning(
                f"Permanently dead video {video_id} '{video.title[:60]}' — "
                f"auto-deleting. Reason: {error_msg}"
            )
            await self._delete_video(video)
            await self._db.commit()
            return None

        else:
            video.resolution_status = "failed"
            video.resolved_at = datetime.datetime.utcnow()
            video.resolution_error = error_msg or "Unknown error"
            await self._db.commit()
            logger.warning(f"Failed to resolve video {video_id}: {error_msg}")
            return None

    async def dedup_after_resolve(self, video: Video) -> Optional[dict]:
        """
        Check for CDN fingerprint duplicates after a fresh resolve.

        Only fires for Vimeo CDN URLs (domain-gated in extract_cdn_fingerprint).

        Returns None if no duplicate or this video is the keeper.
        Returns keeper's result dict if this video was deleted as a duplicate
        — caller returns this so playback redirects transparently.
        """
        stream_url = video.resolved_stream_url
        if not stream_url:
            return None

        fingerprint = extract_cdn_fingerprint(stream_url)
        if fingerprint is None:
            return None

        stmt = select(Video).where(
            Video.resolution_status == "resolved",
            Video.resolved_stream_url.isnot(None),
            Video.id != video.id,
        )
        result = await self._db.execute(stmt)
        candidates = result.scalars().all()

        matches = [
            v for v in candidates
            if extract_cdn_fingerprint(v.resolved_stream_url or "") == fingerprint
        ]

        if not matches:
            return None

        group = [video] + matches
        group.sort(key=lambda v: (-(v.reddit_score or 0), v.created_at or ""))
        keeper = group[0]
        dupes  = group[1:]

        logger.info(
            f"Auto-dedup: fingerprint {fingerprint[:16]}… matched {len(matches)} video(s). "
            f"Keeping video {keeper.id} (score={keeper.reddit_score}, title={keeper.title[:40]})"
        )

        this_video_deleted = False
        for dupe in dupes:
            logger.info(
                f"  Auto-dedup: deleting video {dupe.id} "
                f"(score={dupe.reddit_score}, title={dupe.title[:50]})"
            )
            if dupe.id == video.id:
                this_video_deleted = True
            await self._delete_video(dupe)

        await self._db.commit()

        if this_video_deleted:
            await self._db.refresh(keeper)
            logger.info(
                f"  Auto-dedup redirect: serving keeper video {keeper.id} "
                f"(title={keeper.title[:50]}) for deleted video {video.id}"
            )
            return self._build_result(keeper)

        return None

    async def purge_duplicate_cdn_files(self) -> dict:
        """Full CDN fingerprint dedup sweep across all resolved videos."""
        stmt = select(Video).where(
            Video.resolution_status == "resolved",
            Video.resolved_stream_url.isnot(None),
        )
        result = await self._db.execute(stmt)
        resolved_videos = result.scalars().all()

        groups: dict[str, list] = {}
        no_fingerprint = 0

        for v in resolved_videos:
            fp = extract_cdn_fingerprint(v.resolved_stream_url)
            if fp is None:
                no_fingerprint += 1
                continue
            groups.setdefault(fp, []).append(v)

        duplicate_groups = {fp: vids for fp, vids in groups.items() if len(vids) > 1}
        deleted_count = 0
        kept_count = 0

        for fp, vids in duplicate_groups.items():
            vids.sort(key=lambda v: (-(v.reddit_score or 0), v.created_at or ""))
            keeper = vids[0]
            dupes = vids[1:]
            logger.info(
                f"CDN fingerprint {fp[:16]}…: keeping video {keeper.id} "
                f"(score={keeper.reddit_score}, title={keeper.title[:40]}), "
                f"deleting {len(dupes)} duplicate(s)"
            )
            for dupe in dupes:
                await self._delete_video(dupe)
                deleted_count += 1
            kept_count += 1

        await self._db.commit()
        return {
            "duplicate_groups_found": len(duplicate_groups),
            "deleted_count": deleted_count,
            "kept_count": kept_count,
            "no_fingerprint_count": no_fingerprint,
        }

    def _build_result(self, video: Video) -> dict:
        return {
            "id": video.id,
            "title": video.title,
            "artist": video.artist,
            "stream_url": video.resolved_stream_url,
            "format": video.resolved_format,
            "resolved_at": video.resolved_at.isoformat() if video.resolved_at else None,
            "source_url": video.source_url,
            "thumbnail_url": video.thumbnail_url,
        }

    async def _delete_video(self, video: Video) -> None:
        fav_stmt = select(Favorite).where(Favorite.video_id == video.id)
        fav_result = await self._db.execute(fav_stmt)
        fav = fav_result.scalar_one_or_none()
        if fav:
            await self._db.delete(fav)
        await self._db.delete(video)

    async def resolve_batch(self, limit: int = 10) -> dict:
        """Resolve a batch of pending videos."""
        stmt = (
            select(Video)
            .where(Video.resolution_status == "pending")
            .order_by(Video.reddit_score.desc().nullslast())
            .limit(limit)
        )
        result = await self._db.execute(stmt)
        videos = result.scalars().all()

        expiry_cutoff = datetime.datetime.utcnow() - datetime.timedelta(hours=RESOLUTION_TTL_HOURS)
        expired_stmt = (
            select(Video)
            .where(
                Video.resolution_status == "resolved",
                Video.resolved_at < expiry_cutoff,
            )
            .order_by(Video.reddit_score.desc().nullslast())
            .limit(max(0, limit - len(videos)))
        )
        expired_result = await self._db.execute(expired_stmt)
        expired_videos = expired_result.scalars().all()

        all_videos = list(videos) + list(expired_videos)
        summary = {"total": len(all_videos), "resolved": 0, "failed": 0, "deleted": 0}

        for video in all_videos:
            video_id = video.id
            result = await self.resolve_video(video_id, force=True)
            if result is not None:
                summary["resolved"] += 1
            else:
                check = await self._db.execute(select(Video).where(Video.id == video_id))
                if check.scalar_one_or_none() is None:
                    summary["deleted"] += 1
                else:
                    summary["failed"] += 1
            await asyncio.sleep(1.0)

        logger.info(
            f"Batch resolve complete: {summary['resolved']} resolved, "
            f"{summary['failed']} failed, {summary['deleted']} deleted "
            f"out of {summary['total']}"
        )
        return summary

    async def resolve_expired(self, limit: int = 100) -> dict:
        """Re-resolve videos whose cached stream URLs have gone stale."""
        expiry_cutoff = datetime.datetime.utcnow() - datetime.timedelta(hours=RESOLUTION_TTL_HOURS)
        stmt = (
            select(Video)
            .where(
                Video.resolution_status == "resolved",
                Video.resolved_at < expiry_cutoff,
            )
            .order_by(Video.reddit_score.desc().nullslast())
            .limit(limit)
        )
        result = await self._db.execute(stmt)
        videos = result.scalars().all()

        summary = {"total": len(videos), "resolved": 0, "failed": 0, "deleted": 0}

        for video in videos:
            video_id = video.id
            result = await self.resolve_video(video_id, force=True)
            if result is not None:
                summary["resolved"] += 1
            else:
                check = await self._db.execute(select(Video).where(Video.id == video_id))
                if check.scalar_one_or_none() is None:
                    summary["deleted"] += 1
                else:
                    summary["failed"] += 1
            await asyncio.sleep(1.0)

        logger.info(
            f"Expired resolve complete: {summary['resolved']} refreshed, "
            f"{summary['failed']} failed, {summary['deleted']} deleted "
            f"out of {summary['total']}"
        )
        return summary

    async def backfill_thumbnails(self, limit: int = 50) -> dict:
        """Metadata-only yt-dlp pass to fill missing thumbnails."""
        from sqlalchemy import or_

        stmt = (
            select(Video)
            .where(or_(Video.thumbnail_url.is_(None), Video.thumbnail_url == ""))
            .order_by(Video.id.asc())
            .limit(limit)
        )
        result = await self._db.execute(stmt)
        videos = result.scalars().all()

        summary = {"total": len(videos), "filled": 0, "skipped": 0, "failed": 0}

        loop = asyncio.get_event_loop()
        for video in videos:
            try:
                thumbnail_url = await asyncio.wait_for(
                    loop.run_in_executor(
                        _process_pool,
                        _fetch_thumbnail_sync_worker,
                        video.source_url,
                        self._cookies_path,
                    ),
                    timeout=60.0,
                )
                if thumbnail_url:
                    video.thumbnail_url = thumbnail_url
                    summary["filled"] += 1
                    logger.info(f"Backfill thumbnail: video {video.id} -> {thumbnail_url[:80]}")
                else:
                    summary["skipped"] += 1
            except asyncio.TimeoutError:
                summary["failed"] += 1
                logger.warning(f"Backfill thumbnail: timed out for video {video.id}")
            except Exception as e:
                summary["failed"] += 1
                logger.warning(f"Backfill thumbnail: failed for video {video.id}: {e}")

            await asyncio.sleep(0.5)

        await self._db.commit()
        logger.info(
            f"Thumbnail backfill complete: {summary['filled']} filled, "
            f"{summary['skipped']} skipped, {summary['failed']} failed "
            f"out of {summary['total']}"
        )
        return summary

    async def purge_dash_videos(self) -> int:
        """Delete all videos whose resolved stream URL is a DASH manifest."""
        stmt = select(Video).where(Video.resolution_status == "resolved")
        result = await self._db.execute(stmt)
        resolved_videos = result.scalars().all()

        dash_videos = [v for v in resolved_videos if _is_dash_url(v.resolved_stream_url or "")]
        count = len(dash_videos)

        for video in dash_videos:
            logger.info(f"Purging DASH-only video {video.id}: {video.title[:60]}")
            await self._delete_video(video)

        await self._db.commit()
        logger.info(f"Purged {count} DASH-only videos from database")
        return count

    async def purge_dead_videos(self) -> int:
        """Delete all videos currently marked as failed."""
        stmt = select(Video).where(Video.resolution_status == "failed")
        result = await self._db.execute(stmt)
        dead_videos = result.scalars().all()

        count = len(dead_videos)
        for video in dead_videos:
            await self._delete_video(video)

        await self._db.commit()
        logger.info(f"Purged {count} dead videos from database")
        return count

    async def _extract_with_ytdlp(
        self, url: str
    ) -> Tuple[Optional[StreamInfo], Optional[str], bool]:
        """
        Run yt-dlp extraction in a subprocess with a hard wall-clock timeout.

        Uses ProcessPoolExecutor so the yt-dlp process can be forcibly killed
        if it exceeds YTDLP_TIMEOUT_SECONDS. ThreadPoolExecutor cannot kill
        a hung thread, which is why we use processes here.

        Returns (StreamInfo | None, error_msg | None, is_permanent: bool).
        """
        loop = asyncio.get_event_loop()
        try:
            result_dict, error_msg, is_permanent = await asyncio.wait_for(
                loop.run_in_executor(
                    _process_pool,
                    _extract_sync_worker,
                    url,
                    self._cookies_path,
                ),
                timeout=float(YTDLP_TIMEOUT_SECONDS),
            )
        except asyncio.TimeoutError:
            logger.error(
                f"yt-dlp TIMED OUT after {YTDLP_TIMEOUT_SECONDS}s for {url} — "
                "marking as transient failure. The hung subprocess has been abandoned."
            )
            return None, f"yt-dlp timed out after {YTDLP_TIMEOUT_SECONDS}s", False
        except Exception as e:
            logger.error(f"yt-dlp extraction error for {url}: {e}")
            return None, f"yt-dlp extraction error: {e}", False

        if result_dict is None:
            return None, error_msg, is_permanent

        stream_info = StreamInfo.from_dict(result_dict)

        # Log what yt-dlp picked
        is_hls  = _is_hls_url(stream_info.stream_url)
        is_dash = _is_dash_url(stream_info.stream_url)
        logger.info(
            f"yt-dlp selected | ext={result_dict.get('ext')} | "
            f"protocol={result_dict.get('protocol')} | "
            f"hls={is_hls} | dash={is_dash} | "
            f"url_preview={stream_info.stream_url[:80]}"
        )

        if is_dash:
            logger.warning(
                f"DASH URL slipped through format selector for {url} — "
                f"browser cannot play this."
            )

        if not self._cookies_path or not os.path.isfile(self._cookies_path):
            logger.warning(
                "No cookies.txt found — YouTube age-restricted content will fail. "
                f"Expected at: {self._cookies_path}"
            )

        return stream_info, None, False

    def _is_cache_valid(self, video: Video) -> bool:
        if video.resolution_status != "resolved":
            return False
        if not video.resolved_stream_url:
            return False
        if not video.resolved_at:
            return False

        age_seconds = (datetime.datetime.utcnow() - video.resolved_at).total_seconds()

        if _is_adaptive_url(video.resolved_stream_url):
            ttl_seconds = ADAPTIVE_TTL_MINUTES * 60
            valid = age_seconds < ttl_seconds
            url_type = "HLS" if _is_hls_url(video.resolved_stream_url) else "DASH"
            logger.debug(
                f"{url_type} cache check: age={int(age_seconds)}s "
                f"ttl={ttl_seconds}s valid={valid}"
            )
            return valid
        else:
            return age_seconds < (RESOLUTION_TTL_HOURS * 3600)
