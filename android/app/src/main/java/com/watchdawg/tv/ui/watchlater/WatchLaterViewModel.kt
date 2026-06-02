package com.watchdawg.tv.ui.watchlater

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.watchdawg.tv.data.api.WatchlistItemDto
import com.watchdawg.tv.data.auth.TokenHolder
import com.watchdawg.tv.data.repo.WatchDawgRepository
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/**
 * Loads the Watch Later list from GET /watchlist.
 *
 * Adult content is excluded unconditionally by the backend regardless of
 * token state — the Watch Later button is hidden on locked-channel content
 * at the point of adding, not at the point of displaying.
 *
 * Remove action: DELETE /watchlist/{video_id}. Optimistic update: removes
 * from local list immediately, re-fetches on failure.
 *
 * Re-fetches automatically when the session token changes so the list stays
 * current across lock/unlock transitions.
 */
class WatchLaterViewModel(private val repo: WatchDawgRepository) : ViewModel() {

    data class UiState(
        val loading: Boolean = true,
        val items: List<WatchlistItemDto> = emptyList(),
        val error: String? = null,
        // IDs currently being removed — drives the "Removing…" state on buttons.
        val removingIds: Set<Int> = emptySet(),
    )

    private val _state = MutableStateFlow(UiState())
    val state: StateFlow<UiState> = _state.asStateFlow()

    init {
        refresh()
        viewModelScope.launch {
            TokenHolder.tokenFlow.collect { refresh() }
        }
    }

    fun refresh() {
        viewModelScope.launch {
            _state.value = _state.value.copy(loading = true, error = null)
            repo.getWatchlist()
                .onSuccess { items ->
                    _state.value = UiState(loading = false, items = items)
                }
                .onFailure { e ->
                    _state.value = _state.value.copy(
                        loading = false,
                        error = e.message ?: "Could not load Watch Later list.",
                    )
                }
        }
    }

    /**
     * Remove a video from Watch Later by its video_id.
     * Optimistic: removes from list immediately. Re-fetches on failure.
     */
    fun remove(videoId: Int) {
        if (_state.value.removingIds.contains(videoId)) return
        viewModelScope.launch {
            // Optimistic remove
            _state.value = _state.value.copy(
                items = _state.value.items.filterNot { it.videoId == videoId },
                removingIds = _state.value.removingIds + videoId,
            )
            repo.removeFromWatchlist(videoId)
                .onSuccess {
                    _state.value = _state.value.copy(
                        removingIds = _state.value.removingIds - videoId,
                    )
                }
                .onFailure {
                    // Roll back optimistic remove and show error
                    _state.value = _state.value.copy(
                        removingIds = _state.value.removingIds - videoId,
                    )
                    refresh()
                }
        }
    }
}
