"""
Live TV API Router — Milestone I / Session 36.

Endpoints:
  GET    /live-tv/channels                  — List channels (disabled-source filter,
                                              dead-channel filter optional).
  POST   /live-tv/add-stream                — Manually add a single stream.
  POST   /live-tv/import-m3u               — Import from M3U playlist URL.
  POST   /live-tv/health-check              — Trigger online-status probe.
  DELETE /live-tv/channels/{id}             — Remove a single channel.
  POST   /live-tv/channels/{id}/favorite    — Toggle is_favorite on a channel.

Source management:
  GET    /live-tv/sources                   — List all M3U sources with stats.
  PATCH  /live-tv/sources/{id}/toggle       — Enable / disable a source.
  POST   /live-tv/sources/{id}/refresh      — Re-fetch M3U and sync channels (add new, remove stale).
  DELETE /live-tv/sources/{id}              — Delete source + all its channels.

Group management (Session 36):
  GET    /live-tv/groups                    — List distinct group names with
                                              current sort_order and channel count.
  PATCH  /live-tv/groups/reorder            — Bulk-set sort_order for groups.
  DELETE /live-tv/groups/{group_name}       — Delete all channels in a group.

Session 34: browser User-Agent for M3U fetch; improved error handling.
Session 35: LiveTvSource table wired in; include_disabled query param;
            source CRUD endpoints; dead-channel filter default.
Session 36: is_favorite toggle endpoint; sort_order applied to channel list
            ordering; group management endpoints; serializer updated.
"""

import datetime
import logging
import re
import urllib.parse
from typing import Optional, List

import httpx
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, func, delete as sa_delete, update as sa_update, distinct
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session, async_session_factory
from app.models import LiveTvChannel, LiveTvSource

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/live-tv", tags=["live-tv"])

PROBE_TIMEOUT = 8

M3U_FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/plain, text/html, application/x-mpegurl, */*",
    "Accept-Language": "en-US,en;q=0.9",
}


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class AddStreamRequest(BaseModel):
    name: str = Field(..., description="Display name for the channel")
    stream_url: str = Field(..., description="Direct stream URL (HLS/MPEG-TS)")
    logo_url: Optional[str] = Field(None)
    group_name: Optional[str] = Field(None)


class ImportM3URequest(BaseModel):
    url: Optional[str] = Field(None)
    content: Optional[str] = Field(None)
    group_filter: Optional[str] = Field(None, description="Only import channels whose group-title matches (case-insensitive). Leave blank to import all.")
    label: Optional[str] = Field(None, description="Friendly label for source (auto-derived if omitted)")


class ReorderGroupsRequest(BaseModel):
    """
    Body for PATCH /live-tv/groups/reorder.
    groups is an ordered list of group names — index 0 = top.
    The endpoint assigns sort_order = index*10 to all channels in each group.
    """
    groups: List[str] = Field(..., description="Group names in desired display order")


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------

def _serialize_channel(ch: LiveTvChannel) -> dict:
    return {
        "id":           ch.id,
        "name":         ch.name,
        "logo_url":     ch.logo_url,
        "stream_url":   ch.stream_url,
        "group_name":   ch.group_name,
        "channel_type": ch.channel_type,
        "is_online":    ch.is_online,
        "is_favorite":  ch.is_favorite,
        "sort_order":   ch.sort_order,
        "last_checked": ch.last_checked.isoformat() if ch.last_checked else None,
        "source_m3u":   ch.source_m3u,
        "created_at":   ch.created_at.isoformat() if ch.created_at else None,
    }


def _serialize_source(src: LiveTvSource) -> dict:
    return {
        "id":               src.id,
        "label":            src.label,
        "url":              src.url,
        "enabled":          src.enabled,
        "channel_count":    src.channel_count,
        "group_filter":     src.group_filter,
        "created_at":       src.created_at.isoformat() if src.created_at else None,
        "last_imported_at": src.last_imported_at.isoformat() if src.last_imported_at else None,
    }


def _derive_label(url: str) -> str:
    clean = url.split("?")[0].rstrip("/")
    parts = clean.split("/")
    for part in reversed(parts):
        if part:
            label = re.sub(r"\.(m3u8?|txt)$", "", part, flags=re.IGNORECASE)
            if label:
                return label[:200]
    try:
        return urllib.parse.urlparse(url).hostname or url[:200]
    except Exception:
        return url[:200]


# ---------------------------------------------------------------------------
# M3U parser
# ---------------------------------------------------------------------------

def _parse_m3u(content: str, source_label: Optional[str] = None) -> list:
    channels = []
    lines = content.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("#EXTINF"):
            name = logo_url = group_name = None

            m = re.search(r'tvg-name="([^"]*)"', line)
            if m:
                name = m.group(1).strip()

            m = re.search(r'tvg-logo="([^"]*)"', line)
            if m:
                logo_url = m.group(1).strip() or None

            m = re.search(r'group-title="([^"]*)"', line)
            if m:
                group_name = m.group(1).strip() or None

            comma_idx = line.rfind(",")
            if comma_idx >= 0:
                display_name = line[comma_idx + 1:].strip()
                if display_name:
                    name = name or display_name

            name = name or "Unknown Channel"

            j = i + 1
            stream_url = None
            while j < len(lines):
                next_line = lines[j].strip()
                if next_line and not next_line.startswith("#"):
                    stream_url = next_line
                    i = j
                    break
                j += 1

            if stream_url and (stream_url.startswith("http://") or stream_url.startswith("https://")):
                channels.append({
                    "name":       name,
                    "stream_url": stream_url,
                    "logo_url":   logo_url,
                    "group_name": group_name,
                    "source_m3u": source_label,
                })
        i += 1
    return channels


# ---------------------------------------------------------------------------
# Endpoints — channels
# ---------------------------------------------------------------------------

@router.get("/channels")
async def list_live_channels(
    include_disabled: bool = Query(
        False,
        description="Include channels from disabled sources (web UI uses true).",
    ),
    db: AsyncSession = Depends(get_db_session),
):
    """
    List live TV channels.

    Ordering: sort_order ASC (per-group custom order), then group_name ASC,
    then name ASC within each group.

    Default (include_disabled=false): channels from disabled LiveTvSources
    are excluded. Android always uses the default.
    Web UI passes include_disabled=true to show everything.
    """
    from sqlalchemy import text

    if include_disabled:
        stmt = select(LiveTvChannel).order_by(
            LiveTvChannel.sort_order,
            LiveTvChannel.group_name.nullslast(),
            LiveTvChannel.name,
        )
        result = await db.execute(stmt)
        channels = result.scalars().all()
    else:
        # Exclude channels from disabled sources; manually-added always included.
        raw = await db.execute(text("""
            SELECT ltc.id
            FROM live_tv_channels ltc
            LEFT JOIN live_tv_sources lts ON ltc.source_m3u = lts.url
            WHERE lts.enabled IS NULL OR lts.enabled = 1
            ORDER BY ltc.sort_order ASC, ltc.group_name NULLS LAST, ltc.name ASC
        """))
        included_ids = [row[0] for row in raw.fetchall()]

        if not included_ids:
            return {"channels": [], "total": 0}

        stmt = (
            select(LiveTvChannel)
            .where(LiveTvChannel.id.in_(included_ids))
            .order_by(
                LiveTvChannel.sort_order,
                LiveTvChannel.group_name.nullslast(),
                LiveTvChannel.name,
            )
        )
        result = await db.execute(stmt)
        channels = result.scalars().all()

    return {
        "channels": [_serialize_channel(ch) for ch in channels],
        "total":    len(channels),
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
        is_online=None,
        source_m3u=None,
        is_favorite=False,
        sort_order=999,
        created_at=datetime.datetime.utcnow(),
    )
    db.add(channel)
    await db.commit()
    await db.refresh(channel)

    logger.info(f"Live TV: added stream '{request.name}'")
    return {"status": "added", "channel": _serialize_channel(channel)}


@router.post("/import-m3u")
async def import_m3u(
    request: ImportM3URequest,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Import channels from an M3U playlist URL or raw content.
    Auto-creates/updates a LiveTvSource record when importing from a URL.
    Duplicate stream URLs are silently skipped.
    """
    if not request.url and not request.content:
        raise HTTPException(status_code=400, detail="Provide either 'url' or 'content'")

    content = request.content
    source_label = request.url or "manual_import"

    if request.url:
        try:
            async with httpx.AsyncClient(
                timeout=30,
                follow_redirects=True,
                headers=M3U_FETCH_HEADERS,
            ) as client:
                resp = await client.get(request.url)
                if not resp.is_success:
                    logger.error(f"Live TV M3U fetch failed: HTTP {resp.status_code} from {request.url}")
                    raise HTTPException(
                        status_code=502,
                        detail=f"M3U host returned HTTP {resp.status_code}.",
                    )
                content = resp.text
        except httpx.TimeoutException:
            raise HTTPException(status_code=502, detail="Request timed out fetching M3U URL.")
        except httpx.HTTPError as e:
            raise HTTPException(status_code=502, detail=f"Network error: {e}")

    parsed = _parse_m3u(content, source_label=source_label)

    if not parsed:
        return {
            "status":       "complete",
            "imported":     0,
            "skipped":      0,
            "total_parsed": 0,
            "warning":      "No channels found. The URL may point to an HLS stream, not an M3U channel list.",
        }

    if request.group_filter:
        parsed = [ch for ch in parsed if (ch.get("group_name") or "").lower() == request.group_filter.lower()]

    existing_stmt = select(LiveTvChannel.stream_url)
    existing_result = await db.execute(existing_stmt)
    existing_urls = {row[0] for row in existing_result.fetchall() if row[0]}

    imported = skipped = 0
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
            is_favorite=False,
            sort_order=999,
            created_at=now,
        )
        db.add(channel)
        existing_urls.add(ch_data["stream_url"])
        imported += 1

    await db.commit()

    if request.url:
        await _upsert_source(db, url=request.url, label=request.label, now=now, group_filter=request.group_filter or None)

    logger.info(f"Live TV M3U import: {imported} imported, {skipped} skipped from {source_label}")
    return {
        "status":       "complete",
        "imported":     imported,
        "skipped":      skipped,
        "total_parsed": len(parsed) + skipped,
        "source":       source_label,
    }


@router.post("/health-check")
async def trigger_health_check(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db_session),
):
    """Manually trigger an online-status probe for all live TV channels."""
    stmt = select(LiveTvChannel)
    result = await db.execute(stmt)
    channels = result.scalars().all()

    if not channels:
        return {"status": "complete", "message": "No live TV channels to probe."}

    background_tasks.add_task(_probe_all_channels)
    logger.info(f"Live TV health check triggered for {len(channels)} channels")
    return {
        "status":        "probing",
        "message":       f"Probing {len(channels)} channels in background.",
        "channel_count": len(channels),
    }


@router.delete("/channels/{channel_id}")
async def delete_live_channel(
    channel_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    """Remove a single live TV channel."""
    stmt = select(LiveTvChannel).where(LiveTvChannel.id == channel_id)
    result = await db.execute(stmt)
    channel = result.scalar_one_or_none()

    if channel is None:
        raise HTTPException(status_code=404, detail="Live TV channel not found")

    name = channel.name
    source_url = channel.source_m3u
    await db.delete(channel)
    await db.commit()

    if source_url:
        await _refresh_source_count(db, source_url)

    logger.info(f"Live TV: deleted channel '{name}'")
    return {"status": "deleted", "name": name}


@router.post("/channels/{channel_id}/favorite")
async def toggle_favorite(
    channel_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Toggle the is_favorite flag on a live TV channel.

    Returns the new state so the Android app can update its UI immediately
    without a full reload.
    """
    stmt = select(LiveTvChannel).where(LiveTvChannel.id == channel_id)
    result = await db.execute(stmt)
    channel = result.scalar_one_or_none()

    if channel is None:
        raise HTTPException(status_code=404, detail="Live TV channel not found")

    channel.is_favorite = not channel.is_favorite
    await db.commit()
    await db.refresh(channel)

    state = "favorited" if channel.is_favorite else "unfavorited"
    logger.info(f"Live TV: channel '{channel.name}' {state}")
    return {
        "status":      state,
        "id":          channel.id,
        "is_favorite": channel.is_favorite,
    }


# ---------------------------------------------------------------------------
# Endpoints — sources
# ---------------------------------------------------------------------------

@router.get("/sources")
async def list_sources(db: AsyncSession = Depends(get_db_session)):
    """List all M3U sources with live channel counts and online/offline stats."""
    stmt = select(LiveTvSource).order_by(LiveTvSource.label)
    result = await db.execute(stmt)
    sources = result.scalars().all()

    from sqlalchemy import text
    raw = await db.execute(text("""
        SELECT source_m3u,
               SUM(CASE WHEN is_online = 1 THEN 1 ELSE 0 END) AS online_count,
               SUM(CASE WHEN is_online = 0 THEN 1 ELSE 0 END) AS offline_count,
               COUNT(*) AS total
        FROM live_tv_channels
        WHERE source_m3u IS NOT NULL
        GROUP BY source_m3u
    """))
    stats: dict[str, dict] = {}
    for row in raw.fetchall():
        stats[row[0]] = {"online": row[1] or 0, "offline": row[2] or 0, "total": row[3] or 0}

    serialized = []
    for src in sources:
        s = _serialize_source(src)
        s.update(stats.get(src.url, {"online": 0, "offline": 0, "total": 0}))
        serialized.append(s)

    return {"sources": serialized, "total": len(serialized)}


@router.patch("/sources/{source_id}/toggle")
async def toggle_source(source_id: int, db: AsyncSession = Depends(get_db_session)):
    """Enable or disable a source."""
    stmt = select(LiveTvSource).where(LiveTvSource.id == source_id)
    result = await db.execute(stmt)
    source = result.scalar_one_or_none()

    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

    source.enabled = not source.enabled
    await db.commit()
    await db.refresh(source)

    state = "enabled" if source.enabled else "disabled"
    logger.info(f"Live TV source '{source.label}' {state}")
    return {"status": state, "source": _serialize_source(source)}


@router.delete("/sources/{source_id}")
async def delete_source(source_id: int, db: AsyncSession = Depends(get_db_session)):
    """Delete a source and all channels imported from it."""
    stmt = select(LiveTvSource).where(LiveTvSource.id == source_id)
    result = await db.execute(stmt)
    source = result.scalar_one_or_none()

    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

    source_url = source.url
    label = source.label

    del_stmt = sa_delete(LiveTvChannel).where(LiveTvChannel.source_m3u == source_url)
    del_result = await db.execute(del_stmt)
    deleted_channels = del_result.rowcount

    await db.delete(source)
    await db.commit()

    logger.info(f"Live TV: deleted source '{label}' and {deleted_channels} channels")
    return {"status": "deleted", "label": label, "channels_deleted": deleted_channels}


@router.post("/sources/{source_id}/refresh")
async def refresh_source(source_id: int, db: AsyncSession = Depends(get_db_session)):
    """
    Re-fetch a source's M3U URL and sync channels:
      - Adds channels present in M3U but not in DB.
      - Removes channels that belong to this source but are no longer in M3U.
      - Preserves favorites, sort_order, and is_online on existing channels.
    """
    stmt = select(LiveTvSource).where(LiveTvSource.id == source_id)
    result = await db.execute(stmt)
    source = result.scalar_one_or_none()

    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

    # Fetch fresh M3U content
    try:
        async with httpx.AsyncClient(
            timeout=30,
            follow_redirects=True,
            headers=M3U_FETCH_HEADERS,
        ) as client:
            resp = await client.get(source.url)
            if not resp.is_success:
                raise HTTPException(
                    status_code=502,
                    detail=f"M3U host returned HTTP {resp.status_code}.",
                )
            content = resp.text
    except httpx.TimeoutException:
        raise HTTPException(status_code=502, detail="Request timed out fetching M3U URL.")
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Network error: {e}")

    parsed = _parse_m3u(content, source_label=source.url)

    # Apply stored group filter if set
    if source.group_filter:
        parsed = [ch for ch in parsed if (ch.get("group_name") or "").lower() == source.group_filter.lower()]

    # Build set of stream URLs currently in M3U
    m3u_urls = {ch["stream_url"] for ch in parsed}

    # Fetch existing channels for this source
    existing_stmt = select(LiveTvChannel).where(LiveTvChannel.source_m3u == source.url)
    existing_result = await db.execute(existing_stmt)
    existing_channels = existing_result.scalars().all()
    existing_url_map = {ch.stream_url: ch for ch in existing_channels}

    now = datetime.datetime.utcnow()
    added = removed = 0

    # Add new channels not already in DB
    for ch_data in parsed:
        if ch_data["stream_url"] not in existing_url_map:
            channel = LiveTvChannel(
                name=ch_data["name"],
                stream_url=ch_data["stream_url"],
                logo_url=ch_data["logo_url"],
                group_name=ch_data["group_name"],
                channel_type="real",
                is_online=None,
                source_m3u=source.url,
                is_favorite=False,
                sort_order=999,
                created_at=now,
            )
            db.add(channel)
            added += 1

    # Remove channels no longer in M3U (only from this source)
    for stream_url, channel in existing_url_map.items():
        if stream_url not in m3u_urls:
            await db.delete(channel)
            removed += 1

    # Update source metadata
    source.last_imported_at = now
    source.channel_count = len(m3u_urls)

    await db.commit()

    logger.info(
        f"Live TV source refresh '{source.label}': "
        f"+{added} added, -{removed} removed, "
        f"{len(existing_url_map) - removed} unchanged"
    )
    return {
        "status":    "refreshed",
        "label":     source.label,
        "added":     added,
        "removed":   removed,
        "unchanged": len(existing_url_map) - removed,
    }


async def refresh_all_sources(db: AsyncSession) -> dict:
    """
    Shared sync logic used by the scheduler to refresh all enabled sources.
    Re-fetches each source's M3U and syncs channels (add new, remove stale).
    """
    stmt = select(LiveTvSource).where(LiveTvSource.enabled == True)
    result = await db.execute(stmt)
    sources = result.scalars().all()

    total_added = total_removed = 0

    for source in sources:
        try:
            async with httpx.AsyncClient(
                timeout=30,
                follow_redirects=True,
                headers=M3U_FETCH_HEADERS,
            ) as client:
                resp = await client.get(source.url)
                if not resp.is_success:
                    logger.warning(
                        f"Live TV scheduler refresh: HTTP {resp.status_code} from {source.url}"
                    )
                    continue
                content = resp.text

            parsed = _parse_m3u(content, source_label=source.url)

            # Apply stored group filter if set
            if source.group_filter:
                parsed = [ch for ch in parsed if (ch.get("group_name") or "").lower() == source.group_filter.lower()]

            m3u_urls = {ch["stream_url"] for ch in parsed}

            existing_stmt = select(LiveTvChannel).where(LiveTvChannel.source_m3u == source.url)
            existing_result = await db.execute(existing_stmt)
            existing_channels = existing_result.scalars().all()
            existing_url_map = {ch.stream_url: ch for ch in existing_channels}

            now = datetime.datetime.utcnow()
            added = removed = 0

            for ch_data in parsed:
                if ch_data["stream_url"] not in existing_url_map:
                    channel = LiveTvChannel(
                        name=ch_data["name"],
                        stream_url=ch_data["stream_url"],
                        logo_url=ch_data["logo_url"],
                        group_name=ch_data["group_name"],
                        channel_type="real",
                        is_online=None,
                        source_m3u=source.url,
                        is_favorite=False,
                        sort_order=999,
                        created_at=now,
                    )
                    db.add(channel)
                    added += 1

            for stream_url, channel in existing_url_map.items():
                if stream_url not in m3u_urls:
                    await db.delete(channel)
                    removed += 1

            source.last_imported_at = now
            source.channel_count = len(m3u_urls)
            await db.commit()

            total_added += added
            total_removed += removed
            logger.info(
                f"Live TV scheduler refresh '{source.label}': "
                f"+{added} added, -{removed} removed"
            )

        except Exception as e:
            logger.error(f"Live TV scheduler refresh failed for '{source.url}': {e}")
            continue

    return {"sources": len(sources), "added": total_added, "removed": total_removed}


# ---------------------------------------------------------------------------
# Endpoints — group management (Session 36)
# ---------------------------------------------------------------------------

@router.get("/groups")
async def list_groups(db: AsyncSession = Depends(get_db_session)):
    """
    List all distinct group names with their current sort_order and channel count.
    The sort_order returned is the minimum sort_order across all channels in the
    group (they are all set to the same value by the reorder endpoint).
    """
    from sqlalchemy import text
    raw = await db.execute(text("""
        SELECT
            COALESCE(group_name, 'Other') AS gname,
            MIN(sort_order)               AS sort_ord,
            COUNT(*)                      AS ch_count,
            SUM(CASE WHEN is_online = 1 THEN 1 ELSE 0 END) AS online_count
        FROM live_tv_channels
        GROUP BY COALESCE(group_name, 'Other')
        ORDER BY MIN(sort_order) ASC, COALESCE(group_name, 'Other') ASC
    """))
    groups = [
        {
            "name":         row[0],
            "sort_order":   row[1],
            "channel_count": row[2],
            "online_count": row[3] or 0,
        }
        for row in raw.fetchall()
    ]
    return {"groups": groups, "total": len(groups)}


@router.patch("/groups/reorder")
async def reorder_groups(
    request: ReorderGroupsRequest,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Set sort_order for groups in bulk.

    The request body is {"groups": ["Local OTA", "News", "Sports", ...]}.
    Each group name is assigned sort_order = index * 10.
    Groups not mentioned in the list are left unchanged.
    """
    updated = 0
    for idx, group_name in enumerate(request.groups):
        sort_val = idx * 10
        if group_name == "Other":
            # "Other" maps to null group_name in the DB
            stmt = (
                sa_update(LiveTvChannel)
                .where(LiveTvChannel.group_name.is_(None))
                .values(sort_order=sort_val)
            )
        else:
            stmt = (
                sa_update(LiveTvChannel)
                .where(LiveTvChannel.group_name == group_name)
                .values(sort_order=sort_val)
            )
        result = await db.execute(stmt)
        updated += result.rowcount

    await db.commit()
    logger.info(f"Live TV group reorder: {len(request.groups)} groups, {updated} channels updated")
    return {
        "status":          "reordered",
        "groups_set":      len(request.groups),
        "channels_updated": updated,
    }


@router.delete("/groups/{group_name}")
async def delete_group(
    group_name: str,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Delete all channels whose group_name matches the given value.
    Use the special value '__other__' to delete channels with no group_name.
    Returns the count of deleted channels.
    """
    if group_name == "__other__":
        stmt = sa_delete(LiveTvChannel).where(LiveTvChannel.group_name.is_(None))
    else:
        stmt = sa_delete(LiveTvChannel).where(LiveTvChannel.group_name == group_name)

    result = await db.execute(stmt)
    deleted = result.rowcount
    await db.commit()

    # Refresh source counts for any affected sources
    # (bulk delete doesn't trigger per-channel refresh, so do a full pass)
    src_stmt = select(LiveTvSource)
    src_result = await db.execute(src_stmt)
    for src in src_result.scalars().all():
        await _refresh_source_count(db, src.url)

    logger.info(f"Live TV: deleted group '{group_name}' — {deleted} channels removed")
    return {"status": "deleted", "group_name": group_name, "channels_deleted": deleted}



# ---------------------------------------------------------------------------
# Wipe all live TV channels
# ---------------------------------------------------------------------------

@router.delete("/channels/all")
async def delete_all_live_channels(
    confirm: bool = Query(
        False,
        description="Must be true to execute. Prevents accidental wipes.",
    ),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Delete ALL live TV channels in one shot.

    Protected by ?confirm=true. Also clears channel_count on all sources
    so they accurately reflect zero after the wipe.
    """
    if not confirm:
        raise HTTPException(
            status_code=400,
            detail="Pass ?confirm=true to execute this operation.",
        )

    result = await db.execute(sa_delete(LiveTvChannel))
    deleted = result.rowcount
    await db.commit()

    # Zero out all source channel counts
    src_result = await db.execute(select(LiveTvSource))
    for src in src_result.scalars().all():
        src.channel_count = 0
    await db.commit()

    logger.warning(f"Live TV WIPE ALL: {deleted} channels deleted")
    return {"status": "wiped", "channels_deleted": deleted}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _upsert_source(
    db: AsyncSession,
    url: str,
    label: Optional[str],
    now: datetime.datetime,
    group_filter: Optional[str] = None,
) -> None:
    stmt = select(LiveTvSource).where(LiveTvSource.url == url)
    result = await db.execute(stmt)
    source = result.scalar_one_or_none()

    count_stmt = select(func.count()).select_from(LiveTvChannel).where(
        LiveTvChannel.source_m3u == url
    )
    count_result = await db.execute(count_stmt)
    channel_count = count_result.scalar_one()

    if source is None:
        source = LiveTvSource(
            url=url,
            label=label or _derive_label(url),
            enabled=True,
            channel_count=channel_count,
            group_filter=group_filter,
            created_at=now,
            last_imported_at=now,
        )
        db.add(source)
    else:
        if label:
            source.label = label
        if group_filter is not None:
            source.group_filter = group_filter
        source.channel_count = channel_count
        source.last_imported_at = now

    await db.commit()


async def _refresh_source_count(db: AsyncSession, source_url: str) -> None:
    src_stmt = select(LiveTvSource).where(LiveTvSource.url == source_url)
    src_result = await db.execute(src_stmt)
    source = src_result.scalar_one_or_none()
    if source is None:
        return

    count_stmt = select(func.count()).select_from(LiveTvChannel).where(
        LiveTvChannel.source_m3u == source_url
    )
    count_result = await db.execute(count_stmt)
    source.channel_count = count_result.scalar_one()
    await db.commit()


# ---------------------------------------------------------------------------
# Background health probe
# ---------------------------------------------------------------------------

async def _probe_all_channels():
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
                        # Don't follow redirects — a 3xx response means the channel
                        # server is alive and responding (e.g. Tunarr returns 302 before
                        # spinning up FFmpeg). Following the redirect causes a timeout
                        # waiting for FFmpeg to produce the first HLS segment.
                        resp = await client.head(channel.stream_url, follow_redirects=False)
                        if resp.status_code == 405:
                            get_resp = await client.get(
                                channel.stream_url,
                                follow_redirects=False,
                                headers={"Range": "bytes=0-0"},
                            )
                            is_online = get_resp.status_code < 500
                        else:
                            # 2xx, 3xx = online; 4xx, 5xx = offline
                            is_online = resp.status_code < 400 or resp.status_code in (301, 302, 303, 307, 308)
                    except Exception:
                        is_online = False

                    channel.is_online = is_online
                    channel.last_checked = now

            await db.commit()
            online_count = sum(1 for ch in channels if ch.is_online)
            logger.info(f"Live TV health probe complete: {online_count}/{len(channels)} online")

        except Exception as e:
            logger.error(f"Live TV health probe failed: {e}")


probe_all_channels = _probe_all_channels
