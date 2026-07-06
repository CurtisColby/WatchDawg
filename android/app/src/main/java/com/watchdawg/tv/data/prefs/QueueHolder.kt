package com.watchdawg.tv.data.prefs

import com.watchdawg.tv.data.api.VideoDto

/**
 * In-memory queue holder. Avoids putting large queues in navigation route
 * strings which get truncated or corrupted by Android's nav system when
 * the queue has thousands of IDs.
 *
 * Two flavours:
 *   - idQueue: List<Int>  — video IDs resolved on-demand via /resolve (Feed)
 *   - urlQueue: List<String> — direct stream URLs (Library / Favorites)
 *
 * resumePositionMs: set when expanding mini-player back to full-screen so
 * the player resumes at the exact position the mini-player was at.
 *
 * Milestone E: lockedSource flag — set true when the playing video comes from
 * a locked or adult channel. PlayerViewModel reads this via PlayerStartMode.Resolve
 * and refuses to persist the resume state to SharedPreferences, ensuring locked
 * content never surfaces in the resume banner on cold-start.
 *
 * Milestone R-2: pendingVideo replaces MovieHolder. The full VideoDto is stored
 * here immediately before navigating to Routes.MOVIE_DETAIL. The composable
 * reads and consumes it. Cleared in onBack of MovieDetailScreen.
 *
 * Session 33 — Smart Shuffle:
 *   onVideoPlayed: optional callback set by MusicViewModel / AdultViewModel
 *   before queuing a Smart Shuffle. PlayerViewModel calls it each time it
 *   successfully resolves a new video (in resolveCurrent via the state playToken).
 *   The ViewModel uses the videoId to mark the video as played in its in-memory
 *   playedIds Set, enabling within-session no-repeat behaviour.
 *   Cleared alongside the queue in clear() and setUrlQueue() so it never fires
 *   for non-Smart-Shuffle queues.
 */
object QueueHolder {
    var idQueue: List<Int> = emptyList()
    var urlQueue: List<String> = emptyList()
    var startIndex: Int = 0
    var resumePositionMs: Long = 0L
    var hlsMode: Boolean = false
    var lockedSource: Boolean = false

    /** Replaces MovieHolder — VideoDto for the MovieDetailScreen composable. */
    var pendingVideo: VideoDto? = null

    /**
     * Smart Shuffle callback. Set by MusicViewModel / AdultViewModel before
     * calling setIdQueue() for a smart shuffle queue. PlayerViewModel calls
     * this with the videoId each time a new video begins resolving so the
     * ViewModel can mark it as played in its in-memory Set.
     *
     * Null for all non-smart-shuffle queues — PlayerViewModel guards with a
     * null-check before invoking.
     */
    var onVideoPlayed: ((Int) -> Unit)? = null

    fun setIdQueue(ids: List<Int>, index: Int = 0, hls: Boolean = false, locked: Boolean = false) {
        idQueue = ids
        urlQueue = emptyList()
        startIndex = index
        resumePositionMs = 0L
        hlsMode = hls
        lockedSource = locked
        // onVideoPlayed is intentionally NOT cleared here — the caller sets it
        // before setIdQueue() when it wants Smart Shuffle tracking, or leaves
        // it as the previous value (null for non-smart queues).
    }

    fun setUrlQueue(urls: List<String>, index: Int = 0, resumeMs: Long = 0L) {
        urlQueue = urls
        idQueue = emptyList()
        startIndex = index
        resumePositionMs = resumeMs
        hlsMode = false
        lockedSource = false
        onVideoPlayed = null   // URL queues never use Smart Shuffle tracking
    }

    fun clear() {
        idQueue = emptyList()
        urlQueue = emptyList()
        startIndex = 0
        resumePositionMs = 0L
        hlsMode = false
        lockedSource = false
        pendingVideo = null
        onVideoPlayed = null
    }
}
