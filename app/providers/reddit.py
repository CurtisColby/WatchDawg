"""
WatchDawg Reddit Provider.

Scrapes configured subreddits using Reddit's JSON listings, authenticated
with session cookies exported from a logged-in browser.

Session 60 rebuild — fetch layer only:
- Reddit 403-blocks ALL unauthenticated JSON access from this network
  (every User-Agent, every curl_cffi impersonation target, old.reddit,
  api.reddit.com — all tested dead in Session 59). OAuth app registration
  is gated behind manual approval and effectively closed to personal
  projects. The ONE working path, verified live from the container:
  logged-in browser cookies (MozillaCookieJar at settings.reddit_cookies_path)
  + curl_cffi TLS impersonation (chrome136). HTTP 200 with real JSON,
  NSFW subreddits included (the account's over-18 preference applies).
- Cookies are re-read from disk at the start of every scrape run, so
  re-exporting fresh cookies to the host file takes effect on the next
  run with no restart.
- Volume discipline (the Vimeo lesson, applied from day one): one request
  per subreddit per run, randomized 20-40s spacing between subreddits.
- Self-healing pause flag: any 403 flips a module-level "paused" flag,
  the rest of the run is skipped, and nothing is ever marked failed.
  While paused, each run sends exactly ONE probe request (the first
  subreddit): 403 keeps us paused, 200 clears the flag and the run
  continues normally. Recovery after a cookie re-export is automatic.
  A missing/unreadable/empty cookie file triggers the same pause path.

URL extraction logic (UNCHANGED from the original provider):
- Reddit posts can contain YouTube links, Vimeo links, direct video URLs,
  or Reddit-hosted video (v.redd.it).
- We extract the actual media URL, not the Reddit post URL — except for
  Reddit-hosted video, where we store the post permalink so yt-dlp gets
  the full muxed stream (the fallback_url is video-only / gray box).
- Self-posts (text only) and image-only posts are filtered out.
"""

import asyncio
import logging
import random
import re
from http.cookiejar import MozillaCookieJar
from typing import Dict, List, Optional
from urllib.parse import urlparse

from curl_cffi.requests import AsyncSession

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

# Browser TLS fingerprint to impersonate. chrome136 is the target verified
# working against Reddit from this container in Session 59. curl_cffi sets
# matching browser headers automatically — do NOT override User-Agent.
IMPERSONATE_TARGET = "chrome136"

# Inter-subreddit pacing (seconds). Randomized to avoid a mechanical
# request signature. Burst volume is the block trigger on every provider
# we scrape — slow drips are the sustainable posture.
PACING_MIN_SECONDS = 20
PACING_MAX_SECONDS = 40

# Per-request timeout (seconds)
REQUEST_TIMEOUT = 30

# ---------------------------------------------------------------------------
# Module-level pause flag.
#
# Module-level (not instance-level) because the scraper may construct a
# fresh RedditProvider per run — the flag must survive across instances
# for the lifetime of the process. It intentionally does NOT survive a
# container restart: a restart gets one fresh full attempt, which either
# works or immediately re-pauses on the first 403.
# ---------------------------------------------------------------------------
_reddit_paused: bool = False


def reddit_is_paused() -> bool:
    """Expose pause state for logging/UI without touching the flag."""
    return _reddit_paused


def _set_paused(value: bool) -> None:
    global _reddit_paused
    _reddit_paused = value


class RedditProvider(BaseProvider):
    """
    Scrapes subreddits for video posts using Reddit's JSON listings,
    authenticated with browser session cookies.
    """

    def __init__(self, subreddits: Optional[List[str]] = None):
        """
        Args:
            subreddits: List of subreddit names to scrape (no r/ prefix).
                        Defaults to the configured list from .env.
        """
        self._subreddits = subreddits or settings.subreddit_list
        self._session = AsyncSession(
            impersonate=IMPERSONATE_TARGET,
            timeout=REQUEST_TIMEOUT,
        )

    @property
    def provider_name(self) -> str:
        return "reddit"

    # ------------------------------------------------------------------
    # Cookie handling
    # ------------------------------------------------------------------

    def _load_cookies(self) -> Optional[Dict[str, str]]:
        """
        Load Reddit session cookies fresh from disk.

        Returns a simple {name: value} dict scoped to reddit.com domains,
        or None if the file is missing, unreadable, or contains no Reddit
        cookies. Loading fresh on every scrape run means a re-exported
        cookie file takes effect on the next run — no restart needed.
        """
        path = settings.reddit_cookies_path
        jar = MozillaCookieJar()
        try:
            # ignore_discard: keep session cookies (Reddit's login IS a
            # session cookie). ignore_expires: let Reddit judge staleness,
            # not the local clock.
            jar.load(path, ignore_discard=True, ignore_expires=True)
        except FileNotFoundError:
            logger.error(
                f"REDDIT PAUSED: cookie file not found at {path}. "
                f"Export Reddit cookies from a logged-in browser "
                f"(MozillaCookieJar format) to the mounted host file."
            )
            return None
        except Exception as e:
            logger.error(
                f"REDDIT PAUSED: cookie file at {path} could not be parsed: {e}. "
                f"Re-export it from the browser extension (MozillaCookieJar format)."
            )
            return None

        cookies = {
            c.name: c.value
            for c in jar
            if c.value is not None and "reddit" in (c.domain or "").lower()
        }
        if not cookies:
            logger.error(
                f"REDDIT PAUSED: cookie file at {path} loaded but contains "
                f"no reddit.com cookies. Re-export from a logged-in browser."
            )
            return None

        logger.debug(f"Loaded {len(cookies)} Reddit cookies from {path}")
        return cookies

    # ------------------------------------------------------------------
    # Fetch layer
    # ------------------------------------------------------------------

    async def fetch_posts(self, limit: int = 50) -> List[DiscoveredVideo]:
        """
        Fetch video posts from all configured subreddits.

        One request per subreddit (hot listing), randomized 20-40s spacing
        between subreddits. Any 403 pauses Reddit scraping for the rest of
        this run and all future runs until a probe succeeds — videos stay
        pending, nothing is ever marked failed.

        While paused, this method sends exactly one probe request per run:
        success clears the pause and the run continues normally; failure
        leaves the pause in place and returns immediately.
        """
        cookies = self._load_cookies()
        if cookies is None:
            _set_paused(True)
            return []

        if not self._subreddits:
            logger.warning("Reddit: no subreddits configured — nothing to scrape.")
            return []

        if _reddit_paused:
            logger.info(
                "Reddit is PAUSED (stale cookies suspected) — sending one "
                "probe request to check for recovery..."
            )

        all_videos: List[DiscoveredVideo] = []

        for index, subreddit in enumerate(self._subreddits):
            # Randomized pacing between subreddits (not before the first)
            if index > 0:
                delay = random.uniform(PACING_MIN_SECONDS, PACING_MAX_SECONDS)
                logger.debug(f"Reddit pacing: sleeping {delay:.1f}s before r/{subreddit}")
                await asyncio.sleep(delay)

            try:
                videos = await self._scrape_subreddit(subreddit, cookies, limit=limit)
            except _RedditBlocked:
                _set_paused(True)
                logger.error(
                    f"REDDIT PAUSED: got HTTP 403 from r/{subreddit}. Reddit "
                    f"cookies appear stale or the session was rotated. "
                    f"Skipping all remaining subreddits this run. Re-export "
                    f"cookies to the host file — scraping will resume "
                    f"automatically on the next successful probe."
                )
                break
            except Exception as e:
                # Transient (timeout, 429, 5xx, parse hiccup): log and move
                # to the next subreddit. No pause, nothing marked failed.
                logger.error(f"Failed to scrape r/{subreddit} (transient): {e}")
                continue

            # A successful response proves the cookies work — clear the
            # pause if it was set and let the run continue normally.
            if _reddit_paused:
                _set_paused(False)
                logger.info(
                    "Reddit scraping RESUMED: probe request succeeded — "
                    "cookies are valid again. Continuing full scrape run."
                )

            all_videos.extend(videos)
            logger.info(f"Scraped r/{subreddit}: found {len(videos)} video posts")

        logger.info(f"Total videos discovered across all subreddits: {len(all_videos)}")
        return all_videos

    async def _scrape_subreddit(
        self,
        subreddit: str,
        cookies: Dict[str, str],
        limit: int = 50,
        sort: str = "hot",
    ) -> List[DiscoveredVideo]:
        """
        Scrape a single subreddit's JSON feed. One request.

        Args:
            subreddit: Subreddit name without r/ prefix.
            cookies: Reddit session cookies (name -> value).
            limit: Max posts to fetch (Reddit caps at 100 per request).
            sort: Listing sort — "hot", "new", "top", "rising".

        Raises:
            _RedditBlocked: on HTTP 403 (stale cookies / rotated session).
            Exception: on any other failure (treated as transient upstream).
        """
        url = f"{REDDIT_BASE_URL}/r/{subreddit}/{sort}.json"
        params = {"limit": min(limit, 100), "raw_json": 1}

        response = await self._session.get(url, params=params, cookies=cookies)

        if response.status_code == 403:
            raise _RedditBlocked(f"HTTP 403 for r/{subreddit}")
        response.raise_for_status()

        data = response.json()
        posts = data.get("data", {}).get("children", [])

        videos: List[DiscoveredVideo] = []
        for post in posts:
            video = self._parse_post(post.get("data", {}), subreddit)
            if video is not None:
                videos.append(video)

        return videos

    # ------------------------------------------------------------------
    # Parsing layer — UNCHANGED from the original provider
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def validate_connection(self) -> bool:
        """Test that Reddit's JSON listings are reachable with our cookies."""
        cookies = self._load_cookies()
        if cookies is None or not self._subreddits:
            return False
        try:
            response = await self._session.get(
                f"{REDDIT_BASE_URL}/r/{self._subreddits[0]}/hot.json",
                params={"limit": 1, "raw_json": 1},
                cookies=cookies,
            )
            return response.status_code == 200
        except Exception:
            return False

    async def close(self):
        """Close the HTTP session. Call on shutdown."""
        try:
            await self._session.close()
        except Exception:
            pass


class _RedditBlocked(Exception):
    """Raised on HTTP 403 — cookies stale or session rotated."""
    pass
