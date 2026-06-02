package com.watchdawg.tv.ui.music

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.watchdawg.tv.data.api.VideoDto
import com.watchdawg.tv.data.prefs.QueueHolder
import com.watchdawg.tv.data.repo.WatchDawgRepository
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch

/**
 * ViewModel for MusicScreen — Milestone R-4.
 *
 * Mirrors MoviesViewModel exactly with category = "music".
 *
 * Owns three pieces of state:
 *  1. [musicState]    — flat list of music VideoDto objects from GET /feed?category=music.
 *  2. [genreState]    — distinct genre tags from GET /feed/genres?category=music.
 *                       Drives the pill bar (e.g. "Hot", "Rock", "70s", "Classic Rock").
 *                       Empty list = pill bar shows "All" only.
 *  3. [selectedGenre] — active genre pill. Null = "All" (no filter).
 *
 * Loading flow:
 *   loadGenres() fires once on first composition.
 *   loadMusic(genreTag) fires on every genre pill selection change,
 *   including initial load (selectedGenre starts null = All).
 *
 * Play All / Shuffle All:
 *   Uses GET /feed/ids?category=music&genre_tag=X via repo.getFeedIds().
 *   Always fetches the full ID list from the backend — Shuffle All shuffles
 *   ALL matching videos in the database, not just what is visible on screen.
 *
 * Session 33 — Smart Shuffle (hybrid played-bit model):
 *   smartShuffle() replaces shuffleAll() as the shuffle action.
 *
 *   Within-session layer (in-memory Set):
 *     playedIds tracks which video IDs have already played this session for
 *     the current genre. When the player resolves a new video, PlayerViewModel
 *     calls QueueHolder.onVideoPlayed which invokes markPlayed() here.
 *     The next smartShuffle() call filters the full ID pool against playedIds —
 *     only unplayed videos enter the queue. When the pool is exhausted (all
 *     videos played), playedIds is cleared silently and a fresh cycle begins.
 *
 *   Cross-session layer (backend ordering):
 *     The full ID list is fetched with order_by=least_watched, which causes
 *     the backend to order by watch_history.last_watched_at ASC NULLS FIRST.
 *     Never-watched and least-recently-watched content surfaces first within
 *     each new cycle, giving cross-session variety on top of within-session
 *     uniqueness.
 *
 *   Genre isolation:
 *     playedIds is cleared whenever the genre pill changes (selectedGenre
 *     changes) so each genre maintains its own independent played cycle.
 *     The backend genre_tag filter ensures only matching videos enter the pool.
 */
class MusicViewModel(private val repo: WatchDawgRepository) : ViewModel() {

    // ── Music list state ──────────────────────────────────────────────────────

    sealed class MusicState {
        object Loading : MusicState()
        data class Ready(val videos: List<VideoDto>) : MusicState()
        data class Error(val message: String) : MusicState()
    }

    private val _musicState = MutableStateFlow<MusicState>(MusicState.Loading)
    val musicState: StateFlow<MusicState> = _musicState

    // ── Genre pill state ──────────────────────────────────────────────────────

    sealed class GenreState {
        object Loading : GenreState()
        data class Ready(val tags: List<String>) : GenreState()
    }

    private val _genreState = MutableStateFlow<GenreState>(GenreState.Loading)
    val genreState: StateFlow<GenreState> = _genreState

    private val _selectedGenre = MutableStateFlow<String?>(null)
    val selectedGenre: StateFlow<String?> = _selectedGenre

    // ── Pending play queue (Play All / Smart Shuffle) ─────────────────────────

    data class PendingQueue(val ids: List<Int>, val startIndex: Int)

    private val _pendingQueue = MutableStateFlow<PendingQueue?>(null)
    val pendingQueue: StateFlow<PendingQueue?> = _pendingQueue

    // ── Smart Shuffle — in-memory played-bit Set ──────────────────────────────

    /**
     * Tracks video IDs that have already been played this session for the
     * current genre. Scoped to a single genre — cleared on genre pill change.
     * Cleared and restarted silently when the entire pool has been played
     * (full cycle complete).
     */
    private val playedIds: MutableSet<Int> = mutableSetOf()

    // ── Actions ───────────────────────────────────────────────────────────────

    fun loadGenres() {
        viewModelScope.launch {
            repo.fetchGenres("music")
                .onSuccess { tags -> _genreState.value = GenreState.Ready(tags) }
                .onFailure  { _genreState.value = GenreState.Ready(emptyList()) }
        }
    }

    fun loadMusic(genreTag: String? = null) {
        _musicState.value = MusicState.Loading
        viewModelScope.launch {
            repo.getFeed(
                limit    = 1000,
                category = "music",
                genreTag = genreTag,
            ).onSuccess { response ->
                _musicState.value = MusicState.Ready(response.videos)
            }.onFailure { e ->
                _musicState.value = MusicState.Error(e.message ?: "Failed to load music")
            }
        }
    }

    fun selectGenre(tag: String?) {
        // Clear the played set when switching genres so each genre has its own
        // independent cycle. The new genre's cycle starts fresh.
        if (tag != _selectedGenre.value) {
            playedIds.clear()
        }
        _selectedGenre.value = tag
    }

    /**
     * Mark a video ID as played within the current session.
     * Called by QueueHolder.onVideoPlayed → set before each smart shuffle queue.
     */
    fun markPlayed(videoId: Int) {
        playedIds.add(videoId)
    }

    /**
     * Play All — loads full ID list in natural order for the active genre filter,
     * then fires the queue via [pendingQueue]. MusicScreen LaunchedEffect picks it up.
     *
     * Always fetches the complete ID list from the backend — not limited to what
     * is currently visible on screen.
     */
    fun playAll() {
        viewModelScope.launch {
            repo.getFeedIds(
                category = "music",
                genreTag = _selectedGenre.value,
            ).onSuccess { response ->
                val ids = response.ids.map { it.id }
                if (ids.isNotEmpty()) {
                    QueueHolder.onVideoPlayed = null   // Play All does not track played state
                    _pendingQueue.value = PendingQueue(ids = ids, startIndex = 0)
                }
            }
        }
    }

    /**
     * Smart Shuffle — hybrid played-bit + cross-session weighting model.
     *
     * 1. Fetches full genre-filtered ID list from backend ordered by
     *    least_watched (last_watched_at ASC NULLS FIRST) for cross-session variety.
     * 2. Filters out IDs already played this session (in-memory playedIds Set)
     *    for within-session no-repeat guarantee.
     * 3. If the unplayed pool is empty (full cycle complete), resets playedIds
     *    silently and uses the full list for a fresh cycle.
     * 4. Sets QueueHolder.onVideoPlayed to markPlayed() BEFORE calling setIdQueue
     *    so PlayerViewModel fires the callback when each video resolves.
     */
    fun smartShuffle() {
        viewModelScope.launch {
            repo.getFeedIds(
                category = "music",
                genreTag = _selectedGenre.value,
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

                // Shuffle within the backend-ordered pool for randomness
                val shuffled = pool.shuffled()

                // Register the played callback BEFORE queuing so PlayerViewModel
                // can fire it as soon as the first video resolves.
                QueueHolder.onVideoPlayed = { videoId -> markPlayed(videoId) }

                _pendingQueue.value = PendingQueue(ids = shuffled, startIndex = 0)

            }.onFailure {
                // Backend call failed — fall back to plain shuffle of visible videos
                // so the button always does something useful.
                val visibleIds = (_musicState.value as? MusicState.Ready)
                    ?.videos?.map { it.id }?.shuffled() ?: return@onFailure
                if (visibleIds.isNotEmpty()) {
                    QueueHolder.onVideoPlayed = { videoId -> markPlayed(videoId) }
                    _pendingQueue.value = PendingQueue(ids = visibleIds, startIndex = 0)
                }
            }
        }
    }

    fun clearPendingQueue() {
        _pendingQueue.value = null
    }
}
