package com.meta.wearable.dat.externalsampleapps.displayaccess

import org.junit.Assert.*
import org.junit.Test

class SpeechEndpointTest {
  private val silence = ShortArray(1600)
  private val voice = ShortArray(1600) { 4000 }

  @Test fun quietInputTimesOutWithoutPretendingToHearSpeech() {
    val endpoint = SpeechEndpoint()
    repeat(79) { assertFalse(endpoint.add(silence, silence.size)) }
    assertTrue(endpoint.add(silence, silence.size))
    assertFalse(endpoint.heardSpeech)
  }

  @Test fun speechEndsAfterOnePointTwoSecondsOfSilence() {
    val endpoint = SpeechEndpoint()
    assertFalse(endpoint.add(voice, voice.size))
    assertTrue(endpoint.heardSpeech)
    repeat(11) { assertFalse(endpoint.add(silence, silence.size)) }
    assertTrue(endpoint.add(silence, silence.size))
  }

  @Test fun shortPausesDoNotEndTheUtterance() {
    val endpoint = SpeechEndpoint()
    endpoint.add(voice, voice.size)
    repeat(8) { assertFalse(endpoint.add(silence, silence.size)) }
    assertFalse(endpoint.add(voice, voice.size))
    repeat(11) { assertFalse(endpoint.add(silence, silence.size)) }
    assertTrue(endpoint.add(silence, silence.size))
  }

  @Test fun continuousNoiseCannotKeepTheMicrophoneOpenForever() {
    val endpoint = SpeechEndpoint()
    repeat(299) { assertFalse(endpoint.add(voice, voice.size)) }
    assertTrue(endpoint.add(voice, voice.size))
  }
  @Test fun manualNoteDoesNotStopForSilenceOrDuration() {
    val endpoint = SpeechEndpoint(manualStop = true)
    repeat(100) { assertFalse(endpoint.add(silence, silence.size)) }
    assertFalse(endpoint.heardSpeech)
    assertFalse(endpoint.add(voice, voice.size))
    assertTrue(endpoint.heardSpeech)
    repeat(400) { assertFalse(endpoint.add(silence, silence.size)) }
    repeat(400) { assertFalse(endpoint.add(voice, voice.size)) }
  }
}
