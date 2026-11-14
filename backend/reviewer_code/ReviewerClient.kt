package com.meta.wearable.dat.externalsampleapps.displayaccess

import android.Manifest
import android.app.Application
import android.content.pm.PackageManager
import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import android.os.Looper
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URI
import kotlin.math.atan2

/** Phone GPS is separate from the glasses heading; never substitute phone attitude. */
class ReviewerClient(private val app: Application) {
  @Volatile private var heading: Double? = null
  @Volatile private var headingAt = 0L
  @Volatile private var location: Location? = null
  private val manager = app.getSystemService(LocationManager::class.java)
  private val listener = LocationListener { location = it }
  private var listening = false

  fun orientation(rotation: FloatArray?) {
    if (rotation == null) { heading = null; return }
    // Forward column of the existing image-to-world matrix; X east, Y north.
    if (rotation.size == 9 && rotation.all { it.isFinite() }) {
      val east = rotation[2].toDouble()
      val north = rotation[5].toDouble()
      if (east*east + north*north < 0.01) { heading = null; return }
      heading = (Math.toDegrees(atan2(east, north)) + 360) % 360
      headingAt = android.os.SystemClock.elapsedRealtime()
    }
  }

  fun stop() {
    if (listening) manager.removeUpdates(listener)
    listening = false
    location = null
  }

  fun poll(base: String): YoloState {
    val config = JSONObject(request("$base/api/settings"))
    val simulated = config.getBoolean("simulated")
    if (!simulated) {
      if (app.checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) != PackageManager.PERMISSION_GRANTED)
        return YoloState("Enable precise phone location for restaurant reviews")
      if (!listening) {
        if (!manager.isProviderEnabled(LocationManager.GPS_PROVIDER)) return YoloState("Enable phone GPS")
        manager.requestLocationUpdates(LocationManager.GPS_PROVIDER, 1000L, 0f, listener, Looper.getMainLooper())
        listening = true
      }
    }
    val fix = location
    val now = android.os.SystemClock.elapsedRealtime()
    if (!simulated && (fix == null || now - fix.elapsedRealtimeNanos / 1_000_000 > 15000 || !fix.hasAccuracy() || fix.accuracy > 10f))
      return YoloState("Waiting for fresh phone GPS accurate to 10 m")
    val compass = heading
    if (!simulated && (compass == null || now - headingAt > 3000))
      return YoloState("Waiting for glasses heading; connect glasses motion")
    val body = JSONObject().put("latitude", fix?.latitude ?: 0.0)
      .put("longitude", fix?.longitude ?: 0.0).put("heading", compass ?: 0.0)
    val data = JSONObject(request("$base/api/reviews", body)).getJSONArray("reviews")
    val items = if (data.length() == 0) emptyList() else {
      val item = data.getJSONObject(0)
      val stars = "★".repeat(item.getInt("rating"))
      listOf(YoloItem("reviewer-bar", "$stars ${item.getString("name")}", 1.0,
        listOf(360.0, 190.0, 570.0, 220.0),
        "${item.getString("title")}: ${item.getString("review")}"))
    }
    return YoloState(if (simulated) "Reviewer: SIMULATED location / heading" else "Reviewer: phone GPS / glasses heading %.1f?".format(compass ?: 0.0), items, false, 600, 600)
  }

  private fun request(url: String, body: JSONObject? = null): String {
    val connection = URI(url).toURL().openConnection() as HttpURLConnection
    try {
      connection.connectTimeout = 2000
      connection.readTimeout = 2000
      if (body != null) {
        connection.requestMethod = "POST"
        connection.doOutput = true
        connection.setRequestProperty("Content-Type", "application/json")
        connection.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
      }
      return connection.inputStream.bufferedReader().use { it.readText() }
    } finally { connection.disconnect() }
  }
}
