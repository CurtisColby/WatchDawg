package com.watchdawg.tv.ui.auth

import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.composed
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEvent
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type

/**
 * Watches for the hidden unlock code (Up, Up, Down, Down) on ANY screen.
 *
 * The [enabled] flag must be set to false when the PIN pad is already visible.
 * If left enabled while the overlay is open, onPreviewKeyEvent on the root Box
 * intercepts every key before the PIN pad buttons can receive them, preventing
 * the pad from ever getting focus or responding to D-pad input.
 */
fun Modifier.globalUnlockGesture(
    enabled: Boolean = true,
    onTriggered: () -> Unit,
): Modifier = composed {
    if (!enabled) return@composed this

    val pattern = remember {
        listOf(Key.DirectionUp, Key.DirectionUp, Key.DirectionDown, Key.DirectionDown)
    }
    val progress = remember { IntArray(1) { 0 } }
    val lastKeyTime = remember { LongArray(1) { 0L } }
    val resetWindowMs = 1500L

    onPreviewKeyEvent { event: KeyEvent ->
        if (event.type != KeyEventType.KeyUp) return@onPreviewKeyEvent false

        val now = System.currentTimeMillis()
        if (now - lastKeyTime[0] > resetWindowMs) progress[0] = 0
        lastKeyTime[0] = now

        val expected = pattern[progress[0]]
        if (event.key == expected) {
            progress[0] += 1
            if (progress[0] == pattern.size) {
                progress[0] = 0
                onTriggered()
                true
            } else {
                false
            }
        } else {
            progress[0] = if (event.key == pattern[0]) 1 else 0
            false
        }
    }
}
