package com.worldgit.hud.spatial

import kotlin.math.*

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

  fun shift(delta: Double) {
    raw += delta
    value += delta
  }

  fun filter(input: Double, seconds: Double): Double {
    if (!input.isFinite() || !seconds.isFinite()) return value
    if (time.isNaN() || seconds - time > 0.5) {
      time = seconds
      raw = input
      value = input
      derivative = 0.0
      return input
    }
    if (seconds <= time) return value
    val dt = seconds - time
    fun alpha(cutoff: Double) = 1.0 / (1.0 + 1.0 / (2.0 * PI * cutoff * dt))
    derivative += alpha(derivativeCutoff) * ((input - raw) / dt - derivative)
    value += alpha(minCutoff + beta * abs(derivative)) * (input - value)
    raw = input
    time = seconds
    return value
  }
}

class MotionPredictor {
  private val orientations = ArrayDeque<Pair<Long, FloatArray>>()
  private val gyros = ArrayDeque<Pair<Long, FloatArray>>()

  @Synchronized
  fun orientation(value: FloatArray, timestamp: Long) {
    if (value.size != 9 || value.any { !it.isFinite() }) return
    orientations.addLast(timestamp to value.clone())
    while (orientations.size > 120) orientations.removeFirst()
  }

  @Synchronized
  fun angularVelocity(x: Float, y: Float, z: Float, timestamp: Long) {
    if (!x.isFinite() || !y.isFinite() || !z.isFinite()) return
    gyros.addLast(timestamp to floatArrayOf(x, y, z))
    while (gyros.size > 120) gyros.removeFirst()
  }

  @Synchronized
  fun clear() {
    orientations.clear()
    gyros.clear()
  }

  @Synchronized
  fun predict(timestamp: Long): FloatArray? {
    val (at, base) = orientations.lastOrNull { it.first <= timestamp } ?: return null
    if (timestamp - at !in 0..150_000_000L) return null
    val (gyroAt, gyro) = gyros.lastOrNull { it.first <= timestamp } ?: return base.clone()
    if (timestamp - gyroAt !in 0..100_000_000L) return base.clone()
    val dt = ((timestamp - at) / 1e9).coerceIn(0.0, 0.035)
    val speed = sqrt(gyro.sumOf { it.toDouble() * it })
    if (speed < 1e-6) return base.clone()
    val x = gyro[0] / speed
    val y = gyro[1] / speed
    val z = gyro[2] / speed
    val c = cos(speed * dt)
    val s = sin(speed * dt)
    val t = 1 - c
    val delta = doubleArrayOf(
      t * x * x + c, t * x * y - s * z, t * x * z + s * y,
      t * x * y + s * z, t * y * y + c, t * y * z - s * x,
      t * x * z - s * y, t * y * z + s * x, t * z * z + c,
    )
    return FloatArray(9) { i -> (0..2).sumOf { base[(i / 3) * 3 + it] * delta[it * 3 + i % 3] }.toFloat() }
  }
}

class GrayFrame(val width: Int, val height: Int, val pixels: IntArray)

data class ImageMotion(
  val previous: FloatArray?,
  val current: FloatArray?,
  val dx: Double = 0.0,
  val dy: Double = 0.0,
  val focalX: Double = 0.9,
  val focalY: Double = 0.9,
) {
  fun point(x: Double, y: Double): Pair<Double, Double> {
    if (previous == null || current == null) return (x + dx) to (y + dy)
    val ray = doubleArrayOf((x - 0.5) / focalX, (y - 0.5) / focalY, 1.0)
    val world = DoubleArray(3) { row -> (0..2).sumOf { previous[row * 3 + it] * ray[it] } }
    val camera = DoubleArray(3) { col -> (0..2).sumOf { current[it * 3 + col] * world[it] } }
    if (camera[2] <= 0.1) return (x + dx) to (y + dy)
    return (0.5 + focalX * camera[0] / camera[2] + dx) to (0.5 + focalY * camera[1] / camera[2] + dy)
  }

  fun box(box: List<Double>): List<Double> {
    val points = listOf(point(box[0], box[1]), point(box[2], box[1]), point(box[2], box[3]), point(box[0], box[3]))
    return listOf(points.minOf { it.first }, points.minOf { it.second }, points.maxOf { it.first }, points.maxOf { it.second })
  }
}

object SparseImageFlow {
  private fun score(a: GrayFrame, b: GrayFrame, x: Int, y: Int, u: Int, v: Int): Double {
    if (minOf(x, y, u, v) < 2 || x >= a.width - 2 || y >= a.height - 2 || u >= b.width - 2 || v >= b.height - 2) {
      return Double.POSITIVE_INFINITY
    }
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
      if (error < best) {
        best = error
        result = (guessX + dx) to (guessY + dy)
      }
    }
    return result.takeIf { best < 900.0 }
  }

  fun estimate(a: GrayFrame, b: GrayFrame, box: List<Double>, prior: ImageMotion): ImageMotion? {
    val residuals = mutableListOf<Pair<Double, Double>>()
    for (row in 1..3) for (col in 1..3) {
      val nx = box[0] + (box[2] - box[0]) * col / 4
      val ny = box[1] + (box[3] - box[1]) * row / 4
      val x = (nx * a.width).roundToInt()
      val y = (ny * a.height).roundToInt()
      if (x !in 3 until a.width - 3 || y !in 3 until a.height - 3) continue
      var mean = 0.0
      var square = 0.0
      for (j in -2..2) for (i in -2..2) {
        val value = a.pixels[(y + j) * a.width + x + i].toDouble()
        mean += value
        square += value * value
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

  fun reset() {
    tracks.clear()
    history.clear()
    previous = null
    rotation = null
    lastTime = Double.NaN
  }

  private fun parameters(box: List<Double>) = listOf((box[0] + box[2]) / 2, (box[1] + box[3]) / 2, box[2] - box[0], box[3] - box[1])

  fun frame(
    gray: GrayFrame,
    pose: FloatArray?,
    seconds: Double,
    imageWidth: Int,
    imageHeight: Int,
    focalX: Double,
    focalY: Double,
  ): List<YoloItem> {
    if (width != imageWidth || height != imageHeight || (!lastTime.isNaN() && seconds - lastTime > 0.5)) reset()
    width = imageWidth
    height = imageHeight
    val old = previous
    if (old != null) {
      val prior = ImageMotion(rotation, pose, focalX = focalX, focalY = focalY)
      val global = SparseImageFlow.estimate(old, gray, listOf(0.05, 0.05, 0.95, 0.95), prior) ?: prior
      val motions = mutableMapOf<String, ImageMotion>()
      tracks.forEach { (id, track) ->
        val motion = SparseImageFlow.estimate(old, gray, track.box, global) ?: global
        motions[id] = motion
        val before = parameters(track.box)
        val after = parameters(prior.box(track.box))
        track.box = motion.box(track.box)
        track.filters.forEachIndexed { index, filter -> filter.shift(after[index] - before[index]) }
      }
      history.addLast(Step(seconds, global, motions))
      while (history.isNotEmpty() && seconds - history.first().time > 1.5) history.removeFirst()
    }
    previous = gray
    rotation = pose?.clone()
    lastTime = seconds
    tracks.entries.removeAll { seconds - it.value.seen > 1.2 }
    return tracks.values.mapNotNull { track ->
      val raw = parameters(track.box)
      val p = raw.mapIndexed { i, value -> track.filters[i].filter(value, seconds) }
      val box = listOf(p[0] - p[2] / 2, p[1] - p[3] / 2, p[0] + p[2] / 2, p[1] + p[3] / 2)
      if (box[2] <= 0 || box[3] <= 0 || box[0] >= 1 || box[1] >= 1) null
      else track.item.copy(box = box.mapIndexed { i, value -> value.coerceIn(0.0, 1.0) * if (i % 2 == 0) width else height })
    }
  }

  fun correct(items: List<YoloItem>, captured: Double, now: Double, imageWidth: Int, imageHeight: Int) {
    if (imageWidth != width || imageHeight != height || now - captured > 1.0 || captured > lastTime) return
    val used = mutableSetOf<String>()
    items.filter { it.box.size == 4 && it.box.all(Double::isFinite) }.take(24).forEach { item ->
      var box = item.box.mapIndexed { i, value -> value / if (i % 2 == 0) width else height }
      if (box[2] <= box[0] || box[3] <= box[1]) return@forEach
      history.filter { it.time > captured }.forEach { box = (it.objects[item.id] ?: it.global).box(box) }
      val same = tracks[item.id]?.takeIf { item.id !in used }
      val match = same ?: tracks.values
        .filter { it.item.id !in used && it.item.label == item.label }
        .maxByOrNull { overlap(it.box, box) }
        ?.takeIf { overlap(it.box, box) > 0.35 }
      if (match != null) {
        used += match.item.id
        match.item = item.copy(id = match.item.id)
        match.box = box
        match.seen = now
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

class HudFramePredictor {
  private var state = YoloState()
  private var at = Double.NaN
  private var pose: FloatArray? = null
  private var velocity = emptyMap<String, List<Double>>()
  private var drawn = emptyMap<String, List<Double>>()
  private var drawnAt = Double.NaN
  private var drawnPose: FloatArray? = null

  fun reset() {
    at = Double.NaN
    drawnAt = Double.NaN
    drawn = emptyMap()
    velocity = emptyMap()
    pose = null
    drawnPose = null
  }

  private fun motion(previous: FloatArray?, current: FloatArray?, calibration: HudCalibration): ImageMotion {
    val focal = hypot(state.width.toDouble(), state.height.toDouble()) / 2 / calibration.size.coerceIn(0.2f, 3f)
    return ImageMotion(previous, current, focalX = focal / maxOf(1, state.width), focalY = focal / maxOf(1, state.height))
  }

  private fun normalized(item: YoloItem) = item.box.mapIndexed { i, v -> v / if (i % 2 == 0) state.width else state.height }

  fun observe(value: YoloState, rotation: FloatArray?, seconds: Double, calibration: HudCalibration) {
    val dt = seconds - at
    if (value.width != state.width || value.height != state.height || dt > 0.5) reset()
    val previous = state.items.associateBy { it.id }
    val m = motion(pose, rotation, calibration)
    velocity = value.items.associate { item ->
      val old = previous[item.id]
      val v = if (old != null && dt in 0.01..0.5) {
        val predicted = m.box(normalized(old))
        val raw = item.box.mapIndexed { i, x -> x / if (i % 2 == 0) value.width else value.height }
        raw.mapIndexed { i, x -> (((x - predicted[i]) / dt).coerceIn(-2.0, 2.0) * 0.4 + (velocity[item.id]?.get(i) ?: 0.0) * 0.6) }
      } else List(4) { 0.0 }
      item.id to v
    }
    state = value
    at = seconds
    pose = rotation?.clone()
  }

  fun sample(rotation: FloatArray?, seconds: Double, calibration: HudCalibration): YoloState {
    if (at.isNaN() || seconds - at > 0.8) return state.copy(items = emptyList())
    val age = (seconds - at).coerceIn(0.0, 0.1)
    val m = motion(pose, rotation, calibration)
    val moveDrawn = motion(drawnPose, rotation, calibration)
    val dt = if (drawnAt.isNaN()) 1.0 else (seconds - drawnAt).coerceIn(0.0, 0.1)
    val alpha = 1 - exp(-dt / 0.045)
    val next = mutableMapOf<String, List<Double>>()
    val output = state.items.map { item ->
      val predicted = m.box(normalized(item)).mapIndexed { i, x -> x + ((velocity[item.id]?.get(i) ?: 0.0) * age).coerceIn(-0.02, 0.02) }
      val old = drawn[item.id]?.let { moveDrawn.box(it) }
      val box = if (old == null) predicted else predicted.mapIndexed { i, x -> old[i] + alpha * (x - old[i]) }
      next[item.id] = box
      item.copy(box = box.mapIndexed { i, x -> x * if (i % 2 == 0) state.width else state.height })
    }
    drawn = next
    drawnAt = seconds
    drawnPose = rotation?.clone()
    return state.copy(items = output)
  }
}

class HudProjection(private val width: Int, private val height: Int, private val calibration: HudCalibration) {
  private val cameraFocal = hypot(width.toDouble(), height.toDouble()) / (2 * tan(Math.toRadians(90.0) / 2))
  private val displayFocal = hypot(600.0, 600.0) / (2 * tan(Math.toRadians(20.0) / 2))
  private val scale = displayFocal / cameraFocal * calibration.size.coerceIn(0.2f, 3f)
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

  fun state(source: YoloState): YoloState = source.copy(
    width = 600,
    height = 600,
    items = source.items.mapNotNull { item ->
      val b = item.box
      val corners = listOf(point(b[0], b[1]), point(b[2], b[1]), point(b[2], b[3]), point(b[0], b[3]))
      val box = listOf(corners.minOf { it.first }, corners.minOf { it.second }, corners.maxOf { it.first }, corners.maxOf { it.second })
      if (box[2] <= 0 || box[3] <= 0 || box[0] >= 600 || box[1] >= 600) null else item.copy(box = box)
    },
  )

  fun cameraCursor(cursor: HeadCursorState): HeadCursorState {
    val p = inverse(cursor.x * 600.0, cursor.y * 600.0)
    return cursor.copy(x = (p.first / maxOf(1, width)).toFloat(), y = (p.second / maxOf(1, height)).toFloat())
  }
}

class HeadCursorController {
  var state = HeadCursorState(visible = true, x = 0.5f, y = 0.5f)
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
    fun component(column: Int): Double = (0..2).sumOf { row ->
      reference[row * 3 + column].toDouble() * cameraToWorld[row * 3 + 2]
    }
    val forward = component(2)
    state = state.copy(
      x = (0.5 + atan2(component(0), forward) / Math.toRadians(60.0)).toFloat().coerceIn(0.03f, 0.97f),
      y = (0.5 + atan2(component(1), forward) / Math.toRadians(45.0)).toFloat().coerceIn(0.03f, 0.90f),
    )
    return state
  }

  @Synchronized
  fun setPointer(xNorm: Float, yNorm: Float): HeadCursorState {
    state = state.copy(
      visible = true,
      x = xNorm.coerceIn(0.03f, 0.97f),
      y = yNorm.coerceIn(0.03f, 0.90f),
    )
    return state
  }

  @Synchronized
  fun updateHoverSelection(selectedId: String?): HeadCursorState {
    if (state.selectedId != selectedId) {
      state = state.copy(selectedId = selectedId)
    }
    return state
  }

  @Synchronized
  fun recenter(): HeadCursorState {
    referencePose = latestPose?.clone()
    state = HeadCursorState(visible = true, x = 0.5f, y = 0.5f, selectedId = null)
    return state
  }
}

object GlassesFramePixels {
  fun i420ToArgb(data: ByteArray, width: Int, height: Int): IntArray {
    require(width in 2..1920 && height in 2..1920 && width % 2 == 0 && height % 2 == 0)
    val size = width * height
    require(data.size == size * 3 / 2) { "Unsupported YUV layout: ${data.size} for ${width}x$height" }
    val out = IntArray(size)
    val uvWidth = width / 2
    val vOffset = size + size / 4
    for (y in 0 until height) {
      val rowOffset = y * width
      val chromaRow = (y shr 1) * uvWidth
      for (x in 0 until width) {
        val index = rowOffset + x
        val chroma = chromaRow + (x shr 1)
        val luma = ((data[index].toInt() and 255) - 16).coerceAtLeast(0)
        val u = (data[size + chroma].toInt() and 255) - 128
        val v = (data[vOffset + chroma].toInt() and 255) - 128
        val red = ((298 * luma + 409 * v + 128) shr 8).coerceIn(0, 255)
        val green = ((298 * luma - 100 * u - 208 * v + 128) shr 8).coerceIn(0, 255)
        val blue = ((298 * luma + 516 * u + 128) shr 8).coerceIn(0, 255)
        out[index] = (255 shl 24) or (red shl 16) or (green shl 8) or blue
      }
    }
    return out
  }
}

class SpeechEndpoint(
  private val sampleRate: Int = 16000,
  private val manualStop: Boolean = false,
  private val maxWallClockMs: Long = 25_000L,
  private val stalledStreamMs: Long = 1_500L,
) {
  var heardSpeech = false
    private set
  private var samples = 0L
  private var lastVoice = 0L
  private var voicedRun = 0
  private var noise = 0.002

  fun add(audio: ShortArray, count: Int): Boolean {
    if (count <= 0) return false
    samples += count
    var sum = 0.0
    for (index in 0 until count) {
      val value = audio[index] / 32768.0
      sum += value * value
    }
    val rms = sqrt(sum / count)
    if (rms > max(0.006, noise * 3)) {
      voicedRun += count
      if (voicedRun >= sampleRate / 10) heardSpeech = true
      lastVoice = samples
    } else {
      voicedRun = 0
      if (!heardSpeech) noise = noise * 0.95 + rms * 0.05
    }
    return !manualStop && (samples >= sampleRate * 25L ||
      (heardSpeech && samples - lastVoice >= sampleRate * 11L / 10) ||
      (!heardSpeech && samples >= sampleRate * 7L))
  }

  fun wallClockDeadlineReason(elapsedMs: Long, sinceLastChunkMs: Long, gotAnyAudio: Boolean): String? {
    if (manualStop) return null
    if (elapsedMs >= maxWallClockMs) return "MAX_WALL_CLOCK"
    if (gotAnyAudio && sinceLastChunkMs >= stalledStreamMs) return "STREAM_STALLED"
    if (!heardSpeech && elapsedMs >= 7_000L) return "INITIAL_SILENCE_TIMEOUT"
    return null
  }
}

object CompassMath {
  /** Extract yaw heading (0..360 deg), pitch deg, and roll deg from image rotation matrix (right/down/forward). */
  fun extractEulerDeg(rot: FloatArray): Triple<Float, Float, Float> {
    if (rot.size != 9 || rot.any { !it.isFinite() }) return Triple(0f, 0f, 0f)
    // Forward vector is column 2 in image-to-world coordinates: (rot[2], rot[5], rot[8])
    val fx = rot[2].toDouble()
    val fz = rot[8].toDouble()
    val fy = rot[5].toDouble()
    val yawRad = atan2(fx, -fz)
    val yawDeg = (((Math.toDegrees(yawRad) % 360.0) + 360.0) % 360.0).toFloat()
    val pitchDeg = Math.toDegrees(-asin(fy.coerceIn(-1.0, 1.0))).toFloat()
    val rollDeg = Math.toDegrees(atan2(rot[3].toDouble(), rot[4].toDouble())).toFloat()
    return Triple(yawDeg, pitchDeg, rollDeg)
  }

  fun cardinalFor(headingDeg: Float): String {
    val dirs = arrayOf("N", "NE", "E", "SE", "S", "SW", "W", "NW")
    val idx = (((headingDeg % 360f + 360f) % 360f) / 45f).roundToInt() % 8
    return dirs[idx]
  }

  fun relativeTargetNav(
    wearerYawDeg: Float,
    wearerPos: Triple<Double, Double, Double>,
    target: SpatialObject?,
  ): Tuple4<Float, String, String, Boolean> {
    if (target == null) return Tuple4(0f, "", "NO TARGET", false)
    val dx = target.x - wearerPos.first
    val dz = target.z - wearerPos.third
    val rawBearing = Math.toDegrees(atan2(dx, -dz))
    val rel = (((rawBearing - wearerYawDeg + 540.0) % 360.0) + 360.0) % 360.0 - 180.0
    val angle = (((rel % 360.0) + 360.0) % 360.0).toFloat()
    val aligned = abs(rel) <= 25.0
    val turn = when {
      aligned -> "AHEAD"
      abs(rel) >= 150.0 -> "TURN AROUND"
      rel > 0 -> "TURN RIGHT"
      else -> "TURN LEFT"
    }
    val glyph = when {
      rel <= -135.0 || rel >= 135.0 -> "↓"
      rel < -60.0 -> "←"
      rel < -25.0 -> "↖"
      rel <= 25.0 -> "↑"
      rel <= 60.0 -> "↗"
      else -> "→"
    }
    return Tuple4(angle, glyph, turn, aligned)
  }

  data class Tuple4<A, B, C, D>(val first: A, val second: B, val third: C, val fourth: D)
}
