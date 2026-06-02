package com.watchdawg.tv.ui.series

import androidx.activity.compose.BackHandler
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
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.lazy.grid.rememberLazyGridState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import kotlinx.coroutines.delay
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
import com.watchdawg.tv.ui.feed.VideoCard
import com.watchdawg.tv.ui.theme.WatchDawgColors
import com.watchdawg.tv.ui.theme.focusGlow
import kotlinx.coroutines.launch
import java.time.LocalDate
import java.time.format.DateTimeFormatter
import java.util.Locale

/**
 * Episode list drill-down screen — Milestone R-2.5.
 *
 * Milestone R-2.5 simplification: removed all mini-player machinery.
 *   - [miniPlayerActive] param — removed
 *   - [lastPlayedVideoId] param — removed
 *   - [episodeFocusRequester] param — removed (was owned by MainActivity to
 *     survive the miniPlayerActive early-return guard; that guard is gone)
 *
 * This is now a clean full-screen content view. Focus on first card is handled
 * by a simple LaunchedEffect(state) with a 150ms layout delay — no races,
 * no external FocusRequester, no guard to work around.
 *
 * Back navigation: popBackStack() via [onBack] → returns to TVScreen (series grid).
 * SeriesScreen restores focus to the tapped series card via lastTappedSeriesChannelId.
 */
@Composable
fun EpisodeListScreen(
    channelId: Int,
    channelName: String,
    viewModel: SeriesViewModel,
    onPlay: (videoId: Int, queue: List<Int>, index: Int, hlsMode: Boolean) -> Unit,
    onBack: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val state        by viewModel.episodeState.collectAsStateWithLifecycle()
    val sort         by viewModel.episodeSort.collectAsStateWithLifecycle()
    val pendingQueue by viewModel.pendingQueue.collectAsStateWithLifecycle()

    val gridState = rememberLazyGridState()
    val scope     = rememberCoroutineScope()

    // pendingPlay holds the episode the user tapped — triggers PlayModeMenu.
    // Null = no menu visible.
    var pendingPlay by remember { mutableStateOf<VideoDto?>(null) }
    val playFocusRequester = remember { FocusRequester() }

    // First-card FocusRequester — owned locally, no external hoisting needed.
    val firstCardFocus = remember { FocusRequester() }

    LaunchedEffect(channelId) {
        viewModel.loadEpisodes(channelId)
    }

    DisposableEffect(channelId) {
        onDispose { viewModel.clearEpisodes() }
    }

    // One-shot: Play All / Shuffle queue ready — fire play (always split-stream).
    LaunchedEffect(pendingQueue) {
        val q = pendingQueue ?: return@LaunchedEffect
        if (q.ids.isNotEmpty()) {
            onPlay(q.ids[q.startIndex], q.ids, q.startIndex, false)
            viewModel.clearPendingQueue()
        }
    }

    // Auto-focus the Play Mode menu button when it appears.
    LaunchedEffect(pendingPlay) {
        if (pendingPlay != null) {
            try { playFocusRequester.requestFocus() } catch (_: Exception) {}
        }
    }

    // Grab focus on first card after episodes load.
    // 150ms delay: LazyVerticalGrid must finish its first layout pass before
    // requestFocus() can succeed. This is the only focus logic needed now —
    // no NavRail to steal focus, no mini-player guard to work around.
    LaunchedEffect(state) {
        if (state is SeriesViewModel.EpisodeState.Success &&
            (state as SeriesViewModel.EpisodeState.Success).data.episodes.isNotEmpty()
        ) {
            delay(150)
            try { firstCardFocus.requestFocus() } catch (_: Exception) {}
        }
    }

    BackHandler { onBack() }

    Box(modifier = modifier.fillMaxSize()) {

        Column(modifier = Modifier.fillMaxSize()) {

            // ── Header ────────────────────────────────────────────────────────
            Row(
                verticalAlignment     = Alignment.Bottom,
                horizontalArrangement = Arrangement.SpaceBetween,
                modifier              = Modifier.fillMaxWidth(),
            ) {
                Column {
                    Text(
                        text  = channelName,
                        style = MaterialTheme.typography.displayLarge,
                        color = WatchDawgColors.TextPrimary,
                    )
                    val subtitle = when (val s = state) {
                        is SeriesViewModel.EpisodeState.Success ->
                            "${s.data.episodes.size} episodes"
                        is SeriesViewModel.EpisodeState.Loading -> "Loading…"
                        is SeriesViewModel.EpisodeState.Error   -> "Error"
                        is SeriesViewModel.EpisodeState.Idle    -> ""
                    }
                    if (subtitle.isNotEmpty()) {
                        Text(
                            text  = subtitle,
                            style = MaterialTheme.typography.bodyLarge,
                            color = WatchDawgColors.TextTertiary,
                        )
                    }
                }
            }

            Spacer(Modifier.height(12.dp))

            // ── Action bar ────────────────────────────────────────────────────
            Row(
                horizontalArrangement = Arrangement.spacedBy(12.dp),
                verticalAlignment     = Alignment.CenterVertically,
                modifier              = Modifier.fillMaxWidth(),
            ) {
                Button(
                    onClick  = onBack,
                    colors   = ButtonDefaults.colors(
                        containerColor        = WatchDawgColors.Surface,
                        contentColor          = WatchDawgColors.TextSecondary,
                        focusedContainerColor = WatchDawgColors.SurfaceFocused,
                        focusedContentColor   = WatchDawgColors.TextPrimary,
                    ),
                    modifier = Modifier.focusGlow(),
                ) {
                    Text("← Back", style = MaterialTheme.typography.titleSmall)
                }

                if (state is SeriesViewModel.EpisodeState.Success) {
                    Button(
                        onClick  = { viewModel.playEpisodes() },
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
                        onClick  = { viewModel.shuffleEpisodes() },
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

                    val sortLabel = when (sort) {
                        SeriesViewModel.EpisodeSort.NEWEST_FIRST -> "🕐 Newest First"
                        SeriesViewModel.EpisodeSort.TITLE_ASC    -> "🔤 A → Z"
                    }
                    Button(
                        onClick  = { viewModel.toggleSort() },
                        colors   = ButtonDefaults.colors(
                            containerColor        = WatchDawgColors.Surface,
                            contentColor          = WatchDawgColors.TextSecondary,
                            focusedContainerColor = WatchDawgColors.SurfaceFocused,
                            focusedContentColor   = WatchDawgColors.TextPrimary,
                        ),
                        modifier = Modifier.focusGlow(),
                    ) {
                        Text(sortLabel, style = MaterialTheme.typography.titleSmall)
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

            Spacer(Modifier.height(16.dp))

            // ── Content ───────────────────────────────────────────────────────
            when (val s = state) {
                is SeriesViewModel.EpisodeState.Idle,
                is SeriesViewModel.EpisodeState.Loading -> {
                    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                        Text(
                            text  = "Loading episodes…",
                            style = MaterialTheme.typography.titleLarge,
                            color = WatchDawgColors.TextSecondary,
                        )
                    }
                }

                is SeriesViewModel.EpisodeState.Error -> {
                    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                        Column(horizontalAlignment = Alignment.CenterHorizontally) {
                            Text("📺", style = MaterialTheme.typography.displayLarge)
                            Spacer(Modifier.height(12.dp))
                            Text(
                                text  = "Could not load episodes.",
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

                is SeriesViewModel.EpisodeState.Success -> {
                    val rawEpisodes = s.data.episodes

                    if (rawEpisodes.isEmpty()) {
                        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                                Text("📺", style = MaterialTheme.typography.displayLarge)
                                Spacer(Modifier.height(12.dp))
                                Text(
                                    text  = "No episodes found.",
                                    style = MaterialTheme.typography.titleLarge,
                                    color = WatchDawgColors.TextSecondary,
                                )
                            }
                        }
                    } else {
                        val episodes   = remember(rawEpisodes, sort) { viewModel.sorted(rawEpisodes) }
                        val episodeIds = remember(episodes) { episodes.map { it.id } }
                        val labelMap   = remember(episodes) {
                            episodes.associate { ep -> ep.id to episodeLabelFor(ep) }
                        }

                        // Per-card FocusRequesters for scroll-to-focus restore.
                        val cardFocusRequesters = remember { mutableMapOf<Int, FocusRequester>() }

                        LazyVerticalGrid(
                            state   = gridState,
                            columns = GridCells.Fixed(4),
                            contentPadding        = PaddingValues(top = 8.dp, bottom = 48.dp),
                            horizontalArrangement = Arrangement.spacedBy(20.dp),
                            verticalArrangement   = Arrangement.spacedBy(20.dp),
                            modifier = Modifier.fillMaxSize(),
                        ) {
                            items(episodes, key = { it.id }) { episode ->
                                val index  = episodeIds.indexOf(episode.id)
                                val cardFr = cardFocusRequesters.getOrPut(episode.id) {
                                    FocusRequester()
                                }
                                val isFirst = index == 0

                                VideoCard(
                                    video        = episode,
                                    episodeLabel = labelMap[episode.id],
                                    onPlay       = { pendingPlay = episode },
                                    modifier     = if (isFirst)
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
        // Shown when the user taps a single episode card. Matches the behaviour
        // of the old FeedScreen flat grid. Play All / Shuffle bypass this menu.
        if (pendingPlay != null) {
            val episode = pendingPlay!!
            PlayModeMenu(
                onPlay = { hlsMode ->
                    val episodes = (state as? SeriesViewModel.EpisodeState.Success)
                        ?.data?.episodes ?: emptyList()
                    val sorted     = viewModel.sorted(episodes)
                    val episodeIds = sorted.map { it.id }
                    val index      = episodeIds.indexOf(episode.id).coerceAtLeast(0)
                    pendingPlay = null
                    onPlay(episode.id, episodeIds, index, hlsMode)
                },
                onDismiss          = { pendingPlay = null },
                playFocusRequester = playFocusRequester,
            )
        }
    }
}

// ── Play mode menu ────────────────────────────────────────────────────────────

@Composable
private fun PlayModeMenu(
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

            Button(
                onClick  = { onPlay(false) },
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
                    Text("▶  Play", style = MaterialTheme.typography.titleMedium)
                    Text(
                        "Best quality · No seeking",
                        style = MaterialTheme.typography.labelMedium,
                        color = WatchDawgColors.TextSecondary,
                    )
                }
            }

            Button(
                onClick  = { onPlay(true) },
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
                    Text("⚡  HLS Mode", style = MaterialTheme.typography.titleMedium)
                    Text(
                        "Seekable · Lower max quality",
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

// ── Episode label logic ───────────────────────────────────────────────────────

private fun episodeLabelFor(video: VideoDto): String? {
    val title = video.title.orEmpty()

    val patternA = Regex("""[Ss](\d{1,2})\s*[Ee](\d{1,2})""", RegexOption.IGNORE_CASE)
    patternA.find(title)?.let { m ->
        val s = m.groupValues[1].toIntOrNull() ?: return@let
        val e = m.groupValues[2].toIntOrNull() ?: return@let
        return "S%02d · E%02d".format(s, e)
    }

    val patternB = Regex("""[Ss]eason\s+(\d{1,2})\s+[Ee]pisode\s+(\d{1,2})""", RegexOption.IGNORE_CASE)
    patternB.find(title)?.let { m ->
        val s = m.groupValues[1].toIntOrNull() ?: return@let
        val e = m.groupValues[2].toIntOrNull() ?: return@let
        return "S%02d · E%02d".format(s, e)
    }

    val patternC = Regex("""(\d{1,2})x(\d{2})""")
    patternC.find(title)?.let { m ->
        val s = m.groupValues[1].toIntOrNull() ?: return@let
        val e = m.groupValues[2].toIntOrNull() ?: return@let
        return "S%02d · E%02d".format(s, e)
    }

    val createdAt = video.createdAt ?: return null
    return try {
        val date = LocalDate.parse(
            createdAt.take(10),
            DateTimeFormatter.ISO_LOCAL_DATE,
        )
        val monthAbbr = date.month.getDisplayName(java.time.format.TextStyle.SHORT, Locale.US)
        "Added $monthAbbr ${date.dayOfMonth}"
    } catch (_: Exception) {
        null
    }
}
