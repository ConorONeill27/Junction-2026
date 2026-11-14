package com.meta.wearable.dat.externalsampleapps.displayaccess

import kotlin.math.*

/** Short, bounded gyro extrapolation from a fused orientation. All timestamps use the receiving phone's clock. */
class MotionPredictor {
  private val orientations = ArrayDeque<Pair<Long, FloatArray>>()
  private val gyros = ArrayDeque<Pair<Long, FloatArray>>()

  @Synchronized fun orientation(value: FloatArray, timestamp: Long) {
    if (value.size != 9 || value.any { !it.isFinite() }) return
    orientations.addLast(timestamp to value.clone())
    while (orientations.size > 120) orientations.removeFirst()
  }
  @Synchronized fun angularVelocity(x: Float, y: Float, z: Float, timestamp: Long) {
    if (listOf(x, y, z).any { !it.isFinite() }) return
    gyros.addLast(timestamp to floatArrayOf(x, y, z))
    while (gyros.size > 120) gyros.removeFirst()
  }
  @Synchronized fun clear() { orientations.clear(); gyros.clear() }

  @Synchronized fun predict(timestamp: Long): FloatArray? {
    val (at, base) = orientations.lastOrNull { it.first <= timestamp } ?: return null
    if (timestamp - at !in 0..150_000_000L) return null
    val (gyroAt, gyro) = gyros.lastOrNull { it.first <= timestamp } ?: return base.clone()
    if (timestamp - gyroAt !in 0..100_000_000L) return base.clone()
    val dt = ((timestamp - at) / 1e9).coerceIn(0.0, .035)
    val speed = sqrt(gyro.sumOf { it.toDouble() * it })
    if (speed < 1e-6) return base.clone()
    val x = gyro[0] / speed; val y = gyro[1] / speed; val z = gyro[2] / speed
    val c = cos(speed * dt); val s = sin(speed * dt); val t = 1 - c
    val delta = doubleArrayOf(t*x*x+c, t*x*y-s*z, t*x*z+s*y,
      t*x*y+s*z, t*y*y+c, t*y*z-s*x, t*x*z-s*y, t*y*z+s*x, t*z*z+c)
    return FloatArray(9) { i -> (0..2).sumOf { base[(i / 3) * 3 + it] * delta[it * 3 + i % 3] }.toFloat() }
  }
}
