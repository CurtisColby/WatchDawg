package com.watchdawg.tv.ui.channels

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import com.watchdawg.tv.ui.theme.WatchDawgColors

/**
 * ChannelsScreen — Milestone R-2.
 *
 * The Android Channels screen has been retired in favour of the web UI
 * (localhost:6868) for source management. This stub keeps the build
 * compiling during the R-2 transition. The nav rail no longer has a
 * Channels entry so this screen is unreachable in normal use.
 *
 * Will be repurposed or deleted in a future milestone.
 */
@Composable
fun ChannelsScreen(
    modifier: Modifier = Modifier,
) {
    Box(
        modifier = modifier
            .fillMaxSize()
            .padding(32.dp),
        contentAlignment = Alignment.Center,
    ) {
        Column(
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Text(
                text  = "📡  Manage Sources",
                style = MaterialTheme.typography.displayLarge,
                color = WatchDawgColors.TextPrimary,
            )
            Text(
                text  = "Use the web UI at localhost:6868 to add and manage sources.",
                style = MaterialTheme.typography.bodyLarge,
                color = WatchDawgColors.TextTertiary,
            )
        }
    }
}
