package com.meta.wearable.dat.externalsampleapps.displayaccess

import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Color

// The same fit transform is used by the phone camera preview and the 600x600 HUD.
fun drawYoloOverlay(canvas: Canvas, width: Float, height: Float, state: YoloState) {
  val scale = minOf(width / state.width, height / state.height)
  val left = (width - state.width * scale) / 2
  val top = (height - state.height * scale) / 2
  val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply { textSize = (width / 35).coerceIn(12f, 18f) }
  state.items.forEach { item ->
    val label = item.label.take(28)
    val w = paint.measureText(label) + 12f
    val h = paint.textSize + 10f
    val cx = (left + ((item.box[0] + item.box[2]) / 2 * scale).toFloat()).coerceIn(0f, width)
    val cy = (top + ((item.box[1] + item.box[3]) / 2 * scale).toFloat()).coerceIn(0f, height)
    val x = (cx - w / 2).coerceIn(0f, maxOf(0f, width - w))
    val y = (cy - h / 2).coerceIn(0f, maxOf(0f, height - h))
    paint.style = Paint.Style.FILL; paint.color = Color.argb(210, 0, 0, 0)
    canvas.drawRoundRect(x, y, x + w, y + h, 4f, 4f, paint)
    paint.style = Paint.Style.STROKE; paint.strokeWidth = 1f; paint.color = Color.GREEN
    canvas.drawRoundRect(x, y, x + w, y + h, 4f, 4f, paint)
    paint.style = Paint.Style.FILL; paint.color = Color.WHITE
    canvas.drawText(label, x + 6f, y + h - 6f, paint)
  }
}
