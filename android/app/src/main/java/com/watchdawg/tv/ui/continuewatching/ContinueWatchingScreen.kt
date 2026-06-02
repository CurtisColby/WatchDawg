package com.watchdawg.tv.ui.continuewatching

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
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
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
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.tv.material3.Button
import androidx.tv.material3.ButtonDefaults
import androidx.tv.material3.Card
import androidx.tv.material3.CardDefaults
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import coil.compose.AsyncImage
import com.watchdawg.tv.Graph
import com.watchdawg.tv.data.api.HistoryItemDto
import com.watchdawg.tv.ui.theme.WatchDawgColors
import com.watchdawg.tv.ui.theme.focusGlow
import com.watchdawg.tv.ui.theme.focusGlowCard

/**
 * Continue Watching screen — R-4 update.
 *
 * Added: Clear All button in the header row with a confirmation dialog.
 *   "This will clear your entire watch history. Are you sure?"
 *   Calls viewModel.clearAll() which deletes all items via DELETE /history/{id}.
 *   Only shown when there are items to clear.
 *
 * Session 27: Redesigned from LazyVerticalGrid to LazyColumn using the same
 *   row + remove-pill pattern as WatchLaterScreen and FavoritesScreen.
 *
 * D-pad behavior (identical to Watch Later / Favorites):
 *   Card → D-pad Right → Remove pill gets focus
 *   Remove → D-pad Left → card gets focus
 *
 * Focus rule: Card uses .onFocusChanged { } + .focusGlowCard(bool) with NO
 *   FocusRequester and NO onKeyEvent — Session 26 Bug 1 fix pattern.
 *
 * Milestone E: positionSeconds/durationSeconds are Float? — cast to Int for display.
 */
@Composable
fun ContinueWatchingScreen(
    viewModel: ContinueWatchingViewModel,
    onResumePlay: (videoId: Int, queue: List<Int>, index: Int, positionMs: Long) -> Unit,
    modifier: Modifier = Modifier,
) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    var showClearConfirm by remember { mutableStateOf(false) }

    Box(modifier = modifier.fillMaxSize()) {

        Column(modifier = Modifier.fillMaxSize().padding(end = 32.dp, top = 28.dp)) {
            Text(
                text  = "Continue Watching",
                style = MaterialTheme.typography.displayLarge,
                color = WatchDawgColors.TextPrimary,
            )
            Text(
                text     = "Pick up where you left off",
                style    = MaterialTheme.typography.bodyLarge,
                color    = WatchDawgColors.TextSecondary,
                modifier = Modifier.padding(top = 4.dp),
            )

            Spacer(Modifier.height(12.dp))

            // ── Header buttons ────────────────────────────────────────────────
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                Button(
                    onClick  = { viewModel.refresh() },
                    colors   = ButtonDefaults.colors(
                        containerColor        = WatchDawgColors.Surface,
                        contentColor          = WatchDawgColors.TextSecondary,
                        focusedContainerColor = WatchDawgColors.SurfaceFocused,
                        focusedContentColor   = WatchDawgColors.TextPrimary,
                    ),
                    modifier = Modifier.focusGlow(),
                ) {
                    Text("⟳  Refresh", style = MaterialTheme.typography.titleSmall)
                }

                // Clear All — only when there is history to clear
                if (state.items.isNotEmpty()) {
                    Button(
                        onClick  = { showClearConfirm = true },
                        colors   = ButtonDefaults.colors(
                            containerColor        = WatchDawgColors.Surface,
                            contentColor          = WatchDawgColors.FailedBadge,
                            focusedContainerColor = WatchDawgColors.FailedBadge,
                            focusedContentColor   = Color.White,
                        ),
                        modifier = Modifier.focusGlow(),
                    ) {
                        Text(
                            text  = if (state.clearing) "Clearing…" else "🗑  Clear All",
                            style = MaterialTheme.typography.titleSmall,
                        )
                    }
                }
            }

            Spacer(Modifier.height(12.dp))

            when {
                state.error != null -> {
                    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                        Text(
                            text  = state.error!!,
                            style = MaterialTheme.typography.titleLarge,
                            color = WatchDawgColors.FailedBadge,
                        )
                    }
                }
                state.items.isEmpty() && !state.loading && !state.clearing -> {
                    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                        Column(horizontalAlignment = Alignment.CenterHorizontally) {
                            Text(
                                text  = "⏱",
                                style = MaterialTheme.typography.displayLarge,
                                color = WatchDawgColors.TextTertiary,
                            )
                            Spacer(Modifier.height(12.dp))
                            Text(
                                text  = "Nothing in progress yet",
                                style = MaterialTheme.typography.titleLarge,
                                color = WatchDawgColors.TextSecondary,
                            )
                            Text(
                                text     = "Videos you start watching will appear here.",
                                style    = MaterialTheme.typography.bodyLarge,
                                color    = WatchDawgColors.TextTertiary,
                                modifier = Modifier.padding(top = 6.dp),
                            )
                        }
                    }
                }
                else -> {
                    val queue = state.items.map { it.videoId }
                    LazyColumn(
                        verticalArrangement = Arrangement.spacedBy(10.dp),
                        contentPadding      = PaddingValues(bottom = 48.dp),
                        modifier            = Modifier.fillMaxSize(),
                    ) {
                        items(state.items, key = { it.videoId }) { item ->
                            val index = state.items.indexOfFirst { it.videoId == item.videoId }
                            HistoryRow(
                                item       = item,
                                isRemoving = state.removingIds.contains(item.videoId),
                                onPlay     = {
                                    val hasRealProgress = (item.progressPct ?: 0f) > 2f
                                    val positionMs = if (hasRealProgress)
                                        ((item.positionSeconds ?: 0f).toLong() * 1000L)
                                    else 0L
                                    onResumePlay(item.videoId, queue, index.coerceAtLeast(0), positionMs)
                                },
                                onRemove = { viewModel.removeItem(item.videoId) },
                            )
                        }
                    }
                }
            }
        }

        // ── Clear All confirmation dialog ──────────────────────────────────────
        if (showClearConfirm) {
            ClearHistoryConfirmDialog(
                count     = state.items.size,
                onConfirm = {
                    showClearConfirm = false
                    viewModel.clearAll()
                },
                onDismiss = { showClearConfirm = false },
            )
        }
    }
}

// ── Clear All confirmation dialog ─────────────────────────────────────────────

@Composable
private fun ClearHistoryConfirmDialog(
    count: Int,
    onConfirm: () -> Unit,
    onDismiss: () -> Unit,
) {
    val cancelFocus  = remember { FocusRequester() }
    val confirmFocus = remember { FocusRequester() }

    LaunchedEffect(Unit) {
        try { cancelFocus.requestFocus() } catch (_: Exception) {}
    }

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(Color(0xCC000000))
            .onKeyEvent { event ->
                if (event.type == KeyEventType.KeyUp && event.key == Key.Back) {
                    onDismiss(); true
                } else false
            },
        contentAlignment = Alignment.Center,
    ) {
        Column(
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(24.dp),
            modifier = Modifier
                .background(WatchDawgColors.Surface, RoundedCornerShape(16.dp))
                .padding(horizontal = 56.dp, vertical = 40.dp),
        ) {
            Text(
                text  = "🗑  Clear Watch History?",
                style = MaterialTheme.typography.titleLarge,
                color = WatchDawgColors.TextPrimary,
            )
            Text(
                text  = "This will clear all $count item${if (count != 1) "s" else ""} from your watch history.",
                style = MaterialTheme.typography.bodyMedium,
                color = WatchDawgColors.TextSecondary,
            )
            Row(horizontalArrangement = Arrangement.spacedBy(16.dp)) {
                Button(
                    onClick  = onDismiss,
                    colors   = ButtonDefaults.colors(
                        containerColor        = WatchDawgColors.Surface,
                        contentColor          = WatchDawgColors.TextSecondary,
                        focusedContainerColor = WatchDawgColors.SurfaceFocused,
                        focusedContentColor   = WatchDawgColors.TextPrimary,
                    ),
                    modifier = Modifier.width(140.dp).focusRequester(cancelFocus).focusGlow(),
                ) {
                    Text("Cancel", style = MaterialTheme.typography.titleSmall)
                }
                Button(
                    onClick  = onConfirm,
                    colors   = ButtonDefaults.colors(
                        containerColor        = WatchDawgColors.FailedBadge,
                        contentColor          = Color.White,
                        focusedContainerColor = WatchDawgColors.FailedBadge,
                        focusedContentColor   = Color.White,
                    ),
                    modifier = Modifier.width(140.dp).focusRequester(confirmFocus).focusGlow(),
                ) {
                    Text("Clear All", style = MaterialTheme.typography.titleSmall)
                }
            }
        }
    }
}

// ── History row ───────────────────────────────────────────────────────────────

@Composable
private fun HistoryRow(
    item: HistoryItemDto,
    isRemoving: Boolean,
    onPlay: () -> Unit,
    onRemove: () -> Unit,
) {
    var cardHasFocus by remember { mutableStateOf(false) }

    Row(
        modifier              = Modifier.fillMaxWidth(),
        verticalAlignment     = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Card(
            onClick  = onPlay,
            colors   = CardDefaults.colors(
                containerColor        = WatchDawgColors.Surface,
                focusedContainerColor = WatchDawgColors.SurfaceFocused,
            ),
            scale    = CardDefaults.scale(focusedScale = 1.02f),
            modifier = Modifier
                .weight(1f)
                .focusGlowCard(cardHasFocus)
                .onFocusChanged { cardHasFocus = it.isFocused },
        ) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                modifier          = Modifier.padding(8.dp),
            ) {
                Box(
                    modifier = Modifier
                        .width(160.dp)
                        .aspectRatio(16f / 9f)
                        .clip(MaterialTheme.shapes.small),
                ) {
                    AsyncImage(
                        model              = thumbModel(item.thumbnailUrl),
                        contentDescription = item.title,
                        contentScale       = ContentScale.Crop,
                        modifier           = Modifier
                            .fillMaxSize()
                            .background(WatchDawgColors.SurfaceElevated),
                    )

                    // Orange progress bar at bottom of thumbnail
                    val progress = item.progressPct
                    if (progress != null && progress > 0f) {
                        Box(
                            modifier = Modifier
                                .align(Alignment.BottomStart)
                                .fillMaxWidth()
                                .height(4.dp)
                                .background(Color(0x66000000)),
                        ) {
                            Box(
                                modifier = Modifier
                                    .fillMaxWidth(fraction = (progress / 100f).coerceIn(0f, 1f))
                                    .height(4.dp)
                                    .background(WatchDawgColors.Orange),
                            )
                        }
                    }

                    // WATCHED badge — top right of thumbnail
                    if (item.completed) {
                        Text(
                            text     = "✓ WATCHED",
                            style    = MaterialTheme.typography.labelSmall,
                            color    = Color.White,
                            modifier = Modifier
                                .align(Alignment.TopEnd)
                                .padding(4.dp)
                                .clip(MaterialTheme.shapes.small)
                                .background(WatchDawgColors.ResolvedBadge.copy(alpha = 0.85f))
                                .padding(horizontal = 4.dp, vertical = 2.dp),
                        )
                    }
                }

                Spacer(Modifier.width(16.dp))

                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        text     = item.title ?: "Untitled",
                        style    = MaterialTheme.typography.titleMedium,
                        color    = WatchDawgColors.TextPrimary,
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis,
                    )
                    if (!item.artist.isNullOrBlank()) {
                        Text(
                            text     = item.artist,
                            style    = MaterialTheme.typography.bodyMedium,
                            color    = WatchDawgColors.Orange,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                            modifier = Modifier.padding(top = 2.dp),
                        )
                    }
                    if (!item.channelName.isNullOrBlank()) {
                        Text(
                            text     = item.channelName,
                            style    = MaterialTheme.typography.bodySmall,
                            color    = WatchDawgColors.TextTertiary,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                            modifier = Modifier.padding(top = 2.dp),
                        )
                    }
                    val positionSec = (item.positionSeconds ?: 0f).toInt()
                    val durationSec = (item.durationSeconds ?: 0f).toInt()
                    if (durationSec > 0 && positionSec > 0) {
                        val remainingSec = (durationSec - positionSec).coerceAtLeast(0)
                        val remainingMin = remainingSec / 60
                        val remainingSecs = remainingSec % 60
                        val label = if (remainingMin > 0) "${remainingMin}m left" else "${remainingSecs}s left"
                        Text(
                            text     = label,
                            style    = MaterialTheme.typography.labelMedium,
                            color    = WatchDawgColors.TextTertiary,
                            modifier = Modifier.padding(top = 4.dp),
                        )
                    }
                }
            }
        }

        // Remove pill — plain Row sibling, no FocusRequester (Session 26 Bug 1 fix)
        Button(
            onClick  = onRemove,
            enabled  = !isRemoving,
            colors   = ButtonDefaults.colors(
                containerColor         = WatchDawgColors.Surface,
                contentColor           = WatchDawgColors.FailedBadge,
                focusedContainerColor  = WatchDawgColors.FailedBadge,
                focusedContentColor    = Color.White,
                disabledContainerColor = WatchDawgColors.Surface,
                disabledContentColor   = WatchDawgColors.TextTertiary,
            ),
            modifier = Modifier.focusGlow(),
        ) {
            Text(
                text  = if (isRemoving) "…" else "✕ Remove",
                style = MaterialTheme.typography.labelLarge,
            )
        }
    }
}

private fun thumbModel(thumbnailUrl: String?): String? {
    if (thumbnailUrl.isNullOrBlank()) return null
    return if (thumbnailUrl.startsWith("/"))
        Graph.serverPrefs.getBaseUrl().trimEnd('/') + thumbnailUrl
    else thumbnailUrl
}
