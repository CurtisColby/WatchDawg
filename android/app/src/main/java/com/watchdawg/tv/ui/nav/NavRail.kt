package com.watchdawg.tv.ui.nav

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Favorite
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.LockOpen
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.WatchLater
import androidx.compose.material.icons.outlined.FolderOpen
import androidx.compose.material.icons.outlined.History
import androidx.compose.material.icons.outlined.Podcasts
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.tv.material3.Icon
import androidx.tv.material3.ListItem
import androidx.tv.material3.ListItemDefaults
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import com.watchdawg.tv.data.auth.TokenHolder
import com.watchdawg.tv.ui.theme.WatchDawgColors
import com.watchdawg.tv.ui.theme.focusGlow

/**
 * Persistent left nav rail — keeps original signature (current, onSelect)
 * so MainActivity.kt does not need changes.
 *
 * Session 25 additions:
 *  - focusGlow() on every item for visible orange halo on D-pad focus
 *  - Focus-to-select: navigates immediately when D-pad focus lands on an item.
 *    Guard: hasInteracted flag prevents the initial Compose focus assignment
 *    (which fires on first composition) from triggering spurious navigation.
 *    The flag is set true only after the first D-pad Up/Down key event on the
 *    rail, so launch-time focus is silently ignored.
 */
@Composable
fun NavRail(
    current: String,
    onSelect: (NavSection) -> Unit,
    modifier: Modifier = Modifier,
) {
    val isUnlocked by TokenHolder.tokenFlow.collectAsStateWithLifecycle()

    // Set true on first D-pad Up/Down press — prevents initial focus from
    // firing navigation before the user has touched the remote.
    var hasInteracted by remember { mutableStateOf(false) }

    Column(
        modifier = modifier
            .fillMaxHeight()
            .width(220.dp)
            .padding(vertical = 32.dp, horizontal = 16.dp)
            .onPreviewKeyEvent { event ->
                if (event.type == KeyEventType.KeyDown &&
                    (event.key == Key.DirectionUp || event.key == Key.DirectionDown)
                ) {
                    hasInteracted = true
                }
                false
            },
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        BrandHeader()
        Spacer(Modifier.height(24.dp))

        NavSection.entries.forEach { section ->
            val isSelected = current == section.route ||
                (current.startsWith("player") && section == NavSection.FEED)

            // Track focus per item — used for focus-to-select
            var isFocused by remember { mutableStateOf(false) }
            var wasFocused by remember { mutableStateOf(false) }

            ListItem(
                selected = isSelected,
                onClick  = { onSelect(section) },
                leadingContent = {
                    Icon(
                        imageVector        = iconFor(section),
                        contentDescription = section.label,
                        modifier           = Modifier.size(26.dp),
                    )
                },
                headlineContent = {
                    Text(
                        text  = section.label,
                        style = MaterialTheme.typography.titleMedium,
                    )
                },
                colors = ListItemDefaults.colors(
                    containerColor         = WatchDawgColors.Background,
                    focusedContainerColor  = WatchDawgColors.SurfaceFocused,
                    selectedContainerColor = WatchDawgColors.OrangeDim,
                    contentColor           = WatchDawgColors.TextSecondary,
                    focusedContentColor    = WatchDawgColors.TextPrimary,
                    selectedContentColor   = WatchDawgColors.Orange,
                ),
                modifier = Modifier
                    .focusGlow()
                    .onFocusChanged { state ->
                        val nowFocused = state.isFocused
                        // Fire navigate only on false→true transition AND after
                        // the user has physically moved the D-pad (hasInteracted).
                        if (nowFocused && !wasFocused && hasInteracted) {
                            onSelect(section)
                        }
                        wasFocused = nowFocused
                        isFocused  = nowFocused
                    },
            )
        }
    }
}

@Composable
private fun BrandHeader() {
    val unlocked by TokenHolder.tokenFlow.collectAsStateWithLifecycle()
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text(
            text  = "Watch",
            style = MaterialTheme.typography.headlineMedium,
            color = WatchDawgColors.TextPrimary,
        )
        Text(
            text  = "Dawg",
            style = MaterialTheme.typography.headlineMedium,
            color = WatchDawgColors.Orange,
        )
        if (unlocked != null) {
            Icon(
                imageVector        = Icons.Filled.LockOpen,
                contentDescription = "Unlocked",
                tint               = WatchDawgColors.Orange,
                modifier           = Modifier.padding(start = 8.dp).size(18.dp),
            )
        }
    }
}

private fun iconFor(section: NavSection): ImageVector = when (section) {
    NavSection.FEED              -> Icons.Filled.Home
    NavSection.CONTINUE_WATCHING -> Icons.Outlined.History
    NavSection.WATCH_LATER       -> Icons.Filled.WatchLater
    NavSection.FAVORITES         -> Icons.Filled.Favorite
    NavSection.LIBRARY           -> Icons.Outlined.FolderOpen
    NavSection.CHANNELS          -> Icons.Outlined.Podcasts
    NavSection.SETTINGS          -> Icons.Filled.Settings
}
