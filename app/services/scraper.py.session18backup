"""
WatchDawg Scraper Orchestrator.

This is the brain of the discovery pipeline. It:
1. Calls a provider's fetch_posts() to discover new videos.
2. Deduplicates against videos already in the database.
3. Filters out anything on the user's skip list (via HMAC hash lookup).
4. Inserts new discoveries into the videos table with status "pending".

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

import logging
import re
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Video, SkipListEntry
from app.providers.base import BaseProvider, DiscoveredVideo
from app.hashing import hmac_hash

logger = logging.getLogger(__name__)


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


class ScrapeResult:
    """Summary of a scrape run for logging and API responses."""

    def __init__(self):
        self.discovered: int = 0  # Total posts fetched from provider
        self.duplicates: int = 0  # Already in the database
        self.skipped: int = 0  # On the skip list
        self.new: int = 0  # Successfully inserted as new entries
        self.errors: int = 0  # Failed to insert

    def __repr__(self) -> str:
        return (
            f"<ScrapeResult discovered={self.discovered} new={self.new} "
            f"dupes={self.duplicates} skipped={self.skipped} errors={self.errors}>"
        )

    def to_dict(self) -> dict:
        return {
            "discovered": self.discovered,
            "duplicates": self.duplicates,
            "skipped": self.skipped,
            "new": self.new,
            "errors": self.errors,
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
        # This catches the case where the same Vimeo video appears in two different
        # channels under the same numeric ID — before it reaches the resolver.
        is_vimeo = provider.provider_name == "vimeo"
        global_vimeo_ids: set = set()
        if is_vimeo:
            global_vimeo_ids = await self._get_global_vimeo_numeric_ids()
            logger.debug(
                f"Global Vimeo numeric ID set loaded: {len(global_vimeo_ids)} entries"
            )

        # Step 4: Filter and insert
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
            # A video with source_post_id "vimeo_99999" in Channel A is the
            # same physical video as "vimeo_99999" in Channel B — block it.
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

            # Insert as new pending video using a savepoint so a UNIQUE
            # constraint violation on one row never rolls back the whole batch.
            inserted = await self._insert_video(video, channel_id=effective_channel_id)
            if inserted:
                result.new += 1
                # Track this numeric ID so subsequent videos in the same batch
                # don't slip through the global check before the DB is committed.
                if is_vimeo:
                    numeric_id = _extract_vimeo_numeric_id(video.source_post_id)
                    if numeric_id:
                        global_vimeo_ids.add(numeric_id)
            else:
                # Video exists globally under a different channel — duplicate
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

        Used for cross-channel duplicate detection at ingest time. The same
        Vimeo video can be curated by multiple channels under the same numeric
        ID, which the per-channel source_post_id check would miss.

        Extracts the numeric portion from source_post_ids formatted as
        "vimeo_{numeric_id}". Non-matching rows are silently skipped.
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
        on one row only rolls back that single row, not the entire batch.
        Without savepoints, a session-level rollback() after an IntegrityError
        would silently discard all previously flushed-but-uncommitted inserts
        in the same batch.

        Returns True if inserted successfully, False if a duplicate existed
        globally (different channel curated the same video).
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
            # begin_nested() creates a savepoint. On IntegrityError only this
            # savepoint is rolled back — all prior inserts in the batch survive.
            async with self._db.begin_nested():
                self._db.add(db_video)
                await self._db.flush()
            return True
        except IntegrityError:
            # Savepoint rolled back — session is still alive, batch continues.
            logger.debug(
                f"Duplicate globally (different channel): {video.source_post_id}"
            )
            return False
