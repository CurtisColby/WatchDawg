package com.watchdawg.tv.ui.livetv

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.watchdawg.tv.data.api.LiveTvChannelDto
import com.watchdawg.tv.data.repo.WatchDawgRepository
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch

/**
 * ViewModel for LiveTvScreen — Milestone I (Session 34).
 *
 * Loads all live TV channels from GET /live-tv/channels and groups them
 * by [LiveTvChannelDto.groupName] for display in the channel grid.
 *
 * Group ordering:
 *   Channels are grouped alphabetically by group name. Channels with no
 *   group name (null or blank) are placed in an "Other" group at the end.
 *   The planned group names from the curated channel import are:
 *     Local OTA · News · Weather · Sports · Entertainment ·
 *     Classic Movies & TV · Documentary & Nature · Kids · Music · Other
 *
 * No watch history, no resume, no shuffle — live TV is stateless.
 * No genre pills — groupName already serves the categorisation role.
 *
 * [selectedGroup] drives the group pill bar at the top of the screen.
 *   null = "All" — show all groups in one scrollable column.
 *   non-null = show only channels from that group.
 *
 * Hoisted at WatchDawgRoot level so the channel list survives navigation
 * without reloading on every Back → re-enter cycle.
 */
class LiveTvViewModel(private val repo: WatchDawgRepository) : ViewModel() {

    // ── Channel list state ────────────────────────────────────────────────────

    sealed class LiveTvState {
        object Loading : LiveTvState()
        data class Ready(
            val channels: List<LiveTvChannelDto>,
            /** All channel list grouped: preserves order, "Other" always last. */
            val grouped: Map<String, List<LiveTvChannelDto>>,
            /** Sorted distinct group names for the pill bar. "Other" always last. */
            val groups: List<String>,
        ) : LiveTvState()
        data class Error(val message: String) : LiveTvState()
        object Empty : LiveTvState()
    }

    private val _state = MutableStateFlow<LiveTvState>(LiveTvState.Loading)
    val state: StateFlow<LiveTvState> = _state

    // ── Group pill selection ──────────────────────────────────────────────────

    private val _selectedGroup = MutableStateFlow<String?>(null)
    val selectedGroup: StateFlow<String?> = _selectedGroup

    // ── Load ──────────────────────────────────────────────────────────────────

    fun load() {
        viewModelScope.launch {
            _state.value = LiveTvState.Loading
            repo.getLiveChannels()
                .onSuccess { channels ->
                    if (channels.isEmpty()) {
                        _state.value = LiveTvState.Empty
                        return@onSuccess
                    }
                    val grouped  = groupChannels(channels)
                    val groups   = buildGroupList(grouped)
                    _state.value = LiveTvState.Ready(
                        channels = channels,
                        grouped  = grouped,
                        groups   = groups,
                    )
                }
                .onFailure { err ->
                    _state.value = LiveTvState.Error(err.message ?: "Failed to load channels")
                }
        }
    }

    fun selectGroup(group: String?) {
        _selectedGroup.value = group
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    /**
     * Group channels by their [groupName], normalising null/blank to "Other".
     * Groups are sorted alphabetically with "Other" pinned last.
     */
    private fun groupChannels(
        channels: List<LiveTvChannelDto>,
    ): Map<String, List<LiveTvChannelDto>> {
        val raw = channels.groupBy { ch ->
            ch.groupName?.trim()?.takeIf { it.isNotEmpty() } ?: "Other"
        }
        // Sort: alphabetical, "Other" always last
        return raw.entries
            .sortedWith(compareBy { if (it.key == "Other") "\uFFFF" else it.key })
            .associate { it.key to it.value }
    }

    private fun buildGroupList(grouped: Map<String, List<LiveTvChannelDto>>): List<String> =
        grouped.keys.toList()
}
