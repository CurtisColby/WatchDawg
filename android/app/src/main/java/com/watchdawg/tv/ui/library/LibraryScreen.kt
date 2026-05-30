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
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.onFocusChanged
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
 * Library screen — downloaded files from the server storage directory.
 *
 * Session 25: focusGlow on all buttons and cards.
 *
 * Session 26 Bug 3 fix: added LaunchedEffect(Unit) { viewModel.refresh() } so the
 * file list re-fetches every time this screen enters composition. ViewModel init{}
 * only runs once per ViewModel lifetime, so navigating away and back showed stale
 * data. LaunchedEffect(Unit) fires on each re-composition entry which is exactly
 * what NavHost does when navigating back to this route.
 */
@Composable
fun LibraryScreen(
    viewModel: LibraryViewModel,
    onPlayStreamUrl: (relativeUrl: String, title: String) -> Unit,
    onPlayQueue: (queue: List<String>, startIndex: Int) -> Unit,
    modifier: Modifier = Modifier,
) {
    val state by viewModel.state.collectAsStateWithLifecycle()

    // Bug 3 fix: refresh on every screen entry so new downloads are visible
    // immediately without a manual Refresh button press.
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

    Column(modifier = modifier.fillMaxSize().padding(end = 32.dp, top = 28.dp)) {
        Text(text = "Library", style = MaterialTheme.typography.displayLarge, color = WatchDawgColors.TextPrimary)
        Text(
            text     = "Downloaded videos on your server",
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
        }

        Spacer(Modifier.height(12.dp))

        if (state.files.isEmpty() && !state.loading) {
            Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                Text(
                    text  = if (state.lockedHidden) "Locked content is hidden. Enter PIN to view."
                            else "No downloaded files yet.",
                    style = MaterialTheme.typography.titleLarge,
                    color = WatchDawgColors.TextSecondary,
                )
            }
        } else {
            LazyVerticalGrid(
                columns        = GridCells.Fixed(4),
                contentPadding = PaddingValues(top = 8.dp, bottom = 48.dp),
                horizontalArrangement = Arrangement.spacedBy(16.dp),
                verticalArrangement   = Arrangement.spacedBy(16.dp),
                modifier = Modifier.fillMaxSize(),
            ) {
                // LibraryFileDto uses relativePath (not path) as the stable key
                items(state.files, key = { it.relativePath ?: it.filename ?: it.hashCode().toString() }) { file ->
                    LibraryCard(
                        file    = file,
                        onClick = {
                            val url = file.streamUrl
                            if (!url.isNullOrBlank()) {
                                onPlayStreamUrl(url, file.filename ?: "Now Playing")
                            }
                        },
                    )
                }
            }
        }
    }
}

@Composable
private fun LibraryCard(file: LibraryFileDto, onClick: () -> Unit) {
    var focused by remember { mutableStateOf(false) }
    Card(
        onClick  = onClick,
        colors   = CardDefaults.colors(
            containerColor        = WatchDawgColors.Surface,
            focusedContainerColor = WatchDawgColors.SurfaceFocused,
        ),
        scale    = CardDefaults.scale(focusedScale = 1.05f),
        modifier = Modifier
            .onFocusChanged { focused = it.isFocused }
            .focusGlowCard(focused),
    ) {
        Column {
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .aspectRatio(16f / 9f)
                    .clip(MaterialTheme.shapes.medium),
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
            Column(Modifier.padding(10.dp)) {
                Text(
                    text     = file.filename ?: "Untitled",
                    style    = MaterialTheme.typography.titleMedium,
                    color    = WatchDawgColors.TextPrimary,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                )
                if (!file.subfolder.isNullOrBlank()) {
                    Text(
                        text     = file.subfolder,
                        style    = MaterialTheme.typography.bodyMedium,
                        color    = WatchDawgColors.Orange,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
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
