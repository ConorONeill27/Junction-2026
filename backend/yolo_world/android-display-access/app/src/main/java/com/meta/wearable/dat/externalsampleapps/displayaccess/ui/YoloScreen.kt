package com.meta.wearable.dat.externalsampleapps.displayaccess.ui

import android.graphics.Bitmap
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.ui.input.pointer.pointerInput
import com.meta.wearable.dat.externalsampleapps.displayaccess.yoloNoteAt
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.unit.dp
import com.meta.wearable.dat.externalsampleapps.displayaccess.YoloState
import com.meta.wearable.dat.externalsampleapps.displayaccess.AudioClipState
import com.meta.wearable.dat.externalsampleapps.displayaccess.HeadCursorState
import com.meta.wearable.dat.externalsampleapps.displayaccess.LiveSpeechState
import com.meta.wearable.dat.externalsampleapps.displayaccess.drawLiveSpeechOverlay
import com.meta.wearable.dat.externalsampleapps.displayaccess.drawYoloOverlay

@Composable
fun YoloScreen(state: YoloState, preview: Bitmap?, savedAddress: String, onConnect: (String) -> Unit, onPreview: () -> Unit,
  audio: AudioClipState, onAudioPinch: () -> Unit, onRetryAudio: () -> Unit,
  cursor: HeadCursorState, onCursorPinch: () -> Unit, speech: LiveSpeechState, onNoteAction: (String) -> Unit) {
  val latestState by rememberUpdatedState(state)
  val latestCursor by rememberUpdatedState(cursor)
  val latestSpeech by rememberUpdatedState(speech)
  val latestNoteAction by rememberUpdatedState(onNoteAction)
  var address by rememberSaveable { mutableStateOf(savedAddress) }
  Column(Modifier.fillMaxSize().padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
    OutlinedTextField(value = address, onValueChange = { address = it }, label = { Text("Laptop YOLO address") },
      singleLine = true, modifier = Modifier.fillMaxWidth())
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
      Button(onClick = { onConnect(address) }) { Text("Connect / camera") }
      OutlinedButton(onClick = onPreview) { Text("Meta preview") }
    }
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
      OutlinedButton(onClick = onAudioPinch, enabled = !audio.uploading && !speech.active) {
        Text(if (audio.recording) "Stop recording" else "Record audio")
      }
      if (audio.pendingCount > 0 && !audio.recording) {
        OutlinedButton(onClick = onRetryAudio, enabled = !audio.uploading) { Text("Retry upload") }
      }
    }
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
      OutlinedButton(onClick = onCursorPinch) { Text(if (speech.active) "Finish speaking" else if (cursor.visible) "Pinch: select / speak" else "Pinch: show cursor") }
    }
    Text(if (cursor.visible) "Move your head to aim. Pinch a label to select; pinch empty space to speak." else "Pinch to show the head-aimed cursor.", style = MaterialTheme.typography.bodySmall)
    speech.error?.let { Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall) }
    Text(audio.message, style = MaterialTheme.typography.bodySmall,
      color = if (audio.recording) Color.Red else MaterialTheme.colorScheme.onSurface)
    Box(Modifier.weight(1f).fillMaxWidth().background(Color.Black)) {
      if (preview != null) Image(preview.asImageBitmap(), "Phone rear camera", Modifier.fillMaxSize(), contentScale = ContentScale.Fit)
      Canvas(Modifier.fillMaxSize().pointerInput(Unit) {
        detectTapGestures { position ->
          val state = latestState
          val cursor = latestCursor
          val speech = latestSpeech
          if (state.width <= 0 || state.height <= 0) return@detectTapGestures
          val scale = minOf(size.width.toFloat() / state.width, size.height.toFloat() / state.height)
          val left = (size.width - state.width * scale) / 2f
          val top = (size.height - state.height * scale) / 2f
          val tapped = cursor.copy(visible = true,
            x = (position.x - left) / scale / state.width,
            y = (position.y - top) / scale / state.height)
          yoloNoteAt(state, tapped, speech)?.let(latestNoteAction)
        }
      }) {
        drawYoloOverlay(drawContext.canvas.nativeCanvas, size.width, size.height, state, cursor, speech)
        drawLiveSpeechOverlay(drawContext.canvas.nativeCanvas, size.width, size.height, speech)
      }
      Text(state.status, style = MaterialTheme.typography.bodySmall, color = Color.White,
        maxLines = 1, overflow = TextOverflow.Ellipsis,
        modifier = Modifier.align(Alignment.TopStart).fillMaxWidth()
          .background(Color.Black.copy(alpha = .65f)).padding(4.dp))
    }
  }
}
