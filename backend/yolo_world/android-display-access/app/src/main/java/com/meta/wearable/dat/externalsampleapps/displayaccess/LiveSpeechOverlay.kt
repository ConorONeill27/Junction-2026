package com.meta.wearable.dat.externalsampleapps.displayaccess

import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.text.Layout
import android.text.StaticLayout
import android.text.TextPaint
import android.text.TextUtils

fun drawLiveSpeechOverlay(canvas: Canvas, width: Float, height: Float, speech: LiveSpeechState) {
  if (!speech.active || width <= 0 || height <= 0) return
  val paint = Paint(Paint.ANTI_ALIAS_FLAG)
  val x = width / 2
  val y = height / 2
  paint.color = Color.argb(190, 0, 0, 0)
  canvas.drawCircle(x, y, 39f, paint)
  paint.color = Color.WHITE
  canvas.drawRoundRect(x - 9, y - 25, x + 9, y + 7, 9f, 9f, paint)
  paint.style = Paint.Style.STROKE
  paint.strokeWidth = 3f
  canvas.drawArc(RectF(x - 16, y - 20, x + 16, y + 20), 0f, 180f, false, paint)
  canvas.drawLine(x, y + 20, x, y + 30, paint)
  canvas.drawLine(x - 10, y + 30, x + 10, y + 30, paint)

  val margin = (width * .04f).coerceAtLeast(8f)
  val inner = (width - margin * 2 - 24).toInt().coerceAtLeast(1)
  val textPaint = TextPaint(Paint.ANTI_ALIAS_FLAG).apply {
    color = Color.WHITE
    textSize = (width / 25).coerceIn(16f, 26f)
  }
  val text = speech.text.takeLast(100).ifBlank { "Listening..." }
  val layout = StaticLayout.Builder.obtain(text, 0, text.length, textPaint, inner)
    .setAlignment(Layout.Alignment.ALIGN_NORMAL).setIncludePad(false)
    .setMaxLines(3).setEllipsize(TextUtils.TruncateAt.END).build()
  val bottom = height - margin
  val top = (bottom - layout.height - 24).coerceAtLeast(0f)
  paint.style = Paint.Style.FILL
  paint.color = Color.argb(230, 12, 16, 24)
  canvas.drawRoundRect(margin, top, width - margin, bottom, 10f, 10f, paint)
  paint.style = Paint.Style.STROKE
  paint.strokeWidth = 1f
  paint.color = Color.WHITE
  canvas.drawRoundRect(margin, top, width - margin, bottom, 10f, 10f, paint)
  val saved = canvas.save()
  canvas.translate(margin + 12, top + 12)
  layout.draw(canvas)
  canvas.restoreToCount(saved)
}
