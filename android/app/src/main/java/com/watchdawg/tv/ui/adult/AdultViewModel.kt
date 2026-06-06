package com.watchdawg.tv.ui.adult

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.watchdawg.tv.data.api.FavoriteDto
import com.watchdawg.tv.data.api.LibraryFileDto
import com.watchdawg.tv.data.api.VideoDto
import com.watchdawg.tv.data.auth.TokenHolder
import com.watchdawg.tv.data.prefs.QueueHolder
import com.watchdawg.tv.data.repo.WatchDawgRepository
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch

/**
 * ViewModel for AdultScreen — Milestone R-4.
 *
 * Manages three content sources, all PIN-gated:
 *
 *  1. Backend adult feed — GET /feed?category=adult&genre_tag=X
 *     Locked channels only returned when token present (server enforced).
 *     Genre tags drive the backend genre pills.
 *
 *  2. Locked Favorites — GET /favorite filtered to channelLocked=true
 *     Shown under the hardcoded "Favorites" pill (first pill, always).
 *     Remove via DELETE /favorite/{id} — same endpoint as main Favorites screen.
 *     Play All / Shuffle All use ID queue (resolve path, fresh CDN tokens).
 *
 *  3. Local Private files — GET /library filtered to subfolder=="Private"
 *     Shown under the hardcoded "Local" pill (second pill, if files exist).
 *     Remove via DELETE /library/file — same endpoint as LibraryScreen.
 *     Remove shows a confirmation dialog in the UI before executing.
 *     Play All / Shuffle All use URL queue.
 *
 * Pill order: Favorites | Local | [backend genre tags]
 *
 * Duplicate favorite prevention: handled server-side — POST /favorite/{id}/bookmark
 * returns {"status":"already_favorited"} silently with no duplicate created.
 * No client-side guard needed.
 *
 * Session 33 — Smart Shuffle (hybrid played-bit model):
 *   smartShuffle() replaces shuffleAll() for genre pill content (backend feed).
 *   Favorites and Local pills use simple shuffled() — they are small curated
 *   lists where repeat-prevention is less important and there is no backend
 *   ordering endpoint for them.
 *
 *   Genre pill smart shuffle behaviour is identical to MusicViewModel:
 *   - In-memory playedIds Set tracks played videos within the session.
 *   - Backend order_by=least_watched gives cross-session weighting.
 *   - playedIds is cleared when the pill changes (genre isolation).
 *   - Silent cycle reset when pool is exhausted.
 *   - QueueHolder.onVideoPlayed callback wired to markPlayed().
 */
class AdultViewModel(private val repo: WatchDawgRepository) : ViewModel() {

    companion object {
        const val FAVORITES_PILL = "Favorites"
        const val LOCAL_PILL     = "Local"
    }

    // ── Adult backend feed state ──────────────────────────────────────────────

    sealed class AdultState {
        object Loading : AdultState()
        data class Ready(val videos: List<VideoDto>) : AdultState()
        data class Error(val message: String) : AdultState()
    }

    private val _adultState = MutableStateFlow<AdultState>(AdultState.Loading)
    val adultState: StateFlow<AdultState> = _adultState

    // ── Genre pill state ──────────────────────────────────────────────────────

    sealed class GenreState {
        object Loading : GenreState()
        data class Ready(val tags: List<String>) : GenreState()
    }

    private val _genreState = MutableStateFlow<GenreState>(GenreState.Loading)
    val genreState: StateFlow<GenreState> = _genreState

    // ── Selected pill ─────────────────────────────────────────────────────────

    private val _selectedPill = MutableStateFlow<String?>(FAVORITES_PILL)
    val selectedPill: StateFlow<String?> = _selectedPill

    // ── Locked favorites ──────────────────────────────────────────────────────

    private val _lockedFavorites = MutableStateFlow<List<FavoriteDto>>(emptyList())
    val lockedFavorites: StateFlow<List<FavoriteDto>> = _lockedFavorites

    /** IDs currently being removed from locked favorites (optimistic). */
    private val _removingFavIds = MutableStateFlow<Set<Int>>(emptySet())
    val removingFavIds: StateFlow<Set<Int>> = _removingFavIds

    // ── Local private files ───────────────────────────────────────────────────

    private val _privateFiles = MutableStateFlow<List<LibraryFileDto>>(emptyList())
    val privateFiles: StateFlow<List<LibraryFileDto>> = _privateFiles

    /** Relative paths currently being deleted from private files (optimistic). */
    private val _removingPaths = MutableStateFlow<Set<String>>(emptySet())
    val removingPaths: StateFlow<Set<String>> = _removingPaths

    // ── Pending play queues ───────────────────────────────────────────────────

    data class PendingIdQueue(val ids: List<Int>, val startIndex: Int)

    private val _pendingIdQueue = MutableStateFlow<PendingIdQueue?>(null)
    val pendingIdQueue: StateFlow<PendingIdQueue?> = _pendingIdQueue

    private val _pendingUrlQueue = MutableStateFlow<List<String>?>(null)
    val pendingUrlQueue: StateFlow<List<String>?> = _pendingUrlQueue

    // ── Smart Shuffle — in-memory played-bit Set ──────────────────────────────

    /**
     * Tracks video IDs that have already been played this session for the
     * current genre pill. Cleared on genre pill change and on full-cycle reset.
     * Does NOT apply to Favorites or Local pills (they use simple shuffle).
     */
    private val playedIds: MutableSet<Int> = mutableSetOf()

    // ── Init ──────────────────────────────────────────────────────────────────

    init {
        viewModelScope.launch {
            TokenHolder.tokenFlow.collect {
                loadAll()
                _selectedPill.value = FAVORITES_PILL
            }
        }
    }

    // ── Load ──────────────────────────────────────────────────────────────────

    fun loadAll() {
        loadGenres()
        loadLockedFavorites()
        loadPrivateFiles()
        val pill = _selectedPill.value
        if (pill != FAVORITES_PILL && pill != LOCAL_PILL) {
            loadAdult(genreTag = pill)
        }
    }

    fun loadGenres() {
        viewModelScope.launch {
            repo.fetchGenres("adult")
                .onSuccess { tags -> _genreState.value = GenreState.Ready(tags) }
                .onFailure  { _genreState.value = GenreState.Ready(emptyList()) }
        }
    }

    fun loadAdult(genreTag: String? = null) {
        _adultState.value = AdultState.Loading
        viewModelScope.launch {
            repo.getFeed(
                limit    = 1000,
                category = "adult",
                genreTag = genreTag,
            ).onSuccess { response ->
                _adultState.value = AdultState.Ready(response.videos)
            }.onFailure { e ->
                _adultState.value = AdultState.Error(e.message ?: "Failed to load adult content")
            }
        }
    }

    fun loadLockedFavorites() {
        viewModelScope.launch {
            repo.getFavorites()
                .onSuccess { list ->
                    _lockedFavorites.value = list.filter { it.channelLocked }
                }
                .onFailure {
                    _lockedFavorites.value = emptyList()
                }
        }
    }

    fun loadPrivateFiles() {
        viewModelScope.launch {
            repo.getLibrary()
                .onSuccess { response ->
                    _privateFiles.value = response.files.filter { it.subfolder == "Private" }
                }
                .onFailure {
                    _privateFiles.value = emptyList()
                }
        }
    }

    fun selectPill(pill: String?) {
        // Clear played Set when switching between genre pills so each genre
        // has its own independent played cycle. Favorites / Local do not use
        // the played Set so the clear is harmless for them.
        if (pill != _selectedPill.value) {
            playedIds.clear()
            // Session 42: clear subfolder filter when leaving Local pill
            if (_selectedPill.value == LOCAL_PILL) {
                _localSubfolderFilter.value = emptySet()
            }
        }
        _selectedPill.value = pill
        if (pill != FAVORITES_PILL && pill != LOCAL_PILL) {
            loadAdult(genreTag = pill)
        }
    }

    // ── Remove actions ────────────────────────────────────────────────────────

    /**
     * Remove a locked favorite from the Adult Favorites pill.
     * Optimistic: removes from list immediately, refreshes on failure.
     * Calls DELETE /favorite/{id} — same endpoint as main Favorites screen.
     */
    fun removeLockedFavorite(favoriteId: Int) {
        if (_removingFavIds.value.contains(favoriteId)) return
        viewModelScope.launch {
            _lockedFavorites.value = _lockedFavorites.value.filterNot { it.id == favoriteId }
            _removingFavIds.value  = _removingFavIds.value + favoriteId
            repo.removeFavorite(favoriteId)
                .onSuccess {
                    _removingFavIds.value = _removingFavIds.value - favoriteId
                }
                .onFailure {
                    _removingFavIds.value = _removingFavIds.value - favoriteId
                    loadLockedFavorites()
                }
        }
    }

    /**
     * Delete a Private library file from the Adult Local pill.
     * Optimistic: removes from list immediately, refreshes on failure.
     * Calls DELETE /library/file?relative_path=X — same endpoint as LibraryScreen.
     * Confirmation dialog is shown in AdultScreen before this is called.
     */
    fun removePrivateFile(relativePath: String) {
        if (_removingPaths.value.contains(relativePath)) return
        viewModelScope.launch {
            _privateFiles.value   = _privateFiles.value.filterNot { it.relativePath == relativePath }
            _removingPaths.value  = _removingPaths.value + relativePath
            repo.deleteLibraryFile(relativePath)
                .onSuccess {
                    _removingPaths.value = _removingPaths.value - relativePath
                }
                .onFailure {
                    _removingPaths.value = _removingPaths.value - relativePath
                    loadPrivateFiles()
                }
        }
    }

    // ── Local subfolder filter (Session 42) ──────────────────────────────────
    // Multi-select: empty set = show all private files.
    // Each entry is the display name (id prefix stripped) of a Private subfolder.
    // Toggling a name adds/removes it; selecting "All" clears the set.

    private val _localSubfolderFilter = MutableStateFlow<Set<String>>(emptySet())
    val localSubfolderFilter: StateFlow<Set<String>> = _localSubfolderFilter

    /**
     * Toggle a subfolder name in the filter set.
     * If the name is already selected, remove it. Otherwise add it.
     */
    fun toggleLocalSubfolder(name: String) {
        val current = _localSubfolderFilter.value
        _localSubfolderFilter.value = if (name in current) current - name else current + name
    }

    /** Clear all subfolder filters — show everything in Local. */
    fun clearLocalSubfolderFilter() {
        _localSubfolderFilter.value = emptySet()
    }

    /**
     * Returns the private files filtered by the current subfolder selection.
     * Empty filter = all files. Used by AdultScreen instead of privateFiles directly.
     */
    fun filteredPrivateFiles(): List<LibraryFileDto> {
        val filter = _localSubfolderFilter.value
        if (filter.isEmpty()) return _privateFiles.value
        return _privateFiles.value.filter { file ->
            val folderDisplayName = subfolderDisplayName(file.relativePath)
            folderDisplayName != null && folderDisplayName in filter
        }
    }

    /**
     * Returns unique subfolder display names from the private files list.
     * Strips the {channel_id}_ prefix: "123_Shuffle Kings" → "Shuffle Kings".
     * Used to populate the secondary pill row in AdultScreen.
     */
    fun privateSubfolders(): List<String> {
        return _privateFiles.value
            .mapNotNull { subfolderDisplayName(it.relativePath) }
            .distinct()
            .sorted()
    }

    private fun subfolderDisplayName(relativePath: String?): String? {
        if (relativePath.isNullOrBlank()) return null
        // relativePath: "Private/123_Shuffle Kings/video.mp4"
        val parts = relativePath.split("/")
        if (parts.size < 2) return null
        val folder = parts[1] // e.g. "123_Shuffle Kings"
        // Strip leading "{digits}_" prefix
        return folder.replaceFirst(Regex("^\\d+_"), "").ifBlank { folder }
    }

    /**
     * Called by QueueHolder.onVideoPlayed when PlayerViewModel successfully
     * resolves a new video during a smart shuffle queue. Adds the videoId to
     * the in-memory playedIds Set so the next smartShuffle() call excludes it.
     */
    fun markPlayed(videoId: Int) {
        playedIds.add(videoId)
    }

    // ── Play All / Shuffle All ────────────────────────────────────────────────

    fun playAll() {
        when (val pill = _selectedPill.value) {
            FAVORITES_PILL -> {
                val ids = _lockedFavorites.value
                    .filter { it.downloadStatus != "complete" && it.videoId != null && it.videoId > 0 }
                    .mapNotNull { it.videoId }
                if (ids.isNotEmpty()) {
                    QueueHolder.onVideoPlayed = null
                    _pendingIdQueue.value = PendingIdQueue(ids, 0)
                }
            }
            LOCAL_PILL -> {
                val urls = filteredPrivateFiles().mapNotNull { it.streamUrl?.takeIf { u -> u.isNotBlank() } }
                if (urls.isNotEmpty()) _pendingUrlQueue.value = urls
            }
            else -> {
                viewModelScope.launch {
                    repo.getFeedIds(category = "adult", genreTag = pill)
                        .onSuccess { response ->
                            val ids = response.ids.map { it.id }
                            if (ids.isNotEmpty()) {
                                QueueHolder.onVideoPlayed = null
                                _pendingIdQueue.value = PendingIdQueue(ids, 0)
                            }
                        }
                }
            }
        }
    }

    /**
     * Smart Shuffle:
     * - Favorites pill: simple shuffle of the in-memory favorites list (small
     *   curated list, no played-bit tracking needed).
     * - Local pill: simple shuffle of the in-memory private files list.
     * - Genre pills: hybrid Smart Shuffle — same model as MusicViewModel.
     *   Cross-session weighting via order_by=least_watched, within-session
     *   no-repeat via playedIds Set, silent cycle reset when pool exhausted.
     */
    fun smartShuffle() {
        when (val pill = _selectedPill.value) {
            FAVORITES_PILL -> {
                val ids = _lockedFavorites.value
                    .filter { it.downloadStatus != "complete" && it.videoId != null && it.videoId > 0 }
                    .mapNotNull { it.videoId }
                    .shuffled()
                if (ids.isNotEmpty()) {
                    QueueHolder.onVideoPlayed = null
                    _pendingIdQueue.value = PendingIdQueue(ids, 0)
                }
            }
            LOCAL_PILL -> {
                val urls = filteredPrivateFiles()
                    .mapNotNull { it.streamUrl?.takeIf { u -> u.isNotBlank() } }
                    .shuffled()
                if (urls.isNotEmpty()) _pendingUrlQueue.value = urls
            }
            else -> {
                // Genre pill — full Smart Shuffle with played-bit + cross-session weighting
                viewModelScope.launch {
                    repo.getFeedIds(
                        category = "adult",
                        genreTag = pill,
                        orderBy  = "least_watched",
                    ).onSuccess { response ->
                        val allIds = response.ids.map { it.id }
                        if (allIds.isEmpty()) return@onSuccess

                        // Filter to unplayed videos for this session
                        val unplayed = allIds.filter { it !in playedIds }

                        // If pool exhausted — silent cycle reset
                        val pool = if (unplayed.isEmpty()) {
                            playedIds.clear()
                            allIds
                        } else {
                            unplayed
                        }

                        // Shuffle within the backend-ordered pool
                        val shuffled = pool.shuffled()

                        // Register played callback BEFORE queuing
                        QueueHolder.onVideoPlayed = { videoId -> markPlayed(videoId) }

                        _pendingIdQueue.value = PendingIdQueue(shuffled, 0)

                    }.onFailure {
                        // Backend call failed — fall back to plain shuffle of visible videos
                        val visibleIds = (_adultState.value as? AdultState.Ready)
                            ?.videos?.map { it.id }?.shuffled() ?: return@onFailure
                        if (visibleIds.isNotEmpty()) {
                            QueueHolder.onVideoPlayed = { videoId -> markPlayed(videoId) }
                            _pendingIdQueue.value = PendingIdQueue(visibleIds, 0)
                        }
                    }
                }
            }
        }
    }

    fun clearPendingIdQueue()  { _pendingIdQueue.value  = null }
    fun clearPendingUrlQueue() { _pendingUrlQueue.value = null }
}
