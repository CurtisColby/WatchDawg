package com.watchdawg.tv.ui.series

import androidx.activity.compose.BackHandler
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
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.lazy.grid.rememberLazyGridState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
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
 * Milestone F — Episode list drill-down screen.
 *
 * Shown after tapping a series card in SeriesScreen. Displays all episodes
 * for one TV-category channel with a user-togglable sort order.
 *
 * Polish additions:
 *  - Sort toggle: 🕐 Newest First (default) ↔ 🔤 A → Z
 *  - Play All — queues all episodes in current sort order.
 *  - Shuffle — queues all episodes in random order.
 *  - ⬆ Top — scrolls grid back to item 0.
 *  - Episode label on each card (Option B + C):
 *      Priority 1 — Parse S##E## / Season # Episode # from the title.
 *                   Shown in orange as "S02 · E05".
 *      Priority 2 — "Added MMM DD" from createdAt if no S/E found.
 *                   Shown in orange as a date fallback.
 *      Priority 3 — Nothing shown (label = null). Existing card behaviour.
 *    Label is computed once per episode list load via remember(episodes).
 *    All regex work is pure Kotlin — zero network, zero DB, instant.
 *
 * D-pad rules (Session 26 hard-won lessons):
 *  - VideoCard handles its own focus/glow — no extra FocusRequester here.
 *  - Compose TV grid resolver handles all D-pad movement naturally.
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

    LaunchedEffect(channelId) {
        viewModel.loadEpisodes(channelId)
    }

    DisposableEffect(channelId) {
        onDispose { viewModel.clearEpisodes() }
    }

    // One-shot: Play All / Shuffle queue ready — fire play and clear.
    LaunchedEffect(pendingQueue) {
        val q = pendingQueue ?: return@LaunchedEffect
        if (q.ids.isNotEmpty()) {
            onPlay(q.ids[q.startIndex], q.ids, q.startIndex, false)
            viewModel.clearPendingQueue()
        }
    }

    BackHandler { onBack() }

    Column(modifier = modifier.fillMaxSize()) {

        // ── Header ────────────────────────────────────────────────────────────
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

        // ── Action bar ────────────────────────────────────────────────────────
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

        // ── Content ───────────────────────────────────────────────────────────
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
                    // Apply sort — recomputed when sort mode or raw list changes.
                    val episodes = remember(rawEpisodes, sort) {
                        viewModel.sorted(rawEpisodes)
                    }

                    // Build episode ID list in current sort order for queue.
                    val episodeIds = remember(episodes) { episodes.map { it.id } }

                    // Compute episode labels once per sorted list.
                    // Each label is either "S## · E##", "Added MMM DD", or null.
                    // Stored in a map keyed by video ID for O(1) lookup per card.
                    val labelMap = remember(episodes) {
                        episodes.associate { ep -> ep.id to episodeLabelFor(ep) }
                    }

                    LazyVerticalGrid(
                        state   = gridState,
                        columns = GridCells.Fixed(4),
                        contentPadding = PaddingValues(top = 8.dp, bottom = 48.dp),
                        horizontalArrangement = Arrangement.spacedBy(20.dp),
                        verticalArrangement   = Arrangement.spacedBy(20.dp),
                        modifier = Modifier.fillMaxSize(),
                    ) {
                        items(episodes, key = { it.id }) { episode ->
                            val index = episodeIds.indexOf(episode.id)
                            VideoCard(
                                video        = episode,
                                episodeLabel = labelMap[episode.id],
                                onPlay       = {
                                    onPlay(episode.id, episodeIds, index, false)
                                },
                            )
                        }
                    }
                }
            }
        }
    }
}

// ── Episode label logic ───────────────────────────────────────────────────────

/**
 * Derive a human-readable episode label for a single video.
 *
 * Priority 1 — S/E number parsed from the title.
 *   Matches patterns common in YouTube TV channel uploads:
 *     "S2 E5 — Episode Title"
 *     "S02E05 Episode Title"
 *     "Season 2 Episode 5"
 *     "2x05 Episode Title"
 *   Returns formatted as "S02 · E05" (always zero-padded to 2 digits).
 *
 * Priority 2 — Date added from createdAt.
 *   createdAt is an ISO-8601 string ("2024-03-15T10:23:00").
 *   Returns formatted as "Added Mar 15" (month abbreviated, no year).
 *   Year omitted — episodes from the same show are almost always the same
 *   year and it keeps the label short enough to fit on the card.
 *
 * Priority 3 — null.
 *   Returned when createdAt is missing or unparseable.
 *   VideoCard shows nothing for a null label (existing behaviour).
 *
 * This function is pure — no side effects, no network, no DB.
 * Called inside remember(episodes) so it runs at most once per list load.
 */
private fun episodeLabelFor(video: VideoDto): String? {
    val title = video.title.orEmpty()

    // ── Priority 1: parse season/episode from title ───────────────────────────

    // Pattern A: S2E5, S02E05, S2 E5, S02 E05 (with optional separator)
    val patternA = Regex(
        """[Ss](\d{1,2})\s*[Ee](\d{1,2})""",
        RegexOption.IGNORE_CASE,
    )
    patternA.find(title)?.let { m ->
        val s = m.groupValues[1].toIntOrNull() ?: return@let
        val e = m.groupValues[2].toIntOrNull() ?: return@let
        return "S%02d · E%02d".format(s, e)
    }

    // Pattern B: Season 2 Episode 5 (long-form, case-insensitive)
    val patternB = Regex(
        """[Ss]eason\s+(\d{1,2})\s+[Ee]pisode\s+(\d{1,2})""",
        RegexOption.IGNORE_CASE,
    )
    patternB.find(title)?.let { m ->
        val s = m.groupValues[1].toIntOrNull() ?: return@let
        val e = m.groupValues[2].toIntOrNull() ?: return@let
        return "S%02d · E%02d".format(s, e)
    }

    // Pattern C: 2x05 (common in older show naming)
    val patternC = Regex("""(\d{1,2})x(\d{2})""")
    patternC.find(title)?.let { m ->
        val s = m.groupValues[1].toIntOrNull() ?: return@let
        val e = m.groupValues[2].toIntOrNull() ?: return@let
        return "S%02d · E%02d".format(s, e)
    }

    // ── Priority 2: date added from createdAt ─────────────────────────────────

    val createdAt = video.createdAt ?: return null
    return try {
        // Backend sends ISO-8601: "2024-03-15T10:23:00" or "2024-03-15T10:23:00.000000"
        // LocalDate.parse handles the date portion directly.
        val date = LocalDate.parse(
            createdAt.take(10),                       // "2024-03-15"
            DateTimeFormatter.ISO_LOCAL_DATE,
        )
        val monthAbbr = date.month
            .getDisplayName(java.time.format.TextStyle.SHORT, Locale.US)  // "Mar"
        "Added $monthAbbr ${date.dayOfMonth}"         // "Added Mar 15"
    } catch (_: Exception) {
        null   // Unparseable date — show nothing, never crash
    }
}
