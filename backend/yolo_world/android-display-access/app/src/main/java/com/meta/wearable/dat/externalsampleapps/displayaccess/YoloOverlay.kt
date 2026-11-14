package com.meta.wearable.dat.externalsampleapps.displayaccess

import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Color
import android.graphics.RectF

data class YoloLabelLayout(val item: YoloItem, val text: String, val bounds: RectF)

private fun labelPaint(state: YoloState) = Paint(Paint.ANTI_ALIAS_FLAG).apply {
  textSize = (maxOf(state.width, state.height) / 35f).coerceIn(12f, 18f)
}

// Drawing and hit testing share image-pixel rectangles at every display size.
fun yoloLabelLayouts(state: YoloState): List<YoloLabelLayout> {
  val paint = labelPaint(state)
  return state.items.map { item ->
    val text = item.label.take(28)
    val w = (paint.measureText(text) + 12f).coerceAtMost(state.width.toFloat())
    val h = (paint.textSize + 10f).coerceAtMost(state.height.toFloat())
    val cx = ((item.box[0] + item.box[2]) / 2).toFloat()
    val cy = ((item.box[1] + item.box[3]) / 2).toFloat()
    val x = (cx - w / 2).coerceIn(0f, maxOf(0f, state.width - w))
    val y = (cy - h / 2).coerceIn(0f, maxOf(0f, state.height - h))
    YoloLabelLayout(item, text, RectF(x, y, x + w, y + h))
  }
}

fun yoloLabelAt(state: YoloState, x: Float, y: Float): String? =
  yoloLabelLayouts(state).lastOrNull { it.bounds.contains(x * state.width, y * state.height) }?.item?.id

data class YoloNoteLayout(val bounds: RectF, val lines: List<String>)

fun yoloNoteLayout(state: YoloState, label: YoloLabelLayout, speech: LiveSpeechState): YoloNoteLayout {
  val paint = labelPaint(state)
  val text = if (speech.noteId == label.item.id) {
    if (speech.finishing) "Transcribing..." else "stop recording"
  } else speech.notes[label.item.id] ?: "add note?"
  val maxWidth = (state.width - 12f).coerceAtLeast(1f)
  val lines = mutableListOf<String>()
  var remaining = text
  while (remaining.isNotEmpty()) {
    val count = paint.breakText(remaining, true, maxWidth - 12f, null).coerceAtLeast(1)
    val end = if (count < remaining.length) remaining.lastIndexOf(' ', count).takeIf { it > 0 } ?: count else count
    lines.add(remaining.take(end))
    remaining = remaining.drop(end).trimStart()
  }
  val w = maxOf(label.bounds.width(), (lines.maxOfOrNull { paint.measureText(it) } ?: 0f) + 12f).coerceAtMost(maxWidth)
  val h = lines.size * (paint.textSize + 4f) + 8f
  val x = label.bounds.left.coerceIn(0f, maxOf(0f, state.width - w))
  val y = (label.bounds.bottom + 4f).coerceAtMost(maxOf(0f, state.height - h))
  return YoloNoteLayout(RectF(x, y, x + w, y + h), lines)
}

fun yoloNoteAt(state: YoloState, cursor: HeadCursorState, speech: LiveSpeechState): String? {
  if (!cursor.visible) return null
  val label = yoloLabelLayouts(state).lastOrNull { it.item.id == cursor.selectedId } ?: return null
  if (speech.notes.containsKey(label.item.id) && speech.noteId != label.item.id) return null
  return label.item.id.takeIf { yoloNoteLayout(state, label, speech).bounds.contains(cursor.x * state.width, cursor.y * state.height) }
}

fun drawYoloOverlay(canvas: Canvas, width: Float, height: Float, state: YoloState,
                    cursor: HeadCursorState = HeadCursorState(), speech: LiveSpeechState = LiveSpeechState()) {
  if (state.width <= 0 || state.height <= 0) return
  val scale = minOf(width / state.width, height / state.height)
  val left = (width - state.width * scale) / 2
  val top = (height - state.height * scale) / 2
  val paint = labelPaint(state)
  val saved = canvas.save()
  canvas.translate(left, top)
  canvas.scale(scale, scale)
  yoloLabelLayouts(state).forEach { label ->
    val selected = label.item.id == cursor.selectedId
    paint.style = Paint.Style.STROKE
    paint.strokeWidth = 2f / scale
    paint.color = if (selected) Color.rgb(100,165,255) else Color.GREEN
    val box = label.item.box
    canvas.drawRect(box[0].toFloat(), box[1].toFloat(), box[2].toFloat(), box[3].toFloat(), paint)
    paint.style = Paint.Style.FILL
    paint.color = if (selected) Color.rgb(30, 105, 245) else Color.argb(210, 0, 0, 0)
    canvas.drawRoundRect(label.bounds, 4f, 4f, paint)
    paint.style = Paint.Style.STROKE
    paint.strokeWidth = 1f
    paint.color = if (selected) Color.rgb(100, 165, 255) else Color.GREEN
    canvas.drawRoundRect(label.bounds, 4f, 4f, paint)
    paint.style = Paint.Style.FILL
    paint.color = Color.WHITE
    canvas.drawText(label.text, label.bounds.left + 6f, label.bounds.bottom - 6f, paint)
  }
  // Draw last so other object labels cannot cover the selected object's prompt.
  yoloLabelLayouts(state).lastOrNull { it.item.id == cursor.selectedId }?.let { label ->
    val layout = yoloNoteLayout(state, label, speech)
    val bounds = layout.bounds
    paint.style = Paint.Style.FILL
    paint.color = if (speech.noteId == label.item.id && !speech.finishing) Color.rgb(195, 30, 40) else Color.argb(230, 0, 0, 0)
    canvas.drawRoundRect(bounds, 4f, 4f, paint)
    paint.style = Paint.Style.STROKE
    paint.strokeWidth = 1f
    paint.color = Color.rgb(100, 165, 255)
    canvas.drawRoundRect(bounds, 4f, 4f, paint)
    paint.style = Paint.Style.FILL
    paint.color = Color.WHITE
    layout.lines.forEachIndexed { index, line ->
      canvas.drawText(line, bounds.left + 6f, bounds.top + paint.textSize + 4f + index * (paint.textSize + 4f), paint)
    }
  }
  if (cursor.visible) {
    val radius = 6f
    val x = (cursor.x * state.width).coerceIn(radius, maxOf(radius, state.width - radius))
    val y = (cursor.y * state.height).coerceIn(radius, maxOf(radius, state.height - radius))
    paint.style = Paint.Style.STROKE
    paint.strokeWidth = 3f
    paint.color = Color.argb(180, 0, 0, 0)
    canvas.drawCircle(x, y, radius, paint)
    paint.style = Paint.Style.FILL
    paint.color = Color.WHITE
    canvas.drawCircle(x, y, radius, paint)
  }
  canvas.restoreToCount(saved)
}
