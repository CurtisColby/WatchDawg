package com.watchdawg.tv.ui.player

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.watchdawg.tv.data.api.ChannelDto
import com.watchdawg.tv.data.prefs.QueueHolder
import com.watchdawg.tv.data.prefs.ResumeState
import com.watchdawg.tv.data.repo.WatchDawgRepository
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/**
 * Owns playback orchestration.
 *
 * Milestone E additions:
 *   - Watch history writes via startHistoryLoop() / writeHistoryCompletion()
 *   - Speed control via setSpeed() — resets to 1.0f on every new video
 *   - saveResume() now passes hlsMode and isLockedSource to ResumeState so
 *     resume correctly re-resolves with the right client param, and locked/adult
 *     content is never persisted to disk.
 *
 * Speed reset guarantee: every code path that emits a new playToken explicitly
 * sets playbackSpeed = 1.0f. The token-refresh path does NOT reset speed since
 * that is a mid-video CDN URL swap, not a new video.
 *
 * Session 33 — Smart Shuffle:
 *   resolveCurrent() calls QueueHolder.onVideoPlayed(videoId) each time a new
 *   video successfully resolves. The callback is set by MusicViewModel /
 *   AdultViewModel before queuing a Smart Shuffle so they can mark the video
 *   as played in their in-memory playedIds Set. For all other queue types the
 *   callback is null and this call is a no-op.
 */
class PlayerViewModel(
    private val repo: WatchDawgRepository,
    private val resumeState: ResumeState,
) : ViewModel() {

    data class UiState(
        val loading: Boolean = true,
        val title: String = "",
        val artist: String = "",
        val streamUrl: String? = null,
        val audioUrl: String? = null,
        val videoId: Int? = null,
        val thumbnailUrl: String? = null,
        val startPositionMs: Long = 0L,
        val index: Int = 0,
        val queueSize: Int = 0,
        val error: String? = null,
        val ended: Boolean = false,
        val playToken: Int = 0,
        val hlsMode: Boolean = false,
        // Milestone E: resets to 1.0f on every new playToken
        val playbackSpeed: Float = 1.0f,
    )

    private var idQueue: List<Int> = emptyList()
    private var urlQueue: List<String> = emptyList()
    private var index: Int = 0
    private var pendingStartMs: Long = 0L
    private var hlsMode: Boolean = false
    // Milestone E: track whether the current channel is locked so saveResume
    // can refuse to persist locked/adult content to SharedPreferences.
    private var isLockedSource: Boolean = false

    private enum class Mode { RESOLVE, DIRECT_SINGLE, DIRECT_QUEUE }
    private var mode: Mode = Mode.RESOLVE

    private val _state = MutableStateFlow(UiState())
    val state: StateFlow<UiState> = _state.asStateFlow()

    private var tokenRefreshJob: Job? = null
    private val tokenRefreshDelayMs = 3_600_000L * 3 + 1_800_000L  // 3h 30m

    // ── Milestone E: history write loop ──────────────────────────────────────
    private var historyWriteJob: Job? = null
    private val historyIntervalMs = 10_000L

    fun startHistoryLoop(
        videoId: Int,
        playerPositionMs: () -> Long,
        playerDurationMs: () -> Long,
    ) {
        historyWriteJob?.cancel()
        if (!hlsMode || mode != Mode.RESOLVE) return
        historyWriteJob = viewModelScope.launch {
            while (true) {
                delay(historyIntervalMs)
                val posMs = playerPositionMs()
                val durMs = playerDurationMs()
                if (posMs > 0L) {
                    repo.postHistory(
                        videoId = videoId,
                        positionSeconds = posMs / 1000f,
                        durationSeconds = if (durMs > 0L) durMs / 1000f else null,
                    )
                    // Mirror to ResumeState — only if not a locked source
                    if (!isLockedSource) {
                        resumeState.save(
                            videoId = videoId,
                            positionMs = posMs,
                            queue = idQueue,
                            index = index,
                            title = _state.value.title,
                            hlsMode = hlsMode,
                            isLockedSource = false,
                        )
                    }
                }
            }
        }
    }

    fun stopHistoryLoop() {
        historyWriteJob?.cancel()
        historyWriteJob = null
    }

    /**
     * Write a single "started" history record for split-stream (non-HLS) playback.
     *
     * Split-stream videos can't seek, so we don't track position — but we do want
     * them to appear in Continue Watching so the user knows they watched them.
     * Called from PlayerScreen when isPlaying becomes true and hlsMode is false.
     *
     * Posts position_seconds = 0 with the current duration so the backend creates
     * a history record. The video appears in Continue Watching with no progress bar
     * (position = 0). On resume it starts from the beginning, which is correct for
     * non-seekable split-stream content.
     *
     * Not called for locked sources — isLockedSource guards this function directly.
     */
    fun writeHistoryStarted(playerDurationMs: () -> Long) {
        if (mode != Mode.RESOLVE || hlsMode) return  // HLS loop handles its own writes
        if (isLockedSource) return                   // never write history for locked/adult content
        val videoId = _state.value.videoId ?: return
        viewModelScope.launch {
            val durMs = playerDurationMs()
            repo.postHistory(
                videoId = videoId,
                positionSeconds = 0f,
                durationSeconds = if (durMs > 0L) durMs / 1000f else null,
            )
        }
    }

    fun writeHistoryCompletion(positionMs: Long, durationMs: Long) {
        if (mode != Mode.RESOLVE) return
        if (isLockedSource) return  // never write history for locked/adult content
        val videoId = _state.value.videoId ?: return
        viewModelScope.launch {
            repo.postHistory(
                videoId = videoId,
                positionSeconds = positionMs / 1000f,
                durationSeconds = if (durationMs > 0L) durationMs / 1000f else null,
            )
        }
    }

    // ── Milestone E: speed control ────────────────────────────────────────────

    fun setSpeed(speed: Float) {
        _state.value = _state.value.copy(playbackSpeed = speed)
    }

    // ── Play entry points ─────────────────────────────────────────────────────

    fun playSingle(videoId: Int, isHlsMode: Boolean = false, lockedSource: Boolean = false) {
        isLockedSource = lockedSource
        start(videoId, listOf(videoId), 0, 0L, isHlsMode)
    }

    fun start(
        videoId: Int,
        queueIds: List<Int>,
        startIndex: Int,
        startMs: Long = 0L,
        isHlsMode: Boolean = false,
        lockedSource: Boolean = false,
    ) {
        mode = Mode.RESOLVE
        hlsMode = isHlsMode
        isLockedSource = lockedSource
        idQueue = if (queueIds.isNotEmpty()) queueIds else listOf(videoId)
        urlQueue = emptyList()
        index = startIndex.coerceIn(0, idQueue.lastIndex)
        if (idQueue.getOrNull(index) != videoId) {
            val found = idQueue.indexOf(videoId)
            if (found >= 0) index = found
        }
        pendingStartMs = startMs
        stopHistoryLoop()
        resolveCurrent()
    }

    fun resumeFromMiniPlayer(positionMs: Long) {
        _state.value = _state.value.copy(
            loading = false,
            startPositionMs = positionMs,
            error = null,
            ended = false,
            playbackSpeed = 1.0f,
            playToken = _state.value.playToken + 1,
        )
    }

    fun startDirect(streamUrl: String, title: String) {
        mode = Mode.DIRECT_SINGLE
        idQueue = emptyList()
        urlQueue = listOf(streamUrl)
        index = 0
        pendingStartMs = 0L
        hlsMode = false
        isLockedSource = false
        cancelTokenRefresh()
        stopHistoryLoop()
        emitDirect(streamUrl, title, "")
    }

    fun startDirectQueue(urls: List<String>, startIndex: Int, startMs: Long = 0L) {
        mode = Mode.DIRECT_QUEUE
        idQueue = emptyList()
        urlQueue = urls
        index = startIndex.coerceIn(0, urls.lastIndex)
        // Session 38: accept a start position so EPG can seek to the correct
        // offset into the current slot (wall-clock time minus slot start time).
        pendingStartMs = startMs
        hlsMode = false
        isLockedSource = false
        cancelTokenRefresh()
        stopHistoryLoop()
        playCurrentDirect()
    }

    private fun playCurrentDirect() {
        val url = urlQueue.getOrNull(index) ?: run {
            _state.value = _state.value.copy(loading = false, ended = true)
            return
        }
        // Session 42: use the EPG slot title if available, otherwise fall back to "Now Playing".
        val title = QueueHolder.epgSlotTitle.ifBlank { "Now Playing" }
        emitDirect(url, title, "")
    }

    private fun emitDirect(url: String, title: String, artist: String) {
        _state.value = _state.value.copy(
            loading = false,
            title = title,
            artist = artist,
            streamUrl = url,
            audioUrl = null,
            videoId = null,
            thumbnailUrl = null,
            startPositionMs = pendingStartMs,
            index = index,
            queueSize = if (mode == Mode.DIRECT_QUEUE) urlQueue.size else 1,
            error = null,
            ended = false,
            hlsMode = false,
            playbackSpeed = 1.0f,
            playToken = _state.value.playToken + 1,
        )
        pendingStartMs = 0L
    }

    private fun resolveCurrent(retryForce: Boolean = false) {
        val videoId = idQueue.getOrNull(index) ?: run {
            _state.value = _state.value.copy(loading = false, ended = true)
            return
        }
        _state.value = _state.value.copy(loading = true, error = null, ended = false)
        cancelTokenRefresh()
        stopHistoryLoop()

        val clientParam = if (hlsMode) "browser" else "tv"

        viewModelScope.launch {
            // Session 38 — Resume fix: if no explicit startMs was passed by the caller
            // (pendingStartMs == 0L), check the backend watch history for this video.
            // EPG launches always set pendingStartMs > 0 (or use epgSlotStartTimeUtc
            // below), so history lookup is naturally skipped for EPG content.
            if (pendingStartMs == 0L && !isLockedSource && QueueHolder.epgSlotStartTimeUtc == null) {
                repo.getHistory(limit = 200).onSuccess { items ->
                    val record = items.firstOrNull { it.videoId == videoId }
                    if (record != null && !record.completed) {
                        val posMs = ((record.positionSeconds ?: 0f) * 1000f).toLong()
                        if (posMs > 5_000L) {
                            pendingStartMs = posMs
                        }
                    }
                }
            }

            when (val outcome = repo.resolve(videoId, force = retryForce, client = clientParam)) {
                is WatchDawgRepository.ResolveOutcome.Ok -> {
                    val data = outcome.data
                    val url = data.streamUrl
                    if (url.isNullOrBlank()) {
                        advanceAfterFailure()
                    } else {
                        // Session 40 — EPG live offset recomputation:
                        // If this was an EPG launch, recompute (now - slotStartTime)
                        // right after resolve finishes. This corrects for the 10-20s
                        // yt-dlp delay that made the offset stale at playback time.
                        // With pre-resolution the cache is warm and this runs instantly,
                        // but we recompute anyway as a safety net.
                        val epgStart = QueueHolder.epgSlotStartTimeUtc
                        if (epgStart != null && pendingStartMs >= 0L) {
                            try {
                                val fmt1 = java.time.format.DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss.SSSSSS")
                                val fmt2 = java.time.format.DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss")
                                val slotStart = try {
                                    java.time.LocalDateTime.parse(epgStart.replace("T", " ").substringBefore(".") + ".000000", fmt1)
                                } catch (_: Exception) {
                                    java.time.LocalDateTime.parse(epgStart.replace("T", " ").substringBefore("."), fmt2)
                                }
                                val nowUtc = java.time.LocalDateTime.now(java.time.ZoneOffset.UTC)
                                val offsetSec = java.time.temporal.ChronoUnit.SECONDS.between(slotStart, nowUtc)
                                    .coerceAtLeast(0L)
                                pendingStartMs = offsetSec * 1000L
                                android.util.Log.d("WatchDawg", "EPG offset recomputed after resolve: ${offsetSec}s (slotStart=$epgStart)")
                            } catch (e: Exception) {
                                android.util.Log.w("WatchDawg", "EPG offset recompute failed: $e")
                                // Keep whatever pendingStartMs was set at tap time
                            }
                            QueueHolder.epgSlotStartTimeUtc = null
                        }
                        _state.value = _state.value.copy(
                            loading = false,
                            title = data.title ?: "Now Playing",
                            artist = data.artist ?: "",
                            streamUrl = url,
                            audioUrl = data.audioUrl,
                            videoId = videoId,
                            thumbnailUrl = data.thumbnailUrl,
                            startPositionMs = pendingStartMs,
                            index = index,
                            queueSize = idQueue.size,
                            error = null,
                            ended = false,
                            hlsMode = hlsMode,
                            playbackSpeed = 1.0f,
                            playToken = _state.value.playToken + 1,
                        )
                        pendingStartMs = 0L
                        scheduleTokenRefresh(videoId)

                        // Session 33 — Smart Shuffle: notify the originating ViewModel
                        // that this video is now playing so it can mark it as played in
                        // its in-memory playedIds Set. Null-safe — no-op for all non-smart
                        // queues where onVideoPlayed was not set.
                        QueueHolder.onVideoPlayed?.invoke(videoId)
                    }
                }
                WatchDawgRepository.ResolveOutcome.Unavailable -> advanceAfterFailure()
                is WatchDawgRepository.ResolveOutcome.Error -> {
                    _state.value = _state.value.copy(
                        loading = false,
                        error = outcome.throwable.message ?: "Playback error",
                    )
                }
            }
        }
    }

    private fun scheduleTokenRefresh(videoId: Int) {
        cancelTokenRefresh()
        tokenRefreshJob = viewModelScope.launch {
            delay(tokenRefreshDelayMs)
            val currentVideoId = idQueue.getOrNull(index)
            if (mode != Mode.RESOLVE || currentVideoId != videoId) return@launch
            val clientParam = if (hlsMode) "browser" else "tv"
            when (val outcome = repo.resolve(videoId, force = true, client = clientParam)) {
                is WatchDawgRepository.ResolveOutcome.Ok -> {
                    val url = outcome.data.streamUrl ?: return@launch
                    // Token refresh does NOT reset speed — mid-video CDN swap only
                    _state.value = _state.value.copy(
                        streamUrl = url,
                        audioUrl = outcome.data.audioUrl,
                        startPositionMs = -1L,
                        error = null,
                        playToken = _state.value.playToken + 1,
                    )
                    scheduleTokenRefresh(videoId)
                }
                else -> {}
            }
        }
    }

    private fun cancelTokenRefresh() {
        tokenRefreshJob?.cancel()
        tokenRefreshJob = null
    }

    fun onPlaybackError() {
        if (mode == Mode.RESOLVE) {
            viewModelScope.launch {
                val videoId = idQueue.getOrNull(index)
                if (videoId == null) { next(); return@launch }
                val clientParam = if (hlsMode) "browser" else "tv"
                when (val outcome = repo.resolve(videoId, force = true, client = clientParam)) {
                    is WatchDawgRepository.ResolveOutcome.Ok -> {
                        val url = outcome.data.streamUrl
                        if (url.isNullOrBlank()) advanceAfterFailure()
                        else {
                            _state.value = _state.value.copy(
                                loading = false,
                                streamUrl = url,
                                audioUrl = outcome.data.audioUrl,
                                videoId = videoId,
                                startPositionMs = 0L,
                                error = null,
                                playbackSpeed = 1.0f,
                                playToken = _state.value.playToken + 1,
                            )
                            scheduleTokenRefresh(videoId)
                        }
                    }
                    else -> advanceAfterFailure()
                }
            }
        } else {
            next()
        }
    }

    private fun advanceAfterFailure() {
        val qSize = if (mode == Mode.RESOLVE) idQueue.size else urlQueue.size
        if (index < qSize - 1) {
            index += 1
            pendingStartMs = 0L
            stopHistoryLoop()
            if (mode == Mode.RESOLVE) resolveCurrent() else playCurrentDirect()
        } else {
            _state.value = _state.value.copy(loading = false, ended = true)
        }
    }

    fun next() {
        val qSize = if (mode == Mode.RESOLVE) idQueue.size else urlQueue.size
        if (index < qSize - 1) {
            index += 1
            pendingStartMs = 0L
            stopHistoryLoop()
            if (mode == Mode.RESOLVE) resolveCurrent() else playCurrentDirect()
        } else {
            _state.value = _state.value.copy(ended = true)
        }
    }

    fun previous() {
        if (index > 0) {
            index -= 1
            pendingStartMs = 0L
            stopHistoryLoop()
            if (mode == Mode.RESOLVE) resolveCurrent() else playCurrentDirect()
        }
    }

    fun onEnded() = next()

    fun skipCurrent() {
        if (mode != Mode.RESOLVE) { next(); return }
        val videoId = idQueue.getOrNull(index) ?: return
        viewModelScope.launch {
            repo.skip(videoId)
            val newQueue = idQueue.toMutableList().also { it.removeAt(index) }
            idQueue = newQueue
            if (idQueue.isEmpty()) {
                _state.value = _state.value.copy(ended = true)
            } else {
                if (index > idQueue.lastIndex) index = idQueue.lastIndex
                stopHistoryLoop()
                resolveCurrent()
            }
        }
    }

    fun favoriteCurrent() {
        val videoId = idQueue.getOrNull(index) ?: return
        viewModelScope.launch { repo.bookmark(videoId) }
    }

    fun saveCurrent() {
        if (mode != Mode.RESOLVE) return
        val videoId = idQueue.getOrNull(index) ?: return
        viewModelScope.launch { repo.favorite(videoId) }
    }

    /**
     * Persist resume position to SharedPreferences.
     * Passes hlsMode so the banner can re-resolve with the correct client param.
     * Passes isLockedSource so locked/adult content is never written to disk.
     */
    fun saveResume(positionMs: Long) {
        val videoId = idQueue.getOrNull(index) ?: return
        resumeState.save(
            videoId = videoId,
            positionMs = positionMs,
            queue = idQueue,
            index = index,
            title = _state.value.title,
            hlsMode = hlsMode,
            isLockedSource = isLockedSource,
        )
    }

    fun clearResume() = resumeState.clear()

    override fun onCleared() {
        super.onCleared()
        stopHistoryLoop()
        cancelTokenRefresh()
    }
}
