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
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
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
import com.watchdawg.tv.ui.home.APP_VERSION
import com.watchdawg.tv.ui.theme.WatchDawgColors
import com.watchdawg.tv.ui.theme.focusGlow
import kotlinx.coroutines.launch

/**
 * Settings screen — R-4 update.
 *
 * Added: Maintenance section with Generate Local Thumbnails button.
 *   Calls POST /library/generate-thumbnails (limit=50 per run).
 *   Runs ffmpeg frame-grabs for downloaded local files that have no sidecar
 *   thumbnail jpg. Safe to press multiple times — skips files that already
 *   have a thumbnail.
 *
 *   Note: this generates thumbnails for PUBLIC downloaded files only.
 *   Private/adult local file thumbnails are generated from the Adult screen.
 *
 * Updated: version now reads from APP_VERSION const in HomeScreen.kt.
 *   Update APP_VERSION each session/build — one place, reflected here and
 *   in the HomeScreen header badge automatically.
 *
 * Session 25: focusGlow() on the Save button.
 */
@Composable
fun SettingsScreen(modifier: Modifier = Modifier) {
    val prefs: ServerPrefs = Graph.serverPrefs
    val scope = rememberCoroutineScope()

    var value        by remember { mutableStateOf(prefs.getRawServer()) }
    var saved        by remember { mutableStateOf(false) }
    var fieldFocused by remember { mutableStateOf(false) }

    // Thumbnail generation state
    var thumbGenerating by remember { mutableStateOf(false) }
    var thumbResult     by remember { mutableStateOf<String?>(null) }

    Column(
        modifier = modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(end = 32.dp, top = 28.dp, bottom = 48.dp),
    ) {

        // ── Header ────────────────────────────────────────────────────────────
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

        // ── Server address ────────────────────────────────────────────────────
        Text(
            text  = "Server address",
            style = MaterialTheme.typography.titleMedium,
            color = WatchDawgColors.TextSecondary,
        )
        Spacer(Modifier.height(8.dp))

        androidx.compose.foundation.layout.Box(
            modifier = Modifier
                .width(560.dp)
                .clip(MaterialTheme.shapes.medium)
                .background(WatchDawgColors.Surface)
                .border(
                    width = 2.dp,
                    color = if (fieldFocused) WatchDawgColors.Orange else WatchDawgColors.TextTertiary,
                    shape = MaterialTheme.shapes.medium,
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

        Spacer(Modifier.height(40.dp))

        // ── Maintenance ───────────────────────────────────────────────────────
        Text(
            text  = "Maintenance",
            style = MaterialTheme.typography.titleLarge,
            color = WatchDawgColors.TextPrimary,
        )
        Text(
            text     = "Server-side tools for keeping your library in shape",
            style    = MaterialTheme.typography.bodyMedium,
            color    = WatchDawgColors.TextTertiary,
            modifier = Modifier.padding(top = 4.dp, bottom = 16.dp),
        )

        Row(verticalAlignment = Alignment.CenterVertically) {
            Button(
                onClick  = {
                    if (!thumbGenerating) {
                        thumbGenerating = true
                        thumbResult     = null
                        scope.launch {
                            Graph.repository.generateLocalThumbnails()
                                .onSuccess { msg -> thumbResult = "✓  $msg" }
                                .onFailure { thumbResult = "Failed — check server logs" }
                            thumbGenerating = false
                        }
                    }
                },
                enabled  = !thumbGenerating,
                colors   = ButtonDefaults.colors(
                    containerColor         = WatchDawgColors.Surface,
                    contentColor           = WatchDawgColors.TextSecondary,
                    focusedContainerColor  = WatchDawgColors.SurfaceFocused,
                    focusedContentColor    = WatchDawgColors.TextPrimary,
                    disabledContainerColor = WatchDawgColors.Surface,
                    disabledContentColor   = WatchDawgColors.TextTertiary,
                ),
                modifier = Modifier.focusGlow(),
            ) {
                Text(
                    text  = if (thumbGenerating) "⏳  Generating…" else "🖼  Generate Local Thumbnails",
                    style = MaterialTheme.typography.titleSmall,
                )
            }
        }

        Text(
            text     = "Scans downloaded Public files and creates thumbnails for any that are missing.\nSafe to run multiple times — skips files that already have a thumbnail.",
            style    = MaterialTheme.typography.bodySmall,
            color    = WatchDawgColors.TextTertiary,
            modifier = Modifier.padding(top = 8.dp),
        )

        if (thumbResult != null) {
            Spacer(Modifier.height(8.dp))
            Text(
                text  = thumbResult!!,
                style = MaterialTheme.typography.bodyMedium,
                color = if (thumbResult!!.startsWith("✓"))
                    WatchDawgColors.ResolvedBadge
                else
                    WatchDawgColors.FailedBadge,
            )
        }

        Spacer(Modifier.height(40.dp))

        // ── App info ──────────────────────────────────────────────────────────
        Text(
            text  = "App version: $APP_VERSION",
            style = MaterialTheme.typography.bodySmall,
            color = WatchDawgColors.TextTertiary,
        )
    }
}
