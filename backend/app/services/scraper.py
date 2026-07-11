"""
WatchDawg Scraper Orchestrator.

This is the brain of the discovery pipeline. It:
1. Calls a provider's fetch_posts() to discover new videos.
2. Deduplicates against videos already in the database.
3. Filters out anything on the user's skip list (via HMAC hash lookup).
4. Inserts new discoveries into the videos table with status "pending".

For Reddit sources specifically, the scraper checks whether each discovered
video can be played directly (v.redd.it native video via its reddit.com
post permalink, YouTube links, Vimeo links). If it CAN be played directly
it goes into the feed as normal. If it CANNOT (e.g. Redgifs or other
hostile CDNs), it is auto-downloaded via yt-dlp into the Library downloads
tree — Private/Reddit/<subreddit_name>/ for locked channels,
Public/Reddit/<subreddit_name>/ for unlocked ones — and stored as a
Library file only. It never appears in the feed. This sidesteps CDN
Referer/auth issues permanently for non-playable Reddit sources.

Session 60 fixes (first run with real Reddit data):
- _get_reddit_download_dir referenced settings.music_videos_path, which
  was renamed to downloads_path pre-Milestone D — every auto-download
  was skipped with "no download dir available". Now lock-aware:
  locked channel → private_downloads_path, unlocked → public_downloads_path.
- reddit.com added to REDDIT_DIRECTLY_PLAYABLE_DOMAINS: the provider
  stores v.redd.it posts as reddit.com post permalinks (so yt-dlp gets
  muxed audio+video), which were being misrouted to the (broken)
  auto-download path instead of the feed.
- Domain normalizer used str.lstrip("www."), which strips a character
  SET, not a prefix — replaced with str.removeprefix("www.").

The orchestrator doesn't care which provider it's talking to — it works
with any BaseProvider implementation through the standard interface.

Vimeo-specific dedup (Phase 3):
  Beyond the per-channel source_post_id check, Vimeo videos are also
  checked globally by their raw numeric Vimeo ID. The same video often
  appears in multiple Vimeo channels under the same numeric ID but
  different source_post_id values (because source_post_id encodes the
  channel slug). This global check catches cross-channel duplicates at
  ingest time — before they ever reach the resolution queue.

Usage:
    from app.services.scraper import ScraperService
    from app.providers.reddit import RedditProvider

    scraper = ScraperService(db_session)
    results = await scraper.run(RedditProvider())
"""

import asyncio
import logging
import os
import re
from typing import List, Optional
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Video, SkipListEntry, Favorite, Channel
from app.providers.base import BaseProvider, DiscoveredVideo
from app.hashing import hmac_hash
from app.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Reddit auto-download configuration
# ---------------------------------------------------------------------------

# Domains that can be played directly through the feed without downloading.
# Anything NOT in this set from a Reddit source will be auto-downloaded.
REDDIT_DIRECTLY_PLAYABLE_DOMAINS = {
    # Native Reddit video — the provider stores these as reddit.com post
    # permalinks (NOT v.redd.it URLs) so yt-dlp extracts the full muxed
    # stream from the post page. v.redd.it kept for direct-link posts.
    "reddit.com",
    "www.reddit.com",
    "old.reddit.com",
    "v.redd.it",
    # YouTube — resolved via yt-dlp + DASH manifest, works great
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "youtu.be",
    # Vimeo — resolved via yt-dlp, works great
    "vimeo.com",
    "www.vimeo.com",
}

# Per-subreddit cap on auto-downloaded files.
# When a subreddit folder hits this count, new downloads are skipped.
REDDIT_DOWNLOAD_CAP_PER_SUB = 500


def _extract_vimeo_numeric_id(source_post_id: str) -> Optional[str]:
    """
    Extract the raw numeric Vimeo video ID from a source_post_id string.

    The Vimeo provider sets source_post_id as "vimeo_{numeric_id}" —
    e.g. "vimeo_123456789". This function returns the numeric portion
    so we can do a global cross-channel dedup check.

    Returns None for non-Vimeo providers or malformed IDs.
    """
    m = re.match(r'^vimeo_(\d+)$', source_post_id)
    return m.group(1) if m else None


def _reddit_url_is_directly_playable(source_url: str) -> bool:
    """
    Return True if a Reddit post URL points to a domain we can resolve
    and play directly in the feed (YouTube, Vimeo, Reddit-hosted video).

    Return False for everything else (Redgifs, Streamable, direct .mp4
    on hostile CDNs, etc.) — those will be auto-downloaded instead.
    """
    try:
        parsed = urlparse(source_url)
        # removeprefix, NOT lstrip: lstrip("www.") strips any leading
        # run of the characters {w, .}, not the literal prefix "www."
        domain = parsed.netloc.lower().removeprefix("www.")
        # Check against our known-playable set (strip www. for comparison)
        for playable in REDDIT_DIRECTLY_PLAYABLE_DOMAINS:
            if domain == playable.removeprefix("www.") or domain == playable:
                return True
        return False
    except Exception:
        return False


def _sanitize_folder_name(name: str) -> str:
    """
    Sanitize a channel/subreddit name for use as a filesystem directory name.
    Strips characters invalid on Linux/Windows, collapses whitespace.
    """
    safe = re.sub(r'[<>:"/\\|?*]', '', name).strip()
    safe = re.sub(r'\s+', ' ', safe)
    return safe if safe else "Reddit"


def _count_files_in_dir(directory: str) -> int:
    """Count video files in a directory (non-recursive, direct children only)."""
    if not os.path.isdir(directory):
        return 0
    video_exts = {".mp4", ".mkv", ".webm", ".m4v"}
    count = 0
    try:
        for entry in os.scandir(directory):
            if entry.is_file():
                _, ext = os.path.splitext(entry.name)
                if ext.lower() in video_exts:
                    count += 1
    except OSError:
        pass
    return count


class ScrapeResult:
    """Summary of a scrape run for logging and API responses."""

    def __init__(self):
        self.discovered: int = 0      # Total posts fetched from provider
        self.duplicates: int = 0      # Already in the database
        self.skipped: int = 0         # On the skip list
        self.new: int = 0             # Successfully inserted as new feed entries
        self.errors: int = 0          # Failed to insert
        self.downloaded: int = 0      # Auto-downloaded to Library (Reddit non-playable)
        self.download_skipped: int = 0  # Skipped download (cap hit or already exists)

    def __repr__(self) -> str:
        return (
            f"<ScrapeResult discovered={self.discovered} new={self.new} "
            f"dupes={self.duplicates} skipped={self.skipped} "
            f"downloaded={self.downloaded} errors={self.errors}>"
        )

    def to_dict(self) -> dict:
        return {
            "discovered": self.discovered,
            "duplicates": self.duplicates,
            "skipped": self.skipped,
            "new": self.new,
            "errors": self.errors,
            "downloaded": self.downloaded,
            "download_skipped": self.download_skipped,
        }


class ScraperService:
    """
    Orchestrates the scraping pipeline for any provider.
    """

    def __init__(self, db_session: AsyncSession):
        self._db = db_session

    async def run(
        self,
        provider: BaseProvider,
        limit: int = 500,
        channel_id: Optional[int] = None,
    ) -> ScrapeResult:
        """
        Execute a full scrape cycle for the given provider.

        For Reddit sources:
        - Directly playable URLs (YouTube, Vimeo, Reddit-hosted video)
          → inserted as feed entries with status "pending" as normal.
        - Non-playable URLs (Redgifs etc.) → auto-downloaded to Library,
          never inserted into the feed.

        For all other providers: behaviour is unchanged.

        Args:
            provider: Any BaseProvider implementation.
            limit: Max posts to fetch from the provider.
            channel_id: Optional Channel.id to link discovered videos to.
                        If the provider has a channel_id attribute, that
                        takes precedence.

        Returns:
            A ScrapeResult summarizing what happened.
        """
        result = ScrapeResult()

        # Use channel_id from provider if available
        effective_channel_id = getattr(provider, "channel_id", None) or channel_id

        is_reddit = provider.provider_name == "reddit"

        # For Reddit auto-downloads: resolve the subreddit subfolder path once.
        reddit_download_dir = None
        if is_reddit and effective_channel_id is not None:
            reddit_download_dir = await self._get_reddit_download_dir(effective_channel_id)

        # Step 1: Fetch posts from the provider
        logger.info(f"Starting scrape with provider: {provider.provider_name}")
        discovered = await provider.fetch_posts(limit=limit)
        result.discovered = len(discovered)
        logger.info(f"Provider returned {result.discovered} posts")

        if not discovered:
            return result

        # Step 2: Load existing post IDs scoped to this channel to avoid
        # false duplicates across channels sharing a provider name (e.g. Vimeo).
        existing_ids = await self._get_existing_post_ids(
            provider.provider_name, effective_channel_id
        )
        logger.debug(f"Found {len(existing_ids)} existing videos for this channel")

        # Step 3: Load the skip list hashes for filtering
        skip_hashes = await self._get_skip_list_hashes(provider.provider_name)
        logger.debug(f"Skip list contains {len(skip_hashes)} entries")

        # Step 3b (Vimeo only): Load global Vimeo numeric IDs for cross-channel dedup.
        is_vimeo = provider.provider_name == "vimeo"
        global_vimeo_ids: set = set()
        if is_vimeo:
            global_vimeo_ids = await self._get_global_vimeo_numeric_ids()
            logger.debug(
                f"Global Vimeo numeric ID set loaded: {len(global_vimeo_ids)} entries"
            )

        # Step 4: Filter and insert / download
        for video in discovered:
            # Dedup check — scoped to this channel
            if video.source_post_id in existing_ids:
                result.duplicates += 1
                continue

            # Skip list check (compare HMAC hashes, no decryption needed)
            post_hash = hmac_hash(video.source_post_id)
            if post_hash in skip_hashes:
                result.skipped += 1
                continue

            # Vimeo cross-channel dedup: check the raw numeric ID globally.
            if is_vimeo:
                numeric_id = _extract_vimeo_numeric_id(video.source_post_id)
                if numeric_id and numeric_id in global_vimeo_ids:
                    logger.debug(
                        f"Vimeo cross-channel duplicate blocked at ingest: "
                        f"{video.source_post_id} (numeric ID {numeric_id} "
                        f"already exists in another channel)"
                    )
                    result.duplicates += 1
                    continue

            # Reddit-specific routing: playable → feed, non-playable → download
            if is_reddit:
                if _reddit_url_is_directly_playable(video.source_url):
                    # Goes into feed as normal pending video
                    logger.debug(
                        f"Reddit: directly playable URL — inserting into feed: "
                        f"{video.source_url[:80]}"
                    )
                    inserted = await self._insert_video(video, channel_id=effective_channel_id)
                    if inserted:
                        result.new += 1
                        existing_ids.add(video.source_post_id)
                    else:
                        result.duplicates += 1
                else:
                    # Non-playable — auto-download to Library
                    logger.info(
                        f"Reddit: non-playable URL — queuing auto-download: "
                        f"{video.source_url[:80]}"
                    )
                    downloaded = await self._reddit_auto_download(
                        video=video,
                        channel_id=effective_channel_id,
                        download_dir=reddit_download_dir,
                        result=result,
                    )
                    if downloaded:
                        # Track this post ID so it won't be re-downloaded on
                        # next scrape (even though it's not in the videos table)
                        existing_ids.add(video.source_post_id)
                continue

            # Non-Reddit: standard insert into feed
            inserted = await self._insert_video(video, channel_id=effective_channel_id)
            if inserted:
                result.new += 1
                if is_vimeo:
                    numeric_id = _extract_vimeo_numeric_id(video.source_post_id)
                    if numeric_id:
                        global_vimeo_ids.add(numeric_id)
            else:
                result.duplicates += 1

        # Commit all successfully inserted rows
        try:
            await self._db.commit()
        except Exception as e:
            logger.error(f"Failed to commit scrape batch: {e}")
            await self._db.rollback()
            result.errors += result.new
            result.new = 0

        logger.info(f"Scrape complete: {result}")
        return result

    async def _get_reddit_download_dir(self, channel_id: int) -> Optional[str]:
        """
        Build the Library download path for this Reddit channel:
          locked channel   → {private_downloads_path}/Reddit/<subreddit_name>/
          unlocked channel → {public_downloads_path}/Reddit/<subreddit_name>/
        Creates the directory if it doesn't exist.
        Returns None on failure.
        """
        try:
            stmt = select(Channel).where(Channel.id == channel_id)
            result = await self._db.execute(stmt)
            channel = result.scalar_one_or_none()
            if channel is None:
                return None

            # Extract subreddit name from channel name or URL
            # Channel name is typically "r/SubName" — strip the r/ prefix
            sub_name = channel.name
            match = re.search(r'r/([A-Za-z0-9_]+)', sub_name)
            if match:
                sub_name = match.group(1)

            safe_sub = _sanitize_folder_name(sub_name)
            # Lock-aware base: locked sources live in the PIN-protected
            # Private tree, unlocked ones in Public — same rule as every
            # other Library download.
            if getattr(channel, "locked", False):
                base_dir = settings.private_downloads_path
            else:
                base_dir = settings.public_downloads_path
            download_dir = os.path.join(base_dir, "Reddit", safe_sub)

            os.makedirs(download_dir, exist_ok=True)
            logger.info(f"Reddit download dir: {download_dir}")
            return download_dir

        except Exception as e:
            logger.error(f"Failed to build Reddit download dir for channel {channel_id}: {e}")
            return None

    async def _reddit_auto_download(
        self,
        video: DiscoveredVideo,
        channel_id: Optional[int],
        download_dir: Optional[str],
        result: ScrapeResult,
    ) -> bool:
        """
        Auto-download a non-playable Reddit video directly to the Library.

        Flow:
        1. Check per-subreddit file cap — skip if hit.
        2. Insert a Video + Favorite DB record (download_status="downloading").
        3. Run yt-dlp download in a thread.
        4. Update Favorite with result (complete/failed) and local_file_path.
        5. On success, generate the sidecar thumbnail (Session 62) —
           off-thread, non-fatal on failure.

        Returns True if a download was attempted (success or fail),
        False if skipped (cap hit, no dir, already exists).
        """
        from app.routers.favorite import _download_sync, _build_filename, _find_downloaded_file
        import datetime

        if download_dir is None:
            logger.warning(
                f"Reddit auto-download: no download dir available — "
                f"skipping {video.source_url[:80]}"
            )
            result.download_skipped += 1
            return False

        # Check per-subreddit cap
        current_count = _count_files_in_dir(download_dir)
        if current_count >= REDDIT_DOWNLOAD_CAP_PER_SUB:
            logger.info(
                f"Reddit auto-download: cap hit ({current_count}/{REDDIT_DOWNLOAD_CAP_PER_SUB}) "
                f"for {download_dir} — skipping {video.title[:60]}"
            )
            result.download_skipped += 1
            return False

        # Build target filename
        filename = _build_filename(video.title, video.artist)
        output_path = os.path.join(download_dir, filename)

        # Check if file already exists on disk (re-scrape after restart)
        existing = _find_downloaded_file(output_path)
        if existing:
            logger.info(
                f"Reddit auto-download: file already on disk — "
                f"skipping re-download: {existing}"
            )
            result.download_skipped += 1
            return False

        # Insert Video record (resolution_status="downloaded" — never enters resolve queue)
        db_video = Video(
            source_provider=video.source_provider,
            source_post_id=video.source_post_id,
            source_url=video.source_url,
            title=video.title,
            artist=video.artist,
            thumbnail_url=video.thumbnail_url,
            duration_seconds=video.duration_seconds,
            reddit_score=video.score,
            resolution_status="downloaded",  # special status — excluded from feed & resolve
            channel_id=channel_id,
        )

        try:
            async with self._db.begin_nested():
                self._db.add(db_video)
                await self._db.flush()
        except IntegrityError:
            logger.debug(
                f"Reddit auto-download: duplicate video record — "
                f"skipping {video.source_post_id}"
            )
            result.duplicates += 1
            return False

        # Insert Favorite record linked to the video
        favorite = Favorite(
            video_id=db_video.id,
            download_status="downloading",
        )
        self._db.add(favorite)
        await self._db.flush()
        await self._db.commit()

        logger.info(
            f"Reddit auto-download: starting yt-dlp for "
            f"video_id={db_video.id} | {video.source_url[:80]}"
        )

        # Run yt-dlp in a thread — non-blocking
        success, error_msg = await asyncio.to_thread(
            _download_sync, video.source_url, output_path
        )

        # Refresh records after thread completes
        downloaded_path = None  # Session 62: captured for thumbnail generation
        async with self._db.begin_nested():
            fav_stmt = select(Favorite).where(Favorite.id == favorite.id)
            fav_result = await self._db.execute(fav_stmt)
            fav = fav_result.scalar_one_or_none()

            if fav is not None:
                if success:
                    actual_path = _find_downloaded_file(output_path)
                    fav.download_status = "complete"
                    fav.local_file_path = actual_path or output_path
                    fav.downloaded_at = datetime.datetime.utcnow()
                    fav.download_error = None
                    downloaded_path = fav.local_file_path
                    logger.info(
                        f"Reddit auto-download: COMPLETE — "
                        f"video_id={db_video.id} path={fav.local_file_path}"
                    )
                    result.downloaded += 1
                else:
                    fav.download_status = "failed"
                    fav.download_error = error_msg or "Unknown error"
                    logger.error(
                        f"Reddit auto-download: FAILED — "
                        f"video_id={db_video.id} error={error_msg}"
                    )
                    result.errors += 1

        await self._db.commit()

        # ------------------------------------------------------------------
        # Session 62: generate the sidecar thumbnail immediately after a
        # successful download, so new Reddit files land in the Files on Disk
        # view with a preview already made. Runs off-thread (same as the
        # download itself) so scraping never blocks. Non-fatal: on failure we
        # log and move on — the Generate Thumbnails button's folder-walk pass
        # (Session 62, library.py) picks up any stragglers.
        # ------------------------------------------------------------------
        if success and downloaded_path and os.path.isfile(downloaded_path):
            from app.routers.library import _generate_thumb_sync, _thumb_path_for
            thumb_path = _thumb_path_for(downloaded_path)
            if not os.path.isfile(thumb_path):
                thumb_ok = await asyncio.to_thread(
                    _generate_thumb_sync, downloaded_path, thumb_path
                )
                if thumb_ok:
                    logger.info(
                        f"Reddit auto-download: sidecar thumbnail generated "
                        f"for video_id={db_video.id}"
                    )
                else:
                    logger.warning(
                        f"Reddit auto-download: thumbnail generation failed "
                        f"for video_id={db_video.id} — the Generate Thumbnails "
                        f"folder-walk pass will retry it"
                    )

        return True

    async def _get_existing_post_ids(
        self, provider_name: str, channel_id: Optional[int] = None
    ) -> set:
        """
        Fetch all source_post_id values already in the videos table.

        Scoped to channel_id when provided — prevents false duplicates
        across Vimeo channels that all share source_provider='vimeo'.

        Skip list remains global — a skipped video stays skipped
        regardless of which channel tries to re-ingest it.
        """
        if channel_id is not None:
            stmt = select(Video.source_post_id).where(
                Video.source_provider == provider_name,
                Video.channel_id == channel_id,
            )
        else:
            stmt = select(Video.source_post_id).where(
                Video.source_provider == provider_name
            )
        rows = await self._db.execute(stmt)
        return {row[0] for row in rows.fetchall()}

    async def _get_global_vimeo_numeric_ids(self) -> set:
        """
        Fetch the raw numeric Vimeo video IDs for all Vimeo videos currently
        in the database, across ALL channels.
        """
        stmt = select(Video.source_post_id).where(
            Video.source_provider == "vimeo"
        )
        rows = await self._db.execute(stmt)
        ids = set()
        for (post_id,) in rows.fetchall():
            numeric_id = _extract_vimeo_numeric_id(post_id)
            if numeric_id:
                ids.add(numeric_id)
        return ids

    async def _get_skip_list_hashes(self, provider_name: str) -> set:
        """
        Fetch all HMAC hashes from the skip list for the given provider.
        We compare hashes, never decrypt — this keeps the skip list
        private even during query operations.
        """
        stmt = select(SkipListEntry.source_post_id_hash).where(
            SkipListEntry.source_provider == provider_name
        )
        rows = await self._db.execute(stmt)
        return {row[0] for row in rows.fetchall()}

    async def _insert_video(
        self,
        video: DiscoveredVideo,
        channel_id: Optional[int] = None,
    ) -> bool:
        """
        Insert a newly discovered video into the database.
        Status starts as 'pending' — resolution happens via the resolver.

        Uses a savepoint (begin_nested) so that a UNIQUE constraint violation
        on one row never rolls back the whole batch.

        Returns True if inserted successfully, False if duplicate.
        """
        db_video = Video(
            source_provider=video.source_provider,
            source_post_id=video.source_post_id,
            source_url=video.source_url,
            title=video.title,
            artist=video.artist,
            thumbnail_url=video.thumbnail_url,
            duration_seconds=video.duration_seconds,
            reddit_score=video.score,
            resolution_status="pending",
            channel_id=channel_id,
        )

        try:
            async with self._db.begin_nested():
                self._db.add(db_video)
                await self._db.flush()
            return True
        except IntegrityError:
            logger.debug(
                f"Duplicate globally (different channel): {video.source_post_id}"
            )
            return False
