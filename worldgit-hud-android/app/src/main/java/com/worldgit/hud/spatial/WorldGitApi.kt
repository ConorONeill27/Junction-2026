package com.worldgit.hud.spatial

import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URI

data class WorldStateResult(
  val objects: List<SpatialObject>,
  val wearerPos: Triple<Double, Double, Double>,
  val wearerYaw: Float?,
  val pins: List<SavedNote>,
  val undoSteps: List<String>,
  val activeTargetId: String?,
)

data class AskResult(
  val answer: String,
  val targetObject: String?,
  val targetObjectId: String?,
  val objects: List<SpatialObject>,
)

data class FrameResult(
  val items: List<YoloItem>,
  val width: Int,
  val height: Int,
  val inferenceMs: Double,
)

object WorldGitApi {
  fun visionBase(hostInput: String): String {
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
    val conn = URI(url).toURL().openConnection() as HttpURLConnection
    conn.connectTimeout = timeoutMs
    conn.readTimeout = timeoutMs
    try {
      val stream = if (conn.responseCode in 200..299) conn.inputStream else conn.errorStream
      return JSONObject(stream.bufferedReader().use { it.readText() })
    } finally {
      conn.disconnect()
    }
  }

  private fun postJson(url: String, body: JSONObject, timeoutMs: Int = 6000): JSONObject {
    val bytes = body.toString().toByteArray(Charsets.UTF_8)
    val conn = URI(url).toURL().openConnection() as HttpURLConnection
    conn.connectTimeout = timeoutMs
    conn.readTimeout = timeoutMs
    conn.requestMethod = "POST"
    conn.doOutput = true
    conn.setRequestProperty("Content-Type", "application/json")
    conn.setFixedLengthStreamingMode(bytes.size)
    try {
      conn.outputStream.use { it.write(bytes) }
      val stream = if (conn.responseCode in 200..299) conn.inputStream else conn.errorStream
      val text = stream?.bufferedReader()?.use { it.readText() } ?: "{}"
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

  fun fetchSavedNotes(host: String): List<SavedNote> = runCatching {
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
  }.getOrDefault(emptyList())

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
    val wearers = j.optJSONArray("wearers")
    if (wearers != null && wearers.length() > 0) {
      val w0 = wearers.optJSONObject(0)
      val wp = w0?.optJSONArray("position")
      if (wp != null && wp.length() >= 3) {
        wx = wp.optDouble(0, 0.0)
        wy = wp.optDouble(1, 1.6)
        wz = wp.optDouble(2, 0.0)
      }
      if (w0 != null && w0.has("yaw")) {
        wYaw = w0.optDouble("yaw", 0.0).toFloat()
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
    WorldStateResult(objs, Triple(wx, wy, wz), wYaw, pins, undo, activeId)
  }.getOrNull()

  fun postFrame(host: String, jpegBytes: ByteArray, rotation: FloatArray?): FrameResult {
    val conn = URI("${visionBase(host)}/api/frame").toURL().openConnection() as HttpURLConnection
    conn.connectTimeout = 2500
    conn.readTimeout = 3000
    conn.requestMethod = "POST"
    conn.doOutput = true
    conn.setRequestProperty("Content-Type", "image/jpeg")
    conn.setRequestProperty("X-Tracking-Mode", "phone")
    conn.setRequestProperty("X-Image-Rotation", "0")
    rotation?.let { conn.setRequestProperty("X-Camera-Rotation", it.joinToString(",")) }
    conn.setFixedLengthStreamingMode(jpegBytes.size)
    try {
      conn.outputStream.use { it.write(jpegBytes) }
      val json = JSONObject(conn.inputStream.bufferedReader().use { it.readText() })
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

  fun transcribePcm(host: String, pcm16: ByteArray): String {
    val segmentBytes = 16000 * 2 * 20
    val chunks = mutableListOf<String>()
    var offset = 0
    while (offset < pcm16.size) {
      val end = minOf(offset + segmentBytes, pcm16.size)
      var slice = pcm16.copyOfRange(offset, end)
      if (slice.size < 3200) slice = slice.copyOf(3200)
      val text = transcribeChunk(host, slice)
      if (text.isNotBlank()) chunks += text
      offset = end
    }
    return chunks.joinToString(" ").trim()
  }

  private fun transcribeChunk(host: String, slice: ByteArray): String {
    // Call Studio server (:8000) directly for Gemini 3.8 Flash audio ASR, with Vision (:8765) as fallback.
    val urls = listOf(
      "${studioBase(host)}/api/transcribe",
      "${visionBase(host)}/api/transcribe",
    )
    for (url in urls) {
      val res = runCatching {
        val conn = URI(url).toURL().openConnection() as HttpURLConnection
        conn.connectTimeout = 4000
        conn.readTimeout = 25000
        conn.requestMethod = "POST"
        conn.doOutput = true
        conn.setRequestProperty("Content-Type", "application/octet-stream")
        conn.setRequestProperty("X-Sample-Rate", "16000")
        conn.setFixedLengthStreamingMode(slice.size)
        try {
          conn.outputStream.use { it.write(slice) }
          if (conn.responseCode == 200) {
            val j = JSONObject(conn.inputStream.bufferedReader().use { it.readText() })
            j.optString("text", "").trim()
          } else null
        } finally {
          conn.disconnect()
        }
      }.getOrNull()
      if (res != null) return res
    }
    return ""
  }

  fun askGemini(host: String, question: String, yawDeg: Float): AskResult {
    val j = postJson(
      "${studioBase(host)}/api/ask",
      JSONObject()
        .put("wearer_id", "wearer-b")
        .put("query", question)
        .put("yaw", yawDeg.toDouble()),
      timeoutMs = 25000,
    )
    val objs = mutableListOf<SpatialObject>()
    val singleObj = j.optJSONObject("object")
    if (singleObj != null) {
      val pos = singleObj.optJSONArray("position")
      objs += SpatialObject(
        id = singleObj.optString("id", "target-0"),
        label = singleObj.optString("label", "object"),
        x = pos?.optDouble(0, 0.0) ?: singleObj.optDouble("x", 0.0),
        y = pos?.optDouble(1, 0.0) ?: singleObj.optDouble("y", 0.0),
        z = pos?.optDouble(2, 0.0) ?: singleObj.optDouble("z", 0.0),
      )
    }
    val oArr = j.optJSONArray("objects") ?: JSONArray()
    for (i in 0 until oArr.length()) {
      val o = oArr.optJSONObject(i) ?: continue
      val pos = o.optJSONArray("position")
      objs += SpatialObject(
        id = o.optString("id", "obj-$i"),
        label = o.optString("label", "object"),
        x = pos?.optDouble(0, 0.0) ?: o.optDouble("x", 0.0),
        y = pos?.optDouble(1, 0.0) ?: o.optDouble("y", 0.0),
        z = pos?.optDouble(2, 0.0) ?: o.optDouble("z", 0.0),
      )
    }
    val spoken = j.optString(
      "spoken_answer",
      j.optString("spoken_response", j.optString("answer", j.optString("message", ""))),
    ).trim()
    val targetLabel = singleObj?.optString("label")?.takeIf { it.isNotBlank() && it != "null" }
      ?: j.optString("target_label", j.optString("object", "")).takeIf { it.isNotBlank() && it != "null" && !it.startsWith("{") }
    val targetId = singleObj?.optString("id")?.takeIf { it.isNotBlank() && it != "null" }
      ?: j.optString("target_id", "").takeIf { it.isNotBlank() && it != "null" }
    return AskResult(
      answer = spoken.ifBlank { "Turn toward your target." },
      targetObject = targetLabel,
      targetObjectId = targetId,
      objects = objs,
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
  ) {
    // 1. Save to SpatialNoteStore on :8765 (notes.json + crop HSV re-ID + auto-relay to :8000/api/pin-from-hud)
    runCatching {
      val payload = JSONObject()
        .put("object_id", objectId)
        .put("label", label)
        .put("text", text)
        .put("author", "You")
      if (box != null && box.size >= 4) {
        payload.put("bbox_xyxy", JSONArray(box))
        payload.put("frame_size_px", JSONArray(listOf(frameWidth, frameHeight)))
      }
      postJson("${visionBase(host)}/api/notes", payload, timeoutMs = 4000)
    }
    // 2. Also ensure HUD pin sync in Studio :8000/api/pin-from-hud
    runCatching {
      val cx = if (box != null && box.size >= 4) (box[0] + box[2]) / 2.0 else frameWidth / 2.0
      val cy = if (box != null && box.size >= 4) (box[1] + box[3]) / 2.0 else frameHeight / 2.0
      postJson(
        "${studioBase(host)}/api/pin-from-hud",
        JSONObject()
          .put("id", objectId)
          .put("label", label)
          .put("text", text)
          .put("author", "You")
          .put("center_px", JSONArray(listOf(cx, cy)))
          .put("frame_size_px", JSONArray(listOf(frameWidth.toDouble(), frameHeight.toDouble()))),
        timeoutMs = 4000,
      )
    }
  }

  fun runCtrlZ(host: String): List<String>? = runCatching {
    val j = postJson("${studioBase(host)}/api/ctrl-z", JSONObject(), timeoutMs = 4000)
    val arr = j.optJSONArray("undo_steps") ?: JSONArray()
    (0 until arr.length()).mapNotNull { arr.optString(it)?.takeIf { s -> s.isNotBlank() } }
  }.getOrNull()

  fun sendImuSample(host: String, yaw: Float, pitch: Float, roll: Float) = runCatching {
    val nowMs = System.currentTimeMillis().toDouble()
    postJson(
      "${studioBase(host)}/api/imu-sessions",
      JSONObject()
        .put("session_id", "glasses-live")
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

  fun togglePrivacy(host: String, paused: Boolean) = runCatching {
    postJson("${visionBase(host)}/api/privacy", JSONObject().put("paused", paused), timeoutMs = 2000)
  }

  fun clearNotes(host: String) = runCatching {
    val deletePath: (String) -> Int = { path ->
      val conn = URI("${visionBase(host)}$path").toURL().openConnection() as HttpURLConnection
      conn.connectTimeout = 2000
      conn.readTimeout = 2000
      conn.requestMethod = "DELETE"
      try {
        val code = conn.responseCode
        val stream = if (code in 200..299) conn.inputStream else conn.errorStream
        stream?.use { it.readBytes() }
        code
      } finally {
        conn.disconnect()
      }
    }
    val code = deletePath("/api/session")
    if (code !in 200..299) {
      deletePath("/api/notes")
      deletePath("/api/memory")
    }
  }
}
