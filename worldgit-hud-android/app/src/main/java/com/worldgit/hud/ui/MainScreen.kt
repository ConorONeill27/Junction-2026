package com.worldgit.hud.ui

import android.Manifest
import android.app.Activity
import android.content.pm.PackageManager
import androidx.activity.compose.LocalActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.automirrored.filled.Undo
import androidx.compose.material.icons.filled.*
import androidx.compose.material.icons.outlined.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.drawscope.drawIntoCanvas
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.meta.wearable.dat.core.Wearables
import com.meta.wearable.dat.core.types.Permission
import com.meta.wearable.dat.core.types.PermissionStatus
import com.worldgit.hud.WorldGitViewModel
import com.worldgit.hud.hud.WorldGitHudRenderer
import com.worldgit.hud.spatial.*
import kotlinx.coroutines.launch
import kotlin.math.roundToInt

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MainScreen(viewModel: WorldGitViewModel) {
  val ui by viewModel.ui.collectAsStateWithLifecycle()
  val context = LocalContext.current
  val activity = LocalActivity.current as? Activity
  val scope = rememberCoroutineScope()
  val snackbarHostState = remember { SnackbarHostState() }

  var showSettings by remember { mutableStateOf(false) }
  var pendingConnectAfterRegistration by remember { mutableStateOf(false) }
  var askInput by remember { mutableStateOf("") }

  fun hasAndroidAudio(): Boolean =
    context.checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED

  fun connectNow(withGlassesMic: Boolean) {
    activity?.let { act ->
      viewModel.connectOrDisconnectGlasses(act, hasAudioPermission = withGlassesMic && hasAndroidAudio())
    }
  }

  val glassesMicLauncher = rememberLauncherForActivityResult(
    Wearables.RequestPermissionContract(),
  ) { result ->
    val micOk = result.getOrNull() == PermissionStatus.Granted
    connectNow(withGlassesMic = micOk)
  }

  fun ensureMicThenConnect() {
    scope.launch {
      Wearables.checkPermissionStatus(Permission.MICROPHONE)
        .onSuccess { st ->
          if (st == PermissionStatus.Granted) {
            connectNow(withGlassesMic = true)
          } else {
            glassesMicLauncher.launch(Permission.MICROPHONE)
          }
        }
        .onFailure { _, _ ->
          connectNow(withGlassesMic = false)
        }
    }
  }

  val glassesCameraLauncher = rememberLauncherForActivityResult(
    Wearables.RequestPermissionContract(),
  ) { result ->
    if (result.getOrNull() == PermissionStatus.Granted) {
      ensureMicThenConnect()
    } else {
      scope.launch {
        snackbarHostState.showSnackbar("Glasses camera permission is required for Pins")
      }
    }
  }

  fun ensureGlassesPermissionsAndConnect() {
    scope.launch {
      Wearables.checkPermissionStatus(Permission.CAMERA)
        .onSuccess { st ->
          if (st == PermissionStatus.Granted) {
            ensureMicThenConnect()
          } else {
            glassesCameraLauncher.launch(Permission.CAMERA)
          }
        }
        .onFailure { err, _ ->
          snackbarHostState.showSnackbar("Meta glasses check: ${err.description}")
        }
    }
  }

  val audioPermissionLauncher = rememberLauncherForActivityResult(
    ActivityResultContracts.RequestPermission(),
  ) {
    if (ui.registered) {
      ensureGlassesPermissionsAndConnect()
    } else {
      pendingConnectAfterRegistration = true
      activity?.let { viewModel.connectOrDisconnectGlasses(it, hasAudioPermission = false) }
    }
  }

  fun onConnectPressed() {
    if (ui.displayStarted || ui.cameraStreaming) {
      activity?.let { viewModel.connectOrDisconnectGlasses(it, hasAudioPermission = hasAndroidAudio()) }
      return
    }
    if (!hasAndroidAudio()) {
      audioPermissionLauncher.launch(Manifest.permission.RECORD_AUDIO)
      return
    }
    if (!ui.registered) {
      pendingConnectAfterRegistration = true
      activity?.let { viewModel.connectOrDisconnectGlasses(it, hasAudioPermission = true) }
      return
    }
    ensureGlassesPermissionsAndConnect()
  }

  LaunchedEffect(ui.registered) {
    if (pendingConnectAfterRegistration && ui.registered) {
      pendingConnectAfterRegistration = false
      ensureGlassesPermissionsAndConnect()
    }
  }

  LaunchedEffect(ui.snackbar) {
    val msg = ui.snackbar
    if (!msg.isNullOrBlank()) {
      snackbarHostState.showSnackbar(msg)
      viewModel.clearSnackbar()
    }
  }

  Scaffold(
    containerColor = WorldGitPalette.Bg,
    snackbarHost = {
      SnackbarHost(hostState = snackbarHostState) { data ->
        Snackbar(
          snackbarData = data,
          containerColor = WorldGitPalette.CardElevated,
          contentColor = WorldGitPalette.TextPrimary,
          actionColor = WorldGitPalette.Cyan,
          shape = RoundedCornerShape(14.dp),
        )
      }
    },
    topBar = {
      TopBrandBar(
        ui = ui,
        onConnectClick = ::onConnectPressed,
        onUpdateClick = {
          activity?.let { act ->
            if (ui.firmwareUpdateRequired) viewModel.openFirmwareUpdate(act)
            else if (ui.datAppUpdateRequired) viewModel.openDatAppUpdate(act)
          }
        },
        onSettingsClick = { showSettings = true },
      )
    },
    bottomBar = {
      WorldGitBottomDock(
        activeMode = ui.mode,
        onModeTap = viewModel::onDockButtonTap,
      )
    },
  ) { innerPadding ->
    Column(
      modifier = Modifier
        .fillMaxSize()
        .padding(innerPadding)
        .padding(horizontal = 16.dp),
      verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
      when (ui.mode) {
        HudMode.PINS -> {
          HudMirrorViewport(
            ui = ui,
            onPointerMove = viewModel::onPhoneOverlayMove,
            onPointerTap = viewModel::onPhoneOverlayTap,
          )
        }
        HudMode.ASK -> {
          AskCompassHeroCard(ui = ui)
        }
        else -> Unit
      }

      ModeDetailPanel(
        ui = ui,
        askInput = askInput,
        onAskInputChange = { askInput = it },
        onAskSend = {
          val q = askInput.trim()
          if (q.isNotEmpty()) {
            askInput = ""
            viewModel.askTypedQuestion(q)
          }
        },
        onVoiceAsk = viewModel::toggleAskVoice,
        onPinsAction = { viewModel.onDockButtonTap(HudMode.PINS) },
        onTapObjectChip = { item ->
          val cx = (((item.box.getOrNull(0) ?: 0.0) + (item.box.getOrNull(2) ?: 0.0)) / 2.0 / ui.projectedYoloState.width.coerceAtLeast(1)).toFloat()
          val cy = (((item.box.getOrNull(1) ?: 0.0) + (item.box.getOrNull(3) ?: 0.0)) / 2.0 / ui.projectedYoloState.height.coerceAtLeast(1)).toFloat()
          viewModel.onPhoneOverlayTap(cx.coerceIn(0f, 1f), cy.coerceIn(0f, 1f))
        },
        onUndo = viewModel::runCtrlZ,
        onToggleScan = viewModel::toggleImuRecording,
        modifier = Modifier.weight(1f),
      )
    }
  }

  if (showSettings) {
    SettingsModalSheet(
      ui = ui,
      onDismiss = { showSettings = false },
      onApplyHost = viewModel::updateHost,
      onCalibrationChange = viewModel::updateCalibration,
      onSaveCalibration = viewModel::saveCalibrationNow,
      onToggleTts = viewModel::toggleTts,
      onTogglePrivacy = viewModel::togglePrivacy,
      onClearNotes = viewModel::clearSavedNotes,
    )
  }
}

@Composable
private fun TopBrandBar(
  ui: AppUiState,
  onConnectClick: () -> Unit,
  onUpdateClick: () -> Unit,
  onSettingsClick: () -> Unit,
) {
  val isLive = ui.displayStarted || ui.cameraStreaming
  val statusDotColor = when {
    ui.firmwareUpdateRequired || ui.datAppUpdateRequired -> WorldGitPalette.Amber
    ui.cameraStreaming -> WorldGitPalette.Emerald
    ui.displayStarted -> WorldGitPalette.Cyan
    else -> WorldGitPalette.TextMuted
  }
  val statusLabel = when {
    ui.cameraStreaming && ui.displayStarted -> "GLASSES LIVE"
    ui.displayStarted -> "HUD CONNECTED"
    ui.glassesConnected -> "PAIRED"
    else -> "OFFLINE"
  }

  Row(
    modifier = Modifier
      .fillMaxWidth()
      .statusBarsPadding()
      .padding(horizontal = 16.dp, vertical = 10.dp),
    verticalAlignment = Alignment.CenterVertically,
    horizontalArrangement = Arrangement.SpaceBetween,
  ) {
    Row(
      verticalAlignment = Alignment.CenterVertically,
      horizontalArrangement = Arrangement.spacedBy(10.dp),
      modifier = Modifier.weight(1f),
    ) {
      Box(
        modifier = Modifier
          .size(34.dp)
          .clip(RoundedCornerShape(10.dp))
          .background(
            Brush.linearGradient(listOf(WorldGitPalette.Cyan, WorldGitPalette.Cobalt)),
          ),
        contentAlignment = Alignment.Center,
      ) {
        Icon(
          imageVector = Icons.Outlined.Visibility,
          contentDescription = null,
          tint = WorldGitPalette.Bg,
          modifier = Modifier.size(19.dp),
        )
      }
      Column {
        Text(
          text = "WorldGit",
          style = MaterialTheme.typography.titleLarge,
          maxLines = 1,
        )
        Row(
          verticalAlignment = Alignment.CenterVertically,
          horizontalArrangement = Arrangement.spacedBy(5.dp),
        ) {
          Box(
            modifier = Modifier
              .size(6.dp)
              .clip(CircleShape)
              .background(statusDotColor),
          )
          Text(
            text = statusLabel,
            style = MaterialTheme.typography.labelSmall,
            color = if (isLive) WorldGitPalette.Emerald else WorldGitPalette.TextSecondary,
          )
        }
      }
    }

    Row(
      verticalAlignment = Alignment.CenterVertically,
      horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
      if (ui.firmwareUpdateRequired || ui.datAppUpdateRequired) {
        OutlinedButton(
          onClick = onUpdateClick,
          shape = RoundedCornerShape(999.dp),
          border = BorderStroke(1.dp, WorldGitPalette.Amber),
          contentPadding = PaddingValues(horizontal = 12.dp, vertical = 6.dp),
          modifier = Modifier.height(34.dp),
        ) {
          Text("Update", color = WorldGitPalette.Amber, fontSize = 12.sp, fontWeight = FontWeight.SemiBold)
        }
      }

      Button(
        onClick = onConnectClick,
        shape = RoundedCornerShape(999.dp),
        colors = ButtonDefaults.buttonColors(
          containerColor = if (isLive) WorldGitPalette.CardElevated else WorldGitPalette.Cyan,
          contentColor = if (isLive) WorldGitPalette.Rose else WorldGitPalette.Bg,
        ),
        border = if (isLive) BorderStroke(1.dp, WorldGitPalette.Rose.copy(alpha = 0.55f)) else null,
        contentPadding = PaddingValues(horizontal = 14.dp, vertical = 6.dp),
        modifier = Modifier.height(36.dp),
      ) {
        Icon(
          imageVector = if (isLive) Icons.Filled.LinkOff else Icons.Filled.BluetoothConnected,
          contentDescription = null,
          modifier = Modifier.size(16.dp),
        )
        Spacer(Modifier.width(6.dp))
        Text(
          text = if (isLive) "Disconnect" else "Connect",
          fontSize = 13.sp,
          fontWeight = FontWeight.Bold,
        )
      }

      IconButton(
        onClick = onSettingsClick,
        modifier = Modifier
          .size(36.dp)
          .clip(CircleShape)
          .background(WorldGitPalette.Card)
          .border(1.dp, WorldGitPalette.Border, CircleShape),
      ) {
        Icon(
          imageVector = Icons.Outlined.Tune,
          contentDescription = "Settings & HUD Calibration",
          tint = WorldGitPalette.TextPrimary,
          modifier = Modifier.size(18.dp),
        )
      }
    }
  }
}

@Composable
private fun AskCompassHeroCard(ui: AppUiState) {
  val c = ui.compass
  val accent = if (c.aligned) WorldGitPalette.Emerald else WorldGitPalette.Cyan

  Surface(
    modifier = Modifier
      .fillMaxWidth()
      .height(176.dp),
    shape = RoundedCornerShape(22.dp),
    color = WorldGitPalette.CardElevated,
    border = BorderStroke(1.dp, accent.copy(alpha = 0.45f)),
  ) {
    Row(
      modifier = Modifier
        .fillMaxSize()
        .padding(16.dp),
      verticalAlignment = Alignment.CenterVertically,
      horizontalArrangement = Arrangement.spacedBy(16.dp),
    ) {
      Surface(
        modifier = Modifier.size(132.dp),
        shape = CircleShape,
        color = Color.Black,
        border = BorderStroke(1.dp, accent.copy(alpha = 0.35f)),
      ) {
        Canvas(
          modifier = Modifier
            .fillMaxSize()
            .padding(6.dp),
        ) {
          drawIntoCanvas { canvas ->
            WorldGitHudRenderer.drawCompassDial(
              canvas = canvas.nativeCanvas,
              compass = c,
              cx = size.width / 2f,
              cy = size.height / 2f,
              outerR = minOf(size.width, size.height) * 0.46f,
            )
          }
        }
      }

      Column(
        modifier = Modifier.weight(1f),
        verticalArrangement = Arrangement.spacedBy(8.dp),
      ) {
        Text(
          text = if (c.targetLabel.isBlank()) "SPATIAL COMPASS" else c.targetLabel.uppercase(),
          color = WorldGitPalette.TextMuted,
          fontSize = 11.sp,
          fontWeight = FontWeight.SemiBold,
          letterSpacing = 1.1.sp,
          maxLines = 1,
          overflow = TextOverflow.Ellipsis,
        )
        Text(
          text = c.turnInstruction.ifBlank { "Ask about an object" },
          color = accent,
          fontSize = 22.sp,
          fontWeight = FontWeight.Bold,
          maxLines = 2,
          overflow = TextOverflow.Ellipsis,
        )
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
          Surface(
            shape = RoundedCornerShape(999.dp),
            color = accent.copy(alpha = 0.14f),
          ) {
            Text(
              text = "${c.headingDeg.toInt()}° ${c.cardinal} • ${c.sensorSource}",
              color = accent,
              fontSize = 11.sp,
              fontWeight = FontWeight.SemiBold,
              modifier = Modifier.padding(horizontal = 10.dp, vertical = 4.dp),
            )
          }
          Surface(
            shape = RoundedCornerShape(999.dp),
            color = WorldGitPalette.Bg,
          ) {
            Text(
              text = "${ui.savedNotes.size} pins",
              color = WorldGitPalette.TextSecondary,
              fontSize = 11.sp,
              modifier = Modifier.padding(horizontal = 10.dp, vertical = 4.dp),
            )
          }
        }
      }
    }
  }
}

@Composable
private fun HudMirrorViewport(
  ui: AppUiState,
  onPointerMove: (Float, Float) -> Unit,
  onPointerTap: (Float, Float) -> Unit,
) {
  val showCameraUnderlay = ui.mode == HudMode.PINS && ui.cameraPreview != null
  val borderColor = when {
    ui.speech.active -> WorldGitPalette.Emerald
    ui.cursor.selectedId != null && ui.mode == HudMode.PINS -> WorldGitPalette.Cobalt
    ui.displayStarted -> WorldGitPalette.Cyan.copy(alpha = 0.7f)
    else -> WorldGitPalette.BorderBright
  }

  Surface(
    modifier = Modifier
      .fillMaxWidth()
      .aspectRatio(4f / 3f),
    shape = RoundedCornerShape(22.dp),
    color = Color.Black,
    border = BorderStroke(1.5.dp, borderColor),
    tonalElevation = 0.dp,
  ) {
    Box(
      modifier = Modifier
        .fillMaxSize()
        .pointerInput(ui.mode) {
          detectTapGestures { offset ->
            if (ui.mode == HudMode.PINS && size.width > 0 && size.height > 0) {
              onPointerTap(
                (offset.x / size.width.toFloat()).coerceIn(0f, 1f),
                (offset.y / size.height.toFloat()).coerceIn(0f, 1f),
              )
            }
          }
        }
        .pointerInput(ui.mode) {
          detectDragGestures { change, _ ->
            if (ui.mode == HudMode.PINS && size.width > 0 && size.height > 0) {
              change.consume()
              onPointerMove(
                (change.position.x / size.width.toFloat()).coerceIn(0f, 1f),
                (change.position.y / size.height.toFloat()).coerceIn(0f, 1f),
              )
            }
          }
        },
    ) {
      if (showCameraUnderlay) {
        ui.cameraPreview?.let { bmp ->
          Image(
            bitmap = bmp.asImageBitmap(),
            contentDescription = "Meta Ray-Ban Glasses Camera",
            contentScale = ContentScale.Fit,
            modifier = Modifier.fillMaxSize(),
          )
          Box(
            modifier = Modifier
              .fillMaxSize()
              .background(Color.Black.copy(alpha = 0.16f)),
          )
        }
      }

      Canvas(modifier = Modifier.fillMaxSize()) {
        drawIntoCanvas { canvas ->
          WorldGitHudRenderer.drawFullHud(
            canvas = canvas.nativeCanvas,
            width = size.width,
            height = size.height,
            mode = ui.mode,
            compass = ui.compass,
            projectedYolo = ui.projectedYoloState,
            cursor = ui.cursor,
            speech = ui.speech,
            imu = ui.imu,
            undoSteps = ui.undoSteps,
            savedNotes = ui.savedNotes,
            calibration = ui.calibration,
            memoryPaused = ui.memoryPaused,
            clearBlack = !showCameraUnderlay,
          )
        }
      }

      if (ui.mode == HudMode.PINS && !ui.cameraStreaming && ui.cameraPreview == null) {
        Surface(
          shape = RoundedCornerShape(999.dp),
          color = WorldGitPalette.Card.copy(alpha = 0.88f),
          border = BorderStroke(1.dp, WorldGitPalette.Border),
          modifier = Modifier
            .align(Alignment.Center)
            .padding(horizontal = 24.dp),
        ) {
          Row(
            modifier = Modifier.padding(horizontal = 14.dp, vertical = 9.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
          ) {
            Icon(
              imageVector = Icons.Outlined.PhotoCamera,
              contentDescription = null,
              tint = WorldGitPalette.Cyan,
              modifier = Modifier.size(16.dp),
            )
            Text(
              text = ui.deviceStatus,
              fontSize = 12.sp,
              color = WorldGitPalette.TextSecondary,
              maxLines = 2,
              overflow = TextOverflow.Ellipsis,
            )
          }
        }
      }
    }
  }
}

@Composable
private fun ModeDetailPanel(
  ui: AppUiState,
  askInput: String,
  onAskInputChange: (String) -> Unit,
  onAskSend: () -> Unit,
  onVoiceAsk: () -> Unit,
  onPinsAction: () -> Unit,
  onTapObjectChip: (YoloItem) -> Unit,
  onUndo: () -> Unit,
  onToggleScan: () -> Unit,
  modifier: Modifier = Modifier,
) {
  Surface(
    modifier = modifier.fillMaxWidth(),
    shape = RoundedCornerShape(20.dp),
    color = WorldGitPalette.Card,
    border = BorderStroke(1.dp, WorldGitPalette.Border),
  ) {
    when (ui.mode) {
      HudMode.ASK -> AskDetailContent(
        ui = ui,
        askInput = askInput,
        onAskInputChange = onAskInputChange,
        onAskSend = onAskSend,
        onVoiceAsk = onVoiceAsk,
      )
      HudMode.PINS -> PinsDetailContent(
        ui = ui,
        onPinsAction = onPinsAction,
        onTapObjectChip = onTapObjectChip,
      )
      HudMode.UNDO -> UndoDetailContent(
        ui = ui,
        onUndo = onUndo,
      )
      HudMode.SCAN -> ScanDetailContent(
        ui = ui,
        onToggleScan = onToggleScan,
      )
    }
  }
}

@Composable
private fun AskDetailContent(
  ui: AppUiState,
  askInput: String,
  onAskInputChange: (String) -> Unit,
  onAskSend: () -> Unit,
  onVoiceAsk: () -> Unit,
) {
  Column(
    modifier = Modifier
      .fillMaxSize()
      .padding(14.dp),
    verticalArrangement = Arrangement.SpaceBetween,
  ) {
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
      Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
      ) {
        Surface(
          shape = RoundedCornerShape(999.dp),
          color = WorldGitPalette.Emerald.copy(alpha = 0.14f),
          border = BorderStroke(1.dp, WorldGitPalette.Emerald.copy(alpha = 0.45f)),
        ) {
          Text(
            text = "TARGET: ${ui.compass.targetLabel.uppercase()}",
            modifier = Modifier.padding(horizontal = 10.dp, vertical = 4.dp),
            fontSize = 11.sp,
            fontWeight = FontWeight.Bold,
            color = WorldGitPalette.Emerald,
          )
        }
        Text(
          text = "${ui.compass.cardinal} ${ui.compass.headingDeg.roundToInt()}° • ${ui.compass.turnInstruction}",
          style = MaterialTheme.typography.labelSmall,
          color = if (ui.compass.aligned) WorldGitPalette.Emerald else WorldGitPalette.Cyan,
        )
      }

      Text(
        text = ui.compass.answerText,
        style = MaterialTheme.typography.bodyMedium,
        color = WorldGitPalette.TextPrimary,
        maxLines = 4,
        overflow = TextOverflow.Ellipsis,
      )
    }

    Row(
      modifier = Modifier.fillMaxWidth(),
      verticalAlignment = Alignment.CenterVertically,
      horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
      OutlinedTextField(
        value = askInput,
        onValueChange = onAskInputChange,
        placeholder = { Text("Ask Gemini about the room...", fontSize = 13.sp, color = WorldGitPalette.TextMuted) },
        singleLine = true,
        keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
        keyboardActions = KeyboardActions(onSend = { onAskSend() }),
        shape = RoundedCornerShape(999.dp),
        colors = OutlinedTextFieldDefaults.colors(
          focusedBorderColor = WorldGitPalette.Cyan,
          unfocusedBorderColor = WorldGitPalette.BorderBright,
          focusedContainerColor = WorldGitPalette.Surface,
          unfocusedContainerColor = WorldGitPalette.Surface,
          focusedTextColor = WorldGitPalette.TextPrimary,
          unfocusedTextColor = WorldGitPalette.TextPrimary,
        ),
        trailingIcon = {
          if (askInput.isNotBlank()) {
            IconButton(onClick = onAskSend) {
              Icon(Icons.AutoMirrored.Filled.Send, contentDescription = "Ask", tint = WorldGitPalette.Cyan)
            }
          }
        },
        modifier = Modifier.weight(1f),
      )

      val listening = ui.speech.active && ui.speech.purpose == SpeechPurpose.ASK_GEMINI
      FilledIconButton(
        onClick = onVoiceAsk,
        colors = IconButtonDefaults.filledIconButtonColors(
          containerColor = if (listening) WorldGitPalette.Emerald else WorldGitPalette.Cyan,
          contentColor = WorldGitPalette.Bg,
        ),
        modifier = Modifier.size(48.dp),
      ) {
        Icon(
          imageVector = if (listening) Icons.Filled.Stop else Icons.Filled.Mic,
          contentDescription = "Voice Ask",
        )
      }
    }
  }
}

@Composable
private fun PinsDetailContent(
  ui: AppUiState,
  onPinsAction: () -> Unit,
  onTapObjectChip: (YoloItem) -> Unit,
) {
  val hoveredItem = ui.cursor.selectedId?.let { id ->
    ui.projectedYoloState.items.firstOrNull { it.id == id }
      ?: ui.yoloState.items.firstOrNull { it.id == id }
  }
  val recordingNote = ui.speech.active && ui.speech.purpose == SpeechPurpose.PIN_NOTE

  Column(
    modifier = Modifier
      .fillMaxSize()
      .padding(14.dp),
    verticalArrangement = Arrangement.spacedBy(10.dp),
  ) {
    Row(
      modifier = Modifier.fillMaxWidth(),
      verticalAlignment = Alignment.CenterVertically,
      horizontalArrangement = Arrangement.SpaceBetween,
    ) {
      Column(modifier = Modifier.weight(1f)) {
        Text(
          text = when {
            recordingNote -> "Recording pin for ${ui.speech.noteLabel ?: "object"}..."
            hoveredItem != null -> "Selected: ${hoveredItem.label}"
            else -> "Aim HUD crosshair at an object"
          },
          style = MaterialTheme.typography.titleMedium,
          color = when {
            recordingNote -> WorldGitPalette.Emerald
            hoveredItem != null -> WorldGitPalette.Cobalt
            else -> WorldGitPalette.TextPrimary
          },
        )
        Text(
          text = when {
            recordingNote -> "Speak into glasses mic • tap again to save permanently"
            hoveredItem != null -> "Tap Add Note or temple button to attach permanent note"
            else -> "${ui.projectedYoloState.items.size} objects tracked • ${ui.savedNotes.size} saved pins"
          },
          fontSize = 12.sp,
          color = WorldGitPalette.TextSecondary,
          maxLines = 1,
          overflow = TextOverflow.Ellipsis,
        )
      }

      Spacer(Modifier.width(8.dp))

      Button(
        onClick = onPinsAction,
        shape = RoundedCornerShape(999.dp),
        colors = ButtonDefaults.buttonColors(
          containerColor = when {
            recordingNote -> WorldGitPalette.Emerald
            hoveredItem != null -> WorldGitPalette.Cobalt
            else -> WorldGitPalette.CardElevated
          },
          contentColor = if (recordingNote || hoveredItem != null) WorldGitPalette.Bg else WorldGitPalette.TextPrimary,
        ),
        contentPadding = PaddingValues(horizontal = 14.dp, vertical = 8.dp),
      ) {
        Icon(
          imageVector = if (recordingNote) Icons.Filled.Stop else Icons.Filled.PushPin,
          contentDescription = null,
          modifier = Modifier.size(16.dp),
        )
        Spacer(Modifier.width(6.dp))
        Text(
          text = when {
            recordingNote -> "Save"
            hoveredItem != null -> "Add Note"
            else -> "Center"
          },
          fontSize = 13.sp,
          fontWeight = FontWeight.Bold,
        )
      }
    }

    AnimatedVisibility(
      visible = ui.projectedYoloState.items.isNotEmpty(),
      enter = fadeIn(),
      exit = fadeOut(),
    ) {
      Row(
        modifier = Modifier
          .fillMaxWidth()
          .horizontalScroll(rememberScrollState()),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
      ) {
        ui.projectedYoloState.items.forEach { item ->
          val selected = item.id == ui.cursor.selectedId
          val note = ui.speech.notesByObjectId[item.id]
            ?: ui.speech.notesByLabel[item.label.lowercase()]
            ?: item.note
          FilterChip(
            selected = selected,
            onClick = { onTapObjectChip(item) },
            label = {
              Text(
                text = if (!note.isNullOrBlank()) "${item.label}: ${note.take(18)}" else item.label,
                fontSize = 12.sp,
                maxLines = 1,
              )
            },
            colors = FilterChipDefaults.filterChipColors(
              containerColor = WorldGitPalette.Surface,
              labelColor = WorldGitPalette.TextPrimary,
              selectedContainerColor = WorldGitPalette.Cobalt.copy(alpha = 0.22f),
              selectedLabelColor = WorldGitPalette.Cyan,
            ),
            border = FilterChipDefaults.filterChipBorder(
              enabled = true,
              selected = selected,
              borderColor = WorldGitPalette.BorderBright,
              selectedBorderColor = WorldGitPalette.Cobalt,
            ),
          )
        }
      }
    }

    if (ui.savedNotes.isNotEmpty()) {
      LazyColumn(
        modifier = Modifier.weight(1f),
        verticalArrangement = Arrangement.spacedBy(6.dp),
      ) {
        items(ui.savedNotes.reversed(), key = { it.noteId }) { note ->
          Row(
            modifier = Modifier
              .fillMaxWidth()
              .clip(RoundedCornerShape(10.dp))
              .background(WorldGitPalette.Surface)
              .padding(horizontal = 10.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
          ) {
            Icon(
              imageVector = Icons.Filled.PushPin,
              contentDescription = null,
              tint = WorldGitPalette.Cobalt,
              modifier = Modifier.size(14.dp),
            )
            Text(
              text = note.label,
              fontSize = 12.sp,
              fontWeight = FontWeight.Bold,
              color = WorldGitPalette.Cyan,
            )
            Text(
              text = note.text,
              fontSize = 12.sp,
              color = WorldGitPalette.TextPrimary,
              maxLines = 1,
              overflow = TextOverflow.Ellipsis,
              modifier = Modifier.weight(1f),
            )
          }
        }
      }
    }
  }
}

@Composable
private fun UndoDetailContent(ui: AppUiState, onUndo: () -> Unit) {
  Column(
    modifier = Modifier
      .fillMaxSize()
      .padding(14.dp),
    verticalArrangement = Arrangement.SpaceBetween,
  ) {
    Row(
      modifier = Modifier.fillMaxWidth(),
      verticalAlignment = Alignment.CenterVertically,
      horizontalArrangement = Arrangement.SpaceBetween,
    ) {
      Column {
        Text("WorldGit History (Ctrl+Z)", style = MaterialTheme.typography.titleMedium)
        Text("Revert recent tracked changes in your room", fontSize = 12.sp, color = WorldGitPalette.TextSecondary)
      }
      Button(
        onClick = onUndo,
        shape = RoundedCornerShape(999.dp),
        colors = ButtonDefaults.buttonColors(containerColor = WorldGitPalette.Cyan, contentColor = WorldGitPalette.Bg),
      ) {
        Icon(Icons.AutoMirrored.Filled.Undo, contentDescription = null, modifier = Modifier.size(16.dp))
        Spacer(Modifier.width(6.dp))
        Text("Undo Latest", fontWeight = FontWeight.Bold, fontSize = 13.sp)
      }
    }

    LazyColumn(
      modifier = Modifier
        .fillMaxWidth()
        .weight(1f)
        .padding(top = 8.dp),
      verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
      items(ui.undoSteps.take(6)) { step ->
        Surface(
          shape = RoundedCornerShape(10.dp),
          color = WorldGitPalette.Surface,
        ) {
          Text(
            text = step,
            modifier = Modifier
              .fillMaxWidth()
              .padding(horizontal = 10.dp, vertical = 7.dp),
            fontSize = 12.sp,
            color = WorldGitPalette.TextPrimary,
          )
        }
      }
    }
  }
}

@Composable
private fun ScanDetailContent(ui: AppUiState, onToggleScan: () -> Unit) {
  Column(
    modifier = Modifier
      .fillMaxSize()
      .padding(14.dp),
    verticalArrangement = Arrangement.SpaceBetween,
  ) {
    Row(
      modifier = Modifier.fillMaxWidth(),
      horizontalArrangement = Arrangement.SpaceBetween,
      verticalAlignment = Alignment.CenterVertically,
    ) {
      Column {
        Text("Room Spatial Scan", style = MaterialTheme.typography.titleMedium)
        Text(
          text = ui.imu.statusNote,
          fontSize = 12.sp,
          color = if (ui.imu.recording) WorldGitPalette.Emerald else WorldGitPalette.TextSecondary,
        )
      }
      Button(
        onClick = onToggleScan,
        shape = RoundedCornerShape(999.dp),
        colors = ButtonDefaults.buttonColors(
          containerColor = if (ui.imu.recording) WorldGitPalette.Emerald else WorldGitPalette.Cyan,
          contentColor = WorldGitPalette.Bg,
        ),
      ) {
        Icon(
          imageVector = if (ui.imu.recording) Icons.Filled.Stop else Icons.Filled.Radar,
          contentDescription = null,
          modifier = Modifier.size(16.dp),
        )
        Spacer(Modifier.width(6.dp))
        Text(if (ui.imu.recording) "Stop Scan" else "Start Scan", fontWeight = FontWeight.Bold, fontSize = 13.sp)
      }
    }

    Row(
      modifier = Modifier.fillMaxWidth(),
      horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
      MetricPill("HEADING", "${ui.compass.headingDeg.roundToInt()}° ${ui.compass.cardinal}", Modifier.weight(1f))
      MetricPill("SAMPLES", "${ui.imu.sampleCount}", Modifier.weight(1f))
      MetricPill("SOURCE", ui.compass.sensorSource, Modifier.weight(1f))
    }
  }
}

@Composable
private fun MetricPill(label: String, value: String, modifier: Modifier = Modifier) {
  Surface(
    modifier = modifier,
    shape = RoundedCornerShape(12.dp),
    color = WorldGitPalette.Surface,
    border = BorderStroke(1.dp, WorldGitPalette.Border),
  ) {
    Column(modifier = Modifier.padding(horizontal = 10.dp, vertical = 8.dp)) {
      Text(label, style = MaterialTheme.typography.labelSmall, color = WorldGitPalette.TextMuted, fontSize = 10.sp)
      Text(value, fontWeight = FontWeight.Bold, fontSize = 14.sp, color = WorldGitPalette.Cyan, maxLines = 1)
    }
  }
}

@Composable
private fun WorldGitBottomDock(
  activeMode: HudMode,
  onModeTap: (HudMode) -> Unit,
) {
  Surface(
    color = WorldGitPalette.Surface,
    border = BorderStroke(1.dp, WorldGitPalette.Border),
    shape = RoundedCornerShape(topStart = 22.dp, topEnd = 22.dp),
    modifier = Modifier.fillMaxWidth(),
  ) {
    Row(
      modifier = Modifier
        .fillMaxWidth()
        .navigationBarsPadding()
        .padding(horizontal = 12.dp, vertical = 10.dp),
      horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
      DockPill(HudMode.ASK, Icons.Filled.Mic, activeMode == HudMode.ASK) { onModeTap(HudMode.ASK) }
      DockPill(HudMode.PINS, Icons.Filled.PushPin, activeMode == HudMode.PINS) { onModeTap(HudMode.PINS) }
      DockPill(HudMode.UNDO, Icons.AutoMirrored.Filled.Undo, activeMode == HudMode.UNDO) { onModeTap(HudMode.UNDO) }
      DockPill(HudMode.SCAN, Icons.Filled.Radar, activeMode == HudMode.SCAN) { onModeTap(HudMode.SCAN) }
    }
  }
}

@Composable
private fun RowScope.DockPill(
  mode: HudMode,
  icon: ImageVector,
  selected: Boolean,
  onClick: () -> Unit,
) {
  Surface(
    shape = RoundedCornerShape(14.dp),
    color = if (selected) WorldGitPalette.Cyan.copy(alpha = 0.16f) else WorldGitPalette.Card,
    border = BorderStroke(
      width = if (selected) 1.5.dp else 1.dp,
      color = if (selected) WorldGitPalette.Cyan else WorldGitPalette.Border,
    ),
    modifier = Modifier
      .weight(1f)
      .clip(RoundedCornerShape(14.dp))
      .clickable(onClick = onClick),
  ) {
    Column(
      modifier = Modifier.padding(vertical = 8.dp),
      horizontalAlignment = Alignment.CenterHorizontally,
      verticalArrangement = Arrangement.spacedBy(3.dp),
    ) {
      Icon(
        imageVector = icon,
        contentDescription = mode.label,
        tint = if (selected) WorldGitPalette.Cyan else WorldGitPalette.TextSecondary,
        modifier = Modifier.size(18.dp),
      )
      Text(
        text = mode.label,
        fontSize = 11.sp,
        fontWeight = if (selected) FontWeight.Bold else FontWeight.Medium,
        color = if (selected) WorldGitPalette.TextPrimary else WorldGitPalette.TextSecondary,
      )
    }
  }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun SettingsModalSheet(
  ui: AppUiState,
  onDismiss: () -> Unit,
  onApplyHost: (String) -> Unit,
  onCalibrationChange: (HudCalibration) -> Unit,
  onSaveCalibration: () -> Unit,
  onToggleTts: () -> Unit,
  onTogglePrivacy: () -> Unit,
  onClearNotes: () -> Unit,
) {
  var hostDraft by remember(ui.host) { mutableStateOf(ui.host) }

  ModalBottomSheet(
    onDismissRequest = onDismiss,
    containerColor = WorldGitPalette.Surface,
    contentColor = WorldGitPalette.TextPrimary,
    dragHandle = { BottomSheetDefaults.DragHandle(color = WorldGitPalette.BorderBright) },
  ) {
    Column(
      modifier = Modifier
        .fillMaxWidth()
        .verticalScroll(rememberScrollState())
        .padding(horizontal = 20.dp, vertical = 8.dp)
        .navigationBarsPadding(),
      verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
      Text("WorldGit Preferences & Optical Calibration", style = MaterialTheme.typography.titleLarge)

      Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text("Backend Host (USB ADB = 127.0.0.1)", style = MaterialTheme.typography.labelSmall)
        Row(
          verticalAlignment = Alignment.CenterVertically,
          horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
          OutlinedTextField(
            value = hostDraft,
            onValueChange = { hostDraft = it },
            singleLine = true,
            shape = RoundedCornerShape(12.dp),
            modifier = Modifier.weight(1f),
          )
          Button(
            onClick = { onApplyHost(hostDraft) },
            colors = ButtonDefaults.buttonColors(containerColor = WorldGitPalette.Cyan, contentColor = WorldGitPalette.Bg),
          ) {
            Text("Apply", fontWeight = FontWeight.Bold)
          }
        }
      }

      HorizontalDivider(color = WorldGitPalette.Border)

      Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Row(
          modifier = Modifier.fillMaxWidth(),
          horizontalArrangement = Arrangement.SpaceBetween,
          verticalAlignment = Alignment.CenterVertically,
        ) {
          Text("Optical Screen Calibration", style = MaterialTheme.typography.titleMedium)
          TextButton(onClick = onSaveCalibration) {
            Text("Save to Glasses", color = WorldGitPalette.Cyan, fontWeight = FontWeight.Bold)
          }
        }

        Row(
          modifier = Modifier.fillMaxWidth(),
          horizontalArrangement = Arrangement.SpaceBetween,
          verticalAlignment = Alignment.CenterVertically,
        ) {
          Text("Show Alignment Crosshair", fontSize = 13.sp, color = WorldGitPalette.TextSecondary)
          Switch(
            checked = ui.calibration.showGuide,
            onCheckedChange = { onCalibrationChange(ui.calibration.copy(showGuide = it)) },
          )
        }

        CalibrationSlider(
          label = "Scale",
          value = ui.calibration.size,
          range = 0.4f..1.4f,
          onChange = { onCalibrationChange(ui.calibration.copy(size = it, showGuide = true)) },
        )
        CalibrationSlider(
          label = "Horizontal Offset",
          value = ui.calibration.horizontal,
          range = -800f..800f,
          onChange = { onCalibrationChange(ui.calibration.copy(horizontal = it, showGuide = true)) },
        )
        CalibrationSlider(
          label = "Vertical Offset",
          value = ui.calibration.vertical,
          range = -800f..800f,
          onChange = { onCalibrationChange(ui.calibration.copy(vertical = it, showGuide = true)) },
        )
        CalibrationSlider(
          label = "Roll Trim",
          value = ui.calibration.roll,
          range = -15f..15f,
          onChange = { onCalibrationChange(ui.calibration.copy(roll = it, showGuide = true)) },
        )
      }

      HorizontalDivider(color = WorldGitPalette.Border)

      Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
      ) {
        Column {
          Text("Voice Readout (TTS)", style = MaterialTheme.typography.titleMedium)
          Text("Speak Gemini answers aloud", fontSize = 12.sp, color = WorldGitPalette.TextSecondary)
        }
        Switch(checked = ui.ttsEnabled, onCheckedChange = { onToggleTts() })
      }

      Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
      ) {
        Column {
          Text("Pause Room Memory", style = MaterialTheme.typography.titleMedium)
          Text("Temporarily hide spatial overlay recall", fontSize = 12.sp, color = WorldGitPalette.TextSecondary)
        }
        Switch(checked = ui.memoryPaused, onCheckedChange = { onTogglePrivacy() })
      }

      OutlinedButton(
        onClick = onClearNotes,
        shape = RoundedCornerShape(12.dp),
        border = BorderStroke(1.dp, WorldGitPalette.Rose.copy(alpha = 0.6f)),
        modifier = Modifier.fillMaxWidth(),
      ) {
        Icon(Icons.Outlined.DeleteOutline, contentDescription = null, tint = WorldGitPalette.Rose)
        Spacer(Modifier.width(8.dp))
        Text("Clear Saved Pins (${ui.savedNotes.size})", color = WorldGitPalette.Rose)
      }

      Spacer(Modifier.height(12.dp))
    }
  }
}

@Composable
private fun CalibrationSlider(
  label: String,
  value: Float,
  range: ClosedFloatingPointRange<Float>,
  onChange: (Float) -> Unit,
) {
  Column {
    Row(
      modifier = Modifier.fillMaxWidth(),
      horizontalArrangement = Arrangement.SpaceBetween,
    ) {
      Text(label, fontSize = 12.sp, color = WorldGitPalette.TextSecondary)
      Text("%.2f".format(value), style = MaterialTheme.typography.labelSmall, color = WorldGitPalette.Cyan)
    }
    Slider(
      value = value.coerceIn(range.start, range.endInclusive),
      onValueChange = onChange,
      valueRange = range,
      colors = SliderDefaults.colors(
        thumbColor = WorldGitPalette.Cyan,
        activeTrackColor = WorldGitPalette.Cyan,
        inactiveTrackColor = WorldGitPalette.BorderBright,
      ),
    )
  }
}
