package com.watchdawg.tv.ui.epg

import androidx.activity.compose.BackHandler
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutVertically
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import com.watchdawg.tv.data.api.EpgChannelScheduleDto
import com.watchdawg.tv.data.api.EpgSlotDto
import com.watchdawg.tv.ui.theme.WatchDawgColors
import com.watchdawg.tv.ui.theme.focusGlow
import kotlinx.coroutines.delay

/**
 * EPG Screen — Session 40.
 *
 * Classic TV Guide layout:
 *   ┌──────────────┬──────────────────────────────────────────────────────┐
 *   │  CH 101      │  [We Are Zombies ████████░░░] [Dracula III]  [...]   │
 *   │  Horror      │                                                       │
 *   ├──────────────┼──────────────────────────────────────────────────────┤
 *   │  CH 102      │  [Die Hard ████████████░░░░] [Speed]  [...]          │
 *   │  Action      │                                                       │
 *   └──────────────┴──────────────────────────────────────────────────────┘
 *
 * Navigation:
 *   D-pad Up/Down  — move between channel rows
 *   D-pad Left/Right — move between time slots in the focused row
 *   OK / Enter     — tune in to the focused slot (plays from current offset)
 *   Back           — return to Home
 *
 * Session 40 — Scroll fix:
 *   The outer LazyColumn never received D-pad Up/Down scroll because focus was
 *   captured by the inner LazyRow items. Fix: track focusedRowIndex at the grid
 *   level. Each EpgChannelRow reports its focus via onRowFocused callback. A
 *   LaunchedEffect(focusedRowIndex) calls listState.animateScrollToItem() to keep
 *   the focused row visible. Both the channel-name LazyColumn and the slot
 *   LazyColumn share the same listState so they scroll in lockstep (unchanged).
 *
 * When a slot is selected, the ViewModel records the active channel index
 * so PlayerScreen channel surfing knows where to start.
 */
@Composable
fun EpgScreen(
    viewModel: EpgViewModel,
    onPlaySlot: (slot: EpgSlotDto, channelId: Int, offsetSeconds: Long) -> Unit,
    onPlayById: (videoId: Int, hlsMode: Boolean, offsetSeconds: Long) -> Unit,
    onBack: () -> Unit,
) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    val firstRowFocus = remember { FocusRequester() }

    BackHandler { onBack() }

    LaunchedEffect(Unit) {
        delay(80)
        try { firstRowFocus.requestFocus() } catch (_: Exception) {}
    }

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(WatchDawgColors.Background)
    ) {
        Column(modifier = Modifier.fillMaxSize()) {

            // ── Header ────────────────────────────────────────────────────────
            EpgHeader(
                channelCount = state.channels.size,
                onRefresh = { viewModel.load() },
            )

            when {
                state.loading -> {
                    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                        Text(
                            "Loading EPG…",
                            style = MaterialTheme.typography.titleMedium,
                            color = WatchDawgColors.TextSecondary,
                        )
                    }
                }
                state.error != null -> {
                    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                        Column(horizontalAlignment = Alignment.CenterHorizontally) {
                            Text("⚠", fontSize = 48.sp, color = WatchDawgColors.Orange)
                            Spacer(Modifier.height(12.dp))
                            Text(
                                state.error ?: "Unknown error",
                                style = MaterialTheme.typography.bodyLarge,
                                color = WatchDawgColors.TextSecondary,
                            )
                        }
                    }
                }
                state.channels.isEmpty() -> {
                    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                        Column(horizontalAlignment = Alignment.CenterHorizontally) {
                            Text("📺", fontSize = 48.sp, color = WatchDawgColors.TextTertiary)
                            Spacer(Modifier.height(12.dp))
                            Text(
                                "No EPG channels found.",
                                style = MaterialTheme.typography.titleMedium,
                                color = WatchDawgColors.TextSecondary,
                            )
                            Spacer(Modifier.height(8.dp))
                            Text(
                                "Add EPG channels in the web UI and rebuild schedules.",
                                style = MaterialTheme.typography.bodyMedium,
                                color = WatchDawgColors.TextTertiary,
                            )
                        }
                    }
                }
                else -> {
                    EpgGuideGrid(
                        channels       = state.channels,
                        viewModel      = viewModel,
                        firstRowFocus  = firstRowFocus,
                        onPlaySlot     = { slot, channelId, offsetSeconds ->
                            // Session 40: WatchDawg slots have a videoId — resolve
                            // fresh in HLS mode (no popup, always HLS for EPG).
                            // Store slot startTime so PlayerViewModel can recompute
                            // the exact offset after yt-dlp finishes resolving,
                            // correcting for the resolution delay.
                            // All other source types (Plex, IPTV) play direct URL.
                            if (slot.videoId != null) {
                                com.watchdawg.tv.data.prefs.QueueHolder.epgSlotStartTimeUtc = slot.startTime
                                onPlayById(slot.videoId, true, offsetSeconds)
                            } else {
                                onPlaySlot(slot, channelId, offsetSeconds)
                            }
                        },
                    )
                }
            }
        }
    }
}

// ── Header ────────────────────────────────────────────────────────────────────

@Composable
private fun EpgHeader(
    channelCount: Int,
    onRefresh: () -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .background(WatchDawgColors.Surface)
            .padding(horizontal = 24.dp, vertical = 14.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                "📺",
                fontSize = 22.sp,
            )
            Spacer(Modifier.width(10.dp))
            Text(
                "EPG Guide",
                style = MaterialTheme.typography.titleLarge,
                color = WatchDawgColors.TextPrimary,
            )
            if (channelCount > 0) {
                Spacer(Modifier.width(12.dp))
                Text(
                    "$channelCount channels",
                    style = MaterialTheme.typography.bodySmall,
                    color = WatchDawgColors.TextTertiary,
                )
            }
        }
        Text(
            "↑↓ = channels  •  ← → = time slots  •  OK = tune in",
            style = MaterialTheme.typography.labelSmall,
            color = WatchDawgColors.TextTertiary,
        )
    }
}

// ── Main guide grid ───────────────────────────────────────────────────────────

@Composable
private fun EpgGuideGrid(
    channels: List<EpgChannelScheduleDto>,
    viewModel: EpgViewModel,
    firstRowFocus: FocusRequester,
    onPlaySlot: (slot: EpgSlotDto, channelId: Int, offsetSeconds: Long) -> Unit,
) {
    val channelColumnWidth = 130.dp
    val rowHeight          = 76.dp
    val slotMinWidth       = 200.dp
    val listState          = rememberLazyListState()

    // Session 40 — Scroll fix:
    // Track which channel row currently has D-pad focus. Each EpgChannelRow
    // reports focus changes via onRowFocused. When focusedRowIndex changes,
    // we animate the shared listState to keep the focused row in view.
    // scrollOffset = 0 means the item is at the very top of the viewport;
    // using scrollOffset keeps focused rows near the top for comfortable viewing.
    var focusedRowIndex by remember { mutableIntStateOf(0) }

    LaunchedEffect(focusedRowIndex) {
        // animateScrollToItem is safe to call even if the index is already visible.
        // scrollOffset = 0 keeps the focused row at the top of the visible area,
        // which matches standard TV guide scroll behaviour.
        listState.animateScrollToItem(focusedRowIndex)
    }

    Row(modifier = Modifier.fillMaxSize()) {

        // ── Channel name column (fixed left) ──────────────────────────────────
        LazyColumn(
            state            = listState,
            modifier         = Modifier
                .width(channelColumnWidth)
                .fillMaxHeight()
                .background(WatchDawgColors.SurfaceElevated),
            contentPadding   = PaddingValues(vertical = 0.dp),
            userScrollEnabled = false, // synced with slot rows via shared listState
        ) {
            itemsIndexed(channels) { _, channel ->
                ChannelNameCell(
                    channelNumber = channel.channelNumber,
                    channelName   = channel.channelName,
                    channelLogo   = channel.channelLogo,
                    rowHeight     = rowHeight,
                )
            }
        }

        // ── Time slot rows (scrollable horizontally per row) ──────────────────
        // userScrollEnabled is false here too — scrolling is driven programmatically
        // by the LaunchedEffect above when focusedRowIndex changes, which keeps both
        // LazyColumns in lockstep without requiring the user to scroll manually.
        LazyColumn(
            state          = listState,
            modifier       = Modifier
                .weight(1f)
                .fillMaxHeight(),
            contentPadding = PaddingValues(vertical = 0.dp),
            userScrollEnabled = false,
        ) {
            itemsIndexed(channels) { rowIndex, channel ->
                val rowFocusRequester = if (rowIndex == 0) firstRowFocus else remember { FocusRequester() }
                EpgChannelRow(
                    channel           = channel,
                    rowHeight         = rowHeight,
                    slotMinWidth      = slotMinWidth,
                    rowFocusRequester = rowFocusRequester,
                    onRowFocused      = { focusedRowIndex = rowIndex },
                    onPlaySlot        = { slot ->
                        viewModel.setActiveChannel(channel.channelId)
                        val offset = viewModel.getCurrentSlotOffsetSeconds(slot)
                        onPlaySlot(slot, channel.channelId, offset)
                    },
                )
                // Separator between channel rows
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(1.dp)
                        .background(Color(0x22FFFFFF)),
                )
            }
        }
    }
}

// ── Channel name cell (left column) ──────────────────────────────────────────

@Composable
private fun ChannelNameCell(
    channelNumber: Int,
    channelName: String,
    channelLogo: String?,
    rowHeight: androidx.compose.ui.unit.Dp,
) {
    Box(
        modifier = Modifier
            .width(130.dp)
            .height(rowHeight)
            .background(WatchDawgColors.SurfaceElevated)
            .padding(horizontal = 12.dp, vertical = 8.dp),
        contentAlignment = Alignment.CenterStart,
    ) {
        Column {
            Text(
                "CH $channelNumber",
                style = MaterialTheme.typography.labelSmall,
                color = WatchDawgColors.Orange,
                maxLines = 1,
            )
            Spacer(Modifier.height(2.dp))
            Text(
                channelName,
                style = MaterialTheme.typography.bodySmall,
                color = WatchDawgColors.TextPrimary,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
            )
        }
    }
}

// ── Single channel row (horizontal slot strip) ────────────────────────────────

@Composable
private fun EpgChannelRow(
    channel: EpgChannelScheduleDto,
    rowHeight: androidx.compose.ui.unit.Dp,
    slotMinWidth: androidx.compose.ui.unit.Dp,
    rowFocusRequester: FocusRequester,
    // Session 40: callback fires when any slot in this row gains focus, so the
    // parent grid can scroll the outer LazyColumn to keep this row visible.
    onRowFocused: () -> Unit,
    onPlaySlot: (EpgSlotDto) -> Unit,
) {
    if (channel.slots.isEmpty()) {
        // Empty row placeholder
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .height(rowHeight)
                .padding(horizontal = 8.dp),
            contentAlignment = Alignment.CenterStart,
        ) {
            Text(
                if (channel.isLive) "Live — tune in anytime"
                else "No schedule available — check back later",
                style = MaterialTheme.typography.bodySmall,
                color = WatchDawgColors.TextTertiary,
            )
        }
        return
    }

    var focusedSlotIndex by remember { mutableIntStateOf(-1) }
    val slotListState = rememberLazyListState()

    LazyRow(
        state          = slotListState,
        modifier       = Modifier
            .fillMaxWidth()
            .height(rowHeight)
            .focusRequester(rowFocusRequester),
        contentPadding = PaddingValues(horizontal = 6.dp, vertical = 6.dp),
        horizontalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        itemsIndexed(channel.slots) { slotIndex, slot ->
            val isFocused = focusedSlotIndex == slotIndex
            // Calculate approximate slot width proportional to duration
            // Base: 200.dp for 90 minutes, scale linearly
            val durationMinutes = (slot.durationSeconds / 60).coerceAtLeast(10)
            val slotWidth = (slotMinWidth.value * (durationMinutes / 90f))
                .coerceAtLeast(slotMinWidth.value)
                .dp

            EpgSlotCell(
                slot      = slot,
                width     = slotWidth,
                height    = rowHeight - 12.dp,
                isFocused = isFocused,
                onFocus   = {
                    focusedSlotIndex = slotIndex
                    // Notify parent grid that this row has focus so it can scroll
                    onRowFocused()
                },
                onClick   = { onPlaySlot(slot) },
            )
        }
    }
}

// ── Single time slot cell ─────────────────────────────────────────────────────

@Composable
private fun EpgSlotCell(
    slot: EpgSlotDto,
    width: androidx.compose.ui.unit.Dp,
    height: androidx.compose.ui.unit.Dp,
    isFocused: Boolean,
    onFocus: () -> Unit,
    onClick: () -> Unit,
) {
    val isAiring = (slot.progressSeconds ?: 0) > 0
    val progress = if (slot.durationSeconds > 0 && isAiring)
        (slot.progressSeconds ?: 0).toFloat() / slot.durationSeconds.toFloat()
    else 0f

    val bgColor = when {
        isFocused -> WatchDawgColors.OrangeDim
        isAiring  -> WatchDawgColors.Surface
        else      -> WatchDawgColors.Background
    }

    androidx.tv.material3.Card(
        onClick = onClick,
        modifier = Modifier
            .width(width)
            .height(height)
            .onFocusChanged { if (it.isFocused) onFocus() }
            .focusGlow(),
        colors = androidx.tv.material3.CardDefaults.colors(
            containerColor        = bgColor,
            focusedContainerColor = WatchDawgColors.OrangeDim,
        ),
        shape = androidx.tv.material3.CardDefaults.shape(
            shape        = RoundedCornerShape(6.dp),
            focusedShape = RoundedCornerShape(6.dp),
        ),
    ) {
        Box(modifier = Modifier.fillMaxSize()) {
            Column(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(horizontal = 10.dp, vertical = 6.dp),
                verticalArrangement = Arrangement.Center,
            ) {
                Text(
                    text     = slot.title,
                    style    = MaterialTheme.typography.bodySmall,
                    color    = if (isFocused) WatchDawgColors.Orange else WatchDawgColors.TextPrimary,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                if (!slot.subtitle.isNullOrBlank()) {
                    Text(
                        text     = slot.subtitle,
                        style    = MaterialTheme.typography.labelSmall,
                        color    = WatchDawgColors.TextTertiary,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
            }

            if (isAiring && progress > 0f) {
                Box(
                    modifier = Modifier
                        .align(Alignment.BottomStart)
                        .fillMaxWidth(progress)
                        .height(3.dp)
                        .background(
                            Brush.horizontalGradient(
                                listOf(WatchDawgColors.Orange, WatchDawgColors.OrangeDim)
                            )
                        ),
                )
            }

            if (isAiring) {
                Box(
                    modifier = Modifier
                        .align(Alignment.TopEnd)
                        .padding(4.dp)
                        .background(WatchDawgColors.Orange, RoundedCornerShape(4.dp))
                        .padding(horizontal = 4.dp, vertical = 1.dp),
                ) {
                    Text("NOW", fontSize = 8.sp, color = Color.Black)
                }
            }
        }
    }
}

// ── Channel surf banner (shown in PlayerScreen during surfing) ────────────────

/**
 * Overlay banner displayed briefly when the user surfs to a new channel.
 * Shows channel number, name, and currently airing title.
 * Auto-dismisses after 2 seconds.
 */
@Composable
fun EpgChannelBanner(
    channelNumber: Int,
    channelName: String,
    title: String,
    subtitle: String?,
    visible: Boolean,
) {
    AnimatedVisibility(
        visible = visible,
        enter   = slideInVertically(initialOffsetY = { it }) + fadeIn(),
        exit    = slideOutVertically(targetOffsetY = { it }) + fadeOut(),
        modifier = Modifier.fillMaxSize(),
    ) {
        Box(
            modifier = Modifier.fillMaxSize(),
            contentAlignment = Alignment.BottomStart,
        ) {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .background(
                        Brush.verticalGradient(
                            0f to Color.Transparent,
                            0.3f to Color(0xDD000000),
                            1f to Color(0xFF000000),
                        )
                    )
                    .padding(start = 48.dp, end = 48.dp, bottom = 36.dp, top = 40.dp),
            ) {
                Text(
                    "CH $channelNumber  •  $channelName",
                    style = MaterialTheme.typography.labelLarge,
                    color = WatchDawgColors.Orange,
                )
                Spacer(Modifier.height(4.dp))
                Text(
                    title,
                    style = MaterialTheme.typography.titleLarge,
                    color = Color.White,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                if (!subtitle.isNullOrBlank()) {
                    Text(
                        subtitle,
                        style = MaterialTheme.typography.bodyMedium,
                        color = WatchDawgColors.TextSecondary,
                        maxLines = 1,
                    )
                }
            }
        }
    }
}
