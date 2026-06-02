package com.watchdawg.tv.ui.favorites

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
import com.watchdawg.tv.data.api.FavoriteDto
import com.watchdawg.tv.ui.library.FavoritesViewModel
import com.watchdawg.tv.ui.theme.WatchDawgColors
import com.watchdawg.tv.ui.theme.focusGlow
import com.watchdawg.tv.ui.theme.focusGlowCard

/**
 * Favorites screen — R-4 update.
 *
 * Content model (R-4):
 *   Shows ONLY unlocked-channel favorites. Locked-channel favorites live
 *   exclusively on the Adult screen → Favorites pill. No PIN awareness needed
 *   here — the content is always the same regardless of lock state.
 *
 * Clear All (R-4):
 *   Button in the header row. Tapping it shows a confirmation dialog ("This
 *   will remove all favorites. Are you sure?"). Confirming calls viewModel.clearAll()
 *   which deletes each favorite via DELETE /favorite/{id}. Downloaded files are
 *   also deleted server-side per existing behavior.
 *
 * Session 25: stream items re-resolve on every play via onPlayById so Vimeo
 *   CDN tokens are always fresh. Downloaded files play via local stream_url.
 *
 * Session 26 Bug 1 fix: removed manual onKeyEvent + requestFocus() handlers.
 *   Compose TV's directional focus resolver naturally traverses between Row
 *   siblings — manual calls raced the system resolver.
 *
 * Session 26 Bug 3 fix: LaunchedEffect(Unit) { viewModel.refresh() } so the
 *   list re-fetches every time this screen enters composition.
 */
@Composable
fun FavoritesScreen(
    viewModel: FavoritesViewModel,
    onPlayById: (videoId: Int, queue: List<Int>, startIndex: Int) -> Unit,
    onPlayStreamUrl: (relativeUrl: String, title: String) -> Unit,
    onPlayQueue: (queue: List<Int>, startIndex: Int) -> Unit,
    modifier: Modifier = Modifier,
) {
    val state by viewModel.state.collectAsStateWithLifecycle()

    // Show confirmation dialog state
    var showClearConfirm by remember { mutableStateOf(false) }

    // Refresh on every screen entry
    LaunchedEffect(Unit) {
        viewModel.refresh()
    }

    LaunchedEffect(state.pendingQueue) {
        val q = state.pendingQueue ?: return@LaunchedEffect
        if (q.isNotEmpty()) {
            onPlayQueue(q, 0)
            viewModel.clearPendingQueue()
        }
    }

    Box(modifier = modifier.fillMaxSize()) {

        Column(modifier = Modifier.fillMaxSize().padding(end = 32.dp, top = 28.dp)) {
            Text(
                text  = "Favorites",
                style = MaterialTheme.typography.displayLarge,
                color = WatchDawgColors.TextPrimary,
            )
            Text(
                text     = "Bookmarked and downloaded videos",
                style    = MaterialTheme.typography.bodyLarge,
                color    = WatchDawgColors.TextSecondary,
                modifier = Modifier.padding(top = 4.dp),
            )

            Spacer(Modifier.height(12.dp))

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
                ) { Text("▶  Play All", style = MaterialTheme.typography.titleSmall) }

                Button(
                    onClick  = { viewModel.shuffleAll() },
                    colors   = ButtonDefaults.colors(
                        containerColor        = WatchDawgColors.OrangeDim,
                        contentColor          = WatchDawgColors.Orange,
                        focusedContainerColor = WatchDawgColors.Orange,
                        focusedContentColor   = WatchDawgColors.Background,
                    ),
                    modifier = Modifier.focusGlow(),
                ) { Text("🔀  Shuffle All", style = MaterialTheme.typography.titleSmall) }

                Button(
                    onClick  = { viewModel.refresh() },
                    colors   = ButtonDefaults.colors(
                        containerColor        = WatchDawgColors.Surface,
                        contentColor          = WatchDawgColors.TextSecondary,
                        focusedContainerColor = WatchDawgColors.SurfaceFocused,
                        focusedContentColor   = WatchDawgColors.TextPrimary,
                    ),
                    modifier = Modifier.focusGlow(),
                ) { Text("⟳  Refresh", style = MaterialTheme.typography.titleSmall) }

                // Clear All — only visible when there are favorites to clear
                if (state.favorites.isNotEmpty()) {
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

            if (state.favorites.isEmpty() && !state.loading && !state.clearing) {
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Text("★", style = MaterialTheme.typography.displayLarge, color = WatchDawgColors.TextTertiary)
                        Spacer(Modifier.height(12.dp))
                        Text("No favorites yet.", style = MaterialTheme.typography.titleLarge, color = WatchDawgColors.TextSecondary)
                        Text(
                            "Bookmark a video from the player to see it here.",
                            style    = MaterialTheme.typography.bodyMedium,
                            color    = WatchDawgColors.TextTertiary,
                            modifier = Modifier.padding(top = 8.dp),
                        )
                    }
                }
            } else {
                LazyColumn(
                    verticalArrangement = Arrangement.spacedBy(10.dp),
                    contentPadding      = PaddingValues(bottom = 48.dp),
                    modifier            = Modifier.fillMaxSize(),
                ) {
                    items(state.favorites, key = { it.videoId ?: it.hashCode() }) { fav ->
                        FavoriteRow(
                            fav        = fav,
                            isRemoving = state.removingIds.contains(fav.id),
                            onClick    = {
                                val vid = fav.videoId
                                when {
                                    fav.downloadStatus == "failed" ->
                                        fav.id?.let { viewModel.retry(it) }
                                    fav.downloadStatus == "complete" && !fav.streamUrl.isNullOrBlank() ->
                                        onPlayStreamUrl(fav.streamUrl, fav.title ?: "Now Playing")
                                    vid != null -> {
                                        val queue = state.favorites
                                            .mapNotNull { it.videoId }
                                            .filter { it > 0 }
                                        onPlayById(vid, queue, queue.indexOf(vid).coerceAtLeast(0))
                                    }
                                    else -> {}
                                }
                            },
                            onRemove = { fav.id?.let { viewModel.remove(it) } },
                        )
                    }
                }
            }
        }

        // ── Clear All confirmation dialog ──────────────────────────────────────
        if (showClearConfirm) {
            ClearAllConfirmDialog(
                count     = state.favorites.size,
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
private fun ClearAllConfirmDialog(
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
                text  = "🗑  Clear All Favorites?",
                style = MaterialTheme.typography.titleLarge,
                color = WatchDawgColors.TextPrimary,
            )
            Text(
                text  = "This will remove all $count favorite${if (count != 1) "s" else ""}.\nDownloaded files will also be deleted from the server.",
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

// ── Favorite row ──────────────────────────────────────────────────────────────

@Composable
private fun FavoriteRow(
    fav: FavoriteDto,
    isRemoving: Boolean,
    onClick: () -> Unit,
    onRemove: () -> Unit,
) {
    var cardHasFocus by remember { mutableStateOf(false) }

    Row(
        modifier              = Modifier.fillMaxWidth(),
        verticalAlignment     = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Card(
            onClick  = onClick,
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
                        model              = thumbModel(fav.thumbnailUrl),
                        contentDescription = fav.title,
                        contentScale       = ContentScale.Crop,
                        modifier           = Modifier
                            .fillMaxSize()
                            .background(WatchDawgColors.SurfaceElevated),
                    )
                    DownloadBadge(
                        status   = fav.downloadStatus,
                        modifier = Modifier.align(Alignment.TopEnd).padding(6.dp),
                    )
                }
                Spacer(Modifier.width(16.dp))
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        text     = fav.title ?: "Untitled",
                        style    = MaterialTheme.typography.titleMedium,
                        color    = WatchDawgColors.TextPrimary,
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis,
                    )
                    if (!fav.artist.isNullOrBlank()) {
                        Text(
                            text     = fav.artist,
                            style    = MaterialTheme.typography.bodyMedium,
                            color    = WatchDawgColors.Orange,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                            modifier = Modifier.padding(top = 2.dp),
                        )
                    }
                    if (!fav.channelName.isNullOrBlank()) {
                        Text(
                            text     = fav.channelName,
                            style    = MaterialTheme.typography.bodySmall,
                            color    = WatchDawgColors.TextTertiary,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                    }
                }
            }
        }

        Button(
            onClick  = { if (!isRemoving) onRemove() },
            colors   = ButtonDefaults.colors(
                containerColor        = WatchDawgColors.Surface,
                contentColor          = WatchDawgColors.TextSecondary,
                focusedContainerColor = WatchDawgColors.SurfaceFocused,
                focusedContentColor   = WatchDawgColors.FailedBadge,
            ),
            modifier = Modifier
                .width(110.dp)
                .focusGlow(),
        ) {
            Text(
                text  = if (isRemoving) "…" else "✕  Remove",
                style = MaterialTheme.typography.labelLarge,
            )
        }
    }
}

// ── Download badge ────────────────────────────────────────────────────────────

@Composable
private fun DownloadBadge(status: String?, modifier: Modifier = Modifier) {
    if (status.isNullOrBlank() || status == "none" || status == "complete") return
    val (color, label) = when (status.lowercase()) {
        "downloading"     -> WatchDawgColors.Blue        to "DOWNLOADING"
        "pending"         -> WatchDawgColors.PendingBadge to "QUEUED"
        "failed", "error" -> WatchDawgColors.FailedBadge  to "RETRY"
        else              -> WatchDawgColors.PendingBadge  to "QUEUED"
    }
    Text(
        text     = label,
        style    = MaterialTheme.typography.labelSmall,
        color    = Color.White,
        modifier = modifier
            .clip(MaterialTheme.shapes.small)
            .background(color.copy(alpha = 0.85f))
            .padding(horizontal = 6.dp, vertical = 2.dp),
    )
}

private fun thumbModel(thumbnailUrl: String?): String? {
    if (thumbnailUrl.isNullOrBlank()) return null
    return if (thumbnailUrl.startsWith("/"))
        Graph.serverPrefs.getBaseUrl().trimEnd('/') + thumbnailUrl
    else thumbnailUrl
}
