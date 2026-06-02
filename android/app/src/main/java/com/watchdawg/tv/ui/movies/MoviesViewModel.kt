package com.watchdawg.tv.ui.movies

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.watchdawg.tv.data.api.VideoDto
import com.watchdawg.tv.data.repo.WatchDawgRepository
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch

/**
 * ViewModel for MoviesScreen — Milestone R-3.
 *
 * Owns three pieces of state:
 *  1. [movieState]    — flat list of movie VideoDto objects from GET /feed?category=movies.
 *  2. [genreState]    — distinct genre tags from GET /feed/genres?category=movies.
 *                       Drives the pill bar. Empty = pill bar hidden.
 *  3. [selectedGenre] — active genre pill. Null = "All" (no filter).
 *
 * Loading flow:
 *   loadGenres() fires once on first composition.
 *   loadMovies(genreTag) fires on every genre pill selection change,
 *   including initial load (selectedGenre starts null = All).
 *
 * Play All / Shuffle:
 *   Uses GET /feed/ids?category=movies&genre_tag=X via [fetchMovieIds].
 *   Returns full ordered/shuffled ID list without loading every VideoDto.
 */
class MoviesViewModel(private val repo: WatchDawgRepository) : ViewModel() {

    // ── Movie list state ──────────────────────────────────────────────────────

    sealed class MovieState {
        object Loading : MovieState()
        data class Ready(val videos: List<VideoDto>) : MovieState()
        data class Error(val message: String) : MovieState()
    }

    private val _movieState = MutableStateFlow<MovieState>(MovieState.Loading)
    val movieState: StateFlow<MovieState> = _movieState

    // ── Genre pill state ──────────────────────────────────────────────────────

    sealed class GenreState {
        object Loading : GenreState()
        data class Ready(val tags: List<String>) : GenreState()
    }

    private val _genreState = MutableStateFlow<GenreState>(GenreState.Loading)
    val genreState: StateFlow<GenreState> = _genreState

    private val _selectedGenre = MutableStateFlow<String?>(null)
    val selectedGenre: StateFlow<String?> = _selectedGenre

    // ── Pending play queue (Play All / Shuffle) ───────────────────────────────

    data class PendingQueue(val ids: List<Int>, val startIndex: Int)

    private val _pendingQueue = MutableStateFlow<PendingQueue?>(null)
    val pendingQueue: StateFlow<PendingQueue?> = _pendingQueue

    // ── Actions ───────────────────────────────────────────────────────────────

    fun loadGenres() {
        viewModelScope.launch {
            repo.fetchGenres("movies")
                .onSuccess { tags -> _genreState.value = GenreState.Ready(tags) }
                .onFailure  { _genreState.value = GenreState.Ready(emptyList()) }
        }
    }

    fun loadMovies(genreTag: String? = null) {
        _movieState.value = MovieState.Loading
        viewModelScope.launch {
            repo.getFeed(
                limit    = 1000,
                category = "movies",
                genreTag = genreTag,
            ).onSuccess { response ->
                _movieState.value = MovieState.Ready(response.videos)
            }.onFailure { e ->
                _movieState.value = MovieState.Error(e.message ?: "Failed to load movies")
            }
        }
    }

    fun selectGenre(tag: String?) {
        _selectedGenre.value = tag
    }

    /**
     * Kick off Play All — loads full ID list in natural order, then fires
     * the queue via [pendingQueue]. MoviesScreen LaunchedEffect picks it up.
     */
    fun playAll() {
        viewModelScope.launch {
            repo.getFeedIds(
                category = "movies",
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
     * Kick off Shuffle All — loads full ID list then shuffles it.
     */
    fun shuffleAll() {
        viewModelScope.launch {
            repo.getFeedIds(
                category = "movies",
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
