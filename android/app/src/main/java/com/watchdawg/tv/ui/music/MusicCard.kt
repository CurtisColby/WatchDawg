package com.watchdawg.tv.ui.music

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.MusicNote
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Brush
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
import com.watchdawg.tv.ui.theme.WatchDawgColors
import com.watchdawg.tv.ui.theme.focusGlowCard

/**
 * Music video card — R-4 (square).
 *
 * 1:1 square aspect ratio. Music sources have YouTube 16:9 thumbnails —
 * ContentScale.Crop fills the square by center-cropping, which looks clean
 * and uniform across all cards. No TMDB data expected for music sources.
 *
 * Bottom gradient + title/artist metadata over the thumbnail.
 * ♪ icon placeholder when no thumbnail available.
 */
@Composable
fun MusicCard(
    video: VideoDto,
    onPlay: () -> Unit,
    modifier: Modifier = Modifier,
) {
    var focused by remember { mutableStateOf(false) }

    Card(
        onClick  = onPlay,
        modifier = modifier
            .fillMaxWidth()
            .aspectRatio(1f)          // 1:1 square
            .onFocusChanged { focused = it.isFocused }
            .focusGlowCard(focused),
        colors = CardDefaults.colors(
            containerColor        = WatchDawgColors.Surface,
            focusedContainerColor = WatchDawgColors.SurfaceFocused,
        ),
        border = CardDefaults.border(
            focusedBorder = Border(
                border = androidx.compose.foundation.BorderStroke(3.dp, WatchDawgColors.Orange),
            ),
        ),
        scale = CardDefaults.scale(focusedScale = 1.05f),
        shape = CardDefaults.shape(shape = RoundedCornerShape(8.dp)),
    ) {
        Box(modifier = Modifier.fillMaxSize()) {

            // ── Art — center-crop YouTube thumbnail into square ────────────────
            if (video.thumbnailUrl != null) {
                AsyncImage(
                    model              = video.thumbnailUrl,
                    contentDescription = video.title,
                    contentScale       = ContentScale.Crop,
                    modifier           = Modifier
                        .fillMaxSize()
                        .background(WatchDawgColors.SurfaceElevated),
                )
            } else {
                Box(
                    modifier         = Modifier
                        .fillMaxSize()
                        .background(WatchDawgColors.SurfaceElevated),
                    contentAlignment = Alignment.Center,
                ) {
                    Icon(
                        imageVector        = Icons.Outlined.MusicNote,
                        contentDescription = null,
                        tint               = WatchDawgColors.TextTertiary,
                        modifier           = Modifier.size(40.dp),
                    )
                }
            }

            // ── Play overlay on focus ─────────────────────────────────────────
            if (focused) {
                Box(
                    modifier         = Modifier
                        .fillMaxSize()
                        .background(Color(0x44000000)),
                    contentAlignment = Alignment.Center,
                ) {
                    Icon(
                        imageVector        = Icons.Outlined.MusicNote,
                        contentDescription = "Play",
                        tint               = Color.White,
                        modifier           = Modifier.size(36.dp),
                    )
                }
            }

            // ── Bottom gradient + title/artist ────────────────────────────────
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .align(Alignment.BottomStart)
                    .background(
                        Brush.verticalGradient(
                            colors = listOf(Color.Transparent, Color(0xCC000000), Color(0xEE000000)),
                        ),
                    )
                    .padding(horizontal = 8.dp, vertical = 6.dp),
            ) {
                Column {
                    Text(
                        text     = video.title ?: "Untitled",
                        style    = MaterialTheme.typography.labelMedium,
                        color    = if (focused) WatchDawgColors.TextPrimary else Color(0xFFDDDDDD),
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis,
                    )
                    val artist = video.artist
                    if (!artist.isNullOrBlank()) {
                        Text(
                            text     = artist,
                            style    = MaterialTheme.typography.labelSmall,
                            color    = if (focused) WatchDawgColors.Orange else WatchDawgColors.Orange.copy(alpha = 0.8f),
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                    }
                }
            }
        }
    }
}
