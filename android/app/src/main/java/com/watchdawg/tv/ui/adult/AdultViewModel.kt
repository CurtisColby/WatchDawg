package com.watchdawg.tv.ui.adult

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.watchdawg.tv.data.api.FavoriteDto
import com.watchdawg.tv.data.api.LibraryFileDto
import com.watchdawg.tv.data.api.VideoDto
import com.watchdawg.tv.data.auth.TokenHolder
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

    // ── Play All / Shuffle All ────────────────────────────────────────────────

    fun playAll() {
        when (val pill = _selectedPill.value) {
            FAVORITES_PILL -> {
                val ids = _lockedFavorites.value
                    .filter { it.downloadStatus != "complete" && it.videoId != null && it.videoId > 0 }
                    .mapNotNull { it.videoId }
                if (ids.isNotEmpty()) _pendingIdQueue.value = PendingIdQueue(ids, 0)
            }
            LOCAL_PILL -> {
                val urls = _privateFiles.value.mapNotNull { it.streamUrl?.takeIf { u -> u.isNotBlank() } }
                if (urls.isNotEmpty()) _pendingUrlQueue.value = urls
            }
            else -> {
                viewModelScope.launch {
                    repo.getFeedIds(category = "adult", genreTag = pill)
                        .onSuccess { response ->
                            val ids = response.ids.map { it.id }
                            if (ids.isNotEmpty()) _pendingIdQueue.value = PendingIdQueue(ids, 0)
                        }
                }
            }
        }
    }

    fun shuffleAll() {
        when (val pill = _selectedPill.value) {
            FAVORITES_PILL -> {
                val ids = _lockedFavorites.value
                    .filter { it.downloadStatus != "complete" && it.videoId != null && it.videoId > 0 }
                    .mapNotNull { it.videoId }
                    .shuffled()
                if (ids.isNotEmpty()) _pendingIdQueue.value = PendingIdQueue(ids, 0)
            }
            LOCAL_PILL -> {
                val urls = _privateFiles.value
                    .mapNotNull { it.streamUrl?.takeIf { u -> u.isNotBlank() } }
                    .shuffled()
                if (urls.isNotEmpty()) _pendingUrlQueue.value = urls
            }
            else -> {
                viewModelScope.launch {
                    repo.getFeedIds(category = "adult", genreTag = pill)
                        .onSuccess { response ->
                            val ids = response.ids.map { it.id }.shuffled()
                            if (ids.isNotEmpty()) _pendingIdQueue.value = PendingIdQueue(ids, 0)
                        }
                }
            }
        }
    }

    fun clearPendingIdQueue()  { _pendingIdQueue.value  = null }
    fun clearPendingUrlQueue() { _pendingUrlQueue.value = null }
}
