package com.worldgit.hud.spatial

import android.graphics.Bitmap

enum class HudMode(val label: String, val headerTitle: String) {
  ASK("Ask", "WorldGit • Ctrl+F"),
  PINS("Pins", "WorldGit • Pins"),
  UNDO("Undo", "WorldGit • Ctrl+Z"),
  SCAN("Scan", "WorldGit • Scan");

  fun next(): HudMode = entries[(ordinal + 1) % entries.size]
  fun prev(): HudMode = entries[(ordinal - 1 + entries.size) % entries.size]
}

data class SpatialObject(
  val id: String,
  val label: String,
  val x: Double,
  val y: Double,
  val z: Double,
)

data class SavedNote(
  val noteId: String,
  val objectId: String,
  val label: String,
  val text: String,
  val author: String = "Person A",
)

data class YoloItem(
  val id: String,
  val label: String,
  val confidence: Double,
  val box: List<Double>,
  val note: String? = null,
)

data class YoloState(
  val status: String = "Point glasses at objects to scan",
  val items: List<YoloItem> = emptyList(),
  val width: Int = 640,
  val height: Int = 480,
)

data class HudCalibration(
  val size: Float = 0.8522121f,
  val horizontal: Float = -512.44965f,
  val vertical: Float = -365.0765f,
  val roll: Float = 0f,
  val showGuide: Boolean = false,
)

data class HeadCursorState(
  val visible: Boolean = true,
  val x: Float = 0.5f,
  val y: Float = 0.5f,
  val selectedId: String? = null,
)

enum class SpeechPurpose {
  NONE,
  ASK_GEMINI,
  PIN_NOTE,
}

data class LiveSpeechState(
  val active: Boolean = false,
  val finishing: Boolean = false,
  val purpose: SpeechPurpose = SpeechPurpose.NONE,
  val noteId: String? = null,
  val noteLabel: String? = null,
  val noteBox: List<Double>? = null,
  val text: String = "",
  val error: String? = null,
  val notesByObjectId: Map<String, String> = emptyMap(),
  val notesByLabel: Map<String, String> = emptyMap(),
)

data class CompassState(
  val headingDeg: Float = 0f,
  val cardinal: String = "N",
  val pitchDeg: Float = 0f,
  val rollDeg: Float = 0f,
  val targetLabel: String = "bottle",
  val arrowAngleDeg: Float = 0f,
  val arrowGlyph: String = "↑",
  val turnInstruction: String = "AHEAD",
  val aligned: Boolean = true,
  val answerText: String = "Tap Ask to query Gemini about your room.",
  val sensorSource: String = "READY",
)

data class ImuState(
  val recording: Boolean = false,
  val sampleCount: Int = 0,
  val statusNote: String = "Live 60Hz glasses telemetry",
)

data class AppUiState(
  val mode: HudMode = HudMode.ASK,
  val host: String = "127.0.0.1",
  val glassesConnected: Boolean = false,
  val displayStarted: Boolean = false,
  val cameraStreaming: Boolean = false,
  val inputsAttached: Boolean = false,
  val registered: Boolean = false,
  val deviceStatus: String = "Ready to connect Meta Ray-Ban Display",
  val compass: CompassState = CompassState(),
  val yoloState: YoloState = YoloState(),
  val projectedYoloState: YoloState = YoloState(),
  val cursor: HeadCursorState = HeadCursorState(),
  val speech: LiveSpeechState = LiveSpeechState(),
  val calibration: HudCalibration = HudCalibration(),
  val imu: ImuState = ImuState(),
  val undoSteps: List<String> = emptyList(),
  val savedNotes: List<SavedNote> = emptyList(),
  val cameraPreview: Bitmap? = null,
  val hudMirror: Bitmap? = null,
  val ttsEnabled: Boolean = true,
  val memoryPaused: Boolean = false,
  val snackbar: String? = null,
  val firmwareUpdateRequired: Boolean = false,
  val datAppUpdateRequired: Boolean = false,
)
