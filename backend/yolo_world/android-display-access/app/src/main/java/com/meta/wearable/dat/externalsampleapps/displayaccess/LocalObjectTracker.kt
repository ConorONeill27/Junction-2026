package com.meta.wearable.dat.externalsampleapps.displayaccess

import kotlin.math.*

/** Small upright luminance image; no Android dependency, so tracking can be tested off device. */
data class GrayFrame(val width: Int, val height: Int, val pixels: IntArray)

/** Camera-to-world matrices use image axes (right, down, forward). */
data class ImageMotion(val previous: FloatArray?, val current: FloatArray?, val dx: Double = 0.0, val dy: Double = 0.0, val focalX: Double = .9, val focalY: Double = .9) {
  fun point(x: Double, y: Double): Pair<Double, Double> {
    if (previous == null || current == null) return (x + dx) to (y + dy)
    // Approximate normalized intrinsics; visual flow corrects translation and calibration error.
    val ray = doubleArrayOf((x - .5) / focalX, (y - .5) / focalY, 1.0)
    val world = DoubleArray(3) { row -> (0..2).sumOf { previous[row * 3 + it] * ray[it] } }
    val camera = DoubleArray(3) { col -> (0..2).sumOf { current[it * 3 + col] * world[it] } }
    if (camera[2] <= .1) return (x + dx) to (y + dy)
    return (.5 + focalX * camera[0] / camera[2] + dx) to (.5 + focalY * camera[1] / camera[2] + dy)
  }

  fun box(box: List<Double>): List<Double> {
    val points = listOf(point(box[0], box[1]), point(box[2], box[1]), point(box[2], box[3]), point(box[0], box[3]))
    return listOf(points.minOf { it.first }, points.minOf { it.second }, points.maxOf { it.first }, points.maxOf { it.second })
  }
}

/** Sparse patch flow, with a rotation prior and forward/backward rejection. Runs only on the camera thread. */
object SparseImageFlow {
  private fun score(a: GrayFrame, b: GrayFrame, x: Int, y: Int, u: Int, v: Int): Double {
    if (minOf(x, y, u, v) < 2 || x >= a.width - 2 || y >= a.height - 2 || u >= b.width - 2 || v >= b.height - 2) return Double.POSITIVE_INFINITY
    var error = 0.0
    for (j in -2..2) for (i in -2..2) {
      val delta = a.pixels[(y + j) * a.width + x + i] - b.pixels[(v + j) * b.width + u + i]
      error += delta * delta
    }
    return error / 25
  }

  private fun search(a: GrayFrame, b: GrayFrame, x: Int, y: Int, guessX: Int, guessY: Int): Pair<Int, Int>? {
    var best = Double.POSITIVE_INFINITY
    var result: Pair<Int, Int>? = null
    for (dy in -4..4) for (dx in -4..4) {
      val error = score(a, b, x, y, guessX + dx, guessY + dy)
      if (error < best) { best = error; result = (guessX + dx) to (guessY + dy) }
    }
    return result.takeIf { best < 900.0 }
  }

  fun estimate(a: GrayFrame, b: GrayFrame, box: List<Double>, prior: ImageMotion): ImageMotion? {
    val residuals = mutableListOf<Pair<Double, Double>>()
    for (row in 1..3) for (col in 1..3) {
      val nx = box[0] + (box[2] - box[0]) * col / 4
      val ny = box[1] + (box[3] - box[1]) * row / 4
      val x = (nx * a.width).roundToInt(); val y = (ny * a.height).roundToInt()
      if (x !in 3 until a.width - 3 || y !in 3 until a.height - 3) continue
      var mean = 0.0; var square = 0.0
      for (j in -2..2) for (i in -2..2) {
        val value = a.pixels[(y + j) * a.width + x + i].toDouble()
        mean += value; square += value * value
      }
      if (square / 25 - (mean / 25).pow(2) < 60) continue
      val predicted = prior.point(x.toDouble() / a.width, y.toDouble() / a.height)
      val moved = search(a, b, x, y, (predicted.first * b.width).roundToInt(), (predicted.second * b.height).roundToInt()) ?: continue
      val back = search(b, a, moved.first, moved.second, x, y) ?: continue
      if (hypot((back.first - x).toDouble(), (back.second - y).toDouble()) > 1.5) continue
      residuals += (moved.first.toDouble() / b.width - predicted.first) to (moved.second.toDouble() / b.height - predicted.second)
    }
    if (residuals.size < 3) return null
    val dx = residuals.map { it.first }.sorted()[residuals.size / 2]
    val dy = residuals.map { it.second }.sorted()[residuals.size / 2]
    if (residuals.count { hypot((it.first - dx) * b.width, (it.second - dy) * b.height) < 2.5 } < 3) return null
    return prior.copy(dx = prior.dx + dx, dy = prior.dy + dy)
  }
}

class LocalObjectTracker {
  private data class Track(var item: YoloItem, var box: List<Double>, var seen: Double, val filters: List<OneEuroFilter>)
  private data class Step(val time: Double, val global: ImageMotion, val objects: Map<String, ImageMotion>)
  private val tracks = linkedMapOf<String, Track>()
  private val history = ArrayDeque<Step>()
  private var previous: GrayFrame? = null
  private var rotation: FloatArray? = null
  private var lastTime = Double.NaN
  private var width = 0
  private var height = 0

  fun reset() { tracks.clear(); history.clear(); previous = null; rotation = null; lastTime = Double.NaN }

  private fun parameters(box: List<Double>) = listOf((box[0] + box[2]) / 2, (box[1] + box[3]) / 2, box[2] - box[0], box[3] - box[1])

  fun frame(gray: GrayFrame, pose: FloatArray?, seconds: Double, imageWidth: Int, imageHeight: Int, focalX: Double = .9 * maxOf(imageWidth, imageHeight) / imageWidth, focalY: Double = .9 * maxOf(imageWidth, imageHeight) / imageHeight): List<YoloItem> {
    if (width != imageWidth || height != imageHeight || (!lastTime.isNaN() && seconds - lastTime > .5)) reset()
    width = imageWidth; height = imageHeight
    val old = previous
    if (old != null) {
      val prior = ImageMotion(rotation, pose, focalX = focalX, focalY = focalY)
      val global = SparseImageFlow.estimate(old, gray, listOf(.05, .05, .95, .95), prior) ?: prior
      val motions = mutableMapOf<String, ImageMotion>()
      tracks.forEach { (id, track) ->
        val motion = SparseImageFlow.estimate(old, gray, track.box, global) ?: global
        motions[id] = motion
        val before = parameters(track.box)
        val after = parameters(prior.box(track.box))
        track.box = motion.box(track.box)
        // Do not low-pass known camera motion: smooth detection residuals, not the world moving past the camera.
        track.filters.forEachIndexed { index, filter -> filter.shift(after[index] - before[index]) }
      }
      history.addLast(Step(seconds, global, motions))
      while (history.isNotEmpty() && seconds - history.first().time > 1.5) history.removeFirst()
    }
    previous = gray; rotation = pose?.clone(); lastTime = seconds
    tracks.entries.removeAll { seconds - it.value.seen > 1.2 }
    return tracks.values.mapNotNull { track ->
      val raw = parameters(track.box)
      val p = raw.mapIndexed { i, value -> track.filters[i].filter(value, seconds) }
      val box = listOf(p[0] - p[2] / 2, p[1] - p[3] / 2, p[0] + p[2] / 2, p[1] + p[3] / 2)
      if (box[2] <= 0 || box[3] <= 0 || box[0] >= 1 || box[1] >= 1) null
      else track.item.copy(box = box.mapIndexed { i, value -> value.coerceIn(0.0, 1.0) * if (i % 2 == 0) width else height })
    }
  }

  /** Apply detector observations in their capture-time coordinates, replaying local motion to now. */
  fun correct(items: List<YoloItem>, captured: Double, now: Double, imageWidth: Int, imageHeight: Int) {
    if (imageWidth != width || imageHeight != height || now - captured > 1.0 || captured > lastTime) return
    val used = mutableSetOf<String>()
    items.filter { it.box.size == 4 && it.box.all(Double::isFinite) }.take(24).forEach { item ->
      var box = item.box.mapIndexed { i, value -> value / if (i % 2 == 0) width else height }
      if (box[2] <= box[0] || box[3] <= box[1]) return@forEach
      history.filter { it.time > captured }.forEach { box = (it.objects[item.id] ?: it.global).box(box) }
      val same = tracks[item.id]?.takeIf { item.id !in used }
      val match = same ?: tracks.values.filter { it.item.id !in used && it.item.label == item.label }
        .maxByOrNull { overlap(it.box, box) }?.takeIf { overlap(it.box, box) > .35 }
      if (match != null) {
        used += match.item.id
        match.item = item.copy(id = match.item.id)
        match.box = box; match.seen = now
      } else {
        val filters = List(4) { OneEuroFilter() }
        parameters(box).forEachIndexed { i, value -> filters[i].filter(value, now) }
        tracks[item.id] = Track(item, box, now, filters)
        used += item.id
      }
    }
  }

  private fun overlap(a: List<Double>, b: List<Double>): Double {
    val intersection = max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    val union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - intersection
    return if (union > 0) intersection / union else 0.0
  }
}
