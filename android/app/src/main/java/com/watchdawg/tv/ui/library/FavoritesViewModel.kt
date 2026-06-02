package com.watchdawg.tv.ui.library

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.watchdawg.tv.data.api.FavoriteDto
import com.watchdawg.tv.data.auth.TokenHolder
import com.watchdawg.tv.data.repo.WatchDawgRepository
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/**
 * Drives the Favorites screen — R-4 update.
 *
 * Content model change (R-4):
 *   Favorites now shows ONLY unlocked-channel favorites regardless of PIN state.
 *   Locked-channel favorites live exclusively on the Adult screen → Favorites pill.
 *   This is a client-side filter on channelLocked=false — the backend still
 *   returns all favorites the token permits, we just route locked ones elsewhere.
 *
 * Token awareness:
 *   Still subscribes to TokenHolder.tokenFlow and refreshes on lock/unlock.
 *   When locked, backend excludes locked-channel favorites from the response
 *   entirely (server-side gate unchanged). When unlocked, backend returns all —
 *   client filters to channelLocked=false for this screen.
 *
 * clearAll (R-4):
 *   Deletes all currently visible (unlocked-channel) favorites one by one via
 *   DELETE /favorite/{id}. Physical files are deleted server-side per existing
 *   favorite.py logic. Locked-channel favorites are never touched by clearAll
 *   on this screen — they are managed from the Adult screen.
 *
 * playAll / shuffleAll:
 *   Emit video IDs (not stream URLs) so every play goes through the resolve
 *   path — Vimeo CDN tokens are always fresh. Downloaded files (download_status
 *   = complete) are excluded from the ID queue because those play via local
 *   stream_url directly, no resolve needed.
 */
class FavoritesViewModel(private val repo: WatchDawgRepository) : ViewModel() {

    data class UiState(
        val loading: Boolean = true,
        val favorites: List<FavoriteDto> = emptyList(),
        val error: String? = null,
        val removingIds: Set<Int> = emptySet(),
        val pendingQueue: List<Int>? = null,
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
            repo.getFavorites()
                .onSuccess { list ->
                    // R-4: filter to unlocked-channel favorites only.
                    // Locked-channel favorites are routed to Adult → Favorites pill.
                    val unlocked = list.filter { !it.channelLocked }
                    _state.value = _state.value.copy(loading = false, favorites = unlocked)
                }
                .onFailure { e ->
                    _state.value = _state.value.copy(
                        loading = false,
                        error   = e.message ?: "Could not load favorites.",
                    )
                }
        }
    }

    fun remove(favoriteId: Int) {
        if (_state.value.removingIds.contains(favoriteId)) return
        viewModelScope.launch {
            _state.value = _state.value.copy(
                favorites   = _state.value.favorites.filterNot { it.id == favoriteId },
                removingIds = _state.value.removingIds + favoriteId,
            )
            repo.removeFavorite(favoriteId)
                .onSuccess {
                    _state.value = _state.value.copy(
                        removingIds = _state.value.removingIds - favoriteId,
                    )
                }
                .onFailure {
                    _state.value = _state.value.copy(
                        removingIds = _state.value.removingIds - favoriteId,
                    )
                    refresh()
                }
        }
    }

    /**
     * Clear all currently visible (unlocked-channel) favorites.
     *
     * Deletes each favorite sequentially via DELETE /favorite/{id}.
     * The backend deletes the physical downloaded file when applicable.
     * Sets clearing=true while in progress so the UI can show a spinner.
     * Locked-channel favorites on the Adult screen are not affected.
     */
    fun clearAll() {
        val toDelete = _state.value.favorites.mapNotNull { it.id }
        if (toDelete.isEmpty()) return
        viewModelScope.launch {
            _state.value = _state.value.copy(clearing = true, favorites = emptyList())
            toDelete.forEach { id ->
                repo.removeFavorite(id)
            }
            _state.value = _state.value.copy(clearing = false)
            refresh()
        }
    }

    fun retry(favoriteId: Int) {
        viewModelScope.launch {
            repo.retryFavorite(favoriteId).onSuccess { refresh() }
        }
    }

    /**
     * IDs to pass to the resolve-based player.
     * Excludes downloaded files — those play via local stream_url directly.
     */
    private fun resolvableIds(): List<Int> =
        _state.value.favorites
            .filter { fav ->
                fav.downloadStatus != "complete" &&
                fav.videoId != null &&
                fav.videoId > 0
            }
            .mapNotNull { it.videoId }

    fun playAll() {
        val ids = resolvableIds()
        if (ids.isNotEmpty()) _state.value = _state.value.copy(pendingQueue = ids)
    }

    fun shuffleAll() {
        val ids = resolvableIds().shuffled()
        if (ids.isNotEmpty()) _state.value = _state.value.copy(pendingQueue = ids)
    }

    fun clearPendingQueue() {
        _state.value = _state.value.copy(pendingQueue = null)
    }
}
