package com.watchdawg.tv.ui.music

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.watchdawg.tv.data.api.VideoDto
import com.watchdawg.tv.data.repo.WatchDawgRepository
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch

/**
 * ViewModel for MusicScreen — Milestone R-4.
 *
 * Mirrors MoviesViewModel exactly with category = "music".
 *
 * Owns three pieces of state:
 *  1. [musicState]    — flat list of music VideoDto objects from GET /feed?category=music.
 *  2. [genreState]    — distinct genre tags from GET /feed/genres?category=music.
 *                       Drives the pill bar (e.g. "Hot", "Rock", "70s", "Classic Rock").
 *                       Empty list = pill bar shows "All" only.
 *  3. [selectedGenre] — active genre pill. Null = "All" (no filter).
 *
 * Loading flow:
 *   loadGenres() fires once on first composition.
 *   loadMusic(genreTag) fires on every genre pill selection change,
 *   including initial load (selectedGenre starts null = All).
 *
 * Play All / Shuffle All:
 *   Uses GET /feed/ids?category=music&genre_tag=X via repo.getFeedIds().
 *   Always fetches the full ID list from the backend — Shuffle All shuffles
 *   ALL matching videos in the database, not just what is visible on screen.
 */
class MusicViewModel(private val repo: WatchDawgRepository) : ViewModel() {

    // ── Music list state ──────────────────────────────────────────────────────

    sealed class MusicState {
        object Loading : MusicState()
        data class Ready(val videos: List<VideoDto>) : MusicState()
        data class Error(val message: String) : MusicState()
    }

    private val _musicState = MutableStateFlow<MusicState>(MusicState.Loading)
    val musicState: StateFlow<MusicState> = _musicState

    // ── Genre pill state ──────────────────────────────────────────────────────

    sealed class GenreState {
        object Loading : GenreState()
        data class Ready(val tags: List<String>) : GenreState()
    }

    private val _genreState = MutableStateFlow<GenreState>(GenreState.Loading)
    val genreState: StateFlow<GenreState> = _genreState

    private val _selectedGenre = MutableStateFlow<String?>(null)
    val selectedGenre: StateFlow<String?> = _selectedGenre

    // ── Pending play queue (Play All / Shuffle All) ───────────────────────────

    data class PendingQueue(val ids: List<Int>, val startIndex: Int)

    private val _pendingQueue = MutableStateFlow<PendingQueue?>(null)
    val pendingQueue: StateFlow<PendingQueue?> = _pendingQueue

    // ── Actions ───────────────────────────────────────────────────────────────

    fun loadGenres() {
        viewModelScope.launch {
            repo.fetchGenres("music")
                .onSuccess { tags -> _genreState.value = GenreState.Ready(tags) }
                .onFailure  { _genreState.value = GenreState.Ready(emptyList()) }
        }
    }

    fun loadMusic(genreTag: String? = null) {
        _musicState.value = MusicState.Loading
        viewModelScope.launch {
            repo.getFeed(
                limit    = 1000,
                category = "music",
                genreTag = genreTag,
            ).onSuccess { response ->
                _musicState.value = MusicState.Ready(response.videos)
            }.onFailure { e ->
                _musicState.value = MusicState.Error(e.message ?: "Failed to load music")
            }
        }
    }

    fun selectGenre(tag: String?) {
        _selectedGenre.value = tag
    }

    /**
     * Play All — loads full ID list in natural order for the active genre filter,
     * then fires the queue via [pendingQueue]. MusicScreen LaunchedEffect picks it up.
     *
     * Always fetches the complete ID list from the backend — not limited to what
     * is currently visible on screen.
     */
    fun playAll() {
        viewModelScope.launch {
            repo.getFeedIds(
                category = "music",
                genreTag = _selectedGenre.value,
            ).onSuccess { response ->
                val ids = response.ids.map { it.id }
                if (ids.isNotEmpty()) {
                    _pendingQueue.value = PendingQueue(ids = ids, startIndex = 0)
                }
            }
        }
    }

    /**
     * Shuffle All — loads full ID list then shuffles it.
     *
     * Shuffles ALL matching videos in the database for the active genre filter,
     * not just what is visible on screen.
     */
    fun shuffleAll() {
        viewModelScope.launch {
            repo.getFeedIds(
                category = "music",
                genreTag = _selectedGenre.value,
            ).onSuccess { response ->
                val ids = response.ids.map { it.id }.shuffled()
                if (ids.isNotEmpty()) {
                    _pendingQueue.value = PendingQueue(ids = ids, startIndex = 0)
                }
            }
        }
    }

    fun clearPendingQueue() {
        _pendingQueue.value = null
    }
}
