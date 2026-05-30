package com.watchdawg.tv.playback

import android.net.Uri
import java.net.URLEncoder

class StreamUrlResolver(private val baseUrlProvider: () -> String) {

    enum class StreamType { HLS, DASH, YOUTUBE_CDN, VIMEO_CDN, MP4, LOCAL, TRANSCODE }

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
            u.contains(".m3u8") || u.contains("m3u8") -> StreamType.HLS
            u.contains(".mpd") || u.contains("playlist.mpd") || u.contains("/playlist/av/primary") -> StreamType.DASH
            u.contains("googlevideo.com") -> StreamType.YOUTUBE_CDN
            u.contains("vimeocdn.com") || u.contains("vod-progressive.akamaized.net") ||
            u.contains("vod.akamaized.net") || u.contains("skyfire.vimeo.com") ||
            u.contains("av.vimeo.com") -> StreamType.VIMEO_CDN
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