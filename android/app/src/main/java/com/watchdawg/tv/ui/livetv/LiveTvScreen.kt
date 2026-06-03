package com.watchdawg.tv.ui.livetv

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.border
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
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.lazy.grid.rememberLazyGridState
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.tv.material3.Button
import androidx.tv.material3.ButtonDefaults
import androidx.tv.material3.Card
import androidx.tv.material3.CardDefaults
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import coil.compose.AsyncImage
import com.watchdawg.tv.data.api.LiveTvChannelDto
import com.watchdawg.tv.ui.theme.WatchDawgColors
import com.watchdawg.tv.ui.theme.focusGlow
import kotlinx.coroutines.delay

/**
 * Live TV screen — Milestone I (Session 34).
 *
 * Displays all live TV channels grouped by [LiveTvChannelDto.groupName].
 *
 * Layout:
 *   ┌──────────────────────────────────────────────────────────────────┐
 *   │  📡  Live TV                                  N channels online  │
 *   │  Group pills: [All] [Local OTA] [News] [Sports] [Entertainment]… │
 *   ├──────────────────────────────────────────────────────────────────┤
 *   │  ── Local OTA ──────────────────────────────────────────────────  │
 *   │  [Logo][Name●] [Logo][Name●] [Logo][Name◌] …                    │
 *   │  ── News ───────────────────────────────────────────────────────  │
 *   │  [Logo][Name●] …                                                 │
 *   │  …                                                               │
 *   └──────────────────────────────────────────────────────────────────┘
 *
 * Online indicator:
 *   ● Orange dot — online (is_online = true)
 *   ● Grey dot   — offline (is_online = false)
 *   ● No dot     — not yet probed (is_online = null)
 *
 * Tune-in: selecting a channel navigates to PlayerDirect using the channel's
 * stream_url directly — live streams are HLS/MPEG-TS and ExoPlayer handles
 * both natively without any resolve step.
 *
 * Channels with null stream_url are shown but not tappable (greyed title).
 *
 * Back: popBackStack() → Home.
 * Long-press Back: handled by MainActivity root → Home.
 */
@Composable
fun LiveTvScreen(
    viewModel: LiveTvViewModel,
    onTuneIn: (streamUrl: String, channelName: String) -> Unit,
    onBack: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val state         by viewModel.state.collectAsStateWithLifecycle()
    val selectedGroup by viewModel.selectedGroup.collectAsStateWithLifecycle()

    val firstCardFocus = remember { FocusRequester() }

    // Load on first entry — ViewModel is hoisted so this only fires once
    LaunchedEffect(Unit) {
        if (state is LiveTvViewModel.LiveTvState.Loading) {
            viewModel.load()
        }
    }

    // Focus first card after load
    LaunchedEffect(state) {
        if (state is LiveTvViewModel.LiveTvState.Ready) {
            delay(150)
            try { firstCardFocus.requestFocus() } catch (_: Exception) {}
        }
    }

    BackHandler { onBack() }

    Box(
        modifier = modifier
            .fillMaxSize()
            .background(WatchDawgColors.Background),
    ) {
        when (val s = state) {
            is LiveTvViewModel.LiveTvState.Loading -> LiveTvLoadingScreen()

            is LiveTvViewModel.LiveTvState.Error -> LiveTvErrorScreen(s.message) {
                viewModel.load()
            }

            is LiveTvViewModel.LiveTvState.Empty -> LiveTvEmptyScreen()

            is LiveTvViewModel.LiveTvState.Ready -> {
                val onlineCount = s.channels.count { it.isOnline == true }

                // Determine which groups + channels to show
                val displayGrouped: Map<String, List<LiveTvChannelDto>> =
                    if (selectedGroup == null) {
                        s.grouped
                    } else {
                        s.grouped.filterKeys { it == selectedGroup }
                    }

                Column(
                    modifier = Modifier
                        .fillMaxSize()
                        .padding(horizontal = 24.dp),
                ) {
                    Spacer(Modifier.height(16.dp))

                    // ── Header ────────────────────────────────────────────────
                    Row(
                        verticalAlignment     = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.SpaceBetween,
                        modifier              = Modifier.fillMaxWidth(),
                    ) {
                        Column {
                            Text(
                                text  = "📡  Live TV",
                                style = MaterialTheme.typography.displayLarge,
                                color = WatchDawgColors.TextPrimary,
                            )
                            Text(
                                text  = "$onlineCount of ${s.channels.size} channels online",
                                style = MaterialTheme.typography.bodyLarge,
                                color = WatchDawgColors.TextTertiary,
                            )
                        }
                    }

                    Spacer(Modifier.height(8.dp))

                    // ── Group pill bar ─────────────────────────────────────────
                    if (s.groups.isNotEmpty()) {
                        LiveTvGroupPillBar(
                            groups        = s.groups,
                            selectedGroup = selectedGroup,
                            onSelectAll   = { viewModel.selectGroup(null) },
                            onSelectGroup = { viewModel.selectGroup(it) },
                        )
                    }

                    Spacer(Modifier.height(12.dp))

                    // ── Channel list — one section per group ───────────────────
                    LazyColumn(
                        modifier              = Modifier.fillMaxSize(),
                        verticalArrangement   = Arrangement.spacedBy(0.dp),
                        contentPadding        = PaddingValues(bottom = 32.dp),
                    ) {
                        displayGrouped.entries.forEachIndexed { groupIndex, (groupName, channels) ->
                            // Group header
                            item(key = "header_$groupName") {
                                LiveTvGroupHeader(groupName)
                            }

                            // Channel grid row for this group — horizontal lazy row of cards
                            item(key = "row_$groupName") {
                                LiveTvChannelRow(
                                    channels       = channels,
                                    firstCardFocus = if (groupIndex == 0) firstCardFocus else null,
                                    onTuneIn       = onTuneIn,
                                )
                                Spacer(Modifier.height(8.dp))
                            }
                        }
                    }
                }
            }
        }
    }
}

// ── Group header ──────────────────────────────────────────────────────────────

@Composable
private fun LiveTvGroupHeader(groupName: String) {
    Row(
        verticalAlignment = Alignment.CenterVertically,
        modifier          = Modifier
            .fillMaxWidth()
            .padding(vertical = 8.dp),
    ) {
        Text(
            text  = groupName,
            style = MaterialTheme.typography.titleMedium,
            color = WatchDawgColors.Orange,
        )
        Spacer(Modifier.width(12.dp))
        Box(
            modifier = Modifier
                .height(1.dp)
                .weight(1f)
                .background(WatchDawgColors.Orange.copy(alpha = 0.25f)),
        )
    }
}

// ── Horizontal channel row ────────────────────────────────────────────────────

@Composable
private fun LiveTvChannelRow(
    channels: List<LiveTvChannelDto>,
    firstCardFocus: FocusRequester?,
    onTuneIn: (streamUrl: String, channelName: String) -> Unit,
) {
    LazyRow(
        state                 = rememberLazyListState(),
        horizontalArrangement = Arrangement.spacedBy(12.dp),
        contentPadding        = PaddingValues(horizontal = 2.dp, vertical = 4.dp),
    ) {
        items(channels, key = { it.id }) { channel ->
            val cardFocus = remember { FocusRequester() }
            val isFirst   = channels.indexOf(channel) == 0
            LiveTvChannelCard(
                channel  = channel,
                onTuneIn = onTuneIn,
                modifier = if (isFirst && firstCardFocus != null)
                    Modifier.focusRequester(firstCardFocus)
                else
                    Modifier.focusRequester(cardFocus),
            )
        }
    }
}

// ── Channel card ──────────────────────────────────────────────────────────────

@Composable
private fun LiveTvChannelCard(
    channel: LiveTvChannelDto,
    onTuneIn: (streamUrl: String, channelName: String) -> Unit,
    modifier: Modifier = Modifier,
) {
    val hasStream   = !channel.streamUrl.isNullOrBlank()
    val isOnline    = channel.isOnline
    val nameColor   = if (hasStream) WatchDawgColors.TextPrimary else WatchDawgColors.TextTertiary

    // Online dot colour:
    //   true  → orange (online)
    //   false → dark grey (offline)
    //   null  → transparent (not yet probed — no dot shown)
    val dotColor = when (isOnline) {
        true  -> WatchDawgColors.Orange
        false -> WatchDawgColors.TextTertiary.copy(alpha = 0.5f)
        null  -> Color.Transparent
    }

    Card(
        onClick  = {
            if (hasStream) {
                onTuneIn(channel.streamUrl!!, channel.name)
            }
        },
        colors   = CardDefaults.colors(
            containerColor        = WatchDawgColors.Surface,
            focusedContainerColor = WatchDawgColors.SurfaceFocused,
        ),
        modifier = modifier
            .width(180.dp)
            .focusGlow(),
    ) {
        Column(
            modifier            = Modifier.padding(12.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            // Logo or fallback icon
            Box(
                modifier        = Modifier
                    .size(64.dp)
                    .clip(RoundedCornerShape(8.dp))
                    .background(WatchDawgColors.Background),
                contentAlignment = Alignment.Center,
            ) {
                if (!channel.logoUrl.isNullOrBlank()) {
                    AsyncImage(
                        model             = channel.logoUrl,
                        contentDescription = channel.name,
                        contentScale       = ContentScale.Fit,
                        modifier           = Modifier.size(60.dp),
                    )
                } else {
                    // Fallback: first letter of channel name
                    Text(
                        text  = channel.name.take(1).uppercase(),
                        style = MaterialTheme.typography.titleLarge,
                        color = WatchDawgColors.Orange,
                    )
                }
            }

            // Channel name + online indicator
            Row(
                verticalAlignment     = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.Center,
                modifier              = Modifier.fillMaxWidth(),
            ) {
                Text(
                    text     = channel.name,
                    style    = MaterialTheme.typography.labelLarge,
                    color    = nameColor,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f, fill = false),
                )
                if (isOnline != null) {
                    Spacer(Modifier.width(6.dp))
                    Box(
                        modifier = Modifier
                            .size(8.dp)
                            .clip(CircleShape)
                            .background(dotColor),
                    )
                }
            }

            // Offline label if known offline
            if (isOnline == false) {
                Text(
                    text  = "Offline",
                    style = MaterialTheme.typography.labelSmall,
                    color = WatchDawgColors.TextTertiary,
                )
            }
        }
    }
}

// ── Group pill bar ────────────────────────────────────────────────────────────

@Composable
private fun LiveTvGroupPillBar(
    groups: List<String>,
    selectedGroup: String?,
    onSelectAll: () -> Unit,
    onSelectGroup: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    LazyRow(
        state                 = rememberLazyListState(),
        modifier              = modifier.height(48.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
        verticalAlignment     = Alignment.CenterVertically,
        contentPadding        = PaddingValues(horizontal = 4.dp),
    ) {
        item(key = "all") {
            LiveTvGroupPill(
                label      = "All",
                isSelected = selectedGroup == null,
                onClick    = onSelectAll,
            )
        }
        items(groups, key = { it }) { group ->
            LiveTvGroupPill(
                label      = group,
                isSelected = selectedGroup == group,
                onClick    = { onSelectGroup(group) },
            )
        }
    }
}

@Composable
private fun LiveTvGroupPill(
    label: String,
    isSelected: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Button(
        onClick  = onClick,
        colors   = ButtonDefaults.colors(
            containerColor        = if (isSelected) WatchDawgColors.Orange else WatchDawgColors.OrangeDim,
            contentColor          = if (isSelected) WatchDawgColors.Background else WatchDawgColors.Orange,
            focusedContainerColor = WatchDawgColors.Orange,
            focusedContentColor   = WatchDawgColors.Background,
        ),
        modifier = modifier.focusGlow(),
    ) {
        Text(text = label, style = MaterialTheme.typography.labelLarge)
    }
}

// ── Loading / error / empty states ───────────────────────────────────────────

@Composable
private fun LiveTvLoadingScreen() {
    Box(
        modifier         = Modifier.fillMaxSize(),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            text  = "Loading channels…",
            style = MaterialTheme.typography.bodyLarge,
            color = WatchDawgColors.TextTertiary,
        )
    }
}

@Composable
private fun LiveTvErrorScreen(message: String, onRetry: () -> Unit) {
    Box(
        modifier         = Modifier.fillMaxSize(),
        contentAlignment = Alignment.Center,
    ) {
        Column(
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            Text(
                text  = "⚠  Could not load channels",
                style = MaterialTheme.typography.titleLarge,
                color = WatchDawgColors.TextPrimary,
            )
            Text(
                text  = message,
                style = MaterialTheme.typography.bodyMedium,
                color = WatchDawgColors.TextTertiary,
            )
            Button(
                onClick = onRetry,
                colors  = ButtonDefaults.colors(
                    containerColor        = WatchDawgColors.OrangeDim,
                    contentColor          = WatchDawgColors.Orange,
                    focusedContainerColor = WatchDawgColors.Orange,
                    focusedContentColor   = WatchDawgColors.Background,
                ),
                modifier = Modifier.focusGlow(),
            ) {
                Text("↺  Retry", style = MaterialTheme.typography.titleSmall)
            }
        }
    }
}

@Composable
private fun LiveTvEmptyScreen() {
    Box(
        modifier         = Modifier.fillMaxSize(),
        contentAlignment = Alignment.Center,
    ) {
        Column(
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Text(
                text  = "📡  No channels yet",
                style = MaterialTheme.typography.titleLarge,
                color = WatchDawgColors.TextPrimary,
            )
            Text(
                text  = "Import an M3U playlist or add channels via the web UI",
                style = MaterialTheme.typography.bodyLarge,
                color = WatchDawgColors.TextTertiary,
            )
        }
    }
}
