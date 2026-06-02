package com.watchdawg.tv.ui.watchlater

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
import androidx.compose.runtime.Composable
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
import com.watchdawg.tv.data.api.WatchlistItemDto
import com.watchdawg.tv.ui.theme.WatchDawgColors
import com.watchdawg.tv.ui.theme.focusGlow
import com.watchdawg.tv.ui.theme.focusGlowCard

/**
 * Watch Later screen — shows videos saved via the Watch Later action.
 *
 * D-pad:
 *   OK on card  → play via resolve (onPlay)
 *   Right       → focus Remove button
 *   Left on Rm  → return focus to card
 *   OK on Rm    → remove from Watch Later (optimistic)
 *
 * Session 25: focusGlow() applied to card rows and remove buttons.
 */
@Composable
fun WatchLaterScreen(
    viewModel: WatchLaterViewModel,
    onPlay: (videoId: Int, queue: List<Int>, index: Int, hlsMode: Boolean) -> Unit,
    modifier: Modifier = Modifier,
) {
    val state by viewModel.state.collectAsStateWithLifecycle()

    Column(modifier = modifier.fillMaxSize().padding(end = 32.dp, top = 28.dp)) {
        Text(
            text  = "Watch Later",
            style = MaterialTheme.typography.displayLarge,
            color = WatchDawgColors.TextPrimary,
        )
        Text(
            text     = "Videos you saved to watch later",
            style    = MaterialTheme.typography.bodyLarge,
            color    = WatchDawgColors.TextSecondary,
            modifier = Modifier.padding(top = 4.dp),
        )

        Spacer(Modifier.height(12.dp))

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
            state.items.isEmpty() && !state.loading -> {
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Text(
                            text  = "⏰",
                            style = MaterialTheme.typography.displayLarge,
                            color = WatchDawgColors.TextTertiary,
                        )
                        Spacer(Modifier.height(12.dp))
                        Text(
                            text  = "Nothing saved yet",
                            style = MaterialTheme.typography.titleLarge,
                            color = WatchDawgColors.TextSecondary,
                        )
                        Text(
                            text     = "Hit Watch Later on any video to save it here. No PIN required.",
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
                        WatchLaterRow(
                            item       = item,
                            isRemoving = state.removingIds.contains(item.videoId),
                            onPlay     = { onPlay(item.videoId, queue, index.coerceAtLeast(0), false) },
                            onRemove   = { viewModel.remove(item.videoId) },
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun WatchLaterRow(
    item: WatchlistItemDto,
    isRemoving: Boolean,
    onPlay: () -> Unit,
    onRemove: () -> Unit,
) {
    val cardFocus   = remember { FocusRequester() }
    val removeFocus = remember { FocusRequester() }
    var cardHasFocus by remember { mutableStateOf(false) }

    Row(
        modifier              = Modifier.fillMaxWidth(),
        verticalAlignment     = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Card(
            onClick = onPlay,
            colors  = CardDefaults.colors(
                containerColor        = WatchDawgColors.Surface,
                focusedContainerColor = WatchDawgColors.SurfaceFocused,
            ),
            scale    = CardDefaults.scale(focusedScale = 1.02f),
            modifier = Modifier
                .weight(1f)
                .focusGlowCard(cardHasFocus)
                .focusRequester(cardFocus)
                .onFocusChanged { cardHasFocus = it.isFocused }
                .onKeyEvent { event ->
                    if (cardHasFocus &&
                        event.type == KeyEventType.KeyUp &&
                        event.key == Key.DirectionRight
                    ) {
                        try { removeFocus.requestFocus() } catch (_: Exception) {}
                        true
                    } else false
                },
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
                        )
                    }
                }
            }
        }

        // Remove button — reachable via D-pad Right from card
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
                .focusGlow()
                .focusRequester(removeFocus)
                .onKeyEvent { event ->
                    if (event.type == KeyEventType.KeyUp &&
                        event.key == Key.DirectionLeft
                    ) {
                        try { cardFocus.requestFocus() } catch (_: Exception) {}
                        true
                    } else false
                },
        ) {
            Text(
                text  = if (isRemoving) "…" else "✕  Remove",
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
