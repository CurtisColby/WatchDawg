package com.watchdawg.tv.ui.feed

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.tv.material3.Border
import androidx.tv.material3.Card
import androidx.tv.material3.CardDefaults
import androidx.tv.material3.Icon
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import coil.compose.AsyncImage
import com.watchdawg.tv.data.api.VideoDto
import com.watchdawg.tv.data.repo.providerLabel
import com.watchdawg.tv.ui.theme.WatchDawgColors
import com.watchdawg.tv.ui.theme.focusGlowCard

/**
 * Feed video card — 16:9 thumbnail, title, artist.
 *
 * Session 25: .focusGlowCard() adds orange ambient halo on D-pad focus.
 *
 * Milestone E: [isWatched] parameter — when true, a "✓ WATCHED" pill is
 * overlaid on the top-right corner of the thumbnail. Defaults to false so
 * all existing call sites compile without changes.
 *
 * Milestone F polish: [episodeLabel] parameter — optional subtitle shown
 * below the title in tertiary color. Used by EpisodeListScreen to surface
 * season/episode info ("S02 E05") when parseable from the title, or a
 * "Added Jan 12" date fallback when not. Defaults null so every existing
 * call site (Feed, Favorites, Library, Watch Later, Continue Watching)
 * compiles unchanged — label simply not shown.
 *
 * ProviderBadge and StatusBadge are defined in Badges.kt — not redeclared here.
 *
 * Note: tv.material3.Card is already focusable — no .focusable() modifier needed.
 */
@Composable
fun VideoCard(
    video: VideoDto,
    onPlay: () -> Unit,
    modifier: Modifier = Modifier,
    isWatched: Boolean = false,
    episodeLabel: String? = null,
) {
    var focused by remember { mutableStateOf(false) }

    Card(
        onClick  = onPlay,
        modifier = modifier
            .width(300.dp)
            .onFocusChanged { focused = it.isFocused }
            .focusGlowCard(focused),
        colors = CardDefaults.colors(
            containerColor        = WatchDawgColors.Surface,
            focusedContainerColor = WatchDawgColors.SurfaceFocused,
        ),
        border = CardDefaults.border(
            focusedBorder = Border(
                border = androidx.compose.foundation.BorderStroke(
                    3.dp, WatchDawgColors.Orange,
                ),
            ),
        ),
        scale = CardDefaults.scale(focusedScale = 1.06f),
    ) {
        Column {
            ThumbnailBox(video = video, focused = focused, isWatched = isWatched)

            Column(Modifier.padding(12.dp)) {
                Text(
                    text     = video.title ?: "Untitled",
                    style    = MaterialTheme.typography.titleSmall,
                    color    = WatchDawgColors.TextPrimary,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                )
                // Milestone F polish: episode label — S/E number or date added.
                // Only shown in EpisodeListScreen; null everywhere else.
                if (episodeLabel != null) {
                    Text(
                        text     = episodeLabel,
                        style    = MaterialTheme.typography.bodySmall,
                        color    = WatchDawgColors.Orange,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                } else if (!video.artist.isNullOrBlank()) {
                    Text(
                        text     = video.artist,
                        style    = MaterialTheme.typography.bodySmall,
                        color    = WatchDawgColors.Orange,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
                ProviderBadge(
                    label    = video.providerLabel(),
                    modifier = Modifier.padding(top = 4.dp),
                )
            }
        }
    }
}

@Composable
private fun ThumbnailBox(
    video: VideoDto,
    focused: Boolean,
    isWatched: Boolean = false,
) {
    Box(
        modifier = Modifier
            .fillMaxWidth()
            .aspectRatio(16f / 9f)
            .clip(MaterialTheme.shapes.medium),
    ) {
        AsyncImage(
            model              = video.thumbnailUrl,
            contentDescription = video.title,
            contentScale       = ContentScale.Crop,
            modifier           = Modifier
                .fillMaxSize()
                .background(WatchDawgColors.SurfaceElevated),
        )

        // Play arrow overlay on focus
        if (focused) {
            Box(
                modifier         = Modifier
                    .fillMaxSize()
                    .background(Color(0x55000000)),
                contentAlignment = Alignment.Center,
            ) {
                Icon(
                    imageVector        = Icons.Default.PlayArrow,
                    contentDescription = "Play",
                    tint               = Color.White,
                    modifier           = Modifier.size(48.dp),
                )
            }
        }

        // Milestone E: Watched badge — shown top-right when history marks completed=true.
        if (isWatched) {
            Text(
                text     = "✓ WATCHED",
                style    = MaterialTheme.typography.labelSmall,
                color    = Color.White,
                modifier = Modifier
                    .align(Alignment.TopEnd)
                    .padding(6.dp)
                    .clip(MaterialTheme.shapes.small)
                    .background(WatchDawgColors.ResolvedBadge.copy(alpha = 0.85f))
                    .padding(horizontal = 6.dp, vertical = 2.dp),
            )
        }
    }
}
