package com.watchdawg.tv.ui.home

import androidx.lifecycle.ViewModel
import com.watchdawg.tv.data.auth.TokenHolder
import kotlinx.coroutines.flow.StateFlow

/**
 * ViewModel for HomeScreen — Milestone R-2.5.
 *
 * Deliberately lightweight. The Home Screen does not make any network calls
 * on its own — all section data is loaded lazily when the user enters each
 * section. This keeps the Home Screen instant to render.
 *
 * The single responsibility of HomeViewModel is exposing PIN unlock state so
 * HomeScreen can decide whether to render the Adult card.
 *
 * Adult card visibility rule (matches the old NavRail rule exactly):
 *   - [isUnlocked] is null  → Adult card is NOT rendered (structurally absent)
 *   - [isUnlocked] is non-null → Adult card IS rendered
 *
 * Using TokenHolder.tokenFlow directly (not wrapping in a new StateFlow) keeps
 * the logic in one place. HomeScreen collects it via collectAsStateWithLifecycle.
 */
class HomeViewModel : ViewModel() {

    /**
     * Emits the current session token string, or null when locked.
     * HomeScreen checks `isUnlocked != null` to gate the Adult card.
     */
    val isUnlocked: StateFlow<String?> = TokenHolder.tokenFlow
}
