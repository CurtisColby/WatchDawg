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
 * Long-pressing a card triggers removal. The item disappears immediately;
 * on failure it re-appears via a full refresh.
 */
class ContinueWatchingViewModel(private val repo: WatchDawgRepository) : ViewModel() {

    data class UiState(
        val loading: Boolean = true,
        val items: List<HistoryItemDto> = emptyList(),
        val error: String? = null,
        // IDs currently being removed — drives confirmation state on cards
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
     * Remove a video from Continue Watching by its video_id.
     * Optimistic: removes from list immediately, re-fetches on failure.
     */
    fun removeItem(videoId: Int) {
        if (_state.value.removingIds.contains(videoId)) return
        viewModelScope.launch {
            _state.value = _state.value.copy(
                items = _state.value.items.filterNot { it.videoId == videoId },
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
}
