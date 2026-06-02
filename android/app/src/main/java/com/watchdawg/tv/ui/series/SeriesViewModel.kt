package com.watchdawg.tv.ui.series

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.watchdawg.tv.data.api.EpisodesResponse
import com.watchdawg.tv.data.api.SeriesItemDto
import com.watchdawg.tv.data.api.VideoDto
import com.watchdawg.tv.data.repo.WatchDawgRepository
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch

/**
 * ViewModel for TV series two-level navigation.
 *
 * Drives two screens:
 *  - [SeriesScreen]      — grid of series cards (one per TV-category channel)
 *  - [EpisodeListScreen] — sortable episode list for a selected series
 *
 * Milestone R-2: loadSeries() gains optional [genreTag] param.
 *   When non-null the backend filters to channels whose genre_tags field
 *   contains the tag. Null = return all TV series (backward compat).
 *   Called by TVScreen whenever the user selects a genre pill.
 */
class SeriesViewModel(private val repo: WatchDawgRepository) : ViewModel() {

    // ── Sort mode ─────────────────────────────────────────────────────────────

    enum class EpisodeSort {
        NEWEST_FIRST,
        TITLE_ASC,
    }

    private val _episodeSort = MutableStateFlow(EpisodeSort.NEWEST_FIRST)
    val episodeSort: StateFlow<EpisodeSort> = _episodeSort

    fun toggleSort() {
        _episodeSort.value = when (_episodeSort.value) {
            EpisodeSort.NEWEST_FIRST -> EpisodeSort.TITLE_ASC
            EpisodeSort.TITLE_ASC   -> EpisodeSort.NEWEST_FIRST
        }
    }

    // ── Queue payload ─────────────────────────────────────────────────────────

    data class QueuePayload(val ids: List<Int>, val startIndex: Int)

    private val _pendingQueue = MutableStateFlow<QueuePayload?>(null)
    val pendingQueue: StateFlow<QueuePayload?> = _pendingQueue

    fun clearPendingQueue() {
        _pendingQueue.value = null
    }

    // ── Series list state ─────────────────────────────────────────────────────

    sealed class SeriesState {
        object Loading : SeriesState()
        data class Success(val items: List<SeriesItemDto>) : SeriesState()
        data class Error(val message: String) : SeriesState()
    }

    private val _seriesState = MutableStateFlow<SeriesState>(SeriesState.Loading)
    val seriesState: StateFlow<SeriesState> = _seriesState

    // ── Episode list state ────────────────────────────────────────────────────

    sealed class EpisodeState {
        object Idle : EpisodeState()
        object Loading : EpisodeState()
        data class Success(val data: EpisodesResponse) : EpisodeState()
        data class Error(val message: String) : EpisodeState()
    }

    private val _episodeState = MutableStateFlow<EpisodeState>(EpisodeState.Idle)
    val episodeState: StateFlow<EpisodeState> = _episodeState

    // ── TV-level queue loading flag ───────────────────────────────────────────

    private val _tvQueueLoading = MutableStateFlow(false)
    val tvQueueLoading: StateFlow<Boolean> = _tvQueueLoading

    // ── Actions ───────────────────────────────────────────────────────────────

    /**
     * Load (or reload) the series grid.
     *
     * [genreTag] — when non-null, only channels whose genre_tags contains this
     * tag are returned. Null returns all TV series. Called by TVScreen on
     * initial load and whenever the user changes the genre pill selection.
     */
    fun loadSeries(genreTag: String? = null) {
        viewModelScope.launch {
            _seriesState.value = SeriesState.Loading
            repo.fetchSeries(genreTag = genreTag)
                .onSuccess { items ->
                    _seriesState.value = SeriesState.Success(items)
                }
                .onFailure { err ->
                    _seriesState.value = SeriesState.Error(
                        err.message ?: "Failed to load series"
                    )
                }
        }
    }

    /**
     * Load the episode list for a given channel.
     * Resets sort to NEWEST_FIRST on each fresh channel load.
     */
    fun loadEpisodes(channelId: Int) {
        _episodeSort.value = EpisodeSort.NEWEST_FIRST
        viewModelScope.launch {
            _episodeState.value = EpisodeState.Loading
            repo.fetchEpisodes(channelId)
                .onSuccess { response ->
                    _episodeState.value = EpisodeState.Success(response)
                }
                .onFailure { err ->
                    _episodeState.value = EpisodeState.Error(
                        err.message ?: "Failed to load episodes"
                    )
                }
        }
    }

    fun clearEpisodes() {
        _episodeState.value = EpisodeState.Idle
    }

    // ── Sort helper ───────────────────────────────────────────────────────────

    fun sorted(episodes: List<VideoDto>): List<VideoDto> = when (_episodeSort.value) {
        EpisodeSort.NEWEST_FIRST ->
            episodes.sortedByDescending { it.createdAt ?: "" }
        EpisodeSort.TITLE_ASC ->
            episodes.sortedWith(
                compareBy(nullsLast()) { it.title?.takeIf { t -> t.isNotBlank() } }
            )
    }

    // ── Play All / Shuffle All — current series (in-memory) ──────────────────

    fun playEpisodes() {
        val episodes = currentEpisodes() ?: return
        val ids = sorted(episodes).map { it.id }
        if (ids.isNotEmpty()) _pendingQueue.value = QueuePayload(ids, 0)
    }

    fun shuffleEpisodes() {
        val episodes = currentEpisodes() ?: return
        val ids = episodes.map { it.id }.shuffled()
        if (ids.isNotEmpty()) _pendingQueue.value = QueuePayload(ids, 0)
    }

    private fun currentEpisodes(): List<VideoDto>? =
        (_episodeState.value as? EpisodeState.Success)?.data?.episodes

    // ── Play All / Shuffle All — all TV episodes across all series ────────────

    /**
     * Play all TV-category episodes matching the active genre pill.
     * [genreTag] null = All (no filter). Calls GET /feed/ids?category=tv&genre_tag=X.
     */
    fun playAllTv(genreTag: String? = null) {
        viewModelScope.launch {
            _tvQueueLoading.value = true
            repo.getFeedIds(category = "tv", genreTag = genreTag)
                .onSuccess { resp ->
                    val ids = resp.ids.map { it.id }
                    _tvQueueLoading.value = false
                    if (ids.isNotEmpty()) _pendingQueue.value = QueuePayload(ids, 0)
                }
                .onFailure { _tvQueueLoading.value = false }
        }
    }

    fun shuffleAllTv(genreTag: String? = null) {
        viewModelScope.launch {
            _tvQueueLoading.value = true
            repo.getFeedIds(category = "tv", genreTag = genreTag)
                .onSuccess { resp ->
                    val ids = resp.ids.map { it.id }.shuffled()
                    _tvQueueLoading.value = false
                    if (ids.isNotEmpty()) _pendingQueue.value = QueuePayload(ids, 0)
                }
                .onFailure { _tvQueueLoading.value = false }
        }
    }
}
