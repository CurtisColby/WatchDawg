"""
WatchDawg — Pseudo-Channel Scheduler (Session 40).

Builds 48-hour rolling EPG schedules for each configured EPG channel.
Runs every 6 hours via the background scheduler, and on-demand when
channels are created or edited.

Schedule generation logic per source_type:

  iptv_favorites  — Real IPTV. No schedule pre-computation. Skipped here.
                    The EPG endpoint handles these as live slots directly.

  plex_movie      — Fetches movies from the specified Plex library section,
                    optionally filtered by genre. Shuffles or sequences them.
                    Each movie fills one slot (its runtime). Loops when exhausted.

  plex_tv         — Fetches TV series from Plex. Applies episode_budget per series
                    per day so no single show dominates the channel. Rotates through
                    all series, advancing each one's episode pointer daily.

  watchdawg       — Pulls resolved videos from the WatchDawg DB matching the
                    channel's genre_filter (genre_tags on channels table).
                    If watchdawg_source_id is set, pulls only from that specific
                    WatchDawg channel (ignoring genre_filter). This allows pinning
                    an EPG channel to a single scraped source.
                    Shuffles or sequences. Skips unresolved videos.

Episode pointer tracking (plex_tv):
  A separate table epg_tv_pointers stores the last-played episode index
  per (epg_channel_id, show_rating_key) so the scheduler can resume
  where it left off across rebuilds without replaying episodes.

Primetime boost (7 PM – 11 PM local):
  When primetime_boost=True, the scheduler preferentially fills 7-11 PM
  slots with highest-rated content (Plex audience_rating or reddit_score).

Schedule window: 48 hours from now.
Cleanup: slots older than 2 hours are deleted before each rebuild.
"""

import datetime
import logging
import random
from typing import List, Optional

import httpx

from app.database import async_session_factory
from app.encryption import decrypt_value

logger = logging.getLogger(__name__)

# How many hours of schedule to generate per rebuild
SCHEDULE_HOURS = 48

# Delete slots older than this many hours (keeps DB clean)
CLEANUP_HOURS = 2

# Default episode budget per TV series per day when none specified
DEFAULT_EPISODES_PER_DAY = 2

# Primetime window (UTC hour range — America/Chicago is UTC-5/6)
# 7 PM CDT = 00:00 UTC, 11 PM CDT = 04:00 UTC
# We use a broad window — adjust if needed
PRIMETIME_UTC_START = 0   # midnight UTC
PRIMETIME_UTC_END = 5     # 5 AM UTC

# Plex API timeout — local network, should be fast
PLEX_TIMEOUT = 15

# Plex requires Accept: application/json or it returns XML by default
PLEX_HEADERS = {"Accept": "application/json"}

# Minimum video duration to include in schedule (seconds)
MIN_DURATION_SECONDS = 30


# ---------------------------------------------------------------------------
# Main entry point — called per channel
# ---------------------------------------------------------------------------

async def build_channel_schedule(channel_id: int):
    """
    Build a 48-hour schedule for a single EPG channel.

    1. Load channel config from DB
    2. Clean up stale slots older than CLEANUP_HOURS
    3. Determine which time slots still need filling (gap analysis)
    4. Pull content from appropriate source (Plex or WatchDawg)
    5. Write new epg_schedules rows to fill the gaps
    """
    async with async_session_factory() as db:
        from sqlalchemy import text

        # Load channel config — include watchdawg_source_id (Session 40)
        result = await db.execute(text("""
            SELECT id, channel_number, name, epg_type, source_type,
                   plex_library_key, genre_filter, episodes_per_day,
                   rotation_style, primetime_boost, enabled,
                   watchdawg_source_id
            FROM epg_channels
            WHERE id = :id
        """), {"id": channel_id})
        row = result.fetchone()

        if not row:
            logger.warning(f"EPG scheduler: channel {channel_id} not found.")
            return

        channel = {
            "id":                   row[0],
            "channel_number":       row[1],
            "name":                 row[2],
            "epg_type":             row[3],
            "source_type":          row[4],
            "plex_library_key":     row[5],
            "genre_filter":         row[6],
            "episodes_per_day":     row[7] or DEFAULT_EPISODES_PER_DAY,
            "rotation_style":       row[8] or "shuffle",
            "primetime_boost":      bool(row[9]),
            "enabled":              bool(row[10]),
            # Session 40: None means "all matching sources", int means pinned source
            "watchdawg_source_id":  row[11] if len(row) > 11 else None,
        }

        if not channel["enabled"]:
            logger.info(f"EPG scheduler: channel {channel_id} is disabled — skipping.")
            return

        # IPTV favorites — handled live by EPG endpoint, no schedule needed
        if channel["source_type"] == "iptv_favorites":
            logger.info(f"EPG scheduler: channel {channel_id} is IPTV favorites — no schedule needed.")
            return

        # Clean up old slots
        cutoff = datetime.datetime.utcnow() - datetime.timedelta(hours=CLEANUP_HOURS)
        await db.execute(text("""
            DELETE FROM epg_schedules
            WHERE epg_channel_id = :channel_id AND end_time < :cutoff
        """), {"channel_id": channel_id, "cutoff": cutoff})
        await db.commit()

        # Find the latest scheduled slot end_time (gap analysis)
        latest_result = await db.execute(text("""
            SELECT MAX(end_time) FROM epg_schedules WHERE epg_channel_id = :channel_id
        """), {"channel_id": channel_id})
        latest_row = latest_result.fetchone()

        now = datetime.datetime.utcnow()
        schedule_end = now + datetime.timedelta(hours=SCHEDULE_HOURS)

        # Start building from the later of: now, or where existing schedule ends
        build_from = now
        if latest_row and latest_row[0]:
            existing_end = latest_row[0]
            if isinstance(existing_end, str):
                # SQLite returns strings for DATETIME — parse it
                existing_end = datetime.datetime.fromisoformat(existing_end)
            if existing_end > build_from:
                build_from = existing_end

        # If already fully scheduled, nothing to do
        if build_from >= schedule_end:
            logger.info(
                f"EPG scheduler: channel {channel_id} '{channel['name']}' "
                f"already scheduled through {schedule_end.isoformat()} — nothing to do."
            )
            return

        logger.info(
            f"EPG scheduler: building schedule for channel {channel_id} '{channel['name']}' "
            f"({channel['source_type']}) from {build_from.isoformat()} to {schedule_end.isoformat()}"
        )

        # Route to the appropriate content puller
        try:
            if channel["source_type"] in ("plex_movie", "plex_tv"):
                slots = await _build_plex_schedule(channel, build_from, schedule_end, db)
            elif channel["source_type"] == "watchdawg":
                slots = await _build_watchdawg_schedule(channel, build_from, schedule_end, db)
            elif channel["source_type"] == "local_private":
                slots = await _build_local_private_schedule(channel, build_from, schedule_end)
            else:
                logger.warning(f"EPG scheduler: unknown source_type '{channel['source_type']}' for channel {channel_id}")
                return

            if not slots:
                logger.warning(
                    f"EPG scheduler: no content found for channel {channel_id} '{channel['name']}'. "
                    f"Check genre filter, Plex connection, or WatchDawg channel sources."
                )
                return

            # Write slots to DB
            inserted = 0
            for slot in slots:
                await db.execute(text("""
                    INSERT INTO epg_schedules
                        (epg_channel_id, title, subtitle, description, thumbnail_url,
                         stream_url, source_type, source_id,
                         start_time, end_time, duration_seconds, created_at)
                    VALUES
                        (:channel_id, :title, :subtitle, :description, :thumbnail_url,
                         :stream_url, :source_type, :source_id,
                         :start_time, :end_time, :duration_seconds, :now)
                """), {
                    "channel_id": channel_id,
                    "title": slot.get("title", ""),
                    "subtitle": slot.get("subtitle", ""),
                    "description": slot.get("description", ""),
                    "thumbnail_url": slot.get("thumbnail_url", ""),
                    "stream_url": slot.get("stream_url", ""),
                    "source_type": slot.get("source_type", ""),
                    "source_id": slot.get("source_id", ""),
                    "start_time": slot["start_time"],
                    "end_time": slot["end_time"],
                    "duration_seconds": slot["duration_seconds"],
                    "now": now,
                })
                inserted += 1

            await db.commit()
            logger.info(
                f"EPG scheduler: channel {channel_id} '{channel['name']}' — "
                f"{inserted} slots written."
            )

            # Pre-resolve current + next slots for WatchDawg channels so Android
            # gets instant HLS playback from the DB cache.
            # Also trigger background download of current + next slot video files
            # so the FFmpeg stream path can serve them locally.
            if channel["source_type"] == "watchdawg":
                await _preresolve_watchdawg_slots(channel_id, db)
                # Kick off downloads in background — don't block the scheduler
                import asyncio
                asyncio.ensure_future(
                    _download_watchdawg_slots(channel_id, channel["epg_type"], db)
                )

        except Exception as e:
            logger.error(
                f"EPG scheduler: channel {channel_id} failed — {e}",
                exc_info=True,
            )


# ---------------------------------------------------------------------------
# Plex schedule builders
# ---------------------------------------------------------------------------

async def _build_plex_schedule(
    channel: dict,
    build_from: datetime.datetime,
    schedule_end: datetime.datetime,
    db,
) -> List[dict]:
    """Route to movie or TV builder depending on source_type."""
    if channel["source_type"] == "plex_movie":
        return await _build_plex_movie_schedule(channel, build_from, schedule_end, db)
    elif channel["source_type"] == "plex_tv":
        return await _build_plex_tv_schedule(channel, build_from, schedule_end, db)
    return []


async def _build_plex_movie_schedule(
    channel: dict,
    build_from: datetime.datetime,
    schedule_end: datetime.datetime,
    db,
) -> List[dict]:
    """
    Pull movies from Plex and pack them into time slots.

    Session 42 — Round-robin pointer:
      Instead of random shuffle (which repeats movies before the full library
      is seen), tracks a per-channel pointer in epg_movie_pointers. Each rebuild
      starts from where the last one left off, ensuring all movies are scheduled
      before any repeat. The pointer wraps around when the full library is cycled.

      For channels with rotation_style='shuffle', the library slice pulled for
      this window is shuffled within itself — so the order within a window is
      random but coverage across windows is complete.

      primetime_boost still applies within the selected slice.
    """
    from sqlalchemy import text as sa_text

    creds = await _get_plex_creds(db)
    if not creds:
        logger.warning(f"EPG Plex: no Plex credentials configured — channel {channel['id']} skipped.")
        return []

    library_key = channel.get("plex_library_key")
    genre_filter = channel.get("genre_filter", "")
    channel_id = channel["id"]

    url = f"{creds['url'].rstrip('/')}/library/sections/{library_key}/all"
    params = {
        "X-Plex-Token": creds["token"],
        "type": 1,  # movies
        "sort": "titleSort:asc",  # stable sort so pointer index is consistent
    }
    if genre_filter:
        params["genre"] = genre_filter

    try:
        async with httpx.AsyncClient(timeout=PLEX_TIMEOUT, verify=False) as client:
            resp = await client.get(url, params=params, headers=PLEX_HEADERS)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.error(f"EPG Plex movie: failed to fetch library {library_key}: {e}")
        return []

    media_container = data.get("MediaContainer", {})
    metadata = media_container.get("Metadata", [])

    if not metadata:
        logger.warning(f"EPG Plex movie: no movies found in library {library_key} (genre={genre_filter})")
        return []

    plex_base = creds["url"].rstrip("/")
    all_items = []
    for item in metadata:
        duration_ms = item.get("duration", 0)
        duration_s  = duration_ms // 1000
        if duration_s < MIN_DURATION_SECONDS:
            continue
        thumb = item.get("thumb", "")
        stream_url = _plex_stream_url(item, creds)
        if not stream_url:
            continue
        all_items.append({
            "title":            item.get("title", "Untitled"),
            "subtitle":         item.get("tagline", ""),
            "description":      item.get("summary", ""),
            "thumbnail_url":    f"{plex_base}{thumb}?X-Plex-Token={creds['token']}" if thumb else "",
            "stream_url":       stream_url,
            "source_type":      "plex_movie",
            "source_id":        str(item.get("ratingKey", "")),
            "duration_seconds": duration_s,
            "rating":           float(item.get("audienceRating", 0) or 0),
        })

    if not all_items:
        return []

    total = len(all_items)

    # Load current pointer for this channel
    ptr_result = await db.execute(sa_text("""
        SELECT pointer_index, library_size FROM epg_movie_pointers
        WHERE epg_channel_id = :ch
    """), {"ch": channel_id})
    ptr_row = ptr_result.fetchone()
    pointer = ptr_row[0] if ptr_row else 0

    # If library size changed significantly (>10%), reset pointer to avoid
    # stale index pointing past end of list
    if ptr_row and abs(ptr_row[1] - total) > max(10, total * 0.1):
        pointer = 0
        logger.info(
            f"EPG movie pointer: channel {channel_id} library size changed "
            f"({ptr_row[1]} → {total}) — resetting pointer"
        )

    pointer = pointer % total  # safety wrap

    # Build the ordered slice starting from pointer, wrapping around
    ordered = all_items[pointer:] + all_items[:pointer]

    # Estimate how many movies we need to fill the schedule window.
    # Average duration of first 20 items as a heuristic.
    sample = ordered[:20]
    avg_duration = sum(i["duration_seconds"] for i in sample) / len(sample)
    window_seconds = (schedule_end - build_from).total_seconds()
    movies_needed = int(window_seconds / avg_duration) + 5  # +5 buffer

    # Take the slice we need, looping if necessary
    items = []
    idx = 0
    while len(items) < movies_needed:
        items.append(ordered[idx % len(ordered)])
        idx += 1

    # Advance pointer by however many unique movies we consumed this window
    # (capped at total to avoid multi-wrap in one rebuild)
    consumed = min(movies_needed, total)
    new_pointer = (pointer + consumed) % total

    try:
        await db.execute(sa_text("""
            INSERT INTO epg_movie_pointers
                (epg_channel_id, pointer_index, library_size, updated_at)
            VALUES (:ch, :ptr, :size, :now)
            ON CONFLICT(epg_channel_id)
            DO UPDATE SET pointer_index = :ptr, library_size = :size, updated_at = :now
        """), {
            "ch": channel_id,
            "ptr": new_pointer,
            "size": total,
            "now": datetime.datetime.utcnow(),
        })
        await db.commit()
    except Exception as e:
        logger.warning(f"EPG movie pointer: failed to save for channel {channel_id}: {e}")

    # Within this window's slice, shuffle if requested (variety within window,
    # but full-library coverage guaranteed across windows)
    if channel["rotation_style"] == "shuffle":
        random.shuffle(items)

    if channel["primetime_boost"] and len(items) > 5:
        items = _apply_primetime_boost(items, build_from)

    logger.info(
        f"EPG Plex movie: channel {channel_id} — {total} movies total, "
        f"pointer={pointer}→{new_pointer}, genre={genre_filter or 'all'}"
    )
    return _pack_time_slots(items, build_from, schedule_end, channel)


async def _build_plex_tv_schedule(
    channel: dict,
    build_from: datetime.datetime,
    schedule_end: datetime.datetime,
    db,
) -> List[dict]:
    """
    Pull TV series episodes from Plex and pack them into time slots.

    Applies episode budget per series per day. Advances episode pointers
    via epg_tv_pointers table so the scheduler picks up where it left off.
    """
    from sqlalchemy import text

    creds = await _get_plex_creds(db)
    if not creds:
        logger.warning(f"EPG Plex TV: no Plex credentials — channel {channel['id']} skipped.")
        return []

    library_key = channel.get("plex_library_key")
    genre_filter = channel.get("genre_filter", "")
    budget_per_series = channel.get("episodes_per_day", DEFAULT_EPISODES_PER_DAY)
    channel_id = channel["id"]

    # Fetch TV shows from Plex
    url = f"{creds['url'].rstrip('/')}/library/sections/{library_key}/all"
    params = {
        "X-Plex-Token": creds["token"],
        "type": 2,  # show
        "sort": "audienceRating:desc",
    }
    if genre_filter:
        params["genre"] = genre_filter

    try:
        async with httpx.AsyncClient(timeout=PLEX_TIMEOUT, verify=False) as client:
            resp = await client.get(url, params=params, headers=PLEX_HEADERS)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.error(f"EPG Plex TV: failed to fetch shows from library {library_key}: {e}")
        return []

    shows = data.get("MediaContainer", {}).get("Metadata", [])
    if not shows:
        logger.warning(f"EPG Plex TV: no shows in library {library_key} (genre={genre_filter})")
        return []

    plex_base = creds["url"].rstrip("/")
    all_episodes = []

    for show in shows:
        show_key = str(show.get("ratingKey", ""))
        show_title = show.get("title", "Unknown Show")

        # Load episode pointer for this show
        ptr_result = await db.execute(text("""
            SELECT episode_index FROM epg_tv_pointers
            WHERE epg_channel_id = :ch AND show_rating_key = :key
        """), {"ch": channel_id, "key": show_key})
        ptr_row = ptr_result.fetchone()
        start_ep_index = ptr_row[0] if ptr_row else 0

        # Fetch all episodes for this show
        ep_url = f"{plex_base}/library/metadata/{show_key}/allLeaves"
        try:
            async with httpx.AsyncClient(timeout=PLEX_TIMEOUT, verify=False) as client:
                ep_resp = await client.get(ep_url, params={"X-Plex-Token": creds["token"]}, headers=PLEX_HEADERS)
                ep_resp.raise_for_status()
                ep_data = ep_resp.json()
        except Exception as e:
            logger.error(f"EPG TV: Failed fetching episodes for show {show_key}: {e}", exc_info=True)
            continue

        episodes = ep_data.get("MediaContainer", {}).get("Metadata", [])
        if not episodes:
            continue

        total_eps = len(episodes)
        selected = []
        for i in range(budget_per_series):
            ep = episodes[(start_ep_index + i) % total_eps]
            dur_s = (ep.get("duration", 0) or 0) // 1000
            if dur_s < MIN_DURATION_SECONDS:
                continue
            stream_url = _plex_stream_url(ep, creds)
            if not stream_url:
                continue
            thumb = ep.get("thumb") or show.get("thumb", "")
            selected.append({
                "title":          ep.get("title", "Untitled"),
                "subtitle":       show_title,
                "description":    ep.get("summary", ""),
                "thumbnail_url":  f"{plex_base}{thumb}?X-Plex-Token={creds['token']}" if thumb else "",
                "stream_url":     stream_url,
                "source_type":    "plex_tv",
                "source_id":      str(ep.get("ratingKey", "")),
                "duration_seconds": dur_s,
                "rating":         float(show.get("audienceRating", 0) or 0),
            })

        # Advance pointer
        new_index = (start_ep_index + budget_per_series) % total_eps
        try:
            await db.execute(text("""
                INSERT INTO epg_tv_pointers (epg_channel_id, show_rating_key, episode_index, updated_at)
                VALUES (:ch, :key, :idx, :now)
                ON CONFLICT(epg_channel_id, show_rating_key)
                DO UPDATE SET episode_index = :idx, updated_at = :now
            """), {
                "ch": channel_id,
                "key": show_key,
                "idx": new_index,
                "now": datetime.datetime.utcnow(),
            })
            await db.commit()
        except Exception as e:
            logger.error(f"EPG TV: Failed saving episode pointer: {e}", exc_info=True)

        all_episodes.extend(selected)

    if channel["primetime_boost"] and len(all_episodes) > 5:
        all_episodes = _apply_primetime_boost(all_episodes, build_from)

    logger.info(
        f"EPG TV: channel {channel['id']} — {len(shows)} shows, "
        f"{len(all_episodes)} episodes selected (budget={budget_per_series}/series)"
    )
    return _pack_time_slots(all_episodes, build_from, schedule_end, channel)


# ---------------------------------------------------------------------------
# WatchDawg schedule builder
# ---------------------------------------------------------------------------

async def _build_watchdawg_schedule(
    channel: dict,
    build_from: datetime.datetime,
    schedule_end: datetime.datetime,
    db,
) -> List[dict]:
    """
    Build schedule slots from WatchDawg's own scraped video library.

    Session 40: if watchdawg_source_id is set on the EPG channel, pull only
    from that specific WatchDawg source channel (WHERE c.id = source_id).
    Otherwise, pull from all channels matching genre_filter as before.

    Adult channels pull from locked sources only.
    Main channels never pull from locked sources.

    Session 40: No longer requires resolution_status='resolved'. All scraped
    videos are included regardless of resolution status — Android resolves
    on demand via the PlayModeMenu flow just like tapping a feed card.
    Videos without duration_seconds get a category-based estimate so the
    scheduler can place them in time slots.
    """
    from sqlalchemy import text

    genre_filter = channel.get("genre_filter", "")
    is_adult = channel["epg_type"] == "adult"
    watchdawg_source_id = channel.get("watchdawg_source_id")

    # Pull all scraped videos regardless of resolution status.
    # duration_seconds may be NULL for unresolved videos — we COALESCE with
    # a category-based default so every video gets a usable slot duration.
    # The default is 2700s (45 min) — appropriate for TV/documentary content.
    # Music channels should use a shorter default but genre filtering handles that.
    query_parts = ["""
        SELECT v.id, v.title, v.artist, v.thumbnail_url,
               v.resolved_stream_url,
               COALESCE(v.duration_seconds, 2700) as duration_seconds,
               c.locked, c.genre_tags, v.reddit_score
        FROM videos v
        JOIN channels c ON c.id = v.channel_id
        WHERE v.resolution_status != 'failed'
    """]
    params = {"min_dur": MIN_DURATION_SECONDS}

    # Adult EPG: only pull from locked channels
    # Main EPG: never pull from locked channels
    if is_adult:
        query_parts.append("AND c.locked = 1")
    else:
        query_parts.append("AND c.locked = 0")

    # Session 40: if a specific source channel is pinned, use it exclusively.
    # Otherwise apply genre filter as before.
    if watchdawg_source_id:
        query_parts.append("AND c.id = :source_id")
        params["source_id"] = watchdawg_source_id
        logger.info(
            f"EPG WatchDawg: channel {channel['id']} pinned to source channel {watchdawg_source_id}"
        )
    elif genre_filter:
        # Genre filter — match against channel genre_tags
        filter_genres = [g.strip() for g in genre_filter.split(",") if g.strip()]
        if filter_genres:
            genre_conditions = " OR ".join(
                f"c.genre_tags LIKE :genre_{i}" for i in range(len(filter_genres))
            )
            query_parts.append(f"AND ({genre_conditions})")
            for i, g in enumerate(filter_genres):
                params[f"genre_{i}"] = f"%{g}%"

    query_parts.append("ORDER BY RANDOM() LIMIT 2000")
    query = "\n".join(query_parts)

    result = await db.execute(text(query), params)
    rows = result.fetchall()

    if not rows:
        return []

    items = []
    for row in rows:
        items.append({
            "title": row[1] or "Untitled",
            "subtitle": row[2] or "",  # artist name
            "description": "",
            "thumbnail_url": row[3] or "",
            # stream_url may be None for unresolved videos — store empty string
            # to satisfy the NOT NULL constraint. Android resolves on demand via
            # video_id (source_id) using the PlayModeMenu flow.
            "stream_url": row[4] or "",
            "source_type": "watchdawg",
            "source_id": str(row[0]),
            "duration_seconds": int(row[5]),
            "rating": row[8] or 0,
        })

    if channel["rotation_style"] == "shuffle":
        random.shuffle(items)

    if channel["primetime_boost"] and len(items) > 5:
        items = _apply_primetime_boost(items, build_from)

    logger.info(
        f"EPG WatchDawg: channel {channel['id']} — {len(items)} videos available "
        f"(source_id: {watchdawg_source_id or 'all'}, genre: {genre_filter or 'all'}, adult: {is_adult})"
    )
    return _pack_time_slots(items, build_from, schedule_end, channel)


# ---------------------------------------------------------------------------
# WatchDawg pre-resolution — warm the cache before Android asks
# ---------------------------------------------------------------------------

async def _preresolve_watchdawg_slots(epg_channel_id: int, db) -> None:
    """
    Pre-resolve the currently-airing slot and next 2 slots for a WatchDawg
    EPG channel so the Android client gets instant HLS playback from the DB
    cache instead of waiting 10-20 seconds for a live yt-dlp extraction.

    Called inline after each WatchDawg schedule build. Failures are silent —
    Android falls back to live resolution if the cache is missing or stale.

    Pre-resolves in HLS mode (client=browser) to match what the EPG player
    uses on Android. HLS URLs are cached for 20 minutes (ADAPTIVE_TTL_MINUTES).
    We resolve up to 3 slots so channel surfing to adjacent channels is fast.
    """
    from sqlalchemy import text
    import datetime

    now = datetime.datetime.utcnow()

    # Find the current + next 2 upcoming slots for this channel
    result = await db.execute(text("""
        SELECT source_id
        FROM epg_schedules
        WHERE epg_channel_id = :ch_id
          AND end_time > :now
          AND source_type = 'watchdawg'
          AND source_id IS NOT NULL
          AND source_id != ''
        ORDER BY start_time ASC
        LIMIT 3
    """), {"ch_id": epg_channel_id, "now": now})

    rows = result.fetchall()
    if not rows:
        return

    video_ids = []
    for row in rows:
        try:
            video_ids.append(int(row[0]))
        except (ValueError, TypeError):
            pass

    if not video_ids:
        return

    logger.info(
        f"EPG pre-resolve: channel {epg_channel_id} — "
        f"warming cache for {len(video_ids)} slot(s): {video_ids}"
    )

    try:
        from app.services.resolver import ResolverService
        resolver = ResolverService(db)
        for video_id in video_ids:
            try:
                result = await resolver.resolve_video(video_id, force=False)
                if result:
                    logger.info(
                        f"EPG pre-resolve: video {video_id} cached — "
                        f"url_preview={result.get('stream_url','')[:60]}"
                    )
                else:
                    logger.warning(f"EPG pre-resolve: video {video_id} failed to resolve")
            except Exception as e:
                logger.warning(f"EPG pre-resolve: video {video_id} error — {e}")
    except Exception as e:
        logger.warning(f"EPG pre-resolve: resolver init failed — {e}")


async def _download_watchdawg_slots(epg_channel_id: int, epg_type: str, db) -> None:
    """
    Download the current + next WatchDawg EPG slot videos to local storage
    so the FFmpeg stream endpoint can serve them with perfect timing — same
    approach used for Plex channels.

    Session 40 — Download lifecycle:
      - Files stored at /watchdawg/Public/EPG/{epg_channel_id}_{video_id}.mp4
        (main EPG) or /watchdawg/Private/EPG/ (adult EPG)
      - Only current + next 2 slots are kept — older files are deleted
      - Downloads use yt-dlp subprocess so they don't block the async loop
      - Quality: best mp4 up to 720p (keeps files ~500MB per 45-min episode)
      - Failures are silent — FFmpeg falls back to HLS pre-resolved URL

    When the EPG stream endpoint (/epg/stream/{channel_id}) is called, it
    checks for the local file first and uses it if present, otherwise falls
    back to the Plex/HLS URL path.
    """
    from sqlalchemy import text
    import asyncio
    import os
    import glob

    now = datetime.datetime.utcnow()

    # Determine download directory
    epg_folder = "Private/EPG" if epg_type == "adult" else "Public/EPG"
    download_dir = f"/watchdawg/{epg_folder}"
    os.makedirs(download_dir, exist_ok=True)

    # Find the current + next 2 slots
    result = await db.execute(text("""
        SELECT es.source_id, v.source_url, v.title
        FROM epg_schedules es
        JOIN videos v ON v.id = CAST(es.source_id AS INTEGER)
        WHERE es.epg_channel_id = :ch_id
          AND es.end_time > :now
          AND es.source_type = 'watchdawg'
          AND es.source_id IS NOT NULL
          AND es.source_id != ''
        ORDER BY es.start_time ASC
        LIMIT 2
    """), {"ch_id": epg_channel_id, "now": now})

    rows = result.fetchall()
    if not rows:
        return

    # Build the set of video IDs we want to keep
    wanted_ids = set()
    slots_to_download = []
    for row in rows:
        try:
            vid_id = int(row[0])
            source_url = row[1]
            title = row[2] or f"video_{vid_id}"
            if source_url:
                wanted_ids.add(vid_id)
                slots_to_download.append((vid_id, source_url, title))
        except (ValueError, TypeError):
            pass

    # Clean up old files for this channel that aren't in the wanted set
    pattern = os.path.join(download_dir, f"{epg_channel_id}_*.mp4")
    for old_file in glob.glob(pattern):
        fname = os.path.basename(old_file)
        # Extract video_id from filename: {channel_id}_{video_id}.mp4
        try:
            parts = fname.replace(".mp4", "").split("_")
            old_vid_id = int(parts[-1])
            if old_vid_id not in wanted_ids:
                os.remove(old_file)
                logger.info(f"EPG download: deleted stale file {fname}")
        except Exception:
            pass

    # Download each slot that doesn't already have a local file
    cookies_path = "/config/cookies.txt"
    for vid_id, source_url, title in slots_to_download:
        out_path = os.path.join(download_dir, f"{epg_channel_id}_{vid_id}.mp4")

        # Skip if already downloaded
        if os.path.exists(out_path) and os.path.getsize(out_path) > 1_000_000:
            logger.info(
                f"EPG download: channel {epg_channel_id} video {vid_id} "
                f"already cached ({os.path.getsize(out_path) // 1_000_000}MB)"
            )
            continue

        logger.info(
            f"EPG download: channel {epg_channel_id} — downloading video {vid_id} "
            f"'{title[:50]}' from {source_url[:60]}"
        )

        # yt-dlp command — best mp4 up to 720p, no playlist, cookies
        cmd = [
            "yt-dlp",
            "-f", "best[height<=720][ext=mp4]/best[height<=720]/best[ext=mp4]/best",
            "--no-playlist",
            "--no-warnings",
            "--quiet",
            "-o", out_path,
        ]
        if cookies_path and os.path.isfile(cookies_path):
            cmd += ["--cookies", cookies_path]
        cmd.append(source_url)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            # Wait up to 30 minutes for download — large files may take time
            try:
                _, stderr = await asyncio.wait_for(proc.communicate(), timeout=1800)
                if proc.returncode == 0 and os.path.exists(out_path):
                    size_mb = os.path.getsize(out_path) // 1_000_000
                    logger.info(
                        f"EPG download: video {vid_id} complete — "
                        f"{size_mb}MB at {out_path}"
                    )
                else:
                    err = stderr.decode(errors="ignore")[:200] if stderr else "unknown"
                    logger.warning(
                        f"EPG download: video {vid_id} failed "
                        f"(rc={proc.returncode}) — {err}"
                    )
                    # Clean up partial file
                    if os.path.exists(out_path):
                        os.remove(out_path)
            except asyncio.TimeoutError:
                logger.warning(f"EPG download: video {vid_id} timed out after 30min")
                proc.kill()
                if os.path.exists(out_path):
                    os.remove(out_path)
        except Exception as e:
            logger.warning(f"EPG download: video {vid_id} subprocess error — {e}")



# ---------------------------------------------------------------------------
# Local Private schedule builder (Session 43)
# ---------------------------------------------------------------------------

async def _build_local_private_schedule(
    channel: dict,
    build_from: datetime.datetime,
    schedule_end: datetime.datetime,
) -> list:
    """
    Build schedule slots from files in /watchdawg/Private/{folder_path}.

    plex_library_key holds the subfolder name relative to /watchdawg/Private/,
    e.g. "123_Vimeo_Girls". Walks that directory recursively, gets duration for
    each video via ffprobe, and packs them into time slots using _pack_time_slots.

    All slots use source_type="local_private" and stream_url pointing to
    /library/stream/Private/{folder}/{filename}. For in-progress slots the
    /epg/stream/ endpoint handles FFmpeg seeking (same as Plex channels).
    """
    import asyncio as _asyncio
    import os as _os
    import urllib.parse as _urllib_parse

    folder_path = channel.get("plex_library_key", "") or ""
    if not folder_path:
        logger.warning(f"EPG local_private: channel {channel['id']} has no folder_path set in plex_library_key — skipping.")
        return []

    full_folder = _os.path.join("/watchdawg/Private", folder_path)
    if not _os.path.isdir(full_folder):
        logger.warning(f"EPG local_private: channel {channel['id']} folder does not exist: {full_folder}")
        return []

    video_exts = {".mp4", ".mkv", ".webm", ".m4v", ".avi", ".mov"}
    video_files = []
    for dirpath, _dirs, files in _os.walk(full_folder):
        for fname in sorted(files):
            _, ext = _os.path.splitext(fname)
            if ext.lower() not in video_exts:
                continue
            full_path = _os.path.join(dirpath, fname)
            # Only include files > 1MB — skip partial downloads
            try:
                if _os.path.getsize(full_path) < 1_000_000:
                    continue
            except OSError:
                continue
            video_files.append(full_path)

    if not video_files:
        logger.warning(f"EPG local_private: channel {channel['id']} — no video files found in {full_folder}")
        return []

    # Get duration for each file via ffprobe
    async def _ffprobe_duration(file_path: str) -> int:
        """Returns duration in seconds, or 0 on failure."""
        try:
            proc = await _asyncio.create_subprocess_exec(
                "ffprobe",
                "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                file_path,
                stdout=_asyncio.subprocess.PIPE,
                stderr=_asyncio.subprocess.PIPE,
            )
            stdout, _ = await _asyncio.wait_for(proc.communicate(), timeout=30)
            raw = stdout.decode(errors="ignore").strip()
            if raw:
                import json as _json
                data = _json.loads(raw)
                dur_str = data.get("format", {}).get("duration", "0")
                return max(0, int(float(dur_str)))
            return 0
        except _asyncio.TimeoutError:
            logger.warning(f"EPG local_private: ffprobe timed out for {file_path}")
            return 0
        except Exception as e:
            logger.warning(f"EPG local_private: ffprobe error for {file_path}: {e}")
            return 0

    items = []
    for full_path in video_files:
        # stream_url is relative to the watchdawg downloads root
        # library.py serves /library/stream/{relative_path}
        rel = _os.path.relpath(full_path, "/watchdawg")  # e.g. Private/123_Folder/vid.mp4
        stream_url = f"/library/stream/{_urllib_parse.quote(rel, safe='/')}"

        title = _os.path.splitext(_os.path.basename(full_path))[0]

        duration_s = await _ffprobe_duration(full_path)
        if duration_s < MIN_DURATION_SECONDS:
            # ffprobe failed or very short — skip
            logger.debug(f"EPG local_private: skipping {full_path} (duration={duration_s}s)")
            continue

        items.append({
            "title":            title,
            "subtitle":         folder_path,
            "description":      "",
            "thumbnail_url":    "",
            "stream_url":       stream_url,
            "source_type":      "local_private",
            "source_id":        rel,   # relative path used by FFmpeg stream endpoint
            "duration_seconds": duration_s,
            "rating":           0,
        })

    if not items:
        logger.warning(f"EPG local_private: channel {channel['id']} — no usable video files after duration check in {full_folder}")
        return []

    if channel["rotation_style"] == "shuffle":
        random.shuffle(items)

    logger.info(
        f"EPG local_private: channel {channel['id']} '{channel['name']}' — "
        f"{len(items)} files from {full_folder}"
    )
    return _pack_time_slots(items, build_from, schedule_end, channel)


# ---------------------------------------------------------------------------
# Shared utilities
# ---------------------------------------------------------------------------

def _apply_primetime_boost(items: List[dict], build_from: datetime.datetime) -> List[dict]:
    """
    Reorder items so highest-rated content lands in primetime slots (7-11 PM local).

    Simple approach: sort items by rating descending, then interleave —
    top-rated items will naturally fall into the first few slots which
    often cover primetime.
    """
    rated = sorted(items, key=lambda x: x.get("rating", 0) or 0, reverse=True)
    top_cut = max(1, len(rated) // 5)  # top 20% by rating
    top = rated[:top_cut]
    rest = rated[top_cut:]
    random.shuffle(rest)
    # Interleave: 1 top-rated per 4 regular
    boosted = []
    top_idx = 0
    rest_idx = 0
    slot = 0
    while top_idx < len(top) or rest_idx < len(rest):
        if slot % 5 == 0 and top_idx < len(top):
            boosted.append(top[top_idx])
            top_idx += 1
        elif rest_idx < len(rest):
            boosted.append(rest[rest_idx])
            rest_idx += 1
        elif top_idx < len(top):
            boosted.append(top[top_idx])
            top_idx += 1
        slot += 1
    return boosted


def _pack_time_slots(
    items: List[dict],
    build_from: datetime.datetime,
    schedule_end: datetime.datetime,
    channel: dict,
) -> List[dict]:
    """
    Pack content items into sequential time slots starting at build_from.

    Items are looped if the schedule window is longer than the content list.
    Returns a list of slot dicts ready to write to epg_schedules.
    """
    if not items:
        return []

    slots = []
    current_time = build_from
    item_index = 0
    total_items = len(items)
    loop_count = 0
    max_loops = 10  # safety valve — prevent infinite loops on tiny libraries

    while current_time < schedule_end:
        if item_index >= total_items:
            item_index = 0
            loop_count += 1
            if loop_count >= max_loops:
                logger.warning(
                    f"EPG packer: channel {channel['id']} hit {max_loops} loops — "
                    f"schedule window may not be fully covered."
                )
                break
            # Re-shuffle on each loop for variety
            if channel["rotation_style"] == "shuffle":
                random.shuffle(items)

        item = items[item_index]
        duration = item.get("duration_seconds", 0)

        if duration <= 0:
            item_index += 1
            continue

        slot_end = current_time + datetime.timedelta(seconds=duration)

        slots.append({
            "title": item["title"],
            "subtitle": item.get("subtitle", ""),
            "description": item.get("description", ""),
            "thumbnail_url": item.get("thumbnail_url", ""),
            "stream_url": item["stream_url"],
            "source_type": item.get("source_type", ""),
            "source_id": item.get("source_id", ""),
            "start_time": current_time,
            "end_time": slot_end,
            "duration_seconds": duration,
        })

        current_time = slot_end
        item_index += 1

    return slots


def _plex_stream_url(item: dict, creds: dict) -> Optional[str]:
    """
    Extract a direct-play stream URL for a Plex media item.

    Returns None if the item has no playable media parts.
    """
    try:
        media = item.get("Media", [])
        if not media:
            return None
        parts = media[0].get("Part", [])
        if not parts:
            return None
        key = parts[0].get("key", "")
        if not key:
            return None
        base = creds["url"].rstrip("/")
        return f"{base}{key}?X-Plex-Token={creds['token']}&download=1"
    except Exception:
        return None


async def _get_plex_creds(db) -> Optional[dict]:
    """Load and decrypt Plex credentials from DB. Returns None if not configured."""
    from sqlalchemy import text
    try:
        result = await db.execute(
            text("SELECT plex_url_encrypted, token_encrypted FROM plex_config LIMIT 1")
        )
        row = result.fetchone()
        if not row:
            return None
        return {
            "url": decrypt_value(row[0]),
            "token": decrypt_value(row[1]),
        }
    except Exception as e:
        logger.error(f"EPG scheduler: Failed to load Plex credentials: {e}")
        return None
