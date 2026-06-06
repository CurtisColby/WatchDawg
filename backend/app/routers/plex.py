"""
WatchDawg — Plex Integration Router (Session 39).

Manages the connection between WatchDawg and the user's local Plex Media Server.
Plex credentials (IP, port, token) are stored once, encrypted at rest using
the existing Fernet encryption layer. The token is NEVER returned in any API response.

Plex is reached directly via its HTTP API on the local network:
    http://{plex_ip}:{plex_port}/library/sections
with the X-Plex-Token header.

No Docker Compose changes are needed — the backend calls the host Plex server
by LAN IP. The container can reach host network services directly via IP.

Endpoints:
  POST   /plex/connect              — Save Plex credentials (encrypted at rest)
  GET    /plex/status               — Check connection status and Plex server info
  GET    /plex/libraries            — List all Plex libraries with name, type, count
  GET    /plex/libraries/{key}/genres — List genres for a specific library
  GET    /plex/libraries/{key}/items  — Paginated item list for scheduler use
  DELETE /plex/disconnect           — Wipe stored Plex credentials

DB table (created via migration in main.py — no models.py class needed):
  plex_config
    id              INTEGER PK
    plex_url_encrypted  TEXT        — encrypted "http://ip:port"
    token_encrypted     TEXT        — encrypted Plex token
    server_name         TEXT        — friendly name from Plex (cached)
    connected_at        DATETIME
    last_verified_at    DATETIME
"""

import datetime
import logging
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.encryption import encrypt_value, decrypt_value

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/plex", tags=["plex"])

# Timeout for all Plex API calls — Plex is local so this should be very fast
PLEX_TIMEOUT = 10

# Plex API requires these headers on every request
PLEX_HEADERS = {
    "Accept": "application/json",
    "X-Plex-Client-Identifier": "WatchDawg-Backend",
    "X-Plex-Product": "WatchDawg",
    "X-Plex-Version": "39.0",
}


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class PlexConnectRequest(BaseModel):
    plex_ip: str = Field(..., description="Plex server IP address (e.g. 192.168.50.10)")
    plex_port: int = Field(default=32400, description="Plex server port (default 32400)")
    plex_token: str = Field(..., description="Plex authentication token (X-Plex-Token)")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _get_plex_credentials(db: AsyncSession) -> Optional[dict]:
    """
    Retrieve and decrypt Plex credentials from the DB.
    Returns None if not configured.
    Returns dict with keys: url, token, server_name
    """
    result = await db.execute(
        text("SELECT plex_url_encrypted, token_encrypted, server_name FROM plex_config LIMIT 1")
    )
    row = result.fetchone()
    if not row:
        return None
    try:
        return {
            "url": decrypt_value(row[0]),
            "token": decrypt_value(row[1]),
            "server_name": row[2] or "Plex Server",
        }
    except Exception as e:
        logger.error(f"Failed to decrypt Plex credentials: {e}")
        return None


def _plex_headers(token: str) -> dict:
    """Build Plex API headers with the auth token."""
    return {**PLEX_HEADERS, "X-Plex-Token": token}


async def _verify_plex_connection(url: str, token: str) -> dict:
    """
    Hit the Plex identity endpoint to verify credentials and get server info.
    Returns dict with server_name and version on success.
    Raises HTTPException on failure.
    """
    try:
        async with httpx.AsyncClient(timeout=PLEX_TIMEOUT) as client:
            resp = await client.get(
                f"{url}/identity",
                headers=_plex_headers(token),
            )
            if resp.status_code != 200:
                raise HTTPException(
                    status_code=502,
                    detail=f"Plex server returned HTTP {resp.status_code}. Check IP, port, and token.",
                )
            data = resp.json()
            media_container = data.get("MediaContainer", {})
            return {
                "server_name": media_container.get("friendlyName", "Plex Server"),
                "version": media_container.get("version", "unknown"),
                "machine_identifier": media_container.get("machineIdentifier", ""),
            }
    except httpx.ConnectError:
        raise HTTPException(
            status_code=502,
            detail=f"Cannot reach Plex at {url}. Check that Plex is running and the IP/port are correct.",
        )
    except httpx.TimeoutException:
        raise HTTPException(
            status_code=504,
            detail=f"Plex connection timed out at {url}. Server may be busy.",
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Plex connection error: {e}")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/connect")
async def connect_plex(
    request: PlexConnectRequest,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Save and verify Plex server credentials.

    Validates the connection against the Plex identity endpoint before storing.
    Credentials are encrypted at rest using Fernet encryption.
    The token is NEVER returned in any API response.
    """
    plex_url = f"http://{request.plex_ip.strip()}:{request.plex_port}"

    # Verify credentials before storing
    server_info = await _verify_plex_connection(plex_url, request.plex_token)

    # Encrypt credentials
    encrypted_url = encrypt_value(plex_url)
    encrypted_token = encrypt_value(request.plex_token)
    now = datetime.datetime.utcnow()

    # Upsert — only one Plex config row ever exists
    existing = await db.execute(text("SELECT id FROM plex_config LIMIT 1"))
    row = existing.fetchone()

    if row:
        await db.execute(text("""
            UPDATE plex_config
            SET plex_url_encrypted = :url,
                token_encrypted = :token,
                server_name = :name,
                connected_at = :now,
                last_verified_at = :now
            WHERE id = :id
        """), {
            "url": encrypted_url,
            "token": encrypted_token,
            "name": server_info["server_name"],
            "now": now,
            "id": row[0],
        })
    else:
        await db.execute(text("""
            INSERT INTO plex_config
                (plex_url_encrypted, token_encrypted, server_name, connected_at, last_verified_at)
            VALUES (:url, :token, :name, :now, :now)
        """), {
            "url": encrypted_url,
            "token": encrypted_token,
            "name": server_info["server_name"],
            "now": now,
        })

    await db.commit()
    logger.info(f"Plex connected: {server_info['server_name']} at {plex_url}")

    return {
        "status": "connected",
        "server_name": server_info["server_name"],
        "plex_url": plex_url,  # URL is not sensitive — only the token is
        "version": server_info["version"],
    }


@router.get("/status")
async def plex_status(db: AsyncSession = Depends(get_db_session)):
    """
    Check Plex connection status and verify the stored credentials still work.
    Returns server info if connected, or not_configured if no credentials stored.
    Token is never returned.
    """
    creds = await _get_plex_credentials(db)
    if not creds:
        return {"status": "not_configured"}

    try:
        server_info = await _verify_plex_connection(creds["url"], creds["token"])
        # Update last verified timestamp
        await db.execute(
            text("UPDATE plex_config SET last_verified_at = :now"),
            {"now": datetime.datetime.utcnow()}
        )
        await db.commit()
        return {
            "status": "connected",
            "server_name": server_info["server_name"],
            "plex_url": creds["url"],
            "version": server_info["version"],
        }
    except HTTPException as e:
        return {
            "status": "error",
            "server_name": creds["server_name"],
            "plex_url": creds["url"],
            "detail": e.detail,
        }


@router.get("/libraries")
async def list_plex_libraries(db: AsyncSession = Depends(get_db_session)):
    """
    List all libraries on the connected Plex server.

    Returns each library with:
      - key        — Plex library section key (used in other endpoints)
      - title      — Library name
      - type       — movie | show | music | photo
      - count      — Total item count
      - thumb      — Library art URL (relative to Plex base URL)

    The Adult library is flagged with is_adult=true if the title contains
    common adult-content keywords. This flag is used by the web UI to
    prevent accidental assignment to EPG #1.
    """
    creds = await _get_plex_credentials(db)
    if not creds:
        raise HTTPException(status_code=404, detail="Plex is not configured. POST /plex/connect first.")

    try:
        async with httpx.AsyncClient(timeout=PLEX_TIMEOUT) as client:
            resp = await client.get(
                f"{creds['url']}/library/sections",
                headers=_plex_headers(creds["token"]),
            )
            if resp.status_code != 200:
                raise HTTPException(
                    status_code=502,
                    detail=f"Plex returned HTTP {resp.status_code} for library list.",
                )
            data = resp.json()
            sections = data.get("MediaContainer", {}).get("Directory", [])

            # Adult keyword detection — used to warn web UI, not enforce server-side
            ADULT_KEYWORDS = {"adult", "xxx", "mature", "18+", "nsfw", "explicit"}

            libraries = []
            for section in sections:
                title = section.get("title", "")
                lib_type = section.get("type", "")
                key = section.get("key", "")
                count = section.get("count", 0)

                is_adult = any(kw in title.lower() for kw in ADULT_KEYWORDS)

                libraries.append({
                    "key": key,
                    "title": title,
                    "type": lib_type,       # movie | show | music | photo
                    "count": count,
                    "is_adult": is_adult,
                    "thumb": section.get("thumb", None),
                    "art": section.get("art", None),
                })

            logger.info(f"Plex libraries fetched: {len(libraries)} sections")
            return {
                "status": "ok",
                "server_name": creds["server_name"],
                "libraries": libraries,
                "total": len(libraries),
            }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch Plex libraries: {e}")


@router.get("/libraries/{library_key}/genres")
async def list_plex_library_genres(
    library_key: str,
    db: AsyncSession = Depends(get_db_session),
):
    """
    List all genres present in a specific Plex library.

    Used by the web UI EPG channel builder to populate the genre filter
    dropdown when creating a Plex-sourced EPG channel.

    Returns genres sorted alphabetically with item count per genre.
    """
    creds = await _get_plex_credentials(db)
    if not creds:
        raise HTTPException(status_code=404, detail="Plex is not configured.")

    try:
        async with httpx.AsyncClient(timeout=PLEX_TIMEOUT) as client:
            resp = await client.get(
                f"{creds['url']}/library/sections/{library_key}/genre",
                headers=_plex_headers(creds["token"]),
            )
            if resp.status_code == 404:
                raise HTTPException(
                    status_code=404,
                    detail=f"Plex library section '{library_key}' not found.",
                )
            if resp.status_code != 200:
                raise HTTPException(
                    status_code=502,
                    detail=f"Plex returned HTTP {resp.status_code} for genre list.",
                )
            data = resp.json()
            directories = data.get("MediaContainer", {}).get("Directory", [])

            genres = sorted([
                {
                    "id": d.get("ratingKey", d.get("key", "")),
                    "title": d.get("title", ""),
                    "count": d.get("size", 0),
                }
                for d in directories
                if d.get("title")
            ], key=lambda g: g["title"])

            return {
                "library_key": library_key,
                "genres": genres,
                "total": len(genres),
            }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch genres: {e}")


@router.get("/libraries/{library_key}/items")
async def list_plex_library_items(
    library_key: str,
    genre: Optional[str] = None,
    limit: int = 500,
    offset: int = 0,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Paginated item list from a Plex library section, optionally filtered by genre.

    Called internally by the pseudo-channel scheduler to build EPG schedules.
    Also callable from the web UI for previewing channel content.

    Returns items with:
      - rating_key     — Plex item identifier (used as source_id in epg_schedules)
      - title          — Movie or show title
      - year           — Release year
      - duration_ms    — Duration in milliseconds (Plex native unit)
      - duration_sec   — Duration in seconds (convenience)
      - thumb          — Thumbnail path (relative to Plex base URL)
      - genres         — List of genre strings
      - type           — movie | show | episode
      - summary        — Plot description

    For TV shows (type=show), returns series-level records — the scheduler
    handles episode-level drill-down separately.
    """
    creds = await _get_plex_credentials(db)
    if not creds:
        raise HTTPException(status_code=404, detail="Plex is not configured.")

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            # Build URL — optionally filter by genre using Plex's genre filter
            if genre:
                # Plex genre filter uses a different URL structure
                url = f"{creds['url']}/library/sections/{library_key}/all"
                params = {
                    "genre": genre,
                    "X-Plex-Container-Start": offset,
                    "X-Plex-Container-Size": limit,
                }
            else:
                url = f"{creds['url']}/library/sections/{library_key}/all"
                params = {
                    "X-Plex-Container-Start": offset,
                    "X-Plex-Container-Size": limit,
                }

            resp = await client.get(
                url,
                headers=_plex_headers(creds["token"]),
                params=params,
            )
            if resp.status_code != 200:
                raise HTTPException(
                    status_code=502,
                    detail=f"Plex returned HTTP {resp.status_code} for items.",
                )
            data = resp.json()
            container = data.get("MediaContainer", {})
            items_raw = container.get("Metadata", [])
            total_size = container.get("totalSize", len(items_raw))

            plex_base = creds["url"]
            items = []
            for item in items_raw:
                duration_ms = item.get("duration", 0) or 0
                genres = [g.get("tag", "") for g in item.get("Genre", []) if g.get("tag")]
                items.append({
                    "rating_key": item.get("ratingKey", ""),
                    "title": item.get("title", ""),
                    "year": item.get("year", None),
                    "duration_ms": duration_ms,
                    "duration_sec": round(duration_ms / 1000) if duration_ms else 0,
                    "thumb": f"{plex_base}{item['thumb']}" if item.get("thumb") else None,
                    "art": f"{plex_base}{item['art']}" if item.get("art") else None,
                    "genres": genres,
                    "type": item.get("type", "movie"),
                    "summary": item.get("summary", ""),
                    "content_rating": item.get("contentRating", ""),
                    "rating": item.get("rating", None),
                    "audience_rating": item.get("audienceRating", None),
                })

            return {
                "library_key": library_key,
                "genre_filter": genre,
                "total": total_size,
                "offset": offset,
                "limit": limit,
                "items": items,
            }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch Plex items: {e}")


@router.get("/libraries/{library_key}/episodes/{show_rating_key}")
async def list_plex_show_episodes(
    library_key: str,
    show_rating_key: str,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Fetch all episodes for a specific TV show from Plex.

    Called by the pseudo-channel scheduler when building TV series EPG schedules.
    Returns episodes in season/episode order, each with duration and stream info.
    """
    creds = await _get_plex_credentials(db)
    if not creds:
        raise HTTPException(status_code=404, detail="Plex is not configured.")

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{creds['url']}/library/metadata/{show_rating_key}/allLeaves",
                headers=_plex_headers(creds["token"]),
                params={"X-Plex-Container-Size": 5000},
            )
            if resp.status_code != 200:
                raise HTTPException(
                    status_code=502,
                    detail=f"Plex returned HTTP {resp.status_code} fetching episodes.",
                )
            data = resp.json()
            episodes_raw = data.get("MediaContainer", {}).get("Metadata", [])
            plex_base = creds["url"]

            episodes = []
            for ep in episodes_raw:
                duration_ms = ep.get("duration", 0) or 0
                episodes.append({
                    "rating_key": ep.get("ratingKey", ""),
                    "title": ep.get("title", ""),
                    "grandparent_title": ep.get("grandparentTitle", ""),  # show name
                    "season_number": ep.get("parentIndex", 1),
                    "episode_number": ep.get("index", 1),
                    "duration_ms": duration_ms,
                    "duration_sec": round(duration_ms / 1000) if duration_ms else 0,
                    "thumb": f"{plex_base}{ep['thumb']}" if ep.get("thumb") else None,
                    "summary": ep.get("summary", ""),
                    "view_count": ep.get("viewCount", 0),
                    "last_viewed_at": ep.get("lastViewedAt", None),
                })

            return {
                "show_rating_key": show_rating_key,
                "episode_count": len(episodes),
                "episodes": episodes,
            }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch episodes: {e}")


@router.get("/stream/{rating_key}")
async def get_plex_stream_url(
    rating_key: str,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Resolve a Plex item's direct stream URL.

    Returns the full HTTP URL that ExoPlayer can play directly.
    This is the URL that gets written into epg_schedules.stream_url
    and served to the Android client.

    Plex direct-play URLs are stable while the server is running — no
    separate token refresh is needed for LAN playback when the token
    is appended to the URL.
    """
    creds = await _get_plex_credentials(db)
    if not creds:
        raise HTTPException(status_code=404, detail="Plex is not configured.")

    try:
        async with httpx.AsyncClient(timeout=PLEX_TIMEOUT) as client:
            resp = await client.get(
                f"{creds['url']}/library/metadata/{rating_key}",
                headers=_plex_headers(creds["token"]),
            )
            if resp.status_code != 200:
                raise HTTPException(
                    status_code=404,
                    detail=f"Plex item {rating_key} not found.",
                )
            data = resp.json()
            metadata = data.get("MediaContainer", {}).get("Metadata", [])
            if not metadata:
                raise HTTPException(status_code=404, detail="No metadata returned for this item.")

            item = metadata[0]
            media_list = item.get("Media", [])
            if not media_list:
                raise HTTPException(status_code=404, detail="No media streams found for this item.")

            # Take the first media item (highest quality Plex selects by default)
            media = media_list[0]
            parts = media.get("Part", [])
            if not parts:
                raise HTTPException(status_code=404, detail="No media parts found.")

            part = parts[0]
            part_key = part.get("key", "")

            # Build direct-play URL — Plex token appended as query param
            stream_url = f"{creds['url']}{part_key}?X-Plex-Token={creds['token']}"

            return {
                "rating_key": rating_key,
                "title": item.get("title", ""),
                "stream_url": stream_url,
                "duration_ms": media.get("duration", 0),
                "duration_sec": round(media.get("duration", 0) / 1000),
                "video_codec": media.get("videoCodec", ""),
                "audio_codec": media.get("audioCodec", ""),
                "resolution": f"{media.get('width', 0)}x{media.get('height', 0)}",
                "bitrate": media.get("bitrate", 0),
            }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to resolve Plex stream: {e}")


@router.delete("/disconnect")
async def disconnect_plex(db: AsyncSession = Depends(get_db_session)):
    """
    Wipe all stored Plex credentials.

    Also disables all EPG channels that were sourced from Plex,
    so the schedule doesn't try to generate from a disconnected server.
    """
    result = await db.execute(text("DELETE FROM plex_config"))
    deleted = result.rowcount

    if deleted > 0:
        # Disable Plex-sourced EPG channels — don't delete, just pause them
        await db.execute(text("""
            UPDATE epg_channels
            SET enabled = 0
            WHERE source_type IN ('plex_movie', 'plex_tv')
        """))
        await db.commit()
        logger.info("Plex disconnected — credentials wiped, Plex EPG channels disabled.")
        return {"status": "disconnected", "message": "Plex credentials removed. Plex EPG channels paused."}
    else:
        return {"status": "not_configured", "message": "No Plex credentials were stored."}
