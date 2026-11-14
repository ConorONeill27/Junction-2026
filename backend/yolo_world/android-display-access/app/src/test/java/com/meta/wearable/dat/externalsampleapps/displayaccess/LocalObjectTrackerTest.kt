package com.meta.wearable.dat.externalsampleapps.displayaccess

import org.junit.Assert.*
import org.junit.Test
import kotlin.math.*
import kotlin.random.Random

class LocalObjectTrackerTest {
  private val identity = floatArrayOf(1f,0f,0f, 0f,1f,0f, 0f,0f,1f)
  private fun blank() = GrayFrame(100, 100, IntArray(10000))
  private fun item(x: Double = 20.0, id: String = "object") = YoloItem(id, "cup", .9, listOf(x, 20.0, x + 30, 50.0))

  @Test fun oneEuroSuppressesStationaryJitter() {
    val filter = OneEuroFilter()
    var rawError = 0.0; var filteredError = 0.0
    repeat(180) { i ->
      val input = .5 + if (i % 2 == 0) .005 else -.005
      val output = filter.filter(input, i / 60.0)
      if (i > 60) { rawError += abs(input - .5); filteredError += abs(output - .5) }
    }
    assertTrue(filteredError < rawError * .25)
  }

  @Test fun adaptiveCutoffCatchesFastMotionMoreQuickly() {
    val adaptive = OneEuroFilter(); val fixed = OneEuroFilter(beta = 0.0)
    adaptive.filter(0.0, 0.0); fixed.filter(0.0, 0.0)
    var a = 0.0; var b = 0.0
    repeat(10) { i -> a = adaptive.filter(.5, (i+1)/60.0); b = fixed.filter(.5, (i+1)/60.0) }
    assertTrue(a > b)
    assertTrue(a in 0.0..0.5)
  }

  @Test fun correctionBlendsInsteadOfTeleportingAndPreservesIdentity() {
    val tracker = LocalObjectTracker()
    tracker.frame(blank(), null, 0.0, 100, 100)
    tracker.correct(listOf(item()), 0.0, 0.0, 100, 100)
    tracker.frame(blank(), null, .016, 100, 100)
    tracker.correct(listOf(item(28.0, "new-server-id")), .016, .016, 100, 100)
    val first = tracker.frame(blank(), null, .032, 100, 100).single()
    assertEquals("object", first.id)
    assertTrue(first.box[0] > 20 && first.box[0] < 28)
    var final = first
    repeat(25) { i -> final = tracker.frame(blank(), null, .048 + i * .016, 100, 100).single() }
    assertEquals(28.0, final.box[0], 1.0)
  }

  @Test fun imageFlowFindsTranslationAndRejectsUntexturedImages() {
    val random = Random(12)
    val a = GrayFrame(100,100, IntArray(10000) { random.nextInt(256) })
    val b = GrayFrame(100,100, IntArray(10000) { i ->
      val x = i % 100 - 2; val y = i / 100 - 1
      if (x >= 0 && y >= 0) a.pixels[y*100+x] else 0
    })
    val flow = SparseImageFlow.estimate(a,b,listOf(.1,.1,.9,.9),ImageMotion(null,null))!!
    assertEquals(.02, flow.dx, .001)
    assertEquals(.01, flow.dy, .001)
    assertNull(SparseImageFlow.estimate(blank(),blank(),listOf(.1,.1,.9,.9),ImageMotion(null,null)))
  }

  @Test fun delayedDetectionIsReprojectedThroughCameraMotion() {
    val tracker = LocalObjectTracker()
    tracker.frame(blank(), identity, 0.0,100,100)
    val angle = .04
    val rotated = floatArrayOf(cos(angle).toFloat(),0f,sin(angle).toFloat(), 0f,1f,0f, -sin(angle).toFloat(),0f,cos(angle).toFloat())
    tracker.frame(blank(), rotated,.016,100,100)
    tracker.correct(listOf(item()), 0.0,.02,100,100)
    val output = tracker.frame(blank(), rotated,.032,100,100).single()
    assertTrue(output.box[0] < 18)
    assertEquals(30.0, output.box[2]-output.box[0], 2.0)
  }

  @Test fun staleResponsesAndResolutionChangesCannotResurrectTracks() {
    val tracker = LocalObjectTracker()
    tracker.frame(blank(),null,0.0,100,100)
    tracker.frame(blank(),null,.2,100,100)
    tracker.correct(listOf(item()),0.0,2.0,100,100)
    assertTrue(tracker.frame(blank(),null,.21,100,100).isEmpty())
    tracker.correct(listOf(item()),.21,.21,100,100)
    assertEquals(1, tracker.frame(blank(),null,.22,100,100).size)
    assertTrue(tracker.frame(blank(),null,.23,200,100).isEmpty())
  }

  @Test fun gyroPredictionIsBoundedAndExpires() {
    val predictor = MotionPredictor()
    predictor.orientation(identity,1_000_000_000L)
    predictor.angularVelocity(0f,1f,0f,1_000_000_000L)
    val predicted = predictor.predict(1_020_000_000L)!!
    assertEquals(sin(.02),predicted[2].toDouble(),.0001)
    assertNull(predictor.predict(1_200_000_000L))
    predictor.clear()
    assertNull(predictor.predict(1_020_000_000L))
  }
}
