package com.watchdawg.tv.ui.epg

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.watchdawg.tv.Graph
import com.watchdawg.tv.data.api.EpgChannelScheduleDto
import com.watchdawg.tv.data.api.EpgSlotDto
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter

/**
 * EPG ViewModel — Session 39.
 *
 * Fetches the rolling EPG schedule from GET /epg/schedule and exposes it
 * to EpgScreen. Also manages channel surfing state for PlayerScreen:
 * when the user surfs up/down in the player, the ViewModel returns the
 * adjacent channel's current slot so PlayerScreen can navigate to it.
 *
 * State:
 *   [UiState.channels]        — ordered list of channels with their slots
 *   [UiState.loading]         — true while fetching
 *   [UiState.error]           — non-null if fetch failed
 *   [currentChannelIndex]     — which channel the user is watching in EPG mode
 *
 * Surfing:
 *   [getAdjacentSlot(+1)] — next channel (D-pad Down in player)
 *   [getAdjacentSlot(-1)] — previous channel (D-pad Up in player)
 */
class EpgViewModel : ViewModel() {

    data class UiState(
        val channels: List<EpgChannelScheduleDto> = emptyList(),
        val loading: Boolean = false,
        val error: String? = null,
        val lastRefreshedAt: String? = null,
    )

    private val _state = MutableStateFlow(UiState(loading = true))
    val state: StateFlow<UiState> = _state.asStateFlow()

    // Which channel index is currently being watched in EPG surf mode.
    // -1 = not in EPG surf mode.
    private var _currentChannelIndex: Int = -1

    init {
        load()
    }

    fun load(epgType: String = "main") {
        viewModelScope.launch {
            _state.value = _state.value.copy(loading = true, error = null)
            try {
                val response = Graph.repository.getEpgSchedule(epgType = epgType, hours = 6)
                _state.value = UiState(
                    channels = response.channels,
                    loading = false,
                    lastRefreshedAt = response.generatedAt,
                )
            } catch (e: Exception) {
                _state.value = UiState(
                    loading = false,
                    error = "Failed to load EPG: ${e.message}",
                )
            }
        }
    }

    /**
     * Called when the user tunes into a channel from the EPG grid.
     * Records which channel index is active so surfing knows where to go.
     */
    fun setActiveChannel(channelId: Int) {
        val channels = _state.value.channels
        _currentChannelIndex = channels.indexOfFirst { it.channelId == channelId }
    }

    /**
     * Returns the current-airing slot for the channel [offset] positions
     * away from the currently active channel. Used for channel surfing.
     *
     * offset = +1 → next channel (D-pad Down)
     * offset = -1 → previous channel (D-pad Up)
     *
     * Returns null if not in EPG mode, no channels, or target has no slot.
     */
    fun getAdjacentSlot(offset: Int): EpgSlotDto? {
        val channels = _state.value.channels
        if (channels.isEmpty() || _currentChannelIndex < 0) return null
        val targetIndex = (_currentChannelIndex + offset).coerceIn(0, channels.size - 1)
        if (targetIndex == _currentChannelIndex) return null
        _currentChannelIndex = targetIndex
        return getCurrentSlot(channels[targetIndex])
    }

    /**
     * Returns the channel descriptor for the channel at [offset] from current.
     * Used to show the channel banner when surfing.
     */
    fun getAdjacentChannel(offset: Int): EpgChannelScheduleDto? {
        val channels = _state.value.channels
        if (channels.isEmpty() || _currentChannelIndex < 0) return null
        val targetIndex = (_currentChannelIndex + offset).coerceIn(0, channels.size - 1)
        return channels.getOrNull(targetIndex)
    }

    /**
     * Finds the slot in [channel] that is currently airing.
     * Falls back to the first slot if none match.
     */
    fun getCurrentSlot(channel: EpgChannelScheduleDto): EpgSlotDto? {
        if (channel.slots.isEmpty()) return null
        val now = LocalDateTime.now()
        return channel.slots.firstOrNull { slot ->
            val start = parseSlotTime(slot.startTime)
            val end   = parseSlotTime(slot.endTime)
            start != null && end != null && now >= start && now < end
        } ?: channel.slots.first()
    }

    /**
     * Returns how many seconds into the current slot we are.
     * Used to start streams at the correct wall-clock offset (pseudo-linear feel).
     *
     * Session 40 fix: previously this recalculated the offset on the client using
     * LocalDateTime.now() (device local time) minus slot.startTime (UTC from backend).
     * The timezone mismatch meant the result was always negative, clamped to 0L,
     * so every stream started from the beginning.
     *
     * The backend already computes progress_seconds correctly in UTC via
     * _compute_progress() and returns it in every EpgSlotDto. We use that
     * value directly — it is always accurate and requires no timezone handling.
     */
    fun getCurrentSlotOffsetSeconds(slot: EpgSlotDto): Long {
        return (slot.progressSeconds ?: 0).toLong().coerceAtLeast(0L)
    }

    // ── Time helpers ──────────────────────────────────────────────────────────

    private fun parseSlotTime(timeStr: String?): LocalDateTime? {
        if (timeStr.isNullOrBlank()) return null
        return try {
            LocalDateTime.parse(
                timeStr.replace("T", " ").substringBefore(".") + ".000000",
                DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss.SSSSSS")
            )
        } catch (_: Exception) {
            try {
                LocalDateTime.parse(
                    timeStr.replace("T", " ").substringBefore("."),
                    DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss")
                )
            } catch (_: Exception) { null }
        }
    }

    fun formatSlotTime(timeStr: String?): String {
        val dt = parseSlotTime(timeStr) ?: return ""
        val h = dt.hour
        val m = dt.minute
        val amPm = if (h < 12) "AM" else "PM"
        val displayH = when { h == 0 -> 12; h > 12 -> h - 12; else -> h }
        return "%d:%02d %s".format(displayH, m, amPm)
    }

    fun formatDuration(seconds: Int): String {
        if (seconds <= 0) return ""
        val h = seconds / 3600
        val m = (seconds % 3600) / 60
        return when { h > 0 && m > 0 -> "${h}h ${m}m"; h > 0 -> "${h}h"; else -> "${m}m" }
    }
}
