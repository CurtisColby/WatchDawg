"""
WatchDawg Configuration — Single source of truth for all settings.

Loads from environment variables (populated via .env file).
Validates types and provides sensible defaults where safe to do so.
Secrets have no defaults — the app will refuse to start without them.
"""

from pydantic_settings import BaseSettings
from pydantic import Field
from typing import List


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
    music_videos_path: str = Field(default="/music_videos")

    # --- Timezone ---
    timezone: str = Field(default="America/Chicago")

    @property
    def subreddit_list(self) -> List[str]:
        """Parse comma-separated subreddit string into a list."""
        return [s.strip() for s in self.reddit_subreddits.split(",") if s.strip()]

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


# Singleton instance — import this everywhere
settings = Settings()
