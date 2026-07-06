package com.watchdawg.tv.data.prefs

import android.content.Context
import android.content.SharedPreferences

/**
 * Persists default channel selections across app restarts, with strict
 * separation between locked and unlocked session states.
 *
 * Two completely independent storage buckets:
 *   KEY_LOCKED   — defaults applied when the app is locked (no PIN entered)
 *   KEY_UNLOCKED — defaults applied when the session is unlocked
 *
 * This guarantees that adult/sensitive channels set as defaults while unlocked
 * can never bleed into the locked session. The lock/unlock transition in
 * FeedViewModel loads the appropriate bucket — never the other one.
 *
 * An empty set means "no default filter" → show all visible channels (same
 * as the current behaviour when nothing is selected).
 */
class DefaultChannelPrefs(context: Context) {

    private val prefs: SharedPreferences =
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    /** Returns saved default channel IDs for the locked session. */
    fun getLockedDefaults(): Set<Int> =
        prefs.getStringSet(KEY_LOCKED, emptySet())
            ?.mapNotNull { it.toIntOrNull() }
            ?.toSet()
            ?: emptySet()

    /** Returns saved default channel IDs for the unlocked session. */
    fun getUnlockedDefaults(): Set<Int> =
        prefs.getStringSet(KEY_UNLOCKED, emptySet())
            ?.mapNotNull { it.toIntOrNull() }
            ?.toSet()
            ?: emptySet()

    /** Overwrites the locked defaults with [ids]. Pass empty set to clear. */
    fun setLockedDefaults(ids: Set<Int>) {
        prefs.edit()
            .putStringSet(KEY_LOCKED, ids.map { it.toString() }.toSet())
            .apply()
    }

    /** Overwrites the unlocked defaults with [ids]. Pass empty set to clear. */
    fun setUnlockedDefaults(ids: Set<Int>) {
        prefs.edit()
            .putStringSet(KEY_UNLOCKED, ids.map { it.toString() }.toSet())
            .apply()
    }

    companion object {
        private const val PREFS_NAME  = "watchdawg_default_channels"
        private const val KEY_LOCKED   = "defaults_locked"
        private const val KEY_UNLOCKED = "defaults_unlocked"
    }
}
