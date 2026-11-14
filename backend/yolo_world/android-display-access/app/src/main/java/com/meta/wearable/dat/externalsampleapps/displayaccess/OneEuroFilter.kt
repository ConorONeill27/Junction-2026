package com.meta.wearable.dat.externalsampleapps.displayaccess

import kotlin.math.PI
import kotlin.math.abs

/** Casiez et al., CHI 2012. Coordinates are normalized, time is monotonic seconds. */
class OneEuroFilter(
  private val minCutoff: Double = 1.2,
  private val beta: Double = 3.0,
  private val derivativeCutoff: Double = 1.0,
) {
  private var time = Double.NaN
  private var raw = 0.0
  private var value = 0.0
  private var derivative = 0.0

  fun shift(delta: Double) { raw += delta; value += delta }

  fun filter(input: Double, seconds: Double): Double {
    if (!input.isFinite() || !seconds.isFinite()) return value
    if (time.isNaN() || seconds - time > 0.5) {
      time = seconds; raw = input; value = input; derivative = 0.0
      return input
    }
    if (seconds <= time) return value
    val dt = seconds - time
    fun alpha(cutoff: Double) = 1.0 / (1.0 + 1.0 / (2.0 * PI * cutoff * dt))
    derivative += alpha(derivativeCutoff) * ((input - raw) / dt - derivative)
    value += alpha(minCutoff + beta * abs(derivative)) * (input - value)
    raw = input; time = seconds
    return value
  }
}
