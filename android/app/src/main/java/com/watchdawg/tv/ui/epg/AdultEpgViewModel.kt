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
 * Adult EPG ViewModel — Session 43 (Milestone A-1).
 *
 * Structurally identical to EpgViewModel but scoped to epg_type="adult".
 * All API calls go to /epg/schedule?epg_type=adult and /epg/channels?epg_type=adult.
 *
 * Kept as a separate standalone ViewModel (not a subclass) so the adult EPG
 * maintains its own independent channel index and loading state. The main EPG
 * and adult EPG can both be hoisted at root level without sharing state.
 *
 * The PIN gate is enforced by AdultEpgScreen at the composable level — this
 * ViewModel has no knowledge of PIN state. load() is only called after the
 * PinPadOverlay confirms unlock.
 */
class AdultEpgViewModel : ViewModel() {

    data class UiState(
        val channels: List<EpgChannelScheduleDto> = emptyList(),
        val loading: Boolean = false,
        val error: String? = null,
        val lastRefreshedAt: String? = null,
    )

    private val _state = MutableStateFlow(UiState(loading = false))
    val state: StateFlow<UiState> = _state.asStateFlow()

    // Which channel index is currently being watched in adult EPG surf mode.
    // -1 = not in EPG surf mode.
    private var _currentChannelIndex: Int = -1
    val activeChannelIndex: Int get() = _currentChannelIndex

    // NOTE: No init { load() } — adult EPG loads only after PIN unlock.

    fun load() {
        viewModelScope.launch {
            _state.value = _state.value.copy(loading = true, error = null)
            try {
                val response = Graph.repository.getEpgSchedule(epgType = "adult", hours = 6)
                _state.value = UiState(
                    channels = response.channels,
                    loading = false,
                    lastRefreshedAt = response.generatedAt,
                )
            } catch (e: Exception) {
                _state.value = UiState(
                    loading = false,
                    error = "Failed to load Adult EPG: ${e.message}",
                )
            }
        }
    }

    /**
     * Called when the user tunes into a channel from the adult EPG grid.
     * Records which channel index is active so surfing knows where to go.
     */
    fun setActiveChannel(channelId: Int) {
        val channels = _state.value.channels
        _currentChannelIndex = channels.indexOfFirst { it.channelId == channelId }
    }

    /**
     * Returns the current-airing slot for the channel [offset] positions
     * away from the currently active channel. Used for channel surfing.
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
