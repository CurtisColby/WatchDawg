"""
WatchDawg Generic yt-dlp Playlist Provider.

Handles any URL that yt-dlp can parse as a playlist — Vimeo channels,
YouTube playlists/channels, Dailymotion channels, etc.

This uses yt-dlp's --flat-playlist mode to quickly extract video metadata
from a playlist page WITHOUT downloading or resolving each video. The
actual stream URL resolution happens later through the resolver service.

Supported sources (anything yt-dlp recognizes as a playlist):
- Vimeo channels:   https://vimeo.com/channels/channelname/videos
- Vimeo groups:     https://vimeo.com/groups/groupname/videos
- Vimeo users:      https://vimeo.com/username/videos
- YouTube playlists: https://www.youtube.com/playlist?list=...
- YouTube channels:  https://www.youtube.com/@channelname/videos
- Dailymotion:       https://www.dailymotion.com/username
- And many more...
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

# Map domains to provider name prefixes for source_post_id generation
DOMAIN_PROVIDER_MAP = {
    "vimeo.com": "vimeo",
    "www.vimeo.com": "vimeo",
    "youtube.com": "youtube",
    "www.youtube.com": "youtube",
    "m.youtube.com": "youtube",
    "youtu.be": "youtube",
    "dailymotion.com": "dailymotion",
    "www.dailymotion.com": "dailymotion",
    "streamable.com": "streamable",
}


class PlaylistProvider(BaseProvider):
    """
    Generic yt-dlp playlist provider.

    Scrapes any yt-dlp-supported playlist URL for video entries.
    Uses flat extraction (no stream URL resolution) for speed.
    """

    def __init__(
        self,
        playlist_url: str,
        channel_name: str = "playlist",
        channel_id: Optional[int] = None,
    ):
        """
        Args:
            playlist_url: The full URL to the playlist/channel page.
            channel_name: A display name for logging.
            channel_id: The database Channel.id for linking videos.
        """
        self._playlist_url = playlist_url
        self._channel_name = channel_name
        self._channel_id = channel_id
        self._provider_prefix = self._detect_provider(playlist_url)
        self._cookies_path = settings.ytdlp_cookies_path

    @property
    def provider_name(self) -> str:
        return self._provider_prefix

    @property
    def channel_id(self) -> Optional[int]:
        return self._channel_id

    def _detect_provider(self, url: str) -> str:
        """Detect the provider name from the URL domain."""
        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()
            return DOMAIN_PROVIDER_MAP.get(domain, "playlist")
        except Exception:
            return "playlist"

    async def fetch_posts(self, limit: int = 50) -> List[DiscoveredVideo]:
        """
        Extract video entries from the playlist using yt-dlp's flat extraction.

        This is fast — it only reads the playlist page metadata, doesn't
        resolve any individual video stream URLs.
        """
        logger.info(
            f"Extracting playlist: {self._channel_name} ({self._playlist_url})"
        )

        loop = asyncio.get_event_loop()
        try:
            videos = await loop.run_in_executor(
                None, self._extract_playlist_sync, limit
            )
            logger.info(
                f"Playlist '{self._channel_name}' returned {len(videos)} videos"
            )
            return videos
        except Exception as e:
            logger.error(
                f"Failed to extract playlist '{self._channel_name}': {e}"
            )
            return []

    def _extract_playlist_sync(self, limit: int) -> List[DiscoveredVideo]:
        """
        Synchronous yt-dlp flat playlist extraction. Runs in a thread.

        flat-playlist mode gives us video IDs, titles, and thumbnails
        without the overhead of resolving each video's stream URL.
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

        # Inject cookies if available (needed for some private/restricted content)
        if self._cookies_path and os.path.isfile(self._cookies_path):
            ydl_opts["cookiefile"] = self._cookies_path

        videos: List[DiscoveredVideo] = []

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(self._playlist_url, download=False)

                if info is None:
                    logger.warning(
                        f"yt-dlp returned no info for playlist: {self._playlist_url}"
                    )
                    return []

                entries = info.get("entries", [])
                if entries is None:
                    entries = []

                playlist_title = info.get("title", self._channel_name)

                for entry in entries:
                    if entry is None:
                        continue

                    video = self._parse_entry(entry, playlist_title)
                    if video is not None:
                        videos.append(video)

        except Exception as e:
            logger.error(f"yt-dlp playlist extraction error: {e}")

        return videos

    def _parse_entry(
        self, entry: dict, playlist_title: str
    ) -> Optional[DiscoveredVideo]:
        """
        Parse a single flat playlist entry into a DiscoveredVideo.

        Flat entries have limited metadata compared to full extraction,
        but enough for discovery (id, title, url, thumbnail, duration).
        """
        video_id = entry.get("id", "")
        title = entry.get("title") or "Unknown Title"
        url = entry.get("url", "")
        webpage_url = entry.get("webpage_url", "")

        if not video_id:
            return None

        # Build the actual URL — flat entries sometimes only have video ID
        if webpage_url:
            source_url = webpage_url
        elif url and url.startswith("http"):
            source_url = url
        elif self._provider_prefix == "vimeo" and video_id.isdigit():
            source_url = f"https://vimeo.com/{video_id}"
        elif self._provider_prefix == "youtube":
            source_url = f"https://www.youtube.com/watch?v={video_id}"
        else:
            # url field in flat mode is sometimes just the video id
            source_url = url if url.startswith("http") else ""

        if not source_url:
            return None

        # Build a unique source_post_id
        source_post_id = f"{self._provider_prefix}_{video_id}"

        # Extract thumbnail
        thumbnail = entry.get("thumbnail") or entry.get("thumbnails", [{}])[0].get("url") if entry.get("thumbnails") else None

        # Duration
        duration = entry.get("duration")

        # Try to parse artist from title
        artist = self._parse_artist_from_title(title)

        # Use view_count as a score proxy (Vimeo doesn't have Reddit scores)
        score = entry.get("view_count") or entry.get("like_count") or 0

        return DiscoveredVideo(
            source_provider=self._provider_prefix,
            source_post_id=source_post_id,
            source_url=source_url,
            title=title.strip(),
            artist=artist,
            thumbnail_url=thumbnail,
            duration_seconds=float(duration) if duration else None,
            score=score,
        )

    def _parse_artist_from_title(self, title: str) -> Optional[str]:
        """
        Attempt to extract an artist name from common title formats.

        Common patterns:
        - "Artist - Song Title"
        - "Artist — Song Title"
        - "Artist | Song Title"
        """
        # Strip common bracket prefixes like [Genre] or [NSFW]
        cleaned = re.sub(r"^\[.*?\]\s*", "", title)

        for separator in [" - ", " — ", " – ", " | "]:
            if separator in cleaned:
                artist = cleaned.split(separator)[0].strip()
                if artist and len(artist) < 100:
                    return artist

        return None

    async def validate_connection(self) -> bool:
        """Test that the playlist URL is accessible via yt-dlp."""
        loop = asyncio.get_event_loop()
        try:
            return await loop.run_in_executor(None, self._validate_sync)
        except Exception:
            return False

    def _validate_sync(self) -> bool:
        """Synchronous validation — try to extract 1 entry from the playlist."""
        import yt_dlp

        ydl_opts = {
            "extract_flat": "in_playlist",
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "playlistend": 1,
            "socket_timeout": 15,
        }

        if self._cookies_path and os.path.isfile(self._cookies_path):
            ydl_opts["cookiefile"] = self._cookies_path

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(self._playlist_url, download=False)
                return info is not None
        except Exception:
            return False

    async def close(self):
        """No persistent connections to close for yt-dlp."""
        pass
