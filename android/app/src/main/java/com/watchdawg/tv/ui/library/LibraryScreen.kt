package com.watchdawg.tv.ui.library

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
import androidx.compose.foundation.lazy.LazyRow
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
import com.watchdawg.tv.data.api.LibraryFileDto
import com.watchdawg.tv.ui.theme.WatchDawgColors
import com.watchdawg.tv.ui.theme.focusGlow
import com.watchdawg.tv.ui.theme.focusGlowCard

/**
 * Library / Local screen — Session 42 genre pill filter added.
 *
 * Content model (R-4):
 *   Shows ONLY Public subfolder files. Private files live on Adult → Local pill.
 *   PIN-agnostic — content is always the same regardless of lock state.
 *
 * Session 42 additions:
 *   Genre pill row below the action buttons. "All" pill shows everything.
 *   Selecting a genre pill reloads the list filtered server-side via
 *   GET /library?genre=X. Pill list fetched from GET /library/genres.
 *   Channel name shown in orange under each file title.
 */
@Composable
fun LibraryScreen(
    viewModel: LibraryViewModel,
    onPlayStreamUrl: (relativeUrl: String, title: String) -> Unit,
    onPlayQueue: (queue: List<String>, startIndex: Int) -> Unit,
    modifier: Modifier = Modifier,
) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    var pendingRemovePath by remember { mutableStateOf<String?>(null) }

    LaunchedEffect(Unit) { viewModel.refresh() }

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
                text  = "Local",
                style = MaterialTheme.typography.displayLarge,
                color = WatchDawgColors.TextPrimary,
            )
            Text(
                text     = "Downloaded videos on your server",
                style    = MaterialTheme.typography.bodyLarge,
                color    = WatchDawgColors.TextSecondary,
                modifier = Modifier.padding(top = 4.dp),
            )

            Spacer(Modifier.height(12.dp))

            // ── Action buttons ────────────────────────────────────────────────
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
            }

            // ── Session 42: Genre pill filter row ─────────────────────────────
            if (state.genres.isNotEmpty()) {
                Spacer(Modifier.height(10.dp))
                LazyRow(
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                    contentPadding        = PaddingValues(horizontal = 2.dp),
                ) {
                    item {
                        GenrePill(
                            label      = "All",
                            isSelected = state.selectedGenre == null,
                            onClick    = { viewModel.selectGenre(null) },
                        )
                    }
                    items(state.genres) { genre ->
                        GenrePill(
                            label      = genre,
                            isSelected = state.selectedGenre == genre,
                            onClick    = { viewModel.selectGenre(genre) },
                        )
                    }
                }
            }

            Spacer(Modifier.height(12.dp))

            if (state.files.isEmpty() && !state.loading) {
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    Text(
                        text  = if (state.selectedGenre != null)
                            "No files found for \"${state.selectedGenre}\"."
                        else
                            "No downloaded files yet.",
                        style = MaterialTheme.typography.titleLarge,
                        color = WatchDawgColors.TextSecondary,
                    )
                }
            } else {
                LazyColumn(
                    verticalArrangement = Arrangement.spacedBy(10.dp),
                    contentPadding      = PaddingValues(bottom = 48.dp),
                    modifier            = Modifier.fillMaxSize(),
                ) {
                    items(
                        state.files,
                        key = { it.relativePath ?: it.filename ?: it.hashCode().toString() },
                    ) { file ->
                        val isRemoving = state.removingPaths.contains(file.relativePath)
                        LibraryRow(
                            file       = file,
                            isRemoving = isRemoving,
                            onClick    = {
                                val url = file.streamUrl
                                if (!url.isNullOrBlank()) {
                                    onPlayStreamUrl(url, file.title ?: file.filename ?: "Now Playing")
                                }
                            },
                            onRemove = { pendingRemovePath = file.relativePath },
                        )
                    }
                }
            }
        }

        if (pendingRemovePath != null) {
            val path = pendingRemovePath!!
            RemoveFileConfirmDialog(
                onConfirm = { pendingRemovePath = null; viewModel.removeFile(path) },
                onDismiss = { pendingRemovePath = null },
            )
        }
    }
}

// ── Genre pill ────────────────────────────────────────────────────────────────

@Composable
private fun GenrePill(
    label: String,
    isSelected: Boolean,
    onClick: () -> Unit,
) {
    Button(
        onClick = onClick,
        colors  = ButtonDefaults.colors(
            containerColor        = if (isSelected) WatchDawgColors.Orange else WatchDawgColors.Surface,
            contentColor          = if (isSelected) WatchDawgColors.Background else WatchDawgColors.TextSecondary,
            focusedContainerColor = WatchDawgColors.OrangeDim,
            focusedContentColor   = WatchDawgColors.Orange,
        ),
        modifier = Modifier.focusGlow(),
    ) {
        Text(label, style = MaterialTheme.typography.labelLarge)
    }
}

// ── Library row ───────────────────────────────────────────────────────────────

@Composable
private fun LibraryRow(
    file: LibraryFileDto,
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
                        model              = thumbModel(file.thumbnailUrl),
                        contentDescription = file.filename,
                        contentScale       = ContentScale.Crop,
                        modifier           = Modifier
                            .fillMaxSize()
                            .background(WatchDawgColors.SurfaceElevated),
                    )
                }
                Spacer(Modifier.width(16.dp))
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        text     = file.title ?: file.filename ?: "Untitled",
                        style    = MaterialTheme.typography.titleMedium,
                        color    = WatchDawgColors.TextPrimary,
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis,
                    )
                    if (!file.channelName.isNullOrBlank()) {
                        Text(
                            text     = file.channelName,
                            style    = MaterialTheme.typography.bodySmall,
                            color    = WatchDawgColors.Orange,
                            maxLines = 1,
                            modifier = Modifier.padding(top = 2.dp),
                        )
                    }
                    if (!file.sizeHuman.isNullOrBlank()) {
                        Text(
                            text     = file.sizeHuman,
                            style    = MaterialTheme.typography.bodySmall,
                            color    = WatchDawgColors.TextTertiary,
                            maxLines = 1,
                            modifier = Modifier.padding(top = 2.dp),
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
            modifier = Modifier.width(110.dp).focusGlow(),
        ) {
            Text(
                text  = if (isRemoving) "…" else "✕  Remove",
                style = MaterialTheme.typography.labelLarge,
            )
        }
    }
}

// ── Remove file confirmation dialog ───────────────────────────────────────────

@Composable
private fun RemoveFileConfirmDialog(
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
            Text("🗑  Delete File?", style = MaterialTheme.typography.titleLarge, color = WatchDawgColors.TextPrimary)
            Text(
                text  = "This will permanently delete the file from disc.\nAre you sure?",
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
                ) { Text("Cancel", style = MaterialTheme.typography.titleSmall) }
                Button(
                    onClick  = onConfirm,
                    colors   = ButtonDefaults.colors(
                        containerColor        = WatchDawgColors.FailedBadge,
                        contentColor          = Color.White,
                        focusedContainerColor = WatchDawgColors.FailedBadge,
                        focusedContentColor   = Color.White,
                    ),
                    modifier = Modifier.width(140.dp).focusRequester(confirmFocus).focusGlow(),
                ) { Text("Delete", style = MaterialTheme.typography.titleSmall) }
            }
        }
    }
}

private fun thumbModel(thumbnailUrl: String?): String? {
    if (thumbnailUrl.isNullOrBlank()) return null
    return if (thumbnailUrl.startsWith("/"))
        Graph.serverPrefs.getBaseUrl().trimEnd('/') + thumbnailUrl
    else thumbnailUrl
}
