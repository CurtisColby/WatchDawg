"""
WatchDawg Database Models.

Security notes:
- Fields suffixed with `_encrypted` are Fernet-encrypted at rest.
  Use app.encryption.encrypt_value() before writing and
  app.encryption.decrypt_value() after reading.
- The skip_list stores source_post_id encrypted so that even if the
  database file is compromised, viewing habits remain private.
- Provider config_encrypted holds any API keys or tokens for that source.
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
    last_scraped_at = Column(DateTime, nullable=True)
    last_scrape_count = Column(Integer, nullable=True)  # Videos found last scrape

    created_at = Column(
        DateTime, nullable=False, default=datetime.datetime.utcnow
    )

    def __repr__(self) -> str:
        return f"<Channel id={self.id} name='{self.name}' type={self.channel_type} enabled={self.enabled}>"


class Video(Base):
    """
    A discovered video from any provider.

    The resolved_stream_url is the direct playable link extracted by yt-dlp.
    It has a TTL because platforms like YouTube expire stream URLs.
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
    )  # pending | resolved | failed | expired

    # Error tracking — stores why resolution failed for debugging
    resolution_error = Column(Text, nullable=True)

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
    A video the user has favorited / downloaded to local storage.

    local_file_path points to the file on the /music_videos mount.
    """

    __tablename__ = "favorites"

    id = Column(Integer, primary_key=True, autoincrement=True)
    video_id = Column(Integer, ForeignKey("videos.id"), nullable=False, unique=True)

    local_file_path = Column(Text, nullable=True)  # Populated after download completes
    download_status = Column(
        String(20), nullable=False, default="pending"
    )  # pending | downloading | complete | failed
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
