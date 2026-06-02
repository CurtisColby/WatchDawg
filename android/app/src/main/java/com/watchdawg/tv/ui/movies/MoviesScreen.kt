package com.watchdawg.tv.ui.movies

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.lazy.grid.rememberLazyGridState
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.tv.material3.Button
import androidx.tv.material3.ButtonDefaults
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import com.watchdawg.tv.data.api.VideoDto
import com.watchdawg.tv.ui.theme.WatchDawgColors
import com.watchdawg.tv.ui.theme.focusGlow
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * Movies screen — Milestone R-3.
 *
 * Full-screen 4-column portrait poster grid with genre pill bar.
 * Tap card → PlayModeMenu (HLS focused by default, Split Stream secondary).
 * Play All / Shuffle All load the full ID list via /feed/ids.
 *
 * Layout:
 *   ┌──────────────────────────────────────────────────────────────┐
 *   │  🎬  Movies                            [▶ Play All] [🔀]    │
 *   │  Genre pills: [All] [Action] [Comedy] …                      │
 *   ├──────────────────────────────────────────────────────────────┤
 *   │  [poster][poster][poster][poster]                            │
 *   │  [poster][poster][poster][poster]                            │
 *   │  …                                                           │
 *   └──────────────────────────────────────────────────────────────┘
 *
 * Back navigation: popBackStack() → Home.
 * Long-press Back: handled by MainActivity root → Home.
 *
 * @param viewModel  Owns movie list, genre tags, selected genre, pending queue.
 * @param onPlay     Called with (videoId, queue, startIndex, hlsMode) to launch player.
 * @param onBack     popBackStack() → Home.
 */
@Composable
fun MoviesScreen(
    viewModel: MoviesViewModel,
    onPlay: (videoId: Int, queue: List<Int>, index: Int, hlsMode: Boolean) -> Unit,
    onBack: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val movieState   by viewModel.movieState.collectAsStateWithLifecycle()
    val genreState   by viewModel.genreState.collectAsStateWithLifecycle()
    val selectedGenre by viewModel.selectedGenre.collectAsStateWithLifecycle()
    val pendingQueue by viewModel.pendingQueue.collectAsStateWithLifecycle()

    val gridState = rememberLazyGridState()
    val scope     = rememberCoroutineScope()

    // PlayModeMenu state — which movie was tapped
    var pendingPlay by remember { mutableStateOf<VideoDto?>(null) }
    val playFocusRequester = remember { FocusRequester() }
    val firstCardFocus     = remember { FocusRequester() }

    // Load genres once
    LaunchedEffect(Unit) {
        viewModel.loadGenres()
    }

    // Reload movies whenever genre selection changes
    LaunchedEffect(selectedGenre) {
        viewModel.loadMovies(genreTag = selectedGenre)
    }

    // One-shot: Play All / Shuffle queue ready — fire immediately
    LaunchedEffect(pendingQueue) {
        val q = pendingQueue ?: return@LaunchedEffect
        if (q.ids.isNotEmpty()) {
            onPlay(q.ids[q.startIndex], q.ids, q.startIndex, true) // HLS for queued play
            viewModel.clearPendingQueue()
        }
    }

    // Focus first card after load
    LaunchedEffect(movieState) {
        if (movieState is MoviesViewModel.MovieState.Ready &&
            (movieState as MoviesViewModel.MovieState.Ready).videos.isNotEmpty()
        ) {
            delay(150)
            try { firstCardFocus.requestFocus() } catch (_: Exception) {}
        }
    }

    // Auto-focus HLS button when PlayModeMenu appears
    LaunchedEffect(pendingPlay) {
        if (pendingPlay != null) {
            try { playFocusRequester.requestFocus() } catch (_: Exception) {}
        }
    }

    val tags = (genreState as? MoviesViewModel.GenreState.Ready)?.tags ?: emptyList()

    Box(modifier = modifier.fillMaxSize()) {

        Column(
            modifier = Modifier
                .fillMaxSize()
                .background(WatchDawgColors.Background)
                .padding(horizontal = 24.dp),
        ) {

            Spacer(Modifier.height(16.dp))

            // ── Header row ────────────────────────────────────────────────────
            Row(
                verticalAlignment     = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween,
                modifier              = Modifier.fillMaxWidth(),
            ) {
                Column {
                    Text(
                        text  = "🎬  Movies",
                        style = MaterialTheme.typography.displayLarge,
                        color = WatchDawgColors.TextPrimary,
                    )
                    val subtitle = when (val s = movieState) {
                        is MoviesViewModel.MovieState.Ready   -> "${s.videos.size} movies"
                        is MoviesViewModel.MovieState.Loading -> "Loading…"
                        is MoviesViewModel.MovieState.Error   -> "Error"
                    }
                    Text(
                        text  = subtitle,
                        style = MaterialTheme.typography.bodyLarge,
                        color = WatchDawgColors.TextTertiary,
                    )
                }

                // Action buttons — only when movies are loaded
                if (movieState is MoviesViewModel.MovieState.Ready) {
                    Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        Button(
                            onClick  = { viewModel.playAll() },
                            colors   = ButtonDefaults.colors(
                                containerColor        = WatchDawgColors.OrangeDim,
                                contentColor          = WatchDawgColors.Orange,
                                focusedContainerColor = WatchDawgColors.Orange,
                                focusedContentColor   = WatchDawgColors.Background,
                            ),
                            modifier = Modifier.focusGlow(),
                        ) {
                            Text("▶  Play All", style = MaterialTheme.typography.titleSmall)
                        }
                        Button(
                            onClick  = { viewModel.shuffleAll() },
                            colors   = ButtonDefaults.colors(
                                containerColor        = WatchDawgColors.OrangeDim,
                                contentColor          = WatchDawgColors.Orange,
                                focusedContainerColor = WatchDawgColors.Orange,
                                focusedContentColor   = WatchDawgColors.Background,
                            ),
                            modifier = Modifier.focusGlow(),
                        ) {
                            Text("🔀  Shuffle", style = MaterialTheme.typography.titleSmall)
                        }
                        Button(
                            onClick  = { scope.launch { gridState.scrollToItem(0) } },
                            colors   = ButtonDefaults.colors(
                                containerColor        = WatchDawgColors.Surface,
                                contentColor          = WatchDawgColors.TextSecondary,
                                focusedContainerColor = WatchDawgColors.SurfaceFocused,
                                focusedContentColor   = WatchDawgColors.TextPrimary,
                            ),
                            modifier = Modifier.focusGlow(),
                        ) {
                            Text("⬆ Top", style = MaterialTheme.typography.titleSmall)
                        }
                    }
                }
            }

            Spacer(Modifier.height(12.dp))

            // ── Genre pill bar ────────────────────────────────────────────────
            if (tags.isNotEmpty()) {
                MovieGenrePillBar(
                    tags          = tags,
                    selectedGenre = selectedGenre,
                    onSelectAll   = { viewModel.selectGenre(null) },
                    onSelectGenre = { tag -> viewModel.selectGenre(tag) },
                    modifier      = Modifier
                        .fillMaxWidth()
                        .padding(bottom = 8.dp),
                )
            }

            // ── Content ───────────────────────────────────────────────────────
            when (val s = movieState) {
                is MoviesViewModel.MovieState.Loading -> {
                    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                        Text(
                            text  = "Loading movies…",
                            style = MaterialTheme.typography.titleLarge,
                            color = WatchDawgColors.TextSecondary,
                        )
                    }
                }

                is MoviesViewModel.MovieState.Error -> {
                    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                        Column(horizontalAlignment = Alignment.CenterHorizontally) {
                            Text("🎬", style = MaterialTheme.typography.displayLarge)
                            Spacer(Modifier.height(12.dp))
                            Text(
                                text  = "Could not load movies.",
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

                is MoviesViewModel.MovieState.Ready -> {
                    val movies = s.videos

                    if (movies.isEmpty()) {
                        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                                Text("🎬", style = MaterialTheme.typography.displayLarge)
                                Spacer(Modifier.height(12.dp))
                                Text(
                                    text  = "No movies found.",
                                    style = MaterialTheme.typography.titleLarge,
                                    color = WatchDawgColors.TextSecondary,
                                )
                            }
                        }
                    } else {
                        val movieIds = remember(movies) { movies.map { it.id } }
                        val cardFocusRequesters = remember { mutableMapOf<Int, FocusRequester>() }

                        LazyVerticalGrid(
                            state   = gridState,
                            columns = GridCells.Fixed(4),
                            contentPadding        = PaddingValues(top = 8.dp, bottom = 48.dp),
                            horizontalArrangement = Arrangement.spacedBy(16.dp),
                            verticalArrangement   = Arrangement.spacedBy(16.dp),
                            modifier              = Modifier.fillMaxSize(),
                        ) {
                            items(movies, key = { it.id }) { movie ->
                                val index  = movieIds.indexOf(movie.id)
                                val cardFr = cardFocusRequesters.getOrPut(movie.id) {
                                    FocusRequester()
                                }
                                val isFirst = index == 0

                                MovieCard(
                                    video    = movie,
                                    onPlay   = { pendingPlay = movie },
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

        // ── Play mode menu overlay ─────────────────────────────────────────────
        if (pendingPlay != null) {
            val movie = pendingPlay!!
            MoviePlayModeMenu(
                onPlay = { hlsMode ->
                    val videos = (movieState as? MoviesViewModel.MovieState.Ready)
                        ?.videos ?: emptyList()
                    val ids   = videos.map { it.id }
                    val index = ids.indexOf(movie.id).coerceAtLeast(0)
                    pendingPlay = null
                    onPlay(movie.id, ids, index, hlsMode)
                },
                onDismiss          = { pendingPlay = null },
                playFocusRequester = playFocusRequester,
            )
        }
    }
}

// ── Genre pill bar ────────────────────────────────────────────────────────────

@Composable
private fun MovieGenrePillBar(
    tags: List<String>,
    selectedGenre: String?,
    onSelectAll: () -> Unit,
    onSelectGenre: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    LazyRow(
        state                 = rememberLazyListState(),
        modifier              = modifier.height(48.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
        verticalAlignment     = Alignment.CenterVertically,
        contentPadding        = PaddingValues(horizontal = 4.dp),
    ) {
        item(key = "all") {
            MovieGenrePill(
                label      = "All",
                isSelected = selectedGenre == null,
                onClick    = onSelectAll,
            )
        }
        items(tags, key = { it }) { tag ->
            MovieGenrePill(
                label      = tag,
                isSelected = selectedGenre == tag,
                onClick    = { onSelectGenre(tag) },
            )
        }
    }
}

@Composable
private fun MovieGenrePill(
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
        Text(text = label, style = MaterialTheme.typography.labelLarge)
    }
}

// ── Play mode menu — HLS focused by default ───────────────────────────────────

/**
 * Play mode selection overlay for movies.
 *
 * Unlike EpisodeListScreen's PlayModeMenu where Split Stream is the default
 * (no seeking needed for short TV clips), movies default to HLS because
 * users need to seek through a 2-hour film. Split Stream is still available
 * as a fallback in case HLS resolution fails.
 *
 * [playFocusRequester] is wired to the HLS button — it receives focus when
 * the menu appears. D-pad Down reaches Split Stream.
 */
@Composable
private fun MoviePlayModeMenu(
    onPlay: (hlsMode: Boolean) -> Unit,
    onDismiss: () -> Unit,
    playFocusRequester: FocusRequester,
) {
    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(Color(0xBB000000))
            .onKeyEvent { event ->
                if (event.type == KeyEventType.KeyUp && event.key == Key.Back) {
                    onDismiss(); true
                } else false
            },
        contentAlignment = Alignment.Center,
    ) {
        Column(
            modifier = Modifier
                .widthIn(min = 320.dp, max = 440.dp)
                .clip(RoundedCornerShape(16.dp))
                .background(WatchDawgColors.Surface)
                .padding(horizontal = 32.dp, vertical = 28.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            Text(
                text  = "Choose Play Mode",
                style = MaterialTheme.typography.titleLarge,
                color = WatchDawgColors.TextPrimary,
            )
            Spacer(Modifier.height(4.dp))

            // HLS — default focus, recommended for movies (seekable)
            Button(
                onClick  = { onPlay(true) },
                colors   = ButtonDefaults.colors(
                    containerColor        = WatchDawgColors.OrangeDim,
                    contentColor          = WatchDawgColors.Orange,
                    focusedContainerColor = WatchDawgColors.Orange,
                    focusedContentColor   = WatchDawgColors.Background,
                ),
                modifier = Modifier
                    .fillMaxWidth()
                    .focusRequester(playFocusRequester) // ← HLS gets default focus
                    .focusGlow(),
            ) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text("⚡  HLS Mode", style = MaterialTheme.typography.titleMedium)
                    Text(
                        "Seekable · Recommended for movies",
                        style = MaterialTheme.typography.labelMedium,
                        color = WatchDawgColors.TextSecondary,
                    )
                }
            }

            // Split Stream — fallback if HLS unavailable
            Button(
                onClick  = { onPlay(false) },
                colors   = ButtonDefaults.colors(
                    containerColor        = WatchDawgColors.Surface,
                    contentColor          = WatchDawgColors.TextSecondary,
                    focusedContainerColor = WatchDawgColors.SurfaceFocused,
                    focusedContentColor   = WatchDawgColors.TextPrimary,
                ),
                modifier = Modifier
                    .fillMaxWidth()
                    .focusGlow(),
            ) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text("▶  Split Stream", style = MaterialTheme.typography.titleMedium)
                    Text(
                        "Best quality · No seeking · HLS fallback",
                        style = MaterialTheme.typography.labelMedium,
                        color = WatchDawgColors.TextTertiary,
                    )
                }
            }

            Button(
                onClick  = onDismiss,
                colors   = ButtonDefaults.colors(
                    containerColor        = WatchDawgColors.Surface,
                    contentColor          = WatchDawgColors.TextTertiary,
                    focusedContainerColor = WatchDawgColors.SurfaceFocused,
                    focusedContentColor   = WatchDawgColors.TextSecondary,
                ),
                modifier = Modifier
                    .fillMaxWidth()
                    .focusGlow(),
            ) {
                Text("✕  Cancel", style = MaterialTheme.typography.titleMedium)
            }
        }
    }
}
