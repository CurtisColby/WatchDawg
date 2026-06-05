package com.watchdawg.tv.ui.livetv

import androidx.activity.compose.BackHandler
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.tween
import androidx.compose.animation.slideInHorizontally
import androidx.compose.animation.slideOutHorizontally
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.navigation.NavBackStackEntry
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
import kotlinx.coroutines.launch

/**
 * Live TV screen — Session 35 two-panel layout.
 * Bug fixes — Session 37:
 *   1. Right-panel focus trap: after a Favorite or Delete action in the detail
 *      panel, closeDetailPanel() fires but focus was lost to the void because
 *      the list had re-rendered mid-action and the old FocusRequester targets
 *      were no longer attached. Fixed by adding LaunchedEffect(detailPanelActive)
 *      that re-requests focus on the selectedChannel's list row when the panel
 *      closes (detailPanelActive goes false).
 *   2. Fast-scroll crash: rapid D-pad scrolling caused FocusRequester.requestFocus()
 *      to throw FocusRequesterNotAttachedException because items composed during
 *      a fast fling may not have finished attaching their nodes yet. Fixed by
 *      guarding every requestFocus() call with try/catch and adding a
 *      listState.isScrollInProgress check before focus restore attempts.
 *
 * Layout:
 * ┌─────────────────────┬──────────────────────────────────┐
 * │ 📡 Live TV          │                                  │
 * │                     │  [Logo]  BBC Earth               │
 * │ ▼ ⭐ Favorites  (3) │  Animals + Nature                │
 * │    BBC Earth     ●  │  ● Online                        │
 * │    Love Nature   ●  │                                  │
 * │ ▼ Animals       (8) │  [▶ Watch]                       │
 * │    BBC Earth     ●  │  [★ Unfavorite]                  │
 * │    Dog Whisperer ●  │  [🗑 Remove]                     │
 * │ ▶ Animation    (12) │                                  │
 * │ ▼ News          (5) │  D-pad Left to return to list    │
 * └─────────────────────┴──────────────────────────────────┘
 *
 * Navigation:
 *   D-pad Up/Down   — move through channels and group headers in left panel
 *   Select on group — collapse / expand that group
 *   Select on channel — tune in immediately
 *   D-pad Right from channel — open right detail panel for that channel
 *   D-pad Left from detail   — return to channel list
 *   Select on Watch/Favorite/Remove in detail panel — perform action
 *
 * Collapsible groups:
 *   Each group header shows ▼ (expanded) or ▶ (collapsed) + channel count.
 *   Select on a header toggles collapse. Collapsed groups show only the header.
 *   The ⭐ Favorites group cannot be collapsed.
 *
 * Dead channel filter:
 *   Channels with isOnline == false are hidden. Count shown in header subtitle.
 *
 * Focus restore on Back from player:
 *   [lastTunedChannelId] is set in the ViewModel when onTuneIn fires.
 *   A LaunchedEffect keyed to [currentEntry] fires on every screen re-entry.
 *   It builds a flat index of visible list items, scrolls to the matching
 *   channel row, and requests focus on it via a per-item FocusRequester map.
 */
@Composable
fun LiveTvScreen(
    viewModel: LiveTvViewModel,
    currentEntry: NavBackStackEntry?,
    onTuneIn: (streamUrl: String, channelName: String) -> Unit,
    onBack: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val state             by viewModel.state.collectAsStateWithLifecycle()
    val collapsedGroups   by viewModel.collapsedGroups.collectAsStateWithLifecycle()
    val selectedChannel   by viewModel.selectedChannel.collectAsStateWithLifecycle()
    val detailPanelActive by viewModel.detailPanelActive.collectAsStateWithLifecycle()
    val sidebarOpen       by viewModel.sidebarOpen.collectAsStateWithLifecycle()
    val selectedGroup     by viewModel.selectedGroup.collectAsStateWithLifecycle()
    val pendingDelete     by viewModel.pendingDelete.collectAsStateWithLifecycle()
    val isActioning       by viewModel.isActioning.collectAsStateWithLifecycle()
    val lastTunedId       by viewModel.lastTunedChannelId.collectAsStateWithLifecycle()

    val listState         = rememberLazyListState()
    val firstItemFocus    = remember { FocusRequester() }
    val detailFirstFocus  = remember { FocusRequester() }
    val sidebarFirstFocus = remember { FocusRequester() }
    val scope             = rememberCoroutineScope()

    // Per-channel FocusRequester map — allows focus restore to the exact item.
    // Built fresh each composition from the channel list so it stays in sync.
    val channelFocusMap   = remember { mutableMapOf<Int, FocusRequester>() }

    LaunchedEffect(Unit) {
        if (state is LiveTvViewModel.LiveTvState.Loading) viewModel.load()
    }

    // ── Focus restore on Back from player ─────────────────────────────────────
    //
    // LaunchedEffect(currentEntry) fires every time this screen's back-stack
    // entry changes — which includes re-entry after Back from the player.
    // LaunchedEffect(Unit) does NOT re-run on re-entry, so it is intentionally
    // NOT used here.
    //
    // Logic:
    //   1. Wait 200ms for ExoPlayer release + Compose layout to settle.
    //   2. Guard: if the list is still mid-fling do not attempt focus (fast-scroll
    //      crash fix — a fling that started before Back can still be running).
    //   3. If we have a lastTunedId and the state is Ready:
    //      a. Build a flat list of visible items (favs + groups + channels,
    //         respecting collapsed groups and the active group filter).
    //      b. Find the index of the matching channel row.
    //      c. Scroll to that index so the item is visible.
    //      d. Wait 100ms for layout, then request focus wrapped in try/catch.
    //   4. If no lastTunedId (first visit), fall through to the existing
    //      state-driven focus that lands on firstItemFocus.
    LaunchedEffect(currentEntry) {
        delay(200)
        if (listState.isScrollInProgress) delay(300)

        val s = state
        if (s !is LiveTvViewModel.LiveTvState.Ready) return@LaunchedEffect

        val tunedId = lastTunedId
        if (tunedId != null) {
            // Build the flat visible item index in the same order as the LazyColumn.
            val flatIds = mutableListOf<Pair<String, Int?>>()

            if (s.favorites.isNotEmpty()) {
                flatIds.add("FAV_HEADER" to null)
                if (LiveTvViewModel.FAV_GROUP_NAME !in collapsedGroups) {
                    s.favorites.forEach { flatIds.add("FAV_CHANNEL" to it.id) }
                }
            }

            val displayGrouped = if (selectedGroup == null) s.grouped
            else s.grouped.filterKeys { it == selectedGroup }

            displayGrouped.entries.forEach { (groupName, channels) ->
                flatIds.add("GROUP_HEADER" to null)
                if (groupName !in collapsedGroups) {
                    channels.forEach { flatIds.add("CHANNEL" to it.id) }
                }
            }

            val targetIndex = flatIds.indexOfFirst { (type, id) ->
                (type == "CHANNEL" || type == "FAV_CHANNEL") && id == tunedId
            }

            if (targetIndex >= 0) {
                listState.animateScrollToItem(targetIndex)
                delay(100)
                channelFocusMap[tunedId]?.let {
                    try { it.requestFocus() } catch (_: Exception) {}
                }
                return@LaunchedEffect
            }
        }

        // No lastTunedId or channel not found — focus first item as normal
        delay(50)
        try { firstItemFocus.requestFocus() } catch (_: Exception) {}
    }

    // First-entry focus (only runs when state transitions from Loading → Ready)
    LaunchedEffect(state) {
        if (state is LiveTvViewModel.LiveTvState.Ready && lastTunedId == null) {
            delay(150)
            try { firstItemFocus.requestFocus() } catch (_: Exception) {}
        }
    }

    // ── Detail panel open: focus Watch button ─────────────────────────────────
    LaunchedEffect(detailPanelActive) {
        if (detailPanelActive) {
            delay(100)
            try { detailFirstFocus.requestFocus() } catch (_: Exception) {}
        } else {
            // ── BUG FIX: Right-panel focus trap ──────────────────────────────
            // Detail panel just closed (Favorite or Delete action completed, or
            // user pressed D-pad Left). The list may have re-rendered (load() was
            // called after a Favorite/Delete) which detaches old FocusRequesters.
            // We wait 250ms for the reload + recomposition to settle, then
            // re-request focus on whichever channel is still selected, falling
            // back to the first item if the channel is gone from the list.
            delay(250)
            if (listState.isScrollInProgress) delay(200)
            val selId = selectedChannel?.id
            var focused = false
            if (selId != null) {
                channelFocusMap[selId]?.let {
                    try { it.requestFocus(); focused = true } catch (_: Exception) {}
                }
            }
            if (!focused) {
                try { firstItemFocus.requestFocus() } catch (_: Exception) {}
            }
        }
    }

    LaunchedEffect(sidebarOpen) {
        if (sidebarOpen) {
            delay(150)
            try { sidebarFirstFocus.requestFocus() } catch (_: Exception) {}
        }
    }

    BackHandler {
        when {
            pendingDelete != null -> viewModel.cancelDelete()
            detailPanelActive     -> viewModel.closeDetailPanel()
            sidebarOpen           -> viewModel.closeSidebar()
            else                  -> onBack()
        }
    }

    Box(
        modifier = modifier
            .fillMaxSize()
            .background(WatchDawgColors.Background),
    ) {
        when (val s = state) {
            is LiveTvViewModel.LiveTvState.Loading -> LiveTvLoadingScreen()
            is LiveTvViewModel.LiveTvState.Error   -> LiveTvErrorScreen(s.message) { viewModel.load() }
            is LiveTvViewModel.LiveTvState.Empty   -> LiveTvEmptyScreen()

            is LiveTvViewModel.LiveTvState.Ready -> {
                val onlineCount = s.channels.count { it.isOnline == true }

                val displayGrouped: Map<String, List<LiveTvChannelDto>> =
                    if (selectedGroup == null) s.grouped
                    else s.grouped.filterKeys { it == selectedGroup }

                Row(modifier = Modifier.fillMaxSize()) {

                    // ── LEFT PANEL — channel list ─────────────────────────────
                    Column(
                        modifier = Modifier
                            .weight(1f)
                            .fillMaxHeight()
                            .padding(start = 24.dp, top = 16.dp, end = 8.dp, bottom = 16.dp),
                    ) {
                        // Header
                        Text(
                            text  = "📡  Live TV",
                            style = MaterialTheme.typography.displayLarge,
                            color = WatchDawgColors.TextPrimary,
                        )
                        Text(
                            text  = buildString {
                                append("$onlineCount of ${s.channels.size} channels online")
                                if (s.hiddenOfflineCount > 0) append(" · ${s.hiddenOfflineCount} offline hidden")
                            },
                            style = MaterialTheme.typography.bodyMedium,
                            color = WatchDawgColors.TextTertiary,
                        )

                        Spacer(Modifier.height(12.dp))

                        // Filter sidebar toggle button
                        Button(
                            onClick = { viewModel.openSidebar() },
                            colors  = ButtonDefaults.colors(
                                containerColor        = WatchDawgColors.OrangeDim,
                                contentColor          = WatchDawgColors.Orange,
                                focusedContainerColor = WatchDawgColors.Orange,
                                focusedContentColor   = WatchDawgColors.Background,
                            ),
                            modifier = Modifier.focusGlow(),
                        ) {
                            Text(
                                text  = if (selectedGroup != null) "≡  $selectedGroup" else "≡  All Groups",
                                style = MaterialTheme.typography.labelMedium,
                            )
                        }

                        Spacer(Modifier.height(8.dp))

                        // Channel list
                        LazyColumn(
                            state           = listState,
                            modifier        = Modifier.fillMaxSize(),
                            contentPadding  = androidx.compose.foundation.layout.PaddingValues(bottom = 32.dp),
                        ) {
                            // ── Favorites synthetic group ─────────────────────
                            if (s.favorites.isNotEmpty()) {
                                val isFavCollapsed = LiveTvViewModel.FAV_GROUP_NAME in collapsedGroups
                                item(key = "fav_header") {
                                    LiveTvGroupRow(
                                        name        = LiveTvViewModel.FAV_GROUP_NAME,
                                        count       = s.favorites.size,
                                        isCollapsed = isFavCollapsed,
                                        isSpecial   = true,
                                        collapsible = true,
                                        isFirstItem = true,
                                        firstFocus  = firstItemFocus,
                                        onClick     = { viewModel.toggleGroupCollapsed(LiveTvViewModel.FAV_GROUP_NAME) },
                                    )
                                }

                                if (!isFavCollapsed) items(s.favorites, key = { "fav_${it.id}" }) { channel ->
                                    val fr = channelFocusMap.getOrPut(channel.id) { FocusRequester() }
                                    LiveTvChannelRow(
                                        channel        = channel,
                                        isSelected     = selectedChannel?.id == channel.id,
                                        focusRequester = fr,
                                        onFocus        = { viewModel.selectChannel(channel) },
                                        onSelect       = {
                                            if (!channel.streamUrl.isNullOrBlank()) {
                                                viewModel.recordTunedIn(channel.id)
                                                onTuneIn(channel.streamUrl, channel.name)
                                            }
                                        },
                                        onDpadRight = {
                                            viewModel.selectChannel(channel)
                                            viewModel.openDetailPanel()
                                        },
                                    )
                                }
                            }

                            // ── Regular groups ────────────────────────────────
                            displayGrouped.entries.forEachIndexed { idx, (groupName, channels) ->
                                val isCollapsed = groupName in collapsedGroups
                                val isFirstItem = s.favorites.isEmpty() && idx == 0

                                item(key = "group_$groupName") {
                                    LiveTvGroupRow(
                                        name        = groupName,
                                        count       = channels.size,
                                        isCollapsed = isCollapsed,
                                        isSpecial   = false,
                                        collapsible = true,
                                        isFirstItem = isFirstItem,
                                        firstFocus  = firstItemFocus,
                                        onClick     = { viewModel.toggleGroupCollapsed(groupName) },
                                    )
                                }

                                if (!isCollapsed) {
                                    items(channels, key = { it.id }) { channel ->
                                        val fr = channelFocusMap.getOrPut(channel.id) { FocusRequester() }
                                        LiveTvChannelRow(
                                            channel        = channel,
                                            isSelected     = selectedChannel?.id == channel.id,
                                            focusRequester = fr,
                                            onFocus        = { viewModel.selectChannel(channel) },
                                            onSelect       = {
                                                if (!channel.streamUrl.isNullOrBlank()) {
                                                    viewModel.recordTunedIn(channel.id)
                                                    onTuneIn(channel.streamUrl, channel.name)
                                                }
                                            },
                                            onDpadRight = {
                                                viewModel.selectChannel(channel)
                                                viewModel.openDetailPanel()
                                            },
                                        )
                                    }
                                }
                            }
                        }
                    }

                    // ── RIGHT PANEL — detail / actions ────────────────────────
                    Box(
                        modifier = Modifier
                            .weight(1f)
                            .fillMaxHeight()
                            .padding(24.dp),
                        contentAlignment = Alignment.TopStart,
                    ) {
                        if (selectedChannel != null) {
                            LiveTvDetailPanel(
                                channel       = selectedChannel!!,
                                isPanelActive = detailPanelActive,
                                isActioning   = isActioning,
                                watchFocus    = detailFirstFocus,
                                onTuneIn      = {
                                    if (!selectedChannel!!.streamUrl.isNullOrBlank()) {
                                        viewModel.recordTunedIn(selectedChannel!!.id)
                                        onTuneIn(selectedChannel!!.streamUrl!!, selectedChannel!!.name)
                                    }
                                },
                                onFavorite    = { viewModel.toggleFavorite(selectedChannel!!) },
                                onDelete      = { viewModel.requestDelete(selectedChannel!!) },
                                onDpadLeft    = { viewModel.closeDetailPanel() },
                            )
                        } else {
                            Column(
                                modifier            = Modifier.fillMaxSize(),
                                verticalArrangement = Arrangement.Center,
                                horizontalAlignment = Alignment.CenterHorizontally,
                            ) {
                                Text(
                                    text      = "← Select a channel",
                                    style     = MaterialTheme.typography.titleMedium,
                                    color     = WatchDawgColors.TextTertiary,
                                    textAlign = TextAlign.Center,
                                )
                                Spacer(Modifier.height(8.dp))
                                Text(
                                    text      = "Press Select to tune in\nPress D-pad Right for options",
                                    style     = MaterialTheme.typography.bodyMedium,
                                    color     = WatchDawgColors.TextTertiary.copy(alpha = 0.6f),
                                    textAlign = TextAlign.Center,
                                )
                            }
                        }
                    }
                }

                // ── Sidebar overlay ───────────────────────────────────────────
                AnimatedVisibility(
                    visible = sidebarOpen,
                    enter   = slideInHorizontally(initialOffsetX = { -it }, animationSpec = tween(220)),
                    exit    = slideOutHorizontally(targetOffsetX = { -it }, animationSpec = tween(180)),
                ) {
                    LiveTvSidebar(
                        groups            = s.groups,
                        selectedGroup     = selectedGroup,
                        sidebarFirstFocus = sidebarFirstFocus,
                        hasFavorites      = s.favorites.isNotEmpty(),
                        onSelectAll       = { viewModel.selectGroup(null) },
                        onSelectGroup     = { viewModel.selectGroup(it) },
                        onDismiss         = { viewModel.closeSidebar() },
                    )
                }

                // ── Delete confirm overlay ────────────────────────────────────
                if (pendingDelete != null) {
                    LiveTvDeleteConfirm(
                        channel     = pendingDelete!!,
                        isActioning = isActioning,
                        onDelete    = { viewModel.confirmDelete() },
                        onDismiss   = { viewModel.cancelDelete() },
                    )
                }
            }
        }
    }
}

// ── Group header row ──────────────────────────────────────────────────────────

@Composable
private fun LiveTvGroupRow(
    name: String,
    count: Int,
    isCollapsed: Boolean,
    isSpecial: Boolean,
    collapsible: Boolean,
    isFirstItem: Boolean,
    firstFocus: FocusRequester,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val arrow       = when { !collapsible -> ""; isCollapsed -> "▶  "; else -> "▼  " }
    val accentColor = if (isSpecial) Color(0xFFEAB308) else WatchDawgColors.Orange

    Button(
        onClick  = onClick,
        colors   = ButtonDefaults.colors(
            containerColor        = Color.Transparent,
            contentColor          = accentColor,
            focusedContainerColor = accentColor.copy(alpha = 0.15f),
            focusedContentColor   = accentColor,
        ),
        modifier = modifier
            .fillMaxWidth()
            .padding(top = 8.dp, bottom = 2.dp)
            .then(if (isFirstItem) Modifier.focusRequester(firstFocus) else Modifier)
            .focusGlow(),
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            modifier          = Modifier.fillMaxWidth(),
        ) {
            Box(
                modifier = Modifier
                    .width(3.dp)
                    .height(16.dp)
                    .background(accentColor, RoundedCornerShape(2.dp)),
            )
            Spacer(Modifier.width(8.dp))
            Text(
                text       = "$arrow$name",
                style      = MaterialTheme.typography.titleSmall,
                fontWeight = FontWeight.Bold,
                color      = accentColor,
                modifier   = Modifier.weight(1f),
                maxLines   = 1,
                overflow   = TextOverflow.Ellipsis,
            )
            Text(
                text  = "($count)",
                style = MaterialTheme.typography.labelSmall,
                color = WatchDawgColors.TextTertiary,
            )
        }
    }
}

// ── Channel list row ──────────────────────────────────────────────────────────
//
// Select → tune in immediately.
// D-pad Right → open detail panel for this channel.
// onFocus → update selectedChannel so detail panel shows this channel.

@Composable
private fun LiveTvChannelRow(
    channel: LiveTvChannelDto,
    isSelected: Boolean,
    focusRequester: FocusRequester,
    onFocus: () -> Unit,
    onSelect: () -> Unit,
    onDpadRight: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val hasStream   = !channel.streamUrl.isNullOrBlank()
    val isOnline    = channel.isOnline
    val dotColor    = when (isOnline) {
        true  -> WatchDawgColors.Orange
        false -> WatchDawgColors.TextTertiary.copy(alpha = 0.4f)
        null  -> Color.Transparent
    }
    val nameColor   = if (hasStream) WatchDawgColors.TextPrimary else WatchDawgColors.TextTertiary

    Card(
        onClick  = { if (hasStream) onSelect() },
        colors   = CardDefaults.colors(
            containerColor        = if (isSelected) WatchDawgColors.OrangeDim else Color.Transparent,
            focusedContainerColor = WatchDawgColors.SurfaceFocused,
        ),
        modifier = modifier
            .fillMaxWidth()
            .padding(horizontal = 4.dp, vertical = 2.dp)
            .focusRequester(focusRequester)
            .onFocusChanged { if (it.isFocused) onFocus() }
            .onPreviewKeyEvent { event ->
                if (event.key == Key.DirectionRight && event.type == KeyEventType.KeyDown) {
                    onDpadRight(); true
                } else false
            }
            .focusGlow(),
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            modifier          = Modifier
                .fillMaxWidth()
                .padding(horizontal = 12.dp, vertical = 10.dp),
        ) {
            // Logo or initial
            Box(
                modifier         = Modifier
                    .size(40.dp)
                    .clip(RoundedCornerShape(6.dp))
                    .background(WatchDawgColors.Surface),
                contentAlignment = Alignment.Center,
            ) {
                if (!channel.logoUrl.isNullOrBlank()) {
                    AsyncImage(
                        model              = channel.logoUrl,
                        contentDescription = channel.name,
                        contentScale       = ContentScale.Fit,
                        modifier           = Modifier.size(36.dp),
                    )
                } else {
                    Text(
                        text  = channel.name.take(1).uppercase(),
                        style = MaterialTheme.typography.labelMedium,
                        color = WatchDawgColors.Orange,
                    )
                }
            }

            Spacer(Modifier.width(12.dp))

            // Name + group
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text     = channel.name,
                    style    = MaterialTheme.typography.bodyMedium,
                    color    = nameColor,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                if (!channel.groupName.isNullOrBlank()) {
                    Text(
                        text     = channel.groupName,
                        style    = MaterialTheme.typography.labelSmall,
                        color    = WatchDawgColors.TextTertiary,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
            }

            // Online dot
            if (isOnline != null) {
                Spacer(Modifier.width(8.dp))
                Box(
                    modifier = Modifier
                        .size(8.dp)
                        .clip(CircleShape)
                        .background(dotColor),
                )
            }
        }
    }
}

// ── Detail panel ──────────────────────────────────────────────────────────────

@Composable
private fun LiveTvDetailPanel(
    channel: LiveTvChannelDto,
    isPanelActive: Boolean,
    isActioning: Boolean,
    watchFocus: FocusRequester,
    onTuneIn: () -> Unit,
    onFavorite: () -> Unit,
    onDelete: () -> Unit,
    onDpadLeft: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val hasStream  = !channel.streamUrl.isNullOrBlank()
    val isOnline   = channel.isOnline
    val isFavorite = channel.isFavorite == true

    Column(
        modifier            = modifier.fillMaxWidth(),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        // Logo
        Box(
            modifier         = Modifier
                .size(80.dp)
                .clip(RoundedCornerShape(12.dp))
                .background(WatchDawgColors.Surface),
            contentAlignment = Alignment.Center,
        ) {
            if (!channel.logoUrl.isNullOrBlank()) {
                AsyncImage(
                    model              = channel.logoUrl,
                    contentDescription = channel.name,
                    contentScale       = ContentScale.Fit,
                    modifier           = Modifier.size(72.dp),
                )
            } else {
                Text(
                    text  = channel.name.take(1).uppercase(),
                    style = MaterialTheme.typography.displayLarge,
                    color = WatchDawgColors.Orange,
                )
            }
        }

        // Channel name
        Text(
            text     = channel.name,
            style    = MaterialTheme.typography.titleLarge,
            color    = WatchDawgColors.TextPrimary,
            maxLines = 2,
            overflow = TextOverflow.Ellipsis,
        )

        // Group + online status
        if (!channel.groupName.isNullOrBlank()) {
            Text(
                text  = channel.groupName,
                style = MaterialTheme.typography.bodyMedium,
                color = WatchDawgColors.TextTertiary,
            )
        }

        Row(verticalAlignment = Alignment.CenterVertically) {
            val dotColor = when (isOnline) {
                true  -> WatchDawgColors.Orange
                false -> WatchDawgColors.TextTertiary.copy(alpha = 0.4f)
                null  -> Color.Transparent
            }
            if (isOnline != null) {
                Box(
                    modifier = Modifier
                        .size(8.dp)
                        .clip(CircleShape)
                        .background(dotColor),
                )
                Spacer(Modifier.width(6.dp))
            }
            Text(
                text  = when (isOnline) {
                    true  -> "Online"
                    false -> "Offline"
                    null  -> "Status unknown"
                },
                style = MaterialTheme.typography.labelMedium,
                color = WatchDawgColors.TextTertiary,
            )
        }

        Spacer(Modifier.height(4.dp))

        // ── Action buttons ────────────────────────────────────────────────────

        // Watch button
        Button(
            onClick  = onTuneIn,
            enabled  = hasStream && !isActioning,
            colors   = ButtonDefaults.colors(
                containerColor        = WatchDawgColors.OrangeDim,
                contentColor          = WatchDawgColors.Orange,
                focusedContainerColor = WatchDawgColors.Orange,
                focusedContentColor   = WatchDawgColors.Background,
            ),
            modifier = Modifier
                .fillMaxWidth()
                .then(if (isPanelActive) Modifier.focusRequester(watchFocus) else Modifier)
                .onPreviewKeyEvent { event ->
                    if (event.key == Key.DirectionLeft && event.type == KeyEventType.KeyDown) {
                        onDpadLeft(); true
                    } else false
                }
                .focusGlow(),
        ) {
            Text("▶  Watch Live", style = MaterialTheme.typography.titleSmall)
        }

        // Favorite / Unfavorite button
        Button(
            onClick  = onFavorite,
            enabled  = !isActioning,
            colors   = ButtonDefaults.colors(
                containerColor        = Color(0x22EAB308),
                contentColor          = Color(0xFFEAB308),
                focusedContainerColor = Color(0xFFEAB308),
                focusedContentColor   = WatchDawgColors.Background,
            ),
            modifier = Modifier
                .fillMaxWidth()
                .onPreviewKeyEvent { event ->
                    if (event.key == Key.DirectionLeft && event.type == KeyEventType.KeyDown) {
                        onDpadLeft(); true
                    } else false
                }
                .focusGlow(),
        ) {
            Text(
                text  = if (isFavorite) "★  Unfavorite" else "☆  Add to Favorites",
                style = MaterialTheme.typography.titleSmall,
            )
        }

        // Remove channel button
        Button(
            onClick  = onDelete,
            enabled  = !isActioning,
            colors   = ButtonDefaults.colors(
                containerColor        = Color(0x22EF4444),
                contentColor          = Color(0xFFEF4444),
                focusedContainerColor = Color(0xFFEF4444),
                focusedContentColor   = Color.White,
            ),
            modifier = Modifier
                .fillMaxWidth()
                .onPreviewKeyEvent { event ->
                    if (event.key == Key.DirectionLeft && event.type == KeyEventType.KeyDown) {
                        onDpadLeft(); true
                    } else false
                }
                .focusGlow(),
        ) {
            Text("🗑  Remove Channel", style = MaterialTheme.typography.titleSmall)
        }

        Spacer(Modifier.height(8.dp))

        Text(
            text  = "D-pad Left to return to list",
            style = MaterialTheme.typography.labelSmall,
            color = WatchDawgColors.TextTertiary,
        )
    }
}

// ── Sidebar overlay ───────────────────────────────────────────────────────────

@Composable
private fun LiveTvSidebar(
    groups: List<String>,
    selectedGroup: String?,
    sidebarFirstFocus: FocusRequester,
    hasFavorites: Boolean,
    onSelectAll: () -> Unit,
    onSelectGroup: (String) -> Unit,
    onDismiss: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Box(
        modifier = modifier
            .fillMaxSize()
            .background(Color.Black.copy(alpha = 0.6f)),
    ) {
        Column(
            modifier = Modifier
                .fillMaxHeight()
                .width(280.dp)
                .background(WatchDawgColors.Surface)
                .padding(16.dp),
        ) {
            Text(
                text  = "Filter by Group",
                style = MaterialTheme.typography.titleMedium,
                color = WatchDawgColors.TextPrimary,
            )
            Spacer(Modifier.height(16.dp))

            LazyColumn {
                // "All" pill
                item(key = "all") {
                    LiveTvSidebarPill(
                        label      = "All Groups",
                        isSelected = selectedGroup == null,
                        modifier   = Modifier.focusRequester(sidebarFirstFocus),
                        onClick    = onSelectAll,
                    )
                }

                // Favorites pseudo-group
                if (hasFavorites) {
                    item(key = "fav") {
                        LiveTvSidebarPill(
                            label      = "⭐  Favorites",
                            isSelected = false,
                            onClick    = { onSelectGroup("⭐  Favorites") },
                        )
                    }
                }

                items(groups, key = { it }) { group ->
                    LiveTvSidebarPill(
                        label      = group,
                        isSelected = selectedGroup == group,
                        onClick    = { onSelectGroup(group) },
                    )
                }
            }
        }
    }
}

@Composable
private fun LiveTvSidebarPill(
    label: String,
    isSelected: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Button(
        onClick  = onClick,
        colors   = ButtonDefaults.colors(
            containerColor        = if (isSelected) WatchDawgColors.OrangeDim else Color.Transparent,
            contentColor          = if (isSelected) WatchDawgColors.Orange else WatchDawgColors.TextSecondary,
            focusedContainerColor = WatchDawgColors.OrangeDim,
            focusedContentColor   = WatchDawgColors.Orange,
        ),
        modifier = modifier
            .fillMaxWidth()
            .padding(vertical = 2.dp)
            .focusGlow(),
    ) {
        Text(
            text     = label,
            style    = MaterialTheme.typography.bodyMedium,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier.fillMaxWidth(),
        )
    }
}

// ── Delete confirm overlay ────────────────────────────────────────────────────

@Composable
private fun LiveTvDeleteConfirm(
    channel: LiveTvChannelDto,
    isActioning: Boolean,
    onDelete: () -> Unit,
    onDismiss: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val confirmFocus = remember { FocusRequester() }

    LaunchedEffect(Unit) {
        delay(100)
        try { confirmFocus.requestFocus() } catch (_: Exception) {}
    }

    Box(
        modifier         = modifier
            .fillMaxSize()
            .background(Color.Black.copy(alpha = 0.75f)),
        contentAlignment = Alignment.Center,
    ) {
        Column(
            modifier            = Modifier
                .width(360.dp)
                .background(WatchDawgColors.Surface, RoundedCornerShape(16.dp))
                .padding(24.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text(
                text  = "Remove Channel?",
                style = MaterialTheme.typography.titleLarge,
                color = WatchDawgColors.TextPrimary,
            )
            Text(
                text      = "\"${channel.name}\" will be removed from Live TV.",
                style     = MaterialTheme.typography.bodyMedium,
                color     = WatchDawgColors.TextTertiary,
                textAlign = TextAlign.Center,
            )

            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                Button(
                    onClick  = onDismiss,
                    enabled  = !isActioning,
                    colors   = ButtonDefaults.colors(
                        containerColor        = WatchDawgColors.OrangeDim,
                        contentColor          = WatchDawgColors.Orange,
                        focusedContainerColor = WatchDawgColors.Orange,
                        focusedContentColor   = WatchDawgColors.Background,
                    ),
                    modifier = Modifier.focusGlow(),
                ) {
                    Text("Cancel", style = MaterialTheme.typography.titleSmall)
                }

                Button(
                    onClick  = onDelete,
                    enabled  = !isActioning,
                    colors   = ButtonDefaults.colors(
                        containerColor        = Color(0x22EF4444),
                        contentColor          = Color(0xFFEF4444),
                        focusedContainerColor = Color(0xFFEF4444),
                        focusedContentColor   = Color.White,
                    ),
                    modifier = Modifier
                        .focusRequester(confirmFocus)
                        .focusGlow(),
                ) {
                    Text(
                        text  = if (isActioning) "Removing…" else "Remove",
                        style = MaterialTheme.typography.titleSmall,
                    )
                }
            }
        }
    }
}

// ── Loading / error / empty states ───────────────────────────────────────────

@Composable
private fun LiveTvLoadingScreen() {
    Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        Text(
            text  = "Loading channels…",
            style = MaterialTheme.typography.bodyLarge,
            color = WatchDawgColors.TextTertiary,
        )
    }
}

@Composable
private fun LiveTvErrorScreen(message: String, onRetry: () -> Unit) {
    Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
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
                onClick  = onRetry,
                colors   = ButtonDefaults.colors(
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
    Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
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
