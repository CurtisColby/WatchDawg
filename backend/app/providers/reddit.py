"""
WatchDawg Reddit Provider.

Scrapes configured subreddits using Reddit's public JSON API.
No OAuth required — we just append .json to the subreddit URL.

Rate limiting notes:
- Reddit allows ~30 requests/minute for unauthenticated JSON access.
- We use a proper User-Agent to avoid getting blocked.
- httpx handles connection pooling and timeouts.

URL extraction logic:
- Reddit posts can contain YouTube links, Vimeo links, direct video URLs,
  or Reddit-hosted video (v.redd.it).
- We extract the actual media URL, not the Reddit post URL.
- Self-posts (text only) and image-only posts are filtered out.
"""

import logging
import re
from typing import List, Optional
from urllib.parse import urlparse

import httpx

from app.providers.base import BaseProvider, DiscoveredVideo
from app.config import settings

logger = logging.getLogger(__name__)

# Domains we recognize as video sources
VIDEO_DOMAINS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "youtu.be",
    "vimeo.com",
    "www.vimeo.com",
    "v.redd.it",
    "streamable.com",
    "dailymotion.com",
    "www.dailymotion.com",
}

# File extensions that indicate a direct video link
VIDEO_EXTENSIONS = {".mp4", ".webm", ".mkv", ".avi", ".mov", ".m3u8"}

# Reddit JSON API base
REDDIT_BASE_URL = "https://www.reddit.com"

# Be a good citizen — identify ourselves
USER_AGENT = "WatchDawg/0.1.0 (Media Aggregation Backend; Contact: github.com/watchdawg)"


class RedditProvider(BaseProvider):
    """
    Scrapes subreddits for video posts using Reddit's public JSON API.
    """

    def __init__(self, subreddits: Optional[List[str]] = None):
        """
        Args:
            subreddits: List of subreddit names to scrape (no r/ prefix).
                        Defaults to the configured list from .env.
        """
        self._subreddits = subreddits or settings.subreddit_list
        self._client = httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT},
            timeout=httpx.Timeout(30.0),
            follow_redirects=True,
        )

    @property
    def provider_name(self) -> str:
        return "reddit"

    async def fetch_posts(self, limit: int = 50) -> List[DiscoveredVideo]:
        """
        Fetch video posts from all configured subreddits.

        Hits the 'hot' listing for each subreddit. Could be extended
        to also scrape 'new' or 'top' with a time filter.
        """
        all_videos: List[DiscoveredVideo] = []

        for subreddit in self._subreddits:
            try:
                videos = await self._scrape_subreddit(subreddit, limit=limit)
                all_videos.extend(videos)
                logger.info(
                    f"Scraped r/{subreddit}: found {len(videos)} video posts"
                )
            except Exception as e:
                logger.error(f"Failed to scrape r/{subreddit}: {e}")
                continue

        logger.info(f"Total videos discovered across all subreddits: {len(all_videos)}")
        return all_videos

    async def _scrape_subreddit(
        self, subreddit: str, limit: int = 50, sort: str = "hot"
    ) -> List[DiscoveredVideo]:
        """
        Scrape a single subreddit's JSON feed.

        Args:
            subreddit: Subreddit name without r/ prefix.
            limit: Max posts to fetch (Reddit caps at 100 per request).
            sort: Listing sort — "hot", "new", "top", "rising".
        """
        url = f"{REDDIT_BASE_URL}/r/{subreddit}/{sort}.json"
        params = {"limit": min(limit, 100), "raw_json": 1}

        response = await self._client.get(url, params=params)
        response.raise_for_status()

        data = response.json()
        posts = data.get("data", {}).get("children", [])

        videos: List[DiscoveredVideo] = []
        for post in posts:
            video = self._parse_post(post.get("data", {}), subreddit)
            if video is not None:
                videos.append(video)

        return videos

    def _parse_post(self, post_data: dict, subreddit: str) -> Optional[DiscoveredVideo]:
        """
        Parse a single Reddit post into a DiscoveredVideo.

        Returns None if the post isn't a video or can't be parsed.

        Source URL strategy:
        - For Reddit-hosted video (v.redd.it / is_video=True): store the Reddit
          post permalink (https://www.reddit.com/r/sub/comments/id/slug/). yt-dlp
          extracts the full muxed stream from the post page — the fallback_url is
          video-only and produces a gray box with no audio.
        - For external links (YouTube, Vimeo, etc.): store the external URL
          directly so yt-dlp resolves it natively.
        """
        # Skip self-posts (text only), stickied mod posts, and removed posts
        if post_data.get("is_self", False):
            return None
        if post_data.get("stickied", False):
            return None
        if post_data.get("removed_by_category") is not None:
            return None

        post_id = post_data.get("id", "")
        title = post_data.get("title", "Unknown Title")
        url = post_data.get("url", "")
        score = post_data.get("score", 0)
        thumbnail = post_data.get("thumbnail", "")
        permalink = post_data.get("permalink", "")

        if not url or not post_id:
            return None

        # Determine the source URL to store
        parsed_url = urlparse(url)
        domain = parsed_url.netloc.lower()
        is_reddit_video = (
            post_data.get("is_video", False) or
            domain == "v.redd.it"
        )

        if is_reddit_video:
            # Use the Reddit post permalink — yt-dlp extracts the full
            # muxed stream from the post page (video + audio).
            # The v.redd.it fallback_url is video-only and plays as gray box.
            if permalink:
                source_url = f"https://www.reddit.com{permalink.rstrip('/')}"
            else:
                source_url = f"https://www.reddit.com/r/{subreddit}/comments/{post_id}/"
        else:
            # External link (YouTube, Vimeo, direct mp4, etc.) — validate it
            video_url = self._extract_video_url(post_data, url)
            if video_url is None:
                return None
            source_url = video_url

        # Clean up the thumbnail — Reddit sometimes returns "default", "self", etc.
        if thumbnail in ("default", "self", "nsfw", "spoiler", "image", ""):
            thumbnail = self._extract_thumbnail(post_data)

        # Try to parse artist from title (common format: "Artist - Song Title")
        artist = self._parse_artist_from_title(title)

        return DiscoveredVideo(
            source_provider="reddit",
            source_post_id=f"reddit_{post_id}",
            source_url=source_url,
            title=title.strip(),
            artist=artist,
            thumbnail_url=thumbnail if thumbnail else None,
            score=score,
        )

    def _extract_video_url(self, post_data: dict, url: str) -> Optional[str]:
        """
        Determine the actual video URL from a Reddit post.

        Reddit posts can link to videos in several ways:
        1. Direct link to YouTube/Vimeo/etc.
        2. Reddit-hosted video (v.redd.it)
        3. Direct link to a video file (.mp4, .webm)
        4. Embedded in a cross-post
        """
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        path_lower = parsed.path.lower()

        # Check if it's a known video domain
        if domain in VIDEO_DOMAINS:
            return url

        # Check if it's a direct video file link
        for ext in VIDEO_EXTENSIONS:
            if path_lower.endswith(ext):
                return url

        # Check for Reddit-hosted video in the media metadata
        if post_data.get("is_video", False):
            reddit_video = (
                post_data.get("media", {})
                .get("reddit_video", {})
                .get("fallback_url")
            )
            if reddit_video:
                # Strip the query params that Reddit adds (?source=fallback)
                return reddit_video.split("?")[0]

        # Check for embedded media (e.g., YouTube embed in "media" field)
        oembed = post_data.get("media", {})
        if oembed and "oembed" in oembed:
            # The original URL is often more useful than the embed
            return url

        # Check cross-post source
        crosspost_list = post_data.get("crosspost_parent_list", [])
        if crosspost_list:
            return self._extract_video_url(crosspost_list[0], crosspost_list[0].get("url", ""))

        # If we can't identify it as a video, skip it
        return None

    def _extract_thumbnail(self, post_data: dict) -> Optional[str]:
        """
        Try to extract a usable thumbnail from Reddit's preview data.
        """
        try:
            images = post_data.get("preview", {}).get("images", [])
            if images:
                # Get the source (highest res) image
                source = images[0].get("source", {})
                url = source.get("url", "")
                # Reddit HTML-encodes the URL in preview data
                return url.replace("&amp;", "&") if url else None
        except (IndexError, KeyError):
            pass
        return None

    def _parse_artist_from_title(self, title: str) -> Optional[str]:
        """
        Attempt to extract an artist name from common title formats.

        Common patterns in music video subreddits:
        - "Artist - Song Title"
        - "Artist — Song Title"
        - "Artist: Song Title"
        - "Artist | Song Title"
        - "[Genre] Artist - Song Title"
        """
        # Strip common bracket prefixes like [Genre] or [NSFW]
        cleaned = re.sub(r"^\[.*?\]\s*", "", title)

        # Try common separators
        for separator in [" - ", " — ", " – ", ": ", " | "]:
            if separator in cleaned:
                artist = cleaned.split(separator)[0].strip()
                if artist and len(artist) < 100:  # Sanity check
                    return artist

        return None

    async def validate_connection(self) -> bool:
        """Test that Reddit's JSON API is reachable."""
        try:
            response = await self._client.get(
                f"{REDDIT_BASE_URL}/r/{self._subreddits[0]}/hot.json",
                params={"limit": 1},
            )
            return response.status_code == 200
        except Exception:
            return False

    async def close(self):
        """Close the HTTP client. Call on shutdown."""
        await self._client.aclose()
