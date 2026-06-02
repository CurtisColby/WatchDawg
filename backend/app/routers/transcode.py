"""
WatchDawg On-Demand Transcode Router.

Endpoint:
- GET /transcode/{video_id} — Stream a video at full quality by downloading
  and muxing its split audio+video streams on the fly using FFmpeg + QuickSync.

How it works:
1. yt-dlp extracts the two raw stream URLs (best video + best audio) for the
   source URL without downloading anything.
2. FFmpeg is spawned with those two URLs as inputs, muxing them in real time
   and writing MP4 output to stdout.
3. The MP4 output is streamed chunk-by-chunk to ExoPlayer via a chunked HTTP
   StreamingResponse — playback starts after a few seconds of buffer.
4. When the client disconnects, the FFmpeg process is killed cleanly.

Hardware acceleration:
- Primary: h264_qsv (Intel QuickSync) — uses /dev/dri/renderD128.
  Requires LIBVA_DRIVER_NAME=iHD and LIBVA_DRIVERS_PATH set in environment.
- Fallback: libx264 software encode — used if QuickSync init fails.
  Slower but always works.

Quality:
- Video: best available from YouTube (1080p, 1440p, 2160p if offered)
- Audio: best available (opus/m4a/aac), re-encoded to aac for MP4 compatibility
- Container: fragmented MP4 (frag_keyframe+empty_moov) for HTTP streaming

Seeking:
- On-demand transcode starts from the beginning and streams forward.
  Seeking backward or jumping ahead requires FFmpeg to restart — ExoPlayer
  handles this gracefully but there will be a brief re-buffer.
- Favorited videos that have been downloaded play from the local file instead
  and support instant seeking with no CPU cost.
"""

import asyncio
import logging
import os
import shutil
import subprocess
from typing import AsyncGenerator, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.models import Video, Favorite
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/transcode", tags=["transcode"])

# Chunk size for streaming FFmpeg output to the client.
# 256KB balances memory use vs. number of yields per second at typical bitrates.
STREAM_CHUNK_BYTES = 256 * 1024

# Format selector for yt-dlp URL extraction (not downloading).
# We want the two best raw URLs — video and audio — without any height cap.
# yt-dlp returns these as requested_formats[0] (video) and requested_formats[1] (audio).
TRANSCODE_FORMAT_SELECTOR = (
    "bestvideo[vcodec^=avc1]+bestaudio/"
    "bestvideo[vcodec^=vp9]+bestaudio/"
    "bestvideo+bestaudio/"
    "best"
)


def _get_raw_stream_urls(source_url: str, cookies_path: Optional[str]) -> tuple[str, Optional[str], int]:
    """
    Use yt-dlp (synchronous) to extract raw video and audio URLs without downloading.

    Returns: (video_url, audio_url_or_None, height)

    When yt-dlp selects a combined stream (no split), audio_url is None
    and FFmpeg treats video_url as a single-input mux.

    Runs synchronously — call via asyncio.to_thread() from async context.
    """
    import yt_dlp

    ydl_opts = {
        "format": TRANSCODE_FORMAT_SELECTOR,
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "simulate": True,
        "socket_timeout": 30,
        "retries": 3,
    }

    if cookies_path and os.path.isfile(cookies_path):
        ydl_opts["cookiefile"] = cookies_path

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(source_url, download=False)

    if info is None:
        raise RuntimeError("yt-dlp returned no info")

    requested = info.get("requested_formats", [])
    height = info.get("height") or 0

    if len(requested) >= 2:
        # Split stream — video and audio are separate
        video_fmt = requested[0]
        audio_fmt = requested[1]
        video_url = video_fmt.get("url")
        audio_url = audio_fmt.get("url")
        height = video_fmt.get("height") or height
        logger.info(
            f"Transcode: split stream | video={video_fmt.get('vcodec')} "
            f"{height}p | audio={audio_fmt.get('acodec')}"
        )
    else:
        # Combined stream — single URL covers both tracks
        video_url = info.get("url") or (requested[0].get("url") if requested else None)
        audio_url = None
        logger.info(f"Transcode: combined stream | {height}p")

    if not video_url:
        raise RuntimeError("No video URL found in yt-dlp output")

    return video_url, audio_url, height


def _build_ffmpeg_cmd_qsv(video_url: str, audio_url: Optional[str]) -> list[str]:
    """
    Build FFmpeg command using Intel VA-API (h264_vaapi) hardware acceleration.

    The pipeline:
      1. Init the VA-API device from /dev/dri/renderD128
      2. Download video stream from CDN URL
      3. Convert decoded frames to nv12 pixel format (required by VA-API encoder)
      4. Upload frames to GPU memory via hwupload filter
      5. Encode with h264_vaapi — GPU does the heavy lifting
      6. Mux with audio into fragmented MP4 streamed to stdout

    This is the exact pipeline confirmed working by the vaapi test:
      ffmpeg -init_hw_device vaapi=va:/dev/dri/renderD128
             -vf 'format=nv12,hwupload' -c:v h264_vaapi
    """
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning"]

    # Initialize the VA-API hardware device
    cmd += ["-init_hw_device", "vaapi=va:/dev/dri/renderD128"]

    # Video input — FFmpeg downloads directly from the CDN URL
    cmd += ["-i", video_url]

    # Audio input (separate stream for split YouTube tracks)
    if audio_url:
        cmd += ["-i", audio_url]

    # Video filter: convert to nv12 then upload to GPU memory
    # This is required — h264_vaapi cannot encode from software pixel formats directly
    cmd += ["-vf", "format=nv12,hwupload=extra_hw_frames=64,format=vaapi"]

    # Hardware encode with h264_vaapi
    # qp=20 is high quality for permanent streaming (lower = better, 18-24 is the sweet spot)
    cmd += [
        "-c:v", "h264_vaapi",
        "-qp", "20",
        "-profile:v", "high",
    ]

    # Audio: re-encode to AAC for maximum MP4/ExoPlayer compatibility
    cmd += [
        "-c:a", "aac",
        "-b:a", "192k",
    ]

    # Map streams explicitly
    cmd += ["-map", "0:v:0"]
    if audio_url:
        cmd += ["-map", "1:a:0"]
    else:
        cmd += ["-map", "0:a:0"]

    # Output: fragmented MP4 to stdout for HTTP streaming
    # frag_keyframe: new fragment at each keyframe — enables progressive playback
    # empty_moov: moov atom at start — required for streaming without a seekable file
    cmd += [
        "-movflags", "frag_keyframe+empty_moov+faststart",
        "-f", "mp4",
        "pipe:1",
    ]

    return cmd


def _build_ffmpeg_cmd_software(video_url: str, audio_url: Optional[str]) -> list[str]:
    """
    Build FFmpeg command using software encode (libx264) as QuickSync fallback.
    Slower but always works regardless of GPU availability.
    """
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning"]

    cmd += ["-i", video_url]
    if audio_url:
        cmd += ["-i", audio_url]

    cmd += [
        "-c:v", "libx264",
        "-preset", "veryfast",  # Fast software encode, acceptable quality
        "-crf", "23",           # Constant rate factor — quality target
    ]

    cmd += [
        "-c:a", "aac",
        "-b:a", "192k",
    ]

    cmd += ["-map", "0:v:0"]
    if audio_url:
        cmd += ["-map", "1:a:0"]
    else:
        cmd += ["-map", "0:a:0"]

    cmd += [
        "-movflags", "frag_keyframe+empty_moov+faststart",
        "-f", "mp4",
        "pipe:1",
    ]

    return cmd


async def _stream_ffmpeg(
    cmd: list[str],
    request: Request,
) -> AsyncGenerator[bytes, None]:
    """
    Spawn FFmpeg and yield its stdout as chunks for StreamingResponse.

    Monitors the client disconnect signal and kills FFmpeg cleanly when
    the TV stops playback, skips to next video, or the app is closed.
    """
    logger.info(f"Transcode: spawning FFmpeg | cmd preview: {' '.join(cmd[:8])}...")

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        while True:
            # Check if client has disconnected
            if await request.is_disconnected():
                logger.info("Transcode: client disconnected — killing FFmpeg")
                proc.kill()
                break

            chunk = await proc.stdout.read(STREAM_CHUNK_BYTES)
            if not chunk:
                # FFmpeg finished or errored
                break
            yield chunk

    except asyncio.CancelledError:
        logger.info("Transcode: stream cancelled — killing FFmpeg")
        proc.kill()
        raise
    finally:
        # Always clean up the process
        try:
            proc.kill()
        except ProcessLookupError:
            pass  # Already exited
        await proc.wait()

        # Log any FFmpeg stderr for debugging
        try:
            stderr_data = await asyncio.wait_for(proc.stderr.read(), timeout=2.0)
            if stderr_data:
                stderr_text = stderr_data.decode(errors="replace").strip()
                if stderr_text:
                    logger.debug(f"Transcode FFmpeg stderr: {stderr_text[:500]}")
        except asyncio.TimeoutError:
            pass


@router.get("/{video_id}")
async def transcode_video(
    video_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Stream a video at full quality via on-demand FFmpeg transcode.

    Called by the Android TV app when no local Favorite file exists.
    ExoPlayer connects and begins buffering; FFmpeg downloads the best
    available video+audio streams and muxes them in real time using
    Intel QuickSync hardware acceleration.

    Falls back to software encoding (libx264) if QuickSync is unavailable.
    """
    # Fetch the video record
    stmt = select(Video).where(Video.id == video_id)
    result = await db.execute(stmt)
    video = result.scalar_one_or_none()

    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")

    # Check if a completed local Favorite file exists — serve that instead
    fav_stmt = select(Favorite).where(
        Favorite.video_id == video_id,
        Favorite.download_status == "complete",
    )
    fav_result = await db.execute(fav_stmt)
    favorite = fav_result.scalar_one_or_none()

    if favorite and favorite.local_file_path and os.path.isfile(favorite.local_file_path):
        # Redirect to library stream — no transcoding needed
        import urllib.parse
        music_dir = settings.music_videos_path
        rel = os.path.relpath(favorite.local_file_path, music_dir)
        stream_path = f"/library/stream/{urllib.parse.quote(rel, safe='/')}"
        logger.info(
            f"Transcode: video {video_id} has local file — "
            f"redirecting to {stream_path}"
        )
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=stream_path)

    logger.info(
        f"Transcode: starting on-demand transcode for video {video_id} "
        f"'{(video.title or '')[:60]}'"
    )

    # Extract raw stream URLs via yt-dlp (runs in thread — blocking call)
    try:
        video_url, audio_url, height = await asyncio.to_thread(
            _get_raw_stream_urls,
            video.source_url,
            settings.ytdlp_cookies_path,
        )
    except Exception as e:
        logger.error(f"Transcode: yt-dlp extraction failed for video {video_id}: {e}")
        raise HTTPException(
            status_code=502,
            detail=f"Could not extract stream URLs: {e}",
        )

    logger.info(
        f"Transcode: extracted URLs for video {video_id} | "
        f"height={height} | split={'yes' if audio_url else 'no'}"
    )

    # Try QuickSync first, fall back to software
    qsv_available = (
        os.path.exists("/dev/dri/renderD128")
        and shutil.which("ffmpeg") is not None
    )

    if qsv_available:
        cmd = _build_ffmpeg_cmd_qsv(video_url, audio_url)
        logger.info(f"Transcode: using QuickSync (h264_qsv) for video {video_id}")
    else:
        cmd = _build_ffmpeg_cmd_software(video_url, audio_url)
        logger.info(f"Transcode: using software encode (libx264) for video {video_id}")

    return StreamingResponse(
        _stream_ffmpeg(cmd, request),
        media_type="video/mp4",
        headers={
            # Tell ExoPlayer this is a streaming response with no known length
            "Cache-Control": "no-cache",
            "X-Transcode-Height": str(height),
            "X-Transcode-VideoId": str(video_id),
        },
    )
