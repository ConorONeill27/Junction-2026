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
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import org.json.JSONObject

data class LiveSpeechState(val active: Boolean = false, val text: String = "", val error: String? = null)

class LiveTranscriptionViewModel(application: Application) : AndroidViewModel(application) {
  private val mutableState = MutableStateFlow(LiveSpeechState())
  val state = mutableState.asStateFlow()
  private val generation = AtomicInteger(0)
  @Volatile private var stopRequested = false
  @Volatile private var microphone: AudioRecord? = null
  @Volatile private var connection: HttpURLConnection? = null
  private var capture: Job? = null
  private var network: Job? = null

  fun unavailable(message: String) { mutableState.value = LiveSpeechState(error = message) }

  fun start(address: String) {
    if (mutableState.value.active) return
    val current = generation.incrementAndGet()
    stopRequested = false
    mutableState.value = LiveSpeechState(active = true)
    capture = viewModelScope.launch(Dispatchers.IO) {
      var recorder: AudioRecord? = null
      var error: String? = null
      val pcm = ByteArrayOutputStream()
      val endpoint = SpeechEndpoint()
      try {
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
        val audio = ShortArray(1600)
        var nextPartial = 16000
        var finished = false
        while (!stopRequested && !finished && generation.get() == current) {
          val count = recorder.read(audio, 0, audio.size, AudioRecord.READ_BLOCKING)
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
                if (generation.get() == current) mutableState.value = LiveSpeechState(active = true, text = text)
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
      network?.join()
      if (generation.get() != current) return@launch
      if (error == null && endpoint.heardSpeech) {
        try {
          val text = recognize(address, pcm.toByteArray())
          if (generation.get() != current) return@launch
          mutableState.value = LiveSpeechState(active = true, text = text)
          delay(700) // Briefly show the final words, then clear both mic and caption.
        } catch (failure: Exception) {
          if (failure is CancellationException) throw failure
          error = failure.message ?: "Final transcription failed"
        }
      }
      if (generation.get() == current) mutableState.value = LiveSpeechState(error = error)
      Log.i("WorldGitSpeech", "Live speech finished; heardSpeech=${endpoint.heardSpeech}; error=$error")
    }
  }

  private fun recognize(address: String, pcm: ByteArray): String {
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

  fun finish() { stopRequested = true }

  fun cancel() {
    generation.incrementAndGet()
    stopRequested = true
    runCatching { microphone?.stop() }
    connection?.disconnect()
    capture?.cancel()
    network?.cancel()
    mutableState.value = LiveSpeechState()
  }

  override fun onCleared() { cancel(); super.onCleared() }
}
