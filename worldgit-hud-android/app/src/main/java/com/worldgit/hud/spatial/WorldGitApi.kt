package com.worldgit.hud.spatial

import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URI
import java.net.URLEncoder

class ApiHttpException(val statusCode: Int, message: String) : Exception(message)

data class WorldStateResult(
  val objects: List<SpatialObject>,
  val wearerPos: Triple<Double, Double, Double>,
  val wearerYaw: Float?,
  val wearerLocalized: Boolean,
  val pins: List<SavedNote>,
  val undoSteps: List<String>,
  val activeTargetId: String?,
)

data class AskResult(
  val found: Boolean,
  val answer: String,
  val targetObject: String?,
  val targetObjectId: String?,
  val objects: List<SpatialObject>,
  val localized: Boolean = false,
  val stale: Boolean = false,
  val ageLabel: String? = null,
  val context: String? = null,
  val ambiguity: String? = null,
  val fromMemory: Boolean = false,
)

data class FrameResult(
  val items: List<YoloItem>,
  val width: Int,
  val height: Int,
  val inferenceMs: Double,
)

enum class TranscriptionStatus {
  OK,
  SILENCE,
  SERVICE_UNAVAILABLE,
}

data class TranscriptionResult(
  val text: String,
  val status: TranscriptionStatus,
  val provider: String = "",
  val audioLeftLocal: Boolean = false,
  val disclosure: String? = null,
  val error: String? = null,
)

data class NoteSaveResult(
  val ok: Boolean,
  val noteId: String?,
  val studioSynced: Boolean,
  val discarded: Boolean = false,
  val error: String? = null,
)

data class PrivacyToggleResult(
  val visionConfirmed: Boolean,
  val studioConfirmed: Boolean,
  val paused: Boolean,
  val error: String? = null,
)

data class SessionClearResult(
  val visionCleared: Boolean,
  val studioCleared: Boolean,
  val error: String? = null,
)

object WorldGitApi {
  @Volatile private var apiToken: String = ""
  @Volatile var visionBaseOverride: String? = null
  @Volatile var studioBaseOverride: String? = null

  fun setApiToken(token: String) {
    apiToken = token.trim()
  }

  private fun openConn(url: String, connectMs: Int, readMs: Int): HttpURLConnection {
    val conn = URI(url).toURL().openConnection() as HttpURLConnection
    conn.connectTimeout = connectMs
    conn.readTimeout = readMs
    val tok = apiToken
    if (tok.isNotBlank()) {
      conn.setRequestProperty("X-WorldGit-Token", tok)
    }
    return conn
  }

  fun visionBase(hostInput: String): String {
    visionBaseOverride?.takeIf { it.isNotBlank() }?.let { return it.trimEnd('/') }
    val clean = hostInput.trim().trimEnd('/')
    if (clean.startsWith("http://") || clean.startsWith("https://")) {
      return runCatching {
        val u = URI(clean)
        "${u.scheme}://${u.host}:8765"
      }.getOrElse { "http://127.0.0.1:8765" }
    }
    val hostOnly = clean.substringBefore(':').ifBlank { "127.0.0.1" }
    return "http://$hostOnly:8765"
  }

  fun studioBase(hostInput: String): String {
    studioBaseOverride?.takeIf { it.isNotBlank() }?.let { return it.trimEnd('/') }
    val clean = hostInput.trim().trimEnd('/')
    if (clean.startsWith("http://") || clean.startsWith("https://")) {
      return runCatching {
        val u = URI(clean)
        val p = if (u.port > 0 && u.port != 8765) ":${u.port}" else ":8000"
        "${u.scheme}://${u.host}$p"
      }.getOrElse { "http://127.0.0.1:8000" }
    }
    val hostOnly = clean.substringBefore(':').ifBlank { "127.0.0.1" }
    return "http://$hostOnly:8000"
  }

  private fun getJson(url: String, timeoutMs: Int = 2500): JSONObject {
    val conn = openConn(url, timeoutMs, timeoutMs)
    try {
      val code = conn.responseCode
      val stream = if (code in 200..299) conn.inputStream else conn.errorStream
      val text = stream?.bufferedReader()?.use { it.readText() } ?: "{}"
      if (code !in 200..299) {
        throw ApiHttpException(code, "HTTP $code from $url: ${text.take(180)}")
      }
      return if (text.isBlank()) JSONObject() else JSONObject(text)
    } finally {
      conn.disconnect()
    }
  }

  private fun postJson(url: String, body: JSONObject, timeoutMs: Int = 6000): JSONObject {
    val bytes = body.toString().toByteArray(Charsets.UTF_8)
    val conn = openConn(url, timeoutMs, timeoutMs)
    conn.requestMethod = "POST"
    conn.doOutput = true
    conn.setRequestProperty("Content-Type", "application/json")
    conn.setFixedLengthStreamingMode(bytes.size)
    try {
      conn.outputStream.use { it.write(bytes) }
      val code = conn.responseCode
      val stream = if (code in 200..299) conn.inputStream else conn.errorStream
      val text = stream?.bufferedReader()?.use { it.readText() } ?: "{}"
      if (code !in 200..299) {
        throw ApiHttpException(code, "HTTP $code from $url: ${text.take(180)}")
      }
      return if (text.isBlank()) JSONObject() else JSONObject(text)
    } finally {
      conn.disconnect()
    }
  }

  fun fetchCalibration(host: String): HudCalibration? = runCatching {
    val j = getJson("${visionBase(host)}/api/calibration", 2000)
    HudCalibration(
      size = j.optDouble("size", 0.8522121).toFloat(),
      horizontal = j.optDouble("horizontal", -512.44965).toFloat(),
      vertical = j.optDouble("vertical", -365.0765).toFloat(),
      roll = j.optDouble("roll", 0.0).toFloat(),
    )
  }.getOrNull()

  fun saveCalibration(host: String, cal: HudCalibration) = runCatching {
    postJson(
      "${visionBase(host)}/api/calibration",
      JSONObject()
        .put("size", cal.size.toDouble())
        .put("horizontal", cal.horizontal.toDouble())
        .put("vertical", cal.vertical.toDouble())
        .put("roll", cal.roll.toDouble()),
      timeoutMs = 2000,
    )
  }

  fun fetchSavedNotes(host: String): Result<List<SavedNote>> = runCatching {
    val j = getJson("${visionBase(host)}/api/notes", 2500)
    val arr = j.optJSONArray("notes") ?: JSONArray()
    (0 until arr.length()).mapNotNull { idx ->
      val o = arr.optJSONObject(idx) ?: return@mapNotNull null
      val text = o.optString("text").trim()
      if (text.isBlank()) return@mapNotNull null
      SavedNote(
        noteId = o.optString("note_id", "note-$idx"),
        objectId = o.optString("object_id", ""),
        label = o.optString("label", "object"),
        text = text,
        author = o.optString("author", "Person A"),
      )
    }
  }

  fun fetchWorldState(host: String): WorldStateResult? = runCatching {
    val j = getJson("${studioBase(host)}/api/state", 2500)
    val objs = mutableListOf<SpatialObject>()
    val oArr = j.optJSONArray("objects") ?: JSONArray()
    for (i in 0 until oArr.length()) {
      val o = oArr.optJSONObject(i) ?: continue
      val pos = o.optJSONArray("position")
      val x = pos?.optDouble(0, 0.0) ?: o.optDouble("x", 0.0)
      val y = pos?.optDouble(1, 0.0) ?: o.optDouble("y", 0.0)
      val z = pos?.optDouble(2, 0.0) ?: o.optDouble("z", 0.0)
      objs += SpatialObject(
        id = o.optString("id", "obj-$i"),
        label = o.optString("label", "object"),
        x = x,
        y = y,
        z = z,
      )
    }
    var wx = 0.0
    var wy = 1.6
    var wz = 0.0
    var wYaw: Float? = null
    var wLocalized = false
    val wearers = j.optJSONArray("wearers")
    if (wearers != null && wearers.length() > 0) {
      var selectedWearer: JSONObject? = null
      for (i in 0 until wearers.length()) {
        val cand = wearers.optJSONObject(i) ?: continue
        val wid = cand.optString("wearer_id", "").ifBlank { cand.optString("id", "") }
        val uid = cand.optString("user_id", "")
        if (wid == "wearer-b" || uid == "usr-conor") {
          selectedWearer = cand
          break
        }
      }
      if (selectedWearer != null) {
        wLocalized = true
        val wp = selectedWearer.optJSONArray("position")
        if (wp != null && wp.length() >= 3) {
          wx = wp.optDouble(0, 0.0)
          wy = wp.optDouble(1, 1.6)
          wz = wp.optDouble(2, 0.0)
        }
        if (selectedWearer.has("yaw")) {
          wYaw = selectedWearer.optDouble("yaw", 0.0).toFloat()
        }
      }
    }
    val pins = mutableListOf<SavedNote>()
    val pArr = j.optJSONArray("pins") ?: JSONArray()
    for (i in 0 until pArr.length()) {
      val p = pArr.optJSONObject(i) ?: continue
      val text = p.optString("text", p.optString("note", "")).trim()
      if (text.isNotBlank()) {
        pins += SavedNote(
          noteId = p.optString("id", "pin-$i"),
          objectId = p.optString("object_id", p.optString("id", "")),
          label = p.optString("label", p.optString("landmark", "pin")),
          text = text,
          author = p.optString("author", "You"),
        )
      }
    }
    val undo = mutableListOf<String>()
    val uArr = j.optJSONArray("undo_steps") ?: JSONArray()
    for (i in 0 until uArr.length()) {
      uArr.optString(i)?.takeIf { it.isNotBlank() }?.let(undo::add)
    }
    val activeId = j.optJSONObject("active_target")?.optString("id")?.takeIf { it.isNotBlank() }
    WorldStateResult(objs, Triple(wx, wy, wz), wYaw, wLocalized, pins, undo, activeId)
  }.getOrNull()

  fun postFrame(host: String, jpegBytes: ByteArray, rotation: FloatArray?): FrameResult {
    val conn = openConn("${visionBase(host)}/api/frame", 2500, 3000)
    conn.requestMethod = "POST"
    conn.doOutput = true
    conn.setRequestProperty("Content-Type", "image/jpeg")
    conn.setRequestProperty("X-Tracking-Mode", "phone")
    conn.setRequestProperty("X-Image-Rotation", "0")
    rotation?.let { conn.setRequestProperty("X-Camera-Rotation", it.joinToString(",")) }
    conn.setFixedLengthStreamingMode(jpegBytes.size)
    try {
      conn.outputStream.use { it.write(jpegBytes) }
      val code = conn.responseCode
      val stream = if (code in 200..299) conn.inputStream else conn.errorStream
      val raw = stream?.bufferedReader()?.use { it.readText() } ?: "{}"
      if (code !in 200..299) {
        throw ApiHttpException(code, "Vision HTTP $code: ${raw.take(160)}")
      }
      val json = JSONObject(raw)
      val arr = json.optJSONArray("detections") ?: JSONArray()
      val items = (0 until arr.length()).mapNotNull { i ->
        val d = arr.optJSONObject(i) ?: return@mapNotNull null
        val b = d.optJSONArray("bbox_xyxy") ?: return@mapNotNull null
        if (b.length() < 4) return@mapNotNull null
        val note = d.optString("note").takeIf { it.isNotBlank() && it != "null" }
        YoloItem(
          id = d.optString("object_id", "${d.optString("label", "obj")}-$i"),
          label = d.optString("label", "object"),
          confidence = d.optDouble("confidence", 0.7),
          box = listOf(b.getDouble(0), b.getDouble(1), b.getDouble(2), b.getDouble(3)),
          note = note,
          noteUncertain = d.optBoolean("note_uncertain", false),
        )
      }
      val fs = json.optJSONArray("frame_size_px")
      val w = fs?.optInt(0, 640) ?: 640
      val h = fs?.optInt(1, 480) ?: 480
      return FrameResult(items, w, h, json.optDouble("inference_seconds", 0.0) * 1000.0)
    } finally {
      conn.disconnect()
    }
  }

  fun transcribePcm(host: String, pcm16: ByteArray): String =
    transcribePcmWithResult(host, pcm16).text

  fun transcribePcmWithResult(host: String, pcm16: ByteArray): TranscriptionResult {
    val segmentBytes = 16000 * 2 * 20
    val chunks = mutableListOf<String>()
    var offset = 0
    var lastProvider = ""
    var anyCloud = false
    var lastDisclosure: String? = null
    var sawSilence = false
    var lastError: String? = null

    while (offset < pcm16.size) {
      val end = minOf(offset + segmentBytes, pcm16.size)
      var slice = pcm16.copyOfRange(offset, end)
      if (slice.size < 3200) slice = slice.copyOf(3200)
      val chunkRes = transcribeChunk(host, slice)
      when (chunkRes.status) {
        TranscriptionStatus.OK -> {
          if (chunkRes.text.isNotBlank()) chunks += chunkRes.text
          if (chunkRes.provider.isNotBlank()) lastProvider = chunkRes.provider
          if (chunkRes.audioLeftLocal) anyCloud = true
          if (!chunkRes.disclosure.isNullOrBlank()) lastDisclosure = chunkRes.disclosure
        }
        TranscriptionStatus.SILENCE -> {
          sawSilence = true
          if (chunkRes.provider.isNotBlank()) lastProvider = chunkRes.provider
          if (chunkRes.audioLeftLocal) anyCloud = true
        }
        TranscriptionStatus.SERVICE_UNAVAILABLE -> {
          lastError = chunkRes.error ?: "Transcription service unavailable"
        }
      }
      offset = end
    }

    val combined = chunks.joinToString(" ").trim()
    return when {
      combined.isNotBlank() -> TranscriptionResult(
        text = combined,
        status = TranscriptionStatus.OK,
        provider = lastProvider,
        audioLeftLocal = anyCloud,
        disclosure = lastDisclosure ?: if (anyCloud) "Audio sent to cloud ASR" else null,
      )
      sawSilence && lastError == null -> TranscriptionResult(
        text = "",
        status = TranscriptionStatus.SILENCE,
        provider = lastProvider,
        audioLeftLocal = anyCloud,
        disclosure = lastDisclosure,
      )
      else -> TranscriptionResult(
        text = "",
        status = TranscriptionStatus.SERVICE_UNAVAILABLE,
        provider = lastProvider,
        audioLeftLocal = anyCloud,
        error = lastError ?: "Transcription service unavailable",
      )
    }
  }

  private fun transcribeChunk(host: String, slice: ByteArray): TranscriptionResult {
    // Prefer local Vision (:8765 Parakeet) first, then Studio (:8000 Gemini cloud ASR).
    val endpoints = listOf(
      "${visionBase(host)}/api/transcribe" to false,
      "${studioBase(host)}/api/transcribe" to true,
    )
    var sawSilence = false
    var silenceProvider = ""
    var silenceCloud = false
    val errors = mutableListOf<String>()

    for ((url, isCloudEndpoint) in endpoints) {
      val attempt = runCatching {
        val conn = openConn(url, 4000, 25000)
        conn.requestMethod = "POST"
        conn.doOutput = true
        conn.setRequestProperty("Content-Type", "application/octet-stream")
        conn.setRequestProperty("X-Sample-Rate", "16000")
        conn.setFixedLengthStreamingMode(slice.size)
        try {
          conn.outputStream.use { it.write(slice) }
          val code = conn.responseCode
          val stream = if (code in 200..299) conn.inputStream else conn.errorStream
          val raw = stream?.bufferedReader()?.use { it.readText() } ?: "{}"
          if (code !in 200..299) {
            throw ApiHttpException(code, "HTTP $code")
          }
          JSONObject(raw)
        } finally {
          conn.disconnect()
        }
      }
      attempt.onSuccess { j ->
        val txt = j.optString("text", "").trim()
        val provider = j.optString("provider", j.optString("model", if (isCloudEndpoint) "gemini-3.8-flash" else "parakeet-local"))
        val leftLocal = j.optBoolean("audio_left_local", isCloudEndpoint)
        val disclosure = j.optString("disclosure", "").takeIf { it.isNotBlank() }
          ?: if (leftLocal) "Audio sent to cloud ASR" else null
        if (txt.isNotBlank()) {
          return TranscriptionResult(
            text = txt,
            status = TranscriptionStatus.OK,
            provider = provider,
            audioLeftLocal = leftLocal,
            disclosure = disclosure,
          )
        }
        // Empty 200 response: record silence but keep trying remaining endpoint in case local/cloud disagrees.
        sawSilence = true
        silenceProvider = provider
        silenceCloud = leftLocal
      }.onFailure { err ->
        errors += "${if (isCloudEndpoint) "Studio" else "Vision"}: ${err.message}"
      }
    }

    return if (sawSilence && errors.size < endpoints.size) {
      TranscriptionResult(
        text = "",
        status = TranscriptionStatus.SILENCE,
        provider = silenceProvider,
        audioLeftLocal = silenceCloud,
      )
    } else {
      TranscriptionResult(
        text = "",
        status = TranscriptionStatus.SERVICE_UNAVAILABLE,
        error = errors.joinToString("; ").ifBlank { "No transcription endpoint responded" },
      )
    }
  }

  fun findMemory(host: String, query: String): JSONObject? = runCatching {
    val enc = URLEncoder.encode(query, "UTF-8")
    getJson("${visionBase(host)}/api/memory/find?q=$enc", timeoutMs = 4000)
  }.getOrNull()

  fun askMemory(host: String, question: String): JSONObject = postJson(
    "${visionBase(host)}/api/memory/ask",
    JSONObject().put("question", question),
    timeoutMs = 5000,
  )

  private fun labelsCompatible(a: String, b: String): Boolean {
    val na = a.trim().lowercase()
    val nb = b.trim().lowercase()
    if (na.isBlank() || nb.isBlank()) return false
    if (na == nb) return true
    val ta = na.split(Regex("\\s+")).filter { it.isNotBlank() }
    val tb = nb.split(Regex("\\s+")).filter { it.isNotBlank() }
    return ta.any { it in tb } || tb.any { it in ta }
  }

  fun askGemini(host: String, question: String, yawDeg: Float): AskResult {
    // 1. Query recent glasses object memory (:8765) first for retrieval questions.
    val memAttempt = runCatching { askMemory(host, question) }
    val studioAttempt = runCatching {
      postJson(
        "${studioBase(host)}/api/ask",
        JSONObject()
          .put("wearer_id", "wearer-b")
          .put("viewer_id", "usr-conor")
          .put("query", question)
          .put("yaw", yawDeg.toDouble()),
        timeoutMs = 25000,
      )
    }

    if (memAttempt.isFailure && studioAttempt.isFailure) {
      throw (studioAttempt.exceptionOrNull() ?: memAttempt.exceptionOrNull() ?: Exception("Servers unreachable"))
    }

    val memJson = memAttempt.getOrNull()
    val memFound = memJson?.optBoolean("found", false) == true
    val memItem = memJson?.optJSONObject("item")
    val memLabel = memItem?.optString("label")?.takeIf { it.isNotBlank() && it != "null" }
    val memAge = memJson?.optString("age_label", memItem?.optString("age_label", "") ?: "")?.takeIf { it.isNotBlank() }
    val memCtx = memJson?.optString("context", memItem?.optString("spatial_phrase", "") ?: "")?.takeIf { it.isNotBlank() }
    val memAmb = memJson?.optString("ambiguity", "")?.takeIf { it.isNotBlank() }
    val memSpoken = memJson?.optString("spoken_response", memJson.optString("summary", ""))?.trim().orEmpty()

    val studioJson = studioAttempt.getOrNull()
    val studioFound = studioJson?.optBoolean("found", false) == true
    val studioLocalized = studioJson?.optBoolean("localized", false) == true
    val studioStale = studioJson?.optBoolean("stale", false) == true
    val studioObjs = mutableListOf<SpatialObject>()
    val singleObj = studioJson?.optJSONObject("object")
    if (singleObj != null) {
      val pos = singleObj.optJSONArray("position")
      studioObjs += SpatialObject(
        id = singleObj.optString("id", "target-0"),
        label = singleObj.optString("label", "object"),
        x = pos?.optDouble(0, 0.0) ?: singleObj.optDouble("x", 0.0),
        y = pos?.optDouble(1, 0.0) ?: singleObj.optDouble("y", 0.0),
        z = pos?.optDouble(2, 0.0) ?: singleObj.optDouble("z", 0.0),
      )
    }
    val oArr = studioJson?.optJSONArray("objects") ?: JSONArray()
    for (i in 0 until oArr.length()) {
      val o = oArr.optJSONObject(i) ?: continue
      val pos = o.optJSONArray("position")
      studioObjs += SpatialObject(
        id = o.optString("id", "obj-$i"),
        label = o.optString("label", "object"),
        x = pos?.optDouble(0, 0.0) ?: o.optDouble("x", 0.0),
        y = pos?.optDouble(1, 0.0) ?: o.optDouble("y", 0.0),
        z = pos?.optDouble(2, 0.0) ?: o.optDouble("z", 0.0),
      )
    }
    val studioSpoken = studioJson?.optString(
      "spoken_answer",
      studioJson.optString("spoken_response", studioJson.optString("answer", studioJson.optString("message", ""))),
    )?.trim().orEmpty()
    val studioTargetLabel = singleObj?.optString("label")?.takeIf { it.isNotBlank() && it != "null" }
      ?: studioJson?.optString("target_label", studioJson.optString("object", ""))?.takeIf { it.isNotBlank() && it != "null" && !it.startsWith("{") }
    val studioTargetId = singleObj?.optString("id")?.takeIf { it.isNotBlank() && it != "null" }
      ?: studioJson?.optString("target_id", "")?.takeIf { it.isNotBlank() && it != "null" }

    if (memFound) {
      val sameMappedTarget = studioFound &&
        !memLabel.isNullOrBlank() &&
        !studioTargetLabel.isNullOrBlank() &&
        labelsCompatible(memLabel, studioTargetLabel)
      val useMappedNav = sameMappedTarget && studioLocalized && !studioStale && studioObjs.isNotEmpty()
      val answerText = when {
        memSpoken.isNotBlank() && useMappedNav && studioSpoken.isNotBlank() && !studioSpoken.contains("haven't seen", ignoreCase = true) ->
          "$memSpoken ($studioSpoken)"
        memSpoken.isNotBlank() -> memSpoken
        else -> studioSpoken.ifBlank { "Last seen ${memAge ?: "recently"} ${memCtx ?: "in view"}." }
      }
      return AskResult(
        found = true,
        answer = answerText,
        targetObject = if (useMappedNav) (studioTargetLabel ?: memLabel) else memLabel,
        targetObjectId = if (useMappedNav) studioTargetId else memItem?.optString("instance_id")?.takeIf { it.isNotBlank() },
        objects = if (useMappedNav) studioObjs else emptyList(),
        localized = useMappedNav,
        stale = if (useMappedNav) studioStale else false,
        ageLabel = memAge,
        context = memCtx,
        ambiguity = memAmb,
        fromMemory = true,
      )
    }

    if (studioFound) {
      return AskResult(
        found = true,
        answer = studioSpoken.ifBlank { "Mapped ${studioTargetLabel ?: "object"} found." },
        targetObject = studioTargetLabel,
        targetObjectId = studioTargetId,
        objects = studioObjs,
        localized = studioLocalized && studioObjs.isNotEmpty(),
        stale = studioStale,
        fromMemory = false,
      )
    }

    val noMatchMsg = memSpoken.takeIf { it.isNotBlank() }
      ?: studioSpoken.takeIf { it.isNotBlank() }
      ?: "I don't have a recent observation matching that."
    return AskResult(
      found = false,
      answer = noMatchMsg,
      targetObject = null,
      targetObjectId = null,
      objects = emptyList(),
      localized = false,
      fromMemory = memJson != null,
    )
  }

  fun savePermanentNote(
    host: String,
    objectId: String,
    label: String,
    text: String,
    box: List<Double>?,
    frameWidth: Int,
    frameHeight: Int,
    noteId: String? = null,
  ): NoteSaveResult {
    // Single authoritative forwarding path: Vision :8765/api/notes persists locally and relays to Studio :8000/api/pin-from-hud with one stable note_id.
    return runCatching {
      val payload = JSONObject()
        .put("object_id", objectId)
        .put("label", label)
        .put("text", text)
        .put("author", "You")
      if (!noteId.isNullOrBlank()) {
        payload.put("note_id", noteId)
      }
      if (box != null && box.size >= 4) {
        payload.put("bbox_xyxy", JSONArray(box))
        payload.put("frame_size_px", JSONArray(listOf(frameWidth, frameHeight)))
      }
      val j = postJson("${visionBase(host)}/api/notes", payload, timeoutMs = 4500)
      val noteObj = j.optJSONObject("note")
      val savedId = j.optString("note_id", noteObj?.optString("note_id", noteId ?: "") ?: "").takeIf { it.isNotBlank() }
      val studioSynced = j.optBoolean("studio_synced", noteObj?.optBoolean("studio_synced", false) ?: false)
      val discarded = j.optBoolean("discarded", false)
      NoteSaveResult(
        ok = j.optBoolean("ok", true) && !discarded,
        noteId = savedId,
        studioSynced = studioSynced,
        discarded = discarded,
      )
    }.getOrElse { err ->
      NoteSaveResult(
        ok = false,
        noteId = noteId,
        studioSynced = false,
        error = err.message ?: "Save failed",
      )
    }
  }

  fun runCtrlZ(host: String): List<String>? = runCatching {
    val j = postJson("${studioBase(host)}/api/ctrl-z", JSONObject(), timeoutMs = 4000)
    val arr = j.optJSONArray("undo_steps") ?: JSONArray()
    (0 until arr.length()).mapNotNull { arr.optString(it)?.takeIf { s -> s.isNotBlank() } }
  }.getOrNull()

  fun sendImuSample(
    host: String,
    yaw: Float,
    pitch: Float,
    roll: Float,
    sessionId: String = "glasses-live",
  ) = runCatching {
    val nowMs = System.currentTimeMillis().toDouble()
    postJson(
      "${studioBase(host)}/api/imu-sessions",
      JSONObject()
        .put("session_id", sessionId)
        .put("device", "meta-ray-ban-display")
        .put("started_at_ms", nowMs)
        .put("ended_at_ms", nowMs)
        .put("step_count", 1)
        .put(
          "samples",
          JSONArray().put(
            JSONObject()
              .put("t_ms", nowMs)
              .put("yaw", yaw.toDouble())
              .put("pitch", pitch.toDouble())
              .put("roll", roll.toDouble()),
          ),
        ),
      timeoutMs = 1500,
    )
  }

  fun togglePrivacy(host: String, paused: Boolean): PrivacyToggleResult {
    val visionRes = runCatching {
      postJson("${visionBase(host)}/api/privacy", JSONObject().put("paused", paused), timeoutMs = 2500)
    }
    val studioRes = runCatching {
      postJson("${studioBase(host)}/api/privacy", JSONObject().put("paused", paused), timeoutMs = 2500)
    }
    val vOk = visionRes.isSuccess && visionRes.getOrNull()?.optBoolean("ok", true) != false
    val sOk = studioRes.isSuccess && studioRes.getOrNull()?.optBoolean("ok", true) != false
    val err = listOfNotNull(
      visionRes.exceptionOrNull()?.message?.let { "Vision: $it" },
      studioRes.exceptionOrNull()?.message?.let { "Studio: $it" },
    ).joinToString("; ").takeIf { it.isNotBlank() }
    return PrivacyToggleResult(
      visionConfirmed = vOk,
      studioConfirmed = sOk,
      paused = paused,
      error = err,
    )
  }

  fun clearNotes(host: String): SessionClearResult {
    val deleteJson: (String) -> JSONObject = { fullUrl ->
      val conn = openConn(fullUrl, 2500, 3000)
      conn.requestMethod = "DELETE"
      try {
        val code = conn.responseCode
        val stream = if (code in 200..299) conn.inputStream else conn.errorStream
        val text = stream?.bufferedReader()?.use { it.readText() } ?: "{}"
        if (code !in 200..299) {
          throw ApiHttpException(code, "HTTP $code")
        }
        if (text.isBlank()) JSONObject() else JSONObject(text)
      } finally {
        conn.disconnect()
      }
    }
    val vBase = visionBase(host)
    val vSession = runCatching { deleteJson("$vBase/api/session") }
    var visionCleared = vSession.isSuccess && vSession.getOrNull()?.optBoolean("cleared", true) != false
    var studioCleared = vSession.getOrNull()?.optBoolean("studio_cleared", false) == true

    if (!visionCleared) {
      val nOk = runCatching { deleteJson("$vBase/api/notes") }.isSuccess
      val mOk = runCatching { deleteJson("$vBase/api/memory") }.isSuccess
      visionCleared = nOk && mOk
    }

    if (!studioCleared) {
      val sRes = runCatching { deleteJson("${studioBase(host)}/api/session") }
      studioCleared = sRes.isSuccess && sRes.getOrNull()?.optBoolean("cleared", true) != false
    }

    return SessionClearResult(
      visionCleared = visionCleared,
      studioCleared = studioCleared,
      error = vSession.exceptionOrNull()?.message,
    )
  }
}
