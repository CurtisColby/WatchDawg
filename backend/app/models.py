"""
WatchDawg Database Models.

Security notes:
- Fields suffixed with `_encrypted` are Fernet-encrypted at rest.
  Use app.encryption.encrypt_value() before writing and
  app.encryption.decrypt_value() after reading.
- The skip_list stores source_post_id encrypted so that even if the
  database file is compromised, viewing habits remain private.
- Provider config_encrypted holds any API keys or tokens for that source.

Milestone B additions:
- Channel.category     — content category for grouping (general/movies/tv/etc.)
- Video.tmdb_*         — TMDb metadata fields (poster, description, year, rating)
- WatchHistory         — per-video watch position for resume / continue watching
- Watchlist            — "Watch Later" bookmarks, no PIN required
- LiveTvChannel        — IPTV channel entries (stub, filled out in Milestone I)

Milestone R-1 additions:
- Channel.genre_tags   — free-form comma-separated genre tags (e.g. "Nature,Documentary")
                         Powers the genre pill bar in each Android section screen.
                         Migration: ALTER TABLE channels ADD COLUMN genre_tags TEXT DEFAULT ''
"""

import datetime
from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    DateTime,
    Boolean,
    Float,
    ForeignKey,
    Index,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    """Base class for all ORM models."""
    pass


# ---------------------------------------------------------------------------
# Valid category values — used by Channel.category and enforced in the API.
# Adding a new category in the future is a UI-only change — no migration needed.
# ---------------------------------------------------------------------------
VALID_CATEGORIES = (
    "ccm",
    "chill",
    "sexy",
    "general",
    "movies",
    "tv",
    "nature",
    "music",
    "adult",
    "live_tv",
    "vimeo",
)


class Channel(Base):
    """
    A user-managed media source channel.

    Instead of hardcoding subreddits in .env, users add/remove channels
    through the Web UI or API. Each channel has a type that determines
    which provider handles scraping.

    Supported channel_type values:
      - reddit_subreddit  (e.g., identifier="SexyMusicVideos")
      - vimeo_channel     (e.g., url="https://vimeo.com/channels/eroticas/videos")
      - ytdlp_playlist    (e.g., url="https://www.youtube.com/playlist?list=...")
                          Generic catch-all for any yt-dlp-compatible playlist URL.

    PIN Lock:
      - locked=True means this channel's videos are hidden from feed, favorites,
        and library responses until a valid session token is provided in the
        X-WatchDawg-Token request header.
      - The lock is enforced exclusively server-side — the client never makes
        gating decisions.

    Category:
      - category groups channels for display in the web UI and Android app.
      - Valid values defined in VALID_CATEGORIES above.
      - Default is "general". Adding new categories requires no migration.

    Genre Tags (Milestone R-1):
      - genre_tags is a comma-separated string of free-form tags.
        Examples: "Nature,Documentary"  "Country,Classic"  "Horror,Thriller"
      - Tags are arbitrary — no enum, no validation list. Any string is valid.
      - A source can have zero tags (empty string) or as many as needed.
      - The Android genre pill bar for each section is built dynamically from
        whatever distinct tags exist in the DB for that category.
      - Migration: ALTER TABLE channels ADD COLUMN genre_tags TEXT DEFAULT ''
    """

    __tablename__ = "channels"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Display name (user-friendly label, e.g., "Eroticas Channel")
    name = Column(String(200), nullable=False)

    # Channel type determines which provider to use
    channel_type = Column(String(50), nullable=False, index=True)
    # e.g., "reddit_subreddit", "vimeo_channel", "ytdlp_playlist"

    # The URL or identifier for this channel
    # For Reddit: just the subreddit name (no r/ prefix)
    # For Vimeo/YouTube/other: the full URL
    url = Column(Text, nullable=False)

    # Unique key to prevent duplicate channel entries
    # Built from channel_type + normalized identifier
    unique_key = Column(String(500), nullable=False, unique=True)

    enabled = Column(Boolean, nullable=False, default=True)

    # PIN Lock — when True, this channel's videos are hidden unless the
    # caller supplies a valid X-WatchDawg-Token header issued by POST /auth/unlock.
    # Migration: ALTER TABLE channels ADD COLUMN locked INTEGER DEFAULT 0
    locked = Column(Boolean, nullable=False, default=False)

    # Content category for grouping in web UI and Android nav.
    # Migration: ALTER TABLE channels ADD COLUMN category TEXT DEFAULT 'general'
    # Valid values: see VALID_CATEGORIES tuple above.
    category = Column(String(50), nullable=False, default="general")

    # Free-form genre tags — comma-separated, e.g. "Nature,Documentary"
    # Powers the genre pill bar in each Android section screen.
    # Migration: ALTER TABLE channels ADD COLUMN genre_tags TEXT DEFAULT ''
    genre_tags = Column(Text, nullable=False, default="")

    last_scraped_at = Column(DateTime, nullable=True)
    last_scrape_count = Column(Integer, nullable=True)  # Videos found last scrape

    created_at = Column(
        DateTime, nullable=False, default=datetime.datetime.utcnow
    )

    def __repr__(self) -> str:
        return (
            f"<Channel id={self.id} name='{self.name}' "
            f"type={self.channel_type} category={self.category} "
            f"tags='{self.genre_tags}' enabled={self.enabled} locked={self.locked}>"
        )


class Video(Base):
    """
    A discovered video from any provider.

    The resolved_stream_url is the direct playable link extracted by yt-dlp.
    It has a TTL because platforms like YouTube expire stream URLs.

    Milestone B adds optional TMDb metadata fields. These are populated by
    the TMDb service when a video belongs to a movies/tv category channel.
    All TMDb fields are nullable — graceful fallback if no match found.
    """

    __tablename__ = "videos"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Source identification
    source_provider = Column(String(50), nullable=False, index=True)
    source_post_id = Column(String(255), nullable=False, unique=True)
    source_url = Column(Text, nullable=False)

    # Which channel discovered this video (nullable for legacy data)
    channel_id = Column(Integer, ForeignKey("channels.id"), nullable=True, index=True)

    # Metadata
    title = Column(String(500), nullable=False, default="Unknown Title")
    artist = Column(String(255), nullable=True)
    thumbnail_url = Column(Text, nullable=True)
    duration_seconds = Column(Float, nullable=True)
    reddit_score = Column(Integer, nullable=True)

    # Resolution state
    resolved_stream_url = Column(Text, nullable=True)
    resolved_format = Column(String(50), nullable=True)  # e.g., "mp4/1080p"
    resolved_at = Column(DateTime, nullable=True)
    resolution_status = Column(
        String(20), nullable=False, default="pending"
    )  # pending | resolved | failed | expired | downloaded

    # Error tracking — stores why resolution failed for debugging
    resolution_error = Column(Text, nullable=True)

    # TMDb metadata — populated for movies/tv category channels.
    # All nullable: TMDb lookup is best-effort, never blocks scraping.
    # Migration: ALTER TABLE videos ADD COLUMN tmdb_poster_url TEXT
    # Migration: ALTER TABLE videos ADD COLUMN tmdb_description TEXT
    # Migration: ALTER TABLE videos ADD COLUMN tmdb_year INTEGER
    # Migration: ALTER TABLE videos ADD COLUMN tmdb_rating REAL
    # Migration: ALTER TABLE videos ADD COLUMN tmdb_id INTEGER
    tmdb_poster_url = Column(Text, nullable=True)
    tmdb_description = Column(Text, nullable=True)
    tmdb_year = Column(Integer, nullable=True)
    tmdb_rating = Column(Float, nullable=True)
    tmdb_id = Column(Integer, nullable=True)

    # Timestamps
    created_at = Column(
        DateTime, nullable=False, default=datetime.datetime.utcnow
    )
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.datetime.utcnow,
        onupdate=datetime.datetime.utcnow,
    )

    # Relationships
    favorite = relationship("Favorite", back_populates="video", uselist=False)
    channel = relationship("Channel", backref="videos")
    watch_history = relationship("WatchHistory", back_populates="video", uselist=False)
    watchlist_entry = relationship("Watchlist", back_populates="video", uselist=False)

    __table_args__ = (
        Index("ix_videos_status_provider", "resolution_status", "source_provider"),
    )

    def __repr__(self) -> str:
        return f"<Video id={self.id} title='{self.title[:40]}' status={self.resolution_status}>"


class SkipListEntry(Base):
    """
    Videos the user has flagged to never see again.

    The source_post_id is stored ENCRYPTED so that viewing preferences
    remain private even if the database is accessed directly.
    Filtering works by decrypting entries in memory at query time.
    For performance at scale, we also store an HMAC hash for fast lookups
    without decrypting every row.
    """

    __tablename__ = "skip_list"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Encrypted source identifier — decrypt only in memory for comparison
    source_post_id_encrypted = Column(Text, nullable=False)

    # HMAC hash of the source_post_id for fast lookups without bulk decryption
    source_post_id_hash = Column(String(64), nullable=False, unique=True, index=True)

    # Which provider this came from (not sensitive — stored plaintext)
    source_provider = Column(String(50), nullable=False)

    skipped_at = Column(
        DateTime, nullable=False, default=datetime.datetime.utcnow
    )

    def __repr__(self) -> str:
        return f"<SkipListEntry id={self.id} provider={self.source_provider}>"


class Favorite(Base):
    """
    A video the user has bookmarked as a favorite.

    Milestone B: Favorite is now a bookmark only. Downloading is a separate
    explicit action via POST /favorite/{video_id}/download.

    download_status values:
      - "none"        — bookmarked only, no download requested
      - "pending"     — download queued
      - "downloading" — yt-dlp running
      - "complete"    — file saved to NAS
      - "failed"      — yt-dlp error (see download_error)

    local_file_path points to the file on the /music_videos mount.
    """

    __tablename__ = "favorites"

    id = Column(Integer, primary_key=True, autoincrement=True)
    video_id = Column(Integer, ForeignKey("videos.id"), nullable=False, unique=True)

    local_file_path = Column(Text, nullable=True)  # Populated after download completes
    download_status = Column(
        String(20), nullable=False, default="none"
    )  # none | pending | downloading | complete | failed
    downloaded_at = Column(DateTime, nullable=True)

    # Error tracking — stores why download failed for debugging
    download_error = Column(Text, nullable=True)

    created_at = Column(
        DateTime, nullable=False, default=datetime.datetime.utcnow
    )

    # Relationships
    video = relationship("Video", back_populates="favorite")

    def __repr__(self) -> str:
        return f"<Favorite id={self.id} video_id={self.video_id} status={self.download_status}>"


class WatchHistory(Base):
    """
    Per-video watch position for resume / continue watching.

    The Android app posts the current playback position every 10 seconds
    during HLS (seekable) playback. For split-stream (non-seekable) playback
    only a completion event is posted (position=duration, completed=True).

    This table powers:
      - Continue Watching row (Milestone D)
      - Resume banner on video open
      - Watched badge (completed=True)
      - Smart Shuffle deprioritization (Milestone H)

    Privacy: locked/adult channel content is NEVER returned by GET /history
    regardless of PIN state — enforced unconditionally at query time.
    """

    __tablename__ = "watch_history"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # One history record per video — upserted on each position update
    video_id = Column(
        Integer, ForeignKey("videos.id", ondelete="CASCADE"),
        nullable=False, unique=True, index=True
    )

    position_seconds = Column(Float, nullable=False, default=0.0)
    duration_seconds = Column(Float, nullable=True)

    # True when position_seconds >= 95% of duration_seconds
    completed = Column(Boolean, nullable=False, default=False)

    last_watched_at = Column(
        DateTime, nullable=False, default=datetime.datetime.utcnow
    )

    # Relationships
    video = relationship("Video", back_populates="watch_history")

    def __repr__(self) -> str:
        return (
            f"<WatchHistory video_id={self.video_id} "
            f"pos={self.position_seconds:.0f}s completed={self.completed}>"
        )


class Watchlist(Base):
    """
    'Watch Later' bookmarks — no PIN required to read or write.

    One entry per video. Adult-category videos are blocked from being added
    at the API level regardless of PIN state, keeping this list always safe
    to display without authentication.
    """

    __tablename__ = "watchlist"

    id = Column(Integer, primary_key=True, autoincrement=True)

    video_id = Column(
        Integer, ForeignKey("videos.id", ondelete="CASCADE"),
        nullable=False, unique=True, index=True
    )

    added_at = Column(
        DateTime, nullable=False, default=datetime.datetime.utcnow
    )

    # Relationships
    video = relationship("Video", back_populates="watchlist_entry")

    def __repr__(self) -> str:
        return f"<Watchlist video_id={self.video_id}>"


class LiveTvChannel(Base):
    """
    An IPTV / live stream channel entry.

    Stub table added in Milestone B. Fully implemented in Milestone I.

    channel_type values:
      - "real"    — genuine IPTV stream from an M3U source
      - "pseudo"  — WatchDawg pseudo-linear channel built from library content
    """

    __tablename__ = "live_tv_channels"

    id = Column(Integer, primary_key=True, autoincrement=True)

    name = Column(String(200), nullable=False)
    logo_url = Column(Text, nullable=True)
    stream_url = Column(Text, nullable=True)
    group_name = Column(String(100), nullable=True)  # M3U group-title
    channel_type = Column(String(20), nullable=False, default="real")  # real | pseudo

    # Health tracking — updated by the 15-minute background probe
    is_online = Column(Boolean, nullable=True)  # None = not yet checked
    last_checked = Column(DateTime, nullable=True)

    # Which M3U file this came from (null for manually added entries)
    source_m3u = Column(Text, nullable=True)

    created_at = Column(
        DateTime, nullable=False, default=datetime.datetime.utcnow
    )

    def __repr__(self) -> str:
        return (
            f"<LiveTvChannel id={self.id} name='{self.name}' "
            f"online={self.is_online}>"
        )


class Provider(Base):
    """
    Configuration for a media source provider.

    config_encrypted stores a JSON blob with any provider-specific settings,
    API keys, or tokens — ALL encrypted at rest via Fernet.
    """

    __tablename__ = "providers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True)
    provider_type = Column(String(50), nullable=False)  # reddit | vimeo | usenet | torrent

    # Encrypted JSON configuration (API keys, tokens, etc.)
    config_encrypted = Column(Text, nullable=True)

    enabled = Column(Boolean, nullable=False, default=True)
    last_scraped_at = Column(DateTime, nullable=True)

    created_at = Column(
        DateTime, nullable=False, default=datetime.datetime.utcnow
    )

    def __repr__(self) -> str:
        return f"<Provider name='{self.name}' type={self.provider_type} enabled={self.enabled}>"
