"""
WatchDawg Configuration — Single source of truth for all settings.

Loads from environment variables (populated via .env file).
Validates types and provides sensible defaults where safe to do so.
Secrets have no defaults — the app will refuse to start without them.

Milestone B additions:
- tmdb_api_key: Optional TMDb API key for movie/TV metadata enrichment.

Pre-Milestone D change:
- music_videos_path renamed to downloads_path, default changed from
  /music_videos to /watchdawg to match the new container mount name.
  Host folder renamed from /media/colby/NAS1/WatchDawg to
  /media/colby/NAS1/WD_Downloads for clarity.
  Subfolders Public/ and Private/ are created on first download.
"""

from pydantic_settings import BaseSettings
from pydantic import Field
from typing import List, Optional


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # --- Core ---
    app_port: int = Field(default=6767)
    app_env: str = Field(default="development")
    app_secret_key: str = Field(
        ...,
        description="Secret key for session signing. Must be set in .env.",
    )

    # --- Encryption (ChartHound Standard) ---
    fernet_encryption_key: str = Field(
        ...,
        description="Fernet symmetric key for encrypting data at rest. Must be set in .env.",
    )

    # --- Database ---
    database_url: str = Field(
        default="sqlite+aiosqlite:///app/data/watchdawg.db"
    )

    # --- yt-dlp ---
    ytdlp_cookies_path: str = Field(default="/config/cookies.txt")

    # --- Reddit ---
    reddit_subreddits: str = Field(default="SexyMusicVideos")
    scrape_interval_minutes: int = Field(default=30)

    # --- Downloads ---
    # Root download directory inside the container.
    # Maps to /media/colby/NAS1/WD_Downloads on the host via docker-compose.yml.
    # Subfolder structure:
    #   /watchdawg/Public/{channel_name}/   — unlocked source downloads
    #   /watchdawg/Private/{channel_name}/  — locked source downloads
    downloads_path: str = Field(default="/watchdawg")

    # --- Timezone ---
    timezone: str = Field(default="America/Chicago")

    # --- PIN Lock ---
    # Optional. When set, channels marked locked=True are hidden from all API
    # responses until POST /auth/unlock is called with this PIN and the returned
    # token is passed as X-WatchDawg-Token on subsequent requests.
    # If not set, locking is disabled and all content is always visible.
    # A startup warning is logged when this is absent.
    watchdawg_pin: Optional[str] = Field(
        default=None,
        description="PIN code for locking sensitive channels. Optional — if unset, locking is disabled.",
    )

    # --- TMDb Integration (Milestone B) ---
    # Optional. When set, channels with category=movies or category=tv will
    # have their videos enriched with TMDb poster, description, year, and rating.
    # Get a free API key at https://www.themoviedb.org/settings/api
    # If not set, TMDb enrichment is silently skipped — no errors, no impact.
    tmdb_api_key: Optional[str] = Field(
        default=None,
        description="TMDb API key for movie/TV metadata. Optional.",
    )

    @property
    def subreddit_list(self) -> List[str]:
        """Parse comma-separated subreddit string into a list."""
        return [s.strip() for s in self.reddit_subreddits.split(",") if s.strip()]

    @property
    def public_downloads_path(self) -> str:
        """Path for unlocked source downloads — visible without PIN."""
        return f"{self.downloads_path}/Public"

    @property
    def private_downloads_path(self) -> str:
        """Path for locked source downloads — visible only with PIN (Library tab)."""
        return f"{self.downloads_path}/Private"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


# Singleton instance — import this everywhere
settings = Settings()
