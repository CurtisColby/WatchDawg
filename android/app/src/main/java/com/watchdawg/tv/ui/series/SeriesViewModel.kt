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
 * ViewModel for the Milestone F TV series two-level navigation.
 *
 * Drives two screens:
 *  - [SeriesScreen]      — grid of series cards (one per TV-category channel)
 *  - [EpisodeListScreen] — sortable episode list for a selected series
 *
 * Milestone F polish additions:
 *  - [EpisodeSort] — two sort modes toggled by the user in EpisodeListScreen.
 *    Default is NEWEST_FIRST so fresh scrapes surface at the top automatically.
 *    Client-side sort — no extra API call, instant response.
 *  - [playEpisodes] / [shuffleEpisodes] — Play All and Shuffle All scoped to
 *    the current series episode list (already in memory, zero network cost).
 *  - [playAllTv] / [shuffleAllTv] — Play All and Shuffle All across every TV
 *    episode from all series. Calls GET /feed/ids?category=tv.
 *  - [QueuePayload] — mirrors FeedViewModel's pattern so MainActivity can use
 *    the same LaunchedEffect(pendingQueue) pattern for queue handoff.
 *
 * Thread safety: all state mutations on main thread via viewModelScope.
 * Network I/O dispatched to Dispatchers.IO inside WatchDawgRepository.io.
 */
class SeriesViewModel(private val repo: WatchDawgRepository) : ViewModel() {

    // ── Sort mode ─────────────────────────────────────────────────────────────

    enum class EpisodeSort {
        /** Newest scraped episode first — default, surfaces fresh scrapes. */
        NEWEST_FIRST,
        /** Title A → Z — useful for numbered episode titles (S01E01 etc.). */
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

    // ── Queue payload — mirrors FeedViewModel.QueuePayload ───────────────────

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

    // ── TV-level queue loading flag (Play All / Shuffle All all TV) ───────────

    private val _tvQueueLoading = MutableStateFlow(false)
    val tvQueueLoading: StateFlow<Boolean> = _tvQueueLoading

    // ── Actions ───────────────────────────────────────────────────────────────

    /**
     * Load (or reload) the series grid.
     * Sets state to Loading before the network call so the UI shows a spinner
     * on both initial load and manual refresh.
     */
    fun loadSeries() {
        viewModelScope.launch {
            _seriesState.value = SeriesState.Loading
            repo.fetchSeries()
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
     * Resets episode state to Loading immediately — never shows stale data.
     * Also resets sort to NEWEST_FIRST on each fresh channel load so the
     * default is always "new episodes at top" when entering a series.
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

    /**
     * Reset episode state back to Idle.
     * Called via DisposableEffect when the user navigates back to SeriesScreen.
     */
    fun clearEpisodes() {
        _episodeState.value = EpisodeState.Idle
    }

    // ── Sort helper — applied in the UI ──────────────────────────────────────

    /**
     * Apply the current sort mode to a raw episode list.
     *
     * Called inside EpisodeListScreen with the episodes from EpisodeState.Success.
     * Client-side sort — no network call, instant.
     *
     * NEWEST_FIRST: sort by createdAt descending. The backend returns ISO-8601
     *   strings ("2024-03-15T10:23:00") which sort correctly as plain strings
     *   in reverse order.
     * TITLE_ASC: sort by title ascending, null/blank titles sorted last.
     */
    fun sorted(episodes: List<VideoDto>): List<VideoDto> = when (_episodeSort.value) {
        EpisodeSort.NEWEST_FIRST ->
            episodes.sortedByDescending { it.createdAt ?: "" }
        EpisodeSort.TITLE_ASC ->
            episodes.sortedWith(
                compareBy(nullsLast()) { it.title?.takeIf { t -> t.isNotBlank() } }
            )
    }

    // ── Play All / Shuffle All — scoped to current series (in-memory) ─────────

    /**
     * Play all episodes in the current sort order.
     * IDs come from the already-loaded episode list — zero network cost.
     */
    fun playEpisodes() {
        val episodes = currentEpisodes() ?: return
        val ids = sorted(episodes).map { it.id }
        if (ids.isNotEmpty()) _pendingQueue.value = QueuePayload(ids, 0)
    }

    /**
     * Shuffle all episodes in the current series.
     * IDs come from the already-loaded episode list — zero network cost.
     */
    fun shuffleEpisodes() {
        val episodes = currentEpisodes() ?: return
        val ids = episodes.map { it.id }.shuffled()
        if (ids.isNotEmpty()) _pendingQueue.value = QueuePayload(ids, 0)
    }

    private fun currentEpisodes(): List<VideoDto>? {
        return (_episodeState.value as? EpisodeState.Success)?.data?.episodes
    }

    // ── Play All / Shuffle All — all TV episodes across all series ────────────

    /**
     * Play all TV-category episodes across every series in order.
     * Calls GET /feed/ids?category=tv — one network request, then queues.
     */
    fun playAllTv() {
        viewModelScope.launch {
            _tvQueueLoading.value = true
            repo.getFeedIds(category = "tv")
                .onSuccess { resp ->
                    val ids = resp.ids.map { it.id }
                    _tvQueueLoading.value = false
                    if (ids.isNotEmpty()) _pendingQueue.value = QueuePayload(ids, 0)
                }
                .onFailure {
                    _tvQueueLoading.value = false
                }
        }
    }

    /**
     * Shuffle all TV-category episodes across every series.
     * Calls GET /feed/ids?category=tv — one network request, then shuffles.
     */
    fun shuffleAllTv() {
        viewModelScope.launch {
            _tvQueueLoading.value = true
            repo.getFeedIds(category = "tv")
                .onSuccess { resp ->
                    val ids = resp.ids.map { it.id }.shuffled()
                    _tvQueueLoading.value = false
                    if (ids.isNotEmpty()) _pendingQueue.value = QueuePayload(ids, 0)
                }
                .onFailure {
                    _tvQueueLoading.value = false
                }
        }
    }
}
