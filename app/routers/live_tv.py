"""
Live TV API Router — Milestone B Stub.

Endpoints:
- GET  /live-tv/channels      — List all live TV channels.
- POST /live-tv/add-stream    — Manually add a single stream.
- POST /live-tv/import-m3u   — Import channels from an M3U playlist.
- POST /live-tv/health-check  — Manually trigger an online status probe.

This is the stub implementation for Milestone B. The live_tv_channels table
is created and the basic CRUD + M3U import works. Full EPG scheduling,
pseudo-linear channels, and Android TV integration are Milestone I.

M3U parser handles standard M3U format including:
  - #EXTM3U header
  - #EXTINF with tvg-name, tvg-logo, group-title attributes
  - Stream URL on the line following each #EXTINF
  - Both http:// and https:// stream URLs
"""

import datetime
import logging
import re
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session, async_session_factory
from app.models import LiveTvChannel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/live-tv", tags=["live-tv"])

# Timeout for live stream health probes (seconds)
PROBE_TIMEOUT = 8


# --- Request Models ---

class AddStreamRequest(BaseModel):
    name: str = Field(..., description="Display name for the channel")
    stream_url: str = Field(..., description="Direct stream URL (HLS/MPEG-TS)")
    logo_url: Optional[str] = Field(None, description="Channel logo image URL")
    group_name: Optional[str] = Field(None, description="Group/category label")


class ImportM3URequest(BaseModel):
    url: Optional[str] = Field(None, description="URL to fetch the M3U playlist from")
    content: Optional[str] = Field(None, description="Raw M3U playlist text (alternative to URL)")
    group_filter: Optional[str] = Field(None, description="Only import channels from this group-title")


# --- Serializer ---

def _serialize_channel(ch: LiveTvChannel) -> dict:
    return {
        "id": ch.id,
        "name": ch.name,
        "logo_url": ch.logo_url,
        "stream_url": ch.stream_url,
        "group_name": ch.group_name,
        "channel_type": ch.channel_type,
        "is_online": ch.is_online,
        "last_checked": ch.last_checked.isoformat() if ch.last_checked else None,
        "source_m3u": ch.source_m3u,
        "created_at": ch.created_at.isoformat() if ch.created_at else None,
    }


# --- M3U Parser ---

def _parse_m3u(content: str, source_label: Optional[str] = None) -> list:
    """
    Parse M3U playlist text and return a list of channel dicts.

    Handles standard M3U format:
      #EXTM3U
      #EXTINF:-1 tvg-name="Channel Name" tvg-logo="http://..." group-title="Group",Display Name
      http://stream.url/path

    Returns list of dicts with keys: name, stream_url, logo_url, group_name, source_m3u
    """
    channels = []
    lines = content.splitlines()

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        if line.startswith("#EXTINF"):
            # Parse attributes from the #EXTINF line
            name = None
            logo_url = None
            group_name = None

            # tvg-name attribute
            tvg_name_match = re.search(r'tvg-name="([^"]*)"', line)
            if tvg_name_match:
                name = tvg_name_match.group(1).strip()

            # tvg-logo attribute
            tvg_logo_match = re.search(r'tvg-logo="([^"]*)"', line)
            if tvg_logo_match:
                logo_url = tvg_logo_match.group(1).strip() or None

            # group-title attribute
            group_match = re.search(r'group-title="([^"]*)"', line)
            if group_match:
                group_name = group_match.group(1).strip() or None

            # Display name is after the last comma on the #EXTINF line
            comma_idx = line.rfind(",")
            if comma_idx >= 0:
                display_name = line[comma_idx + 1:].strip()
                if display_name:
                    # Use display name if tvg-name wasn't found
                    name = name or display_name

            name = name or "Unknown Channel"

            # The next non-empty, non-comment line is the stream URL
            j = i + 1
            stream_url = None
            while j < len(lines):
                next_line = lines[j].strip()
                if next_line and not next_line.startswith("#"):
                    stream_url = next_line
                    i = j  # advance outer loop past the URL line
                    break
                j += 1

            if stream_url and (stream_url.startswith("http://") or stream_url.startswith("https://")):
                channels.append({
                    "name": name,
                    "stream_url": stream_url,
                    "logo_url": logo_url,
                    "group_name": group_name,
                    "source_m3u": source_label,
                })

        i += 1

    return channels


# --- Endpoints ---

@router.get("/channels")
async def list_live_channels(
    db: AsyncSession = Depends(get_db_session),
):
    """List all live TV channels ordered by group then name."""
    stmt = select(LiveTvChannel).order_by(
        LiveTvChannel.group_name.nullslast(),
        LiveTvChannel.name,
    )
    result = await db.execute(stmt)
    channels = result.scalars().all()

    return {
        "channels": [_serialize_channel(ch) for ch in channels],
        "total": len(channels),
    }


@router.post("/add-stream")
async def add_stream(
    request: AddStreamRequest,
    db: AsyncSession = Depends(get_db_session),
):
    """Manually add a single live stream channel."""
    if not request.stream_url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="stream_url must start with http:// or https://")

    channel = LiveTvChannel(
        name=request.name,
        logo_url=request.logo_url,
        stream_url=request.stream_url,
        group_name=request.group_name,
        channel_type="real",
        is_online=None,  # Not yet probed
        source_m3u=None,
        created_at=datetime.datetime.utcnow(),
    )
    db.add(channel)
    await db.commit()
    await db.refresh(channel)

    logger.info(f"Live TV: added stream '{request.name}' -> {request.stream_url}")
    return {"status": "added", "channel": _serialize_channel(channel)}


@router.post("/import-m3u")
async def import_m3u(
    request: ImportM3URequest,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Import channels from an M3U playlist.

    Provide either a URL (fetched server-side) or raw M3U text content.
    Optionally filter to a specific group-title with group_filter.

    Duplicate stream URLs are skipped silently.
    Returns counts of imported and skipped channels.
    """
    if not request.url and not request.content:
        raise HTTPException(status_code=400, detail="Provide either 'url' or 'content'")

    # Fetch M3U content if URL provided
    content = request.content
    source_label = request.url or "manual_import"

    if request.url:
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(request.url, follow_redirects=True)
                resp.raise_for_status()
                content = resp.text
        except httpx.HTTPError as e:
            raise HTTPException(status_code=502, detail=f"Failed to fetch M3U from URL: {e}")

    # Parse channels
    parsed = _parse_m3u(content, source_label=source_label)

    if not parsed:
        return {"status": "complete", "imported": 0, "skipped": 0, "total_parsed": 0}

    # Apply group filter if requested
    if request.group_filter:
        parsed = [
            ch for ch in parsed
            if (ch.get("group_name") or "").lower() == request.group_filter.lower()
        ]

    # Fetch existing stream URLs to detect duplicates
    existing_stmt = select(LiveTvChannel.stream_url)
    existing_result = await db.execute(existing_stmt)
    existing_urls = {row[0] for row in existing_result.fetchall() if row[0]}

    imported = 0
    skipped = 0
    now = datetime.datetime.utcnow()

    for ch_data in parsed:
        if ch_data["stream_url"] in existing_urls:
            skipped += 1
            continue

        channel = LiveTvChannel(
            name=ch_data["name"],
            stream_url=ch_data["stream_url"],
            logo_url=ch_data["logo_url"],
            group_name=ch_data["group_name"],
            channel_type="real",
            is_online=None,
            source_m3u=ch_data["source_m3u"],
            created_at=now,
        )
        db.add(channel)
        existing_urls.add(ch_data["stream_url"])
        imported += 1

    await db.commit()

    logger.info(f"Live TV M3U import: {imported} imported, {skipped} skipped from {source_label}")
    return {
        "status": "complete",
        "imported": imported,
        "skipped": skipped,
        "total_parsed": len(parsed) + skipped,
        "source": source_label,
    }


@router.post("/health-check")
async def trigger_health_check(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Manually trigger an online status probe for all live TV channels.

    The probe runs in the background — returns immediately with a count
    of channels to be probed. The scheduler also runs this every 15 minutes.
    """
    stmt = select(LiveTvChannel)
    result = await db.execute(stmt)
    channels = result.scalars().all()

    if not channels:
        return {"status": "complete", "message": "No live TV channels to probe."}

    background_tasks.add_task(_probe_all_channels)

    logger.info(f"Live TV health check triggered for {len(channels)} channels")
    return {
        "status": "probing",
        "message": f"Probing {len(channels)} channels in background.",
        "channel_count": len(channels),
    }


@router.delete("/channels/{channel_id}")
async def delete_live_channel(
    channel_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    """Remove a live TV channel."""
    stmt = select(LiveTvChannel).where(LiveTvChannel.id == channel_id)
    result = await db.execute(stmt)
    channel = result.scalar_one_or_none()

    if channel is None:
        raise HTTPException(status_code=404, detail="Live TV channel not found")

    name = channel.name
    await db.delete(channel)
    await db.commit()

    logger.info(f"Live TV: deleted channel '{name}'")
    return {"status": "deleted", "name": name}


# --- Background health probe ---

async def _probe_all_channels():
    """
    Probe all live TV channels for online status.
    Called by the scheduler every 15 minutes and on manual trigger.
    Uses a HEAD request — fast, minimal data transfer.
    Falls back to a short GET if HEAD returns 405.
    """
    async with async_session_factory() as db:
        try:
            stmt = select(LiveTvChannel)
            result = await db.execute(stmt)
            channels = result.scalars().all()

            now = datetime.datetime.utcnow()

            async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as client:
                for channel in channels:
                    if not channel.stream_url:
                        continue
                    try:
                        resp = await client.head(channel.stream_url, follow_redirects=True)
                        # 2xx or 3xx = online. 4xx/5xx = offline.
                        # 405 Method Not Allowed = server doesn't support HEAD, try GET
                        if resp.status_code == 405:
                            get_resp = await client.get(
                                channel.stream_url,
                                follow_redirects=True,
                                headers={"Range": "bytes=0-0"},
                            )
                            is_online = get_resp.status_code < 400
                        else:
                            is_online = resp.status_code < 400
                    except Exception:
                        is_online = False

                    channel.is_online = is_online
                    channel.last_checked = now

            await db.commit()
            online_count = sum(1 for ch in channels if ch.is_online)
            logger.info(
                f"Live TV health probe complete: "
                f"{online_count}/{len(channels)} channels online"
            )

        except Exception as e:
            logger.error(f"Live TV health probe failed: {e}")


# Expose for scheduler import
probe_all_channels = _probe_all_channels
