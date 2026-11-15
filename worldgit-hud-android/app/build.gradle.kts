import java.util.Properties
import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
  alias(libs.plugins.android.application)
  alias(libs.plugins.jetbrains.kotlin.android)
  alias(libs.plugins.compose.compiler)
}

val localProperties =
    Properties().apply {
      val file = rootDir.resolve("local.properties")
      if (file.exists()) {
        file.inputStream().use { load(it) }
      }
    }

android {
  namespace = "com.worldgit.hud"
  compileSdk = 36

  buildFeatures { buildConfig = true }

  defaultConfig {
    applicationId = "com.worldgit.hud"
    minSdk = 31
    targetSdk = 36
    versionCode = 1
    versionName = "1.0.0"

    manifestPlaceholders["mwdat_application_id"] =
        localProperties.getProperty("mwdat_application_id", "0")
    manifestPlaceholders["mwdat_client_token"] =
        localProperties.getProperty("mwdat_client_token", "0")
    val apiToken =
        (localProperties.getProperty("worldgit_api_token") ?: System.getenv("WORLDGIT_API_TOKEN") ?: "")
            .replace("\"", "\\\"")
    buildConfigField("String", "WORLDGIT_API_TOKEN", "\"$apiToken\"")
  }

  buildTypes {
    release {
      isMinifyEnabled = true
      proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
    }
  }

  compileOptions {
    sourceCompatibility = JavaVersion.VERSION_17
    targetCompatibility = JavaVersion.VERSION_17
  }

  packaging { resources { excludes += "/META-INF/{AL2.0,LGPL2.1}" } }
}

kotlin { compilerOptions { jvmTarget = JvmTarget.JVM_17 } }

dependencies {
  implementation(libs.androidx.activity.compose)
  implementation(platform(libs.androidx.compose.bom))
  implementation(libs.androidx.lifecycle.runtime.compose)
  implementation(libs.androidx.lifecycle.viewmodel.compose)
  implementation(libs.androidx.material.icons.extended)
  implementation(libs.androidx.material3)
  implementation("com.meta.wearable:mwdat-motion:1.0.1")
  implementation("com.meta.wearable:mwdat-camera:1.0.1")
  implementation("com.meta.wearable:mwdat-inputs:1.0.1")
  implementation(libs.mwdat.core)
  implementation(libs.mwdat.display)
}
