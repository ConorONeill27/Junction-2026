package com.meta.wearable.dat.externalsampleapps.cameraaccess.worldgit

import android.content.Context
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.util.Log
import com.meta.wearable.dat.core.session.DeviceSession
import com.meta.wearable.dat.externalsampleapps.cameraaccess.BuildConfig
import com.meta.wearable.dat.motion.Motion
import com.meta.wearable.dat.motion.addMotion
import com.meta.wearable.dat.motion.removeMotion
import com.meta.wearable.dat.motion.types.MotionConfiguration
import com.meta.wearable.dat.motion.types.MotionSample
import com.meta.wearable.dat.motion.types.MotionSamplingRate
import com.meta.wearable.dat.motion.types.MotionState
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder
import kotlin.math.atan2
import kotlin.math.hypot
import kotlin.math.roundToInt
import kotlin.math.sqrt
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject

data class WorldGitImuLiveState(
    val sourceLabel: String = "idle",
    val isGlassesHardwareImu: Boolean = false,
    val sampleCount: Int = 0,
    val stepCount: Int = 0,
    val compassDeg: Float? = null,
    val yawDeg: Float = 0f,
    val syncStatus: String = "Ready — tap Record to capture Video + 9-axis IMU",
    val isUploading: Boolean = false,
    val hubUrl: String = BuildConfig.WORLDGIT_HUB_URL,
)

class RecordedImuPoint(
    val tMs: Long,
    val compassDeg: Float?,
    val yawDeg: Float,
    val pitchDeg: Float,
    val rollDeg: Float,
    val absolute: Boolean,
    val gyroDps: FloatArray,
    val accelG: FloatArray,
    val accelLin: FloatArray,
    val stepCount: Int,
)

class WorldGitImuRecorder(
    context: Context,
    private val scope: CoroutineScope,
) : SensorEventListener {

  companion object {
    private const val TAG = "WorldGitImuRecorder"
    private const val MAX_SAMPLES = 36000 // 20 mins @ 30Hz
  }

  private val appContext = context.applicationContext
  private val sensorManager =
      appContext.getSystemService(Context.SENSOR_SERVICE) as? SensorManager

  private val _liveState = MutableStateFlow(WorldGitImuLiveState())
  val liveState: StateFlow<WorldGitImuLiveState> = _liveState.asStateFlow()

  private var motion: Motion? = null
  private var motionSampleJob: Job? = null
  private var motionStateJob: Job? = null
  private var motionErrorJob: Job? = null
  private var motionRevivalJob: Job? = null
  @Volatile private var hasStartedMotionOnce = false

  private val sampleLock = Any()
  private val recordedSamples = ArrayList<RecordedImuPoint>(4096)

  @Volatile private var isRecordingWalk = false
  @Volatile private var recordingStartedWallMs = 0L
  @Volatile private var firstGlassesTimestampNs = 0L
  @Volatile private var lastGlassesSampleWallMs = 0L
  @Volatile private var lastSampleRecordedWallMs = 0L
  @Volatile private var lastStepWallMs = 0L
  @Volatile private var stepCount = 0
  @Volatile private var glassesWalkSamples = 0
  @Volatile private var phoneWalkSamples = 0

  private val gVec = floatArrayOf(0f, 9.81f, 0f)
  private val lastMag = floatArrayOf(0f, 0f, 0f)
  private var hasMag = false
  private var gyroYawDeg = 0.0
  private var lastGyroTsNs = 0L
  private var latestCompassDeg: Float? = null
  private var latestPitchDeg = 0f
  private var latestRollDeg = 0f
  private var phoneListenersActive = false

  fun setHubUrl(url: String) {
    val cleaned = url.trim().trimEnd('/')
    if (cleaned.isNotEmpty()) {
      _liveState.update { it.copy(hubUrl = cleaned) }
    }
  }

  fun attachToSession(session: DeviceSession) {
    detachFromSession(session)
    startPhoneFallbackListeners()
    session
        .addMotion(MotionConfiguration(samplingRate = MotionSamplingRate.HZ_10))
        .fold(
            onSuccess = { addedMotion ->
              motion = addedMotion
              hasStartedMotionOnce = false
              _liveState.update {
                it.copy(
                    sourceLabel = "glasses 10Hz (starting)",
                    syncStatus = "Glasses 9-axis IMU attached (10 Hz)",
                )
              }
              motionStateJob =
                  scope.launch {
                    addedMotion.state.collect { st ->
                      Log.d(TAG, "Glasses MotionState: $st")
                      if (st == MotionState.STARTED) {
                        hasStartedMotionOnce = true
                      } else if (st == MotionState.STOPPED && hasStartedMotionOnce) {
                        reviveMotionIfNeeded()
                      }
                    }
                  }
              motionErrorJob =
                  scope.launch {
                    addedMotion.errors.collect { err ->
                      if (err != null) {
                        Log.w(TAG, "Glasses MotionError: $err")
                      }
                    }
                  }
              motionSampleJob =
                  scope.launch(Dispatchers.Default) {
                    addedMotion.samples.collect { sample ->
                      onGlassesMotionSample(sample)
                    }
                  }
              addedMotion.start()
            },
            onFailure = { error, _ ->
              Log.w(TAG, "addMotion failed (${error.description}); using phone IMU fallback")
              _liveState.update {
                it.copy(
                    sourceLabel = "phone 9-axis (fallback)",
                    isGlassesHardwareImu = false,
                )
              }
            },
        )
  }

  private fun reviveMotionIfNeeded() {
    if (motionRevivalJob?.isActive == true) return
    motionRevivalJob =
        scope.launch {
          repeat(5) { attempt ->
            delay(1200L)
            val current = motion ?: return@launch
            if (current.state.value != MotionState.STOPPED) return@launch
            Log.i(TAG, "Glasses Motion stopped unexpectedly; restarting (${attempt + 1}/5)")
            runCatching { current.start() }
          }
        }
  }

  fun detachFromSession(session: DeviceSession?) {
    motionRevivalJob?.cancel()
    motionRevivalJob = null
    hasStartedMotionOnce = false
    motionSampleJob?.cancel()
    motionSampleJob = null
    motionStateJob?.cancel()
    motionStateJob = null
    motionErrorJob?.cancel()
    motionErrorJob = null
    runCatching { motion?.stop() }
    if (session != null && motion != null) {
      runCatching { session.removeMotion() }
    }
    motion = null
    stopPhoneFallbackListeners()
    _liveState.update { it.copy(sourceLabel = "idle", isGlassesHardwareImu = false) }
  }

  fun startRecordingWalk(startedAtWallMs: Long = System.currentTimeMillis()) {
    synchronized(sampleLock) {
      recordedSamples.clear()
      recordingStartedWallMs = startedAtWallMs
      firstGlassesTimestampNs = 0L
      lastSampleRecordedWallMs = 0L
      lastStepWallMs = 0L
      stepCount = 0
      glassesWalkSamples = 0
      phoneWalkSamples = 0
      gyroYawDeg = 0.0
      lastGyroTsNs = 0L
      isRecordingWalk = true
    }
    if (motion?.state?.value == MotionState.STOPPED) {
      Log.i(TAG, "Motion was STOPPED at walk start; calling start()")
      runCatching { motion?.start() }
      reviveMotionIfNeeded()
    }
    _liveState.update {
      it.copy(
          sampleCount = 0,
          stepCount = 0,
          yawDeg = 0f,
          syncStatus = "Recording Video + 9-axis IMU...",
      )
    }
  }

  fun stopAndUploadWalk(
      videoFile: File?,
      startedAtMs: Long,
      endedAtMs: Long = System.currentTimeMillis(),
  ) {
    val snapshot: List<RecordedImuPoint>
    val totalSteps: Int
    val walkGlassesCount: Int
    val walkPhoneCount: Int
    synchronized(sampleLock) {
      isRecordingWalk = false
      snapshot = ArrayList(recordedSamples)
      totalSteps = stepCount
      walkGlassesCount = glassesWalkSamples
      walkPhoneCount = phoneWalkSamples
    }
    val deviceName =
        when {
          walkGlassesCount > 0 && walkPhoneCount == 0 -> "meta-ray-ban-display-dat"
          walkGlassesCount > 0 -> "meta-ray-ban-display-dat-mixed"
          else -> "android-dat-companion"
        }
    Log.i(
        TAG,
        "Walk stopped: total=${snapshot.size}, glasses=$walkGlassesCount, phone=$walkPhoneCount, device=$deviceName",
    )
    val sessionId = "imu_${startedAtMs}"
    val hubUrl = _liveState.value.hubUrl

    scope.launch(Dispatchers.IO) {
      _liveState.update {
        it.copy(
            isUploading = true,
            syncStatus =
                "Uploading ${snapshot.size} IMU pts ($walkGlassesCount glasses / $walkPhoneCount phone)...",
        )
      }
      val resultMsg =
          uploadToWorldGitHub(
              hubUrl = hubUrl,
              sessionId = sessionId,
              deviceName = deviceName,
              startedAtMs = if (startedAtMs > 0L) startedAtMs else recordingStartedWallMs,
              endedAtMs = endedAtMs,
              stepCount = totalSteps,
              samples = snapshot,
              videoFile = videoFile,
          )
      _liveState.update {
        it.copy(
            isUploading = false,
            syncStatus = resultMsg,
        )
      }
    }
  }

  private fun onGlassesMotionSample(sample: MotionSample) {
    val wallNow = System.currentTimeMillis()
    lastGlassesSampleWallMs = wallNow

    val acc = sample.accelerometer
    val gyr = sample.gyroscope
    val mag = sample.magnetometer
    val quat = sample.orientation

    val ax = acc?.x ?: gVec[0]
    val ay = acc?.y ?: gVec[1]
    val az = acc?.z ?: gVec[2]

    // Low-pass gravity vector (m/s^2)
    gVec[0] = 0.82f * gVec[0] + 0.18f * ax
    gVec[1] = 0.82f * gVec[1] + 0.18f * ay
    gVec[2] = 0.82f * gVec[2] + 0.18f * az
    val gn = sqrt(gVec[0] * gVec[0] + gVec[1] * gVec[1] + gVec[2] * gVec[2])

    // Gyroscope in DAT is rad/s -> convert to deg/s for WorldGit imu_fusion.py
    val gxDps = if (gyr != null) Math.toDegrees(gyr.x.toDouble()).toFloat() else 0f
    val gyDps = if (gyr != null) Math.toDegrees(gyr.y.toDouble()).toFloat() else 0f
    val gzDps = if (gyr != null) Math.toDegrees(gyr.z.toDouble()).toFloat() else 0f

    val tsNs = sample.timestampNs
    val dtS =
        if (lastGyroTsNs > 0L && tsNs > lastGyroTsNs) {
          ((tsNs - lastGyroTsNs) / 1_000_000_000.0).coerceIn(0.001, 0.25)
        } else {
          1.0 / 30.0
        }
    lastGyroTsNs = tsNs

    if (gn > 3.0f) {
      val gxHat = gVec[0] / gn
      val gyHat = gVec[1] / gn
      val gzHat = gVec[2] / gn
      val yawRateEarth = gxDps * gxHat + gyDps * gyHat + gzDps * gzHat
      if (kotlin.math.abs(yawRateEarth) >= 0.35f) {
        gyroYawDeg -= yawRateEarth * dtS
      }
      latestPitchDeg = Math.toDegrees(atan2(-gyHat.toDouble(), gzHat.toDouble())).toFloat()
      latestRollDeg =
          Math.toDegrees(
                  atan2(gxHat.toDouble(), hypot(gyHat.toDouble(), gzHat.toDouble())),
              )
              .toFloat()
    }

    if (mag != null) {
      lastMag[0] = mag.x
      lastMag[1] = mag.y
      lastMag[2] = mag.z
      hasMag = true
      val rotMat = FloatArray(9)
      val ori = FloatArray(3)
      if (SensorManager.getRotationMatrix(rotMat, null, gVec, lastMag)) {
        SensorManager.getOrientation(rotMat, ori)
        val azDeg = ((Math.toDegrees(ori[0].toDouble()) + 360.0) % 360.0).toFloat()
        latestCompassDeg = azDeg
      }
    } else if (quat != null) {
      // Extract yaw/pitch/roll from glasses orientation quaternion (x, y, z, w)
      val qx = quat.x.toDouble()
      val qy = quat.y.toDouble()
      val qz = quat.z.toDouble()
      val qw = quat.w.toDouble()
      val sinyCosp = 2.0 * (qw * qz + qx * qy)
      val cosyCosp = 1.0 - 2.0 * (qy * qy + qz * qz)
      val yawQ = ((Math.toDegrees(atan2(sinyCosp, cosyCosp)) + 360.0) % 360.0).toFloat()
      if (latestCompassDeg == null) {
        latestCompassDeg = yawQ
      }
    }

    val linX = ax - gVec[0]
    val linY = ay - gVec[1]
    val linZ = az - gVec[2]
    val linMag = sqrt(linX * linX + linY * linY + linZ * linZ)

    recordSamplePoint(
        wallNow = wallNow,
        glassesTimestampNs = tsNs,
        fromGlasses = true,
        gxDps = gxDps,
        gyDps = gyDps,
        gzDps = gzDps,
        ax = ax,
        ay = ay,
        az = az,
        linX = linX,
        linY = linY,
        linZ = linZ,
        linMag = linMag,
        hasAbsolute = hasMag || quat != null,
    )
  }

  private fun recordSamplePoint(
      wallNow: Long,
      glassesTimestampNs: Long,
      fromGlasses: Boolean,
      gxDps: Float,
      gyDps: Float,
      gzDps: Float,
      ax: Float,
      ay: Float,
      az: Float,
      linX: Float,
      linY: Float,
      linZ: Float,
      linMag: Float,
      hasAbsolute: Boolean,
  ) {
    var countAfter = 0
    var stepsAfter = 0
    synchronized(sampleLock) {
      if (linMag > 1.35f && wallNow - lastStepWallMs > 340L) {
        lastStepWallMs = wallNow
        stepCount += 1
      }
      stepsAfter = stepCount

      if (isRecordingWalk && recordedSamples.size < MAX_SAMPLES && wallNow - lastSampleRecordedWallMs >= 28L) {
        lastSampleRecordedWallMs = wallNow
        val sampleEpochMs =
            if (fromGlasses && glassesTimestampNs > 0L) {
              if (firstGlassesTimestampNs == 0L) {
                firstGlassesTimestampNs = glassesTimestampNs
              }
              recordingStartedWallMs + ((glassesTimestampNs - firstGlassesTimestampNs) / 1_000_000L)
            } else {
              wallNow
            }

        if (fromGlasses) glassesWalkSamples += 1 else phoneWalkSamples += 1
        recordedSamples.add(
            RecordedImuPoint(
                tMs = sampleEpochMs,
                compassDeg = latestCompassDeg,
                yawDeg = gyroYawDeg.toFloat(),
                pitchDeg = latestPitchDeg,
                rollDeg = latestRollDeg,
                absolute = hasAbsolute,
                gyroDps = floatArrayOf(round2(gxDps), round2(gyDps), round2(gzDps)),
                accelG = floatArrayOf(round2(ax), round2(ay), round2(az)),
                accelLin = floatArrayOf(round2(linX), round2(linY), round2(linZ)),
                stepCount = stepCount,
            ),
        )
      }
      countAfter = recordedSamples.size
    }

    _liveState.update {
      it.copy(
          sourceLabel = if (fromGlasses) "glasses 9-axis 10Hz" else "phone 9-axis (fallback)",
          isGlassesHardwareImu = fromGlasses,
          sampleCount = countAfter,
          stepCount = stepsAfter,
          compassDeg = latestCompassDeg,
          yawDeg = ((gyroYawDeg * 10.0).roundToInt() / 10f),
      )
    }
  }

  // Phone SensorManager fallback (used ONLY if glasses mwdat-motion has not emitted samples in >1.5s)
  private fun startPhoneFallbackListeners() {
    if (phoneListenersActive || sensorManager == null) return
    phoneListenersActive = true
    sensorManager.getDefaultSensor(Sensor.TYPE_ACCELEROMETER)?.let {
      sensorManager.registerListener(this, it, SensorManager.SENSOR_DELAY_GAME)
    }
    sensorManager.getDefaultSensor(Sensor.TYPE_GYROSCOPE)?.let {
      sensorManager.registerListener(this, it, SensorManager.SENSOR_DELAY_GAME)
    }
    sensorManager.getDefaultSensor(Sensor.TYPE_MAGNETIC_FIELD)?.let {
      sensorManager.registerListener(this, it, SensorManager.SENSOR_DELAY_GAME)
    }
  }

  private fun stopPhoneFallbackListeners() {
    if (!phoneListenersActive || sensorManager == null) return
    phoneListenersActive = false
    sensorManager.unregisterListener(this)
  }

  private val phoneGyroDps = floatArrayOf(0f, 0f, 0f)

  override fun onSensorChanged(event: SensorEvent?) {
    if (event == null) return
    val wallNow = System.currentTimeMillis()
    // If real glasses mwdat-motion samples are flowing, ignore phone sensors completely!
    if (wallNow - lastGlassesSampleWallMs < 1500L) return

    when (event.sensor.type) {
      Sensor.TYPE_GYROSCOPE -> {
        val gx = Math.toDegrees(event.values[0].toDouble()).toFloat()
        val gy = Math.toDegrees(event.values[1].toDouble()).toFloat()
        val gz = Math.toDegrees(event.values[2].toDouble()).toFloat()
        phoneGyroDps[0] = gx
        phoneGyroDps[1] = gy
        phoneGyroDps[2] = gz
        val tsNs = event.timestamp
        val dtS =
            if (lastGyroTsNs > 0L && tsNs > lastGyroTsNs) {
              ((tsNs - lastGyroTsNs) / 1_000_000_000.0).coerceIn(0.001, 0.25)
            } else {
              0.02
            }
        lastGyroTsNs = tsNs
        val gn = sqrt(gVec[0] * gVec[0] + gVec[1] * gVec[1] + gVec[2] * gVec[2])
        if (gn > 3.0f) {
          val yawRateEarth =
              (gx * gVec[0] + gy * gVec[1] + gz * gVec[2]) / gn
          if (kotlin.math.abs(yawRateEarth) >= 0.35f) {
            gyroYawDeg -= yawRateEarth * dtS
          }
        }
      }
      Sensor.TYPE_MAGNETIC_FIELD -> {
        lastMag[0] = event.values[0]
        lastMag[1] = event.values[1]
        lastMag[2] = event.values[2]
        hasMag = true
        val rotMat = FloatArray(9)
        val ori = FloatArray(3)
        if (SensorManager.getRotationMatrix(rotMat, null, gVec, lastMag)) {
          SensorManager.getOrientation(rotMat, ori)
          latestCompassDeg = ((Math.toDegrees(ori[0].toDouble()) + 360.0) % 360.0).toFloat()
        }
      }
      Sensor.TYPE_ACCELEROMETER -> {
        val ax = event.values[0]
        val ay = event.values[1]
        val az = event.values[2]
        gVec[0] = 0.82f * gVec[0] + 0.18f * ax
        gVec[1] = 0.82f * gVec[1] + 0.18f * ay
        gVec[2] = 0.82f * gVec[2] + 0.18f * az
        val gn = sqrt(gVec[0] * gVec[0] + gVec[1] * gVec[1] + gVec[2] * gVec[2])
        if (gn > 3.0f) {
          latestPitchDeg = Math.toDegrees(atan2((-gVec[1] / gn).toDouble(), (gVec[2] / gn).toDouble())).toFloat()
          latestRollDeg =
              Math.toDegrees(
                      atan2(
                          (gVec[0] / gn).toDouble(),
                          hypot((gVec[1] / gn).toDouble(), (gVec[2] / gn).toDouble()),
                      ),
                  )
                  .toFloat()
        }
        val linX = ax - gVec[0]
        val linY = ay - gVec[1]
        val linZ = az - gVec[2]
        val linMag = sqrt(linX * linX + linY * linY + linZ * linZ)
        recordSamplePoint(
            wallNow = wallNow,
            glassesTimestampNs = 0L,
            fromGlasses = false,
            gxDps = phoneGyroDps[0],
            gyDps = phoneGyroDps[1],
            gzDps = phoneGyroDps[2],
            ax = ax,
            ay = ay,
            az = az,
            linX = linX,
            linY = linY,
            linZ = linZ,
            linMag = linMag,
            hasAbsolute = hasMag,
        )
      }
    }
  }

  override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) = Unit

  private fun round2(v: Float): Float = (v * 100f).roundToInt() / 100f

  private suspend fun uploadToWorldGitHub(
      hubUrl: String,
      sessionId: String,
      deviceName: String,
      startedAtMs: Long,
      endedAtMs: Long,
      stepCount: Int,
      samples: List<RecordedImuPoint>,
      videoFile: File?,
  ): String =
      withContext(Dispatchers.IO) {
        val baseUrl = hubUrl.trimEnd('/')
        var imuOk = false
        try {
          val samplesArr = JSONArray()
          for (s in samples) {
            val obj = JSONObject()
            obj.put("t_ms", s.tMs)
            if (s.compassDeg != null) {
              obj.put("compass_deg", round2(s.compassDeg).toDouble())
            } else {
              obj.put("compass_deg", JSONObject.NULL)
            }
            obj.put("yaw_deg", round2(s.yawDeg).toDouble())
            obj.put("pitch_deg", round2(s.pitchDeg).toDouble())
            obj.put("roll_deg", round2(s.rollDeg).toDouble())
            obj.put("absolute", s.absolute)
            obj.put(
                "gyro_dps",
                JSONArray().put(s.gyroDps[0]).put(s.gyroDps[1]).put(s.gyroDps[2]),
            )
            obj.put(
                "accel_g",
                JSONArray().put(s.accelG[0]).put(s.accelG[1]).put(s.accelG[2]),
            )
            obj.put(
                "accel_lin",
                JSONArray().put(s.accelLin[0]).put(s.accelLin[1]).put(s.accelLin[2]),
            )
            obj.put("step_count", s.stepCount)
            samplesArr.put(obj)
          }

          val bodyObj =
              JSONObject().apply {
                put("session_id", sessionId)
                put("device", deviceName)
                put("started_at_ms", startedAtMs)
                put("ended_at_ms", endedAtMs)
                put("step_count", stepCount)
                put("samples", samplesArr)
              }
          val payloadBytes = bodyObj.toString().toByteArray(Charsets.UTF_8)

          val conn =
              (URL("$baseUrl/api/imu-sessions").openConnection() as HttpURLConnection).apply {
                requestMethod = "POST"
                connectTimeout = 15000
                readTimeout = 25000
                doOutput = true
                setRequestProperty("Content-Type", "application/json; charset=utf-8")
                setFixedLengthStreamingMode(payloadBytes.size)
              }
          conn.outputStream.use { it.write(payloadBytes) }
          imuOk = conn.responseCode in 200..299
          conn.disconnect()
        } catch (e: Exception) {
          Log.e(TAG, "Failed to upload IMU session", e)
        }

        if (videoFile == null || !videoFile.exists() || videoFile.length() == 0L) {
          return@withContext if (imuOk) {
            "Synced ${samples.size} IMU pts ($stepCount steps)"
          } else {
            "IMU upload failed — check Hub URL"
          }
        }

        try {
          val encName = URLEncoder.encode(videoFile.name, "UTF-8")
          val encSid = URLEncoder.encode(sessionId, "UTF-8")
          val uploadUrl =
              "$baseUrl/api/dat-scans?name=$encName&imu_session_id=$encSid&started_at_ms=$startedAtMs"
          val conn =
              (URL(uploadUrl).openConnection() as HttpURLConnection).apply {
                requestMethod = "POST"
                connectTimeout = 20000
                readTimeout = 90000
                doOutput = true
                setRequestProperty("Content-Type", "application/octet-stream")
                setFixedLengthStreamingMode(videoFile.length())
              }
          videoFile.inputStream().use { input ->
            conn.outputStream.use { output -> input.copyTo(output, bufferSize = 64 * 1024) }
          }
          val videoOk = conn.responseCode in 200..299
          conn.disconnect()
          val mb = ((videoFile.length() / (1024.0 * 1024.0)) * 10.0).roundToInt() / 10.0
          return@withContext if (videoOk && imuOk) {
            "Synced ${mb}MB MP4 + ${samples.size} IMU pts ($stepCount steps) -> 3D building!"
          } else if (videoOk) {
            "Synced ${mb}MB MP4 to laptop!"
          } else {
            "Synced ${samples.size} IMU pts (MP4 HTTP ${conn.responseCode})"
          }
        } catch (e: Exception) {
          Log.e(TAG, "Failed to upload MP4 to /api/dat-scans", e)
          return@withContext if (imuOk) {
            "Synced ${samples.size} IMU pts • Share MP4 from preview"
          } else {
            "Hub unreachable — use Share button to save MP4"
          }
        }
      }
}
