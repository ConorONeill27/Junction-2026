package com.meta.wearable.dat.externalsampleapps.displayaccess

import org.junit.Assert.*
import org.junit.Test

class GlassesFramePixelsTest {
  @Test fun decodesNeutralPlanarLuminance() {
    val black = GlassesFramePixels.i420ToArgb(byteArrayOf(16,16,16,16,128.toByte(),128.toByte()),2,2)
    assertTrue(black.all { it == 0xff000000.toInt() })
    val white = GlassesFramePixels.i420ToArgb(byteArrayOf(235.toByte(),235.toByte(),235.toByte(),235.toByte(),128.toByte(),128.toByte()),2,2)
    assertTrue(white.all { it == 0xffffffff.toInt() })
  }
  @Test fun usesSeparateUAndVPlanes() {
    val red = GlassesFramePixels.i420ToArgb(byteArrayOf(81,81,81,81,90,240.toByte()),2,2)[0]
    assertTrue(((red shr 16) and 255) > 240)
    assertTrue(((red shr 8) and 255) < 5)
    assertTrue((red and 255) < 5)
  }
  @Test(expected = IllegalArgumentException::class) fun rejectsUnsupportedStrides() {
    GlassesFramePixels.i420ToArgb(ByteArray(9),2,2)
  }
}
