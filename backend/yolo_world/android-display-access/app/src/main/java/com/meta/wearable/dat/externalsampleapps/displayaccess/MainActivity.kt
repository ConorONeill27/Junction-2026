/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 *
 * This source code is licensed under the license found in the
 * LICENSE file in the root directory of this source tree.
 */

package com.meta.wearable.dat.externalsampleapps.displayaccess

import android.Manifest.permission.BLUETOOTH
import android.Manifest.permission.BLUETOOTH_CONNECT
import android.Manifest.permission.INTERNET
import android.os.Bundle
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts.RequestMultiplePermissions
import androidx.activity.viewModels
import com.meta.wearable.dat.core.Wearables
import com.meta.wearable.dat.externalsampleapps.displayaccess.ui.AppScaffold
import com.meta.wearable.dat.externalsampleapps.displayaccess.ui.DisplayAccessTheme
import com.meta.wearable.dat.externalsampleapps.displayaccess.wearables.WearablesViewModel

class MainActivity : ComponentActivity() {
  companion object {
    private val PERMISSIONS = arrayOf(BLUETOOTH, BLUETOOTH_CONNECT, INTERNET)
  }

  private val wearablesViewModel: WearablesViewModel by viewModels()
  private val permissionsLauncher =
      registerForActivityResult(RequestMultiplePermissions()) { results ->
        if (results.entries.all { it.value }) {
          initializeWearables()
        } else {
          Toast.makeText(this, "All permissions are required", Toast.LENGTH_SHORT).show()
        }
      }

  override fun onCreate(savedInstanceState: Bundle?) {
    super.onCreate(savedInstanceState)
    enableEdgeToEdge()
    setContent { DisplayAccessTheme { AppScaffold(wearablesViewModel = wearablesViewModel) } }
  }

  private fun initializeWearables() {
    Wearables.initialize(this).onSuccess { wearablesViewModel.startObserving() }
      .onFailure { error, _ ->
        android.util.Log.e("WorldGitSetup", "Meta SDK initialization failed: $error")
        Toast.makeText(this, "Meta setup failed: $error", Toast.LENGTH_LONG).show()
      }
  }

  override fun onStart() {
    super.onStart()
    if (PERMISSIONS.all { checkSelfPermission(it) == android.content.pm.PackageManager.PERMISSION_GRANTED }) initializeWearables()
    else permissionsLauncher.launch(PERMISSIONS)
  }
}
