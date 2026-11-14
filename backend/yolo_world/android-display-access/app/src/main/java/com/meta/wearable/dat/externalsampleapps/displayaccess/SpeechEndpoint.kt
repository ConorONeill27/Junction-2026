package com.meta.wearable.dat.externalsampleapps.displayaccess

import kotlin.math.max
import kotlin.math.sqrt

/** Sample-count timing keeps endpointing independent of network and UI delays. */
class SpeechEndpoint(private val sampleRate: Int = 16000, private val manualStop: Boolean = false) {
  var heardSpeech = false
    private set
  private var samples = 0L
  private var lastVoice = 0L
  private var voicedRun = 0
  private var noise = 0.002

  fun add(audio: ShortArray, count: Int): Boolean {
    require(count in 1..audio.size)
    samples += count
    var sum = 0.0
    for (index in 0 until count) {
      val value = audio[index] / 32768.0
      sum += value * value
    }
    val rms = sqrt(sum / count)
    if (rms > max(0.006, noise * 3)) {
      voicedRun += count
      if (voicedRun >= sampleRate / 10) heardSpeech = true
      lastVoice = samples
    } else {
      voicedRun = 0
      if (!heardSpeech) noise = noise * .95 + rms * .05
    }
    return !manualStop && (samples >= sampleRate * 30L ||
      (heardSpeech && samples - lastVoice >= sampleRate * 12L / 10) ||
      (!heardSpeech && samples >= sampleRate * 8L))
  }
}
