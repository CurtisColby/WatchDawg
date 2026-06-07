package com.watchdawg.tv.ui.epg

import androidx.activity.compose.BackHandler
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
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
import androidx.compose.runtime.mutableStateOf
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
import com.watchdawg.tv.Graph
import com.watchdawg.tv.data.api.EpgChannelScheduleDto
import com.watchdawg.tv.data.api.EpgSlotDto
import com.watchdawg.tv.ui.auth.PinPadOverlay
import com.watchdawg.tv.ui.auth.PinViewModel
import com.watchdawg.tv.ui.theme.WatchDawgColors
import com.watchdawg.tv.ui.theme.focusGlow
import com.watchdawg.tv.ui.theme.focusGlowCard
import kotlinx.coroutines.delay

/**
 * Adult EPG Screen — Session 43 (Milestone A-1).
 *
 * PIN-gated version of the EPG guide screen. Shows the same guide grid as
 * the main EpgScreen but fetches from epg_type="adult" — only adult EPG
 * channels appear here.
 *
 * Gate behaviour:
 *   - Shows PinPadOverlay on first entry.
 *   - On successful PIN unlock: dismisses overlay, loads adult schedule, shows grid.
 *   - On Back from the PIN pad (no unlock): navigates back to Home.
 *   - PIN state is shared via TokenHolder — if already unlocked when entering
 *     (e.g. user navigated here from Adult screen in the same session), the
 *     PinViewModel's isUnlocked state reflects that and we skip the pad.
 *
 * No left channel column — same layout as the main EPG (Session 41 redesign).
 * CH number badges are shown on each slot cell.
 *
 * Source types supported:
 *   - plex_movie / plex_tv  — Plex adult libraries
 *   - watchdawg             — locked WatchDawg scraped sources (Vimeo etc.)
 *   - local_private         — files from /watchdawg/Private/{folder}
 */
@Composable
fun AdultEpgScreen(
    viewModel:    AdultEpgViewModel,
    pinViewModel: PinViewModel,
    onPlaySlot:   (slot: EpgSlotDto, channelId: Int, offsetSeconds: Long) -> Unit,
    onPlayById:   (videoId: Int, hlsMode: Boolean, offsetSeconds: Long) -> Unit,
    onBack:       () -> Unit,
) {
    val pinState    by pinViewModel.state.collectAsStateWithLifecycle()
    val epgState    by viewModel.state.collectAsStateWithLifecycle()
    val firstRowFocus = remember { FocusRequester() }

    // Show PIN pad until unlocked. If already unlocked (TokenHolder has a token)
    // the PinViewModel reflects that on first composition.
    var pinUnlocked by remember { mutableStateOf(pinState.isUnlocked) }

    // Sync with pinState — in case TokenHolder was already unlocked this session
    LaunchedEffect(pinState.isUnlocked) {
        if (pinState.isUnlocked && !pinUnlocked) {
            pinUnlocked = true
        }
    }

    BackHandler { onBack() }

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(WatchDawgColors.Background)
    ) {
        if (!pinUnlocked) {
            // ── PIN gate ──────────────────────────────────────────────────────
            PinPadOverlay(
                viewModel = pinViewModel,
                onDismiss = { wasUnlocked ->
                    if (wasUnlocked) {
                        pinUnlocked = true
                    } else {
                        // User backed out of PIN without unlocking — go back
                        onBack()
                    }
                },
            )
        } else {
            // ── Adult EPG guide ───────────────────────────────────────────────
            LaunchedEffect(Unit) {
                val channels    = viewModel.state.value.channels
                val lastRefresh = viewModel.state.value.lastRefreshedAt
                val stale       = channels.isEmpty() || lastRefresh == null
                if (stale) viewModel.load()
                delay(80)
                try { firstRowFocus.requestFocus() } catch (_: Exception) {}
            }

            Column(modifier = Modifier.fillMaxSize()) {

                // ── Header ────────────────────────────────────────────────────
                AdultEpgHeader(
                    channelCount = epgState.channels.size,
                    onRefresh    = { viewModel.load() },
                )

                when {
                    epgState.loading -> {
                        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                            Text(
                                "Loading Adult EPG…",
                                style = MaterialTheme.typography.titleMedium,
                                color = WatchDawgColors.TextSecondary,
                            )
                        }
                    }
                    epgState.error != null -> {
                        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                                Text("⚠", fontSize = 48.sp, color = WatchDawgColors.Orange)
                                Spacer(Modifier.height(12.dp))
                                Text(
                                    epgState.error ?: "Unknown error",
                                    style = MaterialTheme.typography.bodyLarge,
                                    color = WatchDawgColors.TextSecondary,
                                )
                            }
                        }
                    }
                    epgState.channels.isEmpty() -> {
                        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                                Text("🔞", fontSize = 48.sp, color = WatchDawgColors.TextTertiary)
                                Spacer(Modifier.height(12.dp))
                                Text(
                                    "No Adult EPG channels found.",
                                    style = MaterialTheme.typography.titleMedium,
                                    color = WatchDawgColors.TextSecondary,
                                )
                                Spacer(Modifier.height(8.dp))
                                Text(
                                    "Add Adult EPG channels in the web UI (EPG type = Adult) and rebuild schedules.",
                                    style = MaterialTheme.typography.bodyMedium,
                                    color = WatchDawgColors.TextTertiary,
                                )
                            }
                        }
                    }
                    else -> {
                        AdultEpgGuideGrid(
                            channels          = epgState.channels,
                            viewModel         = viewModel,
                            firstRowFocus     = firstRowFocus,
                            initialFocusedRow = viewModel.activeChannelIndex.coerceAtLeast(0),
                            onPlaySlot        = { slot, channelId, offsetSeconds ->
                                // Same routing as main EPG:
                                // WatchDawg slots have videoId — resolve via player route.
                                // All other types (Plex, local_private) have a real stream_url.
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
}

// ── Header ────────────────────────────────────────────────────────────────────

@Composable
private fun AdultEpgHeader(
    channelCount: Int,
    onRefresh:    () -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .background(WatchDawgColors.Surface)
            .padding(horizontal = 24.dp, vertical = 14.dp),
        verticalAlignment         = Alignment.CenterVertically,
        horizontalArrangement     = Arrangement.SpaceBetween,
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("🔞", fontSize = 22.sp)
            Spacer(Modifier.width(10.dp))
            Text(
                "Adult EPG Guide",
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
private fun AdultEpgGuideGrid(
    channels:          List<EpgChannelScheduleDto>,
    viewModel:         AdultEpgViewModel,
    firstRowFocus:     FocusRequester,
    initialFocusedRow: Int = 0,
    onPlaySlot:        (slot: EpgSlotDto, channelId: Int, offsetSeconds: Long) -> Unit,
) {
    val rowHeight    = 76.dp
    val slotMinWidth = 200.dp
    val listState    = rememberLazyListState()

    var focusedRowIndex by remember {
        mutableIntStateOf(
            initialFocusedRow.coerceIn(0, (channels.size - 1).coerceAtLeast(0))
        )
    }

    LaunchedEffect(focusedRowIndex) {
        listState.animateScrollToItem(focusedRowIndex, scrollOffset = 0)
    }

    LazyColumn(
        state             = listState,
        modifier          = Modifier.fillMaxSize(),
        contentPadding    = PaddingValues(top = 4.dp, bottom = 0.dp),
        userScrollEnabled = false,
    ) {
        itemsIndexed(channels) { rowIndex, channel ->
            val rowFocusRequester = if (rowIndex == 0) firstRowFocus else remember { FocusRequester() }
            AdultEpgChannelRow(
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

// ── Single channel row (horizontal slot strip) ────────────────────────────────

@Composable
private fun AdultEpgChannelRow(
    channel:           EpgChannelScheduleDto,
    rowHeight:         androidx.compose.ui.unit.Dp,
    slotMinWidth:      androidx.compose.ui.unit.Dp,
    rowFocusRequester: FocusRequester,
    onRowFocused:      () -> Unit,
    onPlaySlot:        (EpgSlotDto) -> Unit,
) {
    val channelNumber = channel.channelNumber
    if (channel.slots.isEmpty()) {
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
        state             = slotListState,
        modifier          = Modifier
            .fillMaxWidth()
            .height(rowHeight)
            .focusRequester(rowFocusRequester)
            .onFocusChanged { fs -> if (!fs.hasFocus) focusedSlotIndex = -1 },
        contentPadding    = PaddingValues(horizontal = 6.dp, vertical = 6.dp),
        horizontalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        itemsIndexed(channel.slots) { slotIndex, slot ->
            val isFocused = focusedSlotIndex == slotIndex
            val durationMinutes = (slot.durationSeconds / 60).coerceAtLeast(10)
            val slotWidth = (slotMinWidth.value * (durationMinutes / 90f))
                .coerceAtLeast(slotMinWidth.value)
                .dp

            AdultEpgSlotCell(
                slot          = slot,
                width         = slotWidth,
                height        = rowHeight - 12.dp,
                isFocused     = isFocused,
                channelNumber = channelNumber,
                onFocus       = {
                    focusedSlotIndex = slotIndex
                    onRowFocused()
                },
                onClick       = { onPlaySlot(slot) },
            )
        }
    }
}

// ── Single time slot cell ─────────────────────────────────────────────────────

@Composable
private fun AdultEpgSlotCell(
    slot:          EpgSlotDto,
    width:         androidx.compose.ui.unit.Dp,
    height:        androidx.compose.ui.unit.Dp,
    isFocused:     Boolean,
    channelNumber: Int,
    onFocus:       () -> Unit,
    onClick:       () -> Unit,
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
            .onFocusChanged { fs -> if (fs.isFocused) onFocus() }
            .focusGlowCard(isFocused, glowRadius = 10.dp, alpha = 0.25f),
        colors = androidx.tv.material3.CardDefaults.colors(
            containerColor        = bgColor,
            focusedContainerColor = bgColor,
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

            // Progress bar for currently-airing slot
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

            // NOW badge for currently-airing slot
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

            // CH number badge — bottom-right of every slot pill
            Box(
                modifier = Modifier
                    .align(Alignment.BottomEnd)
                    .padding(4.dp)
                    .background(
                        color = if (isFocused) WatchDawgColors.Orange.copy(alpha = 0.25f)
                                else Color(0x44000000),
                        shape = RoundedCornerShape(3.dp),
                    )
                    .padding(horizontal = 4.dp, vertical = 1.dp),
            ) {
                Text(
                    text     = "CH $channelNumber",
                    fontSize = 7.sp,
                    color    = if (isFocused) WatchDawgColors.Orange else WatchDawgColors.TextTertiary,
                )
            }
        }
    }
}
