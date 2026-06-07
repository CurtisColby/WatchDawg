package com.watchdawg.tv.playback

import android.content.Context
import android.net.Uri
import androidx.annotation.OptIn
import androidx.media3.common.MediaItem
import androidx.media3.common.MimeTypes
import androidx.media3.common.util.UnstableApi
import androidx.media3.datasource.DataSource
import androidx.media3.datasource.DefaultDataSource
import androidx.media3.datasource.okhttp.OkHttpDataSource
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.dash.DashMediaSource
import androidx.media3.exoplayer.hls.HlsMediaSource
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import androidx.media3.exoplayer.source.MergingMediaSource
import androidx.media3.exoplayer.source.MediaSource
import androidx.media3.exoplayer.source.ProgressiveMediaSource
import com.watchdawg.tv.playback.StreamUrlResolver.StreamType
import okhttp3.OkHttpClient

/**
 * Manages the singleton ExoPlayer instance for the entire app lifetime.
 *
 * Play path selection:
 *
 *   play(url)              — standard path. StreamUrlResolver classifies the URL
 *                            and picks HLS, DASH, Progressive, or Local source.
 *                            Used for: YouTube CDN (HLS mode), Reddit, local files,
 *                            direct MP4s, and any single-URL source.
 *
 *   playDash(manifestUrl)  — DASH MPD manifest path. Used for YouTube split streams
 *                            (video-only + audio-only MP4 progressive files) where
 *                            the backend generates a DASH manifest that ExoPlayer's
 *                            native DASH engine merges. NOT suitable for HLS splits.
 *
 *   playMerged(videoUrl,   — HLS split stream path. Used for Vimeo, which serves
 *              audioUrl)     video and audio as separate m3u8_native HLS streams.
 *                            MergingMediaSource combines two HlsMediaSource instances
 *                            so ExoPlayer plays them in sync. This works correctly
 *                            for HLS because HLS handles its own segment timing —
 *                            unlike progressive MP4s where MergingMediaSource has
 *                            known sync issues in Media3.
 *
 * Routing logic (in PlayerScreen.kt LaunchedEffect):
 *   audioUrl present + URL contains "m3u8" or is VIMEO_CDN type  → playMerged()
 *   audioUrl present + URL is progressive MP4 (YouTube)           → playDash()
 *   audioUrl absent                                                → play()
 *
 * Surface lifecycle:
 *   detachSurface()    — called from MainActivity.onStop(). Clears the video
 *                        surface from the player so ExoPlayer does not hold a
 *                        reference to a Surface that the system is about to
 *                        destroy (e.g. when Projectivy Launcher takes focus or
 *                        the TV briefly interrupts the activity). The player
 *                        instance is NOT released — it survives the lifecycle
 *                        transition so audio continues and state is preserved.
 *
 *   reattachSurface()  — called from MainActivity.onResume(). Previously this
 *                        incorrectly called clearVideoSurface() again (same as
 *                        detachSurface), which meant the surface was never
 *                        restored and ExoPlayer would eventually throw a null
 *                        surface exception and crash. The correct behaviour is
 *                        to do nothing here — PlayerView re-binds itself to
 *                        the player automatically when it re-enters composition
 *                        on the next frame. Calling clearVideoSurface() a second
 *                        time would race with that re-bind and could prevent
 *                        video from ever rendering again after a Projectivy
 *                        overlay interaction.
 */
@OptIn(UnstableApi::class)
class PlayerManager(
    context: Context,
    okHttpClient: OkHttpClient,
    private val streamResolver: StreamUrlResolver,
) {
    private val appContext = context.applicationContext

    private val httpFactory: DataSource.Factory =
        OkHttpDataSource.Factory(okHttpClient)
            .setUserAgent("WatchDawgTV/0.1 (ExoPlayer)")

    private val dataSourceFactory: DataSource.Factory =
        DefaultDataSource.Factory(appContext, httpFactory)

    val player: ExoPlayer = ExoPlayer.Builder(appContext)
        .setMediaSourceFactory(DefaultMediaSourceFactory(dataSourceFactory))
        .build()
        .apply {
            playWhenReady = true
        }

    /**
     * Play a single stream URL — standard path for most sources.
     *
     * Always stops and clears the current media before loading the new source.
     * This is critical because PlayerManager is a singleton — without clearing,
     * a previously loaded DASH or Merged source can interfere with the next play.
     */
    fun play(rawStreamUrl: String, positionMs: Long = 0L): StreamType {
        val playable  = streamResolver.toPlayable(rawStreamUrl)
        val mediaItem = MediaItem.fromUri(playable.uri)

        val source: MediaSource = when (playable.type) {
            StreamType.HLS,
            StreamType.DIRECT_HLS,
            StreamType.YOUTUBE_CDN ->
                HlsMediaSource.Factory(dataSourceFactory).createMediaSource(mediaItem)
            StreamType.DASH ->
                DashMediaSource.Factory(dataSourceFactory).createMediaSource(mediaItem)
            StreamType.TRANSCODE,
            StreamType.VIMEO_CDN,
            StreamType.MP4,
            StreamType.LOCAL ->
                ProgressiveMediaSource.Factory(dataSourceFactory).createMediaSource(mediaItem)
        }

        player.stop()
        player.clearMediaItems()
        player.setMediaSource(source)
        player.prepare()
        if (positionMs > 0L) player.seekTo(positionMs)
        player.playWhenReady = true
        player.play()
        return playable.type
    }

    /**
     * Play a split video+audio stream via a DASH MPD manifest URL.
     *
     * Used for YouTube split streams (video-only + audio-only progressive MP4).
     * The backend generates the DASH manifest at /resolve/{id}/manifest.mpd and
     * ExoPlayer's native DASH engine handles sync of both tracks.
     *
     * NOT used for Vimeo HLS splits — use playMerged() for those instead.
     */
    fun playDash(manifestUrl: String, positionMs: Long = 0L) {
        val mediaItem = MediaItem.Builder()
            .setUri(manifestUrl)
            .setMimeType(MimeTypes.APPLICATION_MPD)
            .build()

        val source = DashMediaSource.Factory(dataSourceFactory)
            .createMediaSource(mediaItem)

        player.stop()
        player.clearMediaItems()
        player.setMediaSource(source)
        player.prepare()
        if (positionMs > 0L) player.seekTo(positionMs)
        player.playWhenReady = true
        player.play()
    }

    /**
     * Play split HLS video+audio streams via MergingMediaSource.
     *
     * Used for Vimeo, which serves content exclusively as separate HLS streams:
     *   videoUrl  — m3u8_native video-only stream  (...&st=video)
     *   audioUrl  — m3u8_native audio-only stream  (...&st=audio or audio-high)
     *
     * MergingMediaSource is safe for HLS because HLS handles its own segment
     * timing internally — ExoPlayer reads each stream's timeline from the m3u8
     * playlist and synchronises them correctly. This is unlike progressive MP4
     * where MergingMediaSource has known Media3 sync issues.
     *
     * Both URLs are already proxied through /proxy/stream by the backend so
     * the required Referer: https://vimeo.com/ header is injected correctly.
     * ExoPlayer never contacts Vimeo CDN directly.
     *
     * positionMs seek is applied after prepare() — ExoPlayer honours seekTo()
     * on HLS by finding the correct segment for that timestamp.
     */
    fun playMerged(videoUrl: String, audioUrl: String, positionMs: Long = 0L) {
        val videoItem  = MediaItem.fromUri(Uri.parse(videoUrl))
        val audioItem  = MediaItem.fromUri(Uri.parse(audioUrl))

        val videoSource = HlsMediaSource.Factory(dataSourceFactory).createMediaSource(videoItem)
        val audioSource = HlsMediaSource.Factory(dataSourceFactory).createMediaSource(audioItem)

        val merged = MergingMediaSource(videoSource, audioSource)

        player.stop()
        player.clearMediaItems()
        player.setMediaSource(merged)
        player.prepare()
        if (positionMs > 0L) player.seekTo(positionMs)
        player.playWhenReady = true
        player.play()
    }

    fun restartAtPosition(rawStreamUrl: String, manifestUrl: String?, positionMs: Long) {
        if (manifestUrl != null) {
            playDash(manifestUrl, positionMs)
        } else {
            play(rawStreamUrl, positionMs)
        }
    }

    fun pause()  { player.pause() }
    fun resume() { player.play() }

    fun seekBy(deltaMs: Long) {
        val target = (player.currentPosition + deltaMs).coerceAtLeast(0L)
        player.seekTo(target)
    }

    fun positionMs(): Long = player.currentPosition

    /**
     * Detach the video surface from the player without releasing it.
     *
     * Called from MainActivity.onStop() so the player survives the activity
     * lifecycle transition (e.g. Projectivy Launcher overlay interactions,
     * screensaver activation) without throwing a null surface exception.
     * Audio continues playing; only the video surface is disconnected.
     */
    fun detachSurface() {
        player.clearVideoSurface()
    }

    /**
     * Signal that the activity has resumed and the video surface is available
     * again.
     *
     * The correct implementation here is intentionally a no-op. PlayerView
     * re-binds itself to the player automatically when it re-enters the Compose
     * composition on the next frame after onResume(). Calling clearVideoSurface()
     * here (as the previous broken implementation did) would race with that
     * automatic re-bind and could permanently prevent video from rendering,
     * ultimately causing a crash when ExoPlayer tries to decode to a null surface.
     *
     * Do NOT add clearVideoSurface() or setVideoSurface() calls here.
     */
    fun reattachSurface() {
        // Intentional no-op. PlayerView handles its own re-bind on resume.
        // See class-level KDoc for the full explanation.
    }

    /**
     * Fully release the ExoPlayer instance.
     * Called ONLY from Graph.releasePlayerManager() → MainActivity.onDestroy().
     * Never called from PlayerScreen disposal — the player must survive nav
     * transitions so the inline mini-player can keep playing while browsing.
     */
    fun release() {
        player.release()
    }
}
