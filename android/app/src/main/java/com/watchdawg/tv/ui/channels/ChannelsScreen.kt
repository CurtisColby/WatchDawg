package com.watchdawg.tv.ui.channels

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
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.outlined.Circle
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.tv.material3.Button
import androidx.tv.material3.ButtonDefaults
import androidx.tv.material3.Icon
import androidx.tv.material3.ListItem
import androidx.tv.material3.ListItemDefaults
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import com.watchdawg.tv.data.auth.TokenHolder
import com.watchdawg.tv.ui.feed.FeedViewModel
import com.watchdawg.tv.ui.theme.WatchDawgColors
import com.watchdawg.tv.ui.theme.focusGlow

/**
 * Channels screen — source selector for the feed.
 *
 * Session 25: focusGlow on ListItems and Upgrade buttons.
 * Session 26 Bug 1 fix: removed manual onKeyEvent + requestFocus() handlers from
 * the ListItem and Upgrade Button. The previous handlers raced against Compose TV's
 * own directional focus resolver and caused a double-jump (two D-pad presses needed
 * instead of one). Compose TV naturally traverses between Row siblings via D-pad
 * Left/Right without any manual intervention — the handlers were redundant and harmful.
 */
@Composable
fun ChannelsScreen(
    feedViewModel: FeedViewModel,
    modifier: Modifier = Modifier,
) {
    val state        by feedViewModel.state.collectAsStateWithLifecycle()
    val isUnlocked   = TokenHolder.isUnlocked
    val selected     = state.selectedChannelIds
    val upgradingIds = state.upgradingChannelIds
    val activeCategory = state.selectedCategory

    val visibleChannels = state.channels.filter { ch ->
        ch.enabled &&
        (isUnlocked || !ch.locked) &&
        (activeCategory == null || ch.category == activeCategory)
    }

    val categoryLabel = activeCategory?.replaceFirstChar { it.uppercase() } ?: "All"

    Column(modifier = modifier.fillMaxSize().padding(end = 32.dp, top = 28.dp)) {
        Text(
            text  = "Channels",
            style = MaterialTheme.typography.displayLarge,
            color = WatchDawgColors.TextPrimary,
        )
        Text(
            text     = if (activeCategory != null)
                           "Showing $categoryLabel sources (${visibleChannels.size})"
                       else
                           "All sources (${visibleChannels.size})",
            style    = MaterialTheme.typography.bodyLarge,
            color    = WatchDawgColors.TextSecondary,
            modifier = Modifier.padding(top = 4.dp),
        )

        Spacer(Modifier.height(16.dp))

        if (visibleChannels.isEmpty()) {
            Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                Text("No channels available.", style = MaterialTheme.typography.titleLarge, color = WatchDawgColors.TextSecondary)
            }
        } else {
            LazyColumn(
                verticalArrangement = Arrangement.spacedBy(6.dp),
                contentPadding      = PaddingValues(bottom = 48.dp),
                modifier            = Modifier.fillMaxSize(),
            ) {
                items(visibleChannels, key = { it.id }) { ch ->
                    ChannelRow(
                        // ChannelDto has .name, not .displayName
                        name        = ch.name,
                        subtitle    = ch.category?.replaceFirstChar { it.uppercase() } ?: "",
                        count       = ch.videoCount,
                        checked     = selected.contains(ch.id),
                        upgrading   = upgradingIds.contains(ch.id),
                        showUpgrade = true,
                        onClick     = { feedViewModel.toggleChannel(ch.id) },
                        onUpgrade   = { feedViewModel.upgradeChannel(ch.id) },
                    )
                }
            }
        }
    }
}

@Composable
private fun ChannelRow(
    name: String,
    subtitle: String,
    count: Int,
    checked: Boolean,
    upgrading: Boolean,
    showUpgrade: Boolean,
    onClick: () -> Unit,
    onUpgrade: () -> Unit,
) {
    // Bug 1 fix: FocusRequesters and manual onKeyEvent handlers removed.
    // Compose TV's built-in directional focus resolver moves between the
    // ListItem and Button naturally on a single D-pad Right/Left press
    // because they are siblings inside the same Row. The previous manual
    // requestFocus() calls fired simultaneously with the system resolver
    // and caused every D-pad press to skip two stops.
    var rowHasFocus by remember { mutableStateOf(false) }

    Row(
        modifier              = Modifier.fillMaxWidth(),
        verticalAlignment     = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        ListItem(
            selected = checked,
            onClick  = onClick,
            leadingContent = {
                Icon(
                    imageVector        = if (checked) Icons.Filled.CheckCircle else Icons.Outlined.Circle,
                    contentDescription = null,
                    tint               = if (checked) WatchDawgColors.Orange else WatchDawgColors.TextTertiary,
                    modifier           = Modifier.size(26.dp),
                )
            },
            headlineContent = { Text(name, style = MaterialTheme.typography.titleMedium) },
            supportingContent = {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("$count videos", style = MaterialTheme.typography.bodyMedium, color = WatchDawgColors.TextSecondary)
                    if (subtitle.isNotBlank())
                        Text("  •  $subtitle", style = MaterialTheme.typography.bodyMedium, color = WatchDawgColors.TextTertiary)
                }
            },
            colors   = ListItemDefaults.colors(
                containerColor         = WatchDawgColors.Surface,
                focusedContainerColor  = WatchDawgColors.SurfaceFocused,
                selectedContainerColor = WatchDawgColors.OrangeDim,
            ),
            modifier = Modifier
                .weight(1f)
                .focusGlow()
                .onFocusChanged { rowHasFocus = it.isFocused },
        )

        if (showUpgrade) {
            Button(
                onClick  = { if (!upgrading) onUpgrade() },
                colors   = ButtonDefaults.colors(
                    containerColor        = WatchDawgColors.Surface,
                    contentColor          = WatchDawgColors.TextSecondary,
                    focusedContainerColor = WatchDawgColors.SurfaceFocused,
                    focusedContentColor   = WatchDawgColors.TextPrimary,
                ),
                modifier = Modifier
                    .width(110.dp)
                    .focusGlow(),
            ) {
                Text(if (upgrading) "…" else "⬆ Upgrade", style = MaterialTheme.typography.labelLarge)
            }
        }
    }
}
