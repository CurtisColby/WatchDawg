package com.watchdawg.tv.data.prefs

import android.content.Context
import android.content.SharedPreferences

/**
 * Persists lightweight playback resume state so closing the app (or the TV
 * losing power) lets us offer to pick up where the user left off.
 *
 * Stores:
 *  - the video id that was playing
 *  - the playback position in ms
 *  - the queue (ordered list of video ids) and the index within it
 *  - the video title (for display in the resume banner)
 *  - hlsMode flag (Milestone E) — whether the video was playing as HLS or
 *    split-stream. Required so resume re-resolves with the correct client
 *    param and ExoPlayer can seek to the saved position.
 *
 * No stream URLs are persisted — those expire. On resume we re-resolve the
 * stored video id fresh. This is intentionally small and safe to lose.
 *
 * Security (Milestone E): save() refuses to persist locked/adult content.
 * The caller (PlayerViewModel.saveResume) passes isLockedSource=true for any
 * video from a locked channel. We silently ignore the save and clear any
 * previously stored state so a cold-start never surfaces locked content in
 * the resume banner when the app is in the unauthenticated state.
 */
class ResumeState(context: Context) {

    private val prefs: SharedPreferences =
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    /**
     * Save resume state. Pass [isLockedSource]=true for any video from a
     * locked or adult channel — the save is silently skipped and any existing
     * state is cleared so it can never surface on cold-start without a PIN.
     */
    fun save(
        videoId: Int,
        positionMs: Long,
        queue: List<Int>,
        index: Int,
        title: String = "",
        hlsMode: Boolean = false,
        isLockedSource: Boolean = false,
    ) {
        if (isLockedSource) {
            // Never persist locked/adult content to disk — clear any prior state.
            clear()
            return
        }
        prefs.edit()
            .putInt(KEY_VIDEO_ID, videoId)
            .putLong(KEY_POSITION, positionMs)
            .putString(KEY_QUEUE, queue.joinToString(","))
            .putInt(KEY_INDEX, index)
            .putString(KEY_TITLE, title)
            .putBoolean(KEY_HLS_MODE, hlsMode)
            .apply()
    }

    fun clear() {
        prefs.edit().clear().apply()
    }

    fun load(): Saved? {
        val videoId = prefs.getInt(KEY_VIDEO_ID, -1)
        if (videoId < 0) return null
        val queueStr = prefs.getString(KEY_QUEUE, "") ?: ""
        val queue = queueStr.split(",").mapNotNull { it.trim().toIntOrNull() }
        return Saved(
            videoId    = videoId,
            positionMs = prefs.getLong(KEY_POSITION, 0L),
            queue      = queue,
            index      = prefs.getInt(KEY_INDEX, 0),
            title      = prefs.getString(KEY_TITLE, "") ?: "",
            hlsMode    = prefs.getBoolean(KEY_HLS_MODE, false),
        )
    }

    data class Saved(
        val videoId: Int,
        val positionMs: Long,
        val queue: List<Int>,
        val index: Int,
        val title: String = "",
        val hlsMode: Boolean = false,
    )

    companion object {
        private const val PREFS_NAME   = "watchdawg_resume"
        private const val KEY_VIDEO_ID = "video_id"
        private const val KEY_POSITION = "position_ms"
        private const val KEY_QUEUE    = "queue"
        private const val KEY_INDEX    = "index"
        private const val KEY_TITLE    = "title"
        private const val KEY_HLS_MODE = "hls_mode"
    }
}
