package com.watchdawg.tv.ui.tv

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.watchdawg.tv.data.repo.WatchDawgRepository
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch

/**
 * ViewModel for TVScreen — Milestone R-2.
 *
 * Owns two pieces of state:
 *  1. [genres]        — list of distinct genre tags for TV-category channels.
 *                       Populated from GET /feed/genres?category=tv on first load.
 *                       An empty list means no tags have been set yet; TVScreen
 *                       hides the pill bar in that case.
 *  2. [selectedGenre] — the currently active genre pill. Null = "All" (no filter).
 *
 * When the user taps a genre pill, TVScreen calls [selectGenre], which updates
 * [selectedGenre] and triggers [SeriesViewModel.loadSeries(genreTag)] in the screen.
 *
 * Genre loading is best-effort — a failure leaves genres empty and TVScreen
 * shows only the "All" pill (which loads all series via loadSeries(null)).
 */
class TVViewModel(private val repo: WatchDawgRepository) : ViewModel() {

    sealed class GenreState {
        object Loading : GenreState()
        data class Ready(val tags: List<String>) : GenreState()
    }

    private val _genreState = MutableStateFlow<GenreState>(GenreState.Loading)
    val genreState: StateFlow<GenreState> = _genreState

    private val _selectedGenre = MutableStateFlow<String?>(null)
    val selectedGenre: StateFlow<String?> = _selectedGenre

    fun loadGenres() {
        viewModelScope.launch {
            repo.fetchGenres("tv")
                .onSuccess { tags -> _genreState.value = GenreState.Ready(tags) }
                .onFailure  { _genreState.value = GenreState.Ready(emptyList()) }
        }
    }

    fun selectGenre(tag: String?) {
        _selectedGenre.value = tag
    }
}
