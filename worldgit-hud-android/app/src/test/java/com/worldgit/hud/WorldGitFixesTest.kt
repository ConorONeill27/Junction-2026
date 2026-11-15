package com.worldgit.hud

import com.worldgit.hud.spatial.ApiHttpException
import com.worldgit.hud.spatial.CompassMath
import com.worldgit.hud.spatial.SpeechEndpoint
import com.worldgit.hud.spatial.TranscriptionStatus
import com.worldgit.hud.spatial.WorldGitApi
import org.json.JSONArray
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.io.BufferedInputStream
import java.net.InetAddress
import java.net.ServerSocket
import java.net.SocketException
import java.util.concurrent.CopyOnWriteArrayList
import kotlin.concurrent.thread

private class TinyHttpServer(
  private val handler: (method: String, path: String, body: String) -> Pair<Int, String>,
) {
  private val serverSocket = ServerSocket(0, 16, InetAddress.getByName("127.0.0.1"))
  @Volatile private var running = true
  val port: Int get() = serverSocket.localPort

  private val acceptThread = thread(isDaemon = true) {
    while (running) {
      val sock = try {
        serverSocket.accept()
      } catch (_: SocketException) {
        break
      }
      thread(isDaemon = true) {
        sock.use { s ->
          val inp = BufferedInputStream(s.getInputStream())
          val headerBytes = ArrayList<Byte>()
          var state = 0
          while (true) {
            val b = inp.read()
            if (b == -1) break
            headerBytes.add(b.toByte())
            state = when {
              state == 0 && b == '\r'.code -> 1
              state == 1 && b == '\n'.code -> 2
              state == 2 && b == '\r'.code -> 3
              state == 3 && b == '\n'.code -> 4
              else -> 0
            }
            if (state == 4) break
          }
          val headerText = headerBytes.toByteArray().toString(Charsets.UTF_8)
          val lines = headerText.split("\r\n")
          val parts = lines.firstOrNull()?.split(" ") ?: emptyList()
          val method = parts.getOrElse(0) { "GET" }
          val rawTarget = parts.getOrElse(1) { "/" }
          val path = rawTarget.substringBefore('?')
          var contentLength = 0
          for (line in lines.drop(1)) {
            if (line.lowercase().startsWith("content-length:")) {
              contentLength = line.substringAfter(':').trim().toIntOrNull() ?: 0
            }
          }
          val bodyBuf = ByteArray(contentLength)
          var readTotal = 0
          while (readTotal < contentLength) {
            val r = inp.read(bodyBuf, readTotal, contentLength - readTotal)
            if (r <= 0) break
            readTotal += r
          }
          val bodyStr = bodyBuf.copyOf(readTotal).toString(Charsets.UTF_8)
          val (code, json) = handler(method, path, bodyStr)
          val payload = json.toByteArray(Charsets.UTF_8)
          val reason = if (code in 200..299) "OK" else "Error"
          val resp = "HTTP/1.1 $code $reason\r\nContent-Type: application/json\r\nContent-Length: ${payload.size}\r\nConnection: close\r\n\r\n"
          val out = s.getOutputStream()
          out.write(resp.toByteArray(Charsets.UTF_8))
          out.write(payload)
          out.flush()
        }
      }
    }
  }

  fun stop() {
    running = false
    runCatching { serverSocket.close() }
    runCatching { acceptThread.join(500) }
  }
}

class WorldGitFixesTest {
  private lateinit var visionServer: TinyHttpServer
  private lateinit var studioServer: TinyHttpServer
  private val recordedRequests = CopyOnWriteArrayList<Pair<String, String>>()

  private var visionNotesResponse: Pair<Int, String> = 200 to """{"notes":[]}"""
  private var visionMemoryAskResponse: Pair<Int, String> = 200 to """{"found":false,"spoken_response":"I don't have a recent observation matching that."}"""
  private var visionTranscribeResponse: Pair<Int, String> = 503 to """{"error":"Local ASR offline"}"""
  private var visionSaveNoteResponse: Pair<Int, String> = 200 to """{"ok":true,"note_id":"note-1","studio_synced":true}"""
  private var visionDeleteSessionResponse: Pair<Int, String> = 200 to """{"ok":true,"studio_cleared":false}"""
  private var visionPrivacyResponse: Pair<Int, String> = 200 to """{"ok":true,"paused":true}"""

  private var studioStateResponse: Pair<Int, String> = 200 to """{"wearers":[],"objects":[],"pins":[]}"""
  private var studioAskResponse: Pair<Int, String> = 200 to """{"found":false,"localized":false,"spoken_answer":"I don't have a recent observation or mapped location for that item."}"""
  private var studioTranscribeResponse: Pair<Int, String> = 503 to """{"error":"Cloud ASR unavailable"}"""
  private var studioPrivacyResponse: Pair<Int, String> = 200 to """{"ok":true,"paused":true}"""

  @Before
  fun setUp() {
    recordedRequests.clear()
    WorldGitApi.setApiToken("")

    visionServer = TinyHttpServer { method, path, body ->
      recordedRequests.add("vision:$method:$path" to body)
      when (path) {
        "/api/notes" -> if (method == "POST") visionSaveNoteResponse else visionNotesResponse
        "/api/memory/ask" -> visionMemoryAskResponse
        "/api/transcribe" -> visionTranscribeResponse
        "/api/session" -> visionDeleteSessionResponse
        "/api/privacy" -> visionPrivacyResponse
        else -> 404 to """{"error":"not found"}"""
      }
    }

    studioServer = TinyHttpServer { method, path, body ->
      recordedRequests.add("studio:$method:$path" to body)
      when (path) {
        "/api/state" -> studioStateResponse
        "/api/ask" -> studioAskResponse
        "/api/transcribe" -> studioTranscribeResponse
        "/api/privacy" -> studioPrivacyResponse
        else -> 404 to """{"error":"not found"}"""
      }
    }

    WorldGitApi.visionBaseOverride = "http://127.0.0.1:${visionServer.port}"
    WorldGitApi.studioBaseOverride = "http://127.0.0.1:${studioServer.port}"
  }

  @After
  fun tearDown() {
    WorldGitApi.visionBaseOverride = null
    WorldGitApi.studioBaseOverride = null
    WorldGitApi.setApiToken("")
    visionServer.stop()
    studioServer.stop()
  }

  @Test
  fun fix1_and_fix2_askQueriesMemoryFirst_andNoFallbackWhenUnlocalizedOrMissing() {
    visionMemoryAskResponse = 200 to JSONObject()
      .put("found", true)
      .put("spoken_response", "Last seen 4 minutes ago beside the laptop.")
      .put("age_label", "4 minutes ago")
      .put("context", "beside the laptop")
      .put("ambiguity", "2 bottles seen")
      .put("item", JSONObject().put("label", "bottle"))
      .toString()

    // Studio does not have this item in 3D room
    studioAskResponse = 200 to """{"found":false,"localized":false,"spoken_answer":"I don't have a recent observation or mapped location for that item."}"""

    val res = WorldGitApi.askGemini("127.0.0.1", "Where is my bottle?", 0f)
    assertTrue(res.found)
    assertTrue(res.fromMemory)
    assertFalse(res.localized)
    assertEquals("bottle", res.targetObject)
    assertTrue(res.answer.contains("Last seen 4 minutes ago"))
    assertEquals("2 bottles seen", res.ambiguity)
    assertTrue(recordedRequests.any { it.first == "vision:POST:/api/memory/ask" })

    // Missing query clears target
    visionMemoryAskResponse = 200 to """{"found":false,"spoken_response":"I don't have a recent observation matching that."}"""
    val missing = WorldGitApi.askGemini("127.0.0.1", "Where are my sunglasses?", 0f)
    assertFalse(missing.found)
    assertNull(missing.targetObject)
    assertTrue(missing.answer.contains("I don't have a recent observation"))

    // CompassMath null target produces no arrow
    val nav = CompassMath.relativeTargetNav(45f, Triple(0.0, 0.0, 0.0), null)
    assertEquals("", nav.second)
    assertEquals("NO TARGET", nav.third)
    assertFalse(nav.fourth)
  }

  @Test
  fun fix2_fetchWorldStateSelectsWearerBExplicitly() {
    studioStateResponse = 200 to JSONObject()
      .put(
        "wearers",
        JSONArray()
          .put(JSONObject().put("wearer_id", "wearer-a").put("name", "Alex").put("position", JSONArray(listOf(9.0, 1.5, 9.0))).put("yaw", 120.0).put("localized", true))
          .put(JSONObject().put("wearer_id", "wearer-b").put("name", "Conor").put("position", JSONArray(listOf(1.0, 1.5, -2.0))).put("yaw", 33.0).put("localized", true)),
      )
      .put("objects", JSONArray())
      .put("pins", JSONArray())
      .toString()

    val state = WorldGitApi.fetchWorldState("127.0.0.1")
    assertNotNull(state)
    assertEquals(Triple(1.0, 1.5, -2.0), state!!.wearerPos)
    assertEquals(33.0f, state.wearerYaw)
    assertTrue(state.wearerLocalized)
  }

  @Test
  fun fix3_and_fix7_explicitOutcomes_non2xxRejection_singleForwarding_and_emptySync() {
    // Fix 7: empty [] from 200 is a valid empty sync, whereas 500 is Result.failure
    visionNotesResponse = 200 to """{"notes":[]}"""
    val emptySync = WorldGitApi.fetchSavedNotes("127.0.0.1")
    assertTrue(emptySync.isSuccess)
    assertTrue(emptySync.getOrThrow().isEmpty())

    visionNotesResponse = 500 to """{"error":"db down"}"""
    val failedSync = WorldGitApi.fetchSavedNotes("127.0.0.1")
    assertTrue(failedSync.isFailure)
    assertTrue(failedSync.exceptionOrNull() is ApiHttpException)

    // Fix 3: single authoritative forwarding path (:8765/api/notes) & stable noteId
    visionSaveNoteResponse = 200 to """{"ok":true,"note_id":"note-stable-42","studio_synced":false}"""
    val saveOk = WorldGitApi.savePermanentNote(
      host = "127.0.0.1",
      objectId = "obj-1",
      label = "bottle",
      text = "Electrolytes",
      box = listOf(10.0, 20.0, 80.0, 140.0),
      frameWidth = 640,
      frameHeight = 480,
      noteId = "note-stable-42",
    )
    assertTrue(saveOk.ok)
    assertEquals("note-stable-42", saveOk.noteId)
    assertFalse(saveOk.studioSynced)
    // Android must NOT post directly to studio /api/pin-from-hud
    assertFalse(recordedRequests.any { it.first == "studio:POST:/api/pin-from-hud" })

    // Server error during save -> ok = false (not silently "Pinned")
    visionSaveNoteResponse = 500 to """{"error":"disk full"}"""
    val saveFail = WorldGitApi.savePermanentNote("127.0.0.1", "obj-1", "bottle", "Electrolytes", null, 640, 480, "note-stable-42")
    assertFalse(saveFail.ok)

    // Clear inspects studio_cleared flag
    visionDeleteSessionResponse = 200 to """{"ok":true,"studio_cleared":false}"""
    val clearRes = WorldGitApi.clearNotes("127.0.0.1")
    assertTrue(clearRes.visionCleared)
    assertFalse(clearRes.studioCleared)

    // Privacy toggle reports partial confirmation if Studio unreachable
    WorldGitApi.studioBaseOverride = "http://127.0.0.1:1"
    val privPartial = WorldGitApi.togglePrivacy("127.0.0.1", true)
    assertTrue(privPartial.visionConfirmed)
    assertFalse(privPartial.studioConfirmed)
  }

  @Test
  fun fix5_wallClockSpeechDeadlines_and_transcriptionFallbackDistinguishesSilenceFromFailure() {
    val endpoint = SpeechEndpoint(maxWallClockMs = 25_000L, stalledStreamMs = 1_500L)
    assertNull(endpoint.wallClockDeadlineReason(elapsedMs = 1_000L, sinceLastChunkMs = 200L, gotAnyAudio = true))
    assertEquals("STREAM_STALLED", endpoint.wallClockDeadlineReason(elapsedMs = 4_000L, sinceLastChunkMs = 1_600L, gotAnyAudio = true))
    assertEquals("MAX_WALL_CLOCK", endpoint.wallClockDeadlineReason(elapsedMs = 25_000L, sinceLastChunkMs = 100L, gotAnyAudio = true))

    val pcm = ByteArray(3200) { 1 }
    // Empty 200 on :8765 must NOT stop fallback to :8000!
    visionTranscribeResponse = 200 to """{"text":""}"""
    studioTranscribeResponse = 200 to """{"status":"ok","text":"where is my bottle","provider":"gemini-3.8-flash","audio_left_local":true,"disclosure":"Audio sent to Google Gemini"}"""
    val fallbackOk = WorldGitApi.transcribePcmWithResult("127.0.0.1", pcm)
    assertEquals(TranscriptionStatus.OK, fallbackOk.status)
    assertEquals("where is my bottle", fallbackOk.text)
    assertTrue(fallbackOk.audioLeftLocal)

    // Explicit silence vs service unavailable
    visionTranscribeResponse = 200 to """{"status":"silence","text":""}"""
    studioTranscribeResponse = 200 to """{"status":"silence","text":""}"""
    assertEquals(TranscriptionStatus.SILENCE, WorldGitApi.transcribePcmWithResult("127.0.0.1", pcm).status)

    visionTranscribeResponse = 503 to """{"error":"offline"}"""
    studioTranscribeResponse = 503 to """{"error":"offline"}"""
    assertEquals(TranscriptionStatus.SERVICE_UNAVAILABLE, WorldGitApi.transcribePcmWithResult("127.0.0.1", pcm).status)
  }

  @Test
  fun fix1_and_fix2_freshMemoryDoesNotInheritStudioStale_matchesAliases_and_cloudPortAgnostic() {
    visionMemoryAskResponse = 200 to JSONObject()
      .put("found", true)
      .put("spoken_response", "Last seen 1 minute ago on desk.")
      .put("item", JSONObject().put("label", "bottle"))
      .toString()

    // Studio has stale object -> fresh memory result must NOT inherit stale = true
    studioAskResponse = 200 to JSONObject()
      .put("found", true)
      .put("localized", false)
      .put("stale", true)
      .put("object", JSONObject().put("id", "obj-b").put("label", "Water Bottle").put("position", JSONArray(listOf(0.5, 0.9, -1.2))))
      .toString()

    val noStaleLeak = WorldGitApi.askGemini("127.0.0.1", "Where is my bottle?", 0f)
    assertTrue(noStaleLeak.found)
    assertFalse(noStaleLeak.localized)
    assertFalse(noStaleLeak.stale)

    // Studio has localized non-stale "Water Bottle" -> alias compatible with "bottle"
    studioAskResponse = 200 to JSONObject()
      .put("found", true)
      .put("localized", true)
      .put("stale", false)
      .put("spoken_answer", "Water Bottle was last seen at Desk (1.2m ahead).")
      .put("object", JSONObject().put("id", "obj-b").put("label", "Water Bottle").put("position", JSONArray(listOf(0.5, 0.9, -1.2))))
      .toString()

    val aliasMatch = WorldGitApi.askGemini("127.0.0.1", "Where is my bottle?", 0f)
    assertTrue(aliasMatch.found)
    assertTrue(aliasMatch.localized)
    assertEquals("Water Bottle", aliasMatch.targetObject)

    // Studio missing label -> must NOT wildcard match memory label
    studioAskResponse = 200 to JSONObject()
      .put("found", true)
      .put("localized", true)
      .put("stale", false)
      .put("objects", JSONArray().put(JSONObject().put("id", "obj-x").put("label", "laptop").put("position", JSONArray(listOf(0.0, 0.8, -1.0)))))
      .toString()

    val nullLabelNoWildcard = WorldGitApi.askGemini("127.0.0.1", "Where is my bottle?", 0f)
    assertTrue(nullLabelNoWildcard.found)
    assertFalse(nullLabelNoWildcard.localized)

    // Studio fallback on non-8000 port without explicit audio_left_local still marks audioLeftLocal = true
    val pcm = ByteArray(3200) { 2 }
    visionTranscribeResponse = 503 to """{"error":"local offline"}"""
    studioTranscribeResponse = 200 to """{"text":"find keys"}"""
    val cloudRes = WorldGitApi.transcribePcmWithResult("127.0.0.1", pcm)
    assertEquals(TranscriptionStatus.OK, cloudRes.status)
    assertTrue(cloudRes.audioLeftLocal)
    assertEquals("gemini-3.8-flash", cloudRes.provider)
  }
}
