package com.watchdawg.tv.ui.continuewatching

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.watchdawg.tv.data.api.HistoryItemDto
import com.watchdawg.tv.data.auth.TokenHolder
import com.watchdawg.tv.data.repo.WatchDawgRepository
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/**
 * Loads the Continue Watching list from GET /history.
 *
 * The backend unconditionally excludes locked and adult-category content
 * from this endpoint regardless of token — safe to display without PIN.
 * Re-fetches automatically when the session token changes (unlock/lock).
 *
 * Milestone E: removeItem() — DELETE /history/{id} with optimistic update.
 *
 * R-4: clearAll() — deletes all current history items sequentially.
 *   Sets clearing=true while in progress so the UI can show the button
 *   in a disabled/spinner state. Items are cleared optimistically from the
 *   list immediately, then each DELETE fires in sequence.
 */
class ContinueWatchingViewModel(private val repo: WatchDawgRepository) : ViewModel() {

    data class UiState(
        val loading: Boolean = true,
        val items: List<HistoryItemDto> = emptyList(),
        val error: String? = null,
        val removingIds: Set<Int> = emptySet(),
        val clearing: Boolean = false,
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
            repo.getHistory(limit = 50)
                .onSuccess { items ->
                    _state.value = UiState(loading = false, items = items)
                }
                .onFailure { e ->
                    _state.value = _state.value.copy(
                        loading = false,
                        error = e.message ?: "Could not load watch history.",
                    )
                }
        }
    }

    /**
     * Remove a single video from Continue Watching by its video_id.
     * Optimistic: removes from list immediately, re-fetches on failure.
     */
    fun removeItem(videoId: Int) {
        if (_state.value.removingIds.contains(videoId)) return
        viewModelScope.launch {
            _state.value = _state.value.copy(
                items       = _state.value.items.filterNot { it.videoId == videoId },
                removingIds = _state.value.removingIds + videoId,
            )
            repo.deleteHistory(videoId)
                .onSuccess {
                    _state.value = _state.value.copy(
                        removingIds = _state.value.removingIds - videoId,
                    )
                }
                .onFailure {
                    _state.value = _state.value.copy(
                        removingIds = _state.value.removingIds - videoId,
                    )
                    refresh()
                }
        }
    }

    /**
     * Clear all Continue Watching entries.
     *
     * Optimistically empties the list immediately, then fires DELETE /history/{id}
     * for each item sequentially. Sets clearing=true for the duration so the
     * Clear All button can show a disabled/spinner state.
     *
     * Only clears what is currently visible — locked/adult content is never
     * in this list (excluded server-side unconditionally).
     */
    fun clearAll() {
        val toDelete = _state.value.items.map { it.videoId }
        if (toDelete.isEmpty()) return
        viewModelScope.launch {
            _state.value = _state.value.copy(clearing = true, items = emptyList())
            toDelete.forEach { videoId ->
                repo.deleteHistory(videoId)
            }
            _state.value = _state.value.copy(clearing = false)
            refresh()
        }
    }
}
