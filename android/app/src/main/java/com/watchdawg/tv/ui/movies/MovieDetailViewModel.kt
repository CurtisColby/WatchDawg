package com.watchdawg.tv.ui.movies

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.watchdawg.tv.data.api.VideoDto
import com.watchdawg.tv.data.repo.WatchDawgRepository
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/**
 * ViewModel for Milestone G: Movie Detail Screen.
 *
 * Receives a [VideoDto] from the navigation argument (passed via QueueHolder
 * or reconstructed from the feed). Fetches watch history to determine resume
 * position. Drives [MovieDetailScreen] state.
 *
 * No backend changes — all data already exists:
 *  - [VideoDto.tmdbPosterUrl]  — TMDb poster art (nullable, fallback to thumbnail)
 *  - [VideoDto.tmdbOverview]   — Description text
 *  - [VideoDto.tmdbYear]       — Release year
 *  - [VideoDto.tmdbRating]     — TMDb vote average
 *  - GET /history              — resume position, completion status
 */
class MovieDetailViewModel(
    private val repo: WatchDawgRepository,
) : ViewModel() {

    data class UiState(
        val loading: Boolean = true,
        /** Resume position in milliseconds. 0 = start from beginning. */
        val resumePositionMs: Long = 0L,
        /** True when position > 95% of duration — drives watched overlay. */
        val isWatched: Boolean = false,
        /** Human-readable resume time label e.g. "1:23:45". Empty = no banner. */
        val resumeLabel: String = "",
        val error: String? = null,
    )

    private val _state = MutableStateFlow(UiState())
    val state: StateFlow<UiState> = _state.asStateFlow()

    /**
     * Load watch history for [videoId].
     *
     * Called from [MovieDetailScreen] via LaunchedEffect(videoId).
     * A missing history record is not an error — it means the movie
     * has never been watched; resume position is left at 0.
     */
    fun loadHistory(videoId: Int) {
        _state.value = UiState(loading = true)
        viewModelScope.launch {
            val result = repo.getHistory(limit = 200)
            result.fold(
                onSuccess = { history ->
                    val entry = history.firstOrNull { it.videoId == videoId }
                    if (entry == null) {
                        _state.value = UiState(loading = false)
                        return@fold
                    }
                    val positionMs = ((entry.positionSeconds ?: 0f).toLong() * 1000L)
                    val hasRealProgress = (entry.progressPct ?: 0f) > 2f
                    val label = if (hasRealProgress && !entry.completed) {
                        val totalSec = (entry.positionSeconds ?: 0f).toLong()
                        val h = totalSec / 3600
                        val m = (totalSec % 3600) / 60
                        val s = totalSec % 60
                        if (h > 0) "%d:%02d:%02d".format(h, m, s)
                        else "%d:%02d".format(m, s)
                    } else ""

                    _state.value = UiState(
                        loading = false,
                        resumePositionMs = if (hasRealProgress && !entry.completed) positionMs else 0L,
                        isWatched = entry.completed,
                        resumeLabel = label,
                    )
                },
                onFailure = {
                    // History fetch failure is non-fatal — fall back to no resume.
                    _state.value = UiState(loading = false)
                },
            )
        }
    }
}
