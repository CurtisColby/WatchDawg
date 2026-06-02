package com.watchdawg.tv.ui.movies

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
import androidx.compose.material.icons.outlined.Movie
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
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
 * Movie poster card — R-3 (uniform grid fix).
 *
 * All cards are uniform 2:3 portrait — consistent grid rows, no height jumps.
 *
 * ContentScale strategy:
 *   - TMDB poster available → ContentScale.Crop  (poster is 2:3, fills card perfectly)
 *   - YouTube thumbnail only → ContentScale.Fit  (16:9 letterboxed inside 2:3 card,
 *     dark bars top/bottom, image stays sharp at native resolution — no stretching)
 *   - No art → placeholder icon centered on dark background
 */
@Composable
fun MovieCard(
    video: VideoDto,
    onPlay: () -> Unit,
    modifier: Modifier = Modifier,
) {
    var focused by remember { mutableStateOf(false) }

    val hasPoster = video.tmdbPosterUrl != null
    val imageUrl  = video.tmdbPosterUrl ?: video.thumbnailUrl

    Card(
        onClick  = onPlay,
        modifier = modifier
            .fillMaxWidth()
            // Uniform 2:3 — every card same height, clean grid rows
            .aspectRatio(2f / 3f)
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

            // ── Art ───────────────────────────────────────────────────────────
            if (imageUrl != null) {
                AsyncImage(
                    model              = imageUrl,
                    contentDescription = video.title,
                    // TMDB poster: Crop fills the 2:3 card edge-to-edge perfectly.
                    // YouTube thumbnail: Fit letterboxes the 16:9 image inside the
                    // 2:3 card — dark bars top/bottom, image stays sharp, no stretch.
                    contentScale       = if (hasPoster) ContentScale.Crop else ContentScale.Fit,
                    modifier           = Modifier
                        .fillMaxSize()
                        .background(WatchDawgColors.SurfaceElevated),
                )
            } else {
                Box(
                    modifier         = Modifier.fillMaxSize().background(WatchDawgColors.SurfaceElevated),
                    contentAlignment = Alignment.Center,
                ) {
                    Icon(
                        imageVector        = Icons.Outlined.Movie,
                        contentDescription = null,
                        tint               = WatchDawgColors.TextTertiary,
                        modifier           = Modifier.size(48.dp),
                    )
                }
            }

            // ── Play overlay on focus ─────────────────────────────────────────
            if (focused) {
                Box(
                    modifier         = Modifier.fillMaxSize().background(Color(0x44000000)),
                    contentAlignment = Alignment.Center,
                ) {
                    Icon(
                        imageVector        = Icons.Outlined.Movie,
                        contentDescription = "Play",
                        tint               = Color.White,
                        modifier           = Modifier.size(44.dp),
                    )
                }
            }

            // ── Bottom gradient + metadata ────────────────────────────────────
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .align(Alignment.BottomStart)
                    .background(
                        Brush.verticalGradient(
                            colors = listOf(Color.Transparent, Color(0xCC000000), Color(0xEE000000)),
                        ),
                    )
                    .padding(horizontal = 8.dp, vertical = 8.dp),
            ) {
                Column {
                    Text(
                        text     = video.title ?: "Untitled",
                        style    = MaterialTheme.typography.labelMedium,
                        color    = if (focused) WatchDawgColors.TextPrimary else Color(0xFFDDDDDD),
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis,
                    )
                    val year   = video.tmdbYear
                    val rating = video.tmdbRating
                    if (year != null || rating != null) {
                        val meta = buildString {
                            year?.let { append(it) }
                            if (year != null && rating != null) append("  ·  ")
                            rating?.let { append("★ %.1f".format(it)) }
                        }
                        Text(
                            text     = meta,
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
