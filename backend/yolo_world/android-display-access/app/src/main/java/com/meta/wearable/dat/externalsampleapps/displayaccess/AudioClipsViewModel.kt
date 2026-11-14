package com.meta.wearable.dat.externalsampleapps.displayaccess

import android.app.Application
import android.media.MediaMetadataRetriever
import android.media.MediaRecorder
import android.util.Log
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import java.net.HttpURLConnection
import java.net.URI
import java.util.UUID
import org.json.JSONObject

data class AudioClipState(
  val recording: Boolean = false,
  val uploading: Boolean = false,
  val message: String = "Pinch to record audio",
  val pendingCount: Int = 0,
)

/** The simulator records the phone microphone; completed clips are uploaded to the laptop. */
class AudioClipsViewModel(application: Application) : AndroidViewModel(application) {
  private val pendingDir = File(application.filesDir, "pendingAudioClips").apply { mkdirs() }
  private val mutableState = MutableStateFlow(AudioClipState(pendingCount = pendingFiles().size))
  val state = mutableState.asStateFlow()
  private var recorder: MediaRecorder? = null
  private var currentFile: File? = null
  private var destination = ""

  private fun pendingFiles() = pendingDir.listFiles()?.filter { it.extension == "m4a" }?.sortedBy { it.name }.orEmpty()

  fun permissionDenied() {
    mutableState.value = mutableState.value.copy(message = "Microphone permission is needed. Pinch to try again.")
  }

  fun toggle(address: String) {
    viewModelScope.launch {
      if (mutableState.value.uploading) return@launch
      if (recorder != null) finishRecording() else startRecording(address)
    }
  }

  private fun startRecording(address: String) {
    destination = address
    val file = File(pendingDir, "${UUID.randomUUID()}.m4a")
    val active = MediaRecorder(getApplication())
    try {
      active.setAudioSource(MediaRecorder.AudioSource.MIC)
      active.setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
      active.setAudioEncoder(MediaRecorder.AudioEncoder.AAC)
      active.setAudioChannels(1)
      active.setAudioSamplingRate(48000)
      active.setAudioEncodingBitRate(128000)
      active.setMaxDuration(600_000)
      active.setOutputFile(file.absolutePath)
      active.setOnInfoListener { _, what, _ ->
        if (what == MediaRecorder.MEDIA_RECORDER_INFO_MAX_DURATION_REACHED) finishRecording()
      }
      active.setOnErrorListener { _, _, _ -> finishRecording() }
      active.prepare()
      active.start()
      recorder = active
      currentFile = file
      mutableState.value = AudioClipState(true, message = "Recording - pinch again to stop", pendingCount = pendingFiles().size)
      Log.i("WorldGitAudio", "Recording started: ${file.name}")
    } catch (error: Exception) {
      active.release()
      file.delete()
      mutableState.value = AudioClipState(message = "Cannot record: ${error.message}", pendingCount = pendingFiles().size)
    }
  }

  fun finishRecording() {
    viewModelScope.launch {
      val active = recorder ?: return@launch
      val file = currentFile!!
      recorder = null
      currentFile = null
      try {
        active.stop()
      } catch (error: RuntimeException) {
        file.delete()
        mutableState.value = AudioClipState(message = "Clip too short or microphone unavailable. Pinch to retry.", pendingCount = pendingFiles().size)
        return@launch
      } finally { active.release() }
      val duration = runCatching {
        MediaMetadataRetriever().use { metadata ->
          metadata.setDataSource(file.absolutePath)
          require(metadata.extractMetadata(MediaMetadataRetriever.METADATA_KEY_HAS_AUDIO) == "yes")
          metadata.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION)?.toLong() ?: 0L
        }
      }.getOrDefault(0L)
      if (duration <= 0) {
        file.delete()
        mutableState.value = AudioClipState(message = "Recording was empty. Pinch to retry.", pendingCount = pendingFiles().size)
        return@launch
      }
      Log.i("WorldGitAudio", "Recording stopped: ${file.name}; duration_ms=$duration; bytes=${file.length()}")
      uploadPending(destination)
    }
  }

  fun retryUploads(address: String) {
    if (recorder == null && !mutableState.value.uploading) viewModelScope.launch { uploadPending(address) }
  }

  private suspend fun uploadPending(address: String) {
    mutableState.value = AudioClipState(uploading = true, message = "Saving audio to laptop...", pendingCount = pendingFiles().size)
    try {
      var saved = ""
      for (file in pendingFiles()) {
        saved = withContext(Dispatchers.IO) {
          val connection = URI("${address.trimEnd('/')}/api/audio-clips").toURL().openConnection() as HttpURLConnection
          try {
            connection.requestMethod = "POST"
            connection.connectTimeout = 3000
            connection.readTimeout = 15000
            connection.doOutput = true
            connection.setRequestProperty("Content-Type", "audio/mp4")
            connection.setRequestProperty("X-Clip-Id", file.nameWithoutExtension)
            connection.setFixedLengthStreamingMode(file.length())
            connection.outputStream.use { output -> file.inputStream().use { it.copyTo(output) } }
            val response = JSONObject(connection.inputStream.bufferedReader().use { it.readText() })
            require(response.getString("status") == "saved")
            val relative = response.getString("relative_path")
            Log.i("WorldGitAudio", "Saved to laptop: $relative; bytes=${response.getLong("bytes")}")
            file.delete()
            relative
          } finally { connection.disconnect() }
        }
      }
      mutableState.value = AudioClipState(message = if (saved.isEmpty()) "No pending clips. Pinch to record." else "Saved: $saved")
    } catch (error: Exception) {
      Log.w("WorldGitAudio", "Upload pending: ${error.message}")
      mutableState.value = AudioClipState(message = "Laptop unavailable. Clip kept on phone; use Retry upload.", pendingCount = pendingFiles().size)
    }
  }

  override fun onCleared() {
    // Preserve a finalized pending clip if the process navigates away while recording.
    recorder?.let { active ->
      runCatching { active.stop() }.onFailure { currentFile?.delete() }
      active.release()
    }
    recorder = null
    super.onCleared()
  }
}
