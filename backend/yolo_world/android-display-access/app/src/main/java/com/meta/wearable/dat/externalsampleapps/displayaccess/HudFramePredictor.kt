package com.meta.wearable.dat.externalsampleapps.displayaccess

import kotlin.math.*

/** Latest-frame prediction for a 30-Hz HUD; observations retain their own camera cadence. */
class HudFramePredictor {
  private var state = YoloState()
  private var at = Double.NaN
  private var pose: FloatArray? = null
  private var velocity = emptyMap<String, List<Double>>()
  private var drawn = emptyMap<String, List<Double>>()
  private var drawnAt = Double.NaN
  private var drawnPose: FloatArray? = null

  fun reset() { at = Double.NaN; drawnAt = Double.NaN; drawn = emptyMap(); velocity = emptyMap(); pose = null; drawnPose = null }

  private fun motion(previous: FloatArray?, current: FloatArray?, calibration: HudCalibration): ImageMotion {
    val focal = hypot(state.width.toDouble(),state.height.toDouble()) / 2 / calibration.size.coerceIn(.2f,3f)
    return ImageMotion(previous,current,focalX=focal/state.width,focalY=focal/state.height)
  }
  private fun normalized(item: YoloItem) = item.box.mapIndexed { i,v -> v / if (i%2==0) state.width else state.height }

  fun observe(value: YoloState, rotation: FloatArray?, seconds: Double, calibration: HudCalibration) {
    val dt = seconds-at
    if (value.width != state.width || value.height != state.height || dt > .5) reset()
    val previous = state.items.associateBy { it.id }
    val m = motion(pose,rotation,calibration)
    velocity = value.items.associate { item ->
      val old = previous[item.id]
      val v = if (old != null && dt in .01..0.5) {
        val predicted = m.box(normalized(old))
        val raw = item.box.mapIndexed { i,x -> x / if (i%2==0) value.width else value.height }
        raw.mapIndexed { i,x -> (((x-predicted[i])/dt).coerceIn(-2.0,2.0) * .4 + (velocity[item.id]?.get(i) ?: 0.0) * .6) }
      } else List(4) { 0.0 }
      item.id to v
    }
    state = value; at = seconds; pose = rotation?.clone()
  }

  fun sample(rotation: FloatArray?, seconds: Double, calibration: HudCalibration): YoloState {
    if (at.isNaN() || seconds-at > .5) return state.copy(items=emptyList())
    val age = (seconds-at).coerceIn(0.0,.1)
    val m = motion(pose,rotation,calibration)
    val moveDrawn = motion(drawnPose,rotation,calibration)
    val dt = if (drawnAt.isNaN()) 1.0 else (seconds-drawnAt).coerceIn(0.0,.1)
    val alpha = 1-exp(-dt/.045)
    val next = mutableMapOf<String,List<Double>>()
    val output = state.items.map { item ->
      val predicted = m.box(normalized(item)).mapIndexed { i,x -> x+((velocity[item.id]?.get(i) ?: 0.0)*age).coerceIn(-.02,.02) }
      val old = drawn[item.id]?.let { moveDrawn.box(it) }
      val box = if (old == null) predicted else predicted.mapIndexed { i,x -> old[i]+alpha*(x-old[i]) }
      next[item.id] = box
      item.copy(box=box.mapIndexed { i,x -> x * if (i%2==0) state.width else state.height })
    }
    drawn = next; drawnAt = seconds; drawnPose = rotation?.clone()
    return state.copy(items=output)
  }
}

object HudAlignmentEstimate {
  /** Independent initial estimate: 90-degree camera diagonal, camera 90 mm left and 25 mm above right eye, target 2 m away. */
  fun estimate(distanceMetres: Double = 2.0): HudCalibration {
    val focal = hypot(600.0,600.0)/(2*tan(Math.toRadians(20.0)/2))
    val distance = distanceMetres.coerceIn(.5,10.0)
    return HudCalibration(size=1f,horizontal=-(focal*.09/distance).toFloat(),vertical=-(focal*.025/distance).toFloat())
  }
}
