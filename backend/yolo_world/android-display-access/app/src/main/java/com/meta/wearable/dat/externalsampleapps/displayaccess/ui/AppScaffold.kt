/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 *
 * This source code is licensed under the license found in the
 * LICENSE file in the root directory of this source tree.
 */

package com.meta.wearable.dat.externalsampleapps.displayaccess.ui

import androidx.compose.runtime.DisposableEffect
import androidx.activity.compose.LocalActivity
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.outlined.RemoveRedEye
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.setValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import com.meta.wearable.dat.core.types.RegistrationState
import com.meta.wearable.dat.core.Wearables
import com.meta.wearable.dat.display.types.DisplayState
import com.meta.wearable.dat.externalsampleapps.displayaccess.R
import com.meta.wearable.dat.externalsampleapps.displayaccess.SampleApp
import com.meta.wearable.dat.externalsampleapps.displayaccess.YoloViewModel
import com.meta.wearable.dat.externalsampleapps.displayaccess.AudioClipsViewModel
import com.meta.wearable.dat.externalsampleapps.displayaccess.LiveTranscriptionViewModel
import com.meta.wearable.dat.externalsampleapps.displayaccess.display.DisplayViewModel
import com.meta.wearable.dat.externalsampleapps.displayaccess.wearables.DeveloperPreviewMode
import com.meta.wearable.dat.externalsampleapps.displayaccess.wearables.WearablesViewModel
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.mapNotNull
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeout

object Routes {
  const val YOLO = "yolo"
  const val CONNECT = "connect"
  const val SAMPLES_LIST = "samples_list"
}

private val BackgroundColor = Color(0xFFF2F2F7)
private val TabContainerColor = Color(0xFFF6F6FA)
private val ActiveTabColor = Color(0xFFE7E7ED)
private val ActiveBlue = Color(0xFF3478F6)

@Composable
fun AppScaffold(
    wearablesViewModel: WearablesViewModel,
    modifier: Modifier = Modifier,
) {
  val navController = rememberNavController()
  val wearablesState by wearablesViewModel.uiState.collectAsStateWithLifecycle()
  val displayViewModel: DisplayViewModel = viewModel()
  val yoloViewModel: YoloViewModel = viewModel()
  val audioViewModel: AudioClipsViewModel = viewModel()
  val speechViewModel: LiveTranscriptionViewModel = viewModel()
  val speechState by speechViewModel.state.collectAsStateWithLifecycle()
  var pendingNoteId by remember { mutableStateOf<String?>(null) }
  var permissionForSpeech by remember { mutableStateOf(false) }
  val audioState by audioViewModel.state.collectAsStateWithLifecycle()
  val yoloState by yoloViewModel.state.collectAsStateWithLifecycle()
  val cursorState by yoloViewModel.cursor.collectAsStateWithLifecycle()
  val cameraPreview by yoloViewModel.preview.collectAsStateWithLifecycle()
  val context = androidx.compose.ui.platform.LocalContext.current
  val permission = androidx.activity.compose.rememberLauncherForActivityResult(
    androidx.activity.result.contract.ActivityResultContracts.RequestPermission()
  ) { granted -> if (granted) yoloViewModel.startCamera() }
  val audioPermission = androidx.activity.compose.rememberLauncherForActivityResult(
    androidx.activity.result.contract.ActivityResultContracts.RequestPermission()
  ) { granted ->
    if (permissionForSpeech) {
      if (granted) speechViewModel.start(yoloViewModel.savedAddress, pendingNoteId)
      else speechViewModel.unavailable("Microphone permission is needed for live transcription")
    } else {
      if (granted) audioViewModel.toggle(yoloViewModel.savedAddress) else audioViewModel.permissionDenied()
    }
    permissionForSpeech = false
  }
  val audioPinch: () -> Unit = {
    if (!speechViewModel.state.value.active) {
      permissionForSpeech = false
      if (context.checkSelfPermission(android.Manifest.permission.RECORD_AUDIO) == android.content.pm.PackageManager.PERMISSION_GRANTED)
        audioViewModel.toggle(yoloViewModel.savedAddress)
      else audioPermission.launch(android.Manifest.permission.RECORD_AUDIO)
    }
  }
  val startSpeechFor: (String?) -> Unit = { noteId ->
    pendingNoteId = noteId
    if (audioViewModel.state.value.recording) speechViewModel.unavailable("Stop the audio clip recording before live transcription")
    else if (context.checkSelfPermission(android.Manifest.permission.RECORD_AUDIO) == android.content.pm.PackageManager.PERMISSION_GRANTED)
      speechViewModel.start(yoloViewModel.savedAddress, noteId)
    else {
      permissionForSpeech = true
      audioPermission.launch(android.Manifest.permission.RECORD_AUDIO)
    }
  }
  val noteAction: (String) -> Unit = { noteId ->
    val speech = speechViewModel.state.value
    if (speech.active) {
      if (speech.noteId == noteId) speechViewModel.finish()
    } else startSpeechFor(noteId)
  }
  val cursorPinch: () -> Unit = {
    val speech = speechViewModel.state.value
    val noteHit = com.meta.wearable.dat.externalsampleapps.displayaccess.yoloNoteAt(
      yoloViewModel.state.value, yoloViewModel.cursor.value, speech)
    if (speech.active) speechViewModel.finish()
    else if (noteHit != null) noteAction(noteHit)
    else if (yoloViewModel.pinchCursor()) startSpeechFor(null)
  }
  DisposableEffect(displayViewModel, audioPermission) {
    displayViewModel.onGlassesMotion = yoloViewModel::setGlassesMotion
    displayViewModel.onGlassesMotionStopped = yoloViewModel::clearGlassesMotion
    displayViewModel.onAudioPinch = audioPinch
    displayViewModel.onCursorPinch = cursorPinch
    onDispose {
      displayViewModel.onGlassesMotion = null
      displayViewModel.onGlassesMotionStopped = null
      yoloViewModel.clearGlassesMotion()
      displayViewModel.onAudioPinch = null
      displayViewModel.onCursorPinch = null
    }
  }
  LaunchedEffect(audioState) { displayViewModel.updateAudio(audioState) }
  LaunchedEffect(cursorState) { displayViewModel.updateCursor(cursorState) }
  LaunchedEffect(speechState) { displayViewModel.updateSpeech(speechState) }
  val lifecycleOwner = androidx.lifecycle.compose.LocalLifecycleOwner.current
  DisposableEffect(lifecycleOwner) {
    val observer = androidx.lifecycle.LifecycleEventObserver { _, event ->
      if (event == androidx.lifecycle.Lifecycle.Event.ON_RESUME &&
          context.checkSelfPermission(android.Manifest.permission.CAMERA) == android.content.pm.PackageManager.PERMISSION_GRANTED) yoloViewModel.startCamera()
      if (event == androidx.lifecycle.Lifecycle.Event.ON_PAUSE) yoloViewModel.stopCamera()
      if (event == androidx.lifecycle.Lifecycle.Event.ON_STOP) {
        audioViewModel.finishRecording()
        speechViewModel.cancel()
      }
    }
    lifecycleOwner.lifecycle.addObserver(observer)
    onDispose { lifecycleOwner.lifecycle.removeObserver(observer); yoloViewModel.stopCamera() }
  }
  LaunchedEffect(Unit) { permission.launch(android.Manifest.permission.CAMERA) }
  LaunchedEffect(yoloState) { displayViewModel.updateYolo(yoloState) }
  val displayState by displayViewModel.uiState.collectAsStateWithLifecycle()
  val mockGlasses by wearablesViewModel.mockDisplayGlasses.collectAsStateWithLifecycle()
  val chromePreviewInfo by wearablesViewModel.chromePreviewInfo.collectAsStateWithLifecycle()
  val developerPreviewMode by wearablesViewModel.developerPreviewMode.collectAsStateWithLifecycle()
  val isDeveloperPreviewChanging by
      wearablesViewModel.isDeveloperPreviewChanging.collectAsStateWithLifecycle()
  val developerPreviewErrorMessage by
      wearablesViewModel.developerPreviewErrorMessage.collectAsStateWithLifecycle()
  val isCapabilityReady = displayState.displayState == DisplayState.STARTED
  val previewDisplayKit =
      remember(mockGlasses, isCapabilityReady, developerPreviewMode) {
        mockGlasses
            ?.takeIf {
              isCapabilityReady && developerPreviewMode == DeveloperPreviewMode.IN_APP
            }
            ?.services
            ?.display
      }
  val snackbarHostState = remember { SnackbarHostState() }
  val activity = LocalActivity.current
  val backStackEntry by navController.currentBackStackEntryAsState()
  val currentRoute = backStackEntry?.destination?.route ?: Routes.CONNECT

  fun openSamples() {
    navController.navigate(Routes.SAMPLES_LIST) {
      popUpTo(Routes.CONNECT)
      launchSingleTop = true
    }
  }

  fun openSettings() {
    navController.navigate(Routes.CONNECT) {
      popUpTo(Routes.CONNECT)
      launchSingleTop = true
    }
  }

  LaunchedEffect(wearablesViewModel, displayViewModel) {
    combine(
        wearablesViewModel.uiState,
        displayViewModel.uiState,
        wearablesViewModel.developerPreviewMode,
    ) { currentWearablesState, currentDisplayState, previewMode ->
      val registrationStopsSession =
          currentWearablesState.registrationState == RegistrationState.UNAVAILABLE ||
              currentWearablesState.registrationState == RegistrationState.AVAILABLE
      registrationStopsSession && currentDisplayState.isSessionActive && previewMode == null
    }
        .distinctUntilChanged()
        .collect { shouldStopSession ->
          if (shouldStopSession) displayViewModel.stopSession()
        }
  }

  LaunchedEffect(displayViewModel, snackbarHostState) {
    displayViewModel.uiState
        .mapNotNull { it.snackbarMessage }
        .collectLatest { message ->
          displayViewModel.clearSnackbarMessage()
          snackbarHostState.showSnackbar(message)
        }
  }

  Scaffold(
      modifier = modifier,
      containerColor = BackgroundColor,
      snackbarHost = { SnackbarHost(snackbarHostState) },
      bottomBar = {
        BottomTabBar(
            currentRoute = currentRoute,
            samplesEnabled = true,
            onOpenSamples = { navController.navigate(Routes.YOLO) { launchSingleTop = true } },
            onOpenSettings = ::openSettings,
        )
      },
  ) { contentPadding ->
    NavHost(
        navController = navController,
        startDestination = Routes.YOLO,
        modifier = Modifier.fillMaxSize().padding(contentPadding),
    ) {
      composable(Routes.YOLO) {
        YoloScreen(yoloState, cameraPreview, yoloViewModel.savedAddress, { address -> yoloViewModel.connect(address); permission.launch(android.Manifest.permission.CAMERA) }, ::openSamples,
          audioState, audioPinch, { audioViewModel.retryUploads(yoloViewModel.savedAddress) },
          cursorState, cursorPinch, speechState, noteAction)
      }
      composable(Routes.CONNECT) {
        ConnectScreen(
            uiState = wearablesState,
            isDatAppUpdateRequired = displayState.isDatAppUpdateRequired,
            mockDisplayDeviceId = mockGlasses?.deviceIdentifier,
            onRegister = { activity?.let { wearablesViewModel.startRegistration(it) } },
            onUnregister = {
              activity?.let { wearablesViewModel.startUnregistration(it) }
            },
            onOpenFirmwareUpdate = { activity?.let { wearablesViewModel.openFirmwareUpdate(it) } },
            onOpenDatAppUpdate = {
              activity?.let { wearablesViewModel.openDATGlassesAppUpdate(it) }
            },
        )
      }

      composable(Routes.SAMPLES_LIST) {
        val isDeveloperPreviewBusy =
            isDeveloperPreviewChanging ||
                displayState.isStartingSession ||
                displayState.isStoppingSession ||
                displayState.isPreparingDisplay ||
                displayState.isSending
        val previewDeviceIdentifier = mockGlasses?.deviceIdentifier
        val coroutineScope = rememberCoroutineScope()

        fun updateDeveloperPreview(mode: DeveloperPreviewMode?) {
          coroutineScope.launch {
            try {
              if (!displayViewModel.stopSession()) return@launch
              val deviceId = wearablesViewModel.setDeveloperPreviewMode(mode)
              if (mode != null && deviceId != null) {
                // Mock pairing announces the device asynchronously; wait before selecting it.
                withTimeout(8000) { Wearables.devices.first { deviceId in it } }
                displayViewModel.sendSampleToPreview(SampleApp.CAR_MAINTENANCE, deviceId)
              }
            } catch (error: CancellationException) {
              throw error
            } catch (error: Exception) {
              wearablesViewModel.reportDeveloperPreviewFailure(error)
            }
          }
        }

        SamplesListScreen(
            isTryItEnabled = !isDeveloperPreviewBusy,
            developerPreviewMode = developerPreviewMode,
            isDeveloperPreviewBusy = isDeveloperPreviewBusy,
            hasLoadedDeveloperPreview =
                developerPreviewMode != null &&
                    previewDeviceIdentifier != null &&
                    displayState.hasSentContent &&
                    displayState.selectedDeviceId == previewDeviceIdentifier,
            chromePreviewCommand = chromePreviewInfo?.adbCommand,
            chromePreviewUrl = chromePreviewInfo?.url,
            developerPreviewErrorMessage =
                developerPreviewErrorMessage ?: displayState.errorMessage,
            onSampleSelected = { sample -> displayViewModel.sendSampleToDisplay(sample) },
            onStartDeveloperPreview = ::updateDeveloperPreview,
            onStopDeveloperPreview = { updateDeveloperPreview(null) },
            previewDisplayKit = previewDisplayKit,
        )
      }

      SampleApp.entries.forEach { sample ->
        composable(sample.route) {
          SamplePlaceholderScreen(
              sample = sample,
              onBack = { navController.popBackStack() },
          )
        }
      }
    }
  }
}

@Composable
private fun BottomTabBar(
    currentRoute: String,
    samplesEnabled: Boolean,
    onOpenSamples: () -> Unit,
    onOpenSettings: () -> Unit,
) {
  Row(
      modifier =
          Modifier.fillMaxWidth()
              .navigationBarsPadding()
              .padding(horizontal = 72.dp, vertical = 18.dp)
              .clip(RoundedCornerShape(999.dp))
              .background(TabContainerColor)
              .padding(8.dp),
      horizontalArrangement = Arrangement.spacedBy(8.dp),
  ) {
    BottomTab(
        label = stringResource(R.string.samples_tab),
        icon = {
          Icon(
              imageVector = Icons.Outlined.RemoveRedEye,
              contentDescription = null,
              modifier = Modifier.size(26.dp),
          )
        },
        selected = currentRoute == Routes.SAMPLES_LIST,
        enabled = samplesEnabled,
        activeContentColor = Color.Black,
        inactiveContentColor = Color.Black.copy(alpha = 0.35f),
        onClick = onOpenSamples,
        modifier = Modifier.weight(1f),
    )
    BottomTab(
        label = stringResource(R.string.settings_tab),
        icon = {
          Icon(
              imageVector = Icons.Filled.Settings,
              contentDescription = null,
              modifier = Modifier.size(28.dp),
          )
        },
        selected = currentRoute == Routes.CONNECT,
        enabled = true,
        activeContentColor = ActiveBlue,
        inactiveContentColor = Color.Black.copy(alpha = 0.35f),
        onClick = onOpenSettings,
        modifier = Modifier.weight(1f),
    )
  }
}

@Composable
private fun BottomTab(
    label: String,
    icon: @Composable () -> Unit,
    selected: Boolean,
    enabled: Boolean,
    activeContentColor: Color,
    inactiveContentColor: Color,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
  Column(
      modifier =
          modifier
              .clip(CircleShape)
              .background(if (selected) ActiveTabColor else Color.Transparent)
              .clickable(enabled = enabled, onClick = onClick)
              .padding(vertical = 14.dp),
      horizontalAlignment = Alignment.CenterHorizontally,
  ) {
    val contentColor =
        when {
          !enabled -> inactiveContentColor
          selected -> activeContentColor
          else -> inactiveContentColor
        }
    Row(verticalAlignment = Alignment.CenterVertically) {
      androidx.compose.runtime.CompositionLocalProvider(
          androidx.compose.material3.LocalContentColor provides contentColor,
      ) {
        icon()
      }
    }
    Text(
        text = label,
        color = contentColor,
        style = MaterialTheme.typography.titleMedium,
        fontWeight = FontWeight.Medium,
        modifier = Modifier.padding(top = 6.dp),
    )
  }
}
