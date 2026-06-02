"""
TMDb (The Movie Database) Metadata Service.

Provides best-effort movie and TV metadata enrichment for WatchDawg channels
with category set to 'movies' or 'tv'.

Usage:
    from app.services.tmdb import TmdbService
    service = TmdbService()
    metadata = await service.lookup("Blade Runner 2049", media_type="movie")
    # Returns dict or None if no match / API key not configured

Configuration:
    Set TMDB_API_KEY in .env to enable. If not set, all lookups return None
    silently — no errors, no log spam, no impact on scraping pipeline.

    Get a free API key at: https://www.themoviedb.org/settings/api

Rate limits:
    TMDb free tier: ~50 requests/second. We call once per scraped video
    for movies/tv channels. The scraper runs async but we add a small delay
    between calls to be a polite API citizen.
"""

import logging
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

TMDB_BASE = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w500"


class TmdbService:
    """
    Async TMDb metadata lookup service.

    Instantiate once and reuse across scrape runs. Uses httpx async client.
    All methods return None gracefully if the API key is not configured or
    if no match is found — callers should never crash on a missing result.
    """

    def __init__(self):
        self._api_key = settings.tmdb_api_key
        self._enabled = bool(self._api_key)

        if not self._enabled:
            logger.debug("TMDb service: API key not configured — enrichment disabled.")

    async def lookup(
        self,
        title: str,
        year: Optional[int] = None,
        media_type: str = "movie",
    ) -> Optional[dict]:
        """
        Search TMDb for a movie or TV show by title.

        Args:
            title:      The title to search for.
            year:       Optional release year to narrow results.
            media_type: "movie" or "tv"

        Returns:
            dict with keys: tmdb_id, poster_url, description, year, rating
            None if not found or API key not configured.
        """
        if not self._enabled:
            return None

        endpoint = "movie" if media_type == "movie" else "tv"
        params = {
            "api_key": self._api_key,
            "query": title,
            "language": "en-US",
            "page": 1,
            "include_adult": False,
        }
        if year:
            # TMDb uses 'year' for movies, 'first_air_date_year' for TV
            if media_type == "movie":
                params["year"] = year
            else:
                params["first_air_date_year"] = year

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{TMDB_BASE}/search/{endpoint}",
                    params=params,
                )
                resp.raise_for_status()
                data = resp.json()

            results = data.get("results", [])
            if not results:
                logger.debug(f"TMDb: no results for '{title}' ({media_type})")
                return None

            # Take the first result — TMDb search is generally well-ordered
            top = results[0]

            poster_path = top.get("poster_path")
            poster_url = f"{TMDB_IMAGE_BASE}{poster_path}" if poster_path else None

            # Date field differs between movie and TV
            release_date = top.get("release_date") or top.get("first_air_date") or ""
            release_year = int(release_date[:4]) if len(release_date) >= 4 else None

            result = {
                "tmdb_id": top.get("id"),
                "poster_url": poster_url,
                "description": top.get("overview") or None,
                "year": release_year,
                "rating": top.get("vote_average") or None,
            }

            logger.debug(
                f"TMDb: matched '{title}' -> id={result['tmdb_id']} "
                f"year={result['year']} rating={result['rating']}"
            )
            return result

        except httpx.HTTPStatusError as e:
            logger.warning(f"TMDb HTTP error for '{title}': {e.response.status_code}")
            return None
        except httpx.RequestError as e:
            logger.warning(f"TMDb request error for '{title}': {e}")
            return None
        except Exception as e:
            logger.warning(f"TMDb unexpected error for '{title}': {e}")
            return None

    async def enrich_video(self, video, media_type: str = "movie") -> bool:
        """
        Attempt to enrich a Video ORM object with TMDb metadata in-place.

        Calls lookup() and writes results directly to the video object fields.
        The caller is responsible for committing the session.

        Returns True if metadata was found and applied, False otherwise.
        """
        if not self._enabled:
            return False

        result = await self.lookup(video.title, media_type=media_type)
        if not result:
            return False

        video.tmdb_id = result["tmdb_id"]
        video.tmdb_poster_url = result["poster_url"]
        video.tmdb_description = result["description"]
        video.tmdb_year = result["year"]
        video.tmdb_rating = result["rating"]

        return True
