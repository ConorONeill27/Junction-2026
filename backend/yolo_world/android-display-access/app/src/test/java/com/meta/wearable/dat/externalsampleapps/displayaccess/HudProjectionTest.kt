package com.meta.wearable.dat.externalsampleapps.displayaccess

import org.junit.Assert.*
import org.junit.Test

class HudProjectionTest {
  @Test fun cameraCenterMapsToDisplayCenter() {
    val p = HudProjection(360,640,HudCalibration()).point(180.0,320.0)
    assertEquals(300.0,p.first,.001); assertEquals(300.0,p.second,.001)
  }
  @Test fun outsideDisplayDetectionsAreNotSqueezedOntoScreen() {
    val state = YoloState(items = listOf(
      YoloItem("visible","cup",.9,listOf(170.0,310.0,190.0,330.0)),
      YoloItem("outside","chair",.9,listOf(0.0,0.0,30.0,40.0))),width=360,height=640)
    val output = HudProjection(360,640,HudCalibration()).state(state)
    assertEquals(listOf("visible"),output.items.map { it.id })
    assertEquals(600,output.width); assertEquals(600,output.height)
  }
  @Test fun projectionPreservesSquareDimensionsAndInverse() {
    val projection = HudProjection(360,640,HudCalibration(1.3f,80f,-35f,7f))
    val corner = projection.point(190.0,330.0)
    val inverse = projection.inverse(corner.first,corner.second)
    assertEquals(190.0,inverse.first,.001); assertEquals(330.0,inverse.second,.001)
    val plain = HudProjection(360,640,HudCalibration())
    val a = plain.point(180.0,320.0); val b = plain.point(190.0,330.0)
    assertEquals(b.first-a.first,b.second-a.second,.001)
  }
  @Test fun offsetsMoveOnlyPositionAndSizeMovesBothDimensionsEqually() {
    val baseline = HudProjection(360,640,HudCalibration()).point(190.0,330.0)
    val shifted = HudProjection(360,640,HudCalibration(horizontal=100f,vertical=-60f)).point(190.0,330.0)
    assertEquals(100.0,shifted.first-baseline.first,.001)
    assertEquals(-60.0,shifted.second-baseline.second,.001)
    val larger = HudProjection(360,640,HudCalibration(size=2f)).point(190.0,330.0)
    assertEquals((baseline.first-300)*2,larger.first-300,.001)
    assertEquals((baseline.second-300)*2,larger.second-300,.001)
  }
}
