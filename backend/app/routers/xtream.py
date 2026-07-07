"""
WatchDawg Xtream Codes Emulation Layer (Phase 1 — Session 48).

Serves the scraped/downloaded WatchDawg catalog as TWO standard Xtream-format
VOD sources that any Xtream-capable client (e.g. OwnTV / SurfTV's client)
can consume:

  PUBLIC stream  — all channels where locked = false (no PIN gate)
  PRIVATE stream — all channels where locked = true  (PIN-gated content)

Both profiles share the same base URL (http://192.168.50.42:6868) and are
separated purely by credentials — exactly how a client adds two commercial
Xtream services.

Credentials (all overridable via environment variables):
  XTREAM_PUBLIC_USER   default "public"
  XTREAM_PUBLIC_PASS   default "watchdawg"
  XTREAM_PRIVATE_USER  default "private"
  XTREAM_PRIVATE_PASS  default = WATCHDAWG_PIN from .env
                       (if neither is set, the private stream is DISABLED)

Endpoints implemented (root-level, standard Xtream layout):
  GET/POST /player_api.php     — handshake + all catalog actions:
        (no action)            → user_info + server_info handshake
        get_live_categories    → one live category per profile (Session 53)
        get_live_streams       → EPG pseudo-channels as live streams
        get_vod_categories     → one category per non-TV channel
        get_vod_streams        → movies (videos) per category
        get_vod_info           → single-movie detail
        get_series_categories  → single "WatchDawg Series" category
        get_series             → one series per category="tv" channel
        get_series_info        → episode list for one series
        get_short_epg          → empty stub
        get_simple_data_table  → empty stub
  GET /movie/{user}/{pass}/{stream_id}.{ext}   — VOD playback
  GET /series/{user}/{pass}/{episode_id}.{ext} — episode playback
  GET /live/{user}/{pass}/{epg_channel_id}.ts  — live playback (Session 53):
        auth + epg_type gate, then 302 → /epg/stream/{id} (mid-show join)
  GET /get.php                 — minimal empty M3U (client probe stub)
  GET /xmltv.php               — real XMLTV guide from epg_schedules (Session 53)

Playback design:
  The stream endpoints validate credentials and the channel lock state,
  then 302-redirect to the existing, battle-tested on-demand pipeline at
  /channel/stream/{video_id}, which already handles:
    - local downloaded files  → direct /library/stream/ serve
    - HLS (Vimeo)             → /proxy/stream with Referer injection
    - split MP4 (YouTube)     → DASH manifest merge
    - combined streams        → /proxy/stream
  Nothing is resolved at catalog time — tokens can't expire in the catalog.
  The .mp4 extension is visible in the URL path, which satisfies players
  that gate playability on URL extension (the NostalgiaTV lesson).

Catalog eligibility (mirrors the proven /channel/all/live.m3u filter):
  - channel.enabled = true
  - video.source_url present and non-empty
  - video.resolution_status != "failed"
  Downloaded videos ARE included — they play instantly from local disk.

Security notes:
  - Credential comparison uses hmac.compare_digest (constant-time).
  - The private stream never leaks into public responses: category,
    stream, info, and playback endpoints all enforce the lock split.
  - Failed player_api auth returns the standard Xtream auth=0 body
    (HTTP 200) so clients show "invalid credentials" cleanly; failed
    playback auth returns 401.
"""

import datetime
import hmac
import logging
import os
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db_session
from app.models import Channel, Video

logger = logging.getLogger(__name__)

router = APIRouter(tags=["xtream"])

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Matches the hardcoded convention used throughout channel.py.
BASE_URL = "http://192.168.50.42:6868"
SERVER_HOST = "192.168.50.42"
SERVER_PORT = "6868"

# Far-future expiry for the perpetual "subscription".
EXP_DATE = "1893456000"  # 2030-01-01

# Single series category (all TV-category channels live under it).
SERIES_CATEGORY_ID = "1"
SERIES_CATEGORY_NAME = "WatchDawg Series"

# ---------------------------------------------------------------------------
# Live TV (Session 53) — EPG pseudo-channels served as Xtream live streams.
#
# Live category IDs use a high offset so they can never collide with VOD
# category IDs (which are WatchDawg channel row IDs). The epg_type split
# mirrors the VOD lock split: the public login sees 'main' EPG channels,
# the private login sees 'adult' EPG channels — two separate sources in
# TiviMate, exactly like VOD.
# ---------------------------------------------------------------------------
LIVE_CATEGORY_MAIN_ID = "900001"
LIVE_CATEGORY_MAIN_NAME = "WatchDawg TV"
LIVE_CATEGORY_ADULT_ID = "900002"
LIVE_CATEGORY_ADULT_NAME = "Adult TV"

# EPG source types that have a working schedule builder + stream path.
# 'xmltv' rows are dead Tunarr-import artifacts (Tunarr is retired) —
# nothing can build schedules for them or stream them, so they are
# filtered out of everything TiviMate sees.
LIVE_SERVABLE_SOURCE_TYPES = ("plex_movie", "plex_tv", "watchdawg", "local_private")

# How far back / forward xmltv.php exports programme data. TiviMate keeps
# a little history visible in the grid; the pseudo-scheduler builds 48h out.
XMLTV_LOOKBACK_HOURS = 6
XMLTV_LOOKAHEAD_HOURS = 48


def _epg_type_for_profile(profile: str) -> str:
    """Mirror of the VOD lock split: public → main, private → adult."""
    return "adult" if profile == "private" else "main"


def _live_category_for_profile(profile: str) -> tuple:
    """(category_id, category_name) for this profile's live section."""
    if profile == "private":
        return (LIVE_CATEGORY_ADULT_ID, LIVE_CATEGORY_ADULT_NAME)
    return (LIVE_CATEGORY_MAIN_ID, LIVE_CATEGORY_MAIN_NAME)


async def _epg_channels_for_profile(db: AsyncSession, profile: str) -> list:
    """
    Enabled, servable EPG pseudo-channels visible to this profile,
    as dicts: {id, number, name, logo}. Raw SQL because the epg_* tables
    predate the ORM models and are addressed with text() throughout epg.py.
    """
    placeholders = ", ".join(f"'{s}'" for s in LIVE_SERVABLE_SOURCE_TYPES)
    result = await db.execute(
        text(f"""
            SELECT id, channel_number, name, logo_url
            FROM epg_channels
            WHERE enabled = 1
              AND epg_type = :etype
              AND source_type IN ({placeholders})
            ORDER BY channel_number ASC, name ASC
        """),
        {"etype": _epg_type_for_profile(profile)},
    )
    return [
        {"id": row[0], "number": row[1], "name": row[2], "logo": row[3]}
        for row in result.fetchall()
    ]


def _parse_epg_dt(value):
    """
    Parse an epg_schedules start/end time as stored by SQLite — may come
    back as a datetime object or an ISO-ish string ('YYYY-MM-DD HH:MM:SS'
    with optional microseconds). Returns a datetime or None.
    """
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return value
    try:
        return datetime.datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None


def _xmltv_time(dt: datetime.datetime) -> str:
    """Format a UTC datetime in XMLTV's 'YYYYMMDDHHMMSS +0000' form."""
    return dt.strftime("%Y%m%d%H%M%S") + " +0000"


# ---------------------------------------------------------------------------
# Credentials & profile resolution
# ---------------------------------------------------------------------------

def _public_credentials() -> tuple:
    return (
        os.getenv("XTREAM_PUBLIC_USER", "public"),
        os.getenv("XTREAM_PUBLIC_PASS", "watchdawg"),
    )


def _private_credentials() -> Optional[tuple]:
    """Returns (user, pass) for the private stream, or None if disabled."""
    user = os.getenv("XTREAM_PRIVATE_USER", "private")
    password = os.getenv("XTREAM_PRIVATE_PASS") or settings.watchdawg_pin
    if not password:
        return None
    return (user, password)


def _match(candidate_user: str, candidate_pass: str, creds: tuple) -> bool:
    """Constant-time credential comparison."""
    user_ok = hmac.compare_digest(
        (candidate_user or "").encode(), creds[0].encode()
    )
    pass_ok = hmac.compare_digest(
        (candidate_pass or "").encode(), creds[1].encode()
    )
    return user_ok and pass_ok


def resolve_profile(username: str, password: str) -> Optional[str]:
    """
    Map credentials to a profile name.

    Returns "public", "private", or None (auth failure / disabled).
    """
    if _match(username, password, _public_credentials()):
        return "public"
    private = _private_credentials()
    if private is not None and _match(username, password, private):
        return "private"
    return None


def _locked_value_for_profile(profile: str) -> bool:
    """
    The channel.locked value this profile is allowed to see.

    public  → locked == False channels only
    private → locked == True channels only (the PIN-gated stream)
    """
    return profile == "private"


# ---------------------------------------------------------------------------
# Shared query helpers
# ---------------------------------------------------------------------------

def _eligible_video_filter(stmt):
    """
    Apply catalog-eligibility filters to a Video select.

    Only resolved videos and local downloaded files are served — unresolved
    or pending videos are excluded so TiviMate never waits on a live yt-dlp
    call at play time. Videos the background scheduler hasn't resolved yet
    simply don't appear in the catalog until the next refresh.
    """
    from sqlalchemy import or_
    return stmt.where(
        Video.source_url.isnot(None),
        Video.source_url != "",
        or_(
            Video.resolution_status == "resolved",
            Video.source_provider == "local_folder",
        ),
    )


async def _channels_for_profile(
    db: AsyncSession, profile: str, tv_only: bool
) -> list:
    """
    Enabled channels visible to this profile, split by TV/non-TV,
    each guaranteed to contain at least one eligible video.
    """
    locked_value = _locked_value_for_profile(profile)

    stmt = (
        select(Channel)
        .where(Channel.enabled == True)  # noqa: E712
        .where(Channel.locked == locked_value)
    )
    if tv_only:
        stmt = stmt.where(Channel.category == "tv")
    else:
        stmt = stmt.where(Channel.category != "tv")

    result = await db.execute(stmt)
    channels = list(result.scalars().all())

    # Drop channels with zero eligible videos — empty categories confuse clients.
    kept = []
    for ch in channels:
        count_stmt = _eligible_video_filter(
            select(func.count(Video.id)).where(Video.channel_id == ch.id)
        )
        count = (await db.execute(count_stmt)).scalar() or 0
        if count > 0:
            kept.append(ch)
    return kept


async def _videos_for_channel(db: AsyncSession, channel_id: int) -> list:
    """Eligible videos for one channel, oldest first (stable episode order)."""
    stmt = _eligible_video_filter(
        select(Video).where(Video.channel_id == channel_id)
    ).order_by(Video.created_at.asc(), Video.id.asc())
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def _load_video_with_lock_check(
    db: AsyncSession, video_id: int, profile: str
) -> Video:
    """Load a video and enforce that its channel belongs to this profile."""
    stmt = select(Video).where(Video.id == video_id)
    result = await db.execute(stmt)
    video = result.scalar_one_or_none()
    if video is None:
        raise HTTPException(status_code=404, detail="Stream not found")

    locked_value = _locked_value_for_profile(profile)

    if video.channel_id is None:
        # Orphan videos (no channel) are treated as public-only.
        if locked_value:
            raise HTTPException(status_code=404, detail="Stream not found")
        return video

    ch_stmt = select(Channel).where(Channel.id == video.channel_id)
    ch_result = await db.execute(ch_stmt)
    channel = ch_result.scalar_one_or_none()
    if channel is None or channel.locked != locked_value or not channel.enabled:
        # Deliberately 404 (not 403) — don't confirm existence across profiles.
        raise HTTPException(status_code=404, detail="Stream not found")
    return video


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _ts(dt: Optional[datetime.datetime]) -> str:
    """Unix-timestamp string for Xtream 'added' fields."""
    if dt is None:
        return "0"
    try:
        return str(int(dt.replace(tzinfo=datetime.timezone.utc).timestamp()))
    except Exception:
        return "0"


def _icon(video: Video) -> str:
    return video.tmdb_poster_url or video.thumbnail_url or ""


def _rating(video: Video) -> str:
    return str(video.tmdb_rating) if video.tmdb_rating is not None else "0"


def _rating_5(video: Video) -> float:
    try:
        return round(float(video.tmdb_rating or 0) / 2.0, 1)
    except Exception:
        return 0.0


def _vod_stream_entry(num: int, video: Video, category_id: str) -> dict:
    return {
        "num": num,
        "name": video.title or "Untitled",
        "stream_type": "movie",
        "stream_id": video.id,
        "stream_icon": _icon(video),
        "rating": _rating(video),
        "rating_5based": _rating_5(video),
        "added": _ts(video.created_at),
        "category_id": category_id,
        "container_extension": "mp4",
        "custom_sid": "",
        "direct_source": "",
    }


def _series_entry(num: int, channel: Channel, cover: str,
                  plot: str, year: Optional[int], rating: str,
                  last_modified: str) -> dict:
    return {
        "num": num,
        "name": channel.name,
        "series_id": channel.id,
        "cover": cover,
        "plot": plot,
        "cast": "",
        "director": "",
        "genre": channel.genre_tags or channel.category or "",
        "releaseDate": str(year) if year else "",
        "last_modified": last_modified,
        "rating": rating,
        "rating_5based": 0.0,
        "backdrop_path": [],
        "youtube_trailer": "",
        "episode_run_time": "",
        "category_id": SERIES_CATEGORY_ID,
    }


def _parse_stream_id(stream_file: str) -> int:
    """
    Extract the numeric video id from an Xtream stream path segment.
    Accepts '16477.mp4', '16477.mkv', or bare '16477'.
    """
    m = re.match(r"^(\d+)(?:\.[A-Za-z0-9]+)?$", stream_file or "")
    if not m:
        raise HTTPException(status_code=404, detail="Invalid stream id")
    return int(m.group(1))


# ---------------------------------------------------------------------------
# player_api.php — handshake + catalog actions
# ---------------------------------------------------------------------------

@router.get("/player_api.php")
@router.post("/player_api.php")
async def player_api(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Xtream player API. Accepts GET query params or POST form params
    (different clients use different methods).
    """
    params = dict(request.query_params)
    if request.method == "POST":
        try:
            form = await request.form()
            for k, v in form.items():
                params.setdefault(k, v)
        except Exception:
            pass

    username = params.get("username", "")
    password = params.get("password", "")
    action = (params.get("action") or "").strip()

    profile = resolve_profile(username, password)

    # ------------------------------------------------------------------
    # Auth failure — standard Xtream auth=0 body, HTTP 200.
    # ------------------------------------------------------------------
    if profile is None:
        logger.warning(f"XTREAM | auth failed for user '{username[:30]}'")
        return JSONResponse({
            "user_info": {"auth": 0, "status": "Disabled",
                          "username": username, "message": "Invalid credentials"},
            "server_info": {},
        })

    # ------------------------------------------------------------------
    # Handshake (no action)
    # ------------------------------------------------------------------
    if not action:
        now = datetime.datetime.now(datetime.timezone.utc)
        logger.info(f"XTREAM | handshake OK — profile={profile}")
        return JSONResponse({
            "user_info": {
                "username": username,
                "password": password,
                "message": f"WatchDawg {profile} VOD",
                "auth": 1,
                "status": "Active",
                "exp_date": EXP_DATE,
                "is_trial": "0",
                "active_cons": "0",
                "created_at": _ts(now),
                "max_connections": "10",
                "allowed_output_formats": ["mp4"],
            },
            "server_info": {
                "url": SERVER_HOST,
                "port": SERVER_PORT,
                "https_port": SERVER_PORT,
                "server_protocol": "http",
                "rtmp_port": "0",
                "timezone": settings.timezone,
                "timestamp_now": int(now.timestamp()),
                "time_now": now.strftime("%Y-%m-%d %H:%M:%S"),
            },
        })

    # ------------------------------------------------------------------
    # Live categories — one per profile, from the EPG pseudo-channel
    # system (Session 53). Empty if the profile has no servable channels.
    # ------------------------------------------------------------------
    if action == "get_live_categories":
        epg_channels = await _epg_channels_for_profile(db, profile)
        if not epg_channels:
            logger.info(f"XTREAM | {profile} live_categories → 0 (no servable EPG channels)")
            return JSONResponse([])
        cat_id, cat_name = _live_category_for_profile(profile)
        logger.info(f"XTREAM | {profile} live_categories → 1 ({cat_name})")
        return JSONResponse([
            {"category_id": cat_id, "category_name": cat_name, "parent_id": 0}
        ])

    # ------------------------------------------------------------------
    # Live streams — one per enabled, servable EPG pseudo-channel.
    # stream_id = epg_channels.id (its own namespace: /live/ URLs never
    # mix with /movie/ or /series/ ids). epg_channel_id links each stream
    # to its <channel id="wd{id}"> element in xmltv.php.
    # ------------------------------------------------------------------
    if action == "get_live_streams":
        cat_id, _cat_name = _live_category_for_profile(profile)
        requested_cat = params.get("category_id")
        if requested_cat and str(requested_cat) != cat_id:
            return JSONResponse([])

        epg_channels = await _epg_channels_for_profile(db, profile)
        payload = []
        for idx, ch in enumerate(epg_channels, start=1):
            payload.append({
                "num": ch["number"] if ch["number"] is not None else idx,
                "name": ch["name"],
                "stream_type": "live",
                "stream_id": ch["id"],
                "stream_icon": ch["logo"] or "",
                "epg_channel_id": f"wd{ch['id']}",
                "added": "0",
                "category_id": cat_id,
                "custom_sid": "",
                "tv_archive": 0,
                "direct_source": "",
                "tv_archive_duration": 0,
            })
        logger.info(f"XTREAM | {profile} live_streams → {len(payload)}")
        return JSONResponse(payload)

    # ------------------------------------------------------------------
    # VOD categories — one per non-TV channel with eligible content.
    # ------------------------------------------------------------------
    if action == "get_vod_categories":
        channels = await _channels_for_profile(db, profile, tv_only=False)
        payload = [
            {
                "category_id": str(ch.id),
                "category_name": ch.name,
                "parent_id": 0,
            }
            for ch in sorted(channels, key=lambda c: (c.category, c.name.lower()))
        ]
        logger.info(f"XTREAM | {profile} vod_categories → {len(payload)}")
        return JSONResponse(payload)

    # ------------------------------------------------------------------
    # VOD streams — movies, optionally filtered by category (channel).
    # ------------------------------------------------------------------
    if action == "get_vod_streams":
        category_id = params.get("category_id")
        channels = await _channels_for_profile(db, profile, tv_only=False)
        if category_id:
            channels = [ch for ch in channels if str(ch.id) == str(category_id)]

        payload = []
        num = 0
        for ch in channels:
            videos = await _videos_for_channel(db, ch.id)
            # Newest first inside each category — matches feed behaviour.
            for video in reversed(videos):
                num += 1
                payload.append(_vod_stream_entry(num, video, str(ch.id)))
        logger.info(f"XTREAM | {profile} vod_streams(cat={category_id}) → {len(payload)}")
        return JSONResponse(payload)

    # ------------------------------------------------------------------
    # VOD info — single movie detail.
    # ------------------------------------------------------------------
    if action == "get_vod_info":
        try:
            vod_id = int(params.get("vod_id", "0"))
        except ValueError:
            raise HTTPException(status_code=404, detail="Invalid vod_id")
        video = await _load_video_with_lock_check(db, vod_id, profile)

        return JSONResponse({
            "info": {
                "name": video.title or "Untitled",
                "movie_image": _icon(video),
                "plot": video.tmdb_description or "",
                "cast": "",
                "director": "",
                "genre": "",
                "releasedate": str(video.tmdb_year) if video.tmdb_year else "",
                "rating": _rating(video),
                "duration_secs": int(video.duration_seconds or 0),
                "duration": "",
                "tmdb_id": video.tmdb_id or "",
            },
            "movie_data": {
                "stream_id": video.id,
                "name": video.title or "Untitled",
                "added": _ts(video.created_at),
                "category_id": str(video.channel_id or 0),
                "container_extension": "mp4",
                "custom_sid": "",
                "direct_source": "",
            },
        })

    # ------------------------------------------------------------------
    # Series categories — single umbrella category.
    # ------------------------------------------------------------------
    if action == "get_series_categories":
        channels = await _channels_for_profile(db, profile, tv_only=True)
        if not channels:
            return JSONResponse([])
        return JSONResponse([{
            "category_id": SERIES_CATEGORY_ID,
            "category_name": SERIES_CATEGORY_NAME,
            "parent_id": 0,
        }])

    # ------------------------------------------------------------------
    # Series — one per category="tv" channel.
    # ------------------------------------------------------------------
    if action == "get_series":
        channels = await _channels_for_profile(db, profile, tv_only=True)
        payload = []
        for num, ch in enumerate(
            sorted(channels, key=lambda c: c.name.lower()), start=1
        ):
            videos = await _videos_for_channel(db, ch.id)
            latest = videos[-1] if videos else None
            cover = _icon(latest) if latest else ""
            plot = (latest.tmdb_description or "") if latest else ""
            year = latest.tmdb_year if latest else None
            rating = _rating(latest) if latest else "0"
            last_modified = _ts(latest.created_at) if latest else "0"
            payload.append(
                _series_entry(num, ch, cover, plot, year, rating, last_modified)
            )
        logger.info(f"XTREAM | {profile} get_series → {len(payload)}")
        return JSONResponse(payload)

    # ------------------------------------------------------------------
    # Series info — episode list for one channel.
    # ------------------------------------------------------------------
    if action == "get_series_info":
        try:
            series_id = int(params.get("series_id", "0"))
        except ValueError:
            raise HTTPException(status_code=404, detail="Invalid series_id")

        locked_value = _locked_value_for_profile(profile)
        ch_stmt = (
            select(Channel)
            .where(Channel.id == series_id)
            .where(Channel.enabled == True)  # noqa: E712
            .where(Channel.locked == locked_value)
            .where(Channel.category == "tv")
        )
        channel = (await db.execute(ch_stmt)).scalar_one_or_none()
        if channel is None:
            raise HTTPException(status_code=404, detail="Series not found")

        videos = await _videos_for_channel(db, series_id)
        latest = videos[-1] if videos else None

        episodes = []
        for ep_num, video in enumerate(videos, start=1):
            episodes.append({
                "id": str(video.id),
                "episode_num": ep_num,
                "title": video.title or f"Episode {ep_num}",
                "container_extension": "mp4",
                "season": 1,
                "added": _ts(video.created_at),
                "custom_sid": "",
                "direct_source": "",
                "info": {
                    "duration_secs": int(video.duration_seconds or 0),
                    "duration": "",
                    "movie_image": _icon(video),
                    "plot": video.tmdb_description or "",
                    "rating": _rating(video),
                },
            })

        return JSONResponse({
            "seasons": [{
                "season_number": 1,
                "name": "Season 1",
                "episode_count": len(episodes),
                "overview": "",
                "air_date": "",
                "cover": _icon(latest) if latest else "",
                "cover_big": _icon(latest) if latest else "",
            }],
            "info": {
                "name": channel.name,
                "cover": _icon(latest) if latest else "",
                "plot": (latest.tmdb_description or "") if latest else "",
                "cast": "",
                "director": "",
                "genre": channel.genre_tags or "",
                "releaseDate": "",
                "last_modified": _ts(latest.created_at) if latest else "0",
                "rating": _rating(latest) if latest else "0",
                "rating_5based": 0.0,
                "backdrop_path": [],
                "youtube_trailer": "",
                "episode_run_time": "",
                "category_id": SERIES_CATEGORY_ID,
            },
            "episodes": {"1": episodes},
        })

    # ------------------------------------------------------------------
    # EPG stubs — some clients probe these for VOD sources.
    # ------------------------------------------------------------------
    if action in ("get_short_epg", "get_simple_data_table"):
        return JSONResponse({"epg_listings": []})

    logger.warning(f"XTREAM | unknown action '{action}' from profile={profile}")
    return JSONResponse([])


# ---------------------------------------------------------------------------
# Playback endpoints
# ---------------------------------------------------------------------------

async def _play(
    username: str, password: str, stream_file: str, db: AsyncSession
) -> RedirectResponse:
    """Shared movie/series playback: auth → lock check → redirect to pipeline."""
    profile = resolve_profile(username, password)
    if profile is None:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    video_id = _parse_stream_id(stream_file)
    video = await _load_video_with_lock_check(db, video_id, profile)

    target = f"{BASE_URL}/channel/stream/{video.id}"
    logger.info(
        f"XTREAM | play — profile={profile} video={video.id} "
        f"title={(video.title or '')[:50]}"
    )
    return RedirectResponse(url=target, status_code=302)


@router.get("/movie/{username}/{password}/{stream_file}")
async def xtream_movie(
    username: str,
    password: str,
    stream_file: str,
    db: AsyncSession = Depends(get_db_session),
):
    """Xtream VOD playback: /movie/{user}/{pass}/{video_id}.mp4"""
    return await _play(username, password, stream_file, db)


@router.get("/series/{username}/{password}/{stream_file}")
async def xtream_series(
    username: str,
    password: str,
    stream_file: str,
    db: AsyncSession = Depends(get_db_session),
):
    """Xtream episode playback: /series/{user}/{pass}/{video_id}.mp4"""
    return await _play(username, password, stream_file, db)


@router.get("/live/{username}/{password}/{stream_file}")
async def xtream_live(
    username: str,
    password: str,
    stream_file: str,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Xtream live playback: /live/{user}/{pass}/{epg_channel_id}.ts

    Authenticates, verifies the EPG channel exists / is enabled / belongs
    to this profile's epg_type (main vs adult — same wall as the VOD lock
    split), then 302-redirects to /epg/stream/{id}, which serves "what's
    on now" with mid-show join (FFmpeg -ss seek, stream copy).
    """
    profile = resolve_profile(username, password)
    if profile is None:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    channel_id = _parse_stream_id(stream_file)

    result = await db.execute(
        text("SELECT id, epg_type, enabled, name FROM epg_channels WHERE id = :id"),
        {"id": channel_id},
    )
    row = result.fetchone()
    # Nonexistent, disabled, or wrong-profile channels all 404 identically
    # so the public login can't even confirm adult channel IDs exist.
    if (
        row is None
        or not row[2]
        or row[1] != _epg_type_for_profile(profile)
    ):
        raise HTTPException(status_code=404, detail="Channel not found")

    target = f"{BASE_URL}/epg/stream/{channel_id}"
    logger.info(
        f"XTREAM | live — profile={profile} epg_channel={channel_id} "
        f"name={(row[3] or '')[:40]}"
    )
    return RedirectResponse(url=target, status_code=302)


# ---------------------------------------------------------------------------
# Client probe stubs
# ---------------------------------------------------------------------------

@router.get("/get.php")
async def xtream_get_php(request: Request):
    """
    M3U export probe. Live is intentionally not served over Xtream —
    return a valid empty playlist so probing clients don't error.
    """
    params = dict(request.query_params)
    profile = resolve_profile(
        params.get("username", ""), params.get("password", "")
    )
    if profile is None:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return Response(content="#EXTM3U\n", media_type="application/x-mpegurl")


@router.get("/xmltv.php")
async def xtream_xmltv(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Real XMLTV guide (Session 53) — generated from epg_schedules.

    Exports every enabled, servable EPG channel for this profile plus its
    programme slots from XMLTV_LOOKBACK_HOURS ago to XMLTV_LOOKAHEAD_HOURS
    ahead. Channel ids ('wd{id}') match the epg_channel_id values emitted
    by get_live_streams, which is how TiviMate joins guide data to streams.
    """
    from xml.sax.saxutils import escape, quoteattr

    params = dict(request.query_params)
    profile = resolve_profile(
        params.get("username", ""), params.get("password", "")
    )
    if profile is None:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    epg_channels = await _epg_channels_for_profile(db, profile)

    now = datetime.datetime.utcnow()
    window_start = now - datetime.timedelta(hours=XMLTV_LOOKBACK_HOURS)
    window_end = now + datetime.timedelta(hours=XMLTV_LOOKAHEAD_HOURS)

    placeholders = ", ".join(f"'{s}'" for s in LIVE_SERVABLE_SOURCE_TYPES)
    result = await db.execute(
        text(f"""
            SELECT s.epg_channel_id, s.title, s.subtitle, s.description,
                   s.thumbnail_url, s.start_time, s.end_time
            FROM epg_schedules s
            JOIN epg_channels c ON c.id = s.epg_channel_id
            WHERE c.enabled = 1
              AND c.epg_type = :etype
              AND c.source_type IN ({placeholders})
              AND s.end_time >= :window_start
              AND s.start_time <= :window_end
            ORDER BY s.epg_channel_id ASC, s.start_time ASC
        """),
        {
            "etype": _epg_type_for_profile(profile),
            "window_start": window_start,
            "window_end": window_end,
        },
    )
    slots = result.fetchall()

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<tv generator-info-name="WatchDawg">',
    ]

    for ch in epg_channels:
        lines.append(f'  <channel id="wd{ch["id"]}">')
        lines.append(f'    <display-name>{escape(ch["name"] or "")}</display-name>')
        if ch["logo"]:
            lines.append(f'    <icon src={quoteattr(ch["logo"])} />')
        lines.append('  </channel>')

    programme_count = 0
    for slot in slots:
        start_dt = _parse_epg_dt(slot[5])
        end_dt = _parse_epg_dt(slot[6])
        if start_dt is None or end_dt is None:
            continue  # unparseable row — skip rather than corrupt the guide
        lines.append(
            f'  <programme start="{_xmltv_time(start_dt)}" '
            f'stop="{_xmltv_time(end_dt)}" channel="wd{slot[0]}">'
        )
        lines.append(f'    <title>{escape(slot[1] or "Untitled")}</title>')
        if slot[2]:
            lines.append(f'    <sub-title>{escape(slot[2])}</sub-title>')
        if slot[3]:
            lines.append(f'    <desc>{escape(slot[3])}</desc>')
        if slot[4]:
            lines.append(f'    <icon src={quoteattr(slot[4])} />')
        lines.append('  </programme>')
        programme_count += 1

    lines.append('</tv>')
    lines.append('')

    logger.info(
        f"XTREAM | {profile} xmltv → {len(epg_channels)} channels, "
        f"{programme_count} programmes"
    )
    return Response(content="\n".join(lines), media_type="application/xml")
