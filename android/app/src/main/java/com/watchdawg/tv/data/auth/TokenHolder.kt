package com.watchdawg.tv.data.auth

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

/**
 * Holds the PIN session token IN MEMORY ONLY.
 *
 * By deliberate design (mirrors the backend's in-memory token set and the
 * browser UI's JS-memory-only storage):
 *  - The token is NEVER written to SharedPreferences, files, or any disk store.
 *  - It is cleared on app process death and explicitly on MainActivity.onDestroy.
 *  - There is no "remember me". Closing the app re-locks everything.
 *
 * The visible PIN entry UI (hidden long-press number pad) is a later session.
 * This holder is the drop-in plumbing so that feature is purely additive.
 */
object TokenHolder {

    private val _token = MutableStateFlow<String?>(null)
    val tokenFlow: StateFlow<String?> = _token

    val token: String? get() = _token.value

    val isUnlocked: Boolean get() = _token.value != null

    fun set(newToken: String?) {
        _token.value = newToken
    }

    fun clear() {
        _token.value = null
    }
}
