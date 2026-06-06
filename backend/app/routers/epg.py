"""
WatchDawg — EPG Router (Session 40).

Manages pseudo-EPG channels and their pre-computed schedules.

Two EPG types exist:
  main  — PIN-free. Fed from IPTV favorites, Plex libraries, WatchDawg scraped sources.
  adult — PIN-gated. Fed from locked scraped sources and Plex adult library.
          Structurally excluded from all main EPG queries at the DB level.

EPG Channel source types:
  iptv_favorites  — Real IPTV channels marked is_favorite=True in live_tv_channels.
                    Schedule is always "live" — no pre-computation needed.
  plex_movie      — Movies from a Plex library section, optionally filtered by genre.
  plex_tv         — TV series from a Plex library section, with episode budgeting.
  watchdawg       — Videos from WatchDawg scraped channels, filtered by genre tags.
                    Optional watchdawg_source_id pins the channel to a single WatchDawg
                    source channel rather than all channels matching the genre.

DB tables (created by _run_migrations in main.py):
  epg_channels   — Channel definitions (number, name, source, rotation settings)
  epg_schedules  — Pre-computed time slots (what plays when on each channel)

Endpoints:
  GET    /epg/channels                    — List EPG channels (main or adult)
  POST   /epg/channels                    — Create a new EPG channel
  PATCH  /epg/channels/{id}              — Edit channel settings
  DELETE /epg/channels/{id}              — Remove channel
  PATCH  /epg/channels/{id}/toggle       — Enable / disable channel
  GET    /epg/schedule                    — Rolling schedule for all channels
  GET    /epg/schedule/{channel_id}       — Schedule for one channel
  POST   /epg/schedule/rebuild            — Force-regenerate all schedules now
  GET    /epg/channels/{id}/preview       — Next 5 upcoming items for a channel
  GET    /epg/next-channel-number         — Suggest next available channel number
  GET    /epg/watchdawg-sources           — List WatchDawg channels usable as EPG sources
  GET    /epg/watchdawg-genres            — Distinct genre tags across WatchDawg channels
  GET    /epg/stream/{channel_id}         — FFmpeg HLS stream for in-progress Plex slots
"""

import asyncio
import datetime
import logging
import os
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks, Request
from fastapi.responses import StreamingResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/epg", tags=["epg"])

# Valid source types
VALID_SOURCE_TYPES = {"iptv_favorites", "plex_movie", "plex_tv", "watchdawg"}

# Valid EPG types — main is PIN-free, adult is PIN-gated
VALID_EPG_TYPES = {"main", "adult"}

# Valid rotation styles
VALID_ROTATION_STYLES = {"sequential", "shuffle"}

# Adult source types allowed in adult EPG only — enforced at DB query level
ADULT_ONLY_CATEGORIES = {"adult", "sexy"}


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class CreateEpgChannelRequest(BaseModel):
    channel_number: int = Field(..., description="Channel number (e.g. 101, 102). Must be unique.")
    name: str = Field(..., description="Display name (e.g. 'Horror Channel')")
    epg_type: str = Field(default="main", description="'main' or 'adult'")
    source_type: str = Field(..., description="'iptv_favorites', 'plex_movie', 'plex_tv', or 'watchdawg'")
    plex_library_key: Optional[str] = Field(None, description="Plex library section key (plex_* types only)")
    genre_filter: Optional[str] = Field(None, description="Comma-separated genres to include. Null = all genres.")
    episodes_per_day: int = Field(default=2, description="Max episodes per TV series per day (plex_tv only)")
    rotation_style: str = Field(default="shuffle", description="'sequential' or 'shuffle'")
    primetime_boost: bool = Field(default=False, description="Feature highest-rated content in primetime slots (7-11 PM)")
    logo_url: Optional[str] = Field(None, description="Channel logo/icon URL")
    enabled: bool = Field(default=True)
    # Session 40: optional pin to a specific WatchDawg source channel (watchdawg source_type only)
    watchdawg_source_id: Optional[int] = Field(None, description="Pin to a specific WatchDawg channel ID. Null = all sources matching genre_filter.")


class UpdateEpgChannelRequest(BaseModel):
    channel_number: Optional[int] = None
    name: Optional[str] = None
    plex_library_key: Optional[str] = None
    genre_filter: Optional[str] = None
    episodes_per_day: Optional[int] = None
    rotation_style: Optional[str] = None
    primetime_boost: Optional[bool] = None
    logo_url: Optional[str] = None
    enabled: Optional[bool] = None
    # Session 40: allow updating the watchdawg source pin
    watchdawg_source_id: Optional[int] = None


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------

def _serialize_channel(row) -> dict:
    return {
        "id":                   row[0],
        "channel_number":       row[1],
        "name":                 row[2],
        "epg_type":             row[3],
        "source_type":          row[4],
        "plex_library_key":     row[5],
        "genre_filter":         row[6],
        "episodes_per_day":     row[7],
        "rotation_style":       row[8],
        "primetime_boost":      bool(row[9]),
        "logo_url":             row[10],
        "enabled":              bool(row[11]),
        "sort_order":           row[12],
        "created_at":           row[13].isoformat() if row[13] and hasattr(row[13], "isoformat") else str(row[13]) if row[13] else None,
        # Session 40: watchdawg_source_id (column 14, may be absent on old rows — default None)
        "watchdawg_source_id":  row[14] if len(row) > 14 else None,
    }


def _serialize_slot(row, base_url: str = "") -> dict:
    source_type = row[10]
    source_id   = row[11]
    raw_stream  = row[9]
    progress    = _compute_progress(row[12], row[14])
    epg_ch_id   = row[1]

    # Session 40 — WatchDawg slots:
    # If a local downloaded file exists for this slot, route through /epg/stream/
    # exactly like Plex channels — FFmpeg seeks to the exact wall-clock position.
    # If no local file, fall back to on-demand resolve via Android (video_id path).
    if source_type == "watchdawg" and source_id:
        try:
            video_id_int = int(source_id)
        except (ValueError, TypeError):
            video_id_int = None

        # Check if a local file exists for this slot
        _base = base_url or os.environ.get("WATCHDAWG_BASE_URL", "").rstrip("/")
        local_file_found = False
        for epg_folder in ("Public/EPG", "Private/EPG"):
            candidate = f"/watchdawg/{epg_folder}/{epg_ch_id}_{source_id}.mp4"
            if os.path.exists(candidate) and os.path.getsize(candidate) > 1_000_000:
                local_file_found = True
                break

        if local_file_found and _base:
            # Local file ready — stream via FFmpeg for perfect timing
            video_id   = None
            stream_url = f"{_base}/epg/stream/{epg_ch_id}"
        else:
            # No local file yet — use Android on-demand resolve
            video_id   = video_id_int
            stream_url = ""

    # Session 40 — Plex in-progress slots: route through FFmpeg stream endpoint
    # so the video starts at the correct wall-clock position.
    # Future slots (progress == 0) get the direct Plex URL — plays from beginning
    # which is exactly right. Only currently-airing slots need the offset seek.
    elif source_type in ("plex_movie", "plex_tv", "plex") and (progress or 0) > 2:
        video_id   = None
        _base = base_url or os.environ.get("WATCHDAWG_BASE_URL", "").rstrip("/")
        stream_url = f"{_base}/epg/stream/{epg_ch_id}" if _base else raw_stream

    else:
        video_id   = None
        stream_url = raw_stream

    return {
        "id":              row[0],
        "epg_channel_id":  epg_ch_id,
        "channel_number":  row[2],
        "channel_name":    row[3],
        "channel_logo":    row[4],
        "title":           row[5],
        "subtitle":        row[6],
        "description":     row[7],
        "thumbnail_url":   row[8],
        "stream_url":      stream_url,
        "source_type":     source_type,
        "source_id":       source_id,
        "video_id":        video_id,
        "start_time":      row[12].isoformat() if row[12] and hasattr(row[12], "isoformat") else str(row[12]) if row[12] else None,
        "end_time":        row[13].isoformat() if row[13] and hasattr(row[13], "isoformat") else str(row[13]) if row[13] else None,
        "duration_seconds": row[14],
        "progress_seconds": progress,
    }


def _compute_progress(start_time, duration_seconds) -> Optional[int]:
    """How many seconds into the current slot we are right now."""
    if not start_time or not duration_seconds:
        return None
    # SQLite may return strings — parse if needed
    if isinstance(start_time, str):
        try:
            start_time = datetime.datetime.fromisoformat(start_time)
        except Exception:
            return None
    now = datetime.datetime.utcnow()
    if start_time > now:
        return 0  # hasn't started yet
    elapsed = (now - start_time).total_seconds()
    return min(int(elapsed), duration_seconds)


# ---------------------------------------------------------------------------
# FFmpeg live stream — Plex EPG channels (Session 40)
# ---------------------------------------------------------------------------

# Active FFmpeg processes keyed by channel_id.
# Only one process per channel at a time — new tune-in kills the old one.
_ffmpeg_procs: dict = {}
# Timestamp of last byte sent per channel — used to kill idle streams.
_ffmpeg_last_activity: dict = {}
# Idle timeout in seconds — kill FFmpeg if no one is reading
_FFMPEG_IDLE_TIMEOUT = 30


async def _kill_ffmpeg(channel_id: int):
    """Kill the FFmpeg process for a channel if one is running."""
    proc = _ffmpeg_procs.pop(channel_id, None)
    if proc and proc.returncode is None:
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass
    _ffmpeg_last_activity.pop(channel_id, None)


async def _ffmpeg_stream_generator(proc, channel_id: int, chunk_size: int = 65536):
    """
    Async generator that yields raw FFmpeg stdout bytes to the client.

    Monitors activity — if no bytes flow for _FFMPEG_IDLE_TIMEOUT seconds
    (client disconnected), kills the process and stops yielding.
    """
    import asyncio
    import time

    _ffmpeg_last_activity[channel_id] = time.monotonic()

    try:
        while True:
            try:
                chunk = await asyncio.wait_for(
                    proc.stdout.read(chunk_size),
                    timeout=_FFMPEG_IDLE_TIMEOUT,
                )
            except asyncio.TimeoutError:
                logger.info(f"EPG stream: channel {channel_id} idle timeout — killing FFmpeg")
                break

            if not chunk:
                # FFmpeg finished (end of file)
                break

            _ffmpeg_last_activity[channel_id] = time.monotonic()
            yield chunk

    except Exception as e:
        logger.warning(f"EPG stream: channel {channel_id} generator error — {e}")
    finally:
        await _kill_ffmpeg(channel_id)


@router.get("/stream/{channel_id}")
async def stream_epg_channel(
    channel_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Stream the currently-airing Plex EPG slot as a live MPEG-TS stream.

    Session 40 — FFmpeg timing fix for Plex channels:

    For in-progress slots (progress_seconds > 0):
      Spawns FFmpeg with -ss {offset} to seek to the exact wall-clock position
      before streaming. Uses stream copy (-c copy) — no transcoding, no CPU load.
      The client receives an MPEG-TS stream that starts mid-video at the right time.

    For future slots (progress_seconds == 0):
      Returns a 302 redirect to the direct Plex URL. No FFmpeg needed — the
      client plays from the beginning which is exactly where it should start.

    Android plays both paths identically via ExoPlayer's HLS/progressive source.

    Process management:
      Only one FFmpeg process runs per channel at a time. New tune-in kills any
      existing process. Idle streams (no bytes read for 30s) self-terminate.
    """
    now = datetime.datetime.utcnow()

    # Find the currently-airing or next upcoming slot for this channel
    result = await db.execute(text("""
        SELECT s.stream_url, s.start_time, s.end_time, s.duration_seconds,
               s.source_type, c.name, s.source_id
        FROM epg_schedules s
        JOIN epg_channels c ON c.id = s.epg_channel_id
        WHERE s.epg_channel_id = :channel_id
          AND s.end_time > :now
        ORDER BY s.start_time ASC
        LIMIT 1
    """), {"channel_id": channel_id, "now": now})

    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"No active slot for EPG channel {channel_id}")

    stream_url   = row[0]
    start_time   = row[1]
    duration_s   = row[3]
    source_type  = row[4]
    channel_name = row[5]
    source_id    = row[6] or ""

    if not stream_url:
        raise HTTPException(status_code=404, detail="Slot has no stream URL")

    # Parse start_time
    if isinstance(start_time, str):
        try:
            start_time = datetime.datetime.fromisoformat(start_time)
        except Exception:
            raise HTTPException(status_code=500, detail="Could not parse slot start_time")

    # Compute current offset into the slot
    if start_time <= now:
        offset_seconds = int((now - start_time).total_seconds())
    else:
        offset_seconds = 0  # future slot — no offset needed

    logger.info(
        f"EPG stream: channel {channel_id} '{channel_name}' | "
        f"offset={offset_seconds}s | url={stream_url[:80]}"
    )

    # Future slot — redirect to direct URL, no FFmpeg needed
    if offset_seconds <= 2:
        if stream_url:
            return RedirectResponse(url=stream_url, status_code=302)
        else:
            raise HTTPException(status_code=404, detail="No stream URL for this slot")

    # Check for a locally downloaded file for this slot.
    # WatchDawg slots downloaded by _download_watchdawg_slots live here.
    # Pattern: /watchdawg/{Public|Private}/EPG/{channel_id}_{source_id}.mp4
    local_file = None
    if source_id:
        for epg_folder in ("Public/EPG", "Private/EPG"):
            candidate = f"/watchdawg/{epg_folder}/{channel_id}_{source_id}.mp4"
            if os.path.exists(candidate) and os.path.getsize(candidate) > 1_000_000:
                local_file = candidate
                logger.info(f"EPG stream: using local file {local_file}")
                break

    # Use local file if available, otherwise use the stored stream URL
    if not stream_url and not local_file:
        raise HTTPException(status_code=404, detail="No stream source available for this slot")

    input_source = local_file or stream_url

    # Kill any existing FFmpeg process for this channel (new tune-in)
    await _kill_ffmpeg(channel_id)

    # Build FFmpeg command:
    # -ss before -i for fast input seek (key-frame accurate for most MKV/MP4)
    # -c copy — stream copy, zero transcoding, full quality
    # -f mpegts — MPEG-TS output, well-supported by ExoPlayer
    # -movflags +faststart not needed for pipe output
    # -avoid_negative_ts make_zero — prevents PTS issues after seek
    ffmpeg_cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "warning",
        "-ss", str(offset_seconds),
        "-i", input_source,
        "-c", "copy",
        "-avoid_negative_ts", "make_zero",
        "-f", "mpegts",
        "pipe:1",
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *ffmpeg_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _ffmpeg_procs[channel_id] = proc
        logger.info(f"EPG stream: FFmpeg started for channel {channel_id} (pid={proc.pid})")
    except Exception as e:
        logger.error(f"EPG stream: Failed to start FFmpeg for channel {channel_id}: {e}")
        raise HTTPException(status_code=500, detail=f"FFmpeg failed to start: {e}")

    return StreamingResponse(
        _ffmpeg_stream_generator(proc, channel_id),
        media_type="video/mp2t",
        headers={
            "Cache-Control": "no-cache, no-store",
            "X-EPG-Channel": str(channel_id),
            "X-EPG-Offset": str(offset_seconds),
        },
    )




@router.get("/watchdawg-sources")
async def get_watchdawg_sources(
    epg_type: str = Query("main", description="'main' returns unlocked sources; 'adult' returns locked sources"),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Return the list of WatchDawg channels that can be used as an EPG source.

    main EPG  — returns unlocked (locked=0) channels.
    adult EPG — returns locked (locked=1) channels.

    Each entry includes the channel's id, name, category, genre_tags, and
    a video count so the UI can show useful information in the picker.
    """
    if epg_type not in VALID_EPG_TYPES:
        raise HTTPException(status_code=400, detail=f"epg_type must be one of: {VALID_EPG_TYPES}")

    locked_filter = 1 if epg_type == "adult" else 0

    result = await db.execute(text("""
        SELECT c.id, c.name, c.category, c.genre_tags, c.channel_type,
               COUNT(v.id) as video_count
        FROM channels c
        LEFT JOIN videos v ON v.channel_id = c.id
            AND v.resolution_status = 'resolved'
            AND v.resolved_stream_url IS NOT NULL
            AND v.duration_seconds IS NOT NULL
            AND v.duration_seconds > 30
        WHERE c.locked = :locked
        GROUP BY c.id, c.name, c.category, c.genre_tags, c.channel_type
        HAVING COUNT(v.id) > 0
        ORDER BY c.name ASC
    """), {"locked": locked_filter})

    sources = []
    for row in result.fetchall():
        sources.append({
            "id":           row[0],
            "name":         row[1],
            "category":     row[2] or "general",
            "genre_tags":   row[3] or "",
            "channel_type": row[4] or "",
            "video_count":  row[5],
        })

    return {
        "epg_type": epg_type,
        "sources": sources,
        "total": len(sources),
    }


@router.get("/watchdawg-genres")
async def get_watchdawg_genres(
    epg_type: str = Query("main", description="'main' returns genres from unlocked sources; 'adult' from locked"),
    source_id: Optional[int] = Query(None, description="Filter genres to a specific WatchDawg channel ID"),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Return all distinct genre tags available from WatchDawg channels.

    genre_tags is a comma-separated string per channel (e.g. "Nature,Documentary").
    This endpoint splits them and returns a deduplicated sorted list.

    Optionally filtered to a single source channel (source_id) to show only
    that channel's genre tags in the picker.
    """
    if epg_type not in VALID_EPG_TYPES:
        raise HTTPException(status_code=400, detail=f"epg_type must be one of: {VALID_EPG_TYPES}")

    locked_filter = 1 if epg_type == "adult" else 0

    params = {"locked": locked_filter}
    source_clause = ""
    if source_id is not None:
        source_clause = "AND c.id = :source_id"
        params["source_id"] = source_id

    result = await db.execute(text(f"""
        SELECT DISTINCT c.genre_tags
        FROM channels c
        WHERE c.locked = :locked
          AND c.genre_tags IS NOT NULL
          AND c.genre_tags != ''
          {source_clause}
        ORDER BY c.genre_tags ASC
    """), params)

    # Explode comma-separated tags into a flat deduplicated sorted set
    genres_set = set()
    for row in result.fetchall():
        raw = row[0] or ""
        for tag in raw.split(","):
            tag = tag.strip()
            if tag:
                genres_set.add(tag)

    genres = sorted(genres_set)

    return {
        "epg_type": epg_type,
        "source_id": source_id,
        "genres": genres,
        "total": len(genres),
    }


# ---------------------------------------------------------------------------
# Endpoints — Channel Management
# ---------------------------------------------------------------------------

@router.get("/next-channel-number")
async def get_next_channel_number(
    epg_type: str = Query("main"),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Suggest the next available channel number.

    Main EPG: starts at 101, increments by 1.
    Adult EPG: starts at 901, increments by 1.
    Always returns a number not currently in use.
    """
    base = 101 if epg_type == "main" else 901
    result = await db.execute(text("""
        SELECT channel_number FROM epg_channels
        WHERE epg_type = :epg_type
        ORDER BY channel_number ASC
    """), {"epg_type": epg_type})
    used = {row[0] for row in result.fetchall()}

    candidate = base
    while candidate in used:
        candidate += 1
    return {"next_channel_number": candidate, "epg_type": epg_type}


@router.get("/channels")
async def list_epg_channels(
    epg_type: str = Query("main", description="'main' or 'adult'"),
    db: AsyncSession = Depends(get_db_session),
):
    """
    List all EPG channels of a given type.

    Adult channels are ONLY returned when epg_type=adult is explicitly requested.
    epg_type=main NEVER returns adult channels — enforced at the SQL WHERE clause,
    not at the application layer.
    """
    if epg_type not in VALID_EPG_TYPES:
        raise HTTPException(status_code=400, detail=f"epg_type must be one of: {VALID_EPG_TYPES}")

    result = await db.execute(text("""
        SELECT id, channel_number, name, epg_type, source_type,
               plex_library_key, genre_filter, episodes_per_day,
               rotation_style, primetime_boost, logo_url, enabled,
               sort_order, created_at,
               watchdawg_source_id
        FROM epg_channels
        WHERE epg_type = :epg_type
        ORDER BY channel_number ASC
    """), {"epg_type": epg_type})

    channels = [_serialize_channel(row) for row in result.fetchall()]
    return {"epg_type": epg_type, "channels": channels, "total": len(channels)}


@router.post("/channels")
async def create_epg_channel(
    request: CreateEpgChannelRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Create a new EPG channel.

    Validates:
    - channel_number is unique across ALL epg_channels
    - source_type is valid
    - plex_* source types require plex_library_key
    - adult source types can only be assigned to epg_type=adult
    - epg_type=adult requires explicit request (no accidental adult channel creation)

    After creation, triggers a background schedule rebuild for the new channel.
    """
    # Validate enums
    if request.epg_type not in VALID_EPG_TYPES:
        raise HTTPException(status_code=400, detail=f"epg_type must be one of: {VALID_EPG_TYPES}")
    if request.source_type not in VALID_SOURCE_TYPES:
        raise HTTPException(status_code=400, detail=f"source_type must be one of: {VALID_SOURCE_TYPES}")
    if request.rotation_style not in VALID_ROTATION_STYLES:
        raise HTTPException(status_code=400, detail=f"rotation_style must be one of: {VALID_ROTATION_STYLES}")

    # Plex source types require a library key
    if request.source_type in ("plex_movie", "plex_tv") and not request.plex_library_key:
        raise HTTPException(
            status_code=400,
            detail="plex_library_key is required for plex_movie and plex_tv source types."
        )

    # Adult channels cannot be created in the main EPG
    if request.epg_type == "adult" and request.source_type == "iptv_favorites":
        raise HTTPException(
            status_code=400,
            detail="IPTV favorites channels cannot be assigned to the adult EPG. "
                   "Use the main EPG for IPTV favorites."
        )

    # Check channel number uniqueness
    existing = await db.execute(
        text("SELECT id FROM epg_channels WHERE channel_number = :num"),
        {"num": request.channel_number}
    )
    if existing.fetchone():
        raise HTTPException(
            status_code=409,
            detail=f"Channel number {request.channel_number} is already in use."
        )

    now = datetime.datetime.utcnow()
    result = await db.execute(text("""
        INSERT INTO epg_channels
            (channel_number, name, epg_type, source_type, plex_library_key,
             genre_filter, episodes_per_day, rotation_style, primetime_boost,
             logo_url, enabled, sort_order, created_at, watchdawg_source_id)
        VALUES
            (:channel_number, :name, :epg_type, :source_type, :plex_library_key,
             :genre_filter, :episodes_per_day, :rotation_style, :primetime_boost,
             :logo_url, :enabled, :sort_order, :now, :watchdawg_source_id)
    """), {
        "channel_number":      request.channel_number,
        "name":                request.name,
        "epg_type":            request.epg_type,
        "source_type":         request.source_type,
        "plex_library_key":    request.plex_library_key,
        "genre_filter":        request.genre_filter,
        "episodes_per_day":    request.episodes_per_day,
        "rotation_style":      request.rotation_style,
        "primetime_boost":     1 if request.primetime_boost else 0,
        "logo_url":            request.logo_url,
        "enabled":             1 if request.enabled else 0,
        "sort_order":          request.channel_number,
        "now":                 now,
        "watchdawg_source_id": request.watchdawg_source_id,
    })
    await db.commit()
    new_id = result.lastrowid

    # Trigger background schedule generation for the new channel
    background_tasks.add_task(_rebuild_channel_schedule, new_id)

    logger.info(
        f"EPG channel created: CH {request.channel_number} '{request.name}' "
        f"({request.epg_type}, {request.source_type})"
    )
    return {
        "status": "created",
        "id": new_id,
        "channel_number": request.channel_number,
        "name": request.name,
        "epg_type": request.epg_type,
        "source_type": request.source_type,
        "message": "Schedule generation started in background.",
    }


@router.patch("/channels/{channel_id}")
async def update_epg_channel(
    channel_id: int,
    request: UpdateEpgChannelRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Update an existing EPG channel's settings.

    Only provided fields are updated (PATCH semantics).
    After update, triggers a background schedule rebuild so changes take effect immediately.
    """
    # Verify channel exists
    existing = await db.execute(
        text("SELECT id, channel_number, name FROM epg_channels WHERE id = :id"),
        {"id": channel_id}
    )
    row = existing.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"EPG channel {channel_id} not found.")

    # Check channel number uniqueness if changing it
    if request.channel_number is not None:
        conflict = await db.execute(
            text("SELECT id FROM epg_channels WHERE channel_number = :num AND id != :id"),
            {"num": request.channel_number, "id": channel_id}
        )
        if conflict.fetchone():
            raise HTTPException(
                status_code=409,
                detail=f"Channel number {request.channel_number} is already in use."
            )

    # Build update SET clause dynamically from provided fields
    updates = {}
    if request.channel_number is not None:
        updates["channel_number"] = request.channel_number
        updates["sort_order"] = request.channel_number
    if request.name is not None:
        updates["name"] = request.name
    if request.plex_library_key is not None:
        updates["plex_library_key"] = request.plex_library_key
    if request.genre_filter is not None:
        updates["genre_filter"] = request.genre_filter
    if request.episodes_per_day is not None:
        updates["episodes_per_day"] = request.episodes_per_day
    if request.rotation_style is not None:
        if request.rotation_style not in VALID_ROTATION_STYLES:
            raise HTTPException(status_code=400, detail=f"rotation_style must be one of: {VALID_ROTATION_STYLES}")
        updates["rotation_style"] = request.rotation_style
    if request.primetime_boost is not None:
        updates["primetime_boost"] = 1 if request.primetime_boost else 0
    if request.logo_url is not None:
        updates["logo_url"] = request.logo_url
    if request.enabled is not None:
        updates["enabled"] = 1 if request.enabled else 0
    # Session 40: support clearing the pin by passing watchdawg_source_id=0
    # (frontend sends 0 to mean "clear", None means "don't change")
    if request.watchdawg_source_id is not None:
        updates["watchdawg_source_id"] = request.watchdawg_source_id if request.watchdawg_source_id > 0 else None

    if not updates:
        return {"status": "no_change", "id": channel_id}

    set_clause = ", ".join(f"{k} = :{k}" for k in updates)
    updates["id"] = channel_id
    await db.execute(text(f"UPDATE epg_channels SET {set_clause} WHERE id = :id"), updates)
    await db.commit()

    # Rebuild schedule to reflect changes
    background_tasks.add_task(_rebuild_channel_schedule, channel_id)

    logger.info(f"EPG channel {channel_id} updated: {list(updates.keys())}")
    return {
        "status": "updated",
        "id": channel_id,
        "updated_fields": [k for k in updates if k != "id"],
        "message": "Schedule will be rebuilt in background.",
    }


@router.patch("/channels/{channel_id}/toggle")
async def toggle_epg_channel(
    channel_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    """Enable or disable an EPG channel without deleting it."""
    existing = await db.execute(
        text("SELECT id, enabled, name FROM epg_channels WHERE id = :id"),
        {"id": channel_id}
    )
    row = existing.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"EPG channel {channel_id} not found.")

    new_state = 0 if row[1] else 1
    await db.execute(
        text("UPDATE epg_channels SET enabled = :state WHERE id = :id"),
        {"state": new_state, "id": channel_id}
    )
    await db.commit()

    logger.info(f"EPG channel {channel_id} '{row[2]}' {'enabled' if new_state else 'disabled'}")
    return {
        "status": "enabled" if new_state else "disabled",
        "id": channel_id,
        "name": row[2],
        "enabled": bool(new_state),
    }


@router.delete("/channels/{channel_id}")
async def delete_epg_channel(
    channel_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Delete an EPG channel and all its scheduled slots.
    This is permanent — use the toggle endpoint to temporarily disable instead.
    """
    existing = await db.execute(
        text("SELECT id, name, channel_number FROM epg_channels WHERE id = :id"),
        {"id": channel_id}
    )
    row = existing.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"EPG channel {channel_id} not found.")

    # Delete schedules first (no FK cascade in raw SQL migration)
    await db.execute(
        text("DELETE FROM epg_schedules WHERE epg_channel_id = :id"),
        {"id": channel_id}
    )
    await db.execute(
        text("DELETE FROM epg_channels WHERE id = :id"),
        {"id": channel_id}
    )
    await db.commit()

    logger.info(f"EPG channel deleted: CH {row[2]} '{row[1]}' (id={channel_id})")
    return {
        "status": "deleted",
        "id": channel_id,
        "channel_number": row[2],
        "name": row[1],
    }


# ---------------------------------------------------------------------------
# Endpoints — Schedule
# ---------------------------------------------------------------------------

@router.get("/schedule")
async def get_epg_schedule(
    epg_type: str = Query("main"),
    hours: int = Query(default=4, ge=1, le=48, description="Hours of schedule to return"),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Return the pre-computed schedule for all enabled channels of the given EPG type.

    Returns time slots covering NOW through NOW+hours.
    Adult schedule is ONLY returned when epg_type=adult is explicitly requested.

    The response groups slots by channel for easy EPG grid rendering on Android.
    Each slot includes progress_seconds so Android can calculate the progress bar
    position for the currently-airing slot.

    Session 40: WatchDawg slots return video_id (the DB video id) and an empty
    stream_url. Android resolves these on-demand via the normal feed path
    (PlayModeMenu → /resolve/{id}/manifest.mpd) so CDN tokens are always fresh.
    """
    if epg_type not in VALID_EPG_TYPES:
        raise HTTPException(status_code=400, detail=f"epg_type must be one of: {VALID_EPG_TYPES}")

    now = datetime.datetime.utcnow()
    window_end = now + datetime.timedelta(hours=hours)

    result = await db.execute(text("""
        SELECT
            s.id, s.epg_channel_id,
            c.channel_number, c.name, c.logo_url,
            s.title, s.subtitle, s.description,
            s.thumbnail_url, s.stream_url, s.source_type, s.source_id,
            s.start_time, s.end_time, s.duration_seconds
        FROM epg_schedules s
        JOIN epg_channels c ON c.id = s.epg_channel_id
        WHERE c.epg_type = :epg_type
          AND c.enabled = 1
          AND s.end_time > :now
          AND s.start_time < :window_end
        ORDER BY c.channel_number ASC, s.start_time ASC
    """), {"epg_type": epg_type, "now": now, "window_end": window_end})

    rows = result.fetchall()

    # Group by channel
    channels_map = {}
    for row in rows:
        ch_id = row[1]
        if ch_id not in channels_map:
            channels_map[ch_id] = {
                "channel_id": ch_id,
                "channel_number": row[2],
                "channel_name": row[3],
                "channel_logo": row[4],
                "slots": [],
            }
        channels_map[ch_id]["slots"].append(_serialize_slot(row))

    # Also include IPTV favorite channels (live — no pre-computed schedule needed)
    live_result = await db.execute(text("""
        SELECT c.id, c.channel_number, c.name, c.logo_url
        FROM epg_channels c
        WHERE c.epg_type = :epg_type
          AND c.enabled = 1
          AND c.source_type = 'iptv_favorites'
        ORDER BY c.channel_number ASC
    """), {"epg_type": epg_type})

    for live_row in live_result.fetchall():
        ch_id = live_row[0]
        if ch_id not in channels_map:
            fav_result = await db.execute(text("""
                SELECT name, stream_url, logo_url
                FROM live_tv_channels
                WHERE is_favorite = 1
                ORDER BY sort_order ASC, name ASC
            """))
            fav_channels = fav_result.fetchall()

            channels_map[ch_id] = {
                "channel_id": ch_id,
                "channel_number": live_row[1],
                "channel_name": live_row[2],
                "channel_logo": live_row[3],
                "is_live": True,
                "live_channels": [
                    {
                        "name": fav[0],
                        "stream_url": fav[1],
                        "logo_url": fav[2],
                    }
                    for fav in fav_channels
                ],
                "slots": [],
            }

    return {
        "epg_type": epg_type,
        "generated_at": now.isoformat(),
        "window_hours": hours,
        "window_end": window_end.isoformat(),
        "channels": list(channels_map.values()),
        "total_channels": len(channels_map),
    }


@router.get("/schedule/{channel_id}")
async def get_channel_schedule(
    channel_id: int,
    hours: int = Query(default=24, ge=1, le=48),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Return the schedule for a single EPG channel.

    Covers NOW through NOW+hours. Includes past slots still in progress
    so the Android client can show what's currently airing with a progress bar.
    """
    channel = await db.execute(
        text("SELECT id, channel_number, name, logo_url, epg_type, source_type, enabled FROM epg_channels WHERE id = :id"),
        {"id": channel_id}
    )
    ch_row = channel.fetchone()
    if not ch_row:
        raise HTTPException(status_code=404, detail=f"EPG channel {channel_id} not found.")

    now = datetime.datetime.utcnow()
    window_end = now + datetime.timedelta(hours=hours)

    result = await db.execute(text("""
        SELECT
            s.id, s.epg_channel_id,
            c.channel_number, c.name, c.logo_url,
            s.title, s.subtitle, s.description,
            s.thumbnail_url, s.stream_url, s.source_type, s.source_id,
            s.start_time, s.end_time, s.duration_seconds
        FROM epg_schedules s
        JOIN epg_channels c ON c.id = s.epg_channel_id
        WHERE s.epg_channel_id = :channel_id
          AND s.end_time > :now
          AND s.start_time < :window_end
        ORDER BY s.start_time ASC
    """), {"channel_id": channel_id, "now": now, "window_end": window_end})

    slots = [_serialize_slot(row) for row in result.fetchall()]

    return {
        "channel_id": channel_id,
        "channel_number": ch_row[1],
        "channel_name": ch_row[2],
        "channel_logo": ch_row[3],
        "epg_type": ch_row[4],
        "source_type": ch_row[5],
        "slot_count": len(slots),
        "slots": slots,
    }


@router.get("/channels/{channel_id}/preview")
async def preview_channel_queue(
    channel_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Return the next 5 upcoming items for an EPG channel.

    Used in the web UI to let you preview what a channel will play
    before committing to its configuration.
    """
    channel = await db.execute(
        text("SELECT id, name, source_type FROM epg_channels WHERE id = :id"),
        {"id": channel_id}
    )
    ch_row = channel.fetchone()
    if not ch_row:
        raise HTTPException(status_code=404, detail=f"EPG channel {channel_id} not found.")

    now = datetime.datetime.utcnow()
    result = await db.execute(text("""
        SELECT title, subtitle, thumbnail_url, start_time, end_time, duration_seconds
        FROM epg_schedules
        WHERE epg_channel_id = :channel_id
          AND end_time > :now
        ORDER BY start_time ASC
        LIMIT 5
    """), {"channel_id": channel_id, "now": now})

    upcoming = [
        {
            "title": row[0],
            "subtitle": row[1],
            "thumbnail_url": row[2],
            "start_time": row[3].isoformat() if row[3] and hasattr(row[3], "isoformat") else str(row[3]) if row[3] else None,
            "end_time": row[4].isoformat() if row[4] and hasattr(row[4], "isoformat") else str(row[4]) if row[4] else None,
            "duration_seconds": row[5],
        }
        for row in result.fetchall()
    ]

    return {
        "channel_id": channel_id,
        "channel_name": ch_row[1],
        "source_type": ch_row[2],
        "upcoming": upcoming,
        "count": len(upcoming),
        "message": "No schedule generated yet — try POST /epg/schedule/rebuild" if not upcoming else None,
    }


@router.post("/schedule/rebuild")
async def rebuild_all_schedules(
    background_tasks: BackgroundTasks,
    epg_type: Optional[str] = Query(None, description="Rebuild only this epg_type. Null = rebuild all."),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Force-rebuild the EPG schedule for all enabled channels.

    Runs in the background — returns immediately.
    Used after adding new channels, changing Plex libraries, or on-demand refresh.
    """
    if epg_type and epg_type not in VALID_EPG_TYPES:
        raise HTTPException(status_code=400, detail=f"epg_type must be one of: {VALID_EPG_TYPES}")

    # Fetch channel IDs to rebuild
    if epg_type:
        result = await db.execute(text("""
            SELECT id FROM epg_channels WHERE enabled = 1 AND epg_type = :epg_type
        """), {"epg_type": epg_type})
    else:
        result = await db.execute(text("SELECT id FROM epg_channels WHERE enabled = 1"))

    channel_ids = [row[0] for row in result.fetchall()]

    if not channel_ids:
        return {
            "status": "no_channels",
            "message": "No enabled EPG channels found to rebuild.",
        }

    # Queue each channel rebuild as a background task
    for channel_id in channel_ids:
        background_tasks.add_task(_rebuild_channel_schedule, channel_id)

    logger.info(f"EPG schedule rebuild queued for {len(channel_ids)} channels (type={epg_type or 'all'})")
    return {
        "status": "rebuilding",
        "channels_queued": len(channel_ids),
        "message": f"Rebuilding schedules for {len(channel_ids)} channels in background.",
    }


# ---------------------------------------------------------------------------
# Background schedule builder — called by background_tasks and scheduler
# ---------------------------------------------------------------------------

async def _rebuild_channel_schedule(channel_id: int):
    """
    Build a 48-hour schedule for a single EPG channel.

    Delegates to the pseudo_scheduler service which knows how to
    pull content from Plex, WatchDawg DB, and IPTV favorites.
    """
    try:
        from app.tasks.pseudo_scheduler import build_channel_schedule
        await build_channel_schedule(channel_id)
    except Exception as e:
        logger.error(f"EPG schedule rebuild failed for channel {channel_id}: {e}")


async def rebuild_all_epg_schedules():
    """
    Called by the background scheduler every 6 hours.
    Rebuilds schedules for all enabled EPG channels.
    """
    from app.database import async_session_factory
    async with async_session_factory() as db:
        result = await db.execute(text("SELECT id FROM epg_channels WHERE enabled = 1"))
        channel_ids = [row[0] for row in result.fetchall()]

    if not channel_ids:
        logger.info("EPG schedule rebuild: no enabled channels found.")
        return

    logger.info(f"EPG scheduled rebuild: {len(channel_ids)} channels...")
    for channel_id in channel_ids:
        await _rebuild_channel_schedule(channel_id)
    logger.info(f"EPG scheduled rebuild complete: {len(channel_ids)} channels processed.")
