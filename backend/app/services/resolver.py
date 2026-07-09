"""
WatchDawg Resolution Engine — yt-dlp Wrapper.

Converts source URLs (YouTube, Vimeo, Reddit, etc.) into direct
streamable URLs that the client can play via ExoPlayer or a browser
video element.

Key behaviors:
- Caching: Direct MP4 URLs are cached for 3 hours. HLS (m3u8) and DASH
  (mpd / http_dash_segments) URLs are cached for only 20 minutes because
  their signed CDN tokens expire in ~15-30 minutes on Vimeo.
- Format selection: Prefers direct HTTP MP4 up to 1080p. Explicitly blocks
  both HLS (m3u8) and DASH (http_dash_segments) protocols. Falls back to HLS
  only as a last resort — never to DASH, since browsers cannot play .mpd.
- Error handling: Dead links, geo-blocks, DMCA takedowns, and private videos
  are caught, flagged as permanent failures, and auto-deleted from the feed.
  Rate-limit errors (HTTP 429, YouTube session rate-limit) are explicitly
  guarded as transient and will never trigger auto-delete.
- Auto-dedup: After successful resolution, the CDN fingerprint is checked
  against all other resolved Vimeo videos. If a duplicate physical file is
  found, the lower-scored copy is deleted and playback is transparently
  redirected to the keeper — the user never sees an error.
- Hard timeout: Each yt-dlp call is capped at YTDLP_TIMEOUT_SECONDS (90s)
  using a ProcessPoolExecutor. If yt-dlp hangs (e.g. on a stalled YouTube
  connection), the process is killed and the video is marked failed rather
  than blocking the entire batch indefinitely.
"""

import asyncio
import concurrent.futures
import datetime
import logging
import os
import re
from typing import Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Video, Favorite

logger = logging.getLogger(__name__)

# Silence chatty third-party loggers
for _noisy in (
    "aiosqlite", "httpcore", "httpx",
    "sqlalchemy.engine", "sqlalchemy.pool", "apscheduler",
):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

# TTL for direct MP4 URLs — Vimeo/YouTube tokens last ~4-6h; we use 3h.
RESOLUTION_TTL_HOURS = 3

# Short TTL for HLS/DASH adaptive URLs — Vimeo signed tokens expire ~15-30min.
ADAPTIVE_TTL_MINUTES = 20

# Per-video locks for TV-path resolution (resolve_video_for_tv).
# TiviMate retries a slow video rapidly — without a lock, each retry spawns
# another concurrent yt-dlp process for the SAME video, piling up work and
# making the timeout worse. With the lock, the first request does the real
# extraction; every concurrent request for the same video waits, then gets
# an instant cache hit from the result the first one stored.
# The dict lives at module level so all requests (and the background
# scheduler) in this FastAPI process share the same locks. Entries are tiny
# asyncio.Lock objects — a few thousand videos costs negligible memory.
_tv_resolve_locks: dict = {}

# ---------------------------------------------------------------------------
# YouTube cookie health (Session 53).
#
# YouTube expires exported browser cookies every few weeks; when they go
# stale, every extraction fails with "Sign in to confirm you're not a bot"
# and the only symptom used to be buried log lines. This tracker records,
# in the MAIN process (extraction workers run in a separate process pool,
# so their module state is invisible here), the outcome of every YouTube
# extraction as results come back. /health exposes it and the web UI's
# Settings page shows a live flag, so nobody has to watch logs.
#
# Rule: cookies are considered stale when the most recent bot-check error
# is newer than the most recent YouTube success. A single success resets
# the flag — self-clearing after a cookie refresh, no button to press.
# In-memory only: a restart resets to "unknown" until the next YouTube
# extraction, which the TV warm pass provides within one scheduler tick.
# ---------------------------------------------------------------------------
_youtube_cookie_status = {
    "last_success": None,     # datetime of last successful YouTube extraction
    "last_bot_error": None,   # datetime of last bot-check rejection
    "bot_error_count": 0,     # failures since the last success
}

_BOT_CHECK_SIGNATURES = ("sign in to confirm", "not a bot")

# ---------------------------------------------------------------------------
# YouTube rate-limit back-off (Session 54).
#
# When YouTube rate-limits a session the error message contains the phrase
# "rate-limited by YouTube for up to an hour." We track this in-process and
# refuse to make ANY further YouTube yt-dlp calls until the cooldown expires.
# This prevents the scheduler from hammering YouTube every 30 minutes while
# already in the penalty box, which would extend the ban indefinitely.
#
# Back-off duration: YOUTUBE_BACKOFF_MINUTES (default 70 — slightly longer
# than YouTube's stated "up to an hour" so we don't immediately re-trigger).
# Self-clearing: once the cooldown expires the next extraction runs normally;
# if it succeeds the back-off is forgotten. No button to press.
#
# Background jobs (resolve_batch, warm_tv_cache) check is_youtube_backed_off()
# and skip YouTube videos silently during the cooldown — those videos stay
# in their current status and will be picked up on the next tick after the
# cooldown lifts. Play-time calls (resolve_video_for_tv) also check and
# return None fast rather than poking YouTube and re-triggering the ban.
# ---------------------------------------------------------------------------
YOUTUBE_BACKOFF_MINUTES = 70

_youtube_backoff_until: Optional[datetime.datetime] = None


def _set_youtube_backoff() -> None:
    """Start the rate-limit cooldown. Called when a rate-limit error is seen."""
    global _youtube_backoff_until
    until = datetime.datetime.utcnow() + datetime.timedelta(minutes=YOUTUBE_BACKOFF_MINUTES)
    _youtube_backoff_until = until
    logger.warning(
        f"YouTube rate-limit back-off activated — skipping all YouTube "
        f"extractions until {until.strftime('%H:%M UTC')} "
        f"({YOUTUBE_BACKOFF_MINUTES} min cooldown)."
    )


def is_youtube_backed_off() -> bool:
    """Return True if we are currently in the YouTube rate-limit cooldown."""
    global _youtube_backoff_until
    if _youtube_backoff_until is None:
        return False
    if datetime.datetime.utcnow() < _youtube_backoff_until:
        return True
    # Cooldown expired — clear it
    _youtube_backoff_until = None
    logger.info("YouTube rate-limit back-off expired — resuming YouTube extractions.")
    return False


def activate_youtube_pause(minutes: int = YOUTUBE_BACKOFF_MINUTES) -> dict:
    """
    Manually activate the YouTube back-off for the given number of minutes.

    Called by POST /resolve/youtube-pause from the web UI or command line.
    Identical effect to a rate-limit error triggering _set_youtube_backoff(),
    except the duration is configurable and the source is logged as "manual".
    Returns the pause state dict so the caller can confirm.
    """
    global _youtube_backoff_until
    until = datetime.datetime.utcnow() + datetime.timedelta(minutes=minutes)
    _youtube_backoff_until = until
    logger.warning(
        f"YouTube back-off manually activated — all YouTube extractions "
        f"paused until {until.strftime('%H:%M UTC')} ({minutes} min)."
    )
    return get_youtube_pause_state()


def cancel_youtube_pause() -> dict:
    """
    Cancel an active YouTube back-off immediately.

    Called by DELETE /resolve/youtube-pause from the web UI Resume button.
    Returns the pause state dict (will show paused=False).
    """
    global _youtube_backoff_until
    _youtube_backoff_until = None
    logger.info("YouTube back-off manually cancelled — resuming YouTube extractions now.")
    return get_youtube_pause_state()


def get_youtube_pause_state() -> dict:
    """
    Return current pause state for /health and the web UI.

    paused: bool — whether the back-off is currently active
    minutes_remaining: int | None — minutes left (None if not paused)
    until_utc: str | None — ISO timestamp when pause expires (None if not paused)
    """
    now = datetime.datetime.utcnow()
    if _youtube_backoff_until is not None and now < _youtube_backoff_until:
        remaining = int((_youtube_backoff_until - now).total_seconds() / 60) + 1
        return {
            "paused": True,
            "minutes_remaining": remaining,
            "until_utc": _youtube_backoff_until.isoformat() + "Z",
        }
    return {
        "paused": False,
        "minutes_remaining": None,
        "until_utc": None,
    }


def _is_rate_limit_error(error_msg: Optional[str]) -> bool:
    """Return True if an error message matches a known rate-limit pattern."""
    if not error_msg:
        return False
    low = error_msg.lower()
    for keyword in RATE_LIMIT_SAFEGUARD_KEYWORDS:
        if keyword in low:
            return True
    return False


# ---------------------------------------------------------------------------
# YouTube cookie-stale pause (Session 56).
#
# Distinct from the rate-limit back-off above. When the exported browser
# cookies expire, YouTube stops returning rate-limit phrases and instead
# rejects every request with a bot-check challenge:
#
#   ERROR: [youtube] <id>: Sign in to confirm you're not a bot. Use
#   --cookies-from-browser or --cookies for the authentication. See
#   https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp
#
# Left unhandled, this error is classed as a plain transient failure and the
# video is marked "failed" — one dead entry per resolve, permanently polluting
# the fail log until manually cleared. A single stale cookie can bury hundreds
# of otherwise-good videos this way.
#
# This pause is a circuit-breaker: the FIRST bot-check rejection flips a
# boolean that makes every background/play-time path skip YouTube extraction
# entirely (leaving those videos pending, never failed, never deleted), while
# Vimeo and local content keep resolving normally. Unlike the rate-limit
# back-off there is NO timer — a stale cookie does not fix itself on a clock.
# It clears in exactly two ways:
#   1. Automatically, the moment any YouTube extraction succeeds again (the
#      success branch of _record_youtube_result), i.e. right after you refresh
#      cookies.txt and the warm pass lands its first good resolve.
#   2. Manually, via DELETE /resolve/cookie-stale (the web UI Resume button).
#
# In-memory only: a restart clears it until the next bot-check rejection.
# ---------------------------------------------------------------------------

# Apostrophe-free anchors — the live log uses a curly apostrophe in "you're",
# so we deliberately match only up to "you" and rely on the yt-dlp cookie
# guidance phrases, which are ASCII-stable regardless of quote style.
COOKIE_STALE_KEYWORDS = (
    "sign in to confirm you",
    "--cookies for the authentication",
    "--cookies-from-browser",
    "how-do-i-pass-cookies",
)

_youtube_cookie_stale_paused: bool = False


def _is_cookie_stale_error(error_msg: Optional[str]) -> bool:
    """Return True if an error message is a YouTube bot-check / stale-cookie rejection."""
    if not error_msg:
        return False
    low = error_msg.lower()
    for keyword in COOKIE_STALE_KEYWORDS:
        if keyword in low:
            return True
    return False


def _set_cookie_stale_pause() -> None:
    """Flip the cookie-stale pause on. Called when a bot-check rejection is seen."""
    global _youtube_cookie_stale_paused
    if not _youtube_cookie_stale_paused:
        _youtube_cookie_stale_paused = True
        logger.warning(
            "YouTube cookie-stale pause ACTIVATED — cookies appear expired. "
            "Skipping all YouTube extractions (videos stay pending, not failed) "
            "until a fresh cookie succeeds or the web UI Resume button is used."
        )


def _clear_cookie_stale_pause(reason: str = "manual") -> None:
    """Flip the cookie-stale pause off. Called on first success or manual resume."""
    global _youtube_cookie_stale_paused
    if _youtube_cookie_stale_paused:
        _youtube_cookie_stale_paused = False
        logger.info(
            f"YouTube cookie-stale pause CLEARED ({reason}) — resuming YouTube extractions."
        )


def is_cookie_stale_paused() -> bool:
    """Return True if YouTube extraction is currently paused due to stale cookies."""
    return _youtube_cookie_stale_paused


def cancel_cookie_stale_pause() -> dict:
    """
    Manually clear the cookie-stale pause.

    Called by DELETE /resolve/cookie-stale from the web UI Resume button.
    Safe to call even if no pause is active. Returns the state dict.
    """
    _clear_cookie_stale_pause(reason="manual resume")
    return get_cookie_stale_state()


def get_cookie_stale_state() -> dict:
    """Return the current cookie-stale pause state for /health and the web UI."""
    return {"cookie_stale_paused": _youtube_cookie_stale_paused}


# ---------------------------------------------------------------------------
# YouTube background-resolve switch (Session 56).
#
# The reason cookies keep dying is VOLUME: with ~12,000+ pending YouTube videos
# and resolved URLs that expire in ~3 hours, the background resolve/warm passes
# hammer YouTube thousands of times a day from one IP + one cookie — a textbook
# automated-scraper pattern that YouTube responds to by killing the session.
#
# Since YouTube resolved URLs go stale within hours anyway, bulk pre-resolving
# them is mostly wasted cookie-burn. This switch turns OFF all *background*
# YouTube extraction (the scheduled pending pass and the TV warm pass), so the
# cookie is only ever touched when a video is actually PLAYED.
#
# Deliberately does NOT affect:
#   - Vimeo / local content — they don't use the cookie and pre-resolve as before.
#   - resolve_video() / resolve_video_for_tv() — the on-demand PLAY-TIME paths.
#     Pressing play on a YouTube video still resolves it live. This switch only
#     stops the *speculative* background grind, not on-play resolution.
#
# Default: DISABLED (background YouTube resolve OFF). Flip to True — or wire the
# planned Settings-page toggle to set_youtube_background_resolve(True) — to turn
# the background passes back on (e.g. once ffmpeg remux-on-play lands and makes
# a fuller YouTube catalog worthwhile).
#
# In-memory only, like the pauses above: a restart returns to this default.
# ---------------------------------------------------------------------------
_youtube_background_resolve_enabled: bool = False


def is_youtube_background_resolve_enabled() -> bool:
    """
    Return True if background (scheduled/warm) YouTube extraction is allowed.

    The single source of truth checked by resolve_batch() and warm_tv_cache().
    Play-time paths do NOT check this — pressing play always resolves.
    """
    return _youtube_background_resolve_enabled


def set_youtube_background_resolve(enabled: bool) -> dict:
    """
    Enable or disable background YouTube extraction.

    Intended for a future Settings-page toggle (GET/POST endpoint). Returns the
    state dict so the caller can confirm.
    """
    global _youtube_background_resolve_enabled
    _youtube_background_resolve_enabled = bool(enabled)
    logger.info(
        f"YouTube background resolve {'ENABLED' if enabled else 'DISABLED'} "
        f"— scheduled pending pass and TV warm pass will "
        f"{'process' if enabled else 'skip'} YouTube videos."
    )
    return get_youtube_background_resolve_state()


def get_youtube_background_resolve_state() -> dict:
    """Return the current background-resolve switch state for /health and the web UI."""
    return {"youtube_background_resolve_enabled": _youtube_background_resolve_enabled}


def _is_youtube_source(url: Optional[str]) -> bool:
    u = url or ""
    return "youtube.com" in u or "youtu.be" in u


def _record_youtube_result(source_url: Optional[str], error_msg: Optional[str]) -> None:
    """
    Record the outcome of a yt-dlp extraction for cookie-health tracking
    and rate-limit back-off.

    Call with error_msg=None on success. Non-YouTube sources are ignored;
    non-bot-check errors (dead videos, timeouts) don't touch the cookie
    status — they say nothing about cookies. Rate-limit errors trigger
    the back-off regardless of bot-check status.
    """
    if not _is_youtube_source(source_url):
        return
    now = datetime.datetime.utcnow()
    if error_msg is None:
        _youtube_cookie_status["last_success"] = now
        _youtube_cookie_status["bot_error_count"] = 0
        # A successful YouTube extraction means the cookie is good again —
        # auto-clear the cookie-stale pause (self-healing after a refresh).
        _clear_cookie_stale_pause(reason="successful YouTube resolve")
        return
    # Rate-limit errors activate the back-off cooldown (separate from cookie health).
    if _is_rate_limit_error(error_msg):
        _set_youtube_backoff()
    # Bot-check / stale-cookie rejection: activate the cookie-stale pause so the
    # scheduler stops feeding YouTube videos into the "failed" bucket.
    if _is_cookie_stale_error(error_msg):
        _set_cookie_stale_pause()
    low = error_msg.lower()
    if any(sig in low for sig in _BOT_CHECK_SIGNATURES):
        _youtube_cookie_status["last_bot_error"] = now
        _youtube_cookie_status["bot_error_count"] += 1
        if _youtube_cookie_status["bot_error_count"] == 1:
            logger.warning(
                "YouTube cookie health: bot-check rejection detected — "
                "cookies are likely stale (flagged on /health and the web UI)"
            )


def get_youtube_cookie_status() -> dict:
    """
    Cookie health + pause state summary for /health.
    state: 'ok' | 'stale' | 'unknown'
    Also includes the current pause state so the web UI gets everything
    in one call.
    """
    s = _youtube_cookie_status
    if s["last_success"] is None and s["last_bot_error"] is None:
        state = "unknown"
    elif s["last_bot_error"] is not None and (
        s["last_success"] is None or s["last_bot_error"] > s["last_success"]
    ):
        state = "stale"
    else:
        state = "ok"
    result = {
        "state": state,
        "last_success": s["last_success"].isoformat() + "Z" if s["last_success"] else None,
        "last_bot_error": s["last_bot_error"].isoformat() + "Z" if s["last_bot_error"] else None,
        "bot_error_count": s["bot_error_count"],
    }
    result.update(get_youtube_pause_state())
    result.update(get_cookie_stale_state())
    result.update(get_youtube_background_resolve_state())
    return result

# Hard wall-clock timeout for a single yt-dlp extraction call.
# If yt-dlp hasn't returned within this many seconds, the subprocess is killed
# and the video is marked failed (transient). This prevents one hung YouTube
# video from blocking the entire batch queue for minutes.
YTDLP_TIMEOUT_SECONDS = 90

# Reusable process pool for yt-dlp calls — capped at 2 concurrent extractions.
# Was 4; reduced in Session 54 to avoid triggering YouTube rate limits.
# 2 workers means at most 2 simultaneous yt-dlp processes hitting YouTube,
# which combined with the inter-request sleep keeps us well under the limit.
_process_pool = concurrent.futures.ProcessPoolExecutor(max_workers=2)

FORMAT_SELECTOR = (
    # Prefer combined mp4 streams (video+audio) up to 1080p
    "best[height<=1080][ext=mp4][vcodec!*=none][acodec!*=none]/"
    "best[ext=mp4][vcodec!*=none][acodec!*=none]/"
    "best[vcodec!*=none][acodec!*=none][protocol!=http_dash_segments]/"
    "best[vcodec!*=none][acodec!*=none]/"
    # HLS combined streams — YouTube serves these with video+audio merged (e.g. 1080p m3u8).
    "best[protocol=m3u8_native][vcodec!*=none][acodec!*=none]/"
    "best[protocol^=m3u8][vcodec!*=none][acodec!*=none]/"
    # Format 18 fallback — 360p combined mp4
    "18/"
    # Vimeo and some sources only serve split streams (no combined format available).
    # Fall back to best split pair so these videos resolve instead of failing entirely.
    "bestvideo[ext=mp4]+bestaudio[ext=m4a]/"
    "bestvideo+bestaudio/"
    "best[protocol!=http_dash_segments]/"
    "best"
)

PERMANENT_ERROR_KEYWORDS = [
    "http error 404",
    "not found",
    "video unavailable",
    "private video",
    "removed by the uploader",
    "account terminated",
    "copyright claim",
    "violates youtube",
    "content warning",
    "sign in to confirm your age",
    "join this channel to get access",
    "members-only content",
    "this video has been removed",
    "video is no longer available",
    "this video does not exist",
    "page not found",
]

# Rate-limit safeguard — checked BEFORE permanent keywords.
# YouTube rate-limit errors contain "video unavailable" in their message, which
# would normally trigger a permanent deletion. These phrases unambiguously identify
# a transient rate-limit condition and must short-circuit the permanent check.
# Confirmed from live log: "Video unavailable. This content isn't available, try
# again later. The current session has been rate-limited by YouTube for up to an hour."
RATE_LIMIT_SAFEGUARD_KEYWORDS = [
    "the current session has been rate-limited",
    "rate-limited by youtube",
    "it is recommended to use `-t sleep`",
    "http error 429",
    "too many requests",
    "please try again later",
]

# Vimeo CDN domains — fingerprinting ONLY fires for URLs from these domains.
# This prevents the broad /video/{hash}/ pattern from false-matching YouTube
# CDN paths and incorrectly deleting unrelated videos as duplicates.
VIMEO_CDN_DOMAINS = (
    "vimeocdn.com",
    "vimeo.com",
    "vod-progressive-ak.vimeocdn.com",
    "vod-adaptive-ak.vimeocdn.com",
    "skyfire.vimeo.com",
    "clips.vimeo.com",
)


def _is_hls_url(url: str) -> bool:
    if not url:
        return False
    path = url.split("?")[0].lower()
    return path.endswith(".m3u8") or "m3u8" in path


def _is_dash_url(url: str) -> bool:
    if not url:
        return False
    path = url.split("?")[0].lower()
    return path.endswith(".mpd") or "playlist.mpd" in path or "/playlist/av/primary" in url.lower()


def _is_adaptive_url(url: str) -> bool:
    return _is_hls_url(url) or _is_dash_url(url)


def _is_vimeo_cdn_url(url: str) -> bool:
    """Return True only for known Vimeo CDN domains."""
    if not url:
        return False
    url_lower = url.lower()
    return any(domain in url_lower for domain in VIMEO_CDN_DOMAINS)


def extract_cdn_fingerprint(url: str) -> Optional[str]:
    """
    Extract a stable CDN file fingerprint from a resolved Vimeo stream URL.

    Domain-gated: only fires for Vimeo CDN URLs. Non-Vimeo URLs (YouTube,
    Reddit, etc.) always return None and are never deduped against each other.

    Patterns (priority order):
      /sep/video/{hash}/           — standard Vimeo progressive CDN
      /video/{hash}/               — alternate Vimeo CDN layout (Vimeo-only, domain-gated)
      id=o-{fingerprint}           — signed CDN token query param
      vimeo-transcode-storage-*/…/{file_id}.mp4  — vod-progressive-ak CDN
    """
    if not url:
        return None

    if not _is_vimeo_cdn_url(url):
        return None

    m = re.search(r'/sep/video/([A-Za-z0-9_-]+)/', url)
    if m:
        return m.group(1)

    m = re.search(r'/video/([A-Za-z0-9_-]{20,})/', url)
    if m:
        return m.group(1)

    m = re.search(r'[?&]id=(o-[A-Za-z0-9_-]+)', url)
    if m:
        return m.group(1)

    m = re.search(r'vimeo-transcode-storage-[^/]+/(?:[^/]+/){3,}(\d+)\.mp4', url)
    if m:
        return f"vts_{m.group(1)}"

    return None


# ---------------------------------------------------------------------------
# Module-level extraction function — must be at top level so ProcessPoolExecutor
# can pickle it across process boundaries.
# ---------------------------------------------------------------------------

def _extract_sync_worker(url: str, cookies_path: Optional[str]) -> Tuple[Optional[dict], Optional[str], bool]:
    """
    Synchronous yt-dlp extraction. Runs in a subprocess via ProcessPoolExecutor.

    Returns (stream_info_dict | None, error_msg | None, is_permanent: bool).
    Using a dict instead of StreamInfo so it survives pickling across processes.
    """
    import yt_dlp

    ydl_opts = {
        "format": FORMAT_SELECTOR,
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
        "skip_download": True,
        "simulate": True,
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,
        # Use Node.js for YouTube JS challenge solving. The Python API requires a
        # dict format: {runtime_name: {options}}. Empty dict means use defaults.
        "js_runtimes": {"node": {}},
    }

    if cookies_path and os.path.isfile(cookies_path):
        ydl_opts["cookiefile"] = cookies_path

    permanent_keywords = [
        "http error 404", "not found", "video unavailable", "private video",
        "removed by the uploader", "account terminated", "copyright claim",
        "violates youtube", "content warning", "sign in to confirm your age",
        "join this channel to get access", "members-only content",
        "this video has been removed", "video is no longer available",
        "this video does not exist", "page not found",
    ]

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        if info is None:
            return None, "yt-dlp returned no info", False

        stream_url = info.get("url")
        audio_url = None
        requested_formats = info.get("requested_formats", [])
        if not stream_url:
            if requested_formats:
                stream_url = requested_formats[0].get("url")
                # If yt-dlp selected a split stream (bestvideo+bestaudio),
                # capture the audio URL from the second requested format.
                if len(requested_formats) >= 2:
                    audio_url = requested_formats[1].get("url")
        if not stream_url:
            all_formats = info.get("formats", [])
            if all_formats:
                stream_url = all_formats[-1].get("url")
        if not stream_url:
            return None, "No stream URL found in yt-dlp output", False

        ext = info.get("ext", "unknown")
        height = info.get("height") or (
            requested_formats[0].get("height")
            if requested_formats else None
        )
        format_note = f"{ext}/{height}p" if height else ext

        return {
            "stream_url": stream_url,
            "audio_url": audio_url,
            "format_note": format_note,
            "width": info.get("width"),
            "height": height,
            "duration": info.get("duration"),
            "thumbnail": info.get("thumbnail"),
            "title": info.get("title"),
            "uploader": info.get("uploader"),
            "protocol": info.get("protocol", "unknown"),
            "ext": ext,
        }, None, False

    except Exception as e:
        error_msg = str(e)
        error_lower = error_msg.lower()
        # GUARD: rate-limit errors are transient. Check before permanent keywords
        # because YouTube rate-limit messages contain "video unavailable" which
        # would otherwise trigger auto-delete. Confirmed from live logs.
        for keyword in RATE_LIMIT_SAFEGUARD_KEYWORDS:
            if keyword in error_lower:
                return None, f"Transient(rate-limit): {error_msg[:300]}", False
        for keyword in permanent_keywords:
            if keyword in error_lower:
                return None, f"Permanent: {error_msg[:300]}", True
        return None, f"Transient: {error_msg[:300]}", False


def _extract_tv_sync_worker(url: str, cookies_path: Optional[str], prefer_hls: bool = False) -> Tuple[Optional[dict], Optional[str], bool]:
    """
    TV-specific yt-dlp extraction. Returns both video_url and audio_url separately
    so the client can pass them to VLC or another external player that can merge
    split streams natively — something ExoPlayer cannot do without a manifest.

    prefer_hls=True: used for Vimeo, which serves HLS-only split streams.
    Explicitly filters for m3u8_native protocol so we always get HLS sub-playlist
    URLs rather than progressive MP4 URLs — required for the master manifest
    approach where TiviMate fetches both sub-playlists in parallel.

    prefer_hls=False (default): used for YouTube and others that serve split
    progressive MP4 (video-only + audio-only) — these go through the DASH
    manifest path which ExoPlayer handles natively.

    Returns (stream_info_dict | None, error_msg | None, is_permanent: bool).
    """
    import yt_dlp

    if prefer_hls:
        # Vimeo: explicitly prefer HLS split streams so URLs are m3u8 sub-playlists
        # that can be declared in a synthetic HLS master manifest. Without the
        # protocol filter yt-dlp may pick progressive MP4 which can't go into
        # an #EXT-X-STREAM-INF entry.
        TV_FORMAT_SELECTOR = (
            "bestvideo[vcodec^=avc1][protocol^=m3u8]+bestaudio[protocol^=m3u8]/"
            "bestvideo[vcodec^=avc1]+bestaudio[ext=m4a]/"
            "bestvideo[vcodec^=avc1]+bestaudio/"
            "bestvideo+bestaudio[ext=m4a]/"
            "bestvideo+bestaudio/"
            "best"
        )
    else:
        TV_FORMAT_SELECTOR = (
            "bestvideo[vcodec^=avc1]+bestaudio[ext=m4a]/"
            "bestvideo[vcodec^=avc1]+bestaudio/"
            "bestvideo+bestaudio[ext=m4a]/"
            "bestvideo+bestaudio/"
            "best"
        )

    ydl_opts = {
        "format": TV_FORMAT_SELECTOR,
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
        "skip_download": True,
        "simulate": True,
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,
        # Use Node.js for YouTube JS challenge solving.
        "js_runtimes": {"node": {}},
    }

    if cookies_path and os.path.isfile(cookies_path):
        ydl_opts["cookiefile"] = cookies_path

    permanent_keywords = [
        "http error 404", "not found", "video unavailable", "private video",
        "removed by the uploader", "account terminated", "copyright claim",
        "violates youtube", "content warning", "sign in to confirm your age",
        "join this channel to get access", "members-only content",
        "this video has been removed", "video is no longer available",
        "this video does not exist", "page not found",
    ]

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        if info is None:
            return None, "yt-dlp returned no info", False

        # ── Split stream path ─────────────────────────────────────────────────
        requested = info.get("requested_formats", [])
        video_url = None
        audio_url = None
        height = info.get("height")

        if len(requested) >= 2:
            # Split stream — video and audio are separate URLs
            video_fmt = requested[0]
            audio_fmt = requested[1]
            video_url = video_fmt.get("url")
            audio_url = audio_fmt.get("url")
            height = video_fmt.get("height") or height
        elif len(requested) == 1:
            video_url = requested[0].get("url")
        else:
            video_url = info.get("url")

        if not video_url:
            all_formats = info.get("formats", [])
            if all_formats:
                video_url = all_formats[-1].get("url")

        if not video_url:
            return None, "No video URL found in yt-dlp output", False

        ext = info.get("ext", "unknown")
        format_note = f"{ext}/{height}p" if height else ext

        return {
            "stream_url": video_url,
            "audio_url": audio_url,
            "format_note": format_note,
            "width": info.get("width"),
            "height": height,
            "duration": info.get("duration"),
            "thumbnail": info.get("thumbnail"),
            "title": info.get("title"),
            "uploader": info.get("uploader"),
            "protocol": info.get("protocol", "unknown"),
            "ext": ext,
        }, None, False

    except Exception as e:
        error_msg = str(e)
        error_lower = error_msg.lower()
        # GUARD: rate-limit errors are transient. Check before permanent keywords
        # because YouTube rate-limit messages contain "video unavailable" which
        # would otherwise trigger auto-delete. Confirmed from live logs.
        for keyword in RATE_LIMIT_SAFEGUARD_KEYWORDS:
            if keyword in error_lower:
                return None, f"Transient(rate-limit): {error_msg[:300]}", False
        for keyword in permanent_keywords:
            if keyword in error_lower:
                return None, f"Permanent: {error_msg[:300]}", True
        return None, f"Transient: {error_msg[:300]}", False


def _fetch_thumbnail_sync_worker(url: str, cookies_path: Optional[str]):
    """Module-level thumbnail fetch — picklable for ProcessPoolExecutor.

    Session 58: returns a (thumbnail_url, outcome) tuple instead of a bare
    Optional[str]. Outcomes:
      "ok"        — thumbnail found (thumbnail_url is the URL)
      "no_thumb"  — extraction succeeded but the video genuinely has no
                    thumbnail; safe to mark terminally
      "permanent" — the video is dead (404/private/removed); safe to mark
                    terminally so it stops clogging the backfill queue
      "transient" — rate limit, network error, timeout, or anything else
                    temporary; caller must LEAVE thumbnail_url NULL so the
                    video is retried on a later pass. The old behavior
                    stamped 'unavailable' on these, permanently poisoning
                    records during outages like a Vimeo 403 block.
    """
    import yt_dlp

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "simulate": True,
        "extract_flat": False,
        "socket_timeout": 20,
        "retries": 2,
    }
    if cookies_path and os.path.isfile(cookies_path):
        ydl_opts["cookiefile"] = cookies_path

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        if info is None:
            return (None, "transient")
        thumbnails = info.get("thumbnails") or []
        if thumbnails:
            best = thumbnails[-1].get("url")
            if best:
                return (best, "ok")
        single = info.get("thumbnail")
        if single:
            return (single, "ok")
        return (None, "no_thumb")
    except Exception as e:
        msg = str(e).lower()
        # Rate-limit safeguard first — these messages can contain permanent-
        # looking phrases like "video unavailable" (documented above for the
        # resolve path; same logic applies here).
        for kw in RATE_LIMIT_SAFEGUARD_KEYWORDS:
            if kw in msg:
                return (None, "transient")
        for kw in PERMANENT_ERROR_KEYWORDS:
            if kw in msg:
                return (None, "permanent")
        return (None, "transient")


class StreamInfo:
    """Result of a successful resolution."""

    def __init__(self, stream_url, format_note="", width=None, height=None,
                 duration=None, thumbnail=None, title=None, uploader=None,
                 audio_url=None):
        self.stream_url = stream_url
        self.audio_url = audio_url  # Separate audio URL for split streams (TV/VLC path)
        self.format_note = format_note
        self.width = width
        self.height = height
        self.duration = duration
        self.thumbnail = thumbnail
        self.title = title
        self.uploader = uploader

    @classmethod
    def from_dict(cls, d: dict) -> "StreamInfo":
        return cls(
            stream_url=d["stream_url"],
            audio_url=d.get("audio_url"),
            format_note=d.get("format_note", ""),
            width=d.get("width"),
            height=d.get("height"),
            duration=d.get("duration"),
            thumbnail=d.get("thumbnail"),
            title=d.get("title"),
            uploader=d.get("uploader"),
        )


class ResolverService:
    """Resolves source URLs into direct playable stream URLs using yt-dlp."""

    def __init__(self, db_session: AsyncSession):
        self._db = db_session
        self._cookies_path = settings.ytdlp_cookies_path

    async def resolve_video(self, video_id: int, force: bool = False) -> Optional[dict]:
        """
        Resolve a video by its database ID.

        Cache check → yt-dlp extraction (with hard timeout) → dedup → result.

        If this video resolves but is a lower-scored CDN duplicate, playback is
        transparently redirected to the keeper's stream URL instead of failing.
        """
        stmt = select(Video).where(Video.id == video_id)
        result = await self._db.execute(stmt)
        video = result.scalar_one_or_none()

        if video is None:
            logger.warning(f"Video ID {video_id} not found in database")
            return None

        self._db.expire(video)
        await self._db.refresh(video)

        # Return cached resolution if still valid
        if not force and self._is_cache_valid(video):
            url_type = "HLS" if _is_hls_url(video.resolved_stream_url) else \
                       "DASH" if _is_dash_url(video.resolved_stream_url) else "MP4"
            logger.info(f"Cache hit for video {video_id} [{url_type}]: {video.title[:60]}")
            return self._build_result(video)

        # Skip permanently failed unless forced
        if not force and video.resolution_status == "failed":
            logger.debug(f"Skipping permanently failed video {video_id}: {video.title}")
            return None

        # Resolve with yt-dlp (hard timeout via ProcessPoolExecutor)
        logger.info(f"Re-resolving video {video_id} (source: {video.source_url[:80]})")
        stream_info, error_msg, is_permanent = await self._extract_with_ytdlp(video.source_url)
        _record_youtube_result(
            video.source_url, None if stream_info is not None else error_msg
        )

        if stream_info is not None:
            url_type = "HLS" if _is_hls_url(stream_info.stream_url) else \
                       "DASH" if _is_dash_url(stream_info.stream_url) else "MP4"
            logger.info(
                f"Resolved video {video_id} [{url_type}]: {stream_info.format_note} "
                f"| url_preview={stream_info.stream_url[:80]}"
            )

            video.resolved_stream_url = stream_info.stream_url
            video.resolved_format = stream_info.format_note
            video.resolved_at = datetime.datetime.utcnow()
            video.resolution_status = "resolved"
            video.resolution_error = None

            if stream_info.thumbnail and not video.thumbnail_url:
                video.thumbnail_url = stream_info.thumbnail
            if stream_info.duration and not video.duration_seconds:
                video.duration_seconds = stream_info.duration
            if stream_info.uploader and not video.artist:
                video.artist = stream_info.uploader

            await self._db.commit()

            # Auto-dedup: if this is the lower-scored duplicate, redirect to keeper
            try:
                keeper_result = await self.dedup_after_resolve(video)
                if keeper_result is not None:
                    logger.info(
                        f"Video {video_id} was lower-scored CDN duplicate — "
                        "deleted. Redirecting playback to keeper."
                    )
                    return keeper_result
            except Exception as exc:
                logger.warning(f"Auto-dedup check failed for video {video_id}: {exc}")

            return self._build_result(video)

        elif is_permanent:
            logger.warning(
                f"Permanently dead video {video_id} '{video.title[:60]}' — "
                f"auto-deleting. Reason: {error_msg}"
            )
            await self._delete_video(video)
            await self._db.commit()
            return None

        else:
            # Rate-limit errors: leave the video as pending (or resolved if it
            # was previously resolved) so the scheduler retries it after the
            # back-off expires. Marking it failed would remove it from the
            # pending queue permanently — it would never be retried.
            if _is_rate_limit_error(error_msg):
                logger.warning(
                    f"Rate-limit error for video {video_id} — "
                    f"leaving status unchanged so it retries after back-off. "
                    f"Error: {error_msg[:200]}"
                )
                return None
            # Stale-cookie (bot-check) errors: leave the video pending, exactly
            # like rate-limit. Marking it failed would bury a good video in the
            # fail log over an expired cookie. The pause was already activated by
            # _record_youtube_result; the video retries once cookies are refreshed.
            if _is_cookie_stale_error(error_msg):
                logger.warning(
                    f"Stale-cookie error for video {video_id} — "
                    f"leaving status pending so it retries after cookie refresh. "
                    f"Error: {error_msg[:200]}"
                )
                return None
            video.resolution_status = "failed"
            video.resolved_at = datetime.datetime.utcnow()
            video.resolution_error = error_msg or "Unknown error"
            await self._db.commit()
            logger.warning(f"Failed to resolve video {video_id}: {error_msg}")
            return None

    async def dedup_after_resolve(self, video: Video) -> Optional[dict]:
        """
        Check for CDN fingerprint duplicates after a fresh resolve.

        Only fires for Vimeo CDN URLs (domain-gated in extract_cdn_fingerprint).

        Returns None if no duplicate or this video is the keeper.
        Returns keeper's result dict if this video was deleted as a duplicate
        — caller returns this so playback redirects transparently.
        """
        stream_url = video.resolved_stream_url
        if not stream_url:
            return None

        fingerprint = extract_cdn_fingerprint(stream_url)
        if fingerprint is None:
            return None

        stmt = select(Video).where(
            Video.resolution_status == "resolved",
            Video.resolved_stream_url.isnot(None),
            Video.id != video.id,
        )
        result = await self._db.execute(stmt)
        candidates = result.scalars().all()

        matches = [
            v for v in candidates
            if extract_cdn_fingerprint(v.resolved_stream_url or "") == fingerprint
        ]

        if not matches:
            return None

        group = [video] + matches
        group.sort(key=lambda v: (-(v.reddit_score or 0), v.created_at or ""))
        keeper = group[0]
        dupes  = group[1:]

        logger.info(
            f"Auto-dedup: fingerprint {fingerprint[:16]}… matched {len(matches)} video(s). "
            f"Keeping video {keeper.id} (score={keeper.reddit_score}, title={keeper.title[:40]})"
        )

        this_video_deleted = False
        for dupe in dupes:
            logger.info(
                f"  Auto-dedup: deleting video {dupe.id} "
                f"(score={dupe.reddit_score}, title={dupe.title[:50]})"
            )
            if dupe.id == video.id:
                this_video_deleted = True
            await self._delete_video(dupe)

        await self._db.commit()

        if this_video_deleted:
            await self._db.refresh(keeper)
            logger.info(
                f"  Auto-dedup redirect: serving keeper video {keeper.id} "
                f"(title={keeper.title[:50]}) for deleted video {video.id}"
            )
            return self._build_result(keeper)

        return None

    async def purge_duplicate_cdn_files(self) -> dict:
        """Full CDN fingerprint dedup sweep across all resolved videos."""
        stmt = select(Video).where(
            Video.resolution_status == "resolved",
            Video.resolved_stream_url.isnot(None),
        )
        result = await self._db.execute(stmt)
        resolved_videos = result.scalars().all()

        groups: dict[str, list] = {}
        no_fingerprint = 0

        for v in resolved_videos:
            fp = extract_cdn_fingerprint(v.resolved_stream_url)
            if fp is None:
                no_fingerprint += 1
                continue
            groups.setdefault(fp, []).append(v)

        duplicate_groups = {fp: vids for fp, vids in groups.items() if len(vids) > 1}
        deleted_count = 0
        kept_count = 0

        for fp, vids in duplicate_groups.items():
            vids.sort(key=lambda v: (-(v.reddit_score or 0), v.created_at or ""))
            keeper = vids[0]
            dupes = vids[1:]
            logger.info(
                f"CDN fingerprint {fp[:16]}…: keeping video {keeper.id} "
                f"(score={keeper.reddit_score}, title={keeper.title[:40]}), "
                f"deleting {len(dupes)} duplicate(s)"
            )
            for dupe in dupes:
                await self._delete_video(dupe)
                deleted_count += 1
            kept_count += 1

        await self._db.commit()
        return {
            "duplicate_groups_found": len(duplicate_groups),
            "deleted_count": deleted_count,
            "kept_count": kept_count,
            "no_fingerprint_count": no_fingerprint,
        }

    def _build_result(self, video: Video) -> dict:
        return {
            "id": video.id,
            "title": video.title,
            "artist": video.artist,
            "stream_url": video.resolved_stream_url,
            "format": video.resolved_format,
            "resolved_at": video.resolved_at.isoformat() if video.resolved_at else None,
            "source_url": video.source_url,
            "thumbnail_url": video.thumbnail_url,
        }

    def _tv_cache_result(self, video: Video) -> Optional[dict]:
        """
        Return the cached TV-path resolution for a video, or None on cache miss.

        A valid TV cache requires BOTH a fresh resolved_at timestamp (checked
        by _is_cache_valid — 20 min for HLS, 3 h for MP4) AND a non-NULL
        resolved_audio_url. The audio column doubles as the "was this video
        ever resolved via the TV path?" marker:
          NULL  -> never TV-resolved (standard resolver only) -> cache miss
          ""    -> TV-resolved as a combined stream (no separate audio)
          "..." -> TV-resolved as split video + audio streams
        """
        if not self._is_cache_valid(video):
            return None
        if video.resolved_audio_url is None:
            return None

        kind = "split" if video.resolved_audio_url else "combined"
        logger.info(
            f"TV resolve cache hit ({kind}): video {video.id} "
            f"'{(video.title or '')[:60]}'"
        )
        return {
            "id": video.id,
            "title": video.title,
            "artist": video.artist,
            "stream_url": video.resolved_stream_url,
            "audio_url": video.resolved_audio_url or None,
            "format": video.resolved_format,
            "source_url": video.source_url,
            "thumbnail_url": video.thumbnail_url,
        }

    async def resolve_video_for_tv(
        self, video_id: int, allow_cookie_probe: bool = False
    ) -> Optional[dict]:
        """
        TV-specific resolution that returns both video_url and audio_url separately.

        Caches both stream_url and audio_url in the DB so repeat plays are instant.
        The cache TTL matches _is_cache_valid() — 20 minutes for HLS/adaptive URLs
        (Vimeo signed tokens), 3 hours for progressive MP4 (YouTube).

        Concurrency: guarded by a per-video asyncio.Lock. When TiviMate fires
        rapid retries at a slow video (or the background warm job and a live
        playback request collide on the same video), only ONE yt-dlp
        extraction runs; every other caller waits, then reads the freshly
        cached result — no duplicate yt-dlp processes.

        allow_cookie_probe: when True, bypass ONLY the stale-cookie skip guard
        (never the rate-limit back-off, which is a real ban). Used by the
        warm_tv_cache probe so a single YouTube extraction can test whether
        refreshed cookies now work — a success auto-clears the pause.

        Returns a dict with stream_url (video), audio_url (audio, may be None
        for combined streams), and the usual metadata fields.
        """
        stmt = select(Video).where(Video.id == video_id)
        result = await self._db.execute(stmt)
        video = result.scalar_one_or_none()

        if video is None:
            logger.warning(f"TV resolve: Video ID {video_id} not found")
            return None

        if video.resolution_status == "failed":
            logger.debug(f"TV resolve: skipping permanently failed video {video_id}")
            return None

        # Fast path: valid TV cache — return immediately, no lock, no yt-dlp.
        cached = self._tv_cache_result(video)
        if cached is not None:
            return cached

        # Back-off guard: if YouTube has rate-limited us, don't make things worse
        # by poking it again at play time. Return None (→ 502) and let the cache
        # warm up once the cooldown expires, rather than extending the ban.
        _cookie_stale_block = is_cookie_stale_paused() and not allow_cookie_probe
        if _is_youtube_source(video.source_url) and (
            is_youtube_backed_off() or _cookie_stale_block
        ):
            reason = "rate-limit back-off" if is_youtube_backed_off() else "stale-cookie pause"
            logger.warning(
                f"TV resolve: YouTube {reason} active — skipping extraction "
                f"for video {video_id} '{(video.title or '')[:50]}'"
            )
            return None

        # Cache miss — serialize extraction per video ID so concurrent
        # requests (TiviMate retries, warm job collisions) never spawn
        # duplicate yt-dlp processes for the same video.
        lock = _tv_resolve_locks.setdefault(video_id, asyncio.Lock())
        async with lock:
            # While we waited for the lock, another request may have finished
            # resolving this exact video. Re-read the row and re-check the
            # cache before doing any work of our own (double-checked locking).
            try:
                await self._db.refresh(video)
            except Exception:
                # Row vanished — a concurrent resolve found the video
                # permanently gone and deleted it from the DB.
                logger.warning(
                    f"TV resolve: video {video_id} was deleted while waiting for lock"
                )
                return None

            if video.resolution_status == "failed":
                logger.debug(
                    f"TV resolve: video {video_id} marked failed while waiting for lock"
                )
                return None

            cached = self._tv_cache_result(video)
            if cached is not None:
                return cached

            return await self._tv_extract_and_cache(video)

    async def _tv_extract_and_cache(self, video: Video) -> Optional[dict]:
        """
        Run the split (video + audio) yt-dlp extraction for a video and cache
        both URLs in the DB. Callers MUST hold the per-video TV resolve lock —
        this is only called from resolve_video_for_tv().
        """
        video_id = video.id

        # Use the split extractor (bestvideo+bestaudio) for all providers —
        # not just YouTube. Vimeo increasingly serves video-only and audio-only
        # HLS streams with no combined rendition, so the standard single-URL
        # resolver produces video-with-no-audio. Running the TV sync worker
        # for all providers ensures we always get both URLs when they exist.
        # The TV sync worker's TV_FORMAT_SELECTOR already handles fallback to
        # combined streams when no split rendition exists, so this is safe for
        # providers that do serve combined streams (we just get audio_url=None).
        logger.info(
            f"TV resolve: extracting split URLs for video {video_id} "
            f"(provider={video.source_provider}) '{(video.title or '')[:60]}'"
        )

        # Vimeo needs HLS-preferring format selection so both URLs come back
        # as m3u8 sub-playlists, compatible with the synthetic master manifest.
        is_vimeo = (
            "vimeo.com" in (video.source_url or "") or
            video.source_provider == "vimeo"
        )

        import functools
        loop = asyncio.get_event_loop()
        try:
            result_dict, error_msg, is_permanent = await asyncio.wait_for(
                loop.run_in_executor(
                    _process_pool,
                    functools.partial(
                        _extract_tv_sync_worker,
                        video.source_url,
                        self._cookies_path,
                        is_vimeo,  # prefer_hls
                    ),
                ),
                timeout=float(YTDLP_TIMEOUT_SECONDS),
            )
        except asyncio.TimeoutError:
            logger.error(f"TV resolve: yt-dlp timed out for video {video_id}")
            return None
        except Exception as e:
            logger.error(f"TV resolve: extraction error for video {video_id}: {e}")
            return None

        _record_youtube_result(
            video.source_url, None if result_dict is not None else error_msg
        )

        if result_dict is None:
            if is_permanent:
                logger.warning(f"TV resolve: video {video_id} permanently gone — {error_msg}")
                await self._delete_video(video)
                await self._db.commit()
            elif _is_rate_limit_error(error_msg):
                # Rate-limit: don't touch the video's status. Leave it for retry
                # after the back-off expires. Back-off was already activated by
                # _record_youtube_result above.
                logger.warning(
                    f"TV resolve: rate-limit error for video {video_id} — "
                    f"leaving status unchanged for retry after back-off."
                )
            elif _is_cookie_stale_error(error_msg):
                # Stale cookie: leave status unchanged for retry after refresh.
                # The cookie-stale pause was already activated by
                # _record_youtube_result above.
                logger.warning(
                    f"TV resolve: stale-cookie error for video {video_id} — "
                    f"leaving status unchanged for retry after cookie refresh."
                )
            else:
                logger.warning(f"TV resolve: transient error for video {video_id}: {error_msg}")
            return None

        audio_url = result_dict.get("audio_url")
        logger.info(
            f"TV resolve: video {video_id} | height={result_dict.get('height')} | "
            f"split={'yes' if audio_url else 'no'} | provider={video.source_provider}"
        )

        stream_url = result_dict["stream_url"]

        # Vimeo CDN URLs require Referer: https://vimeo.com/ — generic players
        # like TiviMate cannot inject this header, so wrap through backend proxy.
        # (YouTube CDN URLs are signed tokens that work without special headers.)
        import urllib.parse as _urlparse
        if _is_vimeo_cdn_url(stream_url):
            proxy_base = f"http://localhost:{settings.app_port}/proxy/stream"
            stream_url = f"{proxy_base}?url={_urlparse.quote(stream_url, safe='')}"
            logger.info(f"TV resolve: Vimeo video stream wrapped through proxy for video {video_id}")
        if audio_url and _is_vimeo_cdn_url(audio_url):
            proxy_base = f"http://localhost:{settings.app_port}/proxy/stream"
            audio_url = f"{proxy_base}?url={_urlparse.quote(audio_url, safe='')}"
            logger.info(f"TV resolve: Vimeo audio stream wrapped through proxy for video {video_id}")

        # Cache both URLs in the DB so repeat plays within the TTL window are instant.
        # Use empty string "" as the sentinel for "resolved as combined stream" so we
        # can distinguish it from NULL meaning "never resolved via TV path".
        try:
            video.resolved_stream_url = stream_url
            video.resolved_audio_url = audio_url if audio_url is not None else ""
            video.resolved_format = result_dict.get("format_note") or video.resolved_format
            video.resolved_at = datetime.datetime.utcnow()
            video.resolution_status = "resolved"
            await self._db.commit()
            logger.info(
                f"TV resolve: cached split URLs for video {video_id} "
                f"(audio={'yes' if audio_url else 'combined'})"
            )
        except Exception as e:
            logger.warning(f"TV resolve: failed to cache URLs for video {video_id}: {e}")

        return {
            "id": video.id,
            "title": video.title,
            "artist": video.artist,
            "stream_url": stream_url,
            "audio_url": audio_url,
            "format": result_dict.get("format_note"),
            "source_url": video.source_url,
            "thumbnail_url": video.thumbnail_url,
        }

    async def _delete_video(self, video: Video) -> None:
        fav_stmt = select(Favorite).where(Favorite.video_id == video.id)
        fav_result = await self._db.execute(fav_stmt)
        fav = fav_result.scalar_one_or_none()
        if fav:
            await self._db.delete(fav)
        await self._db.delete(video)

    async def resolve_batch(self, limit: int = 10) -> dict:
        """
        Resolve a batch of PENDING videos via the standard resolver.

        Pending-only by design: the standard resolver earns its keep on new
        videos (thumbnail/duration backfill, permanent-failure marking,
        auto-dedup) but it only writes resolved_stream_url — it never touches
        resolved_audio_url. Letting it also REFRESH already-resolved videos
        (as it used to) overwrites the video URL while leaving a stale audio
        URL behind: a mismatched pair the TV cache check would happily serve
        to TiviMate. All URL refreshes now go through warm_tv_cache() /
        resolve_video_for_tv(), which always writes both URLs together.
        """
        stmt = (
            select(Video)
            .where(Video.resolution_status == "pending")
        )

        # When background YouTube resolve is OFF (the default), exclude
        # YouTube from the query itself so the batch slots fill with
        # Vimeo/local content. Without this, YouTube's ~12k pending videos
        # (higher scores) fill all 200 slots and get skipped in the loop,
        # leaving zero slots for Vimeo.
        if not is_youtube_background_resolve_enabled():
            stmt = stmt.where(
                ~Video.source_url.contains("youtube.com"),
                ~Video.source_url.contains("youtu.be"),
            )

        stmt = (
            stmt
            .order_by(Video.reddit_score.desc().nullslast())
            .limit(limit)
        )
        result = await self._db.execute(stmt)
        videos = result.scalars().all()

        all_videos = list(videos)
        summary = {"total": len(all_videos), "resolved": 0, "failed": 0, "deleted": 0, "skipped_backoff": 0, "skipped_cookie_stale": 0, "skipped_youtube_bg": 0}

        for video in all_videos:
            video_id = video.id
            is_yt = _is_youtube_source(video.source_url)

            # Background YouTube resolve switch: when disabled (the default),
            # skip YouTube videos entirely in this background pass — they stay
            # pending and resolve on demand when actually played. Cookie-saver.
            if is_yt and not is_youtube_background_resolve_enabled():
                summary["skipped_youtube_bg"] += 1
                continue

            # During YouTube back-off, skip YouTube videos without touching their
            # status — they stay pending and will be picked up after cooldown lifts.
            if is_yt and is_youtube_backed_off():
                summary["skipped_backoff"] += 1
                continue

            # During the cookie-stale pause, skip YouTube videos the same way —
            # they stay pending (not failed) until cookies are refreshed.
            if is_yt and is_cookie_stale_paused():
                summary["skipped_cookie_stale"] += 1
                continue

            result = await self.resolve_video(video_id, force=True)
            if result is not None:
                summary["resolved"] += 1
            else:
                check = await self._db.execute(select(Video).where(Video.id == video_id))
                if check.scalar_one_or_none() is None:
                    summary["deleted"] += 1
                else:
                    summary["failed"] += 1

            # Polite inter-request delay. YouTube needs a longer pause to avoid
            # re-triggering rate limits; Vimeo/other sources use a short pause.
            if is_yt:
                import random
                await asyncio.sleep(random.uniform(6.0, 10.0))
            else:
                await asyncio.sleep(1.0)

        logger.info(
            f"Batch resolve complete: {summary['resolved']} resolved, "
            f"{summary['failed']} failed, {summary['deleted']} deleted, "
            f"{summary['skipped_backoff']} skipped (back-off), "
            f"{summary['skipped_cookie_stale']} skipped (cookie-stale), "
            f"{summary['skipped_youtube_bg']} skipped (yt-bg-off) "
            f"out of {summary['total']}"
        )
        return summary

    async def warm_tv_cache(self, limit: int = 100) -> dict:
        """
        Background pre-warm of the TV-path URL cache (YouTube only).

        This is the fix for TiviMate's YouTube 502s: playback goes through
        resolve_video_for_tv(), whose cache is only warm when BOTH
        resolved_stream_url and resolved_audio_url are populated and fresh.
        YouTube extraction (with the Node.js JS-challenge solving) routinely
        takes 10-30+ seconds — far too slow to run at play time. This job runs
        it in the background instead, so by the time TiviMate presses play the
        answer is already sitting in the DB (an instant cache hit).

        Selects resolved videos that need warming:
          - resolved_audio_url IS NULL  -> never resolved via the TV path, OR
          - resolved_at older than the 3 h MP4 token TTL -> URLs going stale

        Deliberately excluded:
          - local_folder videos — no yt-dlp involved, served straight from disk
          - Vimeo — its HLS tokens die in ~20 minutes, so pre-warming is
            wasted work; Vimeo resolves fast at play time (no JS challenge)
            and is verified working with audio on hardware.

        Replaces the old resolve_expired() standard-path refresh, which
        overwrote resolved_stream_url while leaving resolved_audio_url stale —
        creating mismatched URL pairs the TV cache would then serve.
        """
        from sqlalchemy import or_

        expiry_cutoff = datetime.datetime.utcnow() - datetime.timedelta(hours=RESOLUTION_TTL_HOURS)
        stmt = (
            select(Video)
            .where(
                Video.resolution_status == "resolved",
                # NULL-safe provider exclusion: a plain != comparison silently
                # drops rows where source_provider is NULL (SQL three-valued
                # logic), and older rows may have no provider set.
                or_(
                    Video.source_provider.is_(None),
                    Video.source_provider.notin_(["local_folder", "vimeo"]),
                ),
                Video.source_url.isnot(None),
                ~Video.source_url.like("%vimeo.com%"),
                or_(
                    Video.resolved_audio_url.is_(None),
                    Video.resolved_at.is_(None),
                    Video.resolved_at < expiry_cutoff,
                ),
            )
            .order_by(Video.reddit_score.desc().nullslast())
            .limit(limit)
        )
        result = await self._db.execute(stmt)
        videos = result.scalars().all()

        summary = {"total": len(videos), "warmed": 0, "failed": 0, "deleted": 0, "skipped_backoff": 0, "skipped_cookie_stale": 0, "skipped_youtube_bg": 0}

        # When the cookie-stale pause is active, allow exactly ONE YouTube probe
        # per warm run so a freshly-refreshed cookie can self-heal: a successful
        # extraction auto-clears the pause (via _record_youtube_result) and the
        # rest of the loop then proceeds normally. If the probe still fails, the
        # pause is re-set and all remaining YouTube videos skip. This is what
        # makes "auto-clear on first successful YouTube resolve" work without a
        # manual button press, while never re-flooding YouTube during an outage.
        cookie_probe_used = False

        for video in videos:
            video_id = video.id
            is_yt = _is_youtube_source(video.source_url)

            # Background YouTube resolve switch: when disabled (the default),
            # skip YouTube videos entirely in the warm pass too. YouTube still
            # resolves on demand at play time via resolve_video_for_tv(); this
            # only stops speculative background warming. Checked first so it
            # also suppresses the cookie-stale probe (no background YouTube work
            # of any kind while the switch is off).
            if is_yt and not is_youtube_background_resolve_enabled():
                summary["skipped_youtube_bg"] += 1
                continue

            # During YouTube back-off, skip YouTube videos — they stay in their
            # current status and warm_tv_cache will pick them up after cooldown.
            if is_yt and is_youtube_backed_off():
                summary["skipped_backoff"] += 1
                continue

            # During the cookie-stale pause, skip YouTube videos — except allow
            # a single probe (the first one) so a refreshed cookie can clear it.
            probe_this = False
            if is_yt and is_cookie_stale_paused():
                if cookie_probe_used:
                    summary["skipped_cookie_stale"] += 1
                    continue
                cookie_probe_used = True
                probe_this = True
                logger.info(
                    f"TV cache warm: cookie-stale pause active — probing video "
                    f"{video_id} to test whether cookies have been refreshed."
                )

            result = await self.resolve_video_for_tv(video_id, allow_cookie_probe=probe_this)
            if result is not None:
                summary["warmed"] += 1
            else:
                check = await self._db.execute(select(Video).where(Video.id == video_id))
                if check.scalar_one_or_none() is None:
                    summary["deleted"] += 1
                else:
                    summary["failed"] += 1

            # Polite inter-request delay — YouTube needs a longer gap.
            if is_yt:
                import random
                await asyncio.sleep(random.uniform(6.0, 10.0))
            else:
                await asyncio.sleep(1.0)

        logger.info(
            f"TV cache warm complete: {summary['warmed']} warmed, "
            f"{summary['failed']} failed, {summary['deleted']} deleted, "
            f"{summary['skipped_backoff']} skipped (back-off), "
            f"{summary['skipped_cookie_stale']} skipped (cookie-stale), "
            f"{summary['skipped_youtube_bg']} skipped (yt-bg-off) "
            f"out of {summary['total']}"
        )
        return summary

    async def backfill_thumbnails(self, limit: int = 50, channel_ids: Optional[list] = None) -> dict:
        """Metadata-only yt-dlp pass to fill missing thumbnails, optionally scoped to channels.

        Excludes local_folder videos — yt-dlp cannot fetch thumbnails for local
        file paths. Use /library/generate-thumbnails for local content instead.

        Session 58 fixes:
        - NULL-provider trap: a plain != 'local_folder' comparison silently
          dropped rows where source_provider is NULL (SQL three-valued logic),
          so older scraped videos with no provider stamp were never backfilled.
          Now NULL-provider rows are explicitly included.
        - Outcome-aware marking: only genuinely thumbnail-less or permanently
          dead videos get the terminal 'unavailable' stamp. Transient failures
          (rate limits, 403 blocks, timeouts) leave thumbnail_url NULL so the
          video is retried on a later pass instead of being poisoned forever.
        """
        from sqlalchemy import or_

        stmt = (
            select(Video)
            .where(
                or_(Video.thumbnail_url.is_(None), Video.thumbnail_url == ""),
                or_(
                    Video.source_provider.is_(None),
                    Video.source_provider != "local_folder",
                ),
            )
            .order_by(Video.id.asc())
            .limit(limit)
        )
        if channel_ids:
            stmt = (
                select(Video)
                .where(
                    or_(Video.thumbnail_url.is_(None), Video.thumbnail_url == ""),
                    or_(
                        Video.source_provider.is_(None),
                        Video.source_provider != "local_folder",
                    ),
                    Video.channel_id.in_(channel_ids),
                )
                .order_by(Video.id.asc())
                .limit(limit)
            )
        result = await self._db.execute(stmt)
        videos = result.scalars().all()

        summary = {"total": len(videos), "filled": 0, "skipped": 0, "failed": 0, "deferred": 0}

        loop = asyncio.get_event_loop()
        for video in videos:
            try:
                thumbnail_url, outcome = await asyncio.wait_for(
                    loop.run_in_executor(
                        _process_pool,
                        _fetch_thumbnail_sync_worker,
                        video.source_url,
                        self._cookies_path,
                    ),
                    timeout=60.0,
                )
                if outcome == "ok" and thumbnail_url:
                    video.thumbnail_url = thumbnail_url
                    summary["filled"] += 1
                    logger.info(f"Backfill thumbnail: video {video.id} -> {thumbnail_url[:80]}")
                elif outcome in ("no_thumb", "permanent"):
                    # Terminal: the video has no thumbnail or is dead. Mark it
                    # so it doesn't clog the queue on the next run. The catalog
                    # treats this the same as no thumbnail.
                    video.thumbnail_url = "unavailable"
                    summary["skipped"] += 1
                    logger.info(
                        f"Backfill thumbnail: video {video.id} marked unavailable ({outcome})"
                    )
                else:
                    # Transient (rate limit / network / block): leave NULL so a
                    # later pass retries. Do NOT stamp 'unavailable'.
                    summary["deferred"] += 1
                    logger.info(
                        f"Backfill thumbnail: video {video.id} deferred (transient error)"
                    )
            except asyncio.TimeoutError:
                summary["failed"] += 1
                logger.warning(f"Backfill thumbnail: timed out for video {video.id}")
            except Exception as e:
                summary["failed"] += 1
                logger.warning(f"Backfill thumbnail: failed for video {video.id}: {e}")

            await asyncio.sleep(0.5)

        await self._db.commit()
        scope = f"channels {channel_ids}" if channel_ids else "all channels"
        logger.info(
            f"Thumbnail backfill complete ({scope}): {summary['filled']} filled, "
            f"{summary['skipped']} skipped, {summary['deferred']} deferred, "
            f"{summary['failed']} failed out of {summary['total']}"
        )
        return summary

    async def upgrade_low_quality(
        self,
        min_height: int = 720,
        chunk_size: int = 25,
        chunk_offset: int = 0,
        channel_ids: Optional[list] = None,
    ) -> dict:
        """
        Quality upgrade pass — re-resolves videos that are confirmed low-quality
        and replaces their stream URL only if yt-dlp returns a higher resolution.

        Strategy:
        - Selects `chunk_size` resolved videos whose stored resolved_format
          indicates a height below `min_height` (e.g. 480p, 360p, 240p).
        - `chunk_offset` allows the scheduler to walk through the full set
          across multiple ticks without repeating the same videos every time.
        - Re-runs yt-dlp with the existing FORMAT_SELECTOR (prefers 1080p MP4).
        - Compares returned height against stored height:
            - Higher → replace stream URL and format note, log upgrade.
            - Same or lower → leave untouched, log skip.
        - Never deletes a video. On any error, skips to the next one.
        - Commits after each successful upgrade so partial progress is saved.

        Returns a summary dict for logging.
        """
        import re as _re

        def _parse_height(format_note: Optional[str]) -> Optional[int]:
            """Extract numeric height from format_note like 'mp4/480p' or '480p'."""
            if not format_note:
                return None
            m = _re.search(r'(\d+)p', format_note)
            return int(m.group(1)) if m else None

        # Select resolved videos whose format note suggests below min_height.
        # We filter in Python after the DB fetch since format parsing needs regex.
        stmt = (
            select(Video)
            .where(
                Video.resolution_status == "resolved",
                Video.resolved_format.isnot(None),
            )
            .order_by(Video.reddit_score.desc().nullslast())
            .offset(chunk_offset)
            .limit(chunk_size * 4)  # over-fetch so we have enough after height filter
        )
        if channel_ids:
            stmt = stmt.where(Video.channel_id.in_(channel_ids))
        result = await self._db.execute(stmt)
        candidates = result.scalars().all()

        # Filter to those actually below the quality threshold
        low_quality = [
            v for v in candidates
            if (_parse_height(v.resolved_format) or 9999) < min_height
        ][:chunk_size]

        summary = {
            "checked": len(low_quality),
            "upgraded": 0,
            "same_or_lower": 0,
            "errored": 0,
            "skipped_permanent": 0,
        }

        for video in low_quality:
            old_height = _parse_height(video.resolved_format)
            try:
                logger.info(
                    f"Quality upgrade check: video {video.id} "
                    f"'{(video.title or '')[:50]}' current={video.resolved_format}"
                )
                stream_info, error_msg, is_permanent = await self._extract_with_ytdlp(
                    video.source_url
                )

                if stream_info is None:
                    if is_permanent:
                        logger.info(
                            f"Quality upgrade: video {video.id} is permanently gone — skipping."
                        )
                        summary["skipped_permanent"] += 1
                    else:
                        logger.warning(
                            f"Quality upgrade: transient error for video {video.id}: {error_msg}"
                        )
                        summary["errored"] += 1
                    await asyncio.sleep(1.0)
                    continue

                new_height = stream_info.height or _parse_height(stream_info.format_note)

                if new_height and old_height and new_height > old_height:
                    logger.info(
                        f"Quality upgrade: video {video.id} "
                        f"{old_height}p → {new_height}p ({stream_info.format_note})"
                    )
                    video.resolved_stream_url = stream_info.stream_url
                    video.resolved_format = stream_info.format_note
                    video.resolved_at = datetime.datetime.utcnow()
                    await self._db.commit()
                    summary["upgraded"] += 1
                else:
                    logger.info(
                        f"Quality upgrade: video {video.id} no improvement "
                        f"(current={old_height}p new={new_height}p) — leaving unchanged."
                    )
                    summary["same_or_lower"] += 1

            except Exception as e:
                logger.warning(
                    f"Quality upgrade: unexpected error for video {video.id}: {e} — skipping."
                )
                summary["errored"] += 1

            await asyncio.sleep(1.0)

        logger.info(
            f"Quality upgrade complete: {summary['upgraded']} upgraded, "
            f"{summary['same_or_lower']} unchanged, {summary['errored']} errored, "
            f"{summary['skipped_permanent']} permanent-gone "
            f"out of {summary['checked']} checked"
        )
        return summary

    async def purge_dash_videos(self, channel_ids: Optional[list] = None) -> int:
        """Delete all videos whose resolved stream URL is a DASH manifest, optionally scoped to channels."""
        stmt = select(Video).where(Video.resolution_status == "resolved")
        if channel_ids:
            stmt = stmt.where(Video.channel_id.in_(channel_ids))
        result = await self._db.execute(stmt)
        resolved_videos = result.scalars().all()

        dash_videos = [v for v in resolved_videos if _is_dash_url(v.resolved_stream_url or "")]
        count = len(dash_videos)

        for video in dash_videos:
            logger.info(f"Purging DASH-only video {video.id}: {video.title[:60]}")
            await self._delete_video(video)

        await self._db.commit()
        scope = f"channels {channel_ids}" if channel_ids else "all channels"
        logger.info(f"Purged {count} DASH-only videos from database ({scope})")
        return count

    async def purge_dead_videos(self, channel_ids: Optional[list] = None) -> int:
        """Delete all videos currently marked as failed, optionally scoped to channels."""
        stmt = select(Video).where(Video.resolution_status == "failed")
        if channel_ids:
            stmt = stmt.where(Video.channel_id.in_(channel_ids))
        result = await self._db.execute(stmt)
        dead_videos = result.scalars().all()

        count = len(dead_videos)
        for video in dead_videos:
            await self._delete_video(video)

        await self._db.commit()
        scope = f"channels {channel_ids}" if channel_ids else "all channels"
        logger.info(f"Purged {count} dead videos from database ({scope})")
        return count

    async def _extract_with_ytdlp(
        self, url: str
    ) -> Tuple[Optional[StreamInfo], Optional[str], bool]:
        """
        Run yt-dlp extraction in a subprocess with a hard wall-clock timeout.

        Uses ProcessPoolExecutor so the yt-dlp process can be forcibly killed
        if it exceeds YTDLP_TIMEOUT_SECONDS. ThreadPoolExecutor cannot kill
        a hung thread, which is why we use processes here.

        Returns (StreamInfo | None, error_msg | None, is_permanent: bool).
        """
        loop = asyncio.get_event_loop()
        try:
            result_dict, error_msg, is_permanent = await asyncio.wait_for(
                loop.run_in_executor(
                    _process_pool,
                    _extract_sync_worker,
                    url,
                    self._cookies_path,
                ),
                timeout=float(YTDLP_TIMEOUT_SECONDS),
            )
        except asyncio.TimeoutError:
            logger.error(
                f"yt-dlp TIMED OUT after {YTDLP_TIMEOUT_SECONDS}s for {url} — "
                "marking as transient failure. The hung subprocess has been abandoned."
            )
            return None, f"yt-dlp timed out after {YTDLP_TIMEOUT_SECONDS}s", False
        except Exception as e:
            logger.error(f"yt-dlp extraction error for {url}: {e}")
            return None, f"yt-dlp extraction error: {e}", False

        if result_dict is None:
            return None, error_msg, is_permanent

        stream_info = StreamInfo.from_dict(result_dict)

        # Log what yt-dlp picked
        is_hls  = _is_hls_url(stream_info.stream_url)
        is_dash = _is_dash_url(stream_info.stream_url)
        logger.info(
            f"yt-dlp selected | ext={result_dict.get('ext')} | "
            f"protocol={result_dict.get('protocol')} | "
            f"hls={is_hls} | dash={is_dash} | "
            f"url_preview={stream_info.stream_url[:80]}"
        )

        if is_dash:
            logger.warning(
                f"DASH URL slipped through format selector for {url} — "
                f"browser cannot play this."
            )

        if not self._cookies_path or not os.path.isfile(self._cookies_path):
            logger.warning(
                "No cookies.txt found — YouTube age-restricted content will fail. "
                f"Expected at: {self._cookies_path}"
            )

        return stream_info, None, False

    def _is_cache_valid(self, video: Video) -> bool:
        if video.resolution_status != "resolved":
            return False
        if not video.resolved_stream_url:
            return False
        if not video.resolved_at:
            return False

        age_seconds = (datetime.datetime.utcnow() - video.resolved_at).total_seconds()

        if _is_adaptive_url(video.resolved_stream_url):
            ttl_seconds = ADAPTIVE_TTL_MINUTES * 60
            valid = age_seconds < ttl_seconds
            url_type = "HLS" if _is_hls_url(video.resolved_stream_url) else "DASH"
            logger.debug(
                f"{url_type} cache check: age={int(age_seconds)}s "
                f"ttl={ttl_seconds}s valid={valid}"
            )
            return valid
        else:
            return age_seconds < (RESOLUTION_TTL_HOURS * 3600)
