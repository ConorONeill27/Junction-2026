package com.meta.wearable.dat.externalsampleapps.displayaccess

import org.junit.Assert.*
import org.junit.Test
import kotlin.math.cos
import kotlin.math.sin

class HeadCursorControllerTest {
  private fun yaw(degrees: Double): FloatArray {
    val angle = Math.toRadians(degrees)
    val c = cos(angle).toFloat(); val s = sin(angle).toFloat()
    return floatArrayOf(c, 0f, s, 0f, 1f, 0f, -s, 0f, c)
  }

  @Test fun firstPinchShowsCenteredCursorWithoutSelectingAnything() {
    val controller = HeadCursorController()
    controller.updatePose(yaw(20.0))
    val state = controller.pinch { _, _ -> error("First pinch must only show the cursor") }
    assertTrue(state.visible)
    assertEquals(.5f, state.x, .0001f)
    assertEquals(.5f, state.y, .0001f)
    assertNull(state.selectedId)
  }

  @Test fun headMovementIsRelativeToFirstPinch() {
    val controller = HeadCursorController()
    controller.updatePose(yaw(20.0))
    controller.pinch { _, _ -> null }
    val state = controller.updatePose(yaw(35.0))
    assertEquals(.75f, state.x, .001f)
    assertEquals(.5f, state.y, .001f)
  }

  @Test fun yawCrossesTheAngleBoundaryWithoutJumping() {
    val controller = HeadCursorController()
    controller.updatePose(yaw(170.0))
    controller.pinch { _, _ -> null }
    assertEquals(.75f, controller.updatePose(yaw(-175.0)).x, .001f)
  }

  @Test fun lookingDownMovesTheCursorDown() {
    val controller = HeadCursorController()
    controller.updatePose(yaw(0.0))
    controller.pinch { _, _ -> null }
    val angle = Math.toRadians(11.25)
    val c = cos(angle).toFloat(); val s = sin(angle).toFloat()
    val state = controller.updatePose(floatArrayOf(1f, 0f, 0f, 0f, c, s, 0f, -s, c))
    assertEquals(.75f, state.y, .001f)
  }

  @Test fun pinchSelectsTheHitLabelAndPreservesItWhileAiming() {
    val controller = HeadCursorController()
    controller.updatePose(yaw(0.0))
    controller.pinch { _, _ -> null }
    assertEquals("bottle-7", controller.pinch { _, _ -> "bottle-7" }.selectedId)
    val moved = controller.updatePose(yaw(5.0))
    assertTrue(moved.visible)
    assertEquals("bottle-7", moved.selectedId)
  }

  @Test fun emptySpaceHidesCursorAndClearsSelection() {
    val controller = HeadCursorController()
    controller.pinch { _, _ -> null }
    controller.pinch { _, _ -> "label" }
    val dismissed = controller.pinch { _, _ -> null }
    assertFalse(dismissed.visible)
    assertNull(dismissed.selectedId)
  }

  @Test fun showingAgainRecentersOnTheNewHeadDirection() {
    val controller = HeadCursorController()
    controller.updatePose(yaw(0.0))
    controller.pinch { _, _ -> null }
    controller.updatePose(yaw(25.0))
    controller.pinch { _, _ -> null }
    controller.pinch { _, _ -> null }
    assertEquals(.5f, controller.updatePose(yaw(25.0)).x, .001f)
  }

  @Test fun missingAndInvalidSensorsDoNotCrashOrMoveCursorOutsideImage() {
    val controller = HeadCursorController()
    controller.pinch { _, _ -> null }
    controller.updatePose(yaw(0.0))
    assertEquals(.5f, controller.state.x, .001f)
    assertEquals(1f, controller.updatePose(yaw(90.0)).x, .001f)
    assertEquals(1f, controller.updatePose(FloatArray(9) { Float.NaN }).x, .001f)
    assertFalse(controller.hide().visible)
  }
}
