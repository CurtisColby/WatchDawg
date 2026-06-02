package com.watchdawg.tv.ui.adult

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
import androidx.compose.material.icons.outlined.Lock
import androidx.compose.material.icons.outlined.PlayArrow
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
import com.watchdawg.tv.Graph
import com.watchdawg.tv.data.api.LibraryFileDto
import com.watchdawg.tv.data.api.VideoDto
import com.watchdawg.tv.ui.theme.WatchDawgColors
import com.watchdawg.tv.ui.theme.focusGlowCard

/**
 * Adult content cards — Milestone R-4 (square).
 *
 * Both stream and local cards use 1:1 square aspect ratio.
 * No TMDB poster dependency — ContentScale.Crop center-crops whatever
 * thumbnail is available (YouTube still, Vimeo frame, ffmpeg sidecar).
 *
 * 🔞 badge on top-right corner on all cards.
 * Orange border + focus glow on D-pad focus.
 * Bottom gradient with title readable over any art.
 */

// ── Stream card (VideoDto from GET /feed?category=adult) ──────────────────────

@Composable
fun AdultStreamCard(
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

            // ── Art ───────────────────────────────────────────────────────────
            val imageUrl = video.tmdbPosterUrl ?: video.thumbnailUrl
            if (imageUrl != null) {
                AsyncImage(
                    model              = imageUrl,
                    contentDescription = video.title,
                    contentScale       = ContentScale.Crop,
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
                        imageVector        = Icons.Outlined.Lock,
                        contentDescription = null,
                        tint               = WatchDawgColors.TextTertiary,
                        modifier           = Modifier.size(40.dp),
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
                        imageVector        = Icons.Outlined.PlayArrow,
                        contentDescription = "Play",
                        tint               = Color.White,
                        modifier           = Modifier.size(44.dp),
                    )
                }
            }

            // ── 🔞 badge — top right ──────────────────────────────────────────
            Box(
                modifier = Modifier
                    .align(Alignment.TopEnd)
                    .padding(6.dp)
                    .clip(RoundedCornerShape(4.dp))
                    .background(Color(0xCC000000))
                    .padding(horizontal = 5.dp, vertical = 2.dp),
            ) {
                Text(text = "🔞", style = MaterialTheme.typography.labelSmall)
            }

            // ── Bottom gradient + title ───────────────────────────────────────
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

// ── Local card (LibraryFileDto — Private subfolder) ───────────────────────────

@Composable
fun AdultLocalCard(
    file: LibraryFileDto,
    onPlay: () -> Unit,
    modifier: Modifier = Modifier,
) {
    var focused by remember { mutableStateOf(false) }

    val thumbUrl = file.thumbnailUrl?.let { url ->
        if (url.startsWith("/"))
            Graph.serverPrefs.getBaseUrl().trimEnd('/') + url
        else url
    }

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

            // ── Art ───────────────────────────────────────────────────────────
            if (thumbUrl != null) {
                AsyncImage(
                    model              = thumbUrl,
                    contentDescription = file.filename,
                    contentScale       = ContentScale.Crop,
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
                        imageVector        = Icons.Outlined.Lock,
                        contentDescription = null,
                        tint               = WatchDawgColors.TextTertiary,
                        modifier           = Modifier.size(40.dp),
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
                        imageVector        = Icons.Outlined.PlayArrow,
                        contentDescription = "Play",
                        tint               = Color.White,
                        modifier           = Modifier.size(44.dp),
                    )
                }
            }

            // ── 🔞 badge — top right ──────────────────────────────────────────
            Box(
                modifier = Modifier
                    .align(Alignment.TopEnd)
                    .padding(6.dp)
                    .clip(RoundedCornerShape(4.dp))
                    .background(Color(0xCC000000))
                    .padding(horizontal = 5.dp, vertical = 2.dp),
            ) {
                Text(text = "🔞", style = MaterialTheme.typography.labelSmall)
            }

            // ── Bottom gradient + title + size ────────────────────────────────
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
                        text     = file.title ?: file.filename ?: "Untitled",
                        style    = MaterialTheme.typography.labelMedium,
                        color    = if (focused) WatchDawgColors.TextPrimary else Color(0xFFDDDDDD),
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis,
                    )
                    if (!file.sizeHuman.isNullOrBlank()) {
                        Text(
                            text     = file.sizeHuman,
                            style    = MaterialTheme.typography.labelSmall,
                            color    = WatchDawgColors.TextTertiary,
                            maxLines = 1,
                        )
                    }
                }
            }
        }
    }
}
