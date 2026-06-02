package com.watchdawg.tv.ui.tv

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.tv.material3.Button
import androidx.tv.material3.ButtonDefaults
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import com.watchdawg.tv.ui.series.SeriesScreen
import com.watchdawg.tv.ui.series.SeriesViewModel
import com.watchdawg.tv.ui.theme.WatchDawgColors
import com.watchdawg.tv.ui.theme.focusGlow

/**
 * TV Section screen — Milestone R-2.
 *
 * Layout:
 *   ┌──────────────────────────────────────────────────────────┐
 *   │  Genre pill bar:  [All]  [Nature]  [Drama]  [Comedy] … │
 *   │  (hidden when no tags exist for tv category)             │
 *   ├──────────────────────────────────────────────────────────┤
 *   │  SeriesScreen (reused unchanged from Milestone F)         │
 *   │  Series grid — one card per TV-category channel          │
 *   └──────────────────────────────────────────────────────────┘
 *
 * Navigation flow:
 *   TVScreen → tap series card → EpisodeListScreen → tap episode → Player
 *   Back from EpisodeList → TVScreen (series grid)
 *   Back from series grid → NavRail
 *
 * Genre pill behaviour:
 *   - "All" pill is always present and always first.
 *   - Selecting a genre pill calls seriesViewModel.loadSeries(tag).
 *   - Selecting "All" calls seriesViewModel.loadSeries(null).
 *   - Pill bar is hidden when no tags exist yet (all channels have empty genre_tags).
 *
 * Back-nav safety:
 *   SeriesScreen owns no NavController — it calls [onSeriesTap] / [onPlay] callbacks
 *   which are handled by MainActivity. The screen has no miniPlayerActive guard
 *   (see Restructure Plan — Bug 3 was caused by exactly this pattern on MovieDetail).
 *
 * @param tvViewModel      Owns genre list + selected genre state.
 * @param seriesViewModel  Owns series grid + episode state. Hoisted at WatchDawgRoot
 *                         level so state survives pill switches without reloading.
 * @param onSeriesTap      Navigate to EpisodeListScreen for the selected channel.
 * @param onPlay           Hand off a resolved queue to MainActivity for playback.
 */
@Composable
fun TVScreen(
    tvViewModel: TVViewModel,
    seriesViewModel: SeriesViewModel,
    lastTappedSeriesChannelId: Int = -1,
    onSeriesTap: (channelId: Int, channelName: String) -> Unit,
    onPlay: (videoId: Int, queue: List<Int>, index: Int, hlsMode: Boolean) -> Unit,
    modifier: Modifier = Modifier,
) {
    val genreState    by tvViewModel.genreState.collectAsStateWithLifecycle()
    val selectedGenre by tvViewModel.selectedGenre.collectAsStateWithLifecycle()

    // Load genres once on first composition.
    LaunchedEffect(Unit) {
        tvViewModel.loadGenres()
    }

    // Reload series whenever the genre selection changes.
    // Also clear the Smart Shuffle played-bit Set so each genre
    // gets its own independent played cycle.
    LaunchedEffect(selectedGenre) {
        seriesViewModel.clearPlayedIds()
        seriesViewModel.loadSeries(genreTag = selectedGenre)
    }

    Column(modifier = modifier.fillMaxSize()) {

        // ── Genre pill bar ────────────────────────────────────────────────────
        // Only rendered when tags exist. When hidden, the series grid fills the
        // full height — no empty space above the grid.
        val tags = (genreState as? TVViewModel.GenreState.Ready)?.tags ?: emptyList()

        if (tags.isNotEmpty()) {
            GenrePillBar(
                tags           = tags,
                selectedGenre  = selectedGenre,
                onSelectAll    = { tvViewModel.selectGenre(null) },
                onSelectGenre  = { tag -> tvViewModel.selectGenre(tag) },
                modifier       = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 24.dp)
                    .padding(top = 16.dp, bottom = 8.dp),
            )
        }

        // ── Series grid ───────────────────────────────────────────────────────
        SeriesScreen(
            viewModel                 = seriesViewModel,
            lastTappedSeriesChannelId = lastTappedSeriesChannelId,
            selectedGenre             = selectedGenre,
            onSeriesTap               = onSeriesTap,
            onPlay                    = onPlay,
            modifier                  = Modifier
                .fillMaxSize()
                .padding(horizontal = 24.dp)
                .padding(top = if (tags.isEmpty()) 16.dp else 0.dp),
        )
    }
}

// ── Genre pill bar composable ─────────────────────────────────────────────────

@Composable
private fun GenrePillBar(
    tags: List<String>,
    selectedGenre: String?,
    onSelectAll: () -> Unit,
    onSelectGenre: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    val rowState = rememberLazyListState()

    LazyRow(
        state            = rowState,
        modifier         = modifier.height(48.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
        verticalAlignment = Alignment.CenterVertically,
        contentPadding   = PaddingValues(horizontal = 4.dp),
    ) {
        // "All" pill — always first
        item(key = "all") {
            GenrePill(
                label      = "All",
                isSelected = selectedGenre == null,
                onClick    = onSelectAll,
            )
        }

        // One pill per tag
        items(tags, key = { it }) { tag ->
            GenrePill(
                label      = tag,
                isSelected = selectedGenre == tag,
                onClick    = { onSelectGenre(tag) },
            )
        }
    }
}

@Composable
private fun GenrePill(
    label: String,
    isSelected: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Button(
        onClick  = onClick,
        colors   = ButtonDefaults.colors(
            containerColor        = if (isSelected) WatchDawgColors.Orange else WatchDawgColors.OrangeDim,
            contentColor          = if (isSelected) WatchDawgColors.Background else WatchDawgColors.Orange,
            focusedContainerColor = WatchDawgColors.Orange,
            focusedContentColor   = WatchDawgColors.Background,
        ),
        modifier = modifier.focusGlow(),
    ) {
        Text(
            text  = label,
            style = MaterialTheme.typography.labelLarge,
        )
    }
}
