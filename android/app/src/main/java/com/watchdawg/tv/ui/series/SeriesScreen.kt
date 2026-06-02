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
 * Series grid screen — R-3 thumbnail fix.
 *
 * All cards are uniform 2:3 portrait aspect ratio regardless of art source.
 *
 * ContentScale strategy:
 *   - TMDB poster → ContentScale.Crop  (poster is already 2:3, fills card perfectly)
 *   - YouTube thumbnail → ContentScale.Fit  (16:9 image letterboxed inside 2:3 card,
 *     dark bars top/bottom, image stays sharp at native resolution — no stretching)
 *
 * This keeps the grid rows uniform height while never distorting images.
 */
@Composable
fun SeriesScreen(
    viewModel: SeriesViewModel,
    selectedGenre: String? = null,
    lastTappedSeriesChannelId: Int = -1,
    onSeriesTap: (channelId: Int, channelName: String) -> Unit,
    onPlay: (videoId: Int, queue: List<Int>, index: Int, hlsMode: Boolean) -> Unit,
    modifier: Modifier = Modifier,
) {
    val state        by viewModel.seriesState.collectAsStateWithLifecycle()
    val queueLoading by viewModel.tvQueueLoading.collectAsStateWithLifecycle()
    val pendingQueue by viewModel.pendingQueue.collectAsStateWithLifecycle()

    val gridState           = rememberLazyGridState()
    val cardFocusRequesters = remember { mutableMapOf<Int, FocusRequester>() }
    val firstCardFocus      = remember { FocusRequester() }

    // TVScreen owns the load via LaunchedEffect(selectedGenre) in TVScreen.
    // SeriesScreen must never call loadSeries() independently — it overwrites
    // the genre-filtered result every time this composable re-enters composition.

    LaunchedEffect(pendingQueue) {
        val q = pendingQueue ?: return@LaunchedEffect
        if (q.ids.isNotEmpty()) {
            onPlay(q.ids[q.startIndex], q.ids, q.startIndex, false)
            viewModel.clearPendingQueue()
        }
    }

    LaunchedEffect(state) {
        if (state !is SeriesViewModel.SeriesState.Success) return@LaunchedEffect
        val items = (state as SeriesViewModel.SeriesState.Success).items
        if (items.isEmpty()) return@LaunchedEffect
        delay(150)
        if (lastTappedSeriesChannelId > 0) {
            try { cardFocusRequesters[lastTappedSeriesChannelId]?.requestFocus() }
            catch (_: Exception) { try { firstCardFocus.requestFocus() } catch (_: Exception) {} }
        } else {
            try { firstCardFocus.requestFocus() } catch (_: Exception) {}
        }
    }

    Column(modifier = modifier.fillMaxSize()) {

        // ── Header ────────────────────────────────────────────────────────────
        Row(
            verticalAlignment     = Alignment.Bottom,
            horizontalArrangement = Arrangement.SpaceBetween,
            modifier              = Modifier.fillMaxWidth(),
        ) {
            Row(verticalAlignment = Alignment.Bottom) {
                Text(
                    text  = "TV Series",
                    style = MaterialTheme.typography.displayLarge,
                    color = WatchDawgColors.TextPrimary,
                )
                Spacer(Modifier.width(12.dp))
                val subtitle = when (val s = state) {
                    is SeriesViewModel.SeriesState.Success -> "${s.items.size} series"
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

            if (state is SeriesViewModel.SeriesState.Success &&
                (state as SeriesViewModel.SeriesState.Success).items.isNotEmpty()
            ) {
                Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    Button(
                        onClick  = { if (!queueLoading) viewModel.playAllTv(selectedGenre) },
                        colors   = ButtonDefaults.colors(
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
                        onClick  = { if (!queueLoading) viewModel.shuffleAllTv(selectedGenre) },
                        colors   = ButtonDefaults.colors(
                            containerColor        = WatchDawgColors.OrangeDim,
                            contentColor          = WatchDawgColors.Orange,
                            focusedContainerColor = WatchDawgColors.Orange,
                            focusedContentColor   = WatchDawgColors.Background,
                        ),
                        modifier = Modifier.focusGlow(),
                    ) {
                        Text("🔀  Shuffle All TV", style = MaterialTheme.typography.titleSmall)
                    }
                }
            }
        }

        Spacer(Modifier.height(16.dp))

        // ── Content ───────────────────────────────────────────────────────────
        when (val s = state) {
            is SeriesViewModel.SeriesState.Loading -> {
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    Text("Loading series…", style = MaterialTheme.typography.titleLarge, color = WatchDawgColors.TextSecondary)
                }
            }
            is SeriesViewModel.SeriesState.Error -> {
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Text("📺", style = MaterialTheme.typography.displayLarge)
                        Spacer(Modifier.height(12.dp))
                        Text("Could not load series.", style = MaterialTheme.typography.titleLarge, color = WatchDawgColors.TextSecondary)
                        Text(s.message, style = MaterialTheme.typography.bodyMedium, color = WatchDawgColors.TextTertiary, modifier = Modifier.padding(top = 8.dp))
                    }
                }
            }
            is SeriesViewModel.SeriesState.Success -> {
                if (s.items.isEmpty()) {
                    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                        Column(horizontalAlignment = Alignment.CenterHorizontally) {
                            Text("📺", style = MaterialTheme.typography.displayLarge)
                            Spacer(Modifier.height(12.dp))
                            Text("No TV series yet.", style = MaterialTheme.typography.titleLarge, color = WatchDawgColors.TextSecondary)
                            Text("Add a channel with category 'tv' in the web UI.", style = MaterialTheme.typography.bodyMedium, color = WatchDawgColors.TextTertiary, modifier = Modifier.padding(top = 8.dp))
                        }
                    }
                } else {
                    LazyVerticalGrid(
                        state                 = gridState,
                        columns               = GridCells.Fixed(4),
                        contentPadding        = PaddingValues(top = 8.dp, bottom = 48.dp),
                        horizontalArrangement = Arrangement.spacedBy(20.dp),
                        verticalArrangement   = Arrangement.spacedBy(20.dp),
                        modifier              = Modifier.fillMaxSize(),
                    ) {
                        items(s.items, key = { it.channelId }) { series ->
                            val isFirst = s.items.indexOf(series) == 0
                            val cardFr  = cardFocusRequesters.getOrPut(series.channelId) { FocusRequester() }
                            if (isFirst) cardFocusRequesters[series.channelId] = firstCardFocus
                            SeriesCard(
                                series   = series,
                                onTap    = { onSeriesTap(series.channelId, series.channelName) },
                                modifier = if (isFirst) Modifier.focusRequester(firstCardFocus)
                                           else Modifier.focusRequester(cardFr),
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

    val hasPoster = series.tmdbPosterUrl != null
    val artUrl    = series.tmdbPosterUrl ?: series.latestThumbnail

    Card(
        onClick  = onTap,
        modifier = modifier
            .fillMaxWidth()
            // All cards uniform 2:3 portrait — consistent grid rows, no layout jumps
            .aspectRatio(2f / 3f)
            .onFocusChanged { focused = it.isFocused }
            .focusGlowCard(focused),
        colors = CardDefaults.colors(
            containerColor        = WatchDawgColors.Surface,
            focusedContainerColor = WatchDawgColors.SurfaceFocused,
        ),
        border = CardDefaults.border(
            focusedBorder = Border(
                border = androidx.compose.foundation.BorderStroke(3.dp, WatchDawgColors.Orange),
            ),
        ),
        scale = CardDefaults.scale(focusedScale = 1.06f),
    ) {
        Box(modifier = Modifier.fillMaxSize()) {

            if (artUrl != null) {
                AsyncImage(
                    model              = artUrl,
                    contentDescription = series.channelName,
                    // TMDB poster → Crop (already 2:3, fills card edge-to-edge)
                    // YouTube thumbnail → Fit (16:9 letterboxed inside 2:3, sharp, no stretch)
                    contentScale       = if (hasPoster) ContentScale.Crop else ContentScale.Fit,
                    modifier           = Modifier
                        .fillMaxSize()
                        .background(WatchDawgColors.SurfaceElevated),
                )
            } else {
                Box(
                    modifier         = Modifier.fillMaxSize().background(WatchDawgColors.SurfaceElevated),
                    contentAlignment = Alignment.Center,
                ) {
                    Text("📺", style = MaterialTheme.typography.displayMedium)
                }
            }

            // Episode count badge — bottom-left
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

            // TMDB rating badge — bottom-right
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

            // Title overlay at bottom (gradient) for non-poster cards
            if (!hasPoster) {
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .align(Alignment.BottomStart)
                        .background(
                            androidx.compose.ui.graphics.Brush.verticalGradient(
                                colors = listOf(Color.Transparent, Color(0xDD000000)),
                            )
                        )
                        .padding(start = 8.dp, end = 8.dp, top = 20.dp, bottom = 28.dp),
                ) {
                    Column {
                        Text(
                            text     = series.channelName,
                            style    = MaterialTheme.typography.labelMedium,
                            color    = WatchDawgColors.TextPrimary,
                            maxLines = 2,
                            overflow = TextOverflow.Ellipsis,
                        )
                        if (series.tmdbYear != null) {
                            Text(
                                text  = series.tmdbYear.toString(),
                                style = MaterialTheme.typography.labelSmall,
                                color = WatchDawgColors.Orange,
                            )
                        }
                    }
                }
            }

            // For TMDB poster cards: show name + year below image
            // (handled by card Column below when hasPoster)
        }
    }
}
