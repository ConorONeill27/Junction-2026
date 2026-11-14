package com.meta.wearable.dat.externalsampleapps.displayaccess

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.*
import android.hardware.camera2.*
import android.media.Image
import android.media.ImageReader
import android.os.Handler
import java.io.ByteArrayOutputStream

/** Camera2 exposes 60 FPS modes hidden by the legacy camera API on some phones. */
data class CameraFrame(val bitmap: Bitmap, val jpeg: ByteArray, val rotation: Int)

class PhoneCamera(private val context: Context, private val handler: Handler) {
  private var device: CameraDevice? = null
  private var session: CameraCaptureSession? = null
  private var reader: ImageReader? = null
  private var closed = false

  @SuppressLint("MissingPermission")
  fun start(onMode: (String) -> Unit, onError: (String) -> Unit, onFrame: (CameraFrame) -> Unit) {
    try {
      val manager = context.getSystemService(Context.CAMERA_SERVICE) as CameraManager
      val id = manager.cameraIdList.first {
        manager.getCameraCharacteristics(it).get(CameraCharacteristics.LENS_FACING) == CameraCharacteristics.LENS_FACING_BACK
      }
      val info = manager.getCameraCharacteristics(id)
      val configuration = info.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP)!!
      val sizes = configuration.getOutputSizes(ImageFormat.YUV_420_888).filter { it.width <= 640 }
      val fastSizes = sizes.filter {
        configuration.getOutputMinFrameDuration(ImageFormat.YUV_420_888, it) <= 16_666_667L
      }
      val size = (fastSizes.ifEmpty { sizes }).maxByOrNull { it.width * it.height }
        ?: configuration.getOutputSizes(ImageFormat.YUV_420_888).minBy { it.width * it.height }
      val ranges = info.get(CameraCharacteristics.CONTROL_AE_AVAILABLE_TARGET_FPS_RANGES)!!.toList()
      val range = ranges.minBy { kotlin.math.abs(it.upper - 60) + kotlin.math.abs(it.lower - 60) }
      val modes = info.get(CameraCharacteristics.CONTROL_AF_AVAILABLE_MODES) ?: intArrayOf()
      val focus = listOf(CaptureRequest.CONTROL_AF_MODE_CONTINUOUS_VIDEO,
        CaptureRequest.CONTROL_AF_MODE_CONTINUOUS_PICTURE, CaptureRequest.CONTROL_AF_MODE_AUTO)
        .firstOrNull { it in modes } ?: CaptureRequest.CONTROL_AF_MODE_OFF
      val orientation = info.get(CameraCharacteristics.SENSOR_ORIENTATION) ?: 90
      val images = ImageReader.newInstance(size.width, size.height, ImageFormat.YUV_420_888, 2)
      reader = images
      images.setOnImageAvailableListener({ source ->
        val image = source.acquireLatestImage() ?: return@setOnImageAvailableListener
        try {
          if (!closed) onFrame(bitmap(image, orientation))
        } catch (error: Exception) { if (!closed) onError(error.message ?: "Frame conversion failed")
        } finally { image.close() }
      }, handler)
      manager.openCamera(id, object : CameraDevice.StateCallback() {
        override fun onOpened(camera: CameraDevice) {
          if (closed) { camera.close(); return }
          device = camera
          val request = camera.createCaptureRequest(CameraDevice.TEMPLATE_RECORD).apply {
            addTarget(images.surface)
            set(CaptureRequest.CONTROL_AE_TARGET_FPS_RANGE, range)
            set(CaptureRequest.CONTROL_AF_MODE, focus)
            set(CaptureRequest.CONTROL_AE_MODE, CaptureRequest.CONTROL_AE_MODE_ON)
            set(CaptureRequest.CONTROL_VIDEO_STABILIZATION_MODE, CaptureRequest.CONTROL_VIDEO_STABILIZATION_MODE_OFF)
          }
          @Suppress("DEPRECATION")
          camera.createCaptureSession(listOf(images.surface), object : CameraCaptureSession.StateCallback() {
            override fun onConfigured(active: CameraCaptureSession) {
              if (closed) { active.close(); return }
              session = active
              try {
                active.setRepeatingRequest(request.build(), null, handler)
                if (focus == CaptureRequest.CONTROL_AF_MODE_AUTO) {
                  request.set(CaptureRequest.CONTROL_AF_TRIGGER, CaptureRequest.CONTROL_AF_TRIGGER_START)
                  active.capture(request.build(), null, handler)
                }
                val mode = "Camera2 ${range.lower}-${range.upper} FPS | autofocus ${if (focus == 0) "fixed" else "on"}"
                android.util.Log.i("WorldGitCamera", "Supported FPS: $ranges; $mode; size $size")
                onMode(mode)
              } catch (error: Exception) { onError(error.message ?: "Capture failed") }
            }
            override fun onConfigureFailed(active: CameraCaptureSession) { onError("Camera stream configuration failed") }
          }, handler)
        }
        override fun onDisconnected(camera: CameraDevice) { camera.close(); if (!closed) onError("Camera disconnected") }
        override fun onError(camera: CameraDevice, error: Int) { camera.close(); if (!closed) onError("Camera error $error") }
      }, handler)
    } catch (error: Exception) { onError(error.message ?: "Camera startup failed") }
  }

  private fun bitmap(image: Image, orientation: Int): CameraFrame {
    val width = image.width
    val height = image.height
    val nv21 = ByteArray(width * height * 3 / 2)
    val planes = image.planes
    // Respect row and pixel strides: vendor YUV planes need not be tightly packed.
    for (index in 0..2) {
      val plane = planes[index]
      val columns = if (index == 0) width else width / 2
      val rows = if (index == 0) height else height / 2
      val buffer = plane.buffer
      val base = buffer.position()
      val rowBytes = ByteArray((columns - 1) * plane.pixelStride + 1)
      for (row in 0 until rows) {
        buffer.position(base + row * plane.rowStride)
        buffer.get(rowBytes)
        if (index == 0 && plane.pixelStride == 1) {
          System.arraycopy(rowBytes, 0, nv21, row * width, columns)
        } else {
          for (column in 0 until columns) {
            val destination = if (index == 0) row * width + column
              else width * height + row * width + column * 2 + if (index == 2) 0 else 1
            nv21[destination] = rowBytes[column * plane.pixelStride]
          }
        }
      }
    }
    val output = ByteArrayOutputStream()
    YuvImage(nv21, ImageFormat.NV21, width, height, null).compressToJpeg(Rect(0, 0, width, height), 70, output)
    val jpeg = output.toByteArray()
    val raw = BitmapFactory.decodeByteArray(jpeg, 0, jpeg.size)
    val upright = Bitmap.createBitmap(raw, 0, 0, width, height, Matrix().apply { postRotate(orientation.toFloat()) }, true)
    if (upright !== raw) raw.recycle()
    return CameraFrame(upright, jpeg, orientation)
  }

  fun close() {
    closed = true
    session?.close(); session = null
    device?.close(); device = null
    reader?.close(); reader = null
  }
}
