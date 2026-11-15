package com.worldgit.hud

import android.annotation.SuppressLint
import android.app.Activity
import android.app.Application
import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.Handler
import android.os.HandlerThread
import android.os.SystemClock
import android.speech.tts.TextToSpeech
import android.util.Log
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.meta.wearable.dat.display.views.ActionRole
import com.meta.wearable.dat.display.views.Alignment
import com.meta.wearable.dat.display.views.ButtonGroupAlignment
import com.meta.wearable.dat.display.views.ButtonStyle
import com.meta.wearable.dat.display.views.Direction
import com.meta.wearable.dat.display.views.FlexBoxBackground
import com.meta.wearable.dat.display.views.IconName
import com.meta.wearable.dat.display.views.IconStyle
import com.meta.wearable.dat.display.views.ImageSize
import com.meta.wearable.dat.display.views.TextColor
import com.meta.wearable.dat.display.views.TextStyle
import com.worldgit.hud.hud.WorldGitHudRenderer
import com.worldgit.hud.spatial.*
import com.worldgit.hud.wearables.GlassesHudPacket
import com.worldgit.hud.wearables.WearablesSessionManager
import kotlin.math.roundToInt
import kotlinx.coroutines.*
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.io.ByteArrayOutputStream
import java.util.Locale
import java.util.concurrent.atomic.AtomicBoolean

class WorldGitViewModel(application: Application) : AndroidViewModel(application) {

  companion object {
    private const val TAG = "WorldGitVM"
  }

  private val prefs = application.getSharedPreferences("worldgit_hud", 0)
  private val initialHost = prefs.getString("host", "127.0.0.1") ?: "127.0.0.1"
  private val initialToken =
    (prefs.getString("api_token", null) ?: BuildConfig.WORLDGIT_API_TOKEN).trim().also {
      WorldGitApi.setApiToken(it)
    }
  private val initialCal = HudCalibration(
    size = prefs.getFloat("hud_size", 0.8522121f),
    horizontal = prefs.getFloat("hud_x", -512.44965f),
    vertical = prefs.getFloat("hud_y", -365.0765f),
    roll = prefs.getFloat("hud_roll", 0f),
  )

  private val _ui = MutableStateFlow(
    AppUiState(
      host = initialHost,
      apiToken = initialToken,
      calibration = initialCal,
    ),
  )
  val ui = _ui.asStateFlow()

  private val sensors = application.getSystemService(Context.SENSOR_SERVICE) as SensorManager
  private val cameraThread = HandlerThread("WorldGitVision").apply { start() }
  private val visionHandler = Handler(cameraThread.looper)

  private val localTracker = LocalObjectTracker()
  private val hudPredictor = HudFramePredictor()
  private val glassesMotion = MotionPredictor()
  private val phoneMotion = MotionPredictor()
  private val headCursor = HeadCursorController()

  private val frameBusy = AtomicBoolean(false)
  private val networkBusy = AtomicBoolean(false)
  private var nextUploadAtNs = 0L
  @Volatile private var lastVisionOkMs = 0L
  @Volatile private var visionAttemptStartMs = 0L
  @Volatile private var visionConsecutiveFailures = 0
  @Volatile private var manualSelectionHoldUntilMs = 0L
  @Volatile private var sessionEpoch = 0

  private var spatialObjects: List<SpatialObject> = emptyList()
  private var wearerPos = Triple(0.0, 1.6, 0.0)
  @Volatile private var wearerLocalized = false
  private var activeTargetLabel = ""
  private var activeTargetId: String? = null
  @Volatile private var activeTargetLocalized = false
  @Volatile private var activeTargetStale = false
  private var imuSessionId = "glasses-live"
  private var currentYawDeg = 0f
  private var currentPitchDeg = 0f
  private var currentRollDeg = 0f
  private var sensorSource = "READY"
  private var lastImuPushMs = 0L

  private val glassesAudioQueue = Channel<ByteArray>(capacity = Channel.CONFLATED.let { 64 })
  private var speechJob: Job? = null
  private var manualStopSpeech = false

  private var tts: TextToSpeech? = null
  @Volatile private var ttsReady = false

  private val wearablesManager = WearablesSessionManager(
    scope = viewModelScope,
    onVideoFrame = ::receiveGlassesVideoFrame,
    onAudioPcm = { bytes -> glassesAudioQueue.trySend(bytes) },
    onMotionSample = ::receiveGlassesMotion,
    onHudSelect = { viewModelScope.launch(Dispatchers.Main) { handleHudPrimaryAction() } },
    onHudNav = { dir -> viewModelScope.launch(Dispatchers.Main) { handleHudNav(dir) } },
    onHudBack = { viewModelScope.launch(Dispatchers.Main) { handleHudBack() } },
    buildHudScreen = ::buildGlassesHudPacket,
  )

  private val phoneSensorListener = object : SensorEventListener {
    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) {}
    override fun onSensorChanged(event: SensorEvent) {
      if (event.sensor.type == Sensor.TYPE_GYROSCOPE) {
        phoneMotion.angularVelocity(event.values[0], -event.values[1], -event.values[2], event.timestamp)
        return
      }
      val rot = FloatArray(9)
      SensorManager.getRotationMatrixFromVector(rot, event.values)
      for (row in 0..2) {
        rot[row * 3 + 1] *= -1f
        rot[row * 3 + 2] *= -1f
      }
      phoneMotion.orientation(rot, event.timestamp)
      // Use phone sensors only if glasses motion is not actively updating
      if (!wearablesManager.snapshot.value.cameraStreaming) {
        val (yaw, pitch, roll) = CompassMath.extractEulerDeg(rot)
        currentYawDeg = yaw
        currentPitchDeg = pitch
        currentRollDeg = roll
        sensorSource = "PHONE"
        headCursor.updatePose(rot)
        refreshCompassState()
      }
    }
  }

  init {
    tts = TextToSpeech(application) { st ->
      if (st == TextToSpeech.SUCCESS) {
        tts?.language = Locale.US
        ttsReady = true
      }
    }
    wearablesManager.initialize(application)

    viewModelScope.launch {
      wearablesManager.snapshot.collect { snap ->
        _ui.value = _ui.value.copy(
          registered = snap.registered,
          glassesConnected = snap.connectedDeviceName != null || snap.displayStarted,
          displayStarted = snap.displayStarted,
          cameraStreaming = snap.cameraStreaming,
          inputsAttached = snap.inputsAttached,
          firmwareUpdateRequired = snap.firmwareUpdateRequired,
          datAppUpdateRequired = snap.datAppUpdateRequired,
          deviceStatus = snap.statusMessage,
        )
      }
    }

    startPhoneSensorsFallback()
    startBackendSyncLoops()
    startPhoneHudMirrorLoop()
  }

  private fun startPhoneSensorsFallback() {
    sensors.getDefaultSensor(Sensor.TYPE_GAME_ROTATION_VECTOR)?.let {
      sensors.registerListener(phoneSensorListener, it, SensorManager.SENSOR_DELAY_GAME)
    }
    sensors.getDefaultSensor(Sensor.TYPE_GYROSCOPE)?.let {
      sensors.registerListener(phoneSensorListener, it, SensorManager.SENSOR_DELAY_GAME)
    }
  }

  private fun startBackendSyncLoops() {
    viewModelScope.launch(Dispatchers.IO) {
      // Pull calibration once on startup
      WorldGitApi.fetchCalibration(_ui.value.host)?.let { serverCal ->
        prefs.edit()
          .putFloat("hud_size", serverCal.size)
          .putFloat("hud_x", serverCal.horizontal)
          .putFloat("hud_y", serverCal.vertical)
          .putFloat("hud_roll", serverCal.roll)
          .apply()
        _ui.value = _ui.value.copy(calibration = serverCal.copy(showGuide = _ui.value.calibration.showGuide))
      }

      while (isActive) {
        val host = _ui.value.host
        val reqEpoch = sessionEpoch
        val st = WorldGitApi.fetchWorldState(host)
        val notesRes = WorldGitApi.fetchSavedNotes(host)

        if (reqEpoch != sessionEpoch || host != _ui.value.host) {
          delay(250L)
          continue
        }

        if (st != null) {
          spatialObjects = st.objects
          wearerPos = st.wearerPos
          wearerLocalized = st.wearerLocalized
          if (activeTargetId == null && st.activeTargetId != null) {
            st.objects.firstOrNull { it.id == st.activeTargetId }?.let { t ->
              activeTargetLabel = t.label
              activeTargetId = t.id
              activeTargetLocalized = st.wearerLocalized
              activeTargetStale = false
            }
          }
          _ui.value = _ui.value.copy(undoSteps = st.undoSteps)
          refreshCompassState()
        }

        notesRes.onSuccess { notes ->
          val byId = notes
            .filter { it.objectId.isNotBlank() }
            .associate { it.objectId to it.text }
          val curSpeech = _ui.value.speech
          _ui.value = _ui.value.copy(
            savedNotes = notes,
            notesSyncError = null,
            speech = curSpeech.copy(
              notesByObjectId = byId,
            ),
          )
        }.onFailure { err ->
          _ui.value = _ui.value.copy(
            notesSyncError = "Notes sync error: ${err.message ?: "offline"}",
          )
        }
        delay(1500L)
      }
    }
  }

  private fun startPhoneHudMirrorLoop() {
    viewModelScope.launch {
      while (isActive) {
        if (!_ui.value.displayStarted) {
          // Keep projected overlay & hover selection updated cleanly on phone too
          updateProjectedStateAndCursor()
        }
        delay(100L)
      }
    }
  }

  fun updateHost(newHost: String) {
    val clean = newHost.trim().ifBlank { "127.0.0.1" }
    sessionEpoch += 1
    prefs.edit().putString("host", clean).apply()
    spatialObjects = emptyList()
    wearerLocalized = false
    activeTargetId = null
    activeTargetLabel = ""
    activeTargetLocalized = false
    activeTargetStale = false
    lastVisionOkMs = 0L
    visionAttemptStartMs = 0L
    visionConsecutiveFailures = 0
    _ui.value = _ui.value.copy(
      host = clean,
      savedNotes = emptyList(),
      undoSteps = emptyList(),
      noteSaveStatus = null,
      pendingRetryNote = null,
      notesSyncError = null,
      speech = _ui.value.speech.copy(notesByObjectId = emptyMap()),
    )
    refreshCompassState()
    val reqEpoch = sessionEpoch
    viewModelScope.launch(Dispatchers.IO) {
      WorldGitApi.fetchCalibration(clean)?.let { c ->
        if (reqEpoch == sessionEpoch && _ui.value.host == clean) {
          _ui.value = _ui.value.copy(calibration = c)
        }
      }
    }
  }

  fun updateApiToken(newToken: String) {
    val clean = newToken.trim()
    prefs.edit().putString("api_token", clean).apply()
    WorldGitApi.setApiToken(clean)
    _ui.value = _ui.value.copy(
      apiToken = clean,
      snackbar = if (clean.isEmpty()) "API token cleared" else "API token updated",
    )
  }

  fun connectOrDisconnectGlasses(activity: Activity, hasAudioPermission: Boolean) {
    val snap = wearablesManager.snapshot.value
    if (snap.displayStarted || snap.cameraStreaming) {
      sessionEpoch += 1
      wearablesManager.stopSession()
      return
    }
    if (!snap.registered) {
      wearablesManager.startRegistration(activity)
      return
    }
    sessionEpoch += 1
    wearablesManager.connectGlasses(withMicrophone = hasAudioPermission)
  }

  fun openFirmwareUpdate(activity: Activity) = wearablesManager.openFirmwareUpdate(activity)
  fun openDatAppUpdate(activity: Activity) = wearablesManager.openDatAppUpdate(activity)

  fun setMode(mode: HudMode) {
    if (_ui.value.speech.active) cancelSpeech()
    if (mode == HudMode.PINS) {
      headCursor.recenter()
    }
    _ui.value = _ui.value.copy(mode = mode)
    if (mode == HudMode.UNDO) {
      runCtrlZ()
    }
    wearablesManager.requestImmediateHudRefresh()
  }

  fun onDockButtonTap(mode: HudMode) {
    if (_ui.value.mode == mode) {
      when (mode) {
        HudMode.ASK -> toggleAskVoice()
        HudMode.PINS -> handlePinsPrimaryTap()
        HudMode.UNDO -> runCtrlZ()
        HudMode.SCAN -> toggleImuRecording()
      }
    } else {
      setMode(mode)
      if (mode == HudMode.ASK && _ui.value.compass.answerText.startsWith("Tap Ask")) {
        // Switching back to Ask shows Compass immediately without auto-recording unless already in Ask
      }
    }
  }

  fun handleHudNav(direction: String) {
    if (_ui.value.speech.active) return
    val next = when (direction.uppercase()) {
      "LEFT", "UP" -> _ui.value.mode.prev()
      else -> _ui.value.mode.next()
    }
    setMode(next)
  }

  fun handleHudBack() {
    if (_ui.value.speech.active) {
      cancelSpeech()
      return
    }
    if (_ui.value.mode != HudMode.ASK) {
      setMode(HudMode.ASK)
    }
  }

  fun handleHudPrimaryAction() {
    if (_ui.value.speech.active) {
      manualStopSpeech = true
      return
    }
    when (_ui.value.mode) {
      HudMode.ASK -> toggleAskVoice()
      HudMode.PINS -> handlePinsPrimaryTap()
      HudMode.UNDO -> runCtrlZ()
      HudMode.SCAN -> toggleImuRecording()
    }
  }

  private fun handlePinsPrimaryTap() {
    val cur = _ui.value
    if (cur.speech.active) {
      manualStopSpeech = true
      return
    }
    val hoveredId = cur.cursor.selectedId
      ?: WorldGitHudRenderer.hoveredObjectId(cur.projectedYoloState, cur.cursor, cur.speech)
    val item = cur.projectedYoloState.items.firstOrNull { it.id == hoveredId }
      ?: cur.yoloState.items.firstOrNull { it.id == hoveredId }
    if (item != null) {
      startSpeechCapture(
        purpose = SpeechPurpose.PIN_NOTE,
        noteId = item.id,
        noteLabel = item.label,
        noteBox = item.box,
      )
    } else {
      headCursor.recenter()
      _ui.value = _ui.value.copy(cursor = headCursor.state, snackbar = "Cursor centered • look at an object")
    }
  }

  fun onPhoneOverlayMove(xNorm: Float, yNorm: Float) {
    headCursor.setPointer(xNorm, yNorm)
    updateProjectedStateAndCursor()
  }

  fun onPhoneOverlayTap(xNorm: Float, yNorm: Float) {
    if (_ui.value.speech.active) {
      manualStopSpeech = true
      return
    }
    headCursor.setPointer(xNorm, yNorm)
    val projected = updateProjectedStateAndCursor()
    val hitId = WorldGitHudRenderer.hoveredObjectId(projected, headCursor.state, _ui.value.speech)
    if (hitId != null) {
      headCursor.updateHoverSelection(hitId)
      _ui.value = _ui.value.copy(cursor = headCursor.state)
      val item = projected.items.firstOrNull { it.id == hitId }
        ?: _ui.value.yoloState.items.firstOrNull { it.id == hitId }
      if (item != null) {
        startSpeechCapture(SpeechPurpose.PIN_NOTE, item.id, item.label, item.box)
      }
    }
  }

  fun toggleAskVoice() {
    if (_ui.value.speech.active) {
      manualStopSpeech = true
    } else {
      startSpeechCapture(SpeechPurpose.ASK_GEMINI, null, null, null)
    }
  }

  fun askTypedQuestion(question: String) {
    if (question.isBlank()) return
    queryGeminiAndUpdateCompass(question.trim())
  }

  private fun queryGeminiAndUpdateCompass(question: String) {
    val reqEpoch = sessionEpoch
    val reqHost = _ui.value.host
    viewModelScope.launch(Dispatchers.IO) {
      runCatching {
        WorldGitApi.askGemini(reqHost, question, currentYawDeg)
      }.onSuccess { res ->
        if (reqEpoch != sessionEpoch || reqHost != _ui.value.host) return@onSuccess
        if (!res.found) {
          activeTargetId = null
          activeTargetLabel = ""
          activeTargetLocalized = false
          activeTargetStale = false
          _ui.value = _ui.value.copy(
            mode = HudMode.ASK,
            compass = _ui.value.compass.copy(
              targetLabel = "",
              hasNavigation = false,
              localizationStatus = "NO_TARGET",
              arrowGlyph = "",
              turnInstruction = "No recent observation",
              aligned = false,
              answerText = res.answer,
              memoryContext = null,
              ambiguityNote = null,
            ),
          )
          refreshCompassState()
          wearablesManager.requestImmediateHudRefresh()
          speakAloud("ask-${System.currentTimeMillis()}", res.answer)
          return@onSuccess
        }

        if (res.objects.isNotEmpty()) {
          spatialObjects = res.objects
        }
        val matched = res.objects.firstOrNull {
          (res.targetObjectId != null && it.id == res.targetObjectId) ||
            (res.targetObject != null && it.label.equals(res.targetObject, ignoreCase = true))
        }
        if (matched != null) {
          activeTargetId = matched.id
          activeTargetLabel = matched.label
        } else {
          activeTargetId = res.targetObjectId
          activeTargetLabel = res.targetObject.orEmpty()
        }
        activeTargetLocalized = res.localized && matched != null && (wearerLocalized || !res.fromMemory)
        activeTargetStale = res.stale
        val memCtx = listOfNotNull(res.ageLabel, res.context).joinToString(" • ").takeIf { it.isNotBlank() }
        _ui.value = _ui.value.copy(
          mode = HudMode.ASK,
          compass = _ui.value.compass.copy(
            targetLabel = activeTargetLabel,
            answerText = res.answer,
            memoryContext = memCtx,
            ambiguityNote = res.ambiguity,
          ),
        )
        refreshCompassState()
        wearablesManager.requestImmediateHudRefresh()
        speakAloud("ask-${System.currentTimeMillis()}", res.answer)
      }.onFailure { err ->
        if (reqEpoch != sessionEpoch || reqHost != _ui.value.host) return@onFailure
        activeTargetId = null
        activeTargetLabel = ""
        activeTargetLocalized = false
        activeTargetStale = false
        _ui.value = _ui.value.copy(
          compass = _ui.value.compass.copy(
            targetLabel = "",
            hasNavigation = false,
            localizationStatus = "NO_TARGET",
            arrowGlyph = "",
            turnInstruction = "Server unreachable",
            aligned = false,
            answerText = "Cannot reach WorldGit server (${err.message})",
            memoryContext = null,
            ambiguityNote = null,
          ),
        )
        wearablesManager.requestImmediateHudRefresh()
      }
    }
  }

  private fun startSpeechCapture(
    purpose: SpeechPurpose,
    noteId: String?,
    noteLabel: String?,
    noteBox: List<Double>?,
  ) {
    speechJob?.cancel()
    manualStopSpeech = false
    while (glassesAudioQueue.tryReceive().isSuccess) { /* flush stale PCM */ }

    _ui.value = _ui.value.copy(
      speech = _ui.value.speech.copy(
        active = true,
        finishing = false,
        purpose = purpose,
        noteId = noteId,
        noteLabel = noteLabel,
        noteBox = noteBox,
        text = "",
        error = null,
      ),
    )

    val reqEpoch = sessionEpoch
    val reqHost = _ui.value.host

    speechJob = viewModelScope.launch(Dispatchers.IO) {
      val pcmBuffer = ByteArrayOutputStream()
      val endpoint = SpeechEndpoint(sampleRate = 16000, manualStop = false)
      val useGlassesMic = wearablesManager.snapshot.value.cameraStreaming
      var recorder: AudioRecord? = null
      var streamStalled = false
      val captureStartMs = SystemClock.elapsedRealtime()

      try {
        var fallbackToPhoneMic = !useGlassesMic
        if (useGlassesMic) {
          var gotAnyGlassesAudio = false
          val glassesStartMs = SystemClock.elapsedRealtime()
          var lastChunkMs = glassesStartMs
          while (isActive && !manualStopSpeech) {
            val chunk = withTimeoutOrNull(80L) { glassesAudioQueue.receive() }
            val nowMs = SystemClock.elapsedRealtime()
            if (chunk != null && chunk.size >= 2) {
              gotAnyGlassesAudio = true
              lastChunkMs = nowMs
              pcmBuffer.write(chunk)
              val shorts = ShortArray(chunk.size / 2) { idx ->
                val lo = chunk[idx * 2].toInt() and 0xFF
                val hi = chunk[idx * 2 + 1].toInt()
                ((hi shl 8) or lo).toShort()
              }
              if (endpoint.add(shorts, shorts.size)) break
            } else if (!gotAnyGlassesAudio && nowMs - glassesStartMs >= 1000L) {
              Log.w(TAG, "No glasses PCM audio after 1s, falling back to phone microphone")
              fallbackToPhoneMic = true
              break
            }
            val deadlineReason = endpoint.wallClockDeadlineReason(
              elapsedMs = nowMs - glassesStartMs,
              sinceLastChunkMs = nowMs - lastChunkMs,
              gotAnyAudio = gotAnyGlassesAudio,
            )
            if (deadlineReason != null) {
              if (deadlineReason == "STREAM_STALLED") {
                streamStalled = true
              }
              Log.w(TAG, "Glasses audio stopped due to deadline: $deadlineReason")
              break
            }
          }
        }
        if (fallbackToPhoneMic && !manualStopSpeech) {
          recorder = openPhoneAudioRecord()
          if (recorder == null) {
            throw IllegalStateException("Microphone permission required")
          }
          if (recorder.state != AudioRecord.STATE_INITIALIZED) {
            throw IllegalStateException("Microphone initialization failed")
          }
          recorder.startRecording()
          if (recorder.recordingState != AudioRecord.RECORDSTATE_RECORDING) {
            throw IllegalStateException("Microphone failed to start recording")
          }
          val buf = ShortArray(1024)
          val phoneStartMs = SystemClock.elapsedRealtime()
          var lastPhoneChunkMs = phoneStartMs
          while (isActive && !manualStopSpeech) {
            val count = recorder.read(buf, 0, buf.size)
            val nowMs = SystemClock.elapsedRealtime()
            if (count < 0) {
              throw IllegalStateException("Microphone stream error ($count)")
            }
            if (count > 0) {
              lastPhoneChunkMs = nowMs
              val bytes = ByteArray(count * 2)
              for (i in 0 until count) {
                bytes[i * 2] = (buf[i].toInt() and 0xFF).toByte()
                bytes[i * 2 + 1] = ((buf[i].toInt() shr 8) and 0xFF).toByte()
              }
              pcmBuffer.write(bytes)
              if (endpoint.add(buf, count)) break
            }
            if (nowMs - phoneStartMs >= 25_000L || nowMs - lastPhoneChunkMs >= 2_500L) {
              Log.w(TAG, "Phone microphone capture hit wall-clock deadline")
              break
            }
          }
        }
      } catch (e: CancellationException) {
        throw e
      } catch (e: Exception) {
        _ui.value = _ui.value.copy(
          speech = _ui.value.speech.copy(active = false, finishing = false, error = e.message),
          snackbar = "Microphone error: ${e.message}",
        )
        return@launch
      } finally {
        runCatching { recorder?.stop() }
        runCatching { recorder?.release() }
      }

      if (reqEpoch != sessionEpoch || reqHost != _ui.value.host) {
        cancelSpeech()
        return@launch
      }

      val audio = pcmBuffer.toByteArray()
      if (audio.size < 3200) {
        val msg = if (streamStalled) {
          "Glasses mic disconnected mid-recording — reconnect or use phone mic"
        } else {
          "No speech detected (silence)"
        }
        _ui.value = _ui.value.copy(
          speech = _ui.value.speech.copy(active = false, finishing = false, text = "", error = if (streamStalled) msg else null),
          snackbar = msg,
        )
        return@launch
      }

      _ui.value = _ui.value.copy(
        speech = _ui.value.speech.copy(finishing = true),
      )

      val trRes = runCatching {
        WorldGitApi.transcribePcmWithResult(reqHost, audio)
      }.getOrElse { err ->
        TranscriptionResult(
          text = "",
          status = TranscriptionStatus.SERVICE_UNAVAILABLE,
          error = err.message ?: "Transcription failed",
        )
      }

      if (reqEpoch != sessionEpoch || reqHost != _ui.value.host) {
        cancelSpeech()
        return@launch
      }

      val transcript = trRes.text
      Log.d(TAG, "Speech transcript ($purpose, ${audio.size} bytes, ${trRes.status}, provider=${trRes.provider}): '$transcript'")

      if (trRes.status == TranscriptionStatus.SERVICE_UNAVAILABLE) {
        val msg = "Transcription service unavailable (${trRes.error ?: "check local/cloud ASR"})"
        _ui.value = _ui.value.copy(
          speech = _ui.value.speech.copy(
            active = false,
            finishing = false,
            text = "",
            error = msg,
            providerDisclosure = trRes.disclosure,
          ),
          snackbar = msg,
        )
        return@launch
      }

      if (transcript.isBlank()) {
        val msg = if (streamStalled) {
          "Glasses mic stalled — silence/partial audio only"
        } else {
          "Silence detected — try speaking closer"
        }
        _ui.value = _ui.value.copy(
          speech = _ui.value.speech.copy(
            active = false,
            finishing = false,
            text = "",
            providerDisclosure = trRes.disclosure,
          ),
          snackbar = msg,
        )
        return@launch
      }

      if (purpose == SpeechPurpose.ASK_GEMINI) {
        _ui.value = _ui.value.copy(
          speech = _ui.value.speech.copy(
            active = false,
            finishing = false,
            text = transcript,
            providerDisclosure = trRes.disclosure,
          ),
          snackbar = trRes.disclosure,
        )
        queryGeminiAndUpdateCompass(transcript)
      } else if (purpose == SpeechPurpose.PIN_NOTE && noteId != null) {
        val rawItem = _ui.value.yoloState.items.firstOrNull { it.id == noteId }
        val rawBox = rawItem?.box ?: noteBox
        val stableId = java.util.UUID.randomUUID().toString().replace("-", "").take(12)
        val pending = PendingNoteSave(
          noteId = stableId,
          objectId = noteId,
          label = noteLabel ?: "object",
          text = transcript,
          box = rawBox,
          frameWidth = _ui.value.yoloState.width,
          frameHeight = _ui.value.yoloState.height,
        )
        _ui.value = _ui.value.copy(
          noteSaveStatus = "Saving…",
          pendingRetryNote = pending,
          speech = _ui.value.speech.copy(
            active = false,
            finishing = false,
            noteId = null,
            noteLabel = null,
            noteBox = null,
            text = transcript,
            providerDisclosure = trRes.disclosure,
          ),
          snackbar = "Saving note…",
        )
        executeNoteSave(pending, reqHost, reqEpoch)
      }
    }
  }

  private suspend fun executeNoteSave(pending: PendingNoteSave, reqHost: String, reqEpoch: Int) {
    val saveRes = WorldGitApi.savePermanentNote(
      host = reqHost,
      objectId = pending.objectId,
      label = pending.label,
      text = pending.text,
      box = pending.box,
      frameWidth = pending.frameWidth,
      frameHeight = pending.frameHeight,
      noteId = pending.noteId,
    )
    if (reqEpoch != sessionEpoch || reqHost != _ui.value.host) {
      return
    }
    if (saveRes.discarded) {
      _ui.value = _ui.value.copy(
        noteSaveStatus = "Discarded (session changed)",
        pendingRetryNote = null,
        snackbar = "Note discarded because session was paused or cleared",
      )
      return
    }
    if (!saveRes.ok) {
      _ui.value = _ui.value.copy(
        noteSaveStatus = "Failed—Retry",
        pendingRetryNote = pending,
        snackbar = "Failed to save note (${saveRes.error ?: "server error"}) — Retry",
      )
      return
    }
    val curSpeech = _ui.value.speech
    val updatedById = curSpeech.notesByObjectId + (pending.objectId to pending.text)
    val confirmedId = saveRes.noteId ?: pending.noteId
    val newEntry = SavedNote(confirmedId, pending.objectId, pending.label, pending.text, "You")
    val statusText = if (saveRes.studioSynced) "Saved" else "Saved locally; Studio sync pending"
    _ui.value = _ui.value.copy(
      savedNotes = (listOf(newEntry) + _ui.value.savedNotes).distinctBy { it.noteId },
      noteSaveStatus = statusText,
      pendingRetryNote = null,
      speech = curSpeech.copy(notesByObjectId = updatedById),
      snackbar = "$statusText: ${pending.label}",
    )
    speakAloud("saved-${pending.objectId}", "$statusText: ${pending.text}")
  }

  fun retryPendingNoteSave() {
    val pending = _ui.value.pendingRetryNote ?: return
    val reqEpoch = sessionEpoch
    val reqHost = _ui.value.host
    _ui.value = _ui.value.copy(noteSaveStatus = "Saving…", snackbar = "Retrying note save…")
    viewModelScope.launch(Dispatchers.IO) {
      executeNoteSave(pending, reqHost, reqEpoch)
    }
  }

  @SuppressLint("MissingPermission")
  private fun openPhoneAudioRecord(): AudioRecord? {
    if (getApplication<Application>().checkSelfPermission(android.Manifest.permission.RECORD_AUDIO) !=
      android.content.pm.PackageManager.PERMISSION_GRANTED
    ) return null
    val minBuf = AudioRecord.getMinBufferSize(16000, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
    return AudioRecord(
      MediaRecorder.AudioSource.VOICE_RECOGNITION,
      16000,
      AudioFormat.CHANNEL_IN_MONO,
      AudioFormat.ENCODING_PCM_16BIT,
      maxOf(minBuf, 4096),
    )
  }

  private fun cancelSpeech() {
    speechJob?.cancel()
    speechJob = null
    _ui.value = _ui.value.copy(
      speech = _ui.value.speech.copy(
        active = false,
        finishing = false,
        purpose = SpeechPurpose.NONE,
        noteId = null,
        noteLabel = null,
        noteBox = null,
      ),
    )
  }

  fun runCtrlZ() {
    val reqEpoch = sessionEpoch
    val reqHost = _ui.value.host
    viewModelScope.launch(Dispatchers.IO) {
      val steps = WorldGitApi.runCtrlZ(reqHost)
      if (reqEpoch != sessionEpoch || reqHost != _ui.value.host) return@launch
      if (steps != null) {
        _ui.value = _ui.value.copy(
          undoSteps = steps,
          snackbar = "Restore instructions loaded (guide only)",
        )
      } else {
        _ui.value = _ui.value.copy(snackbar = "Restore server unreachable")
      }
    }
  }

  fun toggleImuRecording() {
    if (_ui.value.memoryPaused && !_ui.value.imu.recording) {
      _ui.value = _ui.value.copy(snackbar = "Resume privacy pause before recording IMU")
      return
    }
    val next = !_ui.value.imu.recording
    if (next) {
      imuSessionId = "imu-${System.currentTimeMillis()}"
    } else {
      imuSessionId = "glasses-live"
    }
    _ui.value = _ui.value.copy(
      imu = _ui.value.imu.copy(
        recording = next,
        sampleCount = if (next) 0 else _ui.value.imu.sampleCount,
        statusNote = if (next) {
          "Sensor 60Hz local; throttled upload (~4-10Hz) to $imuSessionId"
        } else {
          "Sensor 60Hz local; throttled upload (~4Hz) to session"
        },
      ),
    )
  }

  fun updateCalibration(newCal: HudCalibration) {
    prefs.edit()
      .putFloat("hud_size", newCal.size)
      .putFloat("hud_x", newCal.horizontal)
      .putFloat("hud_y", newCal.vertical)
      .putFloat("hud_roll", newCal.roll)
      .apply()
    val wasGuide = _ui.value.calibration.showGuide
    _ui.value = _ui.value.copy(calibration = newCal)
    if (wasGuide && !newCal.showGuide) {
      viewModelScope.launch(Dispatchers.IO) { WorldGitApi.saveCalibration(_ui.value.host, newCal) }
    }
  }

  fun saveCalibrationNow() {
    val cal = _ui.value.calibration.copy(showGuide = false)
    updateCalibration(cal)
    viewModelScope.launch(Dispatchers.IO) {
      WorldGitApi.saveCalibration(_ui.value.host, cal)
    }
  }

  fun toggleTts() {
    val next = !_ui.value.ttsEnabled
    if (!next) tts?.stop()
    _ui.value = _ui.value.copy(ttsEnabled = next)
  }

  fun togglePrivacy() {
    val next = !_ui.value.memoryPaused
    sessionEpoch += 1
    val reqEpoch = sessionEpoch
    val reqHost = _ui.value.host
    if (next) {
      if (_ui.value.speech.active) cancelSpeech()
      _ui.value = _ui.value.copy(
        memoryPaused = true,
        cameraPreview = null,
        yoloState = YoloState(status = "Privacy paused"),
        projectedYoloState = YoloState(status = "Privacy paused"),
        imu = _ui.value.imu.copy(
          recording = false,
          statusNote = "IMU uploads paused during privacy pause",
        ),
        snackbar = "Paused locally… confirming with server",
      )
    } else {
      _ui.value = _ui.value.copy(
        memoryPaused = false,
        yoloState = YoloState(status = "Point glasses at objects to scan"),
        imu = _ui.value.imu.copy(
          statusNote = "Sensor 60Hz local; throttled upload (~4Hz) to session",
        ),
        snackbar = "Resumed locally… confirming with server",
      )
    }
    viewModelScope.launch(Dispatchers.IO) {
      val res = WorldGitApi.togglePrivacy(reqHost, next)
      if (reqEpoch != sessionEpoch || reqHost != _ui.value.host) return@launch
      val msg = when {
        !res.visionConfirmed && next -> "Paused locally; server confirmation unavailable"
        !res.visionConfirmed && !next -> "Resumed locally; server confirmation unavailable"
        !res.studioConfirmed && next -> "Paused on Vision; Studio confirmation unavailable"
        !res.studioConfirmed && !next -> "Resumed on Vision; Studio confirmation unavailable"
        next -> "Privacy paused (confirmed)"
        else -> "Privacy resumed (confirmed)"
      }
      _ui.value = _ui.value.copy(snackbar = msg)
    }
  }

  fun selectNextDetectedObject() {
    val cur = _ui.value
    val items = cur.projectedYoloState.items.ifEmpty { cur.yoloState.items }
    if (items.isEmpty()) return
    val currentIdx = cur.cursor.selectedId?.let { sel -> items.indexOfFirst { it.id == sel } } ?: -1
    val nextItem = items[(currentIdx + 1) % items.size]
    manualSelectionHoldUntilMs = System.currentTimeMillis() + 6000L
    headCursor.updateHoverSelection(nextItem.id)
    _ui.value = cur.copy(cursor = headCursor.state.copy(selectedId = nextItem.id))
  }

  fun clearSavedNotes() {
    val reqEpoch = sessionEpoch
    val reqHost = _ui.value.host
    _ui.value = _ui.value.copy(
      snackbar = "Clearing session memory & pins…",
    )
    viewModelScope.launch(Dispatchers.IO) {
      val res = WorldGitApi.clearNotes(reqHost)
      if (reqEpoch != sessionEpoch || reqHost != _ui.value.host) return@launch
      if (res.visionCleared) {
        sessionEpoch += 1
        activeTargetId = null
        activeTargetLabel = ""
        activeTargetLocalized = false
        activeTargetStale = false
        val msg = if (res.studioCleared) {
          "Memory & Studio session cleared"
        } else {
          "Memory cleared; Studio deletion pending"
        }
        _ui.value = _ui.value.copy(
          savedNotes = emptyList(),
          noteSaveStatus = null,
          pendingRetryNote = null,
          speech = _ui.value.speech.copy(notesByObjectId = emptyMap()),
          compass = _ui.value.compass.copy(
            targetLabel = "",
            hasNavigation = false,
            localizationStatus = "NO_TARGET",
            arrowGlyph = "",
            turnInstruction = "Ask about an item",
            aligned = false,
            memoryContext = null,
            ambiguityNote = null,
          ),
          snackbar = msg,
        )
      } else {
        val msg = if (res.studioCleared) {
          "Studio cleared; Vision clear failed — retry"
        } else {
          "Clear failed; server confirmation unavailable"
        }
        _ui.value = _ui.value.copy(snackbar = msg)
      }
    }
  }

  fun clearSnackbar() {
    _ui.value = _ui.value.copy(snackbar = null)
  }

  private fun receiveGlassesMotion(rot: FloatArray?, gyro: FloatArray?, timestampNs: Long) {
    rot?.let {
      glassesMotion.orientation(it, timestampNs)
      val (yaw, pitch, roll) = CompassMath.extractEulerDeg(it)
      currentYawDeg = yaw
      currentPitchDeg = pitch
      currentRollDeg = roll
      sensorSource = "GLASSES"
      if (_ui.value.mode == HudMode.PINS) {
        headCursor.updatePose(it)
      }
      refreshCompassState()
      val nowMs = System.currentTimeMillis()
      val reqHost = _ui.value.host
      if (!_ui.value.memoryPaused &&
        (_ui.value.imu.recording || nowMs - lastImuPushMs > 250L) &&
        nowMs - lastImuPushMs >= 100L
      ) {
        lastImuPushMs = nowMs
        if (_ui.value.imu.recording) {
          _ui.value = _ui.value.copy(imu = _ui.value.imu.copy(sampleCount = _ui.value.imu.sampleCount + 1))
        }
        val sid = imuSessionId
        viewModelScope.launch(Dispatchers.IO) {
          WorldGitApi.sendImuSample(reqHost, yaw, pitch, roll, sessionId = sid)
        }
      }
    }
    gyro?.let { glassesMotion.angularVelocity(it[0], it[1], it[2], timestampNs) }
  }

  private fun refreshCompassState() {
    val targetObj = if (activeTargetId != null || activeTargetLabel.isNotBlank()) {
      spatialObjects.firstOrNull {
        (activeTargetId != null && it.id == activeTargetId) ||
          (activeTargetLabel.isNotBlank() && it.label.equals(activeTargetLabel, ignoreCase = true))
      }
    } else null

    val hasLocalizedNav = targetObj != null && activeTargetLocalized && !activeTargetStale && wearerLocalized
    val (angle, glyph, turn, aligned) = if (hasLocalizedNav) {
      CompassMath.relativeTargetNav(currentYawDeg, wearerPos, targetObj)
    } else {
      val fallbackTurn = when {
        activeTargetLabel.isBlank() -> "Ask about an item"
        activeTargetStale -> "Last seen (stale location)"
        else -> "Last seen (no 3D direction)"
      }
      CompassMath.Tuple4(0f, "", fallbackTurn, false)
    }
    val locStatus = when {
      hasLocalizedNav -> "LOCALIZED"
      activeTargetLabel.isBlank() -> "NO_TARGET"
      activeTargetStale -> "STALE"
      else -> "UNLOCALIZED_2D"
    }

    val prev = _ui.value.compass
    val next = prev.copy(
      headingDeg = currentYawDeg.roundToInt().toFloat(),
      cardinal = CompassMath.cardinalFor(currentYawDeg),
      pitchDeg = currentPitchDeg.roundToInt().toFloat(),
      rollDeg = currentRollDeg.roundToInt().toFloat(),
      targetLabel = targetObj?.label ?: activeTargetLabel,
      hasNavigation = hasLocalizedNav,
      localizationStatus = locStatus,
      arrowAngleDeg = angle.roundToInt().toFloat(),
      arrowGlyph = glyph,
      turnInstruction = turn,
      aligned = aligned,
      sensorSource = sensorSource,
    )
    if (next != prev) {
      _ui.value = _ui.value.copy(compass = next)
    }
  }

  private fun receiveGlassesVideoFrame(i420: ByteArray, width: Int, height: Int, receivedAtNs: Long) {
    if (_ui.value.memoryPaused) return
    if (!frameBusy.compareAndSet(false, true)) return
    visionHandler.post {
      try {
        if (_ui.value.memoryPaused) return@post
        val argb = GlassesFramePixels.i420ToArgb(i420, width, height)
        val bitmap = Bitmap.createBitmap(argb, width, height, Bitmap.Config.ARGB_8888)
        val pose = glassesMotion.predict(receivedAtNs) ?: phoneMotion.predict(receivedAtNs)
        val cal = _ui.value.calibration

        val smallW = minOf(160, maxOf(16, (width * minOf(1f, 160f / maxOf(width, height))).toInt()))
        val smallH = minOf(160, maxOf(16, (height * minOf(1f, 160f / maxOf(width, height))).toInt()))
        val small = Bitmap.createScaledBitmap(bitmap, smallW, smallH, true)
        val pixels = IntArray(smallW * smallH)
        small.getPixels(pixels, 0, smallW, 0, 0, smallW, smallH)
        if (small !== bitmap) small.recycle()

        val gray = GrayFrame(
          smallW,
          smallH,
          IntArray(pixels.size) { idx ->
            val p = pixels[idx]
            (77 * ((p shr 16) and 255) + 150 * ((p shr 8) and 255) + 29 * (p and 255)) shr 8
          },
        )
        val focal = kotlin.math.hypot(width.toDouble(), height.toDouble()) / 2.0 / cal.size.coerceIn(0.2f, 3f)
        val tracked = localTracker.frame(gray, pose, receivedAtNs / 1e9, width, height, focal / width, focal / height)
        val stateNow = YoloState(
          status = if (tracked.isEmpty()) "Point glasses at objects to scan" else "${tracked.size} objects locked",
          items = tracked,
          width = width,
          height = height,
        )
        hudPredictor.observe(stateNow, pose, receivedAtNs / 1e9, cal)
        _ui.value = _ui.value.copy(cameraPreview = bitmap, yoloState = stateNow)

        // Upload frame to YOLO-World server (:8765/api/frame) every ~200ms
        if (_ui.value.mode == HudMode.PINS &&
          receivedAtNs >= nextUploadAtNs &&
          networkBusy.compareAndSet(false, true)
        ) {
          nextUploadAtNs = receivedAtNs + 200_000_000L
          val baos = ByteArrayOutputStream()
          bitmap.compress(Bitmap.CompressFormat.JPEG, 72, baos)
          val jpeg = baos.toByteArray()
          val host = _ui.value.host
          val reqEpoch = sessionEpoch
          if (visionAttemptStartMs == 0L) {
            visionAttemptStartMs = System.currentTimeMillis()
          }
          viewModelScope.launch(Dispatchers.IO) {
            try {
              val res = WorldGitApi.postFrame(host, jpeg, pose)
              if (reqEpoch != sessionEpoch || host != _ui.value.host || _ui.value.memoryPaused) return@launch
              lastVisionOkMs = System.currentTimeMillis()
              visionConsecutiveFailures = 0
              visionHandler.post {
                localTracker.correct(res.items, receivedAtNs / 1e9, SystemClock.elapsedRealtimeNanos() / 1e9, res.width, res.height)
              }
              val confidentNoteItems = res.items.filter { !it.note.isNullOrBlank() && !it.noteUncertain }
              if (confidentNoteItems.isNotEmpty()) {
                val cur = _ui.value.speech
                val nextIds = cur.notesByObjectId + confidentNoteItems.associate { it.id to it.note!! }
                _ui.value = _ui.value.copy(speech = cur.copy(notesByObjectId = nextIds))
              }
            } catch (e: Exception) {
              if (reqEpoch != sessionEpoch || host != _ui.value.host) return@launch
              visionConsecutiveFailures += 1
              Log.w(TAG, "Frame upload skipped: ${e.message}")
              val nowMs = System.currentTimeMillis()
              val offlineLongEnough = (lastVisionOkMs > 0L && nowMs - lastVisionOkMs > 2500L) ||
                (lastVisionOkMs == 0L && visionAttemptStartMs > 0L && nowMs - visionAttemptStartMs > 2500L) ||
                visionConsecutiveFailures >= 3
              if (offlineLongEnough) {
                val msg = "Vision server offline — reconnecting"
                _ui.value = _ui.value.copy(
                  yoloState = YoloState(status = msg),
                  projectedYoloState = YoloState(status = msg),
                  deviceStatus = msg,
                )
              }
            } finally {
              networkBusy.set(false)
            }
          }
        }
      } catch (e: Exception) {
        Log.e(TAG, "Video frame error", e)
      } finally {
        frameBusy.set(false)
      }
    }
  }

  private fun updateProjectedStateAndCursor(): YoloState {
    val cur = _ui.value
    val predicted = if (cur.yoloState.items.isNotEmpty()) {
      val pose = glassesMotion.predict(SystemClock.elapsedRealtimeNanos() + 15_000_000L)
      hudPredictor.sample(pose, SystemClock.elapsedRealtimeNanos() / 1e9, cur.calibration)
    } else cur.yoloState

    val projected = if (cur.cameraStreaming) {
      HudProjection(predicted.width, predicted.height, cur.calibration).state(predicted)
    } else {
      predicted
    }

    if (cur.mode == HudMode.PINS && !cur.speech.active && !cur.memoryPaused) {
      val hovered = WorldGitHudRenderer.hoveredObjectId(projected, headCursor.state, cur.speech)
      if (hovered != null || System.currentTimeMillis() > manualSelectionHoldUntilMs) {
        headCursor.updateHoverSelection(hovered)
      }
    }

    if (cur.projectedYoloState != projected || cur.cursor != headCursor.state) {
      _ui.value = _ui.value.copy(projectedYoloState = projected, cursor = headCursor.state)
    }
    return projected
  }

  private fun buildGlassesHudPacket(): GlassesHudPacket {
    val projected = updateProjectedStateAndCursor()
    val cur = _ui.value
    fun onMain(block: () -> Unit) {
      viewModelScope.launch(Dispatchers.Main) {
        block()
        wearablesManager.requestImmediateHudRefresh()
      }
    }

    return when (cur.mode) {
      HudMode.ASK -> {
        val c = cur.compass
        val s = cur.speech
        val dialStep = (c.arrowAngleDeg / 15f).roundToInt()
        val bodyText = when {
          s.active && s.finishing -> "Checking memory & Studio..."
          s.active -> s.text.ifBlank { "Listening... tap Stop to ask" }.take(95)
          else -> c.answerText.ifBlank { "Tap Ask to find an item." }.take(110)
        }
        val sig = "ASK:${c.targetLabel}:${c.hasNavigation}:${c.turnInstruction}:$dialStep:$bodyText:${s.active}:${s.finishing}:${cur.memoryPaused}"
        GlassesHudPacket(signature = sig, minIntervalMs = if (s.active) 250L else 650L) {
          flexBox(direction = Direction.COLUMN, gap = 10, padding = 16) {
            flexBox(direction = Direction.ROW, gap = 8, crossAlignment = Alignment.CENTER) {
              icon(IconName.COMPASS_NORTH_UP_RED, style = IconStyle.FILLED)
              flexBox(direction = Direction.COLUMN, flexGrow = 1f) {
                text("WorldGit • Ctrl+F", style = TextStyle.HEADING)
              }
              flexBox(
                padding = 8,
                background = FlexBoxBackground.CARD,
                onClick = { onMain { setMode(HudMode.SCAN) } },
              ) {
                text("Scan", style = TextStyle.META, color = TextColor.SECONDARY)
              }
            }

            flexBox(
              direction = Direction.ROW,
              gap = 14,
              padding = 16,
              background = FlexBoxBackground.CARD,
              crossAlignment = Alignment.CENTER,
              flexGrow = 1f,
            ) {
              if (c.hasNavigation) {
                flexBox(direction = Direction.COLUMN, flexGrow = 1f) {
                  image(
                    bitmap = WorldGitHudRenderer.renderCompassDialBitmap(c, sizePx = 220),
                    sizePreset = ImageSize.FILL,
                  )
                }
              }
              flexBox(direction = Direction.COLUMN, gap = 6, flexGrow = 2f) {
                text(
                  "${(c.targetLabel.ifBlank { "MEMORY" }).uppercase().take(18)} • ${c.cardinal} ${c.headingDeg.roundToInt()}°",
                  style = TextStyle.META,
                  color = TextColor.SECONDARY,
                )
                text(
                  if (c.hasNavigation) "${c.arrowGlyph}  ${c.turnInstruction}" else c.turnInstruction,
                  style = TextStyle.HEADING,
                )
                text(bodyText, style = TextStyle.BODY)
              }
            }

            buttonGroup(alignment = ButtonGroupAlignment.CENTER) {
              button(
                label = if (s.active) "Stop" else "Ask",
                style = ButtonStyle.PRIMARY,
                iconName = if (s.active) IconName.CHECKMARK else IconName.SPEECH_BUBBLE,
                actionRole = ActionRole.PRIMARY,
                onClick = { onMain { handleHudPrimaryAction() } },
              )
              button(
                label = "Pins",
                style = ButtonStyle.SECONDARY,
                iconName = IconName.FOUR_CORNER_FRAME,
                onClick = { onMain { setMode(HudMode.PINS) } },
              )
              button(
                label = "Undo",
                style = ButtonStyle.OUTLINE,
                iconName = IconName.ARROW_U_LEFT,
                onClick = { onMain { setMode(HudMode.UNDO) } },
              )
            }
          }
        }
      }

      HudMode.PINS -> {
        if (cur.memoryPaused) {
          return GlassesHudPacket(signature = "PINS:PAUSED", minIntervalMs = 500L) {
            flexBox(direction = Direction.COLUMN, gap = 10, padding = 16) {
              flexBox(direction = Direction.ROW, gap = 8, crossAlignment = Alignment.CENTER) {
                icon(IconName.EYE, style = IconStyle.OUTLINE)
                text("WorldGit • Privacy Paused", style = TextStyle.HEADING)
              }
              flexBox(direction = Direction.COLUMN, gap = 8, padding = 18, background = FlexBoxBackground.CARD, flexGrow = 1f) {
                text("Camera frame uploads and object memory are paused.", style = TextStyle.BODY)
              }
              buttonGroup(alignment = ButtonGroupAlignment.CENTER) {
                button(
                  label = "Resume",
                  style = ButtonStyle.PRIMARY,
                  iconName = IconName.EYE,
                  actionRole = ActionRole.PRIMARY,
                  onClick = { onMain { togglePrivacy() } },
                )
                button(
                  label = "Ask",
                  style = ButtonStyle.SECONDARY,
                  iconName = IconName.COMPASS_NORTH_UP_RED,
                  onClick = { onMain { setMode(HudMode.ASK) } },
                )
              }
            }
          }
        }
        val s = cur.speech
        val hoveredItem = cur.cursor.selectedId?.let { id ->
          projected.items.firstOrNull { it.id == id } ?: cur.yoloState.items.firstOrNull { it.id == id }
        }
        val boxDigest = projected.items.take(4).joinToString(";") { item ->
          val cx = ((item.box[0] + item.box[2]) / 40.0).roundToInt()
          val cy = ((item.box[1] + item.box[3]) / 40.0).roundToInt()
          "${item.id}:$cx,$cy:${s.notesByObjectId[item.id] ?: item.note ?: ""}"
        }
        val sig = "PINS:${cur.cursor.selectedId}:${(cur.cursor.x * 18f).roundToInt()}:${(cur.cursor.y * 18f).roundToInt()}:$boxDigest:${cur.savedNotes.size}:${s.active}:${s.finishing}:${s.text}:${cur.calibration.showGuide}"
        val intervalMs = if (s.active && !s.finishing) 600L else 320L
        GlassesHudPacket(signature = sig, minIntervalMs = intervalMs) {
          flexBox(direction = Direction.COLUMN, gap = 6, padding = 10) {
            flexBox(direction = Direction.ROW, gap = 8, crossAlignment = Alignment.CENTER) {
              icon(IconName.FOUR_CORNER_FRAME, style = IconStyle.FILLED)
              flexBox(direction = Direction.COLUMN, flexGrow = 1f) {
                text("WorldGit • Pins", style = TextStyle.HEADING)
              }
              text(
                "${projected.items.size} obj • ${cur.savedNotes.size} pins",
                style = TextStyle.META,
                color = TextColor.SECONDARY,
              )
            }

            image(
              bitmap = WorldGitHudRenderer.renderPinsGlassesBitmap(
                projectedYolo = projected,
                cursor = cur.cursor,
                speech = s,
                calibration = cur.calibration,
              ),
              sizePreset = ImageSize.FILL,
              flexGrow = 1f,
              flexShrink = 0f,
            )

            buttonGroup(alignment = ButtonGroupAlignment.CENTER) {
              if (s.active) {
                button(
                  label = if (s.finishing) "Saving" else "Save Note",
                  style = ButtonStyle.PRIMARY,
                  iconName = IconName.CHECKMARK,
                  actionRole = ActionRole.PRIMARY,
                  onClick = { onMain { handleHudPrimaryAction() } },
                )
                button(
                  label = "Cancel",
                  style = ButtonStyle.OUTLINE,
                  iconName = IconName.X,
                  onClick = { onMain { handleHudBack() } },
                )
                button(
                  label = "Ask",
                  style = ButtonStyle.SECONDARY,
                  iconName = IconName.COMPASS_NORTH_UP_RED,
                  onClick = { onMain { setMode(HudMode.ASK) } },
                )
              } else {
                val primaryLabel = if (hoveredItem != null) "Note: ${hoveredItem.label.take(9)}" else "Center"
                val primaryIcon = if (hoveredItem != null) IconName.PENCIL_SQUARE else IconName.FOUR_CORNER_FRAME
                button(
                  label = primaryLabel,
                  style = ButtonStyle.PRIMARY,
                  iconName = primaryIcon,
                  actionRole = ActionRole.PRIMARY,
                  onClick = { onMain { handleHudPrimaryAction() } },
                )
                if (projected.items.isNotEmpty()) {
                  button(
                    label = "Next",
                    style = ButtonStyle.SECONDARY,
                    iconName = IconName.ARROW_RIGHT,
                    onClick = { onMain { selectNextDetectedObject() } },
                  )
                }
                button(
                  label = "Ask",
                  style = ButtonStyle.SECONDARY,
                  iconName = IconName.COMPASS_NORTH_UP_RED,
                  onClick = { onMain { setMode(HudMode.ASK) } },
                )
                if (projected.items.isEmpty()) {
                  button(
                    label = "Scan",
                    style = ButtonStyle.OUTLINE,
                    iconName = IconName.EYE,
                    onClick = { onMain { setMode(HudMode.SCAN) } },
                  )
                }
              }
            }
          }
        }
      }

      HudMode.UNDO -> {
        val steps = cur.undoSteps.ifEmpty { cur.savedNotes.takeLast(3).map { "Pinned ${it.label}: ${it.text}" } }
        val stepText = if (steps.isEmpty()) "No recent changes recorded yet." else steps.take(3).mapIndexed { idx, txt -> "${idx + 1}. ${txt.take(42)}" }.joinToString("\n")
        val sig = "UNDO:$stepText"
        GlassesHudPacket(signature = sig, minIntervalMs = 500L) {
          flexBox(direction = Direction.COLUMN, gap = 10, padding = 16) {
            flexBox(direction = Direction.ROW, gap = 8, crossAlignment = Alignment.CENTER) {
              icon(IconName.ARROW_U_LEFT, style = IconStyle.FILLED)
              text("WorldGit • Ctrl+Z", style = TextStyle.HEADING)
            }
            flexBox(direction = Direction.COLUMN, gap = 8, padding = 18, background = FlexBoxBackground.CARD, flexGrow = 1f) {
              text("Restore Instructions", style = TextStyle.META, color = TextColor.SECONDARY)
              text(stepText, style = TextStyle.BODY)
            }
            buttonGroup(alignment = ButtonGroupAlignment.CENTER) {
              button(
                label = "Restore Guide",
                style = ButtonStyle.PRIMARY,
                iconName = IconName.ARROW_U_LEFT,
                actionRole = ActionRole.PRIMARY,
                onClick = { onMain { runCtrlZ() } },
              )
              button(
                label = "Ask",
                style = ButtonStyle.SECONDARY,
                iconName = IconName.COMPASS_NORTH_UP_RED,
                onClick = { onMain { setMode(HudMode.ASK) } },
              )
              button(
                label = "Pins",
                style = ButtonStyle.OUTLINE,
                iconName = IconName.FOUR_CORNER_FRAME,
                onClick = { onMain { setMode(HudMode.PINS) } },
              )
            }
          }
        }
      }

      HudMode.SCAN -> {
        val c = cur.compass
        val imu = cur.imu
        val sig = "SCAN:${imu.recording}:${imu.sampleCount}:${c.cardinal}:${(c.headingDeg / 15f).roundToInt()}:${c.pitchDeg.roundToInt()}"
        GlassesHudPacket(signature = sig, minIntervalMs = 600L) {
          flexBox(direction = Direction.COLUMN, gap = 10, padding = 16) {
            flexBox(direction = Direction.ROW, gap = 8, crossAlignment = Alignment.CENTER) {
              icon(IconName.EYE, style = IconStyle.FILLED)
              text("WorldGit • Scan", style = TextStyle.HEADING)
            }
            flexBox(direction = Direction.COLUMN, gap = 8, padding = 18, background = FlexBoxBackground.CARD, flexGrow = 1f) {
              text(
                if (imu.recording) "REC IMU • ${imu.sampleCount} pts" else "9-Axis Glasses Telemetry • LIVE",
                style = TextStyle.HEADING,
              )
              text(
                "Heading ${c.cardinal} ${c.headingDeg.roundToInt()}°   •   Pitch ${c.pitchDeg.roundToInt()}°",
                style = TextStyle.BODY,
              )
              text(imu.statusNote, style = TextStyle.META, color = TextColor.SECONDARY)
            }
            buttonGroup(alignment = ButtonGroupAlignment.CENTER) {
              button(
                label = if (imu.recording) "Stop" else "Scan",
                style = ButtonStyle.PRIMARY,
                iconName = if (imu.recording) IconName.CHECKMARK else IconName.EYE,
                actionRole = ActionRole.PRIMARY,
                onClick = { onMain { toggleImuRecording() } },
              )
              button(
                label = "Ask",
                style = ButtonStyle.SECONDARY,
                iconName = IconName.COMPASS_NORTH_UP_RED,
                onClick = { onMain { setMode(HudMode.ASK) } },
              )
              button(
                label = "Pins",
                style = ButtonStyle.OUTLINE,
                iconName = IconName.FOUR_CORNER_FRAME,
                onClick = { onMain { setMode(HudMode.PINS) } },
              )
            }
          }
        }
      }
    }
  }

  private fun speakAloud(key: String, text: String) {
    if (!_ui.value.ttsEnabled || !ttsReady || text.isBlank()) return
    tts?.speak(text, TextToSpeech.QUEUE_FLUSH, null, key)
  }

  override fun onCleared() {
    super.onCleared()
    sensors.unregisterListener(phoneSensorListener)
    wearablesManager.stopSession()
    speechJob?.cancel()
    tts?.stop()
    tts?.shutdown()
    cameraThread.quitSafely()
  }
}
