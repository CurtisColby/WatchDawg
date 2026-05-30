package com.watchdawg.tv.ui.library

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.watchdawg.tv.data.api.LibraryFileDto
import com.watchdawg.tv.data.auth.TokenHolder
import com.watchdawg.tv.data.repo.WatchDawgRepository
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/**
 * Loads the favorites list, supports remove + Play All / Shuffle All.
 *
 * Token-awareness: subscribes to TokenHolder.tokenFlow and re-fetches on
 * every lock/unlock so the list immediately reflects the filtered content
 * (locked-channel favorites hidden when unauthenticated).
 *
 * Remove: calls DELETE /favorite/{id} which also deletes the physical file
 * if download_status=complete, keeping Library in sync with no orphan cards.
 */
/** Loads the recursive library scan and supports Play All / Shuffle All. */
class LibraryViewModel(private val repo: WatchDawgRepository) : ViewModel() {

    data class UiState(
        val loading: Boolean = true,
        val files: List<LibraryFileDto> = emptyList(),
        val lockedHidden: Boolean = false,
        val error: String? = null,
        val pendingQueue: List<String>? = null,
    )

    private val _state = MutableStateFlow(UiState())
    val state: StateFlow<UiState> = _state.asStateFlow()

    init {
        refresh()
        viewModelScope.launch {
            TokenHolder.tokenFlow.collect { refresh() }
        }
    }

    fun refresh() {
        viewModelScope.launch {
            _state.value = _state.value.copy(loading = true, error = null)
            repo.getLibrary()
                .onSuccess {
                    _state.value = UiState(
                        loading = false,
                        files = it.files,
                        lockedHidden = it.lockedHidden,
                    )
                }
                .onFailure {
                    _state.value = _state.value.copy(
                        loading = false, error = it.message ?: "Could not load library.",
                    )
                }
        }
    }

    private fun playableUrls(): List<String> =
        _state.value.files.mapNotNull { it.streamUrl?.takeIf { u -> u.isNotBlank() } }

    fun playAll() {
        val urls = playableUrls()
        if (urls.isNotEmpty()) _state.value = _state.value.copy(pendingQueue = urls)
    }

    fun shuffleAll() {
        val urls = playableUrls().shuffled()
        if (urls.isNotEmpty()) _state.value = _state.value.copy(pendingQueue = urls)
    }

    fun clearPendingQueue() {
        _state.value = _state.value.copy(pendingQueue = null)
    }
}

