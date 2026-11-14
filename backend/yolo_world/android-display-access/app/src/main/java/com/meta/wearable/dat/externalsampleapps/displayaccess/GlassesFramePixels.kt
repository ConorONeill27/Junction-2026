package com.meta.wearable.dat.externalsampleapps.displayaccess

/** Meta's decoded video stream uses planar I420: Y, then U, then V. */
object GlassesFramePixels {
  fun i420ToArgb(data: ByteArray, width: Int, height: Int): IntArray {
    require(width in 2..1920 && height in 2..1920 && width % 2 == 0 && height % 2 == 0)
    val size = width * height
    require(data.size == size * 3 / 2) { "Unsupported glasses YUV layout: ${data.size} bytes for ${width}x$height" }
    return IntArray(size) { index ->
      val x = index % width; val y = index / width
      val chroma = (y / 2) * (width / 2) + x / 2
      val luma = ((data[index].toInt() and 255) - 16).coerceAtLeast(0)
      val u = (data[size + chroma].toInt() and 255) - 128
      val v = (data[size + size / 4 + chroma].toInt() and 255) - 128
      val red = ((298 * luma + 409 * v + 128) shr 8).coerceIn(0,255)
      val green = ((298 * luma - 100 * u - 208 * v + 128) shr 8).coerceIn(0,255)
      val blue = ((298 * luma + 516 * u + 128) shr 8).coerceIn(0,255)
      (255 shl 24) or (red shl 16) or (green shl 8) or blue
    }
  }
}
