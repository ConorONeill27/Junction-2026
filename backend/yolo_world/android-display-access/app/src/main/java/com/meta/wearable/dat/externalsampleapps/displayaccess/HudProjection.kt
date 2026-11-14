package com.meta.wearable.dat.externalsampleapps.displayaccess

import kotlin.math.*

/** Per-wearer alignment. The camera's 90-degree diagonal FOV is an initial estimate, not factory calibration. */
data class HudCalibration(val size: Float = 1f, val horizontal: Float = 0f, val vertical: Float = 0f, val roll: Float = 0f, val showGuide: Boolean = false)

class HudProjection(private val width: Int, private val height: Int, private val calibration: HudCalibration) {
  // Ray-Ban Display: 600x600, 20-degree diagonal FOV. Preserve angular aspect, never stretch portrait video to a square.
  private val cameraFocal = hypot(width.toDouble(), height.toDouble()) / (2 * tan(Math.toRadians(90.0) / 2))
  private val displayFocal = hypot(600.0, 600.0) / (2 * tan(Math.toRadians(20.0) / 2))
  private val scale = displayFocal / cameraFocal * calibration.size.coerceIn(.2f, 3f)
  private val c = cos(Math.toRadians(calibration.roll.toDouble()))
  private val s = sin(Math.toRadians(calibration.roll.toDouble()))

  fun point(x: Double, y: Double): Pair<Double, Double> {
    val dx = (x - width / 2.0) * scale
    val dy = (y - height / 2.0) * scale
    return (300 + calibration.horizontal + c * dx - s * dy) to (300 + calibration.vertical + s * dx + c * dy)
  }

  fun inverse(x: Double, y: Double): Pair<Double, Double> {
    val dx = x - 300 - calibration.horizontal
    val dy = y - 300 - calibration.vertical
    return (width / 2.0 + (c * dx + s * dy) / scale) to (height / 2.0 + (-s * dx + c * dy) / scale)
  }

  fun state(source: YoloState): YoloState = source.copy(width = 600, height = 600, items = source.items.mapNotNull { item ->
    val b = item.box
    val corners = listOf(point(b[0], b[1]), point(b[2], b[1]), point(b[2], b[3]), point(b[0], b[3]))
    val box = listOf(corners.minOf { it.first }, corners.minOf { it.second }, corners.maxOf { it.first }, corners.maxOf { it.second })
    if (box[2] <= 0 || box[3] <= 0 || box[0] >= 600 || box[1] >= 600) null else item.copy(box = box)
  })

  fun cameraCursor(cursor: HeadCursorState): HeadCursorState {
    val p = inverse(cursor.x * 600.0, cursor.y * 600.0)
    return cursor.copy(x = (p.first / width).toFloat(), y = (p.second / height).toFloat())
  }
}
