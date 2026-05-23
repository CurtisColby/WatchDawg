"""
WatchDawg Abstract Base Provider.

Every media source (Reddit, Vimeo, Usenet, Torrent, etc.) must implement
this interface. This enforces a consistent contract so the scraper
orchestrator can work with any provider without knowing the specifics.

To add a new source:
1. Create a new file in app/providers/ (e.g., vimeo.py)
2. Subclass BaseProvider
3. Implement all abstract methods
4. Register it in the scraper service
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional
import datetime


@dataclass
class DiscoveredVideo:
    """
    Standardized representation of a video discovered by any provider.

    This is the common format passed from providers to the scraper orchestrator.
    Provider-specific quirks are normalized here so downstream code never
    has to care where the video came from.
    """

    source_provider: str  # e.g., "reddit", "vimeo"
    source_post_id: str  # Unique ID from the source platform
    source_url: str  # The URL to the video (YouTube link, Vimeo link, direct mp4, etc.)
    title: str = "Unknown Title"
    artist: Optional[str] = None
    thumbnail_url: Optional[str] = None
    duration_seconds: Optional[float] = None
    score: Optional[int] = None  # Platform-specific popularity metric
    discovered_at: datetime.datetime = field(default_factory=datetime.datetime.utcnow)

    def __repr__(self) -> str:
        return f"<DiscoveredVideo provider={self.source_provider} title='{self.title[:40]}' url={self.source_url[:60]}>"


class BaseProvider(ABC):
    """
    Abstract base class for all media source providers.

    Subclasses must implement:
    - fetch_posts(): Retrieve raw post data from the source platform.
    - parse_posts(): Convert raw data into a list of DiscoveredVideo objects.
    - provider_name: A string identifier for this provider.
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """
        Unique string identifier for this provider.
        Used in database records and logs.
        Example: "reddit", "vimeo", "usenet"
        """
        ...

    @abstractmethod
    async def fetch_posts(self, limit: int = 50) -> List[DiscoveredVideo]:
        """
        Discover and return new videos from this source.

        This is the main entry point the scraper orchestrator calls.
        Implementations should:
        1. Hit the source API/endpoint.
        2. Parse the response into DiscoveredVideo objects.
        3. Filter out non-video posts (images, text-only, etc.).
        4. Return the list — dedup and skip filtering happens upstream.

        Args:
            limit: Maximum number of posts to fetch per call.

        Returns:
            A list of DiscoveredVideo objects.
        """
        ...

    @abstractmethod
    async def validate_connection(self) -> bool:
        """
        Test that this provider can reach its source.
        Used by health checks and startup diagnostics.

        Returns:
            True if the source is reachable, False otherwise.
        """
        ...

    def __repr__(self) -> str:
        return f"<Provider: {self.provider_name}>"
