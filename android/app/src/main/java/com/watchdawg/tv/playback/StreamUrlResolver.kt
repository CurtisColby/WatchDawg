package com.watchdawg.tv.playback

import android.net.Uri
import java.net.URLEncoder

class StreamUrlResolver(private val baseUrlProvider: () -> String) {

    enum class StreamType { HLS, DASH, YOUTUBE_CDN, VIMEO_CDN, MP4, LOCAL, TRANSCODE, DIRECT_HLS }

    data class Playable(
        val uri: Uri,
        val type: StreamType,
        val viaProxy: Boolean,
    )

    fun classify(streamUrl: String): StreamType {
        val u = streamUrl.lowercase()
        return when {
            streamUrl.startsWith("/transcode/") -> StreamType.TRANSCODE
            streamUrl.startsWith("/") -> StreamType.LOCAL
            // Any absolute HTTP/HTTPS URL that isn't a known CDN or DASH stream
            // is played directly via DIRECT_HLS (HlsMediaSource handles HLS,
            // ProgressiveMediaSource handles MP4/TS). This covers all Tunarr stream
            // modes (hls, hls_direct_v2, hls_alt, mpeg-ts) without needing to
            // enumerate every possible streamMode= value.
            u.contains(".mpd") || u.contains("playlist.mpd") || u.contains("/playlist/av/primary") -> StreamType.DASH
            u.contains("googlevideo.com") -> StreamType.YOUTUBE_CDN
            u.contains("vimeocdn.com") || u.contains("vod-progressive.akamaized.net") ||
            u.contains("vod.akamaized.net") || u.contains("skyfire.vimeo.com") ||
            u.contains("av.vimeo.com") -> StreamType.VIMEO_CDN
            u.startsWith("http://") || u.startsWith("https://") -> StreamType.DIRECT_HLS
            else -> StreamType.MP4
        }
    }

    fun toPlayable(streamUrl: String): Playable {
        val base = baseUrlProvider().trimEnd('/')
        val type = classify(streamUrl)

        return when (type) {
            StreamType.TRANSCODE -> {
                Playable(Uri.parse(base + streamUrl), type, viaProxy = false)
            }
            StreamType.LOCAL -> {
                Playable(Uri.parse(base + streamUrl), type, viaProxy = false)
            }
            // Absolute HLS URLs (e.g. Tunarr on LAN) — play directly, no proxy needed
            StreamType.DIRECT_HLS -> {
                Playable(Uri.parse(streamUrl), type, viaProxy = false)
            }
            StreamType.HLS,
            StreamType.YOUTUBE_CDN,
            StreamType.VIMEO_CDN -> {
                val encoded = URLEncoder.encode(streamUrl, "UTF-8")
                Playable(Uri.parse("$base/proxy/stream?url=$encoded"), type, viaProxy = true)
            }
            StreamType.DASH,
            StreamType.MP4 -> {
                Playable(Uri.parse(streamUrl), type, viaProxy = false)
            }
        }
    }
}
