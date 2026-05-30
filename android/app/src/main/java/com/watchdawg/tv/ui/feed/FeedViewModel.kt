package com.watchdawg.tv.ui.feed

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.watchdawg.tv.data.api.ChannelDto
import com.watchdawg.tv.data.api.VideoDto
import com.watchdawg.tv.data.auth.TokenHolder
import com.watchdawg.tv.data.prefs.DefaultChannelPrefs
import com.watchdawg.tv.data.prefs.ResumeState
import com.watchdawg.tv.data.repo.WatchDawgRepository
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

private val LOCKED_CATEGORIES = setOf("vimeo", "sexy", "adult")

class FeedViewModel(
    private val repo: WatchDawgRepository,
    private val defaultChannelPrefs: DefaultChannelPrefs,
    private val resumeState: ResumeState,
) : ViewModel() {

    data class UiState(
        val loading: Boolean = true,
        val videos: List<VideoDto> = emptyList(),
        val total: Int = 0,
        val channels: List<ChannelDto> = emptyList(),
        val selectedChannelIds: Set<Int> = emptySet(),
        val lockedChannelsHidden: Boolean = !TokenHolder.isUnlocked,
        val selectedCategory: String? = null,
        val error: String? = null,
        val queueLoading: Boolean = false,
        val pendingQueue: QueuePayload? = null,
        val scraping: Boolean = false,
        val resolving: Boolean = false,
        val actionMessage: String? = null,
        val upgradingChannelIds: Set<Int> = emptySet(),
        // Resume banner — non-null only when app is unlocked at load time
        // AND the saved video came from a public (non-locked) source.
        // Never populated in the locked/unauthenticated state.
        val pendingResume: ResumeState.Saved? = null,
    )

    data class QueuePayload(val ids: List<Int>, val startIndex: Int)

    private val _state = MutableStateFlow(UiState())
    val state: StateFlow<UiState> = _state.asStateFlow()

    init {
        val lockedDefaults = defaultChannelPrefs.getLockedDefaults()
        _state.value = _state.value.copy(selectedChannelIds = lockedDefaults)

        // Security: only load resume state if the app starts unlocked.
        // On normal cold-start the app is always locked, so this is a no-op.
        // If somehow the token survived (it's in-memory only so this can't
        // happen in practice), we load it. The banner is otherwise shown only
        // after onSessionUnlocked() fires.
        if (TokenHolder.isUnlocked) {
            loadResumeState()
        }

        refresh()
        loadChannels()
    }

    /**
     * Loads the saved resume state from SharedPreferences.
     *
     * SECURITY: Only call when TokenHolder.isUnlocked is true. ResumeState.save()
     * already refuses to write locked/adult content, but we double-gate here so
     * a cold-start in the locked state can never surface a resume banner.
     */
    private fun loadResumeState() {
        if (!TokenHolder.isUnlocked) {
            // Do not show resume banner in locked state — ever.
            _state.value = _state.value.copy(pendingResume = null)
            return
        }
        val saved = resumeState.load() ?: return
        if (saved.positionMs > 60_000L) {
            _state.value = _state.value.copy(pendingResume = saved)
        } else {
            resumeState.clear()
        }
    }

    fun clearResume() {
        resumeState.clear()
        _state.value = _state.value.copy(pendingResume = null)
    }

    fun refresh() {
        viewModelScope.launch {
            _state.value = _state.value.copy(loading = true, error = null)
            val channelIds = _state.value.selectedChannelIds
                .takeIf { it.isNotEmpty() }
                ?.joinToString(",")
            repo.getFeed(
                limit = 1000,
                offset = 0,
                channelIds = channelIds,
                status = null,
                category = _state.value.selectedCategory,
            )
                .onSuccess { resp ->
                    _state.value = _state.value.copy(
                        loading = false,
                        videos = resp.videos,
                        total = resp.total,
                        lockedChannelsHidden = resp.lockedChannelsHidden,
                        error = null,
                    )
                }
                .onFailure { e ->
                    _state.value = _state.value.copy(
                        loading = false,
                        error = e.message ?: "Could not reach the WatchDawg server.",
                    )
                }
        }
    }

    fun loadChannels() {
        viewModelScope.launch {
            repo.getChannels().onSuccess { channels ->
                _state.value = _state.value.copy(channels = channels)
            }
        }
    }

    fun setCategory(category: String?) {
        val safeCategory = if (!TokenHolder.isUnlocked && category in LOCKED_CATEGORIES) null
        else category
        _state.value = _state.value.copy(selectedCategory = safeCategory)
        refresh()
    }

    fun onSessionLocked() {
        val lockedSelection = defaultChannelPrefs.getLockedDefaults()
        val safeCategory = if (_state.value.selectedCategory in LOCKED_CATEGORIES) null
        else _state.value.selectedCategory
        _state.value = _state.value.copy(
            selectedChannelIds = lockedSelection,
            lockedChannelsHidden = true,
            selectedCategory = safeCategory,
            // Always clear resume banner on lock — never show locked content
            pendingResume = null,
        )
        refresh()
        loadChannels()
    }

    fun onSessionUnlocked() {
        val unlockedSelection = defaultChannelPrefs.getUnlockedDefaults()
        _state.value = _state.value.copy(
            selectedChannelIds = unlockedSelection,
            lockedChannelsHidden = false,
        )
        // Now that we're unlocked, it's safe to surface the resume banner.
        loadResumeState()
        refresh()
        loadChannels()
    }

    fun toggleChannel(id: Int) {
        val current = _state.value.selectedChannelIds.toMutableSet()
        if (!current.add(id)) current.remove(id)
        _state.value = _state.value.copy(selectedChannelIds = current)
        persistSelection(current)
        refresh()
    }

    fun clearChannelFilter() {
        _state.value = _state.value.copy(selectedChannelIds = emptySet())
        persistSelection(emptySet())
        refresh()
    }

    private fun persistSelection(ids: Set<Int>) {
        if (TokenHolder.isUnlocked) {
            defaultChannelPrefs.setUnlockedDefaults(ids)
        } else {
            defaultChannelPrefs.setLockedDefaults(ids)
        }
    }

    fun skip(videoId: Int) {
        viewModelScope.launch {
            repo.skip(videoId).onSuccess {
                _state.value = _state.value.copy(
                    videos = _state.value.videos.filterNot { it.id == videoId },
                    total = (_state.value.total - 1).coerceAtLeast(0),
                )
            }
        }
    }

    fun favorite(videoId: Int) {
        viewModelScope.launch { repo.favorite(videoId) }
    }

    fun currentQueue(): List<Int> = _state.value.videos.map { it.id }

    fun playAll() {
        viewModelScope.launch {
            _state.value = _state.value.copy(queueLoading = true)
            repo.getFeedIds(
                channelIds = _state.value.selectedChannelIds.takeIf { it.isNotEmpty() }?.joinToString(","),
                category = _state.value.selectedCategory,
            ).onSuccess { resp ->
                val ids = resp.ids.map { it.id }
                if (ids.isNotEmpty()) {
                    _state.value = _state.value.copy(
                        queueLoading = false,
                        pendingQueue = QueuePayload(ids, 0),
                    )
                } else {
                    _state.value = _state.value.copy(queueLoading = false)
                }
            }.onFailure {
                _state.value = _state.value.copy(queueLoading = false)
            }
        }
    }

    fun shuffleAll() {
        viewModelScope.launch {
            _state.value = _state.value.copy(queueLoading = true)
            repo.getFeedIds(
                channelIds = _state.value.selectedChannelIds.takeIf { it.isNotEmpty() }?.joinToString(","),
                category = _state.value.selectedCategory,
            ).onSuccess { resp ->
                val ids = resp.ids.map { it.id }.shuffled()
                if (ids.isNotEmpty()) {
                    _state.value = _state.value.copy(
                        queueLoading = false,
                        pendingQueue = QueuePayload(ids, 0),
                    )
                } else {
                    _state.value = _state.value.copy(queueLoading = false)
                }
            }.onFailure {
                _state.value = _state.value.copy(queueLoading = false)
            }
        }
    }

    fun clearPendingQueue() {
        _state.value = _state.value.copy(pendingQueue = null)
    }

    fun scrape() {
        viewModelScope.launch {
            _state.value = _state.value.copy(scraping = true, actionMessage = null)
            val channelIds = _state.value.selectedChannelIds.takeIf { it.isNotEmpty() }?.joinToString(",")
            repo.scrapeAll(channelIds).onSuccess { msg ->
                _state.value = _state.value.copy(scraping = false, actionMessage = msg)
            }.onFailure { e ->
                _state.value = _state.value.copy(scraping = false, actionMessage = "Scrape failed: ${e.message}")
            }
        }
    }

    fun resolveBatch() {
        viewModelScope.launch {
            _state.value = _state.value.copy(resolving = true, actionMessage = null)
            val channelIds = _state.value.selectedChannelIds.takeIf { it.isNotEmpty() }?.joinToString(",")
            repo.resolveBatch(channelIds).onSuccess { msg ->
                _state.value = _state.value.copy(resolving = false, actionMessage = msg)
            }.onFailure { e ->
                _state.value = _state.value.copy(resolving = false, actionMessage = "Resolve failed: ${e.message}")
            }
        }
    }

    fun upgradeChannel(channelId: Int) {
        viewModelScope.launch {
            _state.value = _state.value.copy(
                upgradingChannelIds = _state.value.upgradingChannelIds + channelId,
            )
            repo.upgradeQuality(channelId.toString()).onSuccess { msg ->
                _state.value = _state.value.copy(
                    upgradingChannelIds = _state.value.upgradingChannelIds - channelId,
                    actionMessage = msg,
                )
            }.onFailure { e ->
                _state.value = _state.value.copy(
                    upgradingChannelIds = _state.value.upgradingChannelIds - channelId,
                    actionMessage = "Upgrade failed: ${e.message}",
                )
            }
        }
    }

    fun clearActionMessage() {
        _state.value = _state.value.copy(actionMessage = null)
    }
}
