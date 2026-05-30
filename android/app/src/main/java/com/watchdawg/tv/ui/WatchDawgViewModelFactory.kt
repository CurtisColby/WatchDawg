package com.watchdawg.tv.ui

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import com.watchdawg.tv.Graph
import com.watchdawg.tv.ui.auth.PinViewModel
import com.watchdawg.tv.ui.continuewatching.ContinueWatchingViewModel
import com.watchdawg.tv.ui.feed.FeedViewModel
import com.watchdawg.tv.ui.library.FavoritesViewModel
import com.watchdawg.tv.ui.library.LibraryViewModel
import com.watchdawg.tv.ui.player.PlayerViewModel
import com.watchdawg.tv.ui.series.SeriesViewModel
import com.watchdawg.tv.ui.watchlater.WatchLaterViewModel

class WatchDawgViewModelFactory : ViewModelProvider.Factory {

    @Suppress("UNCHECKED_CAST")
    override fun <T : ViewModel> create(modelClass: Class<T>): T {
        return when {
            modelClass.isAssignableFrom(FeedViewModel::class.java) ->
                FeedViewModel(
                    Graph.repository,
                    Graph.defaultChannelPrefs,
                    Graph.resumeState,
                ) as T
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
            // Milestone F: TV series two-level navigation
            modelClass.isAssignableFrom(SeriesViewModel::class.java) ->
                SeriesViewModel(Graph.repository) as T
            else -> throw IllegalArgumentException("Unknown ViewModel: ${modelClass.name}")
        }
    }
}
