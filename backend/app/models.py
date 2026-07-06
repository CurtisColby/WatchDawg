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

Milestone I additions:
- LiveTvSource         — tracks imported M3U playlist sources; channels reference
                         source_m3u matching LiveTvSource.url. Supports enable/disable
                         per-source and bulk-delete of all channels from a source.

Session 35 additions:
- LiveTvChannel.is_favorite  — user-starred channel; powers Favorites row on Android.
                               Migration: ALTER TABLE live_tv_channels ADD COLUMN is_favorite INTEGER DEFAULT 0
- LiveTvChannel.sort_order   — group sort priority (lower = shown first in sidebar/grid).
                               Migration: ALTER TABLE live_tv_channels ADD COLUMN sort_order INTEGER DEFAULT 999

Session 38 additions:
- Channel.parent_channel_id  — links playlist child channels back to the YouTube channel
                               root that spawned them during full-channel enumeration.
                               NULL for all standalone channels (all existing data unaffected).
                               Migration: ALTER TABLE channels ADD COLUMN parent_channel_id INTEGER DEFAULT NULL

Session 52 additions:
- Video.resolved_audio_url   — stores the separately-resolved audio-only HLS URL for
                               Vimeo split-stream content (video-only + audio-only HLS).
                               NULL for combined streams (YouTube MP4, local files, etc.).
                               Migration: ALTER TABLE videos ADD COLUMN resolved_audio_url TEXT
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
    __tablename__ = "channels"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    channel_type = Column(String(50), nullable=False, index=True)
    url = Column(Text, nullable=False)
    unique_key = Column(String(500), nullable=False, unique=True)
    enabled = Column(Boolean, nullable=False, default=True)
    locked = Column(Boolean, nullable=False, default=False)
    category = Column(String(50), nullable=False, default="general")
    genre_tags = Column(Text, nullable=False, default="")

    # Session 38: links a playlist child back to the YouTube root channel that
    # enumerated it.  NULL for all standalone channels and all pre-existing rows.
    # Migration: ALTER TABLE channels ADD COLUMN parent_channel_id INTEGER DEFAULT NULL
    parent_channel_id = Column(Integer, nullable=True, default=None)

    last_scraped_at = Column(DateTime, nullable=True)
    last_scrape_count = Column(Integer, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow)

    def __repr__(self) -> str:
        return (
            f"<Channel id={self.id} name='{self.name}' "
            f"type={self.channel_type} category={self.category} "
            f"tags='{self.genre_tags}' enabled={self.enabled} locked={self.locked} "
            f"parent={self.parent_channel_id}>"
        )


class Video(Base):
    __tablename__ = "videos"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_provider = Column(String(50), nullable=False, index=True)
    source_post_id = Column(String(255), nullable=False, unique=True)
    source_url = Column(Text, nullable=False)
    channel_id = Column(Integer, ForeignKey("channels.id"), nullable=True, index=True)
    title = Column(String(500), nullable=False, default="Unknown Title")
    artist = Column(String(255), nullable=True)
    thumbnail_url = Column(Text, nullable=True)
    duration_seconds = Column(Float, nullable=True)
    reddit_score = Column(Integer, nullable=True)
    resolved_stream_url = Column(Text, nullable=True)
    resolved_audio_url = Column(Text, nullable=True)   # split-stream audio (Vimeo HLS, etc.)
    resolved_format = Column(String(50), nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    resolution_status = Column(String(20), nullable=False, default="pending")
    resolution_error = Column(Text, nullable=True)
    tmdb_poster_url = Column(Text, nullable=True)
    tmdb_description = Column(Text, nullable=True)
    tmdb_year = Column(Integer, nullable=True)
    tmdb_rating = Column(Float, nullable=True)
    tmdb_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.datetime.utcnow,
        onupdate=datetime.datetime.utcnow,
    )

    favorite = relationship("Favorite", back_populates="video", uselist=False)
    channel = relationship("Channel", backref="videos")
    watch_history = relationship("WatchHistory", back_populates="video", uselist=False)
    watchlist_entry = relationship("Watchlist", back_populates="video", uselist=False)

    def __repr__(self) -> str:
        return f"<Video id={self.id} title='{self.title[:40]}' status={self.resolution_status}>"


class SkipListEntry(Base):
    __tablename__ = "skip_list"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_post_id_encrypted = Column(Text, nullable=False)
    source_post_id_hash = Column(String(64), nullable=False, unique=True, index=True)
    source_provider = Column(String(50), nullable=False)
    skipped_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow)

    def __repr__(self) -> str:
        return f"<SkipListEntry id={self.id} provider={self.source_provider}>"


class Favorite(Base):
    __tablename__ = "favorites"

    id = Column(Integer, primary_key=True, autoincrement=True)
    video_id = Column(Integer, ForeignKey("videos.id"), nullable=False, unique=True)
    local_file_path = Column(Text, nullable=True)
    download_status = Column(String(20), nullable=False, default="none")
    downloaded_at = Column(DateTime, nullable=True)
    download_error = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow)

    video = relationship("Video", back_populates="favorite")

    def __repr__(self) -> str:
        return f"<Favorite id={self.id} video_id={self.video_id} status={self.download_status}>"


class WatchHistory(Base):
    __tablename__ = "watch_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    video_id = Column(
        Integer, ForeignKey("videos.id", ondelete="CASCADE"),
        nullable=False, unique=True, index=True
    )
    position_seconds = Column(Float, nullable=False, default=0.0)
    duration_seconds = Column(Float, nullable=True)
    completed = Column(Boolean, nullable=False, default=False)
    last_watched_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow)

    video = relationship("Video", back_populates="watch_history")

    def __repr__(self) -> str:
        return (
            f"<WatchHistory video_id={self.video_id} "
            f"pos={self.position_seconds:.0f}s completed={self.completed}>"
        )


class Watchlist(Base):
    __tablename__ = "watchlist"

    id = Column(Integer, primary_key=True, autoincrement=True)
    video_id = Column(
        Integer, ForeignKey("videos.id", ondelete="CASCADE"),
        nullable=False, unique=True, index=True
    )
    added_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow)

    video = relationship("Video", back_populates="watchlist_entry")

    def __repr__(self) -> str:
        return f"<Watchlist video_id={self.video_id}>"


class LiveTvSource(Base):
    """
    Tracks imported M3U playlist sources for Live TV (Session 35).

    url matches LiveTvChannel.source_m3u for channels imported from this source.
    Manually-added channels (source_m3u = None) have no source record.
    """
    __tablename__ = "live_tv_sources"

    id = Column(Integer, primary_key=True, autoincrement=True)
    label = Column(String(300), nullable=False)
    url = Column(Text, nullable=False, unique=True)
    enabled = Column(Boolean, nullable=False, default=True)
    channel_count = Column(Integer, nullable=False, default=0)
    # Session 44: optional group filter — only import channels whose group-title
    # matches this value (case-insensitive). NULL = import all groups.
    # Migration: ALTER TABLE live_tv_sources ADD COLUMN group_filter TEXT DEFAULT NULL
    group_filter = Column(Text, nullable=True, default=None)
    created_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow)
    last_imported_at = Column(DateTime, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<LiveTvSource id={self.id} label='{self.label}' "
            f"enabled={self.enabled} channels={self.channel_count} "
            f"group_filter={self.group_filter}>"
        )


class LiveTvChannel(Base):
    """
    An IPTV / live stream channel entry. Fully implemented in Milestone I.

    channel_type values:
      - "real"    — genuine IPTV stream from an M3U source
      - "pseudo"  — WatchDawg pseudo-linear channel built from library content

    Session 35 additions:
      is_favorite  — user-starred; powers the Favorites row on Android TV.
                     Migration: ALTER TABLE live_tv_channels ADD COLUMN is_favorite INTEGER DEFAULT 0

      sort_order   — group display order (lower = first). Set per group_name via
                     PATCH /live-tv/groups/reorder. Default 999 = alphabetical fallback.
                     Migration: ALTER TABLE live_tv_channels ADD COLUMN sort_order INTEGER DEFAULT 999
    """
    __tablename__ = "live_tv_channels"

    id = Column(Integer, primary_key=True, autoincrement=True)

    name = Column(String(200), nullable=False)
    logo_url = Column(Text, nullable=True)
    stream_url = Column(Text, nullable=True)
    group_name = Column(String(100), nullable=True)   # M3U group-title
    channel_type = Column(String(20), nullable=False, default="real")  # real | pseudo

    # Health tracking — updated by the 15-minute background probe
    is_online = Column(Boolean, nullable=True)         # None = not yet checked
    last_checked = Column(DateTime, nullable=True)

    # Which M3U source this came from — matches LiveTvSource.url
    source_m3u = Column(Text, nullable=True)

    # User-starred channel — shown in Favorites row on Android TV
    # Migration: ALTER TABLE live_tv_channels ADD COLUMN is_favorite INTEGER DEFAULT 0
    is_favorite = Column(Boolean, nullable=False, default=False)

    # Group display sort order (lower = earlier in sidebar + grid)
    # Migration: ALTER TABLE live_tv_channels ADD COLUMN sort_order INTEGER DEFAULT 999
    sort_order = Column(Integer, nullable=False, default=999)

    created_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow)

    def __repr__(self) -> str:
        return (
            f"<LiveTvChannel id={self.id} name='{self.name}' "
            f"online={self.is_online} fav={self.is_favorite}>"
        )


class Provider(Base):
    __tablename__ = "providers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True)
    provider_type = Column(String(50), nullable=False)
    config_encrypted = Column(Text, nullable=True)
    enabled = Column(Boolean, nullable=False, default=True)
    last_scraped_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow)

    def __repr__(self) -> str:
        return f"<Provider name='{self.name}' type={self.provider_type} enabled={self.enabled}>"
