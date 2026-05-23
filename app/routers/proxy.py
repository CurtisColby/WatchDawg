"""
WatchDawg Stream Proxy Router.

Proxies video stream URLs through the backend to bypass browser CORS
restrictions on third-party CDN domains (e.g. vimeocdn.com).

For HLS streams (m3u8), rewrites the manifest so all segment URLs
point back through this proxy — hls.js then fetches every segment
via localhost, never hitting the CDN directly from the browser.

Supports HTTP Range requests for direct MP4 seek/scrub support.

Also exposes /debug/logs — returns the last N log entries captured
by the in-memory ring buffer so the browser UI can display them
without the user needing SSH access.

KEY FIX: The httpx AsyncClient must NOT be used as a context manager
(`async with`) around a StreamingResponse. The `async with` block exits
and closes the client the moment the function returns the StreamingResponse
object — before FastAPI has streamed a single byte. The fix is to create
the client without `async with`, pass it into the stream generator, and
close both the response and the client in the generator's `finally` block.
This keeps the upstream connection alive for the full duration of streaming.

Endpoints:
- GET /proxy/stream?url=<encoded_url>  — Proxy any stream URL through backend.
- GET /debug/logs?n=200               — Last N in-memory log lines (JSON).
"""

import collections
import logging
import re
import urllib.parse
from datetime import datetime

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["proxy"])

# ---------------------------------------------------------------------------
# In-memory ring buffer — stores last LOG_BUFFER_SIZE log records.
# LogBufferHandler appends to this deque; /debug/logs reads from it.
# ---------------------------------------------------------------------------
LOG_BUFFER_SIZE = 1200
_log_buffer: collections.deque = collections.deque(maxlen=LOG_BUFFER_SIZE)


class LogBufferHandler(logging.Handler):
    """Logging handler that appends records to the global deque."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            _log_buffer.append(
                {
                    "ts": datetime.utcnow().strftime("%H:%M:%S.%f")[:-3],
                    "level": record.levelname,
                    "name": record.name,
                    "msg": self.format(record),
                }
            )
        except Exception:
            pass


def install_log_buffer() -> None:
    """
    Attach LogBufferHandler to the root logger.
    Call once at startup (from main.py lifespan) so ALL loggers feed the
    buffer, not just the proxy logger.
    """
    handler = LogBufferHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    root = logging.getLogger()
    if not any(isinstance(h, LogBufferHandler) for h in root.handlers):
        root.addHandler(handler)


# ---------------------------------------------------------------------------
# Headers to forward from CDN response to client
# ---------------------------------------------------------------------------
FORWARD_RESPONSE_HEADERS = {
    "content-type",
    "content-length",
    "content-range",
    "accept-ranges",
    "cache-control",
    "etag",
    "last-modified",
}

PROXY_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _build_upstream_headers(url: str, range_header: str = None) -> dict:
    """Build headers for the upstream CDN request."""
    headers = {
        "User-Agent": PROXY_USER_AGENT,
        "Accept": "*/*",
        "Accept-Encoding": "identity",
        "Connection": "keep-alive",
    }
    if range_header:
        headers["Range"] = range_header
    if "vimeocdn.com" in url or "vimeo.com" in url:
        headers["Referer"] = "https://vimeo.com/"
        headers["Origin"] = "https://vimeo.com"
    return headers


def _rewrite_m3u8(manifest_text: str, base_url: str, proxy_base: str) -> str:
    """
    Rewrite an m3u8 manifest so every segment/sub-manifest URL
    points back through our proxy instead of directly to the CDN.

    Handles:
    - #EXT-X-KEY:   encryption key URIs
    - #EXT-X-MAP:   MP4 init segment URI — hls.js fetches this FIRST before
                    any media segments; missing rewrite causes immediate 404
                    and kills the stream before a single frame plays.
    - segment lines: bare URLs on non-comment lines

    base_url:   the original CDN URL of this manifest (for resolving relatives)
    proxy_base: our proxy endpoint, e.g. "http://localhost:6868/proxy/stream"
    """
    parsed = urllib.parse.urlparse(base_url)
    cdn_base = f"{parsed.scheme}://{parsed.netloc}"
    path_dir = parsed.path.rsplit("/", 1)[0] if "/" in parsed.path else ""
    manifest_dir_url = f"{cdn_base}{path_dir}"

    lines = manifest_text.splitlines()
    rewritten = []

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("#EXT-X-KEY") or stripped.startswith("#EXT-X-MAP"):
            # Rewrite URI= inside key tags AND map/init-segment tags.
            # #EXT-X-MAP carries the MP4 init segment that hls.js fetches FIRST
            # before any media segments. If left as a relative URL, hls.js
            # resolves it against localhost and gets a 404, killing the stream
            # before a single frame is decoded.
            def rewrite_tag_uri(m):
                uri = m.group(1)
                abs_uri = _make_absolute(uri, cdn_base, manifest_dir_url)
                proxied = f"{proxy_base}?url={urllib.parse.quote(abs_uri, safe='')}"
                return f'URI="{proxied}"'
            line = re.sub(r'URI="([^"]+)"', rewrite_tag_uri, line)
            rewritten.append(line)

        elif stripped.startswith("#"):
            rewritten.append(line)

        elif stripped == "":
            rewritten.append(line)

        else:
            # Segment or sub-playlist URL line
            abs_url = _make_absolute(stripped, cdn_base, manifest_dir_url)
            proxied_url = f"{proxy_base}?url={urllib.parse.quote(abs_url, safe='')}"
            rewritten.append(proxied_url)

    return "\n".join(rewritten)


def _make_absolute(url: str, cdn_base: str, manifest_dir_url: str) -> str:
    """Convert a relative URL to absolute using the manifest's base URL."""
    if url.startswith("http://") or url.startswith("https://"):
        return url
    elif url.startswith("/"):
        return cdn_base + url
    else:
        return manifest_dir_url + "/" + url


# ---------------------------------------------------------------------------
# /debug/logs
# ---------------------------------------------------------------------------
@router.get("/debug/logs")
async def get_debug_logs(
    n: int = Query(200, ge=1, le=LOG_BUFFER_SIZE, description="Number of recent log lines to return"),
):
    """Return the last N log entries from the in-memory ring buffer."""
    entries = list(_log_buffer)
    return JSONResponse({"logs": entries[-n:]})


# ---------------------------------------------------------------------------
# /proxy/stream
# ---------------------------------------------------------------------------
@router.get("/proxy/stream")
async def proxy_stream(
    request: Request,
    url: str = Query(..., description="The stream URL to proxy"),
):
    """
    Proxy a video stream URL through the backend.

    CRITICAL — why we do NOT use `async with httpx.AsyncClient`:
    FastAPI's StreamingResponse is lazy — it streams bytes AFTER the
    endpoint coroutine returns. If the client is created with `async with`,
    the context manager exits and closes the upstream connection the instant
    this coroutine returns the StreamingResponse — before a single byte has
    been sent to the browser. This produces:
        ReadError(ClosedResourceError())
    on every segment fetch, which is exactly what we saw in the debug logs.

    The fix: create the client without a context manager, capture it in the
    stream_generator closure, and close it in the generator's `finally` block.
    The generator runs inside FastAPI's streaming machinery, so `finally`
    fires only after all bytes have been sent (or the client disconnects).
    """
    logger.info(f"PROXY HIT | method={request.method} | url={url}")

    if not url.startswith("http"):
        logger.warning(f"PROXY REJECTED — bad URL scheme: {url[:120]}")
        raise HTTPException(
            status_code=400,
            detail="Invalid stream URL — must start with http(s)://",
        )

    range_header = request.headers.get("Range")
    upstream_headers = _build_upstream_headers(url, range_header)

    logger.info(
        f"PROXY UPSTREAM REQUEST | range={range_header or 'none'} | "
        f"referer={'vimeo' if 'vimeocdn' in url or 'vimeo.com' in url else 'none'}"
    )

    # Create client WITHOUT async with — closed manually in generator finally.
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, read=300.0),
        follow_redirects=True,
    )

    try:
        req = httpx.Request("GET", url, headers=upstream_headers)
        upstream_response = await client.send(req, stream=True)

        status_code = upstream_response.status_code
        content_type = upstream_response.headers.get("content-type", "")
        final_url = str(upstream_response.url)

        logger.info(
            f"PROXY UPSTREAM RESPONSE | status={status_code} | "
            f"content-type={content_type!r} | "
            f"final-url={final_url}"
        )

        if status_code not in (200, 206):
            await upstream_response.aclose()
            await client.aclose()
            logger.warning(f"PROXY UPSTREAM ERROR | status={status_code} | url={url}")
            raise HTTPException(
                status_code=502,
                detail=f"Upstream CDN returned HTTP {status_code} for URL: {url[:200]}",
            )

        # --- HLS manifest rewriting ---
        is_m3u8 = (
            "mpegurl" in content_type.lower()
            or final_url.split("?")[0].endswith(".m3u8")
            or url.split("?")[0].endswith(".m3u8")
        )

        if is_m3u8:
            # Manifests are small text — read fully, then close client immediately.
            manifest_bytes = await upstream_response.aread()
            await upstream_response.aclose()
            await client.aclose()

            manifest_text = manifest_bytes.decode("utf-8", errors="replace")
            proxy_base = str(request.base_url).rstrip("/") + "/proxy/stream"
            rewritten = _rewrite_m3u8(manifest_text, final_url, proxy_base)

            preview = "\n".join(rewritten.splitlines()[:10])
            logger.info(
                f"PROXY M3U8 REWRITE | original={len(manifest_text)}B | "
                f"rewritten={len(rewritten)}B | base={final_url}\n"
                f"--- MANIFEST PREVIEW ---\n{preview}\n--- END PREVIEW ---"
            )

            return PlainTextResponse(
                content=rewritten,
                media_type="application/vnd.apple.mpegurl",
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Cache-Control": "no-cache",
                },
            )

        # --- Direct binary stream (MP4 init segment, TS chunk, etc.) ---
        response_headers = {}
        for key, value in upstream_response.headers.items():
            if key.lower() in FORWARD_RESPONSE_HEADERS:
                response_headers[key] = value

        response_headers["Accept-Ranges"] = "bytes"
        response_headers["Access-Control-Allow-Origin"] = "*"

        content_len = upstream_response.headers.get("content-length", "unknown")
        logger.info(
            f"PROXY STREAMING | type={content_type!r} | "
            f"size={content_len} | range={range_header or 'full'}"
        )

        # Both client and upstream_response are captured in this closure.
        # `finally` runs after the last byte is sent OR on any disconnect.
        async def stream_generator():
            try:
                async for chunk in upstream_response.aiter_bytes(chunk_size=65536):
                    yield chunk
            except Exception as e:
                logger.warning(f"PROXY STREAM interrupted: {e}")
            finally:
                await upstream_response.aclose()
                await client.aclose()
                logger.debug("PROXY STREAM DONE — upstream connection closed")

        return StreamingResponse(
            stream_generator(),
            status_code=status_code,
            headers=response_headers,
            media_type=content_type or "video/mp4",
        )

    except HTTPException:
        raise
    except httpx.TimeoutException:
        await client.aclose()
        logger.error(f"PROXY TIMEOUT | url={url}")
        raise HTTPException(status_code=504, detail="Upstream CDN timed out")
    except httpx.RequestError as e:
        await client.aclose()
        logger.error(f"PROXY REQUEST ERROR | {e} | url={url}")
        raise HTTPException(status_code=502, detail=f"Upstream CDN error: {e}")
    except Exception as e:
        await client.aclose()
        logger.error(f"PROXY UNEXPECTED ERROR | {e} | url={url}")
        raise HTTPException(status_code=500, detail=f"Proxy internal error: {e}")
