package com.watchdawg.tv.ui.home

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Paint
import androidx.compose.ui.graphics.drawscope.drawIntoCanvas
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.unit.dp
import androidx.compose.foundation.layout.size
import androidx.compose.ui.platform.LocalConfiguration
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.tv.material3.Card
import androidx.tv.material3.CardDefaults
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import com.watchdawg.tv.ui.nav.NavSection
import com.watchdawg.tv.ui.theme.WatchDawgColors

/**
 * Home Screen — Milestone R-2.5 / R-3 / R-4.
 *
 * R-4 changes:
 *   - Version badge added to BrandHeader — right-aligned in the header row.
 *     Single const val APP_VERSION to update each session. Displays as a small
 *     pill in TextTertiary color so it's visible but unobtrusive.
 *   - Grid contentPadding gains top = 12.dp to prevent first-row card tops
 *     from being clipped by the brand header bar.
 *
 * Adult card:
 *   Structurally absent when locked (TokenHolder emits null).
 *   Appears immediately when PIN is entered, disappears on lock.
 */

/** Bump this each build. Format: v{session}.{build-within-session} */
const val APP_VERSION = "v32.1"

@Composable
fun HomeScreen(
    viewModel: HomeViewModel,
    onNavigate: (route: String) -> Unit,
    modifier: Modifier = Modifier,
) {
    val isUnlocked by viewModel.isUnlocked.collectAsStateWithLifecycle()

    val sections = remember(isUnlocked) {
        NavSection.entries.filter { section ->
            if (section == NavSection.ADULT) isUnlocked != null else true
        }
    }

    Column(
        modifier = modifier
            .fillMaxSize()
            .background(WatchDawgColors.Background)
            .padding(horizontal = 48.dp),
    ) {

        // ── Brand header ──────────────────────────────────────────────────────
        Spacer(Modifier.height(20.dp))
        BrandHeader(isUnlocked = isUnlocked != null)
        Spacer(Modifier.height(20.dp))

        // ── Section card grid ─────────────────────────────────────────────────
        // top = 12.dp content padding prevents first-row card tops from being
        // clipped by the brand header on TV hardware.
        LazyVerticalGrid(
            columns               = GridCells.Fixed(3),
            contentPadding        = PaddingValues(top = 12.dp, bottom = 32.dp),
            horizontalArrangement = Arrangement.spacedBy(20.dp),
            verticalArrangement   = Arrangement.spacedBy(20.dp),
            modifier              = Modifier.fillMaxSize(),
        ) {
            items(sections, key = { it.route }) { section ->
                SectionCard(
                    section  = section,
                    onSelect = { onNavigate(section.route) },
                )
            }
        }
    }
}

// ── Brand header ──────────────────────────────────────────────────────────────

@Composable
private fun BrandHeader(isUnlocked: Boolean) {
    Row(
        verticalAlignment = Alignment.CenterVertically,
        modifier          = Modifier.fillMaxWidth(),
    ) {
        // ── Logo ──────────────────────────────────────────────────────────────
        Text(
            text  = "Watch",
            style = MaterialTheme.typography.displayMedium,
            color = WatchDawgColors.TextPrimary,
        )
        Text(
            text  = "Dawg",
            style = MaterialTheme.typography.displayMedium,
            color = WatchDawgColors.Orange,
        )

        // ── UNLOCKED badge ────────────────────────────────────────────────────
        if (isUnlocked) {
            Spacer(Modifier.width(12.dp))
            Box(
                modifier = Modifier
                    .clip(RoundedCornerShape(4.dp))
                    .background(WatchDawgColors.OrangeDim)
                    .padding(horizontal = 10.dp, vertical = 4.dp),
            ) {
                Text(
                    text  = "UNLOCKED",
                    style = MaterialTheme.typography.labelSmall,
                    color = WatchDawgColors.Orange,
                )
            }
        }

        // ── Push version badge to the far right ───────────────────────────────
        Spacer(Modifier.weight(1f))
        Box(
            modifier = Modifier
                .clip(RoundedCornerShape(4.dp))
                .background(WatchDawgColors.Surface)
                .padding(horizontal = 8.dp, vertical = 4.dp),
        ) {
            Text(
                text  = APP_VERSION,
                style = MaterialTheme.typography.labelSmall,
                color = WatchDawgColors.TextTertiary,
            )
        }
    }
}

// ── Section card ──────────────────────────────────────────────────────────────

@Composable
private fun SectionCard(
    section:  NavSection,
    onSelect: () -> Unit,
    modifier: Modifier = Modifier,
) {
    var isFocused by remember { mutableStateOf(false) }

    Card(
        onClick  = onSelect,
        colors   = CardDefaults.colors(
            containerColor        = WatchDawgColors.Surface,
            focusedContainerColor = WatchDawgColors.SurfaceFocused,
        ),
        shape = CardDefaults.shape(shape = RoundedCornerShape(16.dp)),
        modifier = modifier
            .fillMaxWidth()
            .height(170.dp)
            .onFocusChanged { isFocused = it.isFocused }
            .homeFocusGlow(isFocused)
            .drawBehind {
                if (!isFocused) return@drawBehind
                val strokePx = 3.dp.toPx()
                val cornerPx = 16.dp.toPx()
                val paint = Paint().apply {
                    asFrameworkPaint().apply {
                        isAntiAlias  = true
                        color        = android.graphics.Color.TRANSPARENT
                        style        = android.graphics.Paint.Style.STROKE
                        strokeWidth  = strokePx
                        this.color   = WatchDawgColors.Orange.copy(alpha = 0.9f).toArgb()
                    }
                }
                drawIntoCanvas { canvas ->
                    canvas.drawRoundRect(
                        left    = strokePx / 2f,
                        top     = strokePx / 2f,
                        right   = size.width  - strokePx / 2f,
                        bottom  = size.height - strokePx / 2f,
                        radiusX = cornerPx,
                        radiusY = cornerPx,
                        paint   = paint,
                    )
                }
            },
    ) {
        Box(
            modifier         = Modifier.fillMaxSize(),
            contentAlignment = Alignment.Center,
        ) {
            Column(
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(10.dp),
                modifier            = Modifier.padding(horizontal = 16.dp),
            ) {
                SectionIcon(section = section, isFocused = isFocused)

                Text(
                    text  = section.label,
                    style = MaterialTheme.typography.titleMedium,
                    color = if (isFocused) WatchDawgColors.TextPrimary else WatchDawgColors.TextSecondary,
                )
                val subtitle = subtitleFor(section)
                if (subtitle.isNotEmpty()) {
                    Text(
                        text  = subtitle,
                        style = MaterialTheme.typography.bodySmall,
                        color = WatchDawgColors.TextTertiary,
                    )
                }
            }
        }
    }
}

// ── Section icon dispatch ─────────────────────────────────────────────────────

@Composable
private fun SectionIcon(section: NavSection, isFocused: Boolean) {
    val iconSize = 52.dp
    when (section) {
        NavSection.TV                -> TvIcon(isFocused = isFocused, size = iconSize)
        NavSection.MOVIES            -> MoviesIcon(isFocused = isFocused, size = iconSize)
        NavSection.LIVE_TV           -> LiveTvIcon(isFocused = isFocused, size = iconSize)
        NavSection.MUSIC             -> MusicIcon(isFocused = isFocused, size = iconSize)
        NavSection.CONTINUE_WATCHING -> ContinueWatchingIcon(isFocused = isFocused, size = iconSize)
        NavSection.WATCH_LATER       -> WatchLaterIcon(isFocused = isFocused, size = iconSize)
        NavSection.FAVORITES         -> FavoritesIcon(isFocused = isFocused, size = iconSize)
        NavSection.LOCAL             -> LocalIcon(isFocused = isFocused, size = iconSize)
        NavSection.ADULT             -> AdultIcon(isFocused = isFocused, size = iconSize)
        NavSection.SETTINGS          -> SettingsIcon(isFocused = isFocused, size = iconSize)
    }
}

// ── Home card glow ────────────────────────────────────────────────────────────

private fun Modifier.homeFocusGlow(isFocused: Boolean): Modifier = this.drawBehind {
    if (!isFocused) return@drawBehind
    val glowColor = WatchDawgColors.Orange.copy(alpha = 0.45f)
    val radiusPx  = 28.dp.toPx()
    val glowPaint = Paint().apply {
        asFrameworkPaint().apply {
            isAntiAlias = true
            color       = android.graphics.Color.TRANSPARENT
            setShadowLayer(radiusPx, 0f, 0f, glowColor.toArgb())
        }
    }
    drawIntoCanvas { canvas ->
        canvas.drawRoundRect(
            left    = -radiusPx * 0.5f,
            top     = -radiusPx * 0.5f,
            right   = size.width  + radiusPx * 0.5f,
            bottom  = size.height + radiusPx * 0.5f,
            radiusX = radiusPx * 0.4f,
            radiusY = radiusPx * 0.4f,
            paint   = glowPaint,
        )
    }
}

// ── Static subtitles ──────────────────────────────────────────────────────────

private fun subtitleFor(section: NavSection): String = when (section) {
    NavSection.TV                -> "Browse series"
    NavSection.MOVIES            -> "Browse movies"
    NavSection.LIVE_TV           -> "Coming in Milestone I"
    NavSection.MUSIC             -> "Browse music videos"
    NavSection.CONTINUE_WATCHING -> "Pick up where you left off"
    NavSection.WATCH_LATER       -> "Your saved videos"
    NavSection.FAVORITES         -> "Your favorite clips"
    NavSection.LOCAL             -> "Downloaded to server"
    NavSection.ADULT             -> "PIN unlocked"
    NavSection.SETTINGS          -> "Server & app settings"
}
