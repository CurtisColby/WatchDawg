package com.watchdawg.tv.ui.library

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.watchdawg.tv.Graph
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
 * Session 42: Genre pill filtering added.
 *   - Fetches available genre tags from GET /library/genres on load.
 *   - Selected genre pill calls GET /library?genre=X to filter server-side.
 *   - "All" pill clears the filter and loads all files.
 *   - Pill selection state lives in ViewModel so it survives recomposition.
 *
 * R-4 content model:
 *   Shows ONLY Public subfolder files. Private files live on Adult → Local pill.
 *   PIN-agnostic — content is always the same regardless of lock state.
 */
class LibraryViewModel(private val repo: WatchDawgRepository) : ViewModel() {

    data class UiState(
        val loading: Boolean = true,
        val files: List<LibraryFileDto> = emptyList(),
        val genres: List<String> = emptyList(),
        val selectedGenre: String? = null,
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
        val genre = _state.value.selectedGenre
        viewModelScope.launch {
            _state.value = _state.value.copy(loading = true, error = null)

            // Fetch genres on first load (or if empty)
            if (_state.value.genres.isEmpty()) {
                repo.getLibraryGenres()
                    .onSuccess { tags -> _state.value = _state.value.copy(genres = tags) }
            }

            repo.getLibrary(genre = genre)
                .onSuccess { response ->
                    // R-4: filter to Public subfolder only.
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
     * Session 42: Select a genre pill to filter the library.
     * null = show all files.
     */
    fun selectGenre(genre: String?) {
        _state.value = _state.value.copy(selectedGenre = genre)
        refresh()
    }

    /**
     * Delete a file from the NAS via DELETE /library/file?relative_path=X.
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
