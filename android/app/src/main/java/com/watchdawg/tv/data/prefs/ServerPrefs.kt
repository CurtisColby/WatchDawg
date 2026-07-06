package com.watchdawg.tv.data.prefs

import android.content.Context
import android.content.SharedPreferences
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

/**
 * Persists ONLY the backend server address (host:port). No tokens, no PINs,
 * no credentials of any kind are ever stored here -- those live in memory only.
 *
 * Default points at the known PlexServer LAN address. The user can change it
 * any time on the Settings screen; the change takes effect on the next API call
 * because the Retrofit base URL is read dynamically (see ApiClient).
 */
class ServerPrefs(context: Context) {

    private val prefs: SharedPreferences =
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    private val _baseUrlFlow = MutableStateFlow(getBaseUrl())
    val baseUrlFlow: StateFlow<String> = _baseUrlFlow

    /** Returns a normalized base URL ending in a single trailing slash. */
    fun getBaseUrl(): String {
        val raw = prefs.getString(KEY_SERVER, DEFAULT_SERVER) ?: DEFAULT_SERVER
        return normalize(raw)
    }

    /** Returns the raw stored value for display/editing in Settings. */
    fun getRawServer(): String =
        prefs.getString(KEY_SERVER, DEFAULT_SERVER) ?: DEFAULT_SERVER

    fun setServer(value: String) {
        val cleaned = value.trim()
        prefs.edit().putString(KEY_SERVER, cleaned).apply()
        _baseUrlFlow.value = normalize(cleaned)
    }

    companion object {
        private const val PREFS_NAME = "watchdawg_server"
        private const val KEY_SERVER = "server_address"

        // PlexServer LAN address confirmed for first build.
        const val DEFAULT_SERVER = "192.168.50.42:6868"

        /**
         * Accepts forms like:
         *   "192.168.50.42:6868"
         *   "http://192.168.50.42:6868"
         *   "http://192.168.50.42:6868/"
         * and returns "http://192.168.50.42:6868/" (scheme + single trailing /).
         */
        fun normalize(input: String): String {
            var s = input.trim()
            if (s.isEmpty()) s = DEFAULT_SERVER
            if (!s.startsWith("http://") && !s.startsWith("https://")) {
                s = "http://$s"
            }
            if (!s.endsWith("/")) s = "$s/"
            return s
        }
    }
}
