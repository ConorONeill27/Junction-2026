package com.meta.wearable.dat.externalsampleapps.displayaccess

import org.junit.Assert.*
import org.junit.Test

class HudFramePredictorTest {
  private val calibration = HudCalibration()
  private fun state(x: Double) = YoloState(items=listOf(YoloItem("cup","cup",.9,listOf(x,30.0,x+20,50.0))),width=100,height=100)

  @Test fun generatesIntermediateFramesWithoutNewDetections() {
    val predictor = HudFramePredictor()
    predictor.observe(state(20.0),null,0.0,calibration)
    predictor.sample(null,0.0,calibration)
    predictor.observe(state(22.0),null,.1,calibration)
    val first = predictor.sample(null,.1,calibration).items.single().box[0]
    val second = predictor.sample(null,.133333,calibration).items.single().box[0]
    val third = predictor.sample(null,.166666,calibration).items.single().box[0]
    assertTrue(first < second)
    assertTrue(second < third)
    assertTrue(third < 25)
  }

  @Test fun hidesStaleTracksAndResetsOnResolutionChange() {
    val predictor = HudFramePredictor()
    predictor.observe(state(20.0),null,0.0,calibration)
    assertTrue(predictor.sample(null,.6,calibration).items.isEmpty())
    predictor.observe(state(25.0).copy(width=200),null,.7,calibration)
    assertEquals(25.0,predictor.sample(null,.7,calibration).items.single().box[0],.001)
  }

  @Test fun ownEstimateIsIndependentAndShrinksParallaxWithDistance() {
    val estimate = HudAlignmentEstimate.estimate()
    assertEquals(1f,estimate.size)
    assertEquals(-108.3f,estimate.horizontal,1f)
    assertEquals(-30.1f,estimate.vertical,1f)
    val farther = HudAlignmentEstimate.estimate(4.0)
    assertEquals(estimate.horizontal/2,farther.horizontal,.01f)
    assertEquals(estimate.vertical/2,farther.vertical,.01f)
  }
}
