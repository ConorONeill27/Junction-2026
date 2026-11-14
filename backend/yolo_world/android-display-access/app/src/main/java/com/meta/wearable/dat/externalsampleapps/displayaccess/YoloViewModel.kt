package com.meta.wearable.dat.externalsampleapps.displayaccess

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URI

data class YoloItem(val id: String, val label: String, val confidence: Double, val box: List<Double>, val note: String? = null) {
  val center: String get() = "(${((box[0] + box[2]) / 2).toInt()}, ${((box[1] + box[3]) / 2).toInt()}) px"
}
data class YoloState(val status: String = "Enter your laptop address to connect", val items: List<YoloItem> = emptyList(), val sample: Boolean = false, val width: Int = 480, val height: Int = 640)

class YoloViewModel(application: Application) : AndroidViewModel(application) {
  @Volatile var usingGlassesCamera = false
    private set
  private val glassesFrameBusy = java.util.concurrent.atomic.AtomicBoolean(false)
  private val localTracker = LocalObjectTracker()
  private val phoneMotion = MotionPredictor()
  private val glassesMotion = MotionPredictor()
  private val cursorController = HeadCursorController()
  private val mutableCursor = MutableStateFlow(HeadCursorState())
  val cursor = mutableCursor.asStateFlow()
  private var lastCursorPoseAt = 0L

  private fun updateHeadCursor(rotation: FloatArray) {
    val now = android.os.SystemClock.elapsedRealtimeNanos()
    if (now - lastCursorPoseAt < 33_000_000L) return
    lastCursorPoseAt = now
    mutableCursor.value = cursorController.updatePose(rotation)
  }

  fun pinchCursor(hitState: YoloState? = null): Boolean {
    val wasVisible = mutableCursor.value.visible
    mutableCursor.value = cursorController.pinch { x, y -> yoloLabelAt(hitState ?: interactionState(), x, y) }
    android.util.Log.i("WorldGitCursor", "Pinch: ${mutableCursor.value}")
    return wasVisible && !mutableCursor.value.visible
  }

  private val sensors = application.getSystemService(android.content.Context.SENSOR_SERVICE) as android.hardware.SensorManager
  @Volatile private var cameraRotation: FloatArray? = null
  @Volatile private var simulatedRotation: FloatArray? = null
  // Simulator entry point: row-major camera-to-world rotation, matching the rotated JPEG axes.
  fun setSimulatedCameraRotation(rotation: FloatArray?) {
    require(rotation == null || (rotation.size == 9 && rotation.all { it.isFinite() }))
    simulatedRotation = rotation?.clone()
    (simulatedRotation ?: cameraRotation)?.let { updateHeadCursor(it) }
  }
  fun setGlassesMotion(rotation: FloatArray?, gyro: FloatArray?) {
    val now = android.os.SystemClock.elapsedRealtimeNanos()
    rotation?.let { glassesMotion.orientation(it, now) }
    gyro?.takeIf { it.size == 3 }?.let { glassesMotion.angularVelocity(it[0], it[1], it[2], now) }
    glassesMotion.predict(now + 15_000_000L)?.let { updateHeadCursor(it) }
  }

  fun clearGlassesMotion() { glassesMotion.clear() }

  private val sensorListener = object : android.hardware.SensorEventListener {
    override fun onAccuracyChanged(sensor: android.hardware.Sensor?, accuracy: Int) {}
    override fun onSensorChanged(event: android.hardware.SensorEvent) {
      if (event.sensor.type == android.hardware.Sensor.TYPE_GYROSCOPE) {
        phoneMotion.angularVelocity(event.values[0], -event.values[1], -event.values[2], event.timestamp)
        return
      }
      val rotation = FloatArray(9)
      android.hardware.SensorManager.getRotationMatrixFromVector(rotation, event.values)
      // Portrait JPEG camera axes: right, down, forward; Android device: right, up, out.
      for (row in 0..2) { rotation[row * 3 + 1] *= -1f; rotation[row * 3 + 2] *= -1f }
      cameraRotation = rotation
      phoneMotion.orientation(rotation, event.timestamp)
      updateHeadCursor(simulatedRotation ?: glassesMotion.predict(android.os.SystemClock.elapsedRealtimeNanos() + 15_000_000L) ?: rotation)
    }
  }
  private val preferences = application.getSharedPreferences("yolo", 0)
  private val mutableCalibration = MutableStateFlow(HudCalibration(
    preferences.getFloat("hud-size",1f), preferences.getFloat("hud-x",0f), preferences.getFloat("hud-y",0f), preferences.getFloat("hud-roll",0f)))
  val calibration = mutableCalibration.asStateFlow()
  fun applyEstimatedCalibration() { updateCalibration(HudAlignmentEstimate.estimate()) }
  fun updateCalibration(value: HudCalibration) {
    val wasGuiding = mutableCalibration.value.showGuide
    mutableCalibration.value = value
    preferences.edit().putFloat("hud-size",value.size).putFloat("hud-x",value.horizontal)
      .putFloat("hud-y",value.vertical).putFloat("hud-roll",value.roll).apply()
    if (wasGuiding && !value.showGuide) saveCalibrationToServer()
  }
  fun saveCalibrationToServer(address: String = savedAddress) {
    val cal = mutableCalibration.value
    android.util.Log.i("WorldGitCal", "Saved HUD calibration: size=${cal.size} x=${cal.horizontal} y=${cal.vertical} roll=${cal.roll}")
    val target = address.trim().trimEnd('/').ifBlank { savedAddress }
    viewModelScope.launch(Dispatchers.IO) {
      runCatching {
        val body = JSONObject()
          .put("size", cal.size.toDouble())
          .put("horizontal", cal.horizontal.toDouble())
          .put("vertical", cal.vertical.toDouble())
          .put("roll", cal.roll.toDouble())
          .toString()
          .toByteArray()
        val conn = URI("$target/api/calibration").toURL().openConnection() as HttpURLConnection
        conn.connectTimeout = 2000
        conn.readTimeout = 2000
        conn.requestMethod = "POST"
        conn.doOutput = true
        conn.setRequestProperty("Content-Type", "application/json")
        conn.outputStream.use { it.write(body) }
        conn.inputStream.use { it.readBytes() }
        conn.disconnect()
      }
    }
  }
  fun interactionState(): YoloState {
    val current = mutableState.value
    return if (usingGlassesCamera) HudProjection(current.width,current.height,mutableCalibration.value).state(current) else current
  }
  val savedAddress: String get() = preferences.getString("address", "http://127.0.0.1:8765")!!
  private val mutableState = MutableStateFlow(YoloState())
  val state = mutableState.asStateFlow()
  @Volatile private var base = savedAddress
  @Volatile private var serverRotatesImages = false
  private val generation = java.util.concurrent.atomic.AtomicInteger(0)
  @Volatile private var lastResult = 0L
  init {
    if (!preferences.getBoolean("independent-estimate-v1", false)) {
      applyEstimatedCalibration()
      preferences.edit().putBoolean("independent-estimate-v1",true).apply()
    }
    viewModelScope.launch {
      while (true) {
        delay(1000)
        if (mutableState.value.items.isNotEmpty() && System.currentTimeMillis() - lastResult > 3000)
          mutableState.value = YoloState("Waiting for fresh phone detections...")
      }
    }
  }
  private var cameraMode = ""
  private var captureCount = 0
  private var captureEpoch = 0L
  @Volatile private var captureFps = 0.0
  private var trackingCount = 0
  private var trackingEpoch = 0L
  private var trackingFps = 0.0
  private var nextUploadAt = 0L // Nanoseconds, camera thread only.
  private val busy = java.util.concurrent.atomic.AtomicBoolean(false)
  private var camera: PhoneCamera? = null
  private val cameraThread = android.os.HandlerThread("PhoneCamera").apply { start() }
  private val handler = android.os.Handler(cameraThread.looper)
  private val mutablePreview = MutableStateFlow<android.graphics.Bitmap?>(null)
  val preview = mutablePreview.asStateFlow()

  fun connect(address: String) {
    val uri = runCatching { URI(address.trim().trimEnd('/')) }.getOrNull()
    if (uri?.scheme !in listOf("http", "https") || uri?.host.isNullOrBlank()) {
      mutableState.value = YoloState("Enter http://<laptop-IP>:8765")
      return
    }
    generation.incrementAndGet()
    handler.post { localTracker.reset() }
    base = uri.toString()
    serverRotatesImages = false
    preferences.edit().putString("address", base).apply()
    mutableState.value = YoloState("Waiting for phone camera...")
  }

  fun startCamera() {
    handler.post {
      if (camera != null || usingGlassesCamera) return@post
      sensors.getDefaultSensor(android.hardware.Sensor.TYPE_GAME_ROTATION_VECTOR)?.let {
        sensors.registerListener(sensorListener, it, android.hardware.SensorManager.SENSOR_DELAY_FASTEST, handler)
      }
      sensors.getDefaultSensor(android.hardware.Sensor.TYPE_GYROSCOPE)?.let {
        sensors.registerListener(sensorListener, it, android.hardware.SensorManager.SENSOR_DELAY_GAME, handler)
      }
      phoneMotion.clear()
      localTracker.reset()
      captureEpoch = android.os.SystemClock.elapsedRealtimeNanos()
      captureCount = 0
      nextUploadAt = 0L
      trackingEpoch = 0L
      trackingCount = 0
      camera = PhoneCamera(getApplication(), handler)
      camera?.start(onMode = { cameraMode = it }, onError = { error ->
        sensors.unregisterListener(sensorListener)
        camera?.close(); camera = null
        mutableState.value = YoloState("Camera unavailable: $error")
      }, onFrame = ::processFrame)
    }
  }

  private fun processFrame(frame: CameraFrame) {
          val bitmap = frame.bitmap
          // Measure delivered camera frames independently of tracking responses.
          val capturedAt = frame.timestampNanos
          captureCount++
          if (capturedAt - captureEpoch >= 1_000_000_000L) {
            captureFps = captureCount * 1_000_000_000.0 / (capturedAt - captureEpoch)
            captureEpoch = capturedAt
            captureCount = 0
          }
          mutablePreview.value = bitmap
          val pose = if (usingGlassesCamera) glassesMotion.predict(capturedAt) else simulatedRotation?.clone() ?: phoneMotion.predict(capturedAt)
          val ratio = minOf(1f, 160f / maxOf(bitmap.width, bitmap.height))
          val small = android.graphics.Bitmap.createScaledBitmap(bitmap,
            (bitmap.width * ratio).toInt().coerceAtLeast(1), (bitmap.height * ratio).toInt().coerceAtLeast(1), true)
          val pixels = IntArray(small.width * small.height)
          small.getPixels(pixels, 0, small.width, 0, 0, small.width, small.height)
          val gray = GrayFrame(small.width, small.height, IntArray(pixels.size) { index ->
            val pixel = pixels[index]
            (77 * ((pixel shr 16) and 255) + 150 * ((pixel shr 8) and 255) + 29 * (pixel and 255)) shr 8
          })
          if (small !== bitmap) small.recycle()
          val tracked = localTracker.frame(gray, pose, capturedAt / 1e9, bitmap.width, bitmap.height, frame.focalX, frame.focalY)
          val status = if (System.currentTimeMillis() - lastResult > 3000) "Waiting for YOLO corrections..." else
            "Camera %.0f FPS | YOLO %.1f FPS".format(captureFps, trackingFps)
          mutableState.value = YoloState(status, tracked, false, bitmap.width, bitmap.height)
          val now = capturedAt
          if (now + 1_000_000L >= nextUploadAt && busy.compareAndSet(false, true)) {
            nextUploadAt = maxOf(now, nextUploadAt + 200_000_000L)
            val target = base
            val requestGeneration = generation.get()
            viewModelScope.launch(Dispatchers.IO) {
              var connection: HttpURLConnection? = null
              var succeeded = false
              try {
                val encodeStarted = android.os.SystemClock.elapsedRealtimeNanos()
                // Older laptop servers require upright JPEGs. Negotiate before using raw camera JPEGs.
                val useCameraJpeg = serverRotatesImages && frame.jpeg.isNotEmpty()
                val payload = if (useCameraJpeg) frame.jpeg else java.io.ByteArrayOutputStream().also {
                  bitmap.compress(android.graphics.Bitmap.CompressFormat.JPEG, 70, it)
                }.toByteArray()
                val requestStarted = android.os.SystemClock.elapsedRealtimeNanos()
                connection = URI("$target/api/frame").toURL().openConnection() as HttpURLConnection
                connection.connectTimeout = 3000
                connection.readTimeout = 3000
                connection.requestMethod = "POST"
                connection.doOutput = true
                connection.setRequestProperty("Content-Type", "image/jpeg")
                connection.setRequestProperty("X-Tracking-Mode", "phone")
                connection.setRequestProperty("X-Image-Rotation", if (useCameraJpeg) frame.rotation.toString() else "0")
                pose?.let { connection.setRequestProperty("X-Camera-Rotation", it.joinToString(",")) }
                connection.setFixedLengthStreamingMode(payload.size)
                connection.outputStream.use { it.write(payload) }
                val json = JSONObject(connection.inputStream.bufferedReader().use { it.readText() })
                succeeded = true
                val array = json.getJSONArray("detections")
                val items = (0 until array.length()).map { index ->
                  val item = array.getJSONObject(index)
                  val box = item.getJSONArray("bbox_xyxy")
                  val note = item.optString("note").takeIf { it.isNotBlank() }
                  YoloItem(item.optString("object_id", "object-$index"), item.getString("label"),
                    item.getDouble("confidence"), (0..3).map { box.getDouble(it) }, note)
                }
                val frameSize = json.getJSONArray("frame_size_px")
                require(frameSize.getInt(0) == bitmap.width && frameSize.getInt(1) == bitmap.height) {
                  "Camera and detection coordinate spaces differ"
                }
                serverRotatesImages = json.optBoolean("supports_image_rotation", false)
                val completedAt = android.os.SystemClock.elapsedRealtimeNanos()
                if (trackingEpoch == 0L) trackingEpoch = completedAt
                trackingCount++
                if (completedAt - trackingEpoch >= 1_000_000_000L) {
                  trackingFps = trackingCount * 1_000_000_000.0 / (completedAt - trackingEpoch)
                  trackingEpoch = completedAt
                  trackingCount = 0
                  android.util.Log.i("WorldGitCamera", "Delivered camera FPS=$captureFps; flow response FPS=$trackingFps; encode ms=${(requestStarted - encodeStarted) / 1_000_000.0}; request ms=${(completedAt - requestStarted) / 1_000_000.0}; server ms=${json.optDouble("inference_seconds") * 1000}")
                }
                lastResult = System.currentTimeMillis()
                handler.post {
                  if (generation.get() == requestGeneration) localTracker.correct(items, capturedAt / 1e9,
                    android.os.SystemClock.elapsedRealtimeNanos() / 1e9, frameSize.getInt(0), frameSize.getInt(1))
                }
              } catch (error: CancellationException) { throw error
              } catch (error: Exception) {
                if (generation.get() == requestGeneration) mutableState.value = YoloState("Disconnected. Start laptop YOLO in phone mode; check address / USB forwarding.")
                delay(1000)
              } finally { if (!succeeded) connection?.disconnect(); busy.set(false) }
            }
          }
  }

  fun startGlassesCamera() {
    stopCamera()
    usingGlassesCamera = true
    handler.post {
      localTracker.reset()
      captureEpoch = android.os.SystemClock.elapsedRealtimeNanos()
      captureCount = 0; nextUploadAt = 0L; trackingEpoch = 0L; trackingCount = 0
      cameraMode = "Meta glasses camera"
      mutableState.value = YoloState("Waiting for glasses camera...")
    }
  }

  fun receiveGlassesFrame(data: ByteArray, width: Int, height: Int, receivedAt: Long) {
    if (!usingGlassesCamera || !glassesFrameBusy.compareAndSet(false, true)) return
    val currentGeneration = generation.get()
    handler.post {
      try {
        if (!usingGlassesCamera || generation.get() != currentGeneration) return@post
        val pixels = GlassesFramePixels.i420ToArgb(data, width, height)
        val bitmap = android.graphics.Bitmap.createBitmap(pixels, width, height, android.graphics.Bitmap.Config.ARGB_8888)
        // Keep gyro prediction in the same estimated camera model as HUD projection.
        val focal = kotlin.math.hypot(width.toDouble(),height.toDouble()) / 2 / mutableCalibration.value.size.coerceIn(.2f,3f)
        processFrame(CameraFrame(bitmap, ByteArray(0), 0, receivedAt, focal / width, focal / height))
      } catch (error: Exception) {
        android.util.Log.e("WorldGitCamera", "Glasses frame rejected", error)
        mutableState.value = YoloState("Glasses video error: ${error.message}")
      } finally { glassesFrameBusy.set(false) }
    }
  }

  fun stopCamera() {
    usingGlassesCamera = false
    mutableCursor.value = cursorController.hide()
    generation.incrementAndGet()
    sensors.unregisterListener(sensorListener)
    handler.post {
      camera?.close(); camera = null
      localTracker.reset(); phoneMotion.clear()
      mutablePreview.value = null
      mutableState.value = YoloState("Camera paused")
    }
  }

  override fun onCleared() {
    stopCamera()
    cameraThread.quitSafely()
  }
}
