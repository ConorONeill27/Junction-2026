package com.meta.wearable.dat.externalsampleapps.displayaccess

import kotlin.math.atan2

/** Normalized camera-image coordinates, shared by phone and glasses. */
data class HeadCursorState(
  val visible: Boolean = false,
  val x: Float = 0.5f,
  val y: Float = 0.5f,
  val selectedId: String? = null,
)

class HeadCursorController {
  var state = HeadCursorState()
    private set
  private var latestPose: FloatArray? = null
  private var referencePose: FloatArray? = null

  @Synchronized
  fun updatePose(cameraToWorld: FloatArray): HeadCursorState {
    if (cameraToWorld.size != 9 || cameraToWorld.any { !it.isFinite() }) return state
    latestPose = cameraToWorld.clone()
    if (!state.visible) return state
    val reference = referencePose
    if (reference == null) {
      referencePose = latestPose?.clone()
      return state
    }
    // Project the forward ray into the camera axes at the first pinch.
    // This avoids Euler wraparound and compensates for initial head roll.
    fun component(column: Int): Double = (0..2).sumOf { row ->
      reference[row * 3 + column].toDouble() * cameraToWorld[row * 3 + 2]
    }
    val forward = component(2)
    state = state.copy(
      x = (0.5 + atan2(component(0), forward) / Math.toRadians(60.0)).toFloat().coerceIn(0f, 1f),
      y = (0.5 + atan2(component(1), forward) / Math.toRadians(45.0)).toFloat().coerceIn(0f, 1f),
    )
    return state
  }

  @Synchronized
  fun pinch(hitTest: (Float, Float) -> String?): HeadCursorState {
    state = if (!state.visible) {
      referencePose = latestPose?.clone()
      HeadCursorState(visible = true)
    } else {
      val hit = hitTest(state.x, state.y)
      if (hit == null) {
        referencePose = null
        HeadCursorState()
      } else state.copy(selectedId = hit)
    }
    return state
  }

  @Synchronized
  fun hide(): HeadCursorState {
    referencePose = null
    state = HeadCursorState()
    return state
  }
}
