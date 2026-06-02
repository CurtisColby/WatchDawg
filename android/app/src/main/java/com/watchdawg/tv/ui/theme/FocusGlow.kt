package com.watchdawg.tv.ui.theme

import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Paint
import androidx.compose.ui.graphics.drawscope.drawIntoCanvas
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp

/**
 * Draws a soft orange glow behind a composable when it has D-pad focus.
 *
 * Usage on Buttons, ListItems, and any element that manages its own focus:
 *   Button(modifier = Modifier.focusGlow()) { … }
 *   ListItem(modifier = Modifier.focusGlow()) { … }
 *
 * This version attaches its own onFocusChanged listener to track focus state.
 * Safe for Buttons and ListItems where focus is managed by the composable itself
 * and no extra focus node is introduced.
 *
 * For Cards (tv.material3.Card) use focusGlowCard(isFocused) instead — Cards
 * already track focus via their own onFocusChanged and adding a second one
 * creates an extra focus node that causes D-pad Right to require two presses
 * to jump to the sibling Remove/Upgrade button.
 */
fun Modifier.focusGlow(
    glowColor: Color = Color(0xFFFF7A18),
    glowRadius: Dp = 18.dp,
    alpha: Float = 0.55f,
): Modifier {
    var focused = false
    return this
        .onFocusChanged { focused = it.isFocused }
        .drawBehind {
            if (!focused) return@drawBehind
            drawGlowBehind(glowColor, glowRadius.toPx(), alpha)
        }
}

/**
 * Glow variant for Cards — takes the focused state directly instead of
 * attaching its own onFocusChanged listener.
 *
 * Cards (tv.material3.Card / FavoriteRow / WatchLaterRow / LibraryCard) already
 * track focus via Modifier.onFocusChanged { focused = it.isFocused } on the
 * Card modifier. Adding a SECOND onFocusChanged via focusGlow() creates an
 * extra internal focus node that Compose TV's directional focus resolver treats
 * as an additional stop — causing D-pad Right to require two presses to reach
 * the sibling Remove button.
 *
 * Usage:
 *   var focused by remember { mutableStateOf(false) }
 *   Card(
 *       modifier = Modifier
 *           .onFocusChanged { focused = it.isFocused }
 *           .focusGlowCard(focused)
 *   )
 */
fun Modifier.focusGlowCard(
    isFocused: Boolean,
    glowColor: Color = Color(0xFFFF7A18),
    glowRadius: Dp = 24.dp,
    alpha: Float = 0.48f,
): Modifier = this.drawBehind {
    if (!isFocused) return@drawBehind
    drawGlowBehind(glowColor, glowRadius.toPx(), alpha)
}

/**
 * Internal helper — draws the actual blur halo using Android Paint BlurMaskFilter.
 * Called from both focusGlow and focusGlowCard drawBehind blocks.
 */
private fun androidx.compose.ui.graphics.drawscope.DrawScope.drawGlowBehind(
    glowColor: Color,
    radiusPx: Float,
    alpha: Float,
) {
    val glowPaint = Paint().apply {
        asFrameworkPaint().apply {
            isAntiAlias = true
            color = android.graphics.Color.TRANSPARENT
            setShadowLayer(
                radiusPx,
                0f,
                0f,
                glowColor.copy(alpha = alpha).toArgb(),
            )
        }
    }
    drawIntoCanvas { canvas ->
        canvas.drawRoundRect(
            left    = -radiusPx * 0.5f,
            top     = -radiusPx * 0.5f,
            right   = size.width  + radiusPx * 0.5f,
            bottom  = size.height + radiusPx * 0.5f,
            radiusX = radiusPx * 0.4f,
            radiusY = radiusPx * 0.4f,
            paint   = glowPaint,
        )
    }
}
