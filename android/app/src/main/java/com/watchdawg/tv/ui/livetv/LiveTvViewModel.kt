package com.watchdawg.tv.ui.livetv

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.watchdawg.tv.data.api.LiveTvChannelDto
import com.watchdawg.tv.data.repo.WatchDawgRepository
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch

/**
 * ViewModel for LiveTvScreen — two-panel revision (Session 35).
 *
 * Bug fixes — Session 37:
 *   1. confirmDelete() now calls _detailPanelActive.value = false on success
 *      so LaunchedEffect(detailPanelActive) in LiveTvScreen fires and returns
 *      focus to the left panel. Without this D-pad Right was unreachable after
 *      deleting a channel.
 *   2. groupChannels() now sorts groups by sort_order (set via the backend
 *      Group Order drag-and-drop panel) instead of alphabetically. Alphabetical
 *      is kept as a tiebreaker within groups that share the same sort_order.
 *      "Other" is still pinned last.
 *   3. Favorites group is now collapsible — it participates in collapsedGroups
 *      the same as every other group. The FAV_GROUP_NAME constant is used as
 *      the key so it matches what LiveTvScreen passes to toggleGroupCollapsed.
 *
 * Architecture:
 *   Left panel  — vertical scrollable list of groups + channels.
 *                 Groups are collapsible. D-pad Up/Down to navigate.
 *                 Select on a channel tunes in immediately.
 *                 D-pad Right moves to the detail panel.
 *
 *   Right panel — shows the currently selected channel with three
 *                 actions: Watch, Favorite/Unfavorite, Remove.
 *                 D-pad Left returns to the list.
 *
 * State:
 *   [collapsedGroups]    — set of group names the user has collapsed.
 *                          Includes FAV_GROUP_NAME when Favorites is collapsed.
 *   [selectedChannel]    — channel whose detail is shown on the right.
 *   [detailPanelActive]  — true when focus is on the right detail panel.
 *   [lastTunedChannelId] — id of the channel most recently tuned in.
 *
 * Dead channel filter:
 *   Channels with isOnline == false are excluded from the list.
 *   Never-probed channels (isOnline == null) are always shown.
 *
 * Hoisted at WatchDawgRoot so state survives Back → re-enter.
 */
class LiveTvViewModel(private val repo: WatchDawgRepository) : ViewModel() {

    companion object {
        /** Key used for the Favorites synthetic group in collapsedGroups. */
        const val FAV_GROUP_NAME = "⭐  Favorites"
    }

    // ── State ─────────────────────────────────────────────────────────────────

    sealed class LiveTvState {
        object Loading : LiveTvState()
        data class Ready(
            /** All visible channels (confirmed-offline excluded). */
            val channels: List<LiveTvChannelDto>,
            /** Favorited channels — shown in synthetic top group. */
            val favorites: List<LiveTvChannelDto>,
            /** Groups in sort_order then alpha order. "Other" pinned last. */
            val grouped: Map<String, List<LiveTvChannelDto>>,
            val groups: List<String>,
            val hiddenOfflineCount: Int,
        ) : LiveTvState()
        data class Error(val message: String) : LiveTvState()
        object Empty : LiveTvState()
    }

    private val _state = MutableStateFlow<LiveTvState>(LiveTvState.Loading)
    val state: StateFlow<LiveTvState> = _state

    // ── Collapsed groups ──────────────────────────────────────────────────────
    // Favorites uses FAV_GROUP_NAME as its key so the Screen can collapse it
    // with the same toggleGroupCollapsed() call as any other group.

    private val _collapsedGroups = MutableStateFlow<Set<String>>(emptySet())
    val collapsedGroups: StateFlow<Set<String>> = _collapsedGroups

    fun toggleGroupCollapsed(groupName: String) {
        val current = _collapsedGroups.value
        _collapsedGroups.value = if (groupName in current) current - groupName
                                 else                      current + groupName
    }

    // ── Selected channel (drives right detail panel) ──────────────────────────

    private val _selectedChannel = MutableStateFlow<LiveTvChannelDto?>(null)
    val selectedChannel: StateFlow<LiveTvChannelDto?> = _selectedChannel

    fun selectChannel(channel: LiveTvChannelDto) {
        _selectedChannel.value = channel
    }

    // ── Detail panel focus ────────────────────────────────────────────────────

    private val _detailPanelActive = MutableStateFlow(false)
    val detailPanelActive: StateFlow<Boolean> = _detailPanelActive

    fun openDetailPanel()  { _detailPanelActive.value = true  }
    fun closeDetailPanel() { _detailPanelActive.value = false }

    // ── Sidebar (group filter) ────────────────────────────────────────────────

    private val _sidebarOpen = MutableStateFlow(false)
    val sidebarOpen: StateFlow<Boolean> = _sidebarOpen

    fun openSidebar()  { _sidebarOpen.value = true  }
    fun closeSidebar() { _sidebarOpen.value = false }

    private val _selectedGroup = MutableStateFlow<String?>(null)
    val selectedGroup: StateFlow<String?> = _selectedGroup

    fun selectGroup(group: String?) {
        _selectedGroup.value = group
        _sidebarOpen.value = false
    }

    // ── Delete confirm ────────────────────────────────────────────────────────

    private val _pendingDelete = MutableStateFlow<LiveTvChannelDto?>(null)
    val pendingDelete: StateFlow<LiveTvChannelDto?> = _pendingDelete

    private val _isActioning = MutableStateFlow(false)
    val isActioning: StateFlow<Boolean> = _isActioning

    fun requestDelete(channel: LiveTvChannelDto) { _pendingDelete.value = channel }
    fun cancelDelete()                           { _pendingDelete.value = null    }

    // ── Last tuned channel — drives focus restore on Back from player ─────────

    private val _lastTunedChannelId = MutableStateFlow<Int?>(null)
    val lastTunedChannelId: StateFlow<Int?> = _lastTunedChannelId

    fun recordTunedIn(channelId: Int) {
        _lastTunedChannelId.value = channelId
    }

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

                    val visible   = channels.filter { it.isOnline != false }
                    val hidden    = channels.size - visible.size
                    val favorites = visible.filter { it.isFavorite == true }
                    val grouped   = groupChannels(visible)
                    val groups    = buildGroupList(grouped)

                    _state.value = LiveTvState.Ready(
                        channels           = visible,
                        favorites          = favorites,
                        grouped            = grouped,
                        groups             = groups,
                        hiddenOfflineCount = hidden,
                    )
                }
                .onFailure { err ->
                    _state.value = LiveTvState.Error(err.message ?: "Failed to load channels")
                }
        }
    }

    // ── Favorite toggle ───────────────────────────────────────────────────────

    fun toggleFavorite(channel: LiveTvChannelDto) {
        viewModelScope.launch {
            repo.toggleLiveChannelFavorite(channel.id)
                .onSuccess {
                    // Close the detail panel so detailPanelActive transitions
                    // true → false, triggering LaunchedEffect(detailPanelActive)
                    // in LiveTvScreen to return focus to the left panel list.
                    // The channel still exists so _selectedChannel is kept —
                    // focus restore will land back on the same row.
                    _detailPanelActive.value = false
                    load()
                }
                .onFailure { /* silent — user can retry */ }
        }
    }

    // ── Delete ────────────────────────────────────────────────────────────────

    fun confirmDelete() {
        val channel = _pendingDelete.value ?: return
        viewModelScope.launch {
            _isActioning.value = true
            repo.deleteLiveChannel(channel.id)
                .onSuccess {
                    _pendingDelete.value = null
                    _selectedChannel.value = null
                    if (_lastTunedChannelId.value == channel.id) {
                        _lastTunedChannelId.value = null
                    }
                    // Close the detail panel so detailPanelActive transitions
                    // true → false, triggering LaunchedEffect(detailPanelActive)
                    // in LiveTvScreen to return focus to the left panel list.
                    _detailPanelActive.value = false
                    load()
                }
                .onFailure { _pendingDelete.value = null }
            _isActioning.value = false
        }
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    /**
     * Group channels by [groupName], then sort groups by their minimum
     * [sortOrder] value (set via the backend Group Order panel).
     *
     * Sort priority:
     *   1. sort_order ascending (lower = first) — respects backend drag order
     *   2. group name alphabetical — tiebreaker for groups at the same order
     *   3. "Other" always pinned last regardless of sort_order
     *
     * Previously this sorted alphabetically only, which ignored the backend
     * sort_order entirely.
     */
    private fun groupChannels(
        channels: List<LiveTvChannelDto>,
    ): Map<String, List<LiveTvChannelDto>> {
        val raw = channels.groupBy { ch ->
            ch.groupName?.trim()?.takeIf { it.isNotEmpty() } ?: "Other"
        }
        return raw.entries
            .sortedWith(
                compareBy(
                    // "Other" always last — sort key: 0 = normal, 1 = pinned last
                    { if (it.key == "Other") 1 else 0 },
                    // Primary: minimum sort_order across all channels in the group
                    { entry -> entry.value.minOfOrNull { it.sortOrder } ?: 999 },
                    // Secondary: alphabetical tiebreaker
                    { it.key },
                )
            )
            .associate { it.key to it.value }
    }

    private fun buildGroupList(grouped: Map<String, List<LiveTvChannelDto>>): List<String> =
        grouped.keys.toList()
}
