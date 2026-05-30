package com.watchdawg.tv.ui.settings

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.tv.material3.Button
import androidx.tv.material3.ButtonDefaults
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import com.watchdawg.tv.Graph
import com.watchdawg.tv.data.prefs.ServerPrefs
import com.watchdawg.tv.ui.theme.WatchDawgColors
import com.watchdawg.tv.ui.theme.focusGlow

/**
 * Settings screen — server address entry.
 *
 * Session 25: focusGlow() on the Save button; text field already shows an
 * orange border when focused (existing behaviour), which provides sufficient
 * glow-equivalent affordance for the text input.
 */
@Composable
fun SettingsScreen(modifier: Modifier = Modifier) {
    val prefs: ServerPrefs = Graph.serverPrefs
    var value       by remember { mutableStateOf(prefs.getRawServer()) }
    var saved       by remember { mutableStateOf(false) }
    var fieldFocused by remember { mutableStateOf(false) }

    Column(
        modifier = modifier
            .fillMaxSize()
            .padding(end = 32.dp, top = 28.dp),
    ) {
        Text(
            text  = "Settings",
            style = MaterialTheme.typography.displayLarge,
            color = WatchDawgColors.TextPrimary,
        )
        Text(
            text     = "Point WatchDawg at your server",
            style    = MaterialTheme.typography.bodyLarge,
            color    = WatchDawgColors.TextSecondary,
            modifier = Modifier.padding(top = 4.dp, bottom = 28.dp),
        )

        Text(
            text  = "Server address",
            style = MaterialTheme.typography.titleMedium,
            color = WatchDawgColors.TextSecondary,
        )
        Spacer(Modifier.height(8.dp))

        // Text field — orange border on focus acts as the glow equivalent
        androidx.compose.foundation.layout.Box(
            modifier = Modifier
                .width(560.dp)
                .clip(MaterialTheme.shapes.medium)
                .background(WatchDawgColors.Surface)
                .border(
                    width  = 2.dp,
                    color  = if (fieldFocused) WatchDawgColors.Orange else WatchDawgColors.TextTertiary,
                    shape  = MaterialTheme.shapes.medium,
                )
                .padding(horizontal = 18.dp, vertical = 16.dp),
        ) {
            BasicTextField(
                value          = value,
                onValueChange  = { value = it; saved = false },
                singleLine     = true,
                textStyle      = TextStyle(
                    color    = WatchDawgColors.TextPrimary,
                    fontSize = MaterialTheme.typography.titleMedium.fontSize,
                ),
                cursorBrush    = SolidColor(WatchDawgColors.Orange),
                keyboardOptions = KeyboardOptions(
                    keyboardType = KeyboardType.Uri,
                    imeAction    = ImeAction.Done,
                ),
                keyboardActions = KeyboardActions(onDone = {
                    prefs.setServer(value); saved = true
                }),
                modifier = Modifier
                    .fillMaxWidth()
                    .onFocusChanged { fieldFocused = it.isFocused },
            )
        }

        Text(
            text     = "Example: 192.168.50.42:6868  (http:// is added automatically)",
            style    = MaterialTheme.typography.bodyMedium,
            color    = WatchDawgColors.TextTertiary,
            modifier = Modifier.padding(top = 8.dp),
        )

        Spacer(Modifier.height(24.dp))

        Row(verticalAlignment = Alignment.CenterVertically) {
            Button(
                onClick  = { prefs.setServer(value); saved = true },
                colors   = ButtonDefaults.colors(
                    containerColor        = WatchDawgColors.Orange,
                    contentColor          = WatchDawgColors.Background,
                    focusedContainerColor = WatchDawgColors.OrangeBright,
                    focusedContentColor   = WatchDawgColors.Background,
                ),
                modifier = Modifier.focusGlow(),
            ) {
                Text("Save", style = MaterialTheme.typography.titleMedium)
            }

            if (saved) {
                Spacer(Modifier.width(16.dp))
                Text(
                    text  = "✓  Saved",
                    style = MaterialTheme.typography.bodyLarge,
                    color = WatchDawgColors.ResolvedBadge,
                )
            }
        }

        Spacer(Modifier.height(32.dp))

        Text(
            text  = "App version: Session 25",
            style = MaterialTheme.typography.bodySmall,
            color = WatchDawgColors.TextTertiary,
        )
    }
}
