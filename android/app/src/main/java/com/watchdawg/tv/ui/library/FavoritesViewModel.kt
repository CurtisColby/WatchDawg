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
 * Drives the Favorites screen.
 *
 * Session 25: playAll/shuffleAll emit video IDs (not stream URLs) so every
 * play goes through the resolve path — Vimeo CDN tokens are always fresh.
 * Downloaded files (download_status=complete) are excluded from the ID queue
 * because those play via local stream_url directly, no resolve needed.
 *
 * NOTE: This file replaces FavoritesViewModel in LibraryViewModels.kt.
 * After deploying this file, remove the FavoritesViewModel class from
 * LibraryViewModels.kt (leave LibraryViewModel intact in that file).
 */
class FavoritesViewModel(private val repo: WatchDawgRepository) : ViewModel() {

    data class UiState(
        val loading: Boolean = true,
        val favorites: List<FavoriteDto> = emptyList(),
        val error: String? = null,
        val removingIds: Set<Int> = emptySet(),
        val pendingQueue: List<Int>? = null,
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
                    _state.value = _state.value.copy(loading = false, favorites = list)
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

    fun retry(favoriteId: Int) {
        viewModelScope.launch {
            // Correct repo method name: retryFavorite
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
