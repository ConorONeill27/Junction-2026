/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 *
 * This source code is licensed under the license found in the
 * LICENSE file in the root directory of this source tree.
 */

package com.meta.wearable.dat.externalsampleapps.displayaccess.display

import android.annotation.SuppressLint
import android.app.Application
import android.graphics.BitmapFactory
import android.util.Log
import androidx.annotation.DrawableRes
import androidx.annotation.GuardedBy
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.meta.wearable.dat.core.Wearables
import com.meta.wearable.dat.core.selectors.AutoDeviceSelector
import com.meta.wearable.dat.core.selectors.DeviceSelector
import com.meta.wearable.dat.core.selectors.SpecificDeviceSelector
import com.meta.wearable.dat.core.session.DeviceSession
import com.meta.wearable.dat.core.session.DeviceSessionState
import com.meta.wearable.dat.core.types.DeviceIdentifier
import com.meta.wearable.dat.core.types.DeviceSessionError
import com.meta.wearable.dat.display.Display
import com.meta.wearable.dat.display.addDisplay
import com.meta.wearable.dat.display.removeDisplay
import com.meta.wearable.dat.display.types.DisplayState
import com.meta.wearable.dat.display.types.VideoCodec
import com.meta.wearable.dat.display.types.VideoPlayerState
import com.meta.wearable.dat.display.types.VideoSource
import com.meta.wearable.dat.display.views.ActionRole
import com.meta.wearable.dat.display.views.Alignment
import com.meta.wearable.dat.display.views.ButtonStyle
import com.meta.wearable.dat.display.views.ContentScope
import com.meta.wearable.dat.display.views.CornerRadius
import com.meta.wearable.dat.display.views.Direction
import com.meta.wearable.dat.display.views.FlexBoxBackground
import com.meta.wearable.dat.display.views.FlexBoxScope
import com.meta.wearable.dat.display.views.IconName
import com.meta.wearable.dat.display.views.ImageSize
import com.meta.wearable.dat.display.views.TextColor
import com.meta.wearable.dat.display.views.TextStyle
import com.meta.wearable.dat.display.views.VideoPlayer
import com.meta.wearable.dat.externalsampleapps.displayaccess.R
import com.meta.wearable.dat.externalsampleapps.displayaccess.SampleApp
import com.meta.wearable.dat.externalsampleapps.displayaccess.YoloState
import com.meta.wearable.dat.externalsampleapps.displayaccess.AudioClipState
import com.meta.wearable.dat.externalsampleapps.displayaccess.HeadCursorState
import com.meta.wearable.dat.externalsampleapps.displayaccess.LiveSpeechState
import com.meta.wearable.dat.externalsampleapps.displayaccess.drawLiveSpeechOverlay
import com.meta.wearable.dat.camera.Camera as MetaCamera
import com.meta.wearable.dat.camera.addCamera
import com.meta.wearable.dat.camera.removeCamera
import com.meta.wearable.dat.camera.types.StreamConfiguration
import com.meta.wearable.dat.camera.types.VideoQuality
import com.meta.wearable.dat.camera.types.AudioCodec
import com.meta.wearable.dat.camera.types.AudioSampleRate
import com.meta.wearable.dat.inputs.addInputs
import com.meta.wearable.dat.inputs.removeInputs
import com.meta.wearable.dat.inputs.types.InputsConfiguration
import com.meta.wearable.dat.inputs.types.InputEvent
import com.meta.wearable.dat.motion.Motion
import com.meta.wearable.dat.motion.addMotion
import com.meta.wearable.dat.motion.removeMotion
import com.meta.wearable.dat.motion.types.MotionConfiguration
import com.meta.wearable.dat.motion.types.MotionSamplingRate
import com.meta.wearable.dat.motion.types.MotionSource
import com.meta.wearable.dat.motion.types.MotionState
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineExceptionHandler
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

@SuppressLint(
    "AutoCloseableUse",
    "NavigatorCoroutineLaunchWithoutExceptionHandler",
)
class DisplayViewModel(
    application: Application,
) : AndroidViewModel(application) {

  private companion object {
    private const val TAG = "DisplayAccessVM"
    private const val TUTORIAL_VIDEO_URL =
        "https://github.com/facebook/meta-wearables-dat-android/raw/refs/heads/assets/video_266x150_faststart.mp4"

    private val carMaintenanceTutorials = listOf(
        CarMaintenanceTutorial(
            title = "Oil change",
            duration = "Easy • 45 min",
            imageRes = R.drawable.oil,
            iconRes = R.drawable.oil_square,
            steps =
                listOf(
                    CarMaintenanceTutorialStep(
                        description =
                            "Park on level ground and let the engine cool before opening the hood.",
                    ),
                    CarMaintenanceTutorialStep(
                        description =
                            "Drain the old oil, replace the filter, and tighten the drain plug.",
                    ),
                    CarMaintenanceTutorialStep(
                        description =
                            "Refill with fresh oil, run the engine briefly, and recheck the level.",
                    ),
                ),
        ),
        CarMaintenanceTutorial(
            title = "Fix a flat tire",
            duration = "Easy • 15 min",
            imageUri = "https://www.facebook.com/assets/wearables_dat_display/tire.png",
            iconImageUri = "https://www.facebook.com/assets/wearables_dat_display/tire_square.png",
            steps =
                listOf(
                    CarMaintenanceTutorialStep(
                        description =
                            "Park away from traffic, engage the brake, and place the wheel wedges.",
                    ),
                    CarMaintenanceTutorialStep(
                        description =
                            "Loosen the lug nuts slightly, raise the car, and remove the flat tire.",
                    ),
                    CarMaintenanceTutorialStep(
                        description =
                            "Mount the spare, tighten in a star pattern, and lower the vehicle.",
                    ),
                ),
        ),
        CarMaintenanceTutorial(
            title = "Replace headlight bulb",
            duration = "Very easy • 5 min",
            imageUri = "https://www.facebook.com/assets/wearables_dat_display/light.png",
            iconImageUri = "https://www.facebook.com/assets/wearables_dat_display/light_square.png",
            steps =
                listOf(
                    CarMaintenanceTutorialStep(
                        description =
                            "Open the rear access cover and disconnect the bulb connector.",
                    ),
                    CarMaintenanceTutorialStep(
                        description =
                            "Release the retaining clip, remove the old bulb, and insert the new one.",
                    ),
                    CarMaintenanceTutorialStep(
                        description =
                            "Reconnect power, close the cover, and verify the beam works properly.",
                    ),
                ),
        ),
        CarMaintenanceTutorial(
            title = "Check engine light",
            duration = "Hard • 2 hours",
            imageUri = "https://www.facebook.com/assets/wearables_dat_display/engine.png",
            iconImageUri =
                "https://www.facebook.com/assets/wearables_dat_display/engine_square.png",
            steps =
                listOf(
                    CarMaintenanceTutorialStep(
                        description =
                            "Check whether the light is steady or flashing, and stop driving if it is flashing.",
                    ),
                    CarMaintenanceTutorialStep(
                        description =
                            "Tighten the gas cap fully and look for obvious issues like low fluids or overheating.",
                    ),
                    CarMaintenanceTutorialStep(
                        description =
                            "Scan for diagnostic codes or schedule service if the light stays on after restarting.",
                    ),
                ),
        ),
        CarMaintenanceTutorial(
            title = "Change washer fluid",
            duration = "Very easy • 3 min",
            imageUri = "https://www.facebook.com/assets/wearables_dat_display/washer.png",
            iconImageUri =
                "https://www.facebook.com/assets/wearables_dat_display/washer_square.png",
            steps =
                listOf(
                    CarMaintenanceTutorialStep(
                        description =
                            "Open the hood and locate the washer fluid reservoir cap with the windshield symbol.",
                    ),
                    CarMaintenanceTutorialStep(
                        description =
                            "Pour washer fluid into the reservoir carefully until it reaches the fill line.",
                    ),
                    CarMaintenanceTutorialStep(
                        description =
                            "Close the cap securely and test the sprayers to confirm proper flow.",
                    ),
                ),
        ),
    )
  }

  private val dispatcher: CoroutineDispatcher = Dispatchers.IO
  private val sessionLock = Any()

  private val _uiState = MutableStateFlow(DisplayUIState())
  val uiState: StateFlow<DisplayUIState> = _uiState.asStateFlow()

  private val sessionObserverExceptionHandler = CoroutineExceptionHandler { _, throwable ->
    Log.e(TAG, "Session state observer failed", throwable)
  }
  private val displayObserverExceptionHandler = CoroutineExceptionHandler { _, throwable ->
    Log.e(TAG, "Display state observer failed", throwable)
  }
  private val sendContentExceptionHandler = CoroutineExceptionHandler { _, throwable ->
    Log.e(TAG, "Sending display content failed", throwable)
    _uiState.value =
        _uiState.value.copy(
            isSending = false,
            hasSentContent = false,
            errorMessage = throwable.message ?: "Unexpected error",
            snackbarMessage = "Send failed: ${throwable.message ?: "unexpected error"}",
        )
  }

  @GuardedBy("sessionLock") private var session: DeviceSession? = null
  @GuardedBy("sessionLock") private var display: Display? = null
  @GuardedBy("sessionLock") private var sessionStateJob: Job? = null
  @GuardedBy("sessionLock") private var sessionErrorJob: Job? = null
  @GuardedBy("sessionLock") private var displayStateJob: Job? = null
  @GuardedBy("sessionLock") private var pendingDisplayAttachment = false
  @GuardedBy("sessionLock") private var pendingSample: SampleApp? = null
  private var tutorialVideoStateJob: Job? = null
  private var yoloState = YoloState()
  private var yoloPage = 0
  private var audioState = AudioClipState()
  var onAudioPinch: (() -> Unit)? = null
  var onCursorPinch: (() -> Unit)? = null
  var onGlassesVideo: ((ByteArray, Int, Int, Long) -> Unit)? = null
  var onGlassesAudio: ((ByteArray) -> Unit)? = null
  var onGlassesCameraStopped: (() -> Unit)? = null
  var physicalCameraRequested = false
    private set
  private var glassesAudioRequested = false
  val glassesMicrophoneEnabled: Boolean get() = physicalCameraRequested && glassesAudioRequested
  private var glassesCamera: MetaCamera? = null
  private var glassesCameraSession: DeviceSession? = null
  private var glassesCameraJobs = emptyList<Job>()

  fun startOnPhysicalGlasses(withAudio: Boolean) {
    physicalCameraRequested = true
    glassesAudioRequested = withAudio
    sendSampleToDisplay(SampleApp.CAR_MAINTENANCE)
    synchronized(sessionLock) { session }?.let {
      if (it.state.value == DeviceSessionState.STARTED) attachGlassesCamera(it)
    }
  }

  private fun attachGlassesCamera(currentSession: DeviceSession) {
    if (!physicalCameraRequested || glassesCamera != null) return
    currentSession.addCamera(StreamConfiguration(
      videoQuality = VideoQuality.LOW, frameRate = 24, compressVideo = false,
      audioCodec = if (glassesAudioRequested) AudioCodec.PCM(sampleRate = AudioSampleRate.RATE_16000, numberOfChannels = 1) else null,
    )).fold(
      onSuccess = { attached ->
        glassesCamera = attached
        glassesCameraSession = currentSession
        glassesCameraJobs = listOf(
          viewModelScope.launch(Dispatchers.Default + sessionObserverExceptionHandler, start = CoroutineStart.UNDISPATCHED) {
            attached.stream.videoStream.collect { frame ->
              if (!frame.isCompressed && !frame.isCodecConfig) {
                val copy = frame.buffer.duplicate()
                val bytes = ByteArray(copy.remaining())
                copy.get(bytes)
                onGlassesVideo?.invoke(bytes, frame.width, frame.height, android.os.SystemClock.elapsedRealtimeNanos())
              }
            }
          },
          viewModelScope.launch(Dispatchers.Default + sessionObserverExceptionHandler, start = CoroutineStart.UNDISPATCHED) {
            attached.stream.audioStream.collect { frame ->
              val copy = frame.buffer.duplicate()
              val bytes = ByteArray(copy.remaining())
              copy.get(bytes)
              onGlassesAudio?.invoke(bytes)
            }
          },
          viewModelScope.launch(sessionObserverExceptionHandler, start = CoroutineStart.UNDISPATCHED) {
            attached.stream.state.collect { Log.i(TAG, "Glasses camera state: $it") }
          },
          viewModelScope.launch(sessionObserverExceptionHandler, start = CoroutineStart.UNDISPATCHED) {
            attached.stream.errorStream.collect { error ->
              Log.e(TAG, "Glasses camera error: ${error.description}")
              _uiState.value = _uiState.value.copy(errorMessage = error.description, snackbarMessage = error.description)
            }
          },
        )
        attached.stream.start().onFailure { error, _ ->
          Log.e(TAG, "Glasses stream start failed: ${error.description}")
          _uiState.value = _uiState.value.copy(errorMessage = error.description, snackbarMessage = error.description)
        }
      },
      onFailure = { error, _ ->
        Log.e(TAG, "Glasses camera attach failed: ${error.description}")
        _uiState.value = _uiState.value.copy(errorMessage = error.description, snackbarMessage = error.description)
      },
    )
  }

  private fun detachGlassesCamera() {
    glassesCamera?.stop()
    glassesCameraJobs.forEach { it.cancel() }
    glassesCameraJobs = emptyList()
    glassesCameraSession?.removeCamera()
    glassesCamera = null
    glassesCameraSession = null
    physicalCameraRequested = false
    onGlassesCameraStopped?.invoke()
  }

  var onGlassesMotion: ((FloatArray?, FloatArray?) -> Unit)? = null
  var onGlassesMotionStopped: (() -> Unit)? = null
  private var motion: Motion? = null
  private var motionSession: DeviceSession? = null
  private var motionJobs = emptyList<Job>()

  private fun attachMotion(currentSession: DeviceSession) {
    if (motion != null) return
    currentSession.addMotion(MotionConfiguration(samplingRate = MotionSamplingRate.HZ_60)).fold(
      onSuccess = { attached ->
        motion = attached
        motionSession = currentSession
        motionJobs = listOf(
          viewModelScope.launch(sessionObserverExceptionHandler, start = CoroutineStart.UNDISPATCHED) {
            attached.samples.collect { sample ->
              if (sample.source != MotionSource.GLASSES) return@collect
              val rotation = sample.orientation?.let { q ->
                val norm = kotlin.math.sqrt(q.x*q.x + q.y*q.y + q.z*q.z + q.w*q.w)
                if (norm < .001f || !norm.isFinite()) null else FloatArray(9).also {
                  android.hardware.SensorManager.getRotationMatrixFromVector(it, floatArrayOf(q.x/norm, q.y/norm, q.z/norm, q.w/norm))
                  // Convert device right/up/out axes to image right/down/forward.
                  for (row in 0..2) { it[row*3+1] *= -1f; it[row*3+2] *= -1f }
                }
              }
              val gyro = sample.gyroscope?.let { floatArrayOf(it.x, -it.y, -it.z) }
              val receivedAt = android.os.SystemClock.elapsedRealtimeNanos()
              rotation?.let { hudMotion.orientation(it,receivedAt) }
              gyro?.let { hudMotion.angularVelocity(it[0],it[1],it[2],receivedAt) }
              onGlassesMotion?.invoke(rotation, gyro)
            }
          },
          viewModelScope.launch(sessionObserverExceptionHandler, start = CoroutineStart.UNDISPATCHED) {
            attached.state.collect { state ->
              Log.i(TAG, "Glasses motion state: $state")
              if (state != MotionState.STARTED) onGlassesMotionStopped?.invoke()
            }
          },
          viewModelScope.launch(sessionObserverExceptionHandler, start = CoroutineStart.UNDISPATCHED) {
            attached.errors.collect { error ->
              if (error != null) {
                Log.w(TAG, "Glasses motion unavailable: $error")
                onGlassesMotionStopped?.invoke()
              }
            }
          },
        )
        attached.start()
      },
      onFailure = { error, _ -> Log.w(TAG, "Motion capability unavailable: ${error.description}") },
    )
  }

  private fun detachMotion() {
    motion?.stop()
    motionJobs.forEach { it.cancel() }
    motionJobs = emptyList()
    motionSession?.removeMotion()
    motionSession = null
    motion = null
    onGlassesMotionStopped?.invoke()
  }

  private var cursorState = HeadCursorState()
  private var speechState = LiveSpeechState()

  fun updateSpeech(state: LiveSpeechState) {
    speechState = state
    if (_uiState.value.displayState == DisplayState.STARTED) displayYoloScreen()
  }

  fun updateCursor(state: HeadCursorState) {
    cursorState = state
    if (_uiState.value.displayState == DisplayState.STARTED) displayYoloScreen()
  }

  fun updateAudio(state: AudioClipState) {
    audioState = state
    if (_uiState.value.displayState == DisplayState.STARTED) displayYoloScreen()
  }

  fun updateYolo(state: YoloState) {
    yoloState = state
    val now = android.os.SystemClock.elapsedRealtimeNanos()
    hudPredictor.observe(state, if (physicalCameraRequested) hudMotion.predict(now) else null, now / 1e9, hudCalibration)
    if (_uiState.value.displayState == DisplayState.STARTED) displayYoloScreen()
  }

  private var inputsSession: DeviceSession? = null
  private var inputsJob: Job? = null
  private var inputsAttached = false

  private fun attachGlassesInputs(currentSession: DeviceSession) {
    if (!physicalCameraRequested || inputsSession != null) return
    currentSession.addInputs(InputsConfiguration()).fold(
      onSuccess = { inputs ->
        inputsSession = currentSession
        inputsAttached = true
        inputsJob = viewModelScope.launch(sessionObserverExceptionHandler, start = CoroutineStart.UNDISPATCHED) {
          inputs.events.collect { event -> if (event is InputEvent.Select) onCursorPinch?.invoke() }
        }
        if (_uiState.value.displayState == DisplayState.STARTED) displayYoloScreen()
      },
      onFailure = { error, _ -> Log.w(TAG, "Raw input unavailable; using full-display click: ${error.description}") },
    )
  }

  private fun detachGlassesInputs() {
    inputsJob?.cancel(); inputsJob = null
    inputsSession?.removeInputs()
    inputsSession = null; inputsAttached = false
  }

  private val hudPredictor = com.meta.wearable.dat.externalsampleapps.displayaccess.HudFramePredictor()
  private val hudMotion = com.meta.wearable.dat.externalsampleapps.displayaccess.MotionPredictor()
  private var hudLoop: Job? = null
  private val hudAccepted = java.util.concurrent.atomic.AtomicInteger(0)
  private var hudSkipped = 0
  private var hudStatsAt = 0L
  private var lastProjectedState = YoloState()
  fun interactionState(): YoloState = lastProjectedState

  private var hudCalibration = com.meta.wearable.dat.externalsampleapps.displayaccess.HudCalibration()
  private var hudMemoryPaused = false
  private var hudMemoryBanner: String? = null
  fun updateCalibration(value: com.meta.wearable.dat.externalsampleapps.displayaccess.HudCalibration) {
    hudCalibration = value
    if (_uiState.value.displayState == DisplayState.STARTED) displayYoloScreen()
  }
  fun updateMemoryOverlay(paused: Boolean, banner: String?) {
    hudMemoryPaused = paused
    hudMemoryBanner = banner
    if (_uiState.value.displayState == DisplayState.STARTED) displayYoloScreen()
  }
  private var yoloRenderJob: Job? = null
  private var lastYoloRenderAt = 0L

  private fun displayYoloScreen() {
    if (hudLoop?.isActive == true || _uiState.value.displayState != DisplayState.STARTED) return
    hudLoop = viewModelScope.launch {
      var next = android.os.SystemClock.elapsedRealtimeNanos()
      hudStatsAt = next
      while (isActive && _uiState.value.displayState == DisplayState.STARTED) {
        renderHudFrame()
        next += 33_333_333L
        val now = android.os.SystemClock.elapsedRealtimeNanos()
        if (next < now) next += ((now-next)/33_333_333L+1)*33_333_333L
        delay(((next-now+999_999L)/1_000_000L).coerceAtLeast(1L))
      }
    }
  }

  private fun renderHudFrame() {
    val now = android.os.SystemClock.elapsedRealtimeNanos()
    // Keep one HUD send in flight; never queue old tracking frames behind a slow display link.
    if (now-hudStatsAt >= 2_000_000_000L) {
      val seconds = (now-hudStatsAt)/1e9
      Log.i("WorldGitHUD", "30 FPS target; accepted %.1f FPS; busy skips=%d".format(hudAccepted.getAndSet(0)/seconds,hudSkipped))
      hudStatsAt = now; hudSkipped = 0
    }
    if (yoloRenderJob?.isActive == true) { hudSkipped++; return }
    lastYoloRenderAt = now
    // The optical surface stays 600x600; send a smaller opaque-black raster to
    // reduce Bluetooth payload. Black emits no light on the additive display.
    val bitmap = android.graphics.Bitmap.createBitmap(200, 200, android.graphics.Bitmap.Config.ARGB_8888)
    bitmap.eraseColor(android.graphics.Color.BLACK)
    val hudCanvas = android.graphics.Canvas(bitmap).apply { scale(1f/3f,1f/3f) }
    val predicted = if (yoloState.status.startsWith("Reviewer")) yoloState else hudPredictor.sample(if (physicalCameraRequested) hudMotion.predict(now+15_000_000L) else null,now/1e9,hudCalibration)
    val projected = if (physicalCameraRequested && !yoloState.status.startsWith("Reviewer"))
      com.meta.wearable.dat.externalsampleapps.displayaccess.HudProjection(predicted.width, predicted.height, hudCalibration).state(predicted)
      else predicted
    lastProjectedState = projected
    com.meta.wearable.dat.externalsampleapps.displayaccess.drawYoloOverlay(
      hudCanvas, 600f, 600f, projected, cursorState, speechState)
    drawLiveSpeechOverlay(hudCanvas, 600f, 600f, speechState)
    if (hudMemoryPaused) {
      val p = android.graphics.Paint(android.graphics.Paint.ANTI_ALIAS_FLAG).apply {
        color = android.graphics.Color.rgb(255, 190, 60); textSize = 20f
      }
      hudCanvas.drawText("PRIVACY: MEMORY PAUSED", 20f, 36f, p)
    }
    hudMemoryBanner?.takeIf { it.isNotBlank() }?.let { banner ->
      val p = android.graphics.Paint(android.graphics.Paint.ANTI_ALIAS_FLAG).apply {
        color = android.graphics.Color.argb(220, 12, 24, 18); style = android.graphics.Paint.Style.FILL
      }
      hudCanvas.drawRoundRect(16f, 512f, 584f, 588f, 10f, 10f, p)
      p.style = android.graphics.Paint.Style.STROKE; p.strokeWidth = 2f; p.color = android.graphics.Color.rgb(72, 235, 160)
      hudCanvas.drawRoundRect(16f, 512f, 584f, 588f, 10f, 10f, p)
      p.style = android.graphics.Paint.Style.FILL; p.color = android.graphics.Color.WHITE; p.textSize = 18f
      val words = banner.split(Regex("\\s+"))
      val line1 = StringBuilder()
      val line2 = StringBuilder()
      for (w in words) {
        if (line2.isEmpty() && line1.length + w.length + 1 <= 44) {
          if (line1.isNotEmpty()) line1.append(' ')
          line1.append(w)
        } else {
          if (line2.isNotEmpty()) line2.append(' ')
          line2.append(w)
        }
      }
      if (line2.isEmpty()) {
        hudCanvas.drawText(line1.toString(), 28f, 557f, p)
      } else {
        hudCanvas.drawText(line1.toString(), 28f, 542f, p)
        hudCanvas.drawText(line2.toString().take(46), 28f, 569f, p)
      }
    }
    if (hudCalibration.showGuide) {
      val canvas = hudCanvas
      val paint = android.graphics.Paint(android.graphics.Paint.ANTI_ALIAS_FLAG).apply {
        color = android.graphics.Color.CYAN; style = android.graphics.Paint.Style.STROKE; strokeWidth = 4f
      }
      // TV-style overscan corner brackets at the exact edges of the 600x600 glasses HUD.
      val m = 4f
      val len = 64f
      // Top-left
      canvas.drawLine(m, m, m + len, m, paint)
      canvas.drawLine(m, m, m, m + len, paint)
      // Top-right
      canvas.drawLine(600f - m, m, 600f - m - len, m, paint)
      canvas.drawLine(600f - m, m, 600f - m, m + len, paint)
      // Bottom-left
      canvas.drawLine(m, 600f - m, m + len, 600f - m, paint)
      canvas.drawLine(m, 600f - m, m, 600f - m - len, paint)
      // Bottom-right
      canvas.drawLine(600f - m, 600f - m, 600f - m - len, 600f - m, paint)
      canvas.drawLine(600f - m, 600f - m, 600f - m, 600f - m - len, paint)
      // Center crosshair
      paint.color = android.graphics.Color.WHITE
      paint.strokeWidth = 2f
      canvas.drawLine(280f, 300f, 320f, 300f, paint)
      canvas.drawLine(300f, 280f, 300f, 320f, paint)
    }
    yoloRenderJob = sendContent(onAccepted = { hudAccepted.incrementAndGet() }) {
      flexBox(direction = Direction.COLUMN, padding = 0, gap = 0,
        onClick = if (inputsAttached) null else ({ onCursorPinch?.invoke() })) {
        image(bitmap = bitmap, sizePreset = ImageSize.FILL, flexGrow = 1f, flexShrink = 0f)
      }
    }
  }

  // -- Session lifecycle --

  private fun startSession(
      deviceSelector: DeviceSelector,
      selectedDeviceId: DeviceIdentifier?,
  ) {
    Log.d(TAG, "Starting DAT session")
    _uiState.value =
        _uiState.value.copy(
            selectedDeviceId = selectedDeviceId,
            connectionState = DwaConnectionState.CONNECTING,
            isStartingSession = true,
            isPreparingDisplay = true,
            hasSentContent = false,
            errorMessage = null,
            snackbarMessage = "Starting session...",
        )

    val result = Wearables.createSession(deviceSelector)
    result.fold(
        onSuccess = { newSession ->
          synchronized(sessionLock) { session = newSession }

          val stateJob =
              viewModelScope.launch(sessionObserverExceptionHandler) {
                newSession.state.collect { state ->
                  when (state) {
                    DeviceSessionState.STARTED -> {
                      Log.i(TAG, "Session started")
                      attachMotion(newSession)
                      attachGlassesCamera(newSession)
                      attachGlassesInputs(newSession)
                      _uiState.value =
                          _uiState.value.copy(
                              selectedDeviceId = selectedDeviceId,
                              connectionState = DwaConnectionState.CONNECTED,
                              isSessionActive = true,
                              isStartingSession = false,
                              isDatAppUpdateRequired = false,
                              snackbarMessage = "Session started",
                          )
                      if (consumePendingDisplayAttachment()) {
                        attachDisplay()
                      }
                    }
                    DeviceSessionState.STOPPED -> {
                      Log.i(TAG, "Session stopped")
                      detachGlassesInputs()
                      detachGlassesCamera()
                      detachMotion()
                      clearPendingWork()
                      cleanupDisplay()
                      _uiState.value =
                          _uiState.value.copy(
                              connectionState = DwaConnectionState.DISCONNECTED,
                              isSessionActive = false,
                              isStartingSession = false,
                              isStoppingSession = false,
                              isDisplayAttached = false,
                              isPreparingDisplay = false,
                              isSending = false,
                              hasSentContent = false,
                              selectedDeviceId = null,
                              displayState = null,
                              snackbarMessage = "Session stopped",
                          )
                    }
                    else -> {}
                  }
                }
              }
          replaceSessionStateJob(stateJob)

          val errorJob =
              viewModelScope.launch(sessionObserverExceptionHandler) {
                newSession.errors.collect { error -> handleSessionError(error) }
              }
          replaceSessionErrorJob(errorJob)

          newSession.start()
        },
        onFailure = { error, _ ->
          Log.e(TAG, "Failed to create session: ${error.description}")
          clearPendingWork()
          _uiState.value =
              _uiState.value.copy(
                  connectionState = DwaConnectionState.DISCONNECTED,
                  isStartingSession = false,
                  isPreparingDisplay = false,
                  isDatAppUpdateRequired =
                      error == DeviceSessionError.DAT_APP_ON_THE_GLASSES_UPDATE_REQUIRED,
                  isSending = false,
                  hasSentContent = false,
                  selectedDeviceId = null,
                  errorMessage = error.description,
                  snackbarMessage = "Failed: ${error.description}",
              )
        },
    )
  }

  private fun prepareDisplayConnection(
      deviceSelector: DeviceSelector,
      selectedDeviceId: DeviceIdentifier?,
  ) {
    when {
      _uiState.value.isDisplayAttached -> {
        synchronized(sessionLock) { pendingDisplayAttachment = false }
        _uiState.value =
            _uiState.value.copy(
                selectedDeviceId = selectedDeviceId,
                isPreparingDisplay = _uiState.value.displayState != DisplayState.STARTED,
            )
        if (_uiState.value.displayState == DisplayState.STARTED) sendPendingSample()
      }
      _uiState.value.isSessionActive -> {
        synchronized(sessionLock) { pendingDisplayAttachment = false }
        _uiState.value =
            _uiState.value.copy(
                selectedDeviceId = selectedDeviceId,
                isPreparingDisplay = true,
                snackbarMessage = "Attaching display...",
            )
        attachDisplay()
      }
      else -> {
        synchronized(sessionLock) { pendingDisplayAttachment = true }
        startSession(deviceSelector, selectedDeviceId)
      }
    }
  }

  private fun attachDisplay() {
    val currentSession =
        synchronized(sessionLock) { session }
            ?: run {
              clearPendingWork()
              _uiState.value =
                  _uiState.value.copy(
                      isPreparingDisplay = false,
                      isSending = false,
                      hasSentContent = false,
                      errorMessage = "No active session",
                      snackbarMessage = "No active session",
                  )
              return
            }

    Log.d(TAG, "Attaching display")
    _uiState.value = _uiState.value.copy(snackbarMessage = "Attaching display...")

    currentSession
        .addDisplay()
        .fold(
            onSuccess = { newDisplay ->
              synchronized(sessionLock) { display = newDisplay }
              Log.i(TAG, "Display attached")
              _uiState.value =
                  _uiState.value.copy(
                      isDisplayAttached = true,
                      isPreparingDisplay = true,
                      snackbarMessage = "Display attached",
                  )

              val stateJob =
                  viewModelScope.launch(displayObserverExceptionHandler) {
                    var hasStarted = false
                    newDisplay.state.collect { state ->
                      Log.i(TAG, "Display state: $state")
                      _uiState.value =
                          _uiState.value.copy(
                              displayState = state,
                              isPreparingDisplay =
                                  state != DisplayState.STARTED && state != DisplayState.STOPPED,
                          )
                      if (state == DisplayState.STARTED) {
                        hasStarted = true
                        _uiState.value =
                            _uiState.value.copy(
                                isPreparingDisplay = false,
                                snackbarMessage = "Display ready",
                            )
                        sendPendingSample()
                      }
                      if (state == DisplayState.STOPPED && hasStarted) {
                        _uiState.value =
                            _uiState.value.copy(
                                isPreparingDisplay = false,
                                snackbarMessage = "Display session stopped",
                            )
                      }
                    }
                  }
              replaceDisplayStateJob(stateJob)
            },
            onFailure = { error, _ ->
              Log.e(TAG, "Failed to attach display: ${error.description}")
              clearPendingWork()
              _uiState.value =
                  _uiState.value.copy(
                      isPreparingDisplay = false,
                      isSending = false,
                      hasSentContent = false,
                      errorMessage = error.description,
                      snackbarMessage = "Failed: ${error.description}",
                  )
            },
        )
  }

  // -- Content --

  private fun sendContent(onAccepted: (() -> Unit)? = null, content: ContentScope.() -> Unit): Job {
    _uiState.value =
        _uiState.value.copy(
            isSending = true,
            errorMessage = null,
        )
    return viewModelScope.launch(dispatcher + sendContentExceptionHandler) {
      val currentDisplay =
          synchronized(sessionLock) { display }
              ?: run {
                _uiState.value =
                    _uiState.value.copy(
                        isSending = false,
                        hasSentContent = false,
                        errorMessage = "No display attached",
                        snackbarMessage = "No display attached",
                    )
                return@launch
              }

      val result = currentDisplay.sendContent(content)
      result.fold(
          onSuccess = {
            onAccepted?.invoke()
            _uiState.value =
                _uiState.value.copy(
                    isSending = false,
                    hasSentContent = true,
                    errorMessage = null,
                )
          },
          onFailure = { error, _ ->
            _uiState.value =
                _uiState.value.copy(
                    isSending = false,
                    hasSentContent = false,
                    errorMessage = error.description,
                    snackbarMessage = "Send failed: ${error.description}",
                )
          },
      )
    }
  }

  // -- Sample management --

  fun sendSampleToDisplay(sample: SampleApp) {
    queueOrSendSample(
        sample = sample,
        deviceSelector = AutoDeviceSelector(filter = { it.isDisplayCapable() }),
        selectedDeviceId = null,
    )
  }

  fun sendSampleToPreview(
      sample: SampleApp,
      deviceId: DeviceIdentifier,
  ) {
    queueOrSendSample(
        sample = sample,
        deviceSelector = SpecificDeviceSelector(deviceId),
        selectedDeviceId = deviceId,
    )
  }

  private fun queueOrSendSample(
      sample: SampleApp,
      deviceSelector: DeviceSelector,
      selectedDeviceId: DeviceIdentifier?,
  ) {
    if (_uiState.value.displayState == DisplayState.STARTED) {
      sendSampleContent(sample)
      return
    }

    synchronized(sessionLock) { pendingSample = sample }
    _uiState.value =
        _uiState.value.copy(
            hasSentContent = false,
            errorMessage = null,
        )
    prepareDisplayConnection(deviceSelector, selectedDeviceId)
  }

  private fun sendPendingSample() {
    val sample =
        synchronized(sessionLock) {
          val pending = pendingSample
          pendingSample = null
          pending
        }
    if (sample != null) sendSampleContent(sample)
  }

  private fun sendSampleContent(sample: SampleApp) {
    Log.i(TAG, "Sending sample to display: ${sample.name}")
    when (sample) {
      SampleApp.CAR_MAINTENANCE -> displayYoloScreen()
    }
  }

  // -- Cleanup --

  fun clearSnackbarMessage() {
    _uiState.value = _uiState.value.copy(snackbarMessage = null)
  }

  private fun detachDisplay() {
    Log.d(TAG, "Detaching display")
    synchronized(sessionLock) { session }?.removeDisplay()
    cleanupDisplay()
    _uiState.value =
        _uiState.value.copy(
            isDisplayAttached = false,
            isPreparingDisplay = false,
            isSending = false,
            hasSentContent = false,
            snackbarMessage = "Display detached",
        )
  }

  suspend fun stopSession(): Boolean {
    return withContext(dispatcher) { stopSessionImmediately() }
  }

  private fun stopSessionImmediately(): Boolean {
    return try {
      Log.d(TAG, "Stopping session")
      clearPendingWork()
      _uiState.value =
          _uiState.value.copy(
              isStoppingSession = true,
              isPreparingDisplay = false,
              isSending = false,
              hasSentContent = false,
              errorMessage = null,
              snackbarMessage = "Stopping session...",
          )

      detachGlassesInputs()
      detachGlassesCamera()
      detachMotion()
      detachDisplay()
      clearSessionStateJob()?.cancel()
      clearSessionErrorJob()?.cancel()
      clearSession()?.stop()

      _uiState.value =
          _uiState.value.copy(
              connectionState = DwaConnectionState.DISCONNECTED,
              isSessionActive = false,
              isStartingSession = false,
              isStoppingSession = false,
              isDisplayAttached = false,
              selectedDeviceId = null,
              displayState = null,
              snackbarMessage = "Session stopped",
          )
      true
    } catch (error: CancellationException) {
      throw error
    } catch (error: Exception) {
      Log.e(TAG, "Stopping session failed", error)
      val message = getApplication<Application>().getString(R.string.stop_session_error)
      _uiState.value =
          _uiState.value.copy(
              isStoppingSession = false,
              isPreparingDisplay = false,
              isSending = false,
              hasSentContent = false,
              errorMessage = message,
              snackbarMessage = message,
          )
      false
    }
  }

  private fun cleanupDisplay() {
    hudLoop?.cancel(); hudLoop = null
    hudPredictor.reset(); hudMotion.clear()
    hudAccepted.set(0); hudSkipped = 0
    yoloRenderJob?.cancel()
    yoloRenderJob = null
    lastYoloRenderAt = 0L
    tutorialVideoStateJob?.cancel()
    tutorialVideoStateJob = null
    clearDisplayStateJob()?.cancel()
    synchronized(sessionLock) { display = null }
  }

  private fun handleSessionError(error: DeviceSessionError) {
    Log.e(TAG, "Session error: ${error.description}")
    clearPendingWork()
    cleanupDisplay()
    clearSessionStateJob()?.cancel()
    clearSessionErrorJob()?.cancel()
    clearSession()?.stop()
    _uiState.value =
        _uiState.value.copy(
            connectionState = DwaConnectionState.DISCONNECTED,
            isSessionActive = false,
            isStartingSession = false,
            isPreparingDisplay = false,
            isDisplayAttached = false,
            isDatAppUpdateRequired =
                error == DeviceSessionError.DAT_APP_ON_THE_GLASSES_UPDATE_REQUIRED,
            isSending = false,
            hasSentContent = false,
            selectedDeviceId = null,
            displayState = null,
            errorMessage = error.description,
            snackbarMessage = error.description,
        )
  }

  private fun displayCarMaintenanceScreen() {
    sendContent { carMaintenanceTutorialListContent() }
  }

  private fun displayCarMaintenanceTutorialDetail(tutorial: CarMaintenanceTutorial) {
    sendContent {
      flexBox(direction = Direction.COLUMN, gap = 12) {
        flexBox(padding = 24, background = FlexBoxBackground.CARD) {
          tutorialImage(tutorial.imageRes, tutorial.imageUri)
          text(tutorial.title, style = TextStyle.HEADING)
          text(tutorial.duration, style = TextStyle.META, color = TextColor.SECONDARY)
        }
        flexBox(
            direction = Direction.ROW,
            gap = 8,
            alignment = Alignment.CENTER,
            crossAlignment = Alignment.CENTER,
            wrap = true,
        ) {
          buttonGroup {
            button(
                "Back",
                onClick = { displayCarMaintenanceScreen() },
            )
            button(
                "Start",
                onClick = { displayCarMaintenanceTutorialStep(tutorial, 0) },
            )
          }
        }
      }
    }
  }

  private fun displayTutorialVideo(
      tutorial: CarMaintenanceTutorial,
      stepIndex: Int,
  ) {
    viewModelScope.launch(dispatcher + sendContentExceptionHandler) {
      val currentDisplay =
          synchronized(sessionLock) { display }
              ?: run {
                _uiState.value = _uiState.value.copy(snackbarMessage = "No display attached")
                return@launch
              }

      val player = VideoPlayer(
          source = VideoSource.Url(TUTORIAL_VIDEO_URL),
          codec = VideoCodec.MP4,
      )

      val result = currentDisplay.sendContent { video(player = player) }
      result.fold(
          onSuccess = {
            tutorialVideoStateJob?.cancel()
            tutorialVideoStateJob = viewModelScope.launch {
              player.state.collect { state ->
                if (state == VideoPlayerState.ENDED) {
                  tutorialVideoStateJob?.cancel()
                  tutorialVideoStateJob = null
                  displayCarMaintenanceTutorialStep(tutorial, stepIndex)
                }
              }
            }
            _uiState.value = _uiState.value.copy(snackbarMessage = "Starting video...")
            player.play()
          },
          onFailure = { error, _ ->
            _uiState.value =
                _uiState.value.copy(snackbarMessage = "Send failed: ${error.description}")
          },
      )
    }
  }

  private fun displayCarMaintenanceTutorialStep(
      tutorial: CarMaintenanceTutorial,
      stepIndex: Int,
  ) {
    val clampedIndex = stepIndex.coerceIn(0, tutorial.steps.lastIndex)
    val step = tutorial.steps[clampedIndex]
    sendContent {
      flexBox(direction = Direction.COLUMN, gap = 12) {
        flexBox(padding = 24, background = FlexBoxBackground.CARD) {
          text(
              "Step ${clampedIndex + 1}",
              style = TextStyle.META,
              color = TextColor.SECONDARY,
          )
          text(step.description, style = TextStyle.BODY)
        }

        flexBox(
            direction = Direction.ROW,
            gap = 8,
            alignment = Alignment.CENTER,
            crossAlignment = Alignment.CENTER,
        ) {
          val isLastStep = clampedIndex == tutorial.steps.lastIndex
          buttonGroup {
            button(
                "Previous",
                style = ButtonStyle.PRIMARY,
                iconName = IconName.TRIANGLE_LEFT_VERTICAL_LINE,
                onClick = {
                  if (clampedIndex == 0) {
                    displayCarMaintenanceTutorialDetail(tutorial)
                  } else {
                    displayCarMaintenanceTutorialStep(tutorial, clampedIndex - 1)
                  }
                },
            )
            button(
                if (isLastStep) "Done" else "Next",
                style = ButtonStyle.PRIMARY,
                iconName =
                    if (isLastStep) {
                      IconName.CHECKMARK
                    } else {
                      IconName.TRIANGLE_RIGHT_VERTICAL_LINE
                    },
                onClick = {
                  if (isLastStep) {
                    displayCarMaintenanceScreen()
                  } else if (clampedIndex < tutorial.steps.lastIndex) {
                    displayCarMaintenanceTutorialStep(tutorial, clampedIndex + 1)
                  }
                },
                actionRole = ActionRole.PRIMARY,
            )
            button(
                "Watch video",
                style = ButtonStyle.SECONDARY,
                iconName = IconName.VIDEO_CAMERA,
                onClick = { displayTutorialVideo(tutorial, clampedIndex) },
            )
          }
        }
      }
    }
  }

  private fun ContentScope.carMaintenanceTutorialListContent() {
    flexBox(direction = Direction.COLUMN, gap = 10) {
      carMaintenanceTutorials.forEach { tutorial -> maintenanceTutorialItem(tutorial) }
    }
  }

  private fun FlexBoxScope.maintenanceTutorialItem(tutorial: CarMaintenanceTutorial) {
    flexBox(
        padding = 24,
        background = FlexBoxBackground.CARD,
        onClick = { displayCarMaintenanceTutorialDetail(tutorial) },
    ) {
      flexBox(direction = Direction.ROW, gap = 12, crossAlignment = Alignment.CENTER) {
        if (tutorial.iconRes != null || tutorial.iconImageUri != null) {
          flexBox(direction = Direction.COLUMN, flexGrow = 1f) {
            tutorialImage(tutorial.iconRes, tutorial.iconImageUri)
          }
        }
        flexBox(direction = Direction.COLUMN, flexGrow = 7f) {
          text(tutorial.title, style = TextStyle.BODY)
          text(tutorial.duration, style = TextStyle.META, color = TextColor.SECONDARY)
        }
      }
    }
  }

  private fun FlexBoxScope.tutorialImage(@DrawableRes res: Int?, uri: String?) {
    val bitmap = res?.let { resId ->
      BitmapFactory.decodeResource(getApplication<Application>().resources, resId)
    }
    when {
      bitmap != null ->
          image(bitmap = bitmap, sizePreset = ImageSize.FILL, cornerRadius = CornerRadius.MEDIUM)
      uri != null ->
          image(uri = uri, sizePreset = ImageSize.FILL, cornerRadius = CornerRadius.MEDIUM)
    }
  }

  override fun onCleared() {
    super.onCleared()
    stopSessionImmediately()
  }

  private fun consumePendingDisplayAttachment(): Boolean =
      synchronized(sessionLock) {
        val shouldAttach = pendingDisplayAttachment && !_uiState.value.isDisplayAttached
        pendingDisplayAttachment = false
        shouldAttach
      }

  private fun clearPendingWork() {
    synchronized(sessionLock) {
      pendingDisplayAttachment = false
      pendingSample = null
    }
  }

  private fun replaceSessionStateJob(job: Job) {
    synchronized(sessionLock) {
      sessionStateJob?.cancel()
      sessionStateJob = job
    }
  }

  private fun replaceSessionErrorJob(job: Job) {
    synchronized(sessionLock) {
      sessionErrorJob?.cancel()
      sessionErrorJob = job
    }
  }

  private fun replaceDisplayStateJob(job: Job) {
    synchronized(sessionLock) {
      displayStateJob?.cancel()
      displayStateJob = job
    }
  }

  private fun clearSessionStateJob(): Job? =
      synchronized(sessionLock) {
        val currentJob = sessionStateJob
        sessionStateJob = null
        currentJob
      }

  private fun clearSessionErrorJob(): Job? =
      synchronized(sessionLock) {
        val currentJob = sessionErrorJob
        sessionErrorJob = null
        currentJob
      }

  private fun clearDisplayStateJob(): Job? =
      synchronized(sessionLock) {
        val currentJob = displayStateJob
        displayStateJob = null
        currentJob
      }

  private fun clearSession(): DeviceSession? =
      synchronized(sessionLock) {
        val currentSession = session
        session = null
        currentSession
      }
}

private data class CarMaintenanceTutorial(
    val title: String,
    val duration: String,
    val imageUri: String? = null,
    val iconImageUri: String? = null,
    @param:DrawableRes val imageRes: Int? = null,
    @param:DrawableRes val iconRes: Int? = null,
    val steps: List<CarMaintenanceTutorialStep>,
)

private data class CarMaintenanceTutorialStep(
    val description: String,
)
