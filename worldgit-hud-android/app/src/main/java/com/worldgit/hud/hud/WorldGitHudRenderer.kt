package com.worldgit.hud.hud

import android.graphics.*
import android.text.Layout
import android.text.StaticLayout
import android.text.TextPaint
import android.text.TextUtils
import com.worldgit.hud.spatial.*
import kotlin.math.cos
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt
import kotlin.math.sin

data class OverlayLabelLayout(val item: YoloItem, val text: String, val bounds: RectF)
data class OverlayNoteLayout(val bounds: RectF, val lines: List<String>)

object WorldGitHudRenderer {

  private fun labelPaint(width: Int, height: Int) = Paint(Paint.ANTI_ALIAS_FLAG).apply {
    textSize = (maxOf(width, height) / 34f).coerceIn(14f, 18f)
    typeface = Typeface.create(Typeface.SANS_SERIF, Typeface.BOLD)
  }

  fun computeLabelLayouts(state: YoloState): List<OverlayLabelLayout> {
    if (state.width <= 0 || state.height <= 0) return emptyList()
    val paint = labelPaint(state.width, state.height)
    return state.items.map { item ->
      val text = item.label.take(28)
      val w = (paint.measureText(text) + 18f).coerceAtMost(state.width.toFloat() - 12f)
      val h = (paint.textSize + 12f).coerceAtMost(state.height.toFloat() - 12f)
      val cx = ((item.box[0] + item.box[2]) / 2.0).toFloat()
      val cy = ((item.box[1] + item.box[3]) / 2.0).toFloat()
      val x = (cx - w / 2f).coerceIn(8f, maxOf(8f, state.width - w - 8f))
      val y = (cy - h / 2f).coerceIn(48f, maxOf(48f, state.height - h - 78f))
      OverlayLabelLayout(item, text, RectF(x, y, x + w, y + h))
    }
  }

  fun computeNoteLayout(state: YoloState, label: OverlayLabelLayout, speech: LiveSpeechState): OverlayNoteLayout {
    val paint = labelPaint(state.width, state.height).apply {
      typeface = Typeface.create(Typeface.SANS_SERIF, Typeface.NORMAL)
    }
    val saved = speech.notesByObjectId[label.item.id]
      ?: label.item.note
    val text = when {
      speech.active && speech.purpose == SpeechPurpose.PIN_NOTE && speech.noteId == label.item.id ->
        if (speech.finishing) "Transcribing..." else "stop recording"
      !saved.isNullOrBlank() -> saved
      else -> "add note?"
    }
    val maxWidth = (state.width - 20f).coerceAtLeast(80f)
    val lines = mutableListOf<String>()
    var remaining = text
    while (remaining.isNotEmpty()) {
      val count = paint.breakText(remaining, true, maxWidth - 18f, null).coerceAtLeast(1)
      val end = if (count < remaining.length) remaining.lastIndexOf(' ', count).takeIf { it > 0 } ?: count else count
      lines.add(remaining.take(end))
      remaining = remaining.drop(end).trimStart()
    }
    val w = maxOf(label.bounds.width(), (lines.maxOfOrNull { paint.measureText(it) } ?: 0f) + 18f).coerceAtMost(maxWidth)
    val h = lines.size * (paint.textSize + 5f) + 10f
    val x = label.bounds.left.coerceIn(8f, maxOf(8f, state.width - w - 8f))
    val y = (label.bounds.bottom + 5f).coerceIn(50f, maxOf(50f, state.height - h - 72f))
    return OverlayNoteLayout(RectF(x, y, x + w, y + h), lines)
  }

  /** Returns hovered object id when cursor sits over a label, note pill, or bounding box (smallest area wins). */
  fun hoveredObjectId(state: YoloState, cursor: HeadCursorState, speech: LiveSpeechState): String? {
    if (!cursor.visible || state.items.isEmpty() || state.width <= 0 || state.height <= 0) return null
    val px = cursor.x * state.width
    val py = cursor.y * state.height
    val layouts = computeLabelLayouts(state)

    layouts.lastOrNull {
      val b = it.bounds
      val expanded = RectF(b.left - 12f, b.top - 12f, b.right + 12f, b.bottom + 12f)
      expanded.contains(px, py)
    }?.let { return it.item.id }

    layouts.lastOrNull {
      val nb = computeNoteLayout(state, it, speech).bounds
      val expanded = RectF(nb.left - 12f, nb.top - 12f, nb.right + 12f, nb.bottom + 12f)
      expanded.contains(px, py)
    }?.let { return it.item.id }

    var bestId: String? = null
    var bestArea = Double.POSITIVE_INFINITY
    for (item in state.items) {
      if (item.box.size < 4) continue
      val x1 = item.box[0] - 8.0
      val y1 = item.box[1] - 8.0
      val x2 = item.box[2] + 8.0
      val y2 = item.box[3] + 8.0
      if (px >= x1 && px <= x2 && py >= y1 && py <= y2) {
        val area = max(1.0, (x2 - x1) * (y2 - y1))
        if (area < bestArea) {
          bestArea = area
          bestId = item.id
        }
      }
    }
    return bestId
  }

  fun drawFullHud(
    canvas: Canvas,
    width: Float,
    height: Float,
    mode: HudMode,
    compass: CompassState,
    projectedYolo: YoloState,
    cursor: HeadCursorState,
    speech: LiveSpeechState,
    imu: ImuState,
    undoSteps: List<String>,
    savedNotes: List<SavedNote>,
    calibration: HudCalibration,
    memoryPaused: Boolean,
    clearBlack: Boolean = true,
  ) {
    if (clearBlack) {
      canvas.drawColor(Color.BLACK)
    }
    val saved = canvas.save()
    val scale = min(width / 600f, height / 600f)
    canvas.translate((width - 600f * scale) / 2f, (height - 600f * scale) / 2f)
    canvas.scale(scale, scale)

    when (mode) {
      HudMode.ASK -> drawAskCompassHud(canvas, compass)
      HudMode.PINS -> drawPinsOverlayHud(canvas, projectedYolo, cursor, speech, calibration)
      HudMode.UNDO -> drawUndoHud(canvas, undoSteps, savedNotes)
      HudMode.SCAN -> drawScanHud(canvas, compass, imu)
    }

    drawHeaderBar(canvas, mode, compass, memoryPaused, projectedYolo.items.size, savedNotes.size)
    drawBottomActionRail(canvas, mode, speech)

    if (calibration.showGuide && mode == HudMode.PINS) {
      drawCalibrationBrackets(canvas)
    }

    if (speech.active) {
      drawSpeechModal(canvas, speech)
    }

    canvas.restoreToCount(saved)
  }

  private fun drawHeaderBar(
    canvas: Canvas,
    mode: HudMode,
    compass: CompassState,
    memoryPaused: Boolean,
    objectCount: Int,
    noteCount: Int,
  ) {
    val paint = Paint(Paint.ANTI_ALIAS_FLAG)
    paint.style = Paint.Style.FILL
    paint.color = Color.argb(215, 10, 16, 28)
    canvas.drawRoundRect(16f, 12f, 584f, 48f, 18f, 18f, paint)
    paint.style = Paint.Style.STROKE
    paint.strokeWidth = 1.5f
    paint.color = Color.argb(120, 56, 189, 248)
    canvas.drawRoundRect(16f, 12f, 584f, 48f, 18f, 18f, paint)

    // Luminous cyan dot
    paint.style = Paint.Style.FILL
    paint.color = if (memoryPaused) Color.rgb(251, 191, 36) else Color.rgb(46, 232, 255)
    canvas.drawCircle(34f, 30f, 5f, paint)

    paint.color = Color.WHITE
    paint.textSize = 18f
    paint.typeface = Typeface.create(Typeface.SANS_SERIF, Typeface.BOLD)
    canvas.drawText(mode.headerTitle, 48f, 36f, paint)

    val rightBadge = when (mode) {
      HudMode.PINS -> "$objectCount obj • $noteCount pins"
      HudMode.ASK -> "${compass.cardinal} ${compass.headingDeg.roundToInt()}°"
      else -> "${compass.sensorSource} • ${compass.cardinal} ${compass.headingDeg.roundToInt()}°"
    }
    paint.color = Color.rgb(148, 224, 255)
    paint.textSize = 16f
    paint.typeface = Typeface.create(Typeface.SANS_SERIF, Typeface.NORMAL)
    val rw = paint.measureText(rightBadge)
    canvas.drawText(rightBadge, 568f - rw, 36f, paint)
  }

  private fun drawBottomActionRail(canvas: Canvas, activeMode: HudMode, speech: LiveSpeechState) {
    val paint = Paint(Paint.ANTI_ALIAS_FLAG)
    val railRect = RectF(18f, 536f, 582f, 586f)
    paint.style = Paint.Style.FILL
    paint.color = Color.argb(225, 10, 15, 26)
    canvas.drawRoundRect(railRect, 25f, 25f, paint)
    paint.style = Paint.Style.STROKE
    paint.strokeWidth = 1.5f
    paint.color = Color.argb(95, 255, 255, 255)
    canvas.drawRoundRect(railRect, 25f, 25f, paint)

    val modes = HudMode.entries
    val slotW = (railRect.width() - 16f) / modes.size
    modes.forEachIndexed { i, m ->
      val selected = m == activeMode
      val bx1 = railRect.left + 8f + i * slotW
      val bx2 = bx1 + slotW - 4f
      val pill = RectF(bx1, railRect.top + 6f, bx2, railRect.bottom - 6f)
      if (selected) {
        paint.style = Paint.Style.FILL
        paint.color = Color.argb(220, 24, 62, 118)
        canvas.drawRoundRect(pill, 19f, 19f, paint)
        paint.style = Paint.Style.STROKE
        paint.strokeWidth = 2f
        paint.color = Color.rgb(100, 165, 255)
        canvas.drawRoundRect(pill, 19f, 19f, paint)
      }
      val label = if (m == HudMode.ASK && speech.active && speech.purpose == SpeechPurpose.ASK_GEMINI) "Listening" else m.label
      paint.style = Paint.Style.FILL
      paint.color = if (selected) Color.WHITE else Color.argb(190, 210, 224, 245)
      paint.textSize = 18f
      paint.typeface = if (selected) Typeface.create(Typeface.SANS_SERIF, Typeface.BOLD) else Typeface.SANS_SERIF
      val tw = paint.measureText(label)
      canvas.drawText(label, pill.centerX() - tw / 2f, pill.centerY() + 6f, paint)
    }
  }

  fun renderCompassDialBitmap(compass: CompassState, sizePx: Int = 220): Bitmap {
    val bmp = Bitmap.createBitmap(sizePx, sizePx, Bitmap.Config.ARGB_8888)
    bmp.eraseColor(Color.TRANSPARENT)
    val canvas = Canvas(bmp)
    val s = sizePx / 264f
    canvas.scale(s, s)
    drawCompassDial(canvas, compass, cx = 132f, cy = 132f, outerR = 122f)
    return bmp
  }

  fun renderPinsGlassesBitmap(
    projectedYolo: YoloState,
    cursor: HeadCursorState,
    speech: LiveSpeechState,
    calibration: HudCalibration,
  ): Bitmap {
    val bmp = Bitmap.createBitmap(360, 264, Bitmap.Config.ARGB_8888)
    bmp.eraseColor(Color.BLACK)
    val canvas = Canvas(bmp)
    val saved = canvas.save()
    canvas.scale(360f / 600f, 264f / 490f)
    canvas.translate(0f, -44f)
    drawPinsOverlayHud(canvas, projectedYolo, cursor, speech, calibration)
    if (calibration.showGuide) {
      drawCalibrationBrackets(canvas)
    }
    if (speech.active) {
      drawSpeechModal(canvas, speech)
    }
    canvas.restoreToCount(saved)
    // One alpha-254 pixel tells DAT's DisplayImageResolver to encode lossless PNG instead of quality-85 JPEG.
    bmp.setPixel(0, 0, Color.argb(254, 0, 0, 0))
    return bmp
  }

  private fun drawAskCompassHud(canvas: Canvas, compass: CompassState) {
    val accent = when {
      !compass.hasNavigation -> Color.rgb(148, 163, 184)
      compass.aligned -> Color.rgb(52, 211, 153)
      else -> Color.rgb(56, 189, 248)
    }
    if (compass.hasNavigation) {
      drawCompassDial(canvas, compass, cx = 175f, cy = 285f, outerR = 128f)
    } else {
      val boxPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = 2.5f
        color = Color.argb(90, 148, 163, 184)
      }
      canvas.drawCircle(175f, 285f, 112f, boxPaint)
      val lblPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.rgb(148, 163, 184)
        textSize = 18f
        typeface = Typeface.create(Typeface.SANS_SERIF, Typeface.BOLD)
      }
      drawCenteredText(canvas, "${compass.cardinal} ${compass.headingDeg.roundToInt()}°", 175f, 276f, lblPaint)
      lblPaint.textSize = 14f
      lblPaint.typeface = Typeface.SANS_SERIF
      drawCenteredText(canvas, "No 3D arrow", 175f, 304f, lblPaint)
    }
    val paint = Paint(Paint.ANTI_ALIAS_FLAG)

    // Right column details card
    val cardRect = RectF(320f, 148f, 582f, 432f)
    paint.color = Color.argb(200, 12, 19, 34)
    canvas.drawRoundRect(cardRect, 18f, 18f, paint)
    paint.style = Paint.Style.STROKE
    paint.strokeWidth = 1.5f
    paint.color = Color.argb(90, 100, 165, 255)
    canvas.drawRoundRect(cardRect, 18f, 18f, paint)

    paint.style = Paint.Style.FILL
    paint.color = accent
    paint.textSize = 22f
    paint.typeface = Typeface.create(Typeface.SANS_SERIF, Typeface.BOLD)
    canvas.drawText((compass.targetLabel.ifBlank { "MEMORY ASK" }).uppercase().take(18), 338f, 184f, paint)

    paint.color = Color.WHITE
    paint.textSize = 18f
    canvas.drawText(compass.turnInstruction.take(26), 338f, 214f, paint)

    val textPaint = TextPaint(Paint.ANTI_ALIAS_FLAG).apply {
      color = Color.rgb(226, 232, 240)
      textSize = 19f
    }
    val answer = compass.answerText.ifBlank { "Tap Ask to search memory or room." }
    val layout = StaticLayout.Builder.obtain(answer, 0, answer.length, textPaint, 228)
      .setMaxLines(6)
      .setEllipsize(TextUtils.TruncateAt.END)
      .build()
    val ts = canvas.save()
    canvas.translate(338f, 236f)
    layout.draw(canvas)
    canvas.restoreToCount(ts)

    paint.color = Color.rgb(148, 163, 184)
    paint.textSize = 14f
    paint.typeface = Typeface.SANS_SERIF
    val footer = compass.ambiguityNote?.take(34)
      ?: compass.memoryContext?.take(34)
      ?: "Memory + Studio Ask"
    canvas.drawText(footer, 338f, 412f, paint)

    // Hint strip above rail
    paint.color = Color.argb(175, 203, 213, 225)
    paint.textSize = 16f
    drawCenteredText(canvas, "Tap Ask button or Pinch to speak", 300f, 512f, paint)
  }

  fun drawCompassDial(canvas: Canvas, compass: CompassState, cx: Float, cy: Float, outerR: Float) {
    val paint = Paint(Paint.ANTI_ALIAS_FLAG)
    val accent = if (compass.aligned) Color.rgb(52, 211, 153) else Color.rgb(56, 189, 248)

    paint.style = Paint.Style.FILL
    paint.color = Color.argb(210, 13, 21, 38)
    canvas.drawCircle(cx, cy, outerR, paint)
    paint.style = Paint.Style.STROKE
    paint.strokeWidth = 3.5f
    paint.color = Color.argb(125, 255, 255, 255)
    canvas.drawCircle(cx, cy, outerR, paint)

    paint.strokeWidth = 1.8f
    paint.pathEffect = DashPathEffect(floatArrayOf(6f, 9f), 0f)
    paint.color = Color.argb(70, 255, 255, 255)
    canvas.drawCircle(cx, cy, outerR * 0.70f, paint)
    paint.pathEffect = null

    paint.strokeWidth = 4f
    paint.color = Color.argb(220, 255, 255, 255)
    canvas.drawLine(cx, cy - outerR + 2f, cx, cy - outerR + 18f, paint)
    paint.strokeWidth = 2.5f
    paint.color = Color.argb(120, 255, 255, 255)
    canvas.drawLine(cx + outerR - 2f, cy, cx + outerR - 16f, cy, paint)
    canvas.drawLine(cx, cy + outerR - 2f, cx, cy + outerR - 16f, paint)
    canvas.drawLine(cx - outerR + 2f, cy, cx - outerR + 16f, cy, paint)

    paint.style = Paint.Style.FILL
    paint.textSize = 15f
    paint.typeface = Typeface.create(Typeface.SANS_SERIF, Typeface.BOLD)
    paint.color = Color.argb(215, 255, 255, 255)
    drawCenteredText(canvas, "FWD", cx, cy - outerR * 0.73f, paint)
    paint.color = Color.argb(140, 255, 255, 255)
    drawCenteredText(canvas, "R", cx + outerR * 0.79f, cy + 5f, paint)
    drawCenteredText(canvas, "B", cx, cy + outerR * 0.83f, paint)
    drawCenteredText(canvas, "L", cx - outerR * 0.79f, cy + 5f, paint)

    if (compass.hasNavigation) {
      val saved = canvas.save()
      canvas.rotate(compass.arrowAngleDeg, cx, cy)
      val chevron = Path().apply {
        moveTo(cx, cy - outerR + 14f)
        lineTo(cx + 18f, cy - outerR * 0.48f)
        lineTo(cx, cy - outerR * 0.56f)
        lineTo(cx - 18f, cy - outerR * 0.48f)
        close()
      }
      paint.color = accent
      canvas.drawPath(chevron, paint)
      paint.style = Paint.Style.STROKE
      paint.strokeWidth = 5f
      paint.strokeCap = Paint.Cap.ROUND
      canvas.drawLine(cx, cy - outerR * 0.56f, cx, cy - outerR * 0.37f, paint)
      canvas.restoreToCount(saved)
    }

    val hubR = outerR * 0.36f
    paint.style = Paint.Style.FILL
    paint.color = Color.argb(240, 8, 12, 22)
    canvas.drawCircle(cx, cy, hubR, paint)
    paint.style = Paint.Style.STROKE
    paint.strokeWidth = 2.5f
    paint.color = if (compass.hasNavigation) accent else Color.rgb(148, 163, 184)
    canvas.drawCircle(cx, cy, hubR, paint)

    paint.style = Paint.Style.FILL
    paint.color = Color.WHITE
    if (compass.hasNavigation) {
      paint.textSize = 28f
      drawCenteredText(canvas, compass.arrowGlyph, cx, cy + 2f, paint)
      paint.color = accent
      paint.textSize = 18f
      drawCenteredText(canvas, "${compass.arrowAngleDeg.roundToInt()}°", cx, cy + 26f, paint)
    } else {
      paint.textSize = 14f
      paint.color = Color.rgb(148, 163, 184)
      drawCenteredText(canvas, "NO DIR", cx, cy + 5f, paint)
    }
  }

  private fun drawPinsOverlayHud(
    canvas: Canvas,
    state: YoloState,
    cursor: HeadCursorState,
    speech: LiveSpeechState,
    calibration: HudCalibration,
  ) {
    val paint = labelPaint(state.width, state.height)
    val layouts = computeLabelLayouts(state)

    layouts.forEach { label ->
      val selected = label.item.id == cursor.selectedId
      val hasNote =
        speech.notesByObjectId.containsKey(label.item.id) ||
          !label.item.note.isNullOrBlank()
      val strokeColor = when {
        selected -> Color.rgb(100, 165, 255)
        hasNote && label.item.noteUncertain -> Color.rgb(251, 191, 36)
        hasNote -> Color.rgb(72, 235, 160)
        else -> Color.rgb(0, 255, 0)
      }

      paint.style = Paint.Style.STROKE
      paint.strokeWidth = if (selected) 3.5f else 2.5f
      paint.color = strokeColor
      val b = label.item.box
      canvas.drawRect(b[0].toFloat(), b[1].toFloat(), b[2].toFloat(), b[3].toFloat(), paint)

      paint.style = Paint.Style.FILL
      paint.color = when {
        selected -> Color.rgb(30, 105, 245)
        hasNote && label.item.noteUncertain -> Color.rgb(120, 80, 12)
        hasNote -> Color.rgb(18, 92, 64)
        else -> Color.argb(215, 0, 0, 0)
      }
      canvas.drawRoundRect(label.bounds, 6f, 6f, paint)

      paint.style = Paint.Style.STROKE
      paint.strokeWidth = 1.5f
      paint.color = strokeColor
      canvas.drawRoundRect(label.bounds, 6f, 6f, paint)

      paint.style = Paint.Style.FILL
      paint.color = Color.WHITE
      canvas.drawText(label.text, label.bounds.left + 8f, label.bounds.bottom - 7f, paint)
    }

    // Draw saved notes first (always stay visible)
    layouts
      .filter {
        val hasNote = speech.notesByObjectId.containsKey(it.item.id) ||
          !it.item.note.isNullOrBlank()
        hasNote && it.item.id != cursor.selectedId
      }
      .forEach { label ->
        drawNoteCard(canvas, state, label, speech, isSelected = false)
      }

    // Draw selected object prompt/note last on top
    layouts.lastOrNull { it.item.id == cursor.selectedId }?.let { label ->
      drawNoteCard(canvas, state, label, speech, isSelected = true)
    }

    if (layouts.isEmpty()) {
      val hintPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.argb(180, 226, 232, 240)
        textSize = 20f
      }
      drawCenteredText(canvas, state.status.ifBlank { "Scanning objects with glasses camera..." }, 300f, 290f, hintPaint)
      hintPaint.textSize = 16f
      hintPaint.color = Color.argb(150, 148, 224, 255)
      drawCenteredText(canvas, "Move head cursor onto an object to select", 300f, 322f, hintPaint)
    } else if (cursor.selectedId == null) {
      val hintPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.argb(175, 148, 224, 255)
        textSize = 16f
      }
      drawCenteredText(canvas, "Aim cursor over object -> tap/pinch to add note", 300f, 514f, hintPaint)
    }

    // White head-aimed cursor dot
    if (cursor.visible) {
      val cx = (cursor.x * 600f).coerceIn(10f, 590f)
      val cy = (cursor.y * 600f).coerceIn(52f, 528f)
      paint.style = Paint.Style.STROKE
      paint.strokeWidth = 3.5f
      paint.color = Color.argb(210, 0, 0, 0)
      canvas.drawCircle(cx, cy, 8f, paint)
      paint.style = Paint.Style.FILL
      paint.color = Color.WHITE
      canvas.drawCircle(cx, cy, 7f, paint)
      if (cursor.selectedId != null) {
        paint.style = Paint.Style.STROKE
        paint.strokeWidth = 2f
        paint.color = Color.rgb(100, 165, 255)
        canvas.drawCircle(cx, cy, 13f, paint)
      }
    }
  }

  private fun drawNoteCard(
    canvas: Canvas,
    state: YoloState,
    label: OverlayLabelLayout,
    speech: LiveSpeechState,
    isSelected: Boolean,
  ) {
    val paint = labelPaint(state.width, state.height).apply {
      typeface = Typeface.create(Typeface.SANS_SERIF, Typeface.NORMAL)
    }
    val layout = computeNoteLayout(state, label, speech)
    val bounds = layout.bounds
    val isRecordingThis = speech.active && speech.purpose == SpeechPurpose.PIN_NOTE && speech.noteId == label.item.id
    paint.style = Paint.Style.FILL
    paint.color = when {
      isRecordingThis -> Color.rgb(185, 28, 38)
      isSelected -> Color.argb(235, 8, 18, 36)
      else -> Color.argb(235, 10, 26, 20)
    }
    canvas.drawRoundRect(bounds, 6f, 6f, paint)

    paint.style = Paint.Style.STROKE
    paint.strokeWidth = 1.5f
    paint.color = when {
      isSelected -> Color.rgb(100, 165, 255)
      label.item.noteUncertain -> Color.rgb(251, 191, 36)
      else -> Color.rgb(72, 235, 160)
    }
    canvas.drawRoundRect(bounds, 6f, 6f, paint)

    paint.style = Paint.Style.FILL
    paint.color = Color.WHITE
    layout.lines.forEachIndexed { idx, line ->
      canvas.drawText(line, bounds.left + 8f, bounds.top + paint.textSize + 5f + idx * (paint.textSize + 5f), paint)
    }
  }

  private fun drawUndoHud(canvas: Canvas, undoSteps: List<String>, savedNotes: List<SavedNote>) {
    val paint = Paint(Paint.ANTI_ALIAS_FLAG)
    val box = RectF(28f, 96f, 572f, 492f)
    paint.color = Color.argb(200, 12, 18, 32)
    canvas.drawRoundRect(box, 18f, 18f, paint)
    paint.style = Paint.Style.STROKE
    paint.strokeWidth = 1.5f
    paint.color = Color.argb(100, 56, 189, 248)
    canvas.drawRoundRect(box, 18f, 18f, paint)

    paint.style = Paint.Style.FILL
    paint.color = Color.rgb(56, 189, 248)
    paint.textSize = 22f
    paint.typeface = Typeface.create(Typeface.SANS_SERIF, Typeface.BOLD)
    canvas.drawText("Restore Instructions", 52f, 140f, paint)

    paint.typeface = Typeface.SANS_SERIF
    paint.textSize = 18f
    paint.color = Color.WHITE
    val lines = if (undoSteps.isNotEmpty()) undoSteps else savedNotes.map { "${it.label}: ${it.text}" }
    if (lines.isEmpty()) {
      paint.color = Color.argb(180, 203, 213, 225)
      canvas.drawText("No recent timeline steps yet.", 52f, 190f, paint)
    } else {
      lines.take(7).forEachIndexed { idx, item ->
        canvas.drawText("• ${item.take(44)}", 52f, 188f + idx * 38f, paint)
      }
    }
    paint.color = Color.argb(180, 148, 224, 255)
    paint.textSize = 16f
    drawCenteredText(canvas, "Guide only — tap Undo to refresh restore instructions", 300f, 466f, paint)
  }

  private fun drawScanHud(canvas: Canvas, compass: CompassState, imu: ImuState) {
    val paint = Paint(Paint.ANTI_ALIAS_FLAG)
    val box = RectF(28f, 118f, 572f, 472f)
    paint.color = Color.argb(200, 12, 18, 32)
    canvas.drawRoundRect(box, 18f, 18f, paint)
    paint.style = Paint.Style.STROKE
    paint.strokeWidth = 1.5f
    paint.color = if (imu.recording) Color.rgb(248, 113, 113) else Color.rgb(52, 211, 153)
    canvas.drawRoundRect(box, 18f, 18f, paint)

    paint.style = Paint.Style.FILL
    paint.color = if (imu.recording) Color.rgb(248, 113, 113) else Color.rgb(52, 211, 153)
    paint.textSize = 22f
    paint.typeface = Typeface.create(Typeface.SANS_SERIF, Typeface.BOLD)
    val title = if (imu.recording) "REC IMU • ${imu.sampleCount} pts" else "9-Axis Glasses Telemetry • LIVE"
    canvas.drawText(title, 52f, 166f, paint)

    paint.color = Color.WHITE
    paint.typeface = Typeface.SANS_SERIF
    paint.textSize = 24f
    canvas.drawText("Heading  ${compass.cardinal}  ${compass.headingDeg.roundToInt()}°", 52f, 232f, paint)
    canvas.drawText("Pitch       ${compass.pitchDeg.roundToInt()}°", 52f, 282f, paint)
    canvas.drawText("Roll        ${compass.rollDeg.roundToInt()}°", 52f, 332f, paint)

    paint.color = Color.argb(180, 148, 224, 255)
    paint.textSize = 17f
    canvas.drawText(imu.statusNote.take(46), 52f, 396f, paint)
    drawCenteredText(canvas, "Tap glasses / Scan button to ${if (imu.recording) "stop" else "record"} telemetry", 300f, 446f, paint)
  }

  private fun drawCalibrationBrackets(canvas: Canvas) {
    val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
      color = Color.CYAN
      style = Paint.Style.STROKE
      strokeWidth = 4f
    }
    val m = 4f
    val len = 64f
    canvas.drawLine(m, m, m + len, m, paint)
    canvas.drawLine(m, m, m, m + len, paint)
    canvas.drawLine(600f - m, m, 600f - m - len, m, paint)
    canvas.drawLine(600f - m, m, 600f - m, m + len, paint)
    canvas.drawLine(m, 600f - m, m + len, 600f - m, paint)
    canvas.drawLine(m, 600f - m, m, 600f - m - len, paint)
    canvas.drawLine(600f - m, 600f - m, 600f - m - len, 600f - m, paint)
    canvas.drawLine(600f - m, 600f - m, 600f - m, 600f - m - len, paint)
    paint.color = Color.WHITE
    paint.strokeWidth = 2f
    canvas.drawLine(280f, 300f, 320f, 300f, paint)
    canvas.drawLine(300f, 280f, 300f, 320f, paint)
  }

  private fun drawSpeechModal(canvas: Canvas, speech: LiveSpeechState) {
    val paint = Paint(Paint.ANTI_ALIAS_FLAG)
    val x = 300f
    val y = 260f
    paint.color = Color.argb(215, 8, 14, 28)
    canvas.drawCircle(x, y, 44f, paint)
    paint.style = Paint.Style.STROKE
    paint.strokeWidth = 3f
    paint.color = if (speech.finishing) Color.rgb(52, 211, 153) else Color.rgb(46, 232, 255)
    canvas.drawCircle(x, y, 44f, paint)

    paint.style = Paint.Style.FILL
    paint.color = Color.WHITE
    canvas.drawRoundRect(x - 10f, y - 24f, x + 10f, y + 6f, 10f, 10f, paint)
    paint.style = Paint.Style.STROKE
    paint.strokeWidth = 3f
    canvas.drawArc(RectF(x - 18f, y - 19f, x + 18f, y + 19f), 0f, 180f, false, paint)
    canvas.drawLine(x, y + 19f, x, y + 29f, paint)
    canvas.drawLine(x - 11f, y + 29f, x + 11f, y + 29f, paint)

    val textPaint = TextPaint(Paint.ANTI_ALIAS_FLAG).apply {
      color = Color.WHITE
      textSize = 21f
    }
    val caption = when {
      speech.finishing && speech.purpose == SpeechPurpose.ASK_GEMINI -> "Checking memory & Studio..."
      speech.finishing -> "Transcribing audio..."
      speech.text.isNotBlank() -> speech.text.takeLast(110)
      speech.purpose == SpeechPurpose.ASK_GEMINI -> "Ask about your space..."
      else -> "Speak note for ${speech.noteLabel ?: "object"}..."
    }
    val layout = StaticLayout.Builder.obtain(caption, 0, caption.length, textPaint, 510)
      .setAlignment(Layout.Alignment.ALIGN_CENTER)
      .setIncludePad(false)
      .setMaxLines(3)
      .setEllipsize(TextUtils.TruncateAt.END)
      .build()
    val bottom = 524f
    val top = (bottom - layout.height - 28f).coerceAtLeast(320f)
    val box = RectF(24f, top, 576f, bottom)
    paint.style = Paint.Style.FILL
    paint.color = Color.argb(235, 10, 16, 28)
    canvas.drawRoundRect(box, 16f, 16f, paint)
    paint.style = Paint.Style.STROKE
    paint.strokeWidth = 1.5f
    paint.color = Color.rgb(46, 232, 255)
    canvas.drawRoundRect(box, 16f, 16f, paint)
    val saved = canvas.save()
    canvas.translate(45f, top + 14f)
    layout.draw(canvas)
    canvas.restoreToCount(saved)
  }

  private fun drawCenteredText(canvas: Canvas, text: String, cx: Float, baselineY: Float, paint: Paint) {
    val w = paint.measureText(text)
    canvas.drawText(text, cx - w / 2f, baselineY, paint)
  }
}
