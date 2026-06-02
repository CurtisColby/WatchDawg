package com.watchdawg.tv.ui.series

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.lazy.grid.rememberLazyGridState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.tv.material3.Border
import androidx.tv.material3.Button
import androidx.tv.material3.ButtonDefaults
import androidx.tv.material3.Card
import androidx.tv.material3.CardDefaults
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import coil.compose.AsyncImage
import com.watchdawg.tv.data.api.SeriesItemDto
import com.watchdawg.tv.ui.theme.WatchDawgColors
import com.watchdawg.tv.ui.theme.focusGlow
import com.watchdawg.tv.ui.theme.focusGlowCard
import kotlinx.coroutines.delay

/**
 * Series grid screen — Milestone F.
 *
 * Milestone R-2 focus fix: when returning from EpisodeList via Back, focus is
 * restored to the series card that was last tapped. [lastTappedSeriesChannelId]
 * is passed from MainActivity (hoisted state) and drives a LaunchedEffect that
 * fires on first composition to pull focus away from the NavRail.
 *
 * Same pattern used in EpisodeListScreen — FocusRequester map keyed by
 * channelId, 150ms delay for grid layout to complete.
 */
@Composable
fun SeriesScreen(
    viewModel: SeriesViewModel,
    lastTappedSeriesChannelId: Int = -1,
    selectedGenre: String? = null,   // Session 33: passed from TVScreen for genre-aware Smart Shuffle
    onSeriesTap: (channelId: Int, channelName: String) -> Unit,
    onPlay: (videoId: Int, queue: List<Int>, index: Int, hlsMode: Boolean) -> Unit,
    modifier: Modifier = Modifier,
) {
    val state        by viewModel.seriesState.collectAsStateWithLifecycle()
    val queueLoading by viewModel.tvQueueLoading.collectAsStateWithLifecycle()
    val pendingQueue by viewModel.pendingQueue.collectAsStateWithLifecycle()

    val gridState = rememberLazyGridState()

    // FocusRequester map — one per series channelId.
    // Populated as cards are composed. Used to restore focus on Back from
    // EpisodeList and on first load (to pull focus from the NavRail).
    val cardFocusRequesters = remember { mutableMapOf<Int, FocusRequester>() }

    // Dedicated requester for the first card — used when no specific card
    // is targeted (e.g. first load, no prior series tapped).
    val firstCardFocus = remember { FocusRequester() }

    // Load on entry — idempotent if already loaded.
    LaunchedEffect(Unit) {
        viewModel.loadSeries()
    }

    // One-shot: fire play when queue is ready, then clear.
    LaunchedEffect(pendingQueue) {
        val q = pendingQueue ?: return@LaunchedEffect
        if (q.ids.isNotEmpty()) {
            onPlay(q.ids[q.startIndex], q.ids, q.startIndex, false)
            viewModel.clearPendingQueue()
        }
    }

    // Restore focus when series load completes.
    //   - If lastTappedSeriesChannelId is valid: focus that card (Back from EpisodeList).
    //   - Otherwise: focus the first card (first load, pulls focus from NavRail).
    // 150ms delay: grid must finish layout before requestFocus() works.
    LaunchedEffect(state) {
        if (state !is SeriesViewModel.SeriesState.Success) return@LaunchedEffect
        val items = (state as SeriesViewModel.SeriesState.Success).items
        if (items.isEmpty()) return@LaunchedEffect
        delay(150)
        if (lastTappedSeriesChannelId > 0) {
            try {
                cardFocusRequesters[lastTappedSeriesChannelId]?.requestFocus()
            } catch (_: Exception) {
                try { firstCardFocus.requestFocus() } catch (_: Exception) {}
            }
        } else {
            try { firstCardFocus.requestFocus() } catch (_: Exception) {}
        }
    }

    Column(modifier = modifier.fillMaxSize()) {

        // ── Header row ────────────────────────────────────────────────────────
        Row(
            verticalAlignment     = Alignment.Bottom,
            horizontalArrangement = Arrangement.SpaceBetween,
            modifier              = Modifier.fillMaxWidth(),
        ) {
            // Title + count
            Row(verticalAlignment = Alignment.Bottom) {
                Text(
                    text  = "TV Series",
                    style = MaterialTheme.typography.displayLarge,
                    color = WatchDawgColors.TextPrimary,
                )
                Spacer(Modifier.width(12.dp))
                val subtitle = when (val s = state) {
                    is SeriesViewModel.SeriesState.Success ->
                        "${s.items.size} series"
                    is SeriesViewModel.SeriesState.Loading -> "Loading…"
                    is SeriesViewModel.SeriesState.Error   -> "Error"
                }
                Text(
                    text     = subtitle,
                    style    = MaterialTheme.typography.bodyLarge,
                    color    = WatchDawgColors.TextTertiary,
                    modifier = Modifier.padding(bottom = 6.dp),
                )
            }

            // Action buttons — only shown when series loaded and non-empty
            if (state is SeriesViewModel.SeriesState.Success &&
                (state as SeriesViewModel.SeriesState.Success).items.isNotEmpty()
            ) {
                Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    Button(
                        onClick = { if (!queueLoading) viewModel.playAllTv() },
                        colors  = ButtonDefaults.colors(
                            containerColor        = WatchDawgColors.OrangeDim,
                            contentColor          = WatchDawgColors.Orange,
                            focusedContainerColor = WatchDawgColors.Orange,
                            focusedContentColor   = WatchDawgColors.Background,
                        ),
                        modifier = Modifier.focusGlow(),
                    ) {
                        Text(
                            if (queueLoading) "Loading…" else "▶  Play All TV",
                            style = MaterialTheme.typography.titleSmall,
                        )
                    }
                    Button(
                        onClick = { if (!queueLoading) viewModel.smartShuffleTv(selectedGenre) },
                        colors  = ButtonDefaults.colors(
                            containerColor        = WatchDawgColors.OrangeDim,
                            contentColor          = WatchDawgColors.Orange,
                            focusedContainerColor = WatchDawgColors.Orange,
                            focusedContentColor   = WatchDawgColors.Background,
                        ),
                        modifier = Modifier.focusGlow(),
                    ) {
                        Text("🎲  Smart Shuffle TV", style = MaterialTheme.typography.titleSmall)
                    }
                }
            }
        }

        Spacer(Modifier.height(16.dp))

        // ── Content ───────────────────────────────────────────────────────────
        when (val s = state) {
            is SeriesViewModel.SeriesState.Loading -> {
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    Text(
                        text  = "Loading series…",
                        style = MaterialTheme.typography.titleLarge,
                        color = WatchDawgColors.TextSecondary,
                    )
                }
            }

            is SeriesViewModel.SeriesState.Error -> {
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Text("📺", style = MaterialTheme.typography.displayLarge)
                        Spacer(Modifier.height(12.dp))
                        Text(
                            text  = "Could not load series.",
                            style = MaterialTheme.typography.titleLarge,
                            color = WatchDawgColors.TextSecondary,
                        )
                        Text(
                            text     = s.message,
                            style    = MaterialTheme.typography.bodyMedium,
                            color    = WatchDawgColors.TextTertiary,
                            modifier = Modifier.padding(top = 8.dp),
                        )
                    }
                }
            }

            is SeriesViewModel.SeriesState.Success -> {
                if (s.items.isEmpty()) {
                    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                        Column(horizontalAlignment = Alignment.CenterHorizontally) {
                            Text("📺", style = MaterialTheme.typography.displayLarge)
                            Spacer(Modifier.height(12.dp))
                            Text(
                                text  = "No TV series yet.",
                                style = MaterialTheme.typography.titleLarge,
                                color = WatchDawgColors.TextSecondary,
                            )
                            Text(
                                text     = "Add a channel with category 'tv' in the web UI.",
                                style    = MaterialTheme.typography.bodyMedium,
                                color    = WatchDawgColors.TextTertiary,
                                modifier = Modifier.padding(top = 8.dp),
                            )
                        }
                    }
                } else {
                    LazyVerticalGrid(
                        state   = gridState,
                        columns = GridCells.Fixed(4),
                        contentPadding = PaddingValues(top = 8.dp, bottom = 48.dp),
                        horizontalArrangement = Arrangement.spacedBy(20.dp),
                        verticalArrangement   = Arrangement.spacedBy(20.dp),
                        modifier = Modifier.fillMaxSize(),
                    ) {
                        items(s.items, key = { it.channelId }) { series ->
                            val isFirst = s.items.indexOf(series) == 0
                            val cardFr = cardFocusRequesters.getOrPut(series.channelId) {
                                FocusRequester()
                            }
                            // Register firstCardFocus on the first card so the
                            // LaunchedEffect can use it as a fallback target.
                            if (isFirst) {
                                cardFocusRequesters[series.channelId] = firstCardFocus
                            }
                            SeriesCard(
                                series   = series,
                                onTap    = { onSeriesTap(series.channelId, series.channelName) },
                                modifier = if (isFirst)
                                    Modifier.focusRequester(firstCardFocus)
                                else
                                    Modifier.focusRequester(cardFr),
                            )
                        }
                    }
                }
            }
        }
    }
}

// ── Series card ───────────────────────────────────────────────────────────────

@Composable
private fun SeriesCard(
    series: SeriesItemDto,
    onTap: () -> Unit,
    modifier: Modifier = Modifier,
) {
    var focused by remember { mutableStateOf(false) }

    val artUrl = series.tmdbPosterUrl ?: series.latestThumbnail

    Card(
        onClick  = onTap,
        modifier = modifier
            .width(300.dp)
            .onFocusChanged { focused = it.isFocused }
            .focusGlowCard(focused),
        colors = CardDefaults.colors(
            containerColor        = WatchDawgColors.Surface,
            focusedContainerColor = WatchDawgColors.SurfaceFocused,
        ),
        border = CardDefaults.border(
            focusedBorder = Border(
                border = androidx.compose.foundation.BorderStroke(
                    3.dp, WatchDawgColors.Orange,
                ),
            ),
        ),
        scale = CardDefaults.scale(focusedScale = 1.06f),
    ) {
        Column {
            // ── Thumbnail ─────────────────────────────────────────────────────
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .aspectRatio(16f / 9f)
                    .clip(MaterialTheme.shapes.medium),
            ) {
                AsyncImage(
                    model              = artUrl,
                    contentDescription = series.channelName,
                    contentScale       = ContentScale.Crop,
                    modifier           = Modifier
                        .fillMaxSize()
                        .background(WatchDawgColors.SurfaceElevated),
                )

                // Episode count badge — bottom-left corner
                Text(
                    text     = "${series.episodeCount} ep",
                    style    = MaterialTheme.typography.labelSmall,
                    color    = Color.White,
                    modifier = Modifier
                        .align(Alignment.BottomStart)
                        .padding(6.dp)
                        .clip(MaterialTheme.shapes.small)
                        .background(Color(0xBB000000))
                        .padding(horizontal = 6.dp, vertical = 2.dp),
                )

                // TMDb rating badge — bottom-right corner
                if (series.tmdbRating != null && series.tmdbRating > 0f) {
                    Text(
                        text     = "★ ${"%.1f".format(series.tmdbRating)}",
                        style    = MaterialTheme.typography.labelSmall,
                        color    = Color.White,
                        modifier = Modifier
                            .align(Alignment.BottomEnd)
                            .padding(6.dp)
                            .clip(MaterialTheme.shapes.small)
                            .background(WatchDawgColors.OrangeDim)
                            .padding(horizontal = 6.dp, vertical = 2.dp),
                    )
                }
            }

            // ── Metadata ──────────────────────────────────────────────────────
            Column(Modifier.padding(12.dp)) {
                Text(
                    text     = series.channelName,
                    style    = MaterialTheme.typography.titleSmall,
                    color    = WatchDawgColors.TextPrimary,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                )
                if (series.tmdbYear != null) {
                    Text(
                        text     = series.tmdbYear.toString(),
                        style    = MaterialTheme.typography.bodySmall,
                        color    = WatchDawgColors.Orange,
                        maxLines = 1,
                    )
                }
                if (!series.tmdbDescription.isNullOrBlank()) {
                    Text(
                        text     = series.tmdbDescription,
                        style    = MaterialTheme.typography.bodySmall,
                        color    = WatchDawgColors.TextTertiary,
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.padding(top = 4.dp),
                    )
                }
            }
        }
    }
}
