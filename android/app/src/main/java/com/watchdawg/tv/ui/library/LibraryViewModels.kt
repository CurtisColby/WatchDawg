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
 * Loads the local library and supports Play All / Shuffle All / Remove.
 *
 * R-4 content model change:
 *   Local screen now shows ONLY Public subfolder files regardless of PIN state.
 *   Private subfolder files live exclusively on the Adult screen → Local pill.
 *
 *   The backend already enforces the token gate (locked → Public/ scan only,
 *   unlocked → both). We add a client-side filter to subfolder == "Public" so
 *   that even when unlocked, Private files stay off this screen entirely.
 *
 *   This means Local is now PIN-agnostic — it always shows the same content
 *   (public downloads) regardless of whether the PIN has been entered.
 *
 * Token awareness kept:
 *   Still subscribes to TokenHolder.tokenFlow and refreshes, because the
 *   backend scan root changes on lock/unlock and we want the file list to
 *   stay current. The filter ensures only Public files ever appear here.
 *
 * Remove:
 *   Calls DELETE /library/file?relative_path=X which deletes the physical file,
 *   cleans up the DB favorite record, and adds to the skip list.
 *   Shows a confirmation dialog in LibraryScreen before executing.
 */
class LibraryViewModel(private val repo: WatchDawgRepository) : ViewModel() {

    data class UiState(
        val loading: Boolean = true,
        val files: List<LibraryFileDto> = emptyList(),
        val error: String? = null,
        val pendingQueue: List<String>? = null,
        val removingPaths: Set<String> = emptySet(),
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
                .onSuccess { response ->
                    // R-4: filter to Public subfolder only.
                    // Private files are shown exclusively on Adult → Local pill.
                    val publicFiles = response.files.filter { it.subfolder == "Public" }
                    _state.value = _state.value.copy(
                        loading = false,
                        files   = publicFiles,
                    )
                }
                .onFailure { e ->
                    _state.value = _state.value.copy(
                        loading = false,
                        error   = e.message ?: "Could not load library.",
                    )
                }
        }
    }

    /**
     * Delete a file from the NAS via DELETE /library/file?relative_path=X.
     * The backend handles: physical file deletion, DB cleanup, skip list entry.
     * Optimistically removes from the list immediately; refreshes on completion.
     */
    fun removeFile(relativePath: String) {
        if (_state.value.removingPaths.contains(relativePath)) return
        viewModelScope.launch {
            _state.value = _state.value.copy(
                files         = _state.value.files.filterNot { it.relativePath == relativePath },
                removingPaths = _state.value.removingPaths + relativePath,
            )
            repo.deleteLibraryFile(relativePath)
                .onSuccess {
                    _state.value = _state.value.copy(
                        removingPaths = _state.value.removingPaths - relativePath,
                    )
                }
                .onFailure {
                    _state.value = _state.value.copy(
                        removingPaths = _state.value.removingPaths - relativePath,
                    )
                    refresh()
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
