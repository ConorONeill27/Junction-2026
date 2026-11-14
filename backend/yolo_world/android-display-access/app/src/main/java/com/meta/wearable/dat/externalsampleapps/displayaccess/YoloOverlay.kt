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

fun drawYoloOverlay(canvas: Canvas, width: Float, height: Float, state: YoloState,
                    cursor: HeadCursorState = HeadCursorState()) {
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
