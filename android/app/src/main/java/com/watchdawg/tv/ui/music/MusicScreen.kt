package com.watchdawg.tv.ui.music

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
 * Music screen — Milestone R-4.
 *
 * Flat 4-column 16:9 landscape grid with genre pill bar.
 * Tap card → PlayModeMenu (HLS focused by default — seeking important for music videos).
 * Play All / Shuffle All load the full ID list via /feed/ids?category=music.
 * Shuffle All always shuffles ALL matching videos in the database, not just on-screen.
 *
 * Layout:
 *   ┌──────────────────────────────────────────────────────────────┐
 *   │  🎵  Music                         [▶ Play All] [🔀 Shuffle] │
 *   │  Genre pills: [All] [Hot] [Rock] [70s] [Classic Rock] …      │
 *   ├──────────────────────────────────────────────────────────────┤
 *   │  [16:9][16:9][16:9][16:9]                                    │
 *   │  [16:9][16:9][16:9][16:9]                                    │
 *   │  …                                                           │
 *   └──────────────────────────────────────────────────────────────┘
 *
 * Back navigation: popBackStack() → Home.
 * Long-press Back: handled by MainActivity root → Home.
 */
@Composable
fun MusicScreen(
    viewModel: MusicViewModel,
    onPlay: (videoId: Int, queue: List<Int>, index: Int, hlsMode: Boolean) -> Unit,
    onBack: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val musicState    by viewModel.musicState.collectAsStateWithLifecycle()
    val genreState    by viewModel.genreState.collectAsStateWithLifecycle()
    val selectedGenre by viewModel.selectedGenre.collectAsStateWithLifecycle()
    val pendingQueue  by viewModel.pendingQueue.collectAsStateWithLifecycle()

    val gridState = rememberLazyGridState()
    val scope     = rememberCoroutineScope()

    // PlayModeMenu state — which video was tapped
    var pendingPlay by remember { mutableStateOf<VideoDto?>(null) }
    val playFocusRequester = remember { FocusRequester() }
    val firstCardFocus     = remember { FocusRequester() }

    // Load genres once
    LaunchedEffect(Unit) {
        viewModel.loadGenres()
    }

    // Reload music whenever genre selection changes
    LaunchedEffect(selectedGenre) {
        viewModel.loadMusic(genreTag = selectedGenre)
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
    LaunchedEffect(musicState) {
        if (musicState is MusicViewModel.MusicState.Ready &&
            (musicState as MusicViewModel.MusicState.Ready).videos.isNotEmpty()
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

    val tags = (genreState as? MusicViewModel.GenreState.Ready)?.tags ?: emptyList()

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
                        text  = "🎵  Music",
                        style = MaterialTheme.typography.displayLarge,
                        color = WatchDawgColors.TextPrimary,
                    )
                    val subtitle = when (val s = musicState) {
                        is MusicViewModel.MusicState.Ready   -> "${s.videos.size} videos"
                        is MusicViewModel.MusicState.Loading -> "Loading…"
                        is MusicViewModel.MusicState.Error   -> "Error"
                    }
                    Text(
                        text  = subtitle,
                        style = MaterialTheme.typography.bodyLarge,
                        color = WatchDawgColors.TextTertiary,
                    )
                }

                // Action buttons — only when videos are loaded
                if (musicState is MusicViewModel.MusicState.Ready) {
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
                    }
                }
            }

            Spacer(Modifier.height(8.dp))

            // ── Genre pill bar ─────────────────────────────────────────────────
            MusicGenrePillBar(
                tags          = tags,
                selectedGenre = selectedGenre,
                onSelectAll   = { viewModel.selectGenre(null) },
                onSelectGenre = { viewModel.selectGenre(it) },
            )

            Spacer(Modifier.height(12.dp))

            // ── Content ───────────────────────────────────────────────────────
            when (val s = musicState) {
                is MusicViewModel.MusicState.Loading -> {
                    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                        Text(
                            text  = "Loading…",
                            style = MaterialTheme.typography.titleLarge,
                            color = WatchDawgColors.TextTertiary,
                        )
                    }
                }

                is MusicViewModel.MusicState.Error -> {
                    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                        Text(
                            text  = "Could not load music: ${s.message}",
                            style = MaterialTheme.typography.titleMedium,
                            color = WatchDawgColors.FailedBadge,
                        )
                    }
                }

                is MusicViewModel.MusicState.Ready -> {
                    if (s.videos.isEmpty()) {
                        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                            Text(
                                text  = if (selectedGenre != null) "No videos for \"$selectedGenre\"" else "No music videos yet.",
                                style = MaterialTheme.typography.titleLarge,
                                color = WatchDawgColors.TextTertiary,
                            )
                        }
                    } else {
                        // 4-column 16:9 grid
                        LazyVerticalGrid(
                            columns               = GridCells.Fixed(4),
                            state                 = gridState,
                            contentPadding        = PaddingValues(bottom = 48.dp),
                            horizontalArrangement = Arrangement.spacedBy(12.dp),
                            verticalArrangement   = Arrangement.spacedBy(12.dp),
                            modifier              = Modifier.fillMaxSize(),
                        ) {
                            items(s.videos, key = { it.id }) { video ->
                                val idx    = s.videos.indexOf(video)
                                val cardFr = remember { FocusRequester() }
                                MusicCard(
                                    video   = video,
                                    onPlay  = { pendingPlay = video },
                                    modifier = if (idx == 0)
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
            val video = pendingPlay!!
            MusicPlayModeMenu(
                onPlay = { hlsMode ->
                    val videos = (musicState as? MusicViewModel.MusicState.Ready)
                        ?.videos ?: emptyList()
                    val ids   = videos.map { it.id }
                    val index = ids.indexOf(video.id).coerceAtLeast(0)
                    pendingPlay = null
                    onPlay(video.id, ids, index, hlsMode)
                },
                onDismiss          = { pendingPlay = null },
                playFocusRequester = playFocusRequester,
            )
        }
    }
}

// ── Genre pill bar ────────────────────────────────────────────────────────────

@Composable
private fun MusicGenrePillBar(
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
            MusicGenrePill(
                label      = "All",
                isSelected = selectedGenre == null,
                onClick    = onSelectAll,
            )
        }
        items(tags, key = { it }) { tag ->
            MusicGenrePill(
                label      = tag,
                isSelected = selectedGenre == tag,
                onClick    = { onSelectGenre(tag) },
            )
        }
    }
}

@Composable
private fun MusicGenrePill(
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
 * Play mode selection overlay for music videos.
 *
 * HLS is the default focused option — music videos can be long and users
 * need to seek. Split Stream is available as a fallback.
 */
@Composable
private fun MusicPlayModeMenu(
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

            // HLS — default focus, recommended (seekable)
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
                    .focusRequester(playFocusRequester)
                    .focusGlow(),
            ) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text("⚡  HLS Mode", style = MaterialTheme.typography.titleMedium)
                    Text(
                        "Seekable · Recommended",
                        style = MaterialTheme.typography.labelMedium,
                        color = WatchDawgColors.TextSecondary,
                    )
                }
            }

            // Split Stream — fallback
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
