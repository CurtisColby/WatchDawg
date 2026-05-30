package com.watchdawg.tv.ui.feed

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
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.lazy.grid.rememberLazyGridState
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
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
import com.watchdawg.tv.data.auth.TokenHolder
import com.watchdawg.tv.ui.series.SeriesScreen
import com.watchdawg.tv.ui.series.SeriesViewModel
import com.watchdawg.tv.ui.theme.WatchDawgColors
import com.watchdawg.tv.ui.theme.focusGlow
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

// Number of grid columns — used to calculate one-row jump distance for fast scroll
private const val GRID_COLUMNS = 4

// Minimum milliseconds between accelerated scroll steps when D-pad is held.
private const val SCROLL_DEBOUNCE_MS = 150L

/**
 * Category pill definitions.
 *
 * [key] maps to the backend category string (null = All / no filter).
 * [label] is display text.
 * [locked] true means the pill is hidden when the session is locked.
 */
private data class CategoryPill(val key: String?, val label: String, val locked: Boolean = false)

private val CATEGORY_PILLS = listOf(
    CategoryPill(null,     "All"),
    CategoryPill("music",  "🎵 Music"),
    CategoryPill("ccm",    "✝️ CCM"),
    CategoryPill("chill",  "🌙 Chill"),
    CategoryPill("nature", "🌿 Nature"),
    CategoryPill("movies", "🎬 Movies"),
    CategoryPill("tv",     "📺 TV"),
    CategoryPill("general","📡 General"),
    // PIN-gated pills — hidden when locked
    CategoryPill("vimeo",  "▶ Vimeo",  locked = true),
    CategoryPill("sexy",   "🔥 Sexy",  locked = true),
    CategoryPill("adult",  "🔥 Adult", locked = true),
)

/**
 * Feed screen: category pill bar + header + action buttons + video grid.
 *
 * Milestone F: When the "📺 TV" category pill is active, the standard flat
 * video grid is replaced by [SeriesScreen] which shows one card per TV-category
 * channel. All other pills continue to use the flat grid — zero behavior change.
 *
 * Session 25: focusGlow() applied to all interactive elements — pills,
 * action bar buttons, resume banner buttons, and play mode menu buttons.
 *
 * Session 26 Bug 3 regression fix: added LaunchedEffect(Unit) { viewModel.refresh() }
 * so the feed re-fetches every time this screen enters composition.
 *
 * Root cause of the regression: the Bug 2 fix (mini-player BackHandler) now navigates
 * to Routes.FEED which causes NavHost to tear down and recreate the FeedScreen
 * composable. When the app is locked via PIN while a video is playing, onSessionLocked()
 * is called on FeedViewModel (setting lockedChannelsHidden=true and calling refresh()),
 * but the FeedScreen composable is either not rendered (miniPlayerActive guard) or
 * about to be recreated by the navigation. On re-entry the composable must call
 * refresh() itself to display the correctly filtered locked feed. Without this,
 * adult thumbnails remain visible after locking because the stale pre-lock video
 * list is still in the composition from before the mini-player navigation.
 *
 * Note: FeedViewModel.refresh() respects the current lock state (lockedChannelsHidden,
 * selectedChannelIds) already set by onSessionLocked(), so calling refresh() on
 * re-entry never undoes the lock — it re-fetches using the already-locked state.
 */
@Composable
fun FeedScreen(
    viewModel: FeedViewModel,
    seriesViewModel: SeriesViewModel,
    onPlay: (videoId: Int, queue: List<Int>, index: Int, hlsMode: Boolean) -> Unit,
    onResumePlay: (videoId: Int, queue: List<Int>, index: Int, positionMs: Long) -> Unit,
    onSeriesTap: (channelId: Int, channelName: String) -> Unit,
    modifier: Modifier = Modifier,
) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    val gridState = rememberLazyGridState()
    val scope = rememberCoroutineScope()
    val isUnlocked by TokenHolder.tokenFlow.collectAsStateWithLifecycle()

    var lastScrollMs by remember { mutableLongStateOf(0L) }

    data class PendingPlay(val videoId: Int)
    var pendingPlay by remember { mutableStateOf<PendingPlay?>(null) }
    val playFocusRequester = remember { FocusRequester() }

    // Regression fix (Session 26): refresh on every screen entry so the feed
    // immediately reflects the current lock state.
    LaunchedEffect(Unit) {
        viewModel.refresh()
    }

    // One-shot: Play All / Shuffle All queue ready
    LaunchedEffect(state.pendingQueue) {
        val q = state.pendingQueue ?: return@LaunchedEffect
        if (q.ids.isNotEmpty()) {
            onPlay(q.ids[q.startIndex], q.ids, q.startIndex, false)
            viewModel.clearPendingQueue()
        }
    }

    LaunchedEffect(pendingPlay) {
        if (pendingPlay != null) {
            delay(50)
            try { playFocusRequester.requestFocus() } catch (_: Exception) {}
        }
    }

    LaunchedEffect(state.actionMessage) {
        if (state.actionMessage != null) {
            delay(4_000)
            viewModel.clearActionMessage()
        }
    }

    // Milestone F: TV pill active — hand off entirely to SeriesScreen.
    // SeriesScreen manages its own header, loading state, and grid.
    // The pill bar and action bar are still rendered above it so the user
    // can switch back to another pill without going through a back stack.
    val tvPillActive = state.selectedCategory == "tv"

    Box(modifier = modifier.fillMaxSize()) {
        Column(modifier = Modifier.fillMaxSize().padding(end = 32.dp, top = 28.dp)) {

            // ── Header (hidden when TV pill active — SeriesScreen has its own) ──
            if (!tvPillActive) {
                FeedHeader(
                    total   = state.total,
                    shown   = state.videos.size,
                    loading = state.loading,
                )
                Spacer(Modifier.height(12.dp))
            }

            CategoryPillBar(
                selected   = state.selectedCategory,
                isUnlocked = isUnlocked != null,
                onSelect   = { viewModel.setCategory(it) },
            )

            Spacer(Modifier.height(12.dp))

            // ── TV pill: delegate to SeriesScreen ─────────────────────────────
            if (tvPillActive) {
                SeriesScreen(
                    viewModel    = seriesViewModel,
                    onSeriesTap  = onSeriesTap,
                    onPlay       = onPlay,
                    modifier     = Modifier.fillMaxSize(),
                )
                // Return early — no resume banner, action bar, or video grid
                // are rendered when the TV pill is active.
                return@Column
            }

            // ── Standard feed (all non-TV pills) ─────────────────────────────

            val resumeData = state.pendingResume
            if (resumeData != null) {
                ResumeBanner(
                    title       = resumeData.title,
                    positionMs  = resumeData.positionMs,
                    onContinue  = {
                        viewModel.clearResume()
                        onResumePlay(resumeData.videoId, listOf(resumeData.videoId), 0, resumeData.positionMs)
                    },
                    onStartOver = { viewModel.clearResume() },
                )
                Spacer(Modifier.height(12.dp))
            }

            FeedActionBar(
                queueLoading = state.queueLoading,
                scraping     = state.scraping,
                resolving    = state.resolving,
                onPlayAll    = { viewModel.playAll() },
                onShuffleAll = { viewModel.shuffleAll() },
                onRefresh    = { viewModel.refresh() },
                onScrape     = { viewModel.scrape() },
                onResolve    = { viewModel.resolveBatch() },
            )

            if (state.actionMessage != null) {
                Spacer(Modifier.height(6.dp))
                Text(
                    text  = state.actionMessage!!,
                    style = MaterialTheme.typography.bodySmall,
                    color = WatchDawgColors.TextTertiary,
                )
            }

            Spacer(Modifier.height(12.dp))

            if (state.videos.isEmpty() && !state.loading) {
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Text("📡", style = MaterialTheme.typography.displayLarge)
                        Spacer(Modifier.height(12.dp))
                        Text(
                            text  = "No videos yet.",
                            style = MaterialTheme.typography.titleLarge,
                            color = WatchDawgColors.TextSecondary,
                        )
                        Text(
                            text  = "Add a channel in the web UI and scrape.",
                            style = MaterialTheme.typography.bodyMedium,
                            color = WatchDawgColors.TextTertiary,
                            modifier = Modifier.padding(top = 8.dp),
                        )
                    }
                }
            } else {
                LazyVerticalGrid(
                    state   = gridState,
                    columns = GridCells.Fixed(GRID_COLUMNS),
                    contentPadding = PaddingValues(top = 16.dp, bottom = 48.dp),
                    horizontalArrangement = Arrangement.spacedBy(20.dp),
                    verticalArrangement   = Arrangement.spacedBy(20.dp),
                    modifier = Modifier
                        .fillMaxSize()
                        .onKeyEvent { event ->
                            if (event.type != KeyEventType.KeyDown) return@onKeyEvent false
                            val now = System.currentTimeMillis()
                            if (now - lastScrollMs < SCROLL_DEBOUNCE_MS) return@onKeyEvent false
                            when (event.key) {
                                Key.DirectionDown -> {
                                    lastScrollMs = now
                                    scope.launch {
                                        val first = gridState.firstVisibleItemIndex
                                        gridState.animateScrollToItem((first + GRID_COLUMNS).coerceAtMost(state.videos.size - 1))
                                    }
                                    false // let Compose TV move focus naturally
                                }
                                Key.DirectionUp -> {
                                    lastScrollMs = now
                                    scope.launch {
                                        val first = gridState.firstVisibleItemIndex
                                        gridState.animateScrollToItem((first - GRID_COLUMNS).coerceAtLeast(0))
                                    }
                                    false
                                }
                                else -> false
                            }
                        },
                ) {
                    items(state.videos, key = { it.id }) { video ->
                        VideoCard(
                            video  = video,
                            onPlay = { pendingPlay = PendingPlay(videoId = video.id) },
                        )
                    }
                }
            }
        }

        // ── Play mode menu overlay ────────────────────────────────────────────
        if (pendingPlay != null) {
            PlayModeMenu(
                onPlay = { hlsMode ->
                    val p = pendingPlay!!
                    pendingPlay = null
                    onPlay(p.videoId, listOf(p.videoId), 0, hlsMode)
                },
                onDismiss          = { pendingPlay = null },
                playFocusRequester = playFocusRequester,
            )
        }
    }
}

// ── Category pill bar ─────────────────────────────────────────────────────────

@Composable
private fun CategoryPillBar(
    selected: String?,
    isUnlocked: Boolean,
    onSelect: (String?) -> Unit,
) {
    val visiblePills = CATEGORY_PILLS.filter { pill -> !pill.locked || isUnlocked }

    LazyRow(
        horizontalArrangement = Arrangement.spacedBy(8.dp),
        contentPadding        = PaddingValues(end = 16.dp),
        modifier              = Modifier.fillMaxWidth(),
    ) {
        items(visiblePills, key = { it.key ?: "__all__" }) { pill ->
            val isActive = pill.key == selected
            Button(
                onClick = { onSelect(pill.key) },
                colors  = ButtonDefaults.colors(
                    containerColor        = if (isActive) WatchDawgColors.OrangeDim else WatchDawgColors.Surface,
                    contentColor          = if (isActive) WatchDawgColors.Orange else WatchDawgColors.TextSecondary,
                    focusedContainerColor = if (isActive) WatchDawgColors.Orange else WatchDawgColors.SurfaceFocused,
                    focusedContentColor   = if (isActive) WatchDawgColors.Background else WatchDawgColors.TextPrimary,
                ),
                modifier = Modifier.focusGlow(),
            ) {
                Text(text = pill.label, style = MaterialTheme.typography.labelLarge)
            }
        }
    }
}

// ── Resume banner ─────────────────────────────────────────────────────────────

@Composable
private fun ResumeBanner(
    title: String,
    positionMs: Long,
    onContinue: () -> Unit,
    onStartOver: () -> Unit,
) {
    val totalSeconds = (positionMs / 1000).toInt()
    val hours   = totalSeconds / 3600
    val minutes = (totalSeconds % 3600) / 60
    val seconds = totalSeconds % 60
    val timeLabel = if (hours > 0) "%d:%02d:%02d".format(hours, minutes, seconds)
                    else "%d:%02d".format(minutes, seconds)

    Row(
        verticalAlignment     = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp),
        modifier = Modifier
            .fillMaxWidth()
            .clip(MaterialTheme.shapes.medium)
            .background(WatchDawgColors.Surface)
            .padding(horizontal = 20.dp, vertical = 14.dp),
    ) {
        Column(modifier = Modifier.weight(1f)) {
            if (title.isNotBlank()) {
                Text(
                    text     = title,
                    style    = MaterialTheme.typography.titleMedium,
                    color    = WatchDawgColors.TextPrimary,
                    maxLines = 1,
                )
            }
            Text(
                text  = "▶  Continue from $timeLabel?",
                style = MaterialTheme.typography.bodyLarge,
                color = WatchDawgColors.TextSecondary,
            )
        }
        Button(
            onClick  = onContinue,
            colors   = ButtonDefaults.colors(
                containerColor        = WatchDawgColors.OrangeDim,
                contentColor          = WatchDawgColors.Orange,
                focusedContainerColor = WatchDawgColors.Orange,
                focusedContentColor   = WatchDawgColors.Background,
            ),
            modifier = Modifier.focusGlow(),
        ) {
            Text("Continue", style = MaterialTheme.typography.titleSmall)
        }
        Button(
            onClick  = onStartOver,
            colors   = ButtonDefaults.colors(
                containerColor        = WatchDawgColors.Surface,
                contentColor          = WatchDawgColors.TextSecondary,
                focusedContainerColor = WatchDawgColors.SurfaceFocused,
                focusedContentColor   = WatchDawgColors.TextPrimary,
            ),
            modifier = Modifier.focusGlow(),
        ) {
            Text("Start Over", style = MaterialTheme.typography.titleSmall)
        }
    }
}

// ── Action bar ────────────────────────────────────────────────────────────────

@Composable
private fun FeedActionBar(
    queueLoading: Boolean,
    scraping: Boolean,
    resolving: Boolean,
    onPlayAll: () -> Unit,
    onShuffleAll: () -> Unit,
    onRefresh: () -> Unit,
    onScrape: () -> Unit,
    onResolve: () -> Unit,
) {
    Row(
        horizontalArrangement = Arrangement.spacedBy(12.dp),
        verticalAlignment     = Alignment.CenterVertically,
        modifier              = Modifier.fillMaxWidth(),
    ) {
        Button(
            onClick  = { if (!queueLoading) onPlayAll() },
            colors   = ButtonDefaults.colors(
                containerColor        = WatchDawgColors.OrangeDim,
                contentColor          = WatchDawgColors.Orange,
                focusedContainerColor = WatchDawgColors.Orange,
                focusedContentColor   = WatchDawgColors.Background,
            ),
            modifier = Modifier.focusGlow(),
        ) { Text(if (queueLoading) "Loading…" else "▶  Play All", style = MaterialTheme.typography.titleSmall) }

        Button(
            onClick  = { if (!queueLoading) onShuffleAll() },
            colors   = ButtonDefaults.colors(
                containerColor        = WatchDawgColors.OrangeDim,
                contentColor          = WatchDawgColors.Orange,
                focusedContainerColor = WatchDawgColors.Orange,
                focusedContentColor   = WatchDawgColors.Background,
            ),
            modifier = Modifier.focusGlow(),
        ) { Text("🔀  Shuffle", style = MaterialTheme.typography.titleSmall) }

        Button(
            onClick  = onRefresh,
            colors   = ButtonDefaults.colors(
                containerColor        = WatchDawgColors.Surface,
                contentColor          = WatchDawgColors.TextSecondary,
                focusedContainerColor = WatchDawgColors.SurfaceFocused,
                focusedContentColor   = WatchDawgColors.TextPrimary,
            ),
            modifier = Modifier.focusGlow(),
        ) { Text("⟳  Refresh", style = MaterialTheme.typography.titleSmall) }

        Button(
            onClick  = { if (!scraping) onScrape() },
            colors   = ButtonDefaults.colors(
                containerColor        = WatchDawgColors.Surface,
                contentColor          = WatchDawgColors.TextSecondary,
                focusedContainerColor = WatchDawgColors.SurfaceFocused,
                focusedContentColor   = WatchDawgColors.TextPrimary,
            ),
            modifier = Modifier.focusGlow(),
        ) { Text(if (scraping) "Scraping…" else "⬇  Scrape", style = MaterialTheme.typography.titleSmall) }

        Button(
            onClick  = { if (!resolving) onResolve() },
            colors   = ButtonDefaults.colors(
                containerColor        = WatchDawgColors.Surface,
                contentColor          = WatchDawgColors.TextSecondary,
                focusedContainerColor = WatchDawgColors.SurfaceFocused,
                focusedContentColor   = WatchDawgColors.TextPrimary,
            ),
            modifier = Modifier.focusGlow(),
        ) { Text(if (resolving) "Resolving…" else "⚡ Resolve", style = MaterialTheme.typography.titleSmall) }
    }
}

// ── Feed header ───────────────────────────────────────────────────────────────

@Composable
private fun FeedHeader(total: Int, shown: Int, loading: Boolean) {
    Row(verticalAlignment = Alignment.Bottom) {
        Text(
            text  = "Feed",
            style = MaterialTheme.typography.displayLarge,
            color = WatchDawgColors.TextPrimary,
        )
        Spacer(Modifier.width(12.dp))
        Text(
            text     = if (loading) "Loading…" else "$shown / $total",
            style    = MaterialTheme.typography.bodyLarge,
            color    = WatchDawgColors.TextTertiary,
            modifier = Modifier.padding(bottom = 6.dp),
        )
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
