package com.watchdawg.tv.ui.home

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.size
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.geometry.RoundRect
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp

/**
 * Custom neon-style Canvas icons for the WatchDawg Home Screen.
 *
 * Each icon is a standalone @Composable that draws directly via Canvas.
 * No asset files, no VectorDrawable XML, no painterResource() calls.
 * Scales perfectly at any DPI.
 *
 * All icons share:
 *   - Dark transparent background (card provides the surface)
 *   - Colored stroke lines with glow effect (drawn as wide + narrow overlapping strokes)
 *   - Unique accent color per section
 *   - isFocused: when true, glow alpha increases for a "lit up" effect
 */

// ── Shared glow helper ────────────────────────────────────────────────────────

private fun DrawScope.glowStroke(
    path: Path,
    color: Color,
    strokeWidth: Float,
    isFocused: Boolean,
    cap: StrokeCap = StrokeCap.Round,
    join: StrokeJoin = StrokeJoin.Round,
) {
    val glowAlpha  = if (isFocused) 0.45f else 0.22f
    val glowWidth  = strokeWidth * 3.5f
    // Outer glow
    drawPath(
        path   = path,
        color  = color.copy(alpha = glowAlpha),
        style  = Stroke(width = glowWidth, cap = cap, join = join),
    )
    // Inner crisp line
    drawPath(
        path  = path,
        color = color.copy(alpha = if (isFocused) 1f else 0.85f),
        style = Stroke(width = strokeWidth, cap = cap, join = join),
    )
}

private fun DrawScope.glowCircle(
    center: Offset,
    radius: Float,
    color: Color,
    strokeWidth: Float,
    isFocused: Boolean,
) {
    val glowAlpha = if (isFocused) 0.45f else 0.22f
    drawCircle(color = color.copy(alpha = glowAlpha), radius = radius + strokeWidth * 1.5f, center = center, style = Stroke(strokeWidth * 3f))
    drawCircle(color = color.copy(alpha = if (isFocused) 1f else 0.85f), radius = radius, center = center, style = Stroke(strokeWidth))
}

private fun DrawScope.glowLine(
    start: Offset,
    end: Offset,
    color: Color,
    strokeWidth: Float,
    isFocused: Boolean,
) {
    val glowAlpha = if (isFocused) 0.45f else 0.22f
    drawLine(color = color.copy(alpha = glowAlpha), start = start, end = end, strokeWidth = strokeWidth * 3.5f, cap = StrokeCap.Round)
    drawLine(color = color.copy(alpha = if (isFocused) 1f else 0.85f), start = start, end = end, strokeWidth = strokeWidth, cap = StrokeCap.Round)
}

// ── TV icon — classic television set with antenna ────────────────────────────

@Composable
fun TvIcon(isFocused: Boolean, size: Dp = 52.dp) {
    val color = Color(0xFF4FC3F7) // light blue
    Canvas(modifier = Modifier.size(size)) {
        val w = this.size.width
        val h = this.size.height
        val sw = w * 0.06f

        // TV body
        val bodyPath = Path().apply {
            val bodyLeft   = w * 0.08f
            val bodyTop    = h * 0.28f
            val bodyRight  = w * 0.92f
            val bodyBottom = h * 0.82f
            val cr = w * 0.1f
            addRoundRect(RoundRect(Rect(bodyLeft, bodyTop, bodyRight, bodyBottom), CornerRadius(cr, cr)))
        }
        glowStroke(bodyPath, color, sw, isFocused)

        // Screen inset
        val screenPath = Path().apply {
            val sl = w * 0.17f
            val st = h * 0.36f
            val sr = w * 0.83f
            val sb = h * 0.74f
            val cr = w * 0.05f
            addRoundRect(RoundRect(Rect(sl, st, sr, sb), CornerRadius(cr, cr)))
        }
        glowStroke(screenPath, color.copy(alpha = if (isFocused) 0.7f else 0.45f), sw * 0.7f, isFocused)

        // Left antenna
        glowLine(Offset(w * 0.35f, h * 0.28f), Offset(w * 0.22f, h * 0.06f), color, sw * 0.8f, isFocused)
        // Right antenna
        glowLine(Offset(w * 0.65f, h * 0.28f), Offset(w * 0.78f, h * 0.06f), color, sw * 0.8f, isFocused)

        // Stand legs
        glowLine(Offset(w * 0.38f, h * 0.82f), Offset(w * 0.30f, h * 0.96f), color, sw * 0.7f, isFocused)
        glowLine(Offset(w * 0.62f, h * 0.82f), Offset(w * 0.70f, h * 0.96f), color, sw * 0.7f, isFocused)
    }
}

// ── Movies icon — film clapperboard ──────────────────────────────────────────

@Composable
fun MoviesIcon(isFocused: Boolean, size: Dp = 52.dp) {
    val color = Color(0xFFCE93D8) // soft purple
    Canvas(modifier = Modifier.size(size)) {
        val w = this.size.width
        val h = this.size.height
        val sw = w * 0.06f

        // Board body
        val bodyPath = Path().apply {
            val cr = w * 0.08f
            addRoundRect(RoundRect(Rect(w * 0.08f, h * 0.34f, w * 0.92f, h * 0.90f), CornerRadius(cr, cr)))
        }
        glowStroke(bodyPath, color, sw, isFocused)

        // Top clapper base (attached to body)
        val clapBasePath = Path().apply {
            val cr = w * 0.06f
            addRoundRect(RoundRect(Rect(w * 0.08f, h * 0.18f, w * 0.92f, h * 0.36f), CornerRadius(cr, cr)))
        }
        glowStroke(clapBasePath, color, sw, isFocused)

        // Clapper stripes (diagonal lines on the top bar)
        val stripeColor = color.copy(alpha = if (isFocused) 0.8f else 0.55f)
        val stripeW = sw * 0.7f
        listOf(0.22f, 0.38f, 0.54f, 0.70f).forEach { xFrac ->
            glowLine(
                Offset(w * xFrac, h * 0.18f),
                Offset(w * (xFrac + 0.10f), h * 0.36f),
                stripeColor, stripeW, isFocused,
            )
        }

        // Horizontal divider line on body
        glowLine(Offset(w * 0.08f, h * 0.56f), Offset(w * 0.92f, h * 0.56f), color.copy(alpha = 0.45f), sw * 0.6f, isFocused)

        // Play triangle inside body
        val triPath = Path().apply {
            moveTo(w * 0.38f, h * 0.62f)
            lineTo(w * 0.38f, h * 0.82f)
            lineTo(w * 0.68f, h * 0.72f)
            close()
        }
        glowStroke(triPath, color, sw * 0.75f, isFocused, join = StrokeJoin.Round)
    }
}

// ── Live TV icon — broadcast tower with signal arcs ──────────────────────────

@Composable
fun LiveTvIcon(isFocused: Boolean, size: Dp = 52.dp) {
    val color = Color(0xFFEF5350) // red
    Canvas(modifier = Modifier.size(size)) {
        val w = this.size.width
        val h = this.size.height
        val sw = w * 0.06f
        val cx = w * 0.50f
        val towerTop = h * 0.42f

        // Tower mast
        glowLine(Offset(cx, towerTop), Offset(cx, h * 0.92f), color, sw, isFocused)

        // Tower cross arms
        glowLine(Offset(cx - w * 0.14f, h * 0.58f), Offset(cx + w * 0.14f, h * 0.58f), color, sw * 0.75f, isFocused)
        glowLine(Offset(cx - w * 0.09f, h * 0.70f), Offset(cx + w * 0.09f, h * 0.70f), color, sw * 0.6f, isFocused)

        // Signal arcs — 3 expanding arcs above tower tip
        val arcCenter = Offset(cx, towerTop)
        listOf(
            Triple(w * 0.12f, 0.75f, 200f),
            Triple(w * 0.21f, 0.55f, 200f),
            Triple(w * 0.30f, 0.35f, 200f),
        ).forEach { (radius, alpha, sweep) ->
            val a = if (isFocused) (alpha + 0.25f).coerceAtMost(1f) else alpha
            drawArc(
                color      = color.copy(alpha = a * 0.3f),
                startAngle = -90f - sweep / 2f,
                sweepAngle = sweep,
                useCenter  = false,
                topLeft    = Offset(arcCenter.x - radius, arcCenter.y - radius),
                size       = Size(radius * 2f, radius * 2f),
                style      = Stroke(width = sw * 3f, cap = StrokeCap.Round),
            )
            drawArc(
                color      = color.copy(alpha = a),
                startAngle = -90f - sweep / 2f,
                sweepAngle = sweep,
                useCenter  = false,
                topLeft    = Offset(arcCenter.x - radius, arcCenter.y - radius),
                size       = Size(radius * 2f, radius * 2f),
                style      = Stroke(width = sw * 0.75f, cap = StrokeCap.Round),
            )
        }

        // Live dot at tower tip
        val dotAlpha = if (isFocused) 1f else 0.85f
        drawCircle(color = color.copy(alpha = 0.35f), radius = sw * 2f, center = arcCenter)
        drawCircle(color = color.copy(alpha = dotAlpha), radius = sw * 0.9f, center = arcCenter)
    }
}

// ── Music icon — music note with sound waves ──────────────────────────────────

@Composable
fun MusicIcon(isFocused: Boolean, size: Dp = 52.dp) {
    val color = Color(0xFF66BB6A) // green
    Canvas(modifier = Modifier.size(size)) {
        val w = this.size.width
        val h = this.size.height
        val sw = w * 0.06f

        // Note stem
        glowLine(Offset(w * 0.54f, h * 0.14f), Offset(w * 0.54f, h * 0.72f), color, sw, isFocused)

        // Note flag (curved top)
        val flagPath = Path().apply {
            moveTo(w * 0.54f, h * 0.14f)
            cubicTo(w * 0.82f, h * 0.10f, w * 0.86f, h * 0.36f, w * 0.54f, h * 0.38f)
        }
        glowStroke(flagPath, color, sw, isFocused)

        // Note head (filled circle)
        val headCenter = Offset(w * 0.38f, h * 0.74f)
        val headR = w * 0.12f
        drawCircle(color = color.copy(alpha = if (isFocused) 0.25f else 0.15f), radius = headR * 2.2f, center = headCenter)
        drawCircle(color = color.copy(alpha = if (isFocused) 1f else 0.85f), radius = headR, center = headCenter)

        // Sound waves to the right
        listOf(w * 0.72f to w * 0.10f, w * 0.82f to w * 0.18f).forEachIndexed { i, (cx, r) ->
            val alpha = if (isFocused) 0.75f - i * 0.15f else 0.50f - i * 0.12f
            drawArc(
                color      = color.copy(alpha = alpha * 0.3f),
                startAngle = -50f,
                sweepAngle = 100f,
                useCenter  = false,
                topLeft    = Offset(cx - r, h * 0.38f - r),
                size       = Size(r * 2f, r * 2f),
                style      = Stroke(width = sw * 3f, cap = StrokeCap.Round),
            )
            drawArc(
                color      = color.copy(alpha = alpha),
                startAngle = -50f,
                sweepAngle = 100f,
                useCenter  = false,
                topLeft    = Offset(cx - r, h * 0.38f - r),
                size       = Size(r * 2f, r * 2f),
                style      = Stroke(width = sw * 0.7f, cap = StrokeCap.Round),
            )
        }
    }
}

// ── Continue Watching icon — play button inside progress ring ─────────────────

@Composable
fun ContinueWatchingIcon(isFocused: Boolean, size: Dp = 52.dp) {
    val color = Color(0xFFFF6B35) // WatchDawg orange
    Canvas(modifier = Modifier.size(size)) {
        val w  = this.size.width
        val h  = this.size.height
        val sw = w * 0.06f
        val cx = w * 0.50f
        val cy = h * 0.50f
        val r  = w * 0.38f

        // Background ring (dim track)
        drawCircle(
            color  = color.copy(alpha = if (isFocused) 0.18f else 0.10f),
            radius = r,
            center = Offset(cx, cy),
            style  = Stroke(sw * 1.2f),
        )

        // Progress arc (~70% complete)
        val progressSweep = 252f
        drawArc(
            color      = color.copy(alpha = if (isFocused) 0.35f else 0.18f),
            startAngle = -90f,
            sweepAngle = progressSweep,
            useCenter  = false,
            topLeft    = Offset(cx - r, cy - r),
            size       = Size(r * 2f, r * 2f),
            style      = Stroke(width = sw * 3.5f, cap = StrokeCap.Round),
        )
        drawArc(
            color      = color.copy(alpha = if (isFocused) 1f else 0.85f),
            startAngle = -90f,
            sweepAngle = progressSweep,
            useCenter  = false,
            topLeft    = Offset(cx - r, cy - r),
            size       = Size(r * 2f, r * 2f),
            style      = Stroke(width = sw, cap = StrokeCap.Round),
        )

        // Play triangle inside ring
        val triSize = r * 0.52f
        val triPath = Path().apply {
            moveTo(cx - triSize * 0.55f, cy - triSize * 0.75f)
            lineTo(cx - triSize * 0.55f, cy + triSize * 0.75f)
            lineTo(cx + triSize * 0.85f, cy)
            close()
        }
        glowStroke(triPath, color, sw * 0.75f, isFocused, join = StrokeJoin.Round)
    }
}

// ── Watch Later icon — bookmark with clock ────────────────────────────────────

@Composable
fun WatchLaterIcon(isFocused: Boolean, size: Dp = 52.dp) {
    val color = Color(0xFF4DB6AC) // teal
    Canvas(modifier = Modifier.size(size)) {
        val w  = this.size.width
        val h  = this.size.height
        val sw = w * 0.06f

        // Bookmark shape
        val bmPath = Path().apply {
            val l = w * 0.18f; val r = w * 0.82f
            val t = h * 0.06f; val b = h * 0.94f
            val mid = (l + r) / 2f
            val notch = h * 0.66f
            val cr = w * 0.08f
            moveTo(l + cr, t)
            lineTo(r - cr, t)
            cubicTo(r, t, r, t, r, t + cr)
            lineTo(r, notch)
            lineTo(mid, b - h * 0.08f)
            lineTo(l, notch)
            lineTo(l, t + cr)
            cubicTo(l, t, l, t, l + cr, t)
            close()
        }
        glowStroke(bmPath, color, sw, isFocused)

        // Clock circle inside bookmark
        val cx = w * 0.50f
        val cy = h * 0.38f
        val cr = w * 0.16f
        glowCircle(Offset(cx, cy), cr, color, sw * 0.75f, isFocused)

        // Clock hands
        glowLine(Offset(cx, cy), Offset(cx, cy - cr * 0.65f), color, sw * 0.65f, isFocused) // 12
        glowLine(Offset(cx, cy), Offset(cx + cr * 0.50f, cy + cr * 0.28f), color, sw * 0.65f, isFocused) // ~3
    }
}

// ── Favorites icon — heart ────────────────────────────────────────────────────

@Composable
fun FavoritesIcon(isFocused: Boolean, size: Dp = 52.dp) {
    val color = Color(0xFFF48FB1) // pink
    Canvas(modifier = Modifier.size(size)) {
        val w  = this.size.width
        val h  = this.size.height
        val sw = w * 0.06f

        val heartPath = Path().apply {
            val cx = w * 0.50f
            val top = h * 0.28f
            val bottom = h * 0.88f
            // Left lobe
            moveTo(cx, top + h * 0.10f)
            cubicTo(cx - w * 0.04f, top, cx - w * 0.42f, top - h * 0.04f, cx - w * 0.42f, top + h * 0.18f)
            cubicTo(cx - w * 0.42f, top + h * 0.36f, cx, top + h * 0.50f, cx, bottom)
            // Right lobe
            cubicTo(cx, top + h * 0.50f, cx + w * 0.42f, top + h * 0.36f, cx + w * 0.42f, top + h * 0.18f)
            cubicTo(cx + w * 0.42f, top - h * 0.04f, cx + w * 0.04f, top, cx, top + h * 0.10f)
            close()
        }

        // Filled glow
        drawPath(path = heartPath, color = color.copy(alpha = if (isFocused) 0.22f else 0.10f))
        // Stroke
        glowStroke(heartPath, color, sw, isFocused, join = StrokeJoin.Round)

        // Inner highlight line
        val highlightPath = Path().apply {
            moveTo(w * 0.30f, h * 0.40f)
            cubicTo(w * 0.28f, h * 0.34f, w * 0.22f, h * 0.32f, w * 0.22f, h * 0.46f)
        }
        glowStroke(highlightPath, Color.White.copy(alpha = if (isFocused) 0.55f else 0.28f), sw * 0.55f, isFocused)
    }
}

// ── Local icon — folder with play arrow ──────────────────────────────────────

@Composable
fun LocalIcon(isFocused: Boolean, size: Dp = 52.dp) {
    val color = Color(0xFFFFB74D) // amber
    Canvas(modifier = Modifier.size(size)) {
        val w  = this.size.width
        val h  = this.size.height
        val sw = w * 0.06f

        // Folder body
        val bodyPath = Path().apply {
            val cr = w * 0.08f
            addRoundRect(RoundRect(Rect(w * 0.06f, h * 0.32f, w * 0.94f, h * 0.88f), CornerRadius(cr, cr)))
        }
        glowStroke(bodyPath, color, sw, isFocused)

        // Folder tab (top-left) — rounded top corners only, drawn manually
        // RoundRect with per-corner CornerRadius uses an internal constructor in this
        // Compose version. Use explicit arcTo path instead.
        val tabPath = Path().apply {
            val cr  = w * 0.06f
            val tl  = w * 0.06f; val tr = w * 0.42f
            val top = h * 0.20f; val bot = h * 0.34f
            moveTo(tl, bot)
            lineTo(tl, top + cr)
            arcTo(Rect(tl, top, tl + cr * 2f, top + cr * 2f), 180f, 90f, false)
            lineTo(tr - cr, top)
            arcTo(Rect(tr - cr * 2f, top, tr, top + cr * 2f), 270f, 90f, false)
            lineTo(tr, bot)
            close()
        }
        glowStroke(tabPath, color, sw, isFocused)

        // Play triangle inside folder
        val triPath = Path().apply {
            moveTo(w * 0.34f, h * 0.48f)
            lineTo(w * 0.34f, h * 0.72f)
            lineTo(w * 0.68f, h * 0.60f)
            close()
        }
        glowStroke(triPath, color, sw * 0.75f, isFocused, join = StrokeJoin.Round)
    }
}

// ── Adult icon — open padlock ─────────────────────────────────────────────────

@Composable
fun AdultIcon(isFocused: Boolean, size: Dp = 52.dp) {
    val color = Color(0xFFFF6B35) // WatchDawg orange
    Canvas(modifier = Modifier.size(size)) {
        val w  = this.size.width
        val h  = this.size.height
        val sw = w * 0.06f

        // Lock body
        val bodyPath = Path().apply {
            val cr = w * 0.10f
            addRoundRect(RoundRect(Rect(w * 0.16f, h * 0.46f, w * 0.84f, h * 0.92f), CornerRadius(cr, cr)))
        }
        glowStroke(bodyPath, color, sw, isFocused)

        // Shackle — open (shifted right, not going into lock body)
        val shacklePath = Path().apply {
            val cx   = w * 0.66f
            val top  = h * 0.10f
            val mid  = h * 0.46f
            val r    = w * 0.20f
            moveTo(cx - r, mid)
            lineTo(cx - r, top + r)
            drawArc(
                color     = Color.Transparent,
                startAngle = 180f,
                sweepAngle = -180f,
                useCenter  = false,
                topLeft   = Offset(cx - r, top),
                size      = Size(r * 2f, r * 2f),
            )
            // Use path arc instead
        }
        // Draw shackle manually: left side goes up, arc over top, right side stops short (open)
        glowLine(Offset(w * 0.46f, h * 0.50f), Offset(w * 0.46f, h * 0.28f), color, sw, isFocused)
        drawArc(
            color      = color.copy(alpha = if (isFocused) 0.35f else 0.18f),
            startAngle = 180f,
            sweepAngle = -180f,
            useCenter  = false,
            topLeft    = Offset(w * 0.46f, h * 0.08f),
            size       = Size(w * 0.38f, h * 0.40f),
            style      = Stroke(width = sw * 3.5f, cap = StrokeCap.Round),
        )
        drawArc(
            color      = color.copy(alpha = if (isFocused) 1f else 0.85f),
            startAngle = 180f,
            sweepAngle = -180f,
            useCenter  = false,
            topLeft    = Offset(w * 0.46f, h * 0.08f),
            size       = Size(w * 0.38f, h * 0.40f),
            style      = Stroke(width = sw, cap = StrokeCap.Round),
        )
        // Right shackle side — stops above lock (open)
        glowLine(Offset(w * 0.84f, h * 0.28f), Offset(w * 0.84f, h * 0.40f), color, sw, isFocused)

        // Keyhole inside body
        val keyholeCenter = Offset(w * 0.50f, h * 0.66f)
        glowCircle(keyholeCenter, w * 0.08f, color, sw * 0.7f, isFocused)
        glowLine(
            Offset(w * 0.44f, h * 0.74f),
            Offset(w * 0.56f, h * 0.74f),
            color, sw * 0.7f, isFocused,
        )
        glowLine(
            Offset(w * 0.50f, h * 0.74f),
            Offset(w * 0.50f, h * 0.84f),
            color, sw * 0.7f, isFocused,
        )
    }
}

// ── Settings icon — gear ──────────────────────────────────────────────────────

@Composable
fun SettingsIcon(isFocused: Boolean, size: Dp = 52.dp) {
    val color = Color(0xFF90A4AE) // blue-gray
    Canvas(modifier = Modifier.size(size)) {
        val w  = this.size.width
        val h  = this.size.height
        val sw = w * 0.06f
        val cx = w * 0.50f
        val cy = h * 0.50f

        // Inner circle
        glowCircle(Offset(cx, cy), w * 0.16f, color, sw * 0.85f, isFocused)

        // Outer ring (gear body)
        glowCircle(Offset(cx, cy), w * 0.30f, color.copy(alpha = if (isFocused) 0.6f else 0.4f), sw * 0.7f, isFocused)

        // Gear teeth — 8 teeth
        val toothCount = 8
        val innerR = w * 0.28f
        val outerR = w * 0.42f
        repeat(toothCount) { i ->
            val angleDeg = (360f / toothCount) * i
            val angleRad = Math.toRadians(angleDeg.toDouble()).toFloat()
            val toothW   = Math.toRadians(10.0).toFloat()
            val startX   = cx + innerR * Math.cos((angleRad - toothW).toDouble()).toFloat()
            val startY   = cy + innerR * Math.sin((angleRad - toothW).toDouble()).toFloat()
            val endX     = cx + innerR * Math.cos((angleRad + toothW).toDouble()).toFloat()
            val endY     = cy + innerR * Math.sin((angleRad + toothW).toDouble()).toFloat()
            val tipX     = cx + outerR * Math.cos(angleRad.toDouble()).toFloat()
            val tipY     = cy + outerR * Math.sin(angleRad.toDouble()).toFloat()

            val toothPath = Path().apply {
                moveTo(startX, startY)
                lineTo(tipX, tipY)
                lineTo(endX, endY)
            }
            glowStroke(toothPath, color, sw * 0.75f, isFocused, cap = StrokeCap.Butt, join = StrokeJoin.Miter)
        }
    }
}
