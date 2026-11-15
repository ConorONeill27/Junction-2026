package com.worldgit.hud

import android.Manifest.permission.BLUETOOTH
import android.Manifest.permission.BLUETOOTH_CONNECT
import android.Manifest.permission.INTERNET
import android.Manifest.permission.RECORD_AUDIO
import android.content.pm.PackageManager
import android.graphics.Color as AndroidColor
import android.os.Bundle
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts.RequestMultiplePermissions
import androidx.activity.viewModels
import com.worldgit.hud.ui.MainScreen
import com.worldgit.hud.ui.WorldGitTheme

class MainActivity : ComponentActivity() {
  companion object {
    private val REQUIRED_PERMISSIONS = arrayOf(
      BLUETOOTH,
      BLUETOOTH_CONNECT,
      INTERNET,
      RECORD_AUDIO,
    )
  }

  private val viewModel: WorldGitViewModel by viewModels()

  private val permissionsLauncher = registerForActivityResult(RequestMultiplePermissions()) { results ->
    if (results.values.any { !it }) {
      Toast.makeText(
        this,
        "Bluetooth & Microphone permissions are needed for Meta glasses",
        Toast.LENGTH_SHORT,
      ).show()
    }
  }

  override fun onCreate(savedInstanceState: Bundle?) {
    super.onCreate(savedInstanceState)
    enableEdgeToEdge(
      statusBarStyle = SystemBarStyle.dark(AndroidColor.TRANSPARENT),
      navigationBarStyle = SystemBarStyle.dark(AndroidColor.TRANSPARENT),
    )
    setContent {
      WorldGitTheme {
        MainScreen(viewModel = viewModel)
      }
    }
  }

  override fun onStart() {
    super.onStart()
    val missing = REQUIRED_PERMISSIONS.filter {
      checkSelfPermission(it) != PackageManager.PERMISSION_GRANTED
    }
    if (missing.isNotEmpty()) {
      permissionsLauncher.launch(missing.toTypedArray())
    }
  }
}
