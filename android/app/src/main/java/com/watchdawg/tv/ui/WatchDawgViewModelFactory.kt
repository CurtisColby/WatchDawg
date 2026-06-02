package com.watchdawg.tv.ui

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import com.watchdawg.tv.Graph
import com.watchdawg.tv.ui.adult.AdultViewModel
import com.watchdawg.tv.ui.auth.PinViewModel
import com.watchdawg.tv.ui.continuewatching.ContinueWatchingViewModel
import com.watchdawg.tv.ui.home.HomeViewModel
import com.watchdawg.tv.ui.movies.MoviesViewModel
import com.watchdawg.tv.ui.library.FavoritesViewModel
import com.watchdawg.tv.ui.library.LibraryViewModel
import com.watchdawg.tv.ui.movies.MovieDetailViewModel
import com.watchdawg.tv.ui.music.MusicViewModel
import com.watchdawg.tv.ui.player.PlayerViewModel
import com.watchdawg.tv.ui.series.SeriesViewModel
import com.watchdawg.tv.ui.tv.TVViewModel
import com.watchdawg.tv.ui.watchlater.WatchLaterViewModel

/**
 * ViewModel factory — Milestone R-4.
 *
 * Added: MusicViewModel (Music screen — category=music feed + genre pills).
 *        AdultViewModel (Adult screen — category=adult feed + Private library files).
 *
 * R-3 additions carried forward:
 *   MoviesViewModel, HomeViewModel.
 *
 * SeriesViewModel is hoisted at WatchDawgRoot level (created once, shared by
 * TVScreen and EpisodeListScreen) so series grid state survives genre pill
 * switches without reloading from the network.
 */
class WatchDawgViewModelFactory : ViewModelProvider.Factory {

    @Suppress("UNCHECKED_CAST")
    override fun <T : ViewModel> create(modelClass: Class<T>): T {
        return when {
            modelClass.isAssignableFrom(HomeViewModel::class.java) ->
                HomeViewModel() as T
            modelClass.isAssignableFrom(MoviesViewModel::class.java) ->
                MoviesViewModel(Graph.repository) as T
            modelClass.isAssignableFrom(MusicViewModel::class.java) ->
                MusicViewModel(Graph.repository) as T
            modelClass.isAssignableFrom(AdultViewModel::class.java) ->
                AdultViewModel(Graph.repository) as T
            modelClass.isAssignableFrom(TVViewModel::class.java) ->
                TVViewModel(Graph.repository) as T
            modelClass.isAssignableFrom(PlayerViewModel::class.java) ->
                PlayerViewModel(
                    Graph.repository,
                    Graph.resumeState,
                ) as T
            modelClass.isAssignableFrom(FavoritesViewModel::class.java) ->
                FavoritesViewModel(Graph.repository) as T
            modelClass.isAssignableFrom(LibraryViewModel::class.java) ->
                LibraryViewModel(Graph.repository) as T
            modelClass.isAssignableFrom(PinViewModel::class.java) ->
                PinViewModel(Graph.repository) as T
            modelClass.isAssignableFrom(ContinueWatchingViewModel::class.java) ->
                ContinueWatchingViewModel(Graph.repository) as T
            modelClass.isAssignableFrom(WatchLaterViewModel::class.java) ->
                WatchLaterViewModel(Graph.repository) as T
            modelClass.isAssignableFrom(SeriesViewModel::class.java) ->
                SeriesViewModel(Graph.repository) as T
            modelClass.isAssignableFrom(MovieDetailViewModel::class.java) ->
                MovieDetailViewModel(Graph.repository) as T
            else -> throw IllegalArgumentException("Unknown ViewModel: ${modelClass.name}")
        }
    }
}
