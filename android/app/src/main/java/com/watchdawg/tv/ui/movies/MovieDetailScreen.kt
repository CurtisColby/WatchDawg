package com.watchdawg.tv.ui.movies

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.tv.material3.Button
import androidx.tv.material3.ButtonDefaults
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import coil.compose.AsyncImage
import com.watchdawg.tv.data.api.VideoDto
import com.watchdawg.tv.ui.theme.WatchDawgColors
import com.watchdawg.tv.ui.theme.focusGlow

/**
 * Milestone G — Movie Detail Screen.
 *
 * Full-screen detail view shown when a user taps a card in the Movies pill.
 * Replaces the immediate play-mode-menu pattern used for other categories.
 *
 * Layout:
 *  ┌──────────────────────────────────────────────────────────┐
 *  │  [Backdrop / Poster]          │  Metadata column         │
 *  │  (left ~40% of screen)        │  Title, year, rating     │
 *  │                               │  Description fallback    │
 *  │                               │  ─────────────────       │
 *  │                               │  [▶ Play]                │
 *  │                               │  [▶ Continue from X:XX]  │
 *  │                               │  [← Back]                │
 *  └──────────────────────────────────────────────────────────┘
 *
 * Navigation:
 *  - D-pad left/right between poster and action buttons (natural Compose TV focus)
 *  - Back button → [onBack] → popBackStack() in MainActivity
 *
 * Resume banner:
 *  - If watch history exists with > 2% progress and not completed:
 *    "Continue from X:XX" button is shown as the primary action.
 *    "Start Over" becomes a secondary action.
 *  - If completed: "✓ Watched" badge on poster + "Play Again" primary action.
 *  - If no history: "Play" is the only action.
 *
 * @param video      The VideoDto from the feed. Contains all available metadata.
 * @param viewModel  Drives history/resume state.
 * @param onPlay     Called with (videoId, positionMs) — 0L = start from beginning.
 * @param onBack     Called when user presses Back to return to the movies feed.
 */
@Composable
fun MovieDetailScreen(
    video: VideoDto,
    viewModel: MovieDetailViewModel,
    onPlay: (videoId: Int, positionMs: Long) -> Unit,
    onBack: () -> Unit,
) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    val playFocus = remember { FocusRequester() }

    // Load history when screen appears; clear when it leaves.
    LaunchedEffect(video.id) {
        viewModel.loadHistory(video.id)
    }
    DisposableEffect(video.id) {
        onDispose { /* no explicit clear needed — next loadHistory call resets */ }
    }

    // Auto-focus the primary play button once history is loaded.
    LaunchedEffect(state.loading) {
        if (!state.loading) {
            try { playFocus.requestFocus() } catch (_: Exception) {}
        }
    }

    // Determine image to show — prefer TMDb poster, fall back to thumbnail.
    val posterUrl = video.tmdbPosterUrl ?: video.thumbnailUrl

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(WatchDawgColors.Background),
    ) {

        // ── Full-bleed backdrop (blurred via gradient overlay) ────────────────
        AsyncImage(
            model              = posterUrl,
            contentDescription = null,
            contentScale       = ContentScale.Crop,
            modifier           = Modifier
                .fillMaxSize()
                .background(WatchDawgColors.SurfaceElevated),
        )
        // Dark gradient overlay so text is always legible
        Box(
            modifier = Modifier
                .fillMaxSize()
                .background(
                    Brush.horizontalGradient(
                        0.0f to Color(0x00000000),
                        0.35f to Color(0xCC000000),
                        1.0f to Color(0xF2000000),
                    )
                )
        )

        // ── Content row ───────────────────────────────────────────────────────
        Row(
            modifier = Modifier
                .fillMaxSize()
                .padding(horizontal = 56.dp, vertical = 48.dp),
            horizontalArrangement = Arrangement.spacedBy(48.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {

            // ── Poster ────────────────────────────────────────────────────────
            Box(
                modifier = Modifier
                    .widthIn(max = 260.dp)
                    .fillMaxHeight(0.75f)
                    .aspectRatio(2f / 3f)
                    .clip(MaterialTheme.shapes.large),
            ) {
                AsyncImage(
                    model              = posterUrl,
                    contentDescription = video.title,
                    contentScale       = ContentScale.Crop,
                    modifier           = Modifier
                        .fillMaxSize()
                        .background(WatchDawgColors.SurfaceElevated),
                )
                // Watched overlay
                if (state.isWatched) {
                    Box(
                        modifier = Modifier
                            .fillMaxSize()
                            .background(Color(0x99000000)),
                        contentAlignment = Alignment.Center,
                    ) {
                        Text(
                            text  = "✓ WATCHED",
                            style = MaterialTheme.typography.titleMedium,
                            color = Color.White,
                        )
                    }
                }
            }

            // ── Metadata + actions ────────────────────────────────────────────
            Column(
                modifier              = Modifier.weight(1f),
                verticalArrangement   = Arrangement.spacedBy(12.dp),
            ) {
                // Title
                Text(
                    text     = video.title ?: "Untitled",
                    style    = MaterialTheme.typography.displaySmall,
                    color    = WatchDawgColors.TextPrimary,
                    maxLines = 3,
                    overflow = TextOverflow.Ellipsis,
                )

                // Year · Rating row
                val metaLine = buildString {
                    video.tmdbYear?.let { append(it) }
                    if (video.tmdbYear != null && video.tmdbRating != null) append("  ·  ")
                    video.tmdbRating?.let { append("★%.1f".format(it)) }
                    if (!video.artist.isNullOrBlank()) {
                        if (isNotEmpty()) append("  ·  ")
                        append(video.artist)
                    }
                }
                if (metaLine.isNotBlank()) {
                    Text(
                        text  = metaLine,
                        style = MaterialTheme.typography.titleMedium,
                        color = WatchDawgColors.Orange,
                    )
                }

                Spacer(Modifier.height(8.dp))

                // ── Action buttons ────────────────────────────────────────────
                val hasResume = state.resumeLabel.isNotEmpty() && !state.loading

                if (hasResume) {
                    // Primary: Continue
                    Button(
                        onClick  = { onPlay(video.id, state.resumePositionMs) },
                        colors   = ButtonDefaults.colors(
                            containerColor        = WatchDawgColors.OrangeDim,
                            contentColor          = WatchDawgColors.Orange,
                            focusedContainerColor = WatchDawgColors.Orange,
                            focusedContentColor   = WatchDawgColors.Background,
                        ),
                        modifier = Modifier
                            .fillMaxWidth()
                            .focusRequester(playFocus)
                            .focusGlow(),
                    ) {
                        Text(
                            "▶  Continue from ${state.resumeLabel}",
                            style = MaterialTheme.typography.titleMedium,
                        )
                    }
                    // Secondary: Start Over
                    Button(
                        onClick  = { onPlay(video.id, 0L) },
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
                        Text("↺  Start Over", style = MaterialTheme.typography.titleMedium)
                    }
                } else {
                    // Primary: Play (or Play Again when watched)
                    val playLabel = if (state.isWatched) "▶  Play Again" else "▶  Play"
                    Button(
                        onClick  = { onPlay(video.id, 0L) },
                        colors   = ButtonDefaults.colors(
                            containerColor        = WatchDawgColors.OrangeDim,
                            contentColor          = WatchDawgColors.Orange,
                            focusedContainerColor = WatchDawgColors.Orange,
                            focusedContentColor   = WatchDawgColors.Background,
                        ),
                        modifier = Modifier
                            .fillMaxWidth()
                            .focusRequester(playFocus)
                            .focusGlow(),
                    ) {
                        Text(playLabel, style = MaterialTheme.typography.titleMedium)
                    }
                }

                Spacer(Modifier.height(4.dp))

                // Back button
                Button(
                    onClick  = onBack,
                    colors   = ButtonDefaults.colors(
                        containerColor        = WatchDawgColors.Surface,
                        contentColor          = WatchDawgColors.TextTertiary,
                        focusedContainerColor = WatchDawgColors.SurfaceFocused,
                        focusedContentColor   = WatchDawgColors.TextSecondary,
                    ),
                    modifier = Modifier
                        .width(200.dp)
                        .focusGlow(),
                ) {
                    Text("←  Back", style = MaterialTheme.typography.titleSmall)
                }
            }
        }
    }
}
