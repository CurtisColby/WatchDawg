package com.watchdawg.tv.ui.player

import androidx.annotation.OptIn
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.EnterTransition
import androidx.compose.animation.ExitTransition
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutVertically
import androidx.compose.foundation.background
import androidx.compose.foundation.focusable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.ui.PlayerView
import androidx.activity.compose.BackHandler
import androidx.compose.ui.viewinterop.AndroidView
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.tv.material3.Button
import androidx.tv.material3.ButtonDefaults
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import android.graphics.Bitmap
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.tween
import androidx.compose.ui.graphics.toArgb
import androidx.palette.graphics.Palette
import coil.ImageLoader
import coil.compose.AsyncImage
import coil.request.ImageRequest
import coil.request.SuccessResult
import com.watchdawg.tv.Graph
import com.watchdawg.tv.ui.theme.WatchDawgColors
import com.watchdawg.tv.ui.theme.focusGlow
import kotlinx.coroutines.delay

// ── Player start mode ─────────────────────────────────────────────────────────

sealed class PlayerStartMode {
    data class Resolve(
        val videoId: Int,
        val queue: List<Int>,
        val startIndex: Int,
        val startMs: Long = 0L,
        val hlsMode: Boolean = false,
        // Milestone E: true when the source channel is locked/adult so
        // PlayerViewModel.saveResume() refuses to persist to SharedPreferences.
        val lockedSource: Boolean = false,
    ) : PlayerStartMode()
    data class DirectSingle(val url: String, val title: String) : PlayerStartMode()
    data class DirectQueue(val urls: List<String>, val startIndex: Int, val startMs: Long = 0L) : PlayerStartMode()
}

// ── Hold-seek constants ───────────────────────────────────────────────────────
private const val HOLD_THRESHOLD_MS = 400L
private const val SCRUB_STEP_START  = 10_000L
private const val SCRUB_STEP_MAX    = 120_000L
private const val SCRUB_RAMP_TICKS  = 15

// ── Speed options ─────────────────────────────────────────────────────────────
private val SPEED_OPTIONS = listOf(0.5f, 0.75f, 1.0f, 1.25f, 1.5f, 2.0f)

@OptIn(UnstableApi::class)
@Composable
fun PlayerScreen(
    viewModel: PlayerViewModel,
    startMode: PlayerStartMode,
    onExit: () -> Unit,
    onStop: () -> Unit,
    // Session 38 — EPG channel surfing. When non-null, D-pad Down/Up call these
    // lambdas instead of viewModel.next()/previous(). MainActivity wires them up
    // with EPG logic (getAdjacentSlot + re-navigate). Null for all non-EPG screens
    // so existing queue-based surfing is completely unaffected.
    onSurfNext: (() -> Unit)? = null,
    onSurfPrev: (() -> Unit)? = null,
) {
    val context              = LocalContext.current
    val state                by viewModel.state.collectAsStateWithLifecycle()
    val playerManager        = remember { Graph.playerManager(context) }
    val playerSurfaceFocus   = remember { FocusRequester() }
    val controlBarFirstFocus = remember { FocusRequester() }

    val isPlaying by produceState(initialValue = false, playerManager) {
        val listener = object : Player.Listener {
            override fun onIsPlayingChanged(playing: Boolean) { value = playing }
        }
        playerManager.player.addListener(listener)
        value = playerManager.player.isPlaying
        awaitDispose { playerManager.player.removeListener(listener) }
    }

    var controlsVisible    by remember { mutableStateOf(false) }
    var controlsHideTimer  by remember { mutableLongStateOf(0L) }
    var seekBubble         by remember { mutableStateOf<String?>(null) }
    var favBubble          by remember { mutableStateOf(false) }
    var saveBubble         by remember { mutableStateOf(false) }
    var deleteBubble       by remember { mutableStateOf(false) }
    var speedBubble        by remember { mutableStateOf<String?>(null) }  // Milestone E
    var channelSurfBubble  by remember { mutableStateOf<String?>(null) }  // Session 38
    var okDownTime         by remember { mutableLongStateOf(0L) }
    val longPressMs  = 500L
    val autoHideMs   = 7_000L

    // Milestone E: speed menu visibility (separate from main controls)
    var speedMenuVisible by remember { mutableStateOf(false) }
    val speedMenuFirstFocus = remember { FocusRequester() }

    // Session 33 — Dynamic Tinting: dominant colour extracted from the
    // current video's thumbnail via Palette API. Applied as a subtle background
    // tint behind the control bar. Falls back to transparent (solid black base)
    // when the thumbnail is null or Palette returns no suitable swatch.
    var tintColor by remember { mutableStateOf(Color.Transparent) }
    val animatedTint by animateColorAsState(
        targetValue  = tintColor,
        animationSpec = tween(durationMillis = 600),
        label        = "playerTint",
    )

    var scrubActive         by remember { mutableStateOf(false) }
    var scrubTickCount      by remember { mutableStateOf(0) }
    var holdKeyDownTime     by remember { mutableLongStateOf(0L) }
    var holdTriggered       by remember { mutableStateOf(false) }
    var scrubOverlayVisible by remember { mutableStateOf(false) }
    var scrubOverlayLastInputMs by remember { mutableLongStateOf(0L) }

    fun showControls() {
        controlsVisible = true
        controlsHideTimer = System.currentTimeMillis()
    }
    fun hideControls() {
        controlsVisible = false
        speedMenuVisible = false
        try { playerSurfaceFocus.requestFocus() } catch (_: Exception) {}
    }

    fun holdSeekTick(direction: Int) {
        val ramp = (scrubTickCount.toFloat() / SCRUB_RAMP_TICKS).coerceIn(0f, 1f)
        val step = (SCRUB_STEP_START + (SCRUB_STEP_MAX - SCRUB_STEP_START) * ramp).toLong()
        playerManager.seekBy(step * direction)
        scrubTickCount++
        scrubActive = true
        scrubOverlayVisible = true
        scrubOverlayLastInputMs = System.currentTimeMillis()
    }

    // ── Auto-hide timers ──────────────────────────────────────────────────────
    LaunchedEffect(scrubOverlayLastInputMs) {
        if (scrubOverlayLastInputMs > 0L) {
            delay(1500L)
            if (System.currentTimeMillis() - scrubOverlayLastInputMs >= 1500L) {
                scrubOverlayVisible = false
            }
        }
    }
    LaunchedEffect(controlsHideTimer) {
        if (controlsHideTimer > 0L) {
            delay(autoHideMs)
            if (System.currentTimeMillis() - controlsHideTimer >= autoHideMs && !speedMenuVisible) hideControls()
        }
    }

    // Critical: focus must land on control bar AFTER it is rendered, not before.
    LaunchedEffect(controlsVisible) {
        if (controlsVisible) {
            delay(50)
            try { controlBarFirstFocus.requestFocus() } catch (_: Exception) {}
        }
    }
    // When speed menu opens, move focus to first speed button so D-pad works immediately.
    LaunchedEffect(speedMenuVisible) {
        if (speedMenuVisible) {
            delay(50)
            try { speedMenuFirstFocus.requestFocus() } catch (_: Exception) {}
        }
    }

    // ── Bubble auto-dismiss ───────────────────────────────────────────────────
    LaunchedEffect(seekBubble)        { if (seekBubble != null)        { delay(900);  seekBubble = null  } }
    LaunchedEffect(favBubble)         { if (favBubble)                 { delay(1200); favBubble  = false } }
    LaunchedEffect(saveBubble)        { if (saveBubble)                { delay(1200); saveBubble = false } }
    LaunchedEffect(deleteBubble)      { if (deleteBubble)              { delay(1200); deleteBubble = false } }
    LaunchedEffect(speedBubble)       { if (speedBubble != null)       { delay(1200); speedBubble = null } }
    LaunchedEffect(channelSurfBubble) { if (channelSurfBubble != null) { delay(800);  channelSurfBubble = null } }
    LaunchedEffect(state.ended)  { if (state.ended) onStop() }

    // ── Dynamic Tinting: extract dominant muted colour from thumbnail ─────────
    // Fires whenever the thumbnail URL changes (i.e. on each new video).
    // Uses Coil's ImageLoader to load the bitmap then Palette to extract the
    // dominant dark muted swatch. The resulting colour is darkened to 40% alpha
    // so it blends subtly with the black background rather than overwhelming it.
    // No-op when thumbnailUrl is null — tintColor stays transparent.
    LaunchedEffect(state.thumbnailUrl) {
        val rawUrl = state.thumbnailUrl
        if (rawUrl.isNullOrBlank()) {
            tintColor = Color.Transparent
            return@LaunchedEffect
        }
        val baseUrl = Graph.serverPrefs.getBaseUrl().trimEnd('/')
        val resolvedUrl = if (rawUrl.startsWith("http")) rawUrl else "${'$'}baseUrl${'$'}rawUrl"
        try {
            val loader  = ImageLoader(context)
            val request = ImageRequest.Builder(context).data(resolvedUrl).allowHardware(false).build()
            val result  = loader.execute(request)
            if (result is SuccessResult) {
                val bitmap: Bitmap = (result.drawable as? android.graphics.drawable.BitmapDrawable)
                    ?.bitmap ?: return@LaunchedEffect
                val palette = Palette.from(bitmap).generate()
                val swatch  = palette.darkMutedSwatch
                    ?: palette.mutedSwatch
                    ?: palette.darkVibrantSwatch
                if (swatch != null) {
                    // Apply at 35% alpha so the tint is atmospheric, not garish
                    tintColor = Color(swatch.rgb).copy(alpha = 0.35f)
                } else {
                    tintColor = Color.Transparent
                }
            }
        } catch (_: Exception) {
            tintColor = Color.Transparent
        }
    }

    // ── Initial start ─────────────────────────────────────────────────────────
    LaunchedEffect(Unit) {
        when (val m = startMode) {
            is PlayerStartMode.Resolve -> {
                if (m.startMs > 0L && (playerManager.player.playbackState == 3 || playerManager.player.playbackState == 2)) {
                    viewModel.resumeFromMiniPlayer(m.startMs)
                } else {
                    viewModel.start(m.videoId, m.queue, m.startIndex, m.startMs, m.hlsMode, m.lockedSource)
                }
            }
            is PlayerStartMode.DirectSingle -> viewModel.startDirect(m.url, m.title)
            is PlayerStartMode.DirectQueue  -> viewModel.startDirectQueue(m.urls, m.startIndex, m.startMs)
        }
        playerSurfaceFocus.requestFocus()
    }

    // Stop ExoPlayer when ViewModel is loading (next video resolving)
    LaunchedEffect(state.loading) {
        if (state.loading) playerManager.player.stop()
    }

    // ── Play on new playToken ─────────────────────────────────────────────────
    // Speed is always 1.0f here because every code path that emits a new playToken
    // resets playbackSpeed = 1.0f in UiState. Applying it here ensures ExoPlayer
    // is always at normal speed at the start of each new video.
    LaunchedEffect(state.playToken) {
        val url      = state.streamUrl
        val audioUrl = state.audioUrl
        val videoId  = state.videoId
        if (state.playToken > 0 && !url.isNullOrBlank()) {
            val resumeMs       = state.startPositionMs
            val alreadyPlaying = playerManager.player.playbackState.let { it == 3 || it == 2 }

            // Always apply speed (will be 1.0f for new videos, preserved for token refresh)
            playerManager.player.setPlaybackSpeed(state.playbackSpeed)

            when {
                resumeMs > 0L && alreadyPlaying -> {
                    playerManager.player.seekTo(resumeMs)
                    playerManager.player.play()
                }
                resumeMs == -1L && alreadyPlaying -> {
                    val livePos = playerManager.player.currentPosition
                    playerManager.play(url, livePos)
                }
                !audioUrl.isNullOrBlank() && isHlsSplit(url) -> {
                    // Vimeo HLS split: video + audio are separate m3u8_native streams.
                    playerManager.playMerged(url, audioUrl, resumeMs)
                }
                !audioUrl.isNullOrBlank() && videoId != null -> {
                    // YouTube split (video-only + audio-only progressive MP4).
                    val baseUrl = Graph.serverPrefs.getBaseUrl().trimEnd('/')
                    playerManager.playDash("$baseUrl/resolve/$videoId/manifest.mpd", resumeMs)
                }
                url.contains(".mpd", ignoreCase = true) || url.contains("manifest.mpd", ignoreCase = true) -> {
                    // Session 40: EPG WatchDawg slots return a /resolve/{id}/manifest.mpd URL.
                    // Route through playDash() which uses STATE_READY listener for seekTo()
                    // instead of ClippingConfiguration — ClippingConfiguration fails on DASH
                    // manifests backed by YouTube progressive MP4 (CDN rejects byte-range seeks).
                    playerManager.playDash(url, resumeMs)
                }
                else -> playerManager.play(url, resumeMs)
            }
        }
    }

    // ── Apply speed changes from setSpeed() without changing video ────────────
    // This LaunchedEffect fires when the user picks a new speed from the menu.
    // It does NOT fire on playToken changes — those are handled above.
    LaunchedEffect(state.playbackSpeed) {
        playerManager.player.setPlaybackSpeed(state.playbackSpeed)
    }

    // ── Milestone E: start history writes once playback is confirmed running ────
    //
    // HLS mode: starts the 10-second loop (startHistoryLoop) so Continue Watching
    // gets a live position and can resume at the right spot.
    //
    // Split-stream mode: writes a single "started" record (writeHistoryStarted)
    // so the video appears in Continue Watching. Position = 0 because split-stream
    // is non-seekable — resume always starts from the beginning.
    //
    // Neither path fires for locked sources — isLockedSource guards the ViewModel.
    LaunchedEffect(isPlaying, state.videoId) {
        val videoId = state.videoId
        if (isPlaying && videoId != null) {
            if (state.hlsMode) {
                viewModel.startHistoryLoop(
                    videoId = videoId,
                    playerPositionMs = { playerManager.player.currentPosition },
                    playerDurationMs = { playerManager.player.duration.takeIf { it > 0L } ?: -1L },
                )
            } else {
                // Split-stream: write once when playback starts, stop any prior loop
                viewModel.stopHistoryLoop()
                viewModel.writeHistoryStarted(
                    playerDurationMs = { playerManager.player.duration.takeIf { it > 0L } ?: -1L },
                )
            }
        } else {
            viewModel.stopHistoryLoop()
        }
    }

    // ── Playback event listener ───────────────────────────────────────────────
    DisposableEffect(Unit) {
        val listener = object : Player.Listener {
            override fun onPlaybackStateChanged(s: Int) {
                if (s == Player.STATE_ENDED) {
                    // Write history on completion for all RESOLVE mode videos
                    viewModel.writeHistoryCompletion(
                        positionMs = playerManager.player.currentPosition,
                        durationMs = playerManager.player.duration.takeIf { it > 0L } ?: 0L,
                    )
                    viewModel.onEnded()
                }
            }
            override fun onPlayerError(e: androidx.media3.common.PlaybackException) {
                viewModel.onPlaybackError()
            }
        }
        playerManager.player.addListener(listener)
        onDispose {
            viewModel.saveResume(playerManager.positionMs())
            viewModel.stopHistoryLoop()
            playerManager.player.removeListener(listener)
        }
    }

    // ── BackHandler pairs ─────────────────────────────────────────────────────
    // Speed menu dismisses first if visible, then normal control flow.
    BackHandler(enabled = speedMenuVisible) {
        speedMenuVisible = false
    }
    BackHandler(enabled = controlsVisible && !speedMenuVisible) { hideControls() }
    BackHandler(enabled = !controlsVisible && !speedMenuVisible) {
        viewModel.saveResume(playerManager.positionMs())
        onExit()
    }

    // ── Root box ──────────────────────────────────────────────────────────────
    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(Color.Black)
            // Session 33: Dynamic Tinting — subtle ambient colour derived from
            // the video thumbnail, animated on video change. Layered above the
            // solid black base so black always shows when tint is transparent.
            .background(animatedTint)
            .focusRequester(playerSurfaceFocus)
            .focusable()  // ← critical: Box must be in focus graph to receive key events
            .onKeyEvent { event ->
                when (event.type) {
                    KeyEventType.KeyDown -> when (event.key) {
                        Key.DirectionCenter, Key.Enter -> {
                            if (okDownTime == 0L) okDownTime = System.currentTimeMillis()
                            true
                        }
                        Key.DirectionRight -> {
                            if (controlsVisible) return@onKeyEvent false
                            val now = System.currentTimeMillis()
                            if (holdKeyDownTime == 0L) holdKeyDownTime = now
                            if (now - holdKeyDownTime >= HOLD_THRESHOLD_MS || scrubActive) {
                                holdSeekTick(+1)
                            }
                            true
                        }
                        Key.DirectionLeft -> {
                            if (controlsVisible) return@onKeyEvent false
                            val now = System.currentTimeMillis()
                            if (holdKeyDownTime == 0L) holdKeyDownTime = now
                            if (now - holdKeyDownTime >= HOLD_THRESHOLD_MS || scrubActive) {
                                holdSeekTick(-1)
                            }
                            true
                        }
                        else -> false
                    }
                    KeyEventType.KeyUp -> when (event.key) {
                        Key.DirectionCenter, Key.Enter -> {
                            val held = System.currentTimeMillis() - okDownTime
                            okDownTime = 0L
                            if (held >= longPressMs) { viewModel.favoriteCurrent(); favBubble = true }
                            else showControls()
                            true
                        }
                        Key.DirectionRight -> {
                            if (controlsVisible) return@onKeyEvent false
                            val heldMs = if (holdKeyDownTime > 0L) System.currentTimeMillis() - holdKeyDownTime else 0L
                            holdKeyDownTime = 0L
                            if (!scrubActive && heldMs < HOLD_THRESHOLD_MS) {
                                playerManager.seekBy(10_000); seekBubble = "+10s"
                            }
                            scrubActive = false; scrubTickCount = 0; holdTriggered = false
                            scrubOverlayVisible = false
                            playerManager.player.play()
                            true
                        }
                        Key.DirectionLeft -> {
                            if (controlsVisible) return@onKeyEvent false
                            val heldMs = if (holdKeyDownTime > 0L) System.currentTimeMillis() - holdKeyDownTime else 0L
                            holdKeyDownTime = 0L
                            if (!scrubActive && heldMs < HOLD_THRESHOLD_MS) {
                                playerManager.seekBy(-10_000); seekBubble = "-10s"
                            }
                            scrubActive = false; scrubTickCount = 0; holdTriggered = false
                            scrubOverlayVisible = false
                            playerManager.player.play()
                            true
                        }
                        Key.DirectionDown -> if (controlsVisible) false else {
                            // Session 38: Down surfs to next channel/video when controls hidden.
                            // If onSurfNext is wired (EPG mode) call it — MainActivity handles
                            // getAdjacentSlot + re-navigation. Otherwise fall back to queue next().
                            if (onSurfNext != null) onSurfNext()
                            else viewModel.next()
                            channelSurfBubble = "▶▶"
                            true
                        }
                        Key.DirectionUp -> {
                            // Session 38: Up surfs to previous channel/video when controls hidden.
                            // Up with controls visible still hides the overlay (existing behaviour).
                            if (controlsVisible) hideControls()
                            else {
                                if (onSurfPrev != null) onSurfPrev()
                                else viewModel.previous()
                                channelSurfBubble = "◀◀"
                            }
                            true
                        }
                        else -> false
                    }
                    else -> false
                }
            },
    ) {
        // ── ExoPlayer surface ─────────────────────────────────────────────────
        AndroidView(
            factory  = { ctx -> PlayerView(ctx).apply { player = playerManager.player; useController = false } },
            modifier = Modifier.fillMaxSize(),
        )

        // ── Loading overlay ───────────────────────────────────────────────────
        AnimatedVisibility(
            visible  = state.loading,
            enter    = EnterTransition.None,
            exit     = ExitTransition.None,
            modifier = Modifier.fillMaxSize(),
        ) {
            Box(Modifier.fillMaxSize().background(Color.Black), Alignment.Center) { ResolvingSpinner() }
        }

        // ── Scrub overlay ─────────────────────────────────────────────────────
        AnimatedVisibility(
            visible  = scrubOverlayVisible && !controlsVisible,
            enter    = fadeIn(animationSpec = tween(150)),
            exit     = fadeOut(animationSpec = tween(300)),
            modifier = Modifier.fillMaxSize(),
        ) {
            val baseUrl = Graph.serverPrefs.getBaseUrl()
            ScrubOverlay(
                scrubPositionMs = playerManager.player.currentPosition,
                durationMs      = playerManager.player.duration.takeIf { it > 0L } ?: 0L,
                thumbnailUrl    = state.thumbnailUrl,
                baseUrl         = baseUrl,
                title           = state.title,
            )
        }

        // ── Single-seek bubble ────────────────────────────────────────────────
        AnimatedVisibility(
            visible  = seekBubble != null,
            enter    = fadeIn(), exit = fadeOut(),
            modifier = Modifier.align(Alignment.Center),
        ) {
            Box(
                Modifier
                    .background(Color(0xCC000000), MaterialTheme.shapes.large)
                    .padding(horizontal = 28.dp, vertical = 16.dp),
            ) {
                Text(seekBubble ?: "", fontSize = 28.sp, color = Color.White)
            }
        }

        // ── Action bubbles ────────────────────────────────────────────────────
        if (favBubble) {
            Box(Modifier.fillMaxSize().padding(bottom = 80.dp), Alignment.BottomCenter) {
                Text("♥ Favorited", fontSize = 24.sp, color = WatchDawgColors.Star)
            }
        }
        if (saveBubble) {
            Box(Modifier.fillMaxSize().padding(bottom = 80.dp), Alignment.BottomCenter) {
                Text("⬇ Saving…", fontSize = 24.sp, color = WatchDawgColors.ResolvedBadge)
            }
        }
        if (deleteBubble) {
            Box(Modifier.fillMaxSize().padding(bottom = 80.dp), Alignment.BottomCenter) {
                Text("🗑 Skipped", fontSize = 24.sp, color = WatchDawgColors.FailedBadge)
            }
        }
        // Milestone E: speed confirmation bubble
        if (speedBubble != null) {
            Box(Modifier.fillMaxSize().padding(bottom = 80.dp), Alignment.BottomCenter) {
                Text("⚡ ${speedBubble}×", fontSize = 24.sp, color = WatchDawgColors.Orange)
            }
        }

        // Session 38: channel surf bubble removed — surfing is now instant via
        // FFmpeg streams so the directional arrow indicator is no longer needed.

        // ── Milestone E: Speed menu overlay ──────────────────────────────────
        // Shown above the control bar. D-pad navigable row of speed buttons.
        // Hidden in split-stream mode (hlsMode=false) because speed still works
        // there, but showing it alongside the skip90s absence would be confusing.
        // Speed control works on all modes so we show it always.
        AnimatedVisibility(
            visible  = speedMenuVisible,
            enter    = fadeIn() + slideInVertically { it / 2 },
            exit     = fadeOut() + slideOutVertically { it / 2 },
            modifier = Modifier.align(Alignment.BottomCenter).padding(bottom = 180.dp),
        ) {
            SpeedMenu(
                currentSpeed = state.playbackSpeed,
                firstFocus = speedMenuFirstFocus,
                onSpeedSelected = { speed ->
                    viewModel.setSpeed(speed)
                    speedMenuVisible = false
                    speedBubble = if (speed == 1.0f) "1.0" else speed.toString().trimEnd('0').trimEnd('.')
                    showControls()
                },
            )
        }

        // ── Control bar ───────────────────────────────────────────────────────
        AnimatedVisibility(
            visible  = controlsVisible,
            enter    = slideInVertically { it } + fadeIn(),
            exit     = slideOutVertically { it } + fadeOut(),
            modifier = Modifier.align(Alignment.BottomCenter),
        ) {
            PlayerControlBar(
                title            = state.title,
                artist           = state.artist,
                isPlaying        = isPlaying,
                hlsMode          = state.hlsMode,
                currentSpeed     = state.playbackSpeed,
                firstButtonFocus = controlBarFirstFocus,
                onPrevious    = { viewModel.previous(); showControls() },
                onSeekBack    = { playerManager.seekBy(-10_000); seekBubble = "-10s"; showControls() },
                onPlayPause   = {
                    if (isPlaying) playerManager.player.pause()
                    else playerManager.player.play()
                    showControls()
                },
                onSeekForward = { playerManager.seekBy(10_000); seekBubble = "+10s"; showControls() },
                onNext        = { viewModel.next(); showControls() },
                onFavorite    = { viewModel.favoriteCurrent(); favBubble = true; showControls() },
                onSave        = { viewModel.saveCurrent(); saveBubble = true; showControls() },
                onDelete      = { viewModel.skipCurrent(); deleteBubble = true },
                onStop        = { viewModel.saveResume(playerManager.positionMs()); onStop() },
                // Milestone E: skip 90s — only wired in HLS mode
                onSkip90s     = if (state.hlsMode) {
                    { playerManager.seekBy(90_000); seekBubble = "+90s"; showControls() }
                } else null,
                // Milestone E: open speed menu
                onSpeedMenu   = { speedMenuVisible = !speedMenuVisible; showControls() },
            )
        }
    }
}

// ── Milestone E: Speed menu ───────────────────────────────────────────────────

@Composable
private fun SpeedMenu(
    currentSpeed: Float,
    firstFocus: FocusRequester,
    onSpeedSelected: (Float) -> Unit,
) {
    Box(
        modifier = Modifier
            .background(Color(0xEE0A0A10), MaterialTheme.shapes.large)
            .padding(horizontal = 24.dp, vertical = 16.dp),
    ) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Text(
                text  = "Playback Speed",
                style = MaterialTheme.typography.titleSmall,
                color = WatchDawgColors.TextSecondary,
            )
            Spacer(Modifier.height(12.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                SPEED_OPTIONS.forEach { speed ->
                    val isSelected = speed == currentSpeed
                    val label = when (speed) {
                        0.5f  -> "0.5×"
                        0.75f -> "0.75×"
                        1.0f  -> "1×"
                        1.25f -> "1.25×"
                        1.5f  -> "1.5×"
                        2.0f  -> "2×"
                        else  -> "${speed}×"
                    }
                    // 1.0f button gets the firstFocus requester — it's the logical
                    // default focus target when the menu opens regardless of current speed.
                    val btnMod = if (speed == 1.0f)
                        Modifier.focusRequester(firstFocus).focusGlow()
                    else
                        Modifier.focusGlow()
                    Button(
                        onClick = { onSpeedSelected(speed) },
                        colors  = ButtonDefaults.colors(
                            containerColor        = if (isSelected) WatchDawgColors.Orange else WatchDawgColors.Surface,
                            contentColor          = if (isSelected) Color.Black else WatchDawgColors.TextPrimary,
                            focusedContainerColor = WatchDawgColors.OrangeDim,
                            focusedContentColor   = Color.White,
                        ),
                        modifier = btnMod,
                    ) {
                        Text(label, style = MaterialTheme.typography.titleSmall)
                    }
                }
            }
        }
    }
}

// ── Control bar ───────────────────────────────────────────────────────────────

@Composable
private fun PlayerControlBar(
    title: String,
    artist: String,
    isPlaying: Boolean,
    hlsMode: Boolean,
    currentSpeed: Float,
    firstButtonFocus: FocusRequester,
    onPrevious: () -> Unit,
    onSeekBack: () -> Unit,
    onPlayPause: () -> Unit,
    onSeekForward: () -> Unit,
    onNext: () -> Unit,
    onFavorite: () -> Unit,
    onSave: () -> Unit,
    onDelete: () -> Unit,
    onStop: () -> Unit,
    onSkip90s: (() -> Unit)?,   // null when not in HLS mode — button hidden
    onSpeedMenu: () -> Unit,
) {
    val speedLabel = when (currentSpeed) {
        1.0f -> "1×"
        0.5f -> "0.5×"
        0.75f -> "0.75×"
        1.25f -> "1.25×"
        1.5f -> "1.5×"
        2.0f -> "2×"
        else -> "${currentSpeed}×"
    }

    Box(
        modifier = Modifier
            .fillMaxWidth()
            .background(Brush.verticalGradient(listOf(Color.Transparent, Color(0xF0050507))))
            .padding(horizontal = 40.dp, vertical = 24.dp),
    ) {
        Column {
            Text(title, style = MaterialTheme.typography.titleLarge, color = Color.White)
            if (artist.isNotBlank())
                Text(artist, style = MaterialTheme.typography.bodyLarge, color = WatchDawgColors.Orange)
            Spacer(Modifier.height(20.dp))
            Row(
                horizontalArrangement = Arrangement.spacedBy(12.dp),
                verticalAlignment     = Alignment.CenterVertically,
            ) {
                ControlButton("⏮", "Prev",    onPrevious)
                ControlButton("⏪", "-10s",    onSeekBack)
                ControlButton(
                    label          = if (isPlaying) "⏸" else "▶",
                    hint           = if (isPlaying) "Pause" else "Play",
                    onClick        = onPlayPause,
                    isPrimary      = true,
                    focusRequester = firstButtonFocus,
                )
                ControlButton("⏩", "+10s",   onSeekForward)
                ControlButton("⏭", "Next",    onNext)
                Spacer(Modifier.width(24.dp))
                ControlButton("♥", "Fav",     onFavorite, tint = WatchDawgColors.Star)
                ControlButton("⬇", "Save",    onSave,     tint = WatchDawgColors.ResolvedBadge)
                ControlButton("🗑", "Delete",  onDelete,   tint = WatchDawgColors.FailedBadge)
                ControlButton("⏹", "Stop",    onStop,     tint = WatchDawgColors.TextSecondary)
                Spacer(Modifier.width(24.dp))
                // Milestone E: speed menu button — always visible
                ControlButton("⚡", speedLabel, onSpeedMenu, tint = WatchDawgColors.Orange)
                // Milestone E: skip 90s — only shown in HLS/seekable mode
                if (onSkip90s != null) {
                    ControlButton("⏭90", "+90s", onSkip90s, tint = WatchDawgColors.Orange)
                }
            }
            Spacer(Modifier.height(4.dp))
            val hintSuffix = if (hlsMode) "  •  ⚡ = speed  •  ⏭90 = +90s" else "  •  ⚡ = speed"
            Text(
                "← → = seek 10s  •  Hold ← → = fast seek  •  ↓ = controls  •  Long OK = Favorite  •  ⬇ = Save$hintSuffix  •  ⏹ = stop",
                style = MaterialTheme.typography.labelSmall,
                color = WatchDawgColors.TextTertiary,
            )
        }
    }
}

@Composable
private fun ControlButton(
    label: String,
    hint: String,
    onClick: () -> Unit,
    isPrimary: Boolean = false,
    tint: Color = WatchDawgColors.TextPrimary,
    focusRequester: FocusRequester? = null,
) {
    val mod = if (focusRequester != null)
        Modifier.focusRequester(focusRequester).focusGlow()
    else
        Modifier.focusGlow()

    Button(
        onClick = onClick,
        colors  = ButtonDefaults.colors(
            containerColor        = if (isPrimary) WatchDawgColors.OrangeDim else WatchDawgColors.Surface,
            contentColor          = tint,
            focusedContainerColor = WatchDawgColors.SurfaceFocused,
            focusedContentColor   = Color.White,
        ),
        modifier = mod,
    ) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Text(label, fontSize = if (isPrimary) 22.sp else 18.sp, color = tint)
            Text(hint,  style = MaterialTheme.typography.labelSmall, color = WatchDawgColors.TextTertiary)
        }
    }
}

// ── Spinner ───────────────────────────────────────────────────────────────────

@Composable
private fun ResolvingSpinner() {
    val transition = rememberInfiniteTransition(label = "spinner")
    val rotation by transition.animateFloat(
        initialValue = 0f, targetValue = 360f,
        animationSpec = infiniteRepeatable(
            tween(900, easing = LinearEasing),
            RepeatMode.Restart,
        ),
        label = "spinnerRotation",
    )
    Column(
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(20.dp),
    ) {
        Text("◌", fontSize = 72.sp, color = WatchDawgColors.Orange, modifier = Modifier.rotate(rotation))
        Text("Resolving…", style = MaterialTheme.typography.titleLarge, color = WatchDawgColors.TextSecondary)
    }
}

// ── Scrub overlay ─────────────────────────────────────────────────────────────

@Composable
private fun ScrubOverlay(
    scrubPositionMs: Long,
    durationMs: Long,
    thumbnailUrl: String?,
    baseUrl: String,
    title: String,
) {
    val progress = if (durationMs > 0L) (scrubPositionMs.toFloat() / durationMs).coerceIn(0f, 1f) else 0f

    val resolvedThumbUrl = when {
        thumbnailUrl.isNullOrBlank()    -> null
        thumbnailUrl.startsWith("http") -> thumbnailUrl
        else                            -> baseUrl.trimEnd('/') + thumbnailUrl
    }

    val orange     = WatchDawgColors.Orange
    val trackBg    = Color(0x55FFFFFF)
    val thumbWhite = Color.White

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(
                Brush.verticalGradient(
                    0.0f to Color.Transparent,
                    0.5f to Color(0x88000000),
                    1.0f to Color(0xEE000000),
                ),
            ),
        contentAlignment = Alignment.BottomCenter,
    ) {
        Column(
            modifier            = Modifier
                .fillMaxWidth()
                .padding(start = 60.dp, end = 60.dp, bottom = 60.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            if (resolvedThumbUrl != null) {
                AsyncImage(
                    model              = resolvedThumbUrl,
                    contentDescription = null,
                    contentScale       = ContentScale.Fit,
                    modifier           = Modifier
                        .height(120.dp)
                        .aspectRatio(16f / 9f)
                        .clip(MaterialTheme.shapes.medium)
                        .background(WatchDawgColors.SurfaceElevated),
                )
                Spacer(Modifier.height(12.dp))
            }

            val positionFormatted = formatMs(scrubPositionMs)
            val durationFormatted = formatMs(durationMs)
            Text(
                "$positionFormatted / $durationFormatted",
                style = MaterialTheme.typography.headlineMedium,
                color = Color.White,
            )

            Spacer(Modifier.height(12.dp))

            // Progress bar with thumb
            androidx.compose.foundation.Canvas(
                modifier = Modifier.fillMaxWidth().height(6.dp),
            ) {
                val trackH = size.height
                drawRoundRect(
                    color        = trackBg,
                    topLeft      = androidx.compose.ui.geometry.Offset(0f, 0f),
                    size         = androidx.compose.ui.geometry.Size(size.width, trackH),
                    cornerRadius = androidx.compose.ui.geometry.CornerRadius(trackH / 2),
                )
                drawRoundRect(
                    color        = orange,
                    topLeft      = androidx.compose.ui.geometry.Offset(0f, 0f),
                    size         = androidx.compose.ui.geometry.Size(size.width * progress, trackH),
                    cornerRadius = androidx.compose.ui.geometry.CornerRadius(trackH / 2),
                )
                val thumbX = size.width * progress
                drawCircle(
                    color  = thumbWhite,
                    radius = trackH * 2f,
                    center = androidx.compose.ui.geometry.Offset(thumbX, trackH / 2),
                )
            }

            Spacer(Modifier.height(12.dp))

            Text(
                title,
                style    = MaterialTheme.typography.bodyLarge,
                color    = WatchDawgColors.TextSecondary,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }
    }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

private fun formatMs(ms: Long): String {
    if (ms <= 0L) return "0:00"
    val totalSec = ms / 1000
    val h  = totalSec / 3600
    val m  = (totalSec % 3600) / 60
    val s  = totalSec % 60
    return if (h > 0) "%d:%02d:%02d".format(h, m, s) else "%d:%02d".format(m, s)
}

/**
 * Determines whether a stream URL is a split HLS stream (Vimeo).
 * Used to route to playMerged() instead of playDash() for HLS splits.
 */
private fun isHlsSplit(videoUrl: String): Boolean {
    val lower = videoUrl.lowercase()
    if (lower.contains(".m3u8") || lower.contains("m3u8")) return true
    if (lower.contains("/proxy/stream?url=")) {
        val innerUrl = try {
            java.net.URLDecoder.decode(
                videoUrl.substringAfter("url=").substringBefore("&"),
                "UTF-8",
            ).lowercase()
        } catch (_: Exception) { "" }
        if (innerUrl.contains(".m3u8") || innerUrl.contains("m3u8")) return true
        if (innerUrl.contains("skyfire.vimeocdn.com") ||
            innerUrl.contains("akfire") ||
            innerUrl.contains("/playlist/av/") ||
            innerUrl.contains("media.m3u8")
        ) return true
    }
    return false
}
