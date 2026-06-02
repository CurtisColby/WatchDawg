"""
WatchDawg Vimeo Channel Provider.

Discovers videos from Vimeo channels using yt-dlp flat playlist extraction.

Why not RSS:
  Vimeo RSS feeds are hard-capped at 10 items regardless of channel size.
  A channel with 1,167 videos would only ever yield 10 per scrape.

Why not yt-dlp with /videos suffix:
  https://vimeo.com/channels/{slug}/videos is not a recognized yt-dlp
  extractor pattern and raises UnsupportedURL.

What works:
  https://vimeo.com/channels/{slug} (no /videos suffix) IS recognized by
  yt-dlp's Vimeo channel extractor and fully paginates — tested at 200+
  entries. This is the URL format already stored in the database.

The class retains the VimeoRSSProvider name so nothing else in the codebase
needs to change — channel.py, feed.py, and scheduler.py all import it by
this name.
"""

import asyncio
import logging
import os
import re
from typing import List, Optional
from urllib.parse import urlparse

from app.providers.base import BaseProvider, DiscoveredVideo
from app.config import settings

logger = logging.getLogger(__name__)


class VimeoRSSProvider(BaseProvider):
    """
    Fetches video listings from Vimeo channels using yt-dlp flat extraction.

    Supports all Vimeo channel URL formats:
      https://vimeo.com/channels/{slug}
      https://vimeo.com/groups/{slug}
      https://vimeo.com/{username}

    Uses flat extraction (no stream URL resolution) for fast discovery.
    Actual stream URL resolution happens later via the resolver service.
    """

    def __init__(
        self,
        channel_url: str,
        channel_name: str = "Vimeo",
        channel_id: Optional[int] = None,
    ):
        """
        Args:
            channel_url: The Vimeo channel URL as stored in the database.
            channel_name: Display name for logging.
            channel_id: Database Channel.id for linking discovered videos.
        """
        self._channel_url = self._normalize_url(channel_url)
        self._channel_name = channel_name
        self._channel_id = channel_id
        self._cookies_path = settings.ytdlp_cookies_path

    @property
    def provider_name(self) -> str:
        return "vimeo"

    @property
    def channel_id(self) -> Optional[int]:
        return self._channel_id

    def _normalize_url(self, url: str) -> str:
        """
        Normalize the stored channel URL to a form yt-dlp can handle.

        yt-dlp recognizes:
          https://vimeo.com/channels/{slug}       OK
          https://vimeo.com/groups/{slug}         OK
          https://vimeo.com/{username}            OK

        yt-dlp does NOT recognize:
          https://vimeo.com/channels/{slug}/videos     UNSUPPORTED
          https://vimeo.com/channels/{slug}/videos/rss UNSUPPORTED

        Strip any trailing /videos or /videos/rss suffix so the URL
        lands on the supported extractor pattern.
        """
        parsed = urlparse(url)
        path = parsed.path.rstrip("/")

        if path.endswith("/videos/rss"):
            path = path[: -len("/videos/rss")]
        elif path.endswith("/videos"):
            path = path[: -len("/videos")]

        return f"https://vimeo.com{path}"

    async def fetch_posts(self, limit: int = 500) -> List[DiscoveredVideo]:
        """
        Extract video entries from the Vimeo channel using yt-dlp flat extraction.

        Runs yt-dlp in a thread pool to avoid blocking the async event loop.
        """
        logger.info(
            f"Extracting Vimeo channel: {self._channel_name} ({self._channel_url})"
        )

        loop = asyncio.get_event_loop()
        try:
            videos = await loop.run_in_executor(
                None, self._extract_sync, limit
            )
            logger.info(
                f"Vimeo channel '{self._channel_name}': found {len(videos)} videos"
            )
            return videos
        except Exception as e:
            logger.error(
                f"Failed to extract Vimeo channel '{self._channel_name}': {e}"
            )
            return []

    def _extract_sync(self, limit: int) -> List[DiscoveredVideo]:
        """
        Synchronous yt-dlp flat extraction. Runs in a thread executor.

        flat-playlist mode gives us video IDs, titles, thumbnails, and
        durations without resolving any individual stream URLs.
        """
        import yt_dlp

        ydl_opts = {
            "extract_flat": "in_playlist",
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "playlistend": limit,
            "socket_timeout": 30,
            "retries": 3,
            "ignoreerrors": True,
        }

        if self._cookies_path and os.path.isfile(self._cookies_path):
            ydl_opts["cookiefile"] = self._cookies_path
            logger.debug(f"Using cookies from {self._cookies_path}")

        videos: List[DiscoveredVideo] = []

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(self._channel_url, download=False)

                if info is None:
                    logger.warning(
                        f"yt-dlp returned no info for: {self._channel_url}"
                    )
                    return []

                entries = info.get("entries") or []

                for entry in entries:
                    if entry is None:
                        continue
                    video = self._parse_entry(entry)
                    if video is not None:
                        videos.append(video)

        except Exception as e:
            logger.error(
                f"yt-dlp extraction error for '{self._channel_name}': {e}"
            )

        return videos

    def _parse_entry(self, entry: dict) -> Optional[DiscoveredVideo]:
        """
        Parse a single flat playlist entry into a DiscoveredVideo.

        Flat entries have limited metadata but enough for discovery:
        id, title, url/webpage_url, thumbnail, duration.
        """
        video_id = entry.get("id", "")
        if not video_id or not str(video_id).isdigit():
            return None

        video_id = str(video_id)
        title = (entry.get("title") or "Unknown Title").strip()

        # Build canonical source URL
        webpage_url = entry.get("webpage_url", "")
        url = entry.get("url", "")
        if webpage_url and webpage_url.startswith("http"):
            source_url = webpage_url
        elif url and url.startswith("http"):
            source_url = url
        else:
            source_url = f"https://vimeo.com/{video_id}"

        source_post_id = f"vimeo_{video_id}"

        # Thumbnail
        thumbnail = entry.get("thumbnail")
        if not thumbnail:
            thumbs = entry.get("thumbnails") or []
            if thumbs:
                thumbnail = thumbs[0].get("url")

        # Duration
        duration = entry.get("duration")

        # Score — use view_count as proxy
        score = entry.get("view_count") or entry.get("like_count") or 0

        # Artist from title
        artist = self._parse_artist_from_title(title)

        return DiscoveredVideo(
            source_provider="vimeo",
            source_post_id=source_post_id,
            source_url=source_url,
            title=title,
            artist=artist,
            thumbnail_url=thumbnail,
            duration_seconds=float(duration) if duration else None,
            score=score,
        )

    def _parse_artist_from_title(self, title: str) -> Optional[str]:
        """Extract artist from common title patterns like 'Artist - Song'."""
        cleaned = re.sub(r"^\[.*?\]\s*", "", title)
        for separator in [" - ", " — ", " – ", " | "]:
            if separator in cleaned:
                artist = cleaned.split(separator)[0].strip()
                if artist and len(artist) < 100:
                    return artist
        return None

    async def validate_connection(self) -> bool:
        """Test that the Vimeo channel URL is accessible via yt-dlp."""
        loop = asyncio.get_event_loop()
        try:
            return await loop.run_in_executor(None, self._validate_sync)
        except Exception:
            return False

    def _validate_sync(self) -> bool:
        """Synchronous validation — try to extract 1 entry."""
        import yt_dlp

        ydl_opts = {
            "extract_flat": "in_playlist",
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "playlistend": 1,
            "socket_timeout": 15,
            "ignoreerrors": True,
        }

        if self._cookies_path and os.path.isfile(self._cookies_path):
            ydl_opts["cookiefile"] = self._cookies_path

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(self._channel_url, download=False)
                return info is not None
        except Exception:
            return False

    async def close(self):
        """No persistent connections to close for yt-dlp."""
        pass
