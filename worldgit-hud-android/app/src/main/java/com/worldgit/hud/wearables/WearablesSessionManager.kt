package com.worldgit.hud.wearables

import android.annotation.SuppressLint
import android.app.Activity
import android.content.Context
import android.graphics.Bitmap
import android.hardware.SensorManager
import android.os.SystemClock
import android.util.Log
import com.meta.wearable.dat.camera.Camera as MetaCamera
import com.meta.wearable.dat.camera.addCamera
import com.meta.wearable.dat.camera.removeCamera
import com.meta.wearable.dat.camera.types.AudioCodec
import com.meta.wearable.dat.camera.types.AudioSampleRate
import com.meta.wearable.dat.camera.types.StreamConfiguration
import com.meta.wearable.dat.camera.types.VideoQuality
import com.meta.wearable.dat.core.Wearables
import com.meta.wearable.dat.core.selectors.AutoDeviceSelector
import com.meta.wearable.dat.core.selectors.SpecificDeviceSelector
import com.meta.wearable.dat.core.session.DeviceSession
import com.meta.wearable.dat.core.session.DeviceSessionState
import com.meta.wearable.dat.core.types.*
import com.meta.wearable.dat.display.Display
import com.meta.wearable.dat.display.addDisplay
import com.meta.wearable.dat.display.removeDisplay
import com.meta.wearable.dat.display.types.DisplayState
import com.meta.wearable.dat.display.views.ContentScope
import com.meta.wearable.dat.inputs.addInputs
import com.meta.wearable.dat.inputs.removeInputs
import com.meta.wearable.dat.inputs.types.InputEvent
import com.meta.wearable.dat.inputs.types.InputsConfiguration
import com.meta.wearable.dat.motion.Motion
import com.meta.wearable.dat.motion.addMotion
import com.meta.wearable.dat.motion.removeMotion
import com.meta.wearable.dat.motion.types.MotionConfiguration
import com.meta.wearable.dat.motion.types.MotionSamplingRate
import com.meta.wearable.dat.motion.types.MotionSource
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlin.math.sqrt

data class GlassesHudPacket(
  val signature: String,
  val minIntervalMs: Long,
  val render: ContentScope.() -> Unit,
)

data class WearablesLinkSnapshot(
  val initialized: Boolean = false,
  val registered: Boolean = false,
  val connectedDeviceName: String? = null,
  val displayStarted: Boolean = false,
  val cameraStreaming: Boolean = false,
  val inputsAttached: Boolean = false,
  val firmwareUpdateRequired: Boolean = false,
  val datAppUpdateRequired: Boolean = false,
  val statusMessage: String = "Tap Connect Glasses to start",
)

@SuppressLint("AutoCloseableUse")
class WearablesSessionManager(
  private val scope: CoroutineScope,
  private val onVideoFrame: (ByteArray, Int, Int, Long) -> Unit,
  private val onAudioPcm: (ByteArray) -> Unit,
  private val onMotionSample: (FloatArray?, FloatArray?, Long) -> Unit,
  private val onHudSelect: () -> Unit,
  private val onHudNav: (String) -> Unit,
  private val onHudBack: () -> Unit,
  private val buildHudScreen: () -> GlassesHudPacket,
) {
  companion object {
    private const val TAG = "WorldGitWearables"
  }

  private val _snapshot = MutableStateFlow(WearablesLinkSnapshot())
  val snapshot = _snapshot.asStateFlow()

  private var observingStarted = false
  private var sessionLock = Any()
  private var session: DeviceSession? = null
  private var display: Display? = null
  private var camera: MetaCamera? = null
  private var motion: Motion? = null
  private var inputsAttached = false

  private var sessionJobs = mutableListOf<Job>()
  private var displayJobs = mutableListOf<Job>()
  private var cameraJobs = mutableListOf<Job>()
  private var motionJobs = mutableListOf<Job>()
  private var inputsJob: Job? = null
  private var hudRenderLoopJob: Job? = null
  private var hudSendJob: Job? = null
  @Volatile private var forceHudRefresh = true
  private var lastSentSignature = ""
  private var lastSentAtMs = 0L

  private val metadataJobs = mutableMapOf<DeviceIdentifier, Job>()
  private val devicesMeta = mutableMapOf<DeviceIdentifier, Device>()
  private var pendingConnectWithAudio = false

  fun initialize(context: Context) {
    if (observingStarted) return
    Wearables.initialize(context)
      .onSuccess {
        observingStarted = true
        _snapshot.value = _snapshot.value.copy(initialized = true)
        observeSdkState()
      }
      .onFailure { err, _ ->
        Log.e(TAG, "Wearables.initialize failed: ${err.description}")
        _snapshot.value = _snapshot.value.copy(statusMessage = "Meta SDK init failed: ${err.description}")
      }
  }

  private fun observeSdkState() {
    scope.launch {
      Wearables.registrationState.collect { reg ->
        val isReg = reg == RegistrationState.REGISTERED
        _snapshot.value = _snapshot.value.copy(
          registered = isReg,
          statusMessage = if (isReg) _snapshot.value.statusMessage else "Tap Connect Glasses to register with Meta AI",
        )
      }
    }
    scope.launch {
      Wearables.registrationErrorStream.collect { err ->
        Log.w(TAG, "Registration error: ${err.description}")
        _snapshot.value = _snapshot.value.copy(statusMessage = "Registration: ${err.description}")
      }
    }
    scope.launch {
      Wearables.devices.collect { ids ->
        (metadataJobs.keys - ids).forEach { removed ->
          metadataJobs.remove(removed)?.cancel()
          devicesMeta.remove(removed)
        }
        ids.forEach { id ->
          metadataJobs.getOrPut(id) {
            scope.launch {
              Wearables.devicesMetadata[id]?.collect { dev ->
                devicesMeta[id] = dev
                val needsFw = devicesMeta.values.any { it.compatibility == DeviceCompatibility.DEVICE_UPDATE_REQUIRED }
                val readyDev = devicesMeta.values.firstOrNull {
                  it.linkState == LinkState.CONNECTED && it.isDisplayCapable()
                }
                _snapshot.value = _snapshot.value.copy(
                  connectedDeviceName = readyDev?.name ?: _snapshot.value.connectedDeviceName,
                  firmwareUpdateRequired = needsFw,
                )
              }
            }
          }
        }
      }
    }
  }

  fun startRegistration(activity: Activity) {
    Wearables.startRegistration(activity)
  }

  fun openFirmwareUpdate(activity: Activity) {
    Wearables.openFirmwareUpdate(activity)
  }

  fun openDatAppUpdate(activity: Activity) {
    Wearables.openDATGlassesAppUpdate(activity)
  }

  fun connectGlasses(withMicrophone: Boolean) {
    stopSession()
    pendingConnectWithAudio = withMicrophone
    val picked = devicesMeta.entries.firstOrNull { (_, dev) ->
      dev.linkState == LinkState.CONNECTED && dev.isDisplayCapable()
    }?.key
    val selector = if (picked != null) {
      SpecificDeviceSelector(picked)
    } else {
      AutoDeviceSelector(filter = { dev -> dev.isDisplayCapable() })
    }
    _snapshot.value = _snapshot.value.copy(statusMessage = "Connecting to Meta Ray-Ban Display...")
    Wearables.createSession(selector).fold(
      onSuccess = { newSession ->
        synchronized(sessionLock) { session = newSession }
        sessionJobs += scope.launch {
          newSession.errors.collect { err ->
            Log.e(TAG, "Session error: ${err.description}")
            _snapshot.value = _snapshot.value.copy(
              datAppUpdateRequired = err == DeviceSessionError.DAT_APP_ON_THE_GLASSES_UPDATE_REQUIRED,
              statusMessage = err.description,
            )
          }
        }
        sessionJobs += scope.launch {
          var sessionHasStarted = false
          newSession.state.collect { state ->
            when (state) {
              DeviceSessionState.STARTED -> {
                sessionHasStarted = true
                _snapshot.value = _snapshot.value.copy(statusMessage = "Glasses session active • Starting HUD...")
                attachDisplay(newSession)
                attachMotion(newSession)
                attachInputs(newSession)
                attachCamera(newSession, pendingConnectWithAudio)
              }
              DeviceSessionState.STOPPED -> {
                if (sessionHasStarted) {
                  detachAllCapabilities()
                  _snapshot.value = _snapshot.value.copy(
                    displayStarted = false,
                    cameraStreaming = false,
                    inputsAttached = false,
                    statusMessage = "Glasses disconnected",
                  )
                }
              }
              else -> Unit
            }
          }
        }
        newSession.start()
      },
      onFailure = { err, _ ->
        Log.e(TAG, "createSession failed: ${err.description}")
        _snapshot.value = _snapshot.value.copy(
          datAppUpdateRequired = err == DeviceSessionError.DAT_APP_ON_THE_GLASSES_UPDATE_REQUIRED,
          statusMessage = "Connect failed: ${err.description}",
        )
      },
    )
  }

  private fun attachDisplay(current: DeviceSession) {
    current.addDisplay().fold(
      onSuccess = { newDisplay ->
        display = newDisplay
        displayJobs += scope.launch {
          newDisplay.state.collect { ds ->
            val started = ds == DisplayState.STARTED
            _snapshot.value = _snapshot.value.copy(
              displayStarted = started,
              statusMessage = if (started) "HUD live on Meta Ray-Ban Display" else "Preparing glasses HUD...",
            )
            if (started) startHudRenderLoop() else stopHudRenderLoop()
          }
        }
      },
      onFailure = { err, _ ->
        Log.e(TAG, "addDisplay failed: ${err.description}")
        _snapshot.value = _snapshot.value.copy(statusMessage = "Display error: ${err.description}")
      },
    )
  }

  private fun attachCamera(current: DeviceSession, withAudio: Boolean) {
    val cfg = StreamConfiguration(
      videoQuality = VideoQuality.LOW,
      frameRate = 24,
      compressVideo = false,
      audioCodec = if (withAudio) AudioCodec.PCM(sampleRate = AudioSampleRate.RATE_16000, numberOfChannels = 1) else null,
    )
    current.addCamera(cfg).fold(
      onSuccess = { attached ->
        camera = attached
        cameraJobs += scope.launch(Dispatchers.Default) {
          attached.stream.videoStream.collect { frame ->
            if (!frame.isCompressed && !frame.isCodecConfig) {
              val dup = frame.buffer.duplicate()
              val bytes = ByteArray(dup.remaining())
              dup.get(bytes)
              onVideoFrame(bytes, frame.width, frame.height, SystemClock.elapsedRealtimeNanos())
            }
          }
        }
        cameraJobs += scope.launch(Dispatchers.Default) {
          attached.stream.audioStream.collect { frame ->
            val dup = frame.buffer.duplicate()
            val bytes = ByteArray(dup.remaining())
            dup.get(bytes)
            onAudioPcm(bytes)
          }
        }
        cameraJobs += scope.launch {
          attached.stream.state.collect { st ->
            Log.i(TAG, "Glasses camera state: $st")
            _snapshot.value = _snapshot.value.copy(cameraStreaming = st.name.contains("START", ignoreCase = true) || st.name.contains("STREAM", ignoreCase = true))
          }
        }
        cameraJobs += scope.launch {
          attached.stream.errorStream.collect { err ->
            Log.e(TAG, "Glasses camera error: ${err.description}")
          }
        }
        attached.stream.start().onSuccess {
          _snapshot.value = _snapshot.value.copy(cameraStreaming = true)
        }.onFailure { err, _ ->
          Log.e(TAG, "Camera stream start failed: ${err.description}")
        }
      },
      onFailure = { err, _ ->
        Log.w(TAG, "Camera unavailable: ${err.description}")
      },
    )
  }

  private fun attachMotion(current: DeviceSession) {
    current.addMotion(MotionConfiguration(samplingRate = MotionSamplingRate.HZ_60)).fold(
      onSuccess = { attached ->
        motion = attached
        motionJobs += scope.launch {
          attached.samples.collect { sample ->
            if (sample.source != MotionSource.GLASSES) return@collect
            val rot = sample.orientation?.let { q ->
              val norm = sqrt(q.x * q.x + q.y * q.y + q.z * q.z + q.w * q.w)
              if (norm < 0.001f || !norm.isFinite()) null else FloatArray(9).also {
                SensorManager.getRotationMatrixFromVector(it, floatArrayOf(q.x / norm, q.y / norm, q.z / norm, q.w / norm))
                for (row in 0..2) {
                  it[row * 3 + 1] *= -1f
                  it[row * 3 + 2] *= -1f
                }
              }
            }
            val gyro = sample.gyroscope?.let { floatArrayOf(it.x, -it.y, -it.z) }
            onMotionSample(rot, gyro, SystemClock.elapsedRealtimeNanos())
          }
        }
        attached.start()
      },
      onFailure = { err, _ -> Log.w(TAG, "Glasses motion unavailable: ${err.description}") },
    )
  }

  private fun attachInputs(@Suppress("UNUSED_PARAMETER") current: DeviceSession) {
    // Native Bloks buttonGroup / button onClick handles temple & Neural Band focus and selection.
    // Subscribing to InputEvent.Nav/Select here would hijack swipe navigation to switch HudMode.
    inputsAttached = false
    _snapshot.value = _snapshot.value.copy(inputsAttached = false)
  }

  fun requestImmediateHudRefresh() {
    forceHudRefresh = true
    lastSentSignature = ""
    if (_snapshot.value.displayStarted) {
      sendHudScreenIfNeeded(force = true)
    }
  }

  private fun startHudRenderLoop() {
    if (hudRenderLoopJob?.isActive == true) return
    forceHudRefresh = true
    lastSentSignature = ""
    hudRenderLoopJob = scope.launch {
      while (isActive && _snapshot.value.displayStarted) {
        sendHudScreenIfNeeded(force = forceHudRefresh)
        delay(45L)
      }
    }
  }

  private fun stopHudRenderLoop() {
    hudRenderLoopJob?.cancel()
    hudRenderLoopJob = null
    hudSendJob?.cancel()
    hudSendJob = null
    lastSentSignature = ""
  }

  private fun sendHudScreenIfNeeded(force: Boolean) {
    val activeDisplay = display ?: return
    if (hudSendJob?.isActive == true) return
    val packet = buildHudScreen()
    val nowMs = SystemClock.elapsedRealtime()
    if (!force && packet.signature == lastSentSignature) return
    if (!force && nowMs - lastSentAtMs < packet.minIntervalMs) return
    forceHudRefresh = false
    lastSentSignature = packet.signature
    lastSentAtMs = nowMs
    hudSendJob = scope.launch {
      activeDisplay.sendContent(packet.render).onFailure { err, _ ->
        Log.w(TAG, "sendContent failed: ${err.description}")
      }
    }
  }

  private fun detachAllCapabilities() {
    stopHudRenderLoop()
    inputsJob?.cancel()
    inputsJob = null
    if (inputsAttached) {
      synchronized(sessionLock) { session }?.removeInputs()
      inputsAttached = false
    }
    camera?.stop()
    cameraJobs.forEach { it.cancel() }
    cameraJobs.clear()
    synchronized(sessionLock) { session }?.removeCamera()
    camera = null

    motion?.stop()
    motionJobs.forEach { it.cancel() }
    motionJobs.clear()
    synchronized(sessionLock) { session }?.removeMotion()
    motion = null

    displayJobs.forEach { it.cancel() }
    displayJobs.clear()
    synchronized(sessionLock) { session }?.removeDisplay()
    display = null
  }

  fun stopSession() {
    detachAllCapabilities()
    sessionJobs.forEach { it.cancel() }
    sessionJobs.clear()
    synchronized(sessionLock) {
      session?.stop()
      session = null
    }
    _snapshot.value = _snapshot.value.copy(
      displayStarted = false,
      cameraStreaming = false,
      inputsAttached = false,
      statusMessage = "Glasses session stopped",
    )
  }
}
