package com.meta.wearable.dat.externalsampleapps.displayaccess

import android.app.Application
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.util.Log
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import java.io.ByteArrayOutputStream
import java.net.HttpURLConnection
import java.net.URI
import java.util.concurrent.atomic.AtomicInteger
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import org.json.JSONObject

data class LiveSpeechState(val active: Boolean = false, val text: String = "", val error: String? = null, val noteId: String? = null, val notes: Map<String, String> = emptyMap(), val finishing: Boolean = false)

class LiveTranscriptionViewModel(application: Application) : AndroidViewModel(application) {
  private val mutableState = MutableStateFlow(LiveSpeechState())
  val state = mutableState.asStateFlow()
  private val generation = AtomicInteger(0)
  @Volatile private var stopRequested = false
  @Volatile private var microphone: AudioRecord? = null
  @Volatile private var connection: HttpURLConnection? = null
  @Volatile private var externalCapture = false
  private val externalPcm = Channel<ShortArray>(64)
  private var capture: Job? = null
  private var network: Job? = null

  fun unavailable(message: String) { mutableState.value = mutableState.value.copy(active = false, noteId = null, error = message) }

  fun start(address: String, noteId: String? = null, externalAudio: Boolean = false) {
    if (mutableState.value.active) return
    val current = generation.incrementAndGet()
    stopRequested = false
    externalCapture = externalAudio
    while (externalPcm.tryReceive().isSuccess) { /* discard audio from an earlier recording */ }
    mutableState.value = LiveSpeechState(active = true, noteId = noteId, notes = mutableState.value.notes)
    capture = viewModelScope.launch(Dispatchers.IO) {
      var recorder: AudioRecord? = null
      var error: String? = null
      val pcm = ByteArrayOutputStream()
      val endpoint = SpeechEndpoint(manualStop = noteId != null)
      try {
        if (!externalAudio) {
          val minimum = AudioRecord.getMinBufferSize(16000, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
          require(minimum > 0) { "16-kHz microphone input is unavailable" }
          recorder = AudioRecord.Builder()
            .setAudioSource(MediaRecorder.AudioSource.VOICE_RECOGNITION)
            .setAudioFormat(AudioFormat.Builder().setSampleRate(16000)
              .setChannelMask(AudioFormat.CHANNEL_IN_MONO).setEncoding(AudioFormat.ENCODING_PCM_16BIT).build())
            .setBufferSizeInBytes(maxOf(minimum, 12800)).build()
          require(recorder.state == AudioRecord.STATE_INITIALIZED) { "Microphone is unavailable" }
          microphone = recorder
          recorder.startRecording()
          Log.i("WorldGitSpeech", "Live microphone started")
        }
        var audio = ShortArray(1600)
        var lastExternalAudio = android.os.SystemClock.elapsedRealtime()
        var nextPartial = 16000
        var finished = false
        while (!stopRequested && !finished && generation.get() == current) {
          val count = if (externalAudio) {
            val packet = withTimeoutOrNull(200) { externalPcm.receive() }
            if (packet == null) {
              check(android.os.SystemClock.elapsedRealtime() - lastExternalAudio < 5000) { "No audio received from glasses microphone" }
              continue
            }
            lastExternalAudio = android.os.SystemClock.elapsedRealtime()
            audio = packet
            packet.size
          } else recorder!!.read(audio, 0, audio.size, AudioRecord.READ_BLOCKING)
          check(count > 0) { "Microphone read failed" }
          val bytes = ByteArray(count * 2)
          for (index in 0 until count) {
            bytes[index * 2] = (audio[index].toInt() and 255).toByte()
            bytes[index * 2 + 1] = (audio[index].toInt() shr 8).toByte()
          }
          pcm.write(bytes)
          finished = endpoint.add(audio, count)
          if (endpoint.heardSpeech && pcm.size() >= nextPartial && network?.isActive != true) {
            nextPartial = pcm.size() + 16000 // Latest utterance snapshot every ~500 ms.
            val snapshot = pcm.toByteArray()
            network = viewModelScope.launch(Dispatchers.IO) {
              try {
                val text = recognize(address, snapshot)
                if (generation.get() == current) mutableState.value = mutableState.value.copy(text = text)
              } catch (failure: Exception) {
                if (failure is CancellationException) throw failure
                error = failure.message ?: "Parakeet GPU transcription failed"
                stopRequested = true
              }
            }
          }
        }
      } catch (failure: Exception) {
        if (failure is CancellationException) throw failure
        error = failure.message ?: "Cannot open microphone"
      } finally {
        runCatching { recorder?.stop() }
        recorder?.release()
        microphone = null
      }
      externalCapture = false
      network?.join()
      if (generation.get() != current) return@launch
      if (error == null && endpoint.heardSpeech && pcm.size() >= 3200) {
        try {
          val text = recognize(address, pcm.toByteArray())
          if (generation.get() != current) return@launch
          mutableState.value = mutableState.value.copy(text = text)
          if (noteId == null) delay(700) // Notes remain visible after capture ends.
        } catch (failure: Exception) {
          if (failure is CancellationException) throw failure
          error = failure.message ?: "Final transcription failed"
        }
      }
      if (generation.get() == current) {
        val result = mutableState.value
        val notes = if (noteId != null && result.text.isNotBlank()) result.notes + (noteId to result.text) else result.notes
        mutableState.value = LiveSpeechState(error = error ?: result.error, notes = notes)
      }
      Log.i("WorldGitSpeech", "Live speech finished; heardSpeech=${endpoint.heardSpeech}; error=$error")
    }
  }

  private fun recognize(address: String, pcm: ByteArray): String {
    // Keep microphone capture independent of network work, and respect the
    // existing server's 30-second request limit even for long manual notes.
    val segmentBytes = 16000 * 2 * 25
    return (pcm.indices step segmentBytes).map { offset ->
      val end = minOf(offset + segmentBytes, pcm.size)
      val segment = pcm.copyOfRange(offset, end)
      // Pad a very short final segment to the server's 100-ms minimum.
      recognizeChunk(address, if (segment.size < 3200) segment.copyOf(3200) else segment)
    }.filter { it.isNotBlank() }.joinToString(" ")
  }

  private fun recognizeChunk(address: String, pcm: ByteArray): String {
    val active = URI("${address.trimEnd('/')}/api/transcribe").toURL().openConnection() as HttpURLConnection
    connection = active
    try {
      active.requestMethod = "POST"
      active.connectTimeout = 3000
      active.readTimeout = 12000
      active.doOutput = true
      active.setRequestProperty("Content-Type", "application/octet-stream")
      active.setRequestProperty("X-Sample-Rate", "16000")
      active.setFixedLengthStreamingMode(pcm.size)
      active.outputStream.use { it.write(pcm) }
      if (active.responseCode != 200) {
        val message = runCatching { JSONObject(active.errorStream.bufferedReader().use { it.readText() }).optString("error") }.getOrNull()
        error(message?.takeIf { it.isNotBlank() } ?: "Parakeet GPU server unavailable (${active.responseCode})")
      }
      val json = JSONObject(active.inputStream.bufferedReader().use { it.readText() })
      check(json.optString("model") == "nvidia/parakeet-unified-en-0.6b" && json.optString("device") == "cuda:0") {
        "Expected NVIDIA Parakeet Unified on the GPU"
      }
      return json.getString("text")
    } finally {
      active.disconnect()
      connection = null
    }
  }

  fun receiveGlassesAudio(bytes: ByteArray) {
    if (!externalCapture || !mutableState.value.active || stopRequested || bytes.size % 2 != 0) return
    val samples = ShortArray(bytes.size / 2) { index ->
      ((bytes[index*2].toInt() and 255) or (bytes[index*2+1].toInt() shl 8)).toShort()
    }
    if (samples.isNotEmpty() && externalPcm.trySend(samples).isFailure) {
      stopRequested = true
      mutableState.value = mutableState.value.copy(error = "Glasses audio arrived faster than it could be recorded")
    }
  }

  fun finish() { stopRequested = true; mutableState.value = mutableState.value.copy(finishing = true) }

  fun cancel() {
    generation.incrementAndGet()
    externalCapture = false
    stopRequested = true
    runCatching { microphone?.stop() }
    connection?.disconnect()
    capture?.cancel()
    network?.cancel()
    mutableState.value = LiveSpeechState(notes = mutableState.value.notes)
  }

  override fun onCleared() { cancel(); super.onCleared() }
}
