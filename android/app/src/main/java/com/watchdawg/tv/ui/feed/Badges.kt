package com.watchdawg.tv.ui.feed

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.padding
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import com.watchdawg.tv.ui.theme.WatchDawgColors

/**
 * Small subtle pill showing the source provider (YouTube / Vimeo / Reddit).
 * Kept small and low-opacity so it doesn't compete with the thumbnail or title.
 * Uses labelSmall (one step down from before) and reduced padding.
 */
@Composable
fun ProviderBadge(label: String, modifier: Modifier = Modifier) {
    val color = when (label.lowercase()) {
        "youtube" -> WatchDawgColors.FailedBadge
        "vimeo"   -> WatchDawgColors.Blue
        "reddit"  -> WatchDawgColors.Orange
        else      -> WatchDawgColors.TextTertiary
    }
    Text(
        text = label.uppercase(),
        style = MaterialTheme.typography.labelSmall,   // smaller than before
        color = Color.White,
        modifier = modifier
            .clip(MaterialTheme.shapes.small)
            .background(color.copy(alpha = 0.55f))     // more subtle opacity
            .padding(horizontal = 5.dp, vertical = 2.dp),
    )
}

/**
 * Status badge intentionally removed from the feed grid.
 *
 * Pending/Resolved/Failed is internal resolver state that means nothing
 * actionable to the viewer — the player resolves on demand regardless.
 * Keeping this composable as a no-op stub avoids touching call sites
 * in VideoCard that still reference it.
 */
@Composable
fun StatusBadge(status: String?, modifier: Modifier = Modifier) {
    // Intentionally empty — status pills removed from feed cards.
}
