package com.watchdawg.tv.ui.auth

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.watchdawg.tv.data.auth.TokenHolder
import com.watchdawg.tv.data.repo.WatchDawgRepository
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/**
 * Drives the hidden PIN pad. All credential handling is in-memory only:
 * a successful unlock stores the returned token in [TokenHolder] (never disk),
 * and lock() / app teardown clears it.
 *
 * The backend may or may not have a PIN configured; authStatus() tells us
 * whether the lock feature is even enabled so the pad can message accordingly.
 */
class PinViewModel(private val repo: WatchDawgRepository) : ViewModel() {

    data class UiState(
        val pinLockEnabled: Boolean = false,
        val isUnlocked: Boolean = false,
        val checking: Boolean = false,
        val message: String? = null,      // transient feedback ("Unlocked", "Wrong PIN")
        val messageIsError: Boolean = false,
    )

    private val _state = MutableStateFlow(UiState(isUnlocked = TokenHolder.isUnlocked))
    val state: StateFlow<UiState> = _state.asStateFlow()

    /** Pull current lock status from the backend (called when the pad opens). */
    fun refreshStatus() {
        viewModelScope.launch {
            repo.authStatus().onSuccess { status ->
                _state.value = _state.value.copy(
                    pinLockEnabled = status.pinLockEnabled,
                    // Trust our in-memory token for "unlocked" -- the backend's
                    // is_unlocked is keyed to the token we send, so they agree.
                    isUnlocked = TokenHolder.isUnlocked || status.isUnlocked,
                )
            }
        }
    }

    fun submitPin(pin: String) {
        if (pin.isBlank()) return
        viewModelScope.launch {
            _state.value = _state.value.copy(checking = true, message = null)
            repo.unlock(pin)
                .onSuccess { ok ->
                    if (ok && TokenHolder.isUnlocked) {
                        _state.value = _state.value.copy(
                            checking = false,
                            isUnlocked = true,
                            message = "Unlocked",
                            messageIsError = false,
                        )
                    } else {
                        _state.value = _state.value.copy(
                            checking = false,
                            isUnlocked = TokenHolder.isUnlocked,
                            message = "Incorrect PIN",
                            messageIsError = true,
                        )
                    }
                }
                .onFailure {
                    _state.value = _state.value.copy(
                        checking = false,
                        message = it.message ?: "Could not reach server",
                        messageIsError = true,
                    )
                }
        }
    }

    fun lock() {
        // Session 26 fix: clear the in-memory token SYNCHRONOUSLY here, before
        // launching the coroutine. This is safe because TokenHolder is just an
        // in-memory StateFlow — no I/O, no suspend needed.
        //
        // Root cause of the bug: the Lock Now button in PinPadOverlay calls
        //   viewModel.lock(); onDismiss(false)
        // lock() launches a coroutine, so TokenHolder.clear() runs AFTER onDismiss
        // fires. MainActivity's onDismiss checks TokenHolder.isUnlocked to decide
        // whether to call onSessionLocked() vs onSessionUnlocked() — but the token
        // is still present at that instant, so onSessionUnlocked() fires instead of
        // onSessionLocked(), and adult thumbnails stay visible after locking.
        //
        // By clearing the token here synchronously, TokenHolder.isUnlocked is false
        // by the time onDismiss evaluates it, and onSessionLocked() correctly fires.
        // The coroutine still runs afterward to notify the backend endpoint.
        TokenHolder.clear()

        viewModelScope.launch {
            _state.value = _state.value.copy(checking = true, message = null)
            repo.lock()
                .onSuccess {
                    _state.value = _state.value.copy(
                        checking = false,
                        isUnlocked = false,
                        message = "Locked",
                        messageIsError = false,
                    )
                }
                .onFailure {
                    // lock() clears the token in the repo's finally block regardless,
                    // so locally we are locked even if the network call failed.
                    _state.value = _state.value.copy(
                        checking = false,
                        isUnlocked = TokenHolder.isUnlocked,
                        message = "Locked",
                        messageIsError = false,
                    )
                }
        }
    }

    fun clearMessage() {
        _state.value = _state.value.copy(message = null)
    }
}
