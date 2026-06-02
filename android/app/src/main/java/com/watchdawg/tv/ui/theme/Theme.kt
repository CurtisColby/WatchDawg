package com.watchdawg.tv.ui.theme

import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.darkColorScheme

/**
 * WatchDawg brand palette: deep near-black panels with a hot-orange accent and
 * a secondary electric-blue used for status/secondary actions (matches the
 * concept art and the browser UI's blue "Resolve" affordance).
 */
object WatchDawgColors {
    val Orange = Color(0xFFFF7A18)
    val OrangeBright = Color(0xFFFF9632)
    val OrangeDim = Color(0x33FF7A18)
    val Blue = Color(0xFF4DA3FF)
    val BlueDim = Color(0x334DA3FF)

    val Background = Color(0xFF0E0E12)
    val Surface = Color(0xFF17161E)
    val SurfaceFocused = Color(0xFF211F2B)
    val SurfaceElevated = Color(0xFF1E1C28)

    val TextPrimary = Color(0xFFF4F4F8)
    val TextSecondary = Color(0xFFA8A6B4)
    val TextTertiary = Color(0xFF6E6C7A)

    val PendingBadge = Color(0xFF4DA3FF)
    val ResolvedBadge = Color(0xFF49D17A)
    val FailedBadge = Color(0xFFFF5C5C)

    val Skip = Color(0xFFFF5C5C)
    val Star = Color(0xFFFFC83D)
}

private val WatchDawgScheme = darkColorScheme(
    primary = WatchDawgColors.Orange,
    onPrimary = WatchDawgColors.Background,
    secondary = WatchDawgColors.Blue,
    background = WatchDawgColors.Background,
    onBackground = WatchDawgColors.TextPrimary,
    surface = WatchDawgColors.Surface,
    onSurface = WatchDawgColors.TextPrimary,
    surfaceVariant = WatchDawgColors.SurfaceElevated,
)

@Composable
fun WatchDawgTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = WatchDawgScheme,
        typography = WatchDawgTypography,
        content = content,
    )
}
