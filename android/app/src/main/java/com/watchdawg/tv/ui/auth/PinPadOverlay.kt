package com.watchdawg.tv.ui.auth

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.tv.material3.Button
import androidx.tv.material3.ButtonDefaults
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import com.watchdawg.tv.ui.theme.WatchDawgColors
import com.watchdawg.tv.ui.theme.focusGlow
import kotlinx.coroutines.delay

@Composable
fun PinPadOverlay(
    viewModel: PinViewModel,
    onDismiss: (wasUnlocked: Boolean) -> Unit,
) {
    val state by viewModel.state.collectAsState()
    var entered by remember { mutableStateOf("") }
    val firstButtonFocus = remember { FocusRequester() }

    LaunchedEffect(Unit) {
        viewModel.refreshStatus()
        try { firstButtonFocus.requestFocus() } catch (_: Exception) {}
        delay(150)
        try { firstButtonFocus.requestFocus() } catch (_: Exception) {}
    }

    LaunchedEffect(state.message) {
        val msg = state.message
        if (msg != null && !state.messageIsError) {
            delay(900)
            viewModel.clearMessage()
            onDismiss(state.isUnlocked)
        }
    }

    BackHandler { onDismiss(state.isUnlocked) }

    Box(
        modifier         = Modifier
            .fillMaxSize()
            .background(Color(0xE60A0A0E)),
        contentAlignment = Alignment.Center,
    ) {
        Column(
            modifier              = Modifier
                .width(460.dp)
                .clip(MaterialTheme.shapes.large)
                .background(WatchDawgColors.Surface)
                .padding(36.dp),
            horizontalAlignment   = Alignment.CenterHorizontally,
        ) {
            Text(
                text  = if (state.isUnlocked) "Session Unlocked" else "Enter PIN",
                style = MaterialTheme.typography.headlineMedium,
                color = WatchDawgColors.TextPrimary,
            )
            Text(
                text     = if (state.isUnlocked)
                               "Enter PIN again to switch, or press Lock Now."
                           else if (state.pinLockEnabled)
                               "Enter your PIN to unlock adult content."
                           else
                               "No PIN is configured. Set one in the web UI.",
                style    = MaterialTheme.typography.bodyMedium,
                color    = WatchDawgColors.TextSecondary,
                modifier = Modifier.padding(top = 6.dp, bottom = 24.dp),
            )

            // PIN dot display
            Row(
                horizontalArrangement = Arrangement.spacedBy(12.dp),
                modifier              = Modifier.padding(bottom = 28.dp),
            ) {
                repeat(6) { idx ->
                    Box(
                        modifier = Modifier
                            .size(18.dp)
                            .clip(MaterialTheme.shapes.small)
                            .background(
                                if (idx < entered.length) WatchDawgColors.Orange
                                else WatchDawgColors.SurfaceElevated,
                            ),
                    )
                }
            }

            Column(
                verticalArrangement = Arrangement.spacedBy(10.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
                modifier            = Modifier.fillMaxWidth(),
            ) {
                val onDigit: (String) -> Unit = { d -> if (entered.length < 6) entered += d }
                val onClear: () -> Unit       = { entered = "" }
                val onEnter: () -> Unit       = {
                    if (entered.isNotEmpty()) {
                        viewModel.submitPin(entered)
                        entered = ""
                    }
                }

                PinRow("1", "2", "3", firstFocus = firstButtonFocus,
                    onDigit = onDigit, onClear = onClear, onEnter = onEnter)
                PinRow("4", "5", "6",
                    onDigit = onDigit, onClear = onClear, onEnter = onEnter)
                PinRow("7", "8", "9",
                    onDigit = onDigit, onClear = onClear, onEnter = onEnter)
                PinRow("CLR", "0", "OK",
                    onDigit = onDigit, onClear = onClear, onEnter = onEnter)
            }

            if (state.message != null) {
                Spacer(Modifier.height(20.dp))
                Text(
                    text  = state.message!!,
                    style = MaterialTheme.typography.bodyMedium,
                    // FailedBadge = red, ResolvedBadge = green — correct color names
                    color = if (state.messageIsError) WatchDawgColors.FailedBadge
                            else WatchDawgColors.ResolvedBadge,
                )
            }

            if (state.isUnlocked) {
                Spacer(Modifier.height(20.dp))
                Button(
                    onClick  = { viewModel.lock(); onDismiss(false) },
                    colors   = ButtonDefaults.colors(
                        containerColor        = WatchDawgColors.OrangeDim,
                        contentColor          = WatchDawgColors.Orange,
                        focusedContainerColor = WatchDawgColors.Orange,
                        focusedContentColor   = WatchDawgColors.Background,
                    ),
                    modifier = Modifier.focusGlow(),
                ) {
                    Text("Lock Now", style = MaterialTheme.typography.titleMedium)
                }
            }
        }
    }
}

@Composable
private fun PinRow(
    a: String, b: String, c: String,
    firstFocus: FocusRequester? = null,
    onDigit: (String) -> Unit,
    onClear: () -> Unit,
    onEnter: () -> Unit,
) {
    Row(
        horizontalArrangement = Arrangement.spacedBy(10.dp),
        modifier              = Modifier.fillMaxWidth(),
    ) {
        listOf(a, b, c).forEachIndexed { idx, label ->
            val isOk    = label == "OK"
            val isClear = label == "CLR"
            Button(
                onClick  = {
                    when {
                        isOk    -> onEnter()
                        isClear -> onClear()
                        else    -> onDigit(label)
                    }
                },
                colors   = ButtonDefaults.colors(
                    containerColor        = if (isOk) WatchDawgColors.OrangeDim else WatchDawgColors.SurfaceElevated,
                    contentColor          = WatchDawgColors.TextPrimary,
                    focusedContainerColor = if (isOk) WatchDawgColors.Orange else WatchDawgColors.SurfaceFocused,
                    focusedContentColor   = if (isOk) WatchDawgColors.Background else WatchDawgColors.TextPrimary,
                ),
                modifier = Modifier
                    .weight(1f)
                    .height(60.dp)
                    .focusGlow(
                        glowRadius = if (isOk) 22.dp else 16.dp,
                    )
                    .then(
                        if (idx == 0 && firstFocus != null)
                            Modifier.focusRequester(firstFocus)
                        else Modifier
                    ),
            ) {
                Text(text = label, style = MaterialTheme.typography.titleLarge)
            }
        }
    }
}
