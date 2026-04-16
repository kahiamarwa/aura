"use client";

import { useCallback, useRef, useState } from "react";
import { NonRealTimeVAD } from "@ricky0123/vad-web";

/**
 * Silero VAD hook — runs speech detection on 16kHz Int16 audio frames.
 * Replaces dumb RMS volume thresholding with neural VAD.
 *
 * Usage: feed 16kHz Int16Array chunks via processFrame().
 * Callbacks fire on speech start/end.
 */

export function useSileroVAD() {
  const [isLoaded, setIsLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const vadRef = useRef<NonRealTimeVAD | null>(null);
  const isSpeakingRef = useRef(false);
  const audioBufferRef = useRef<Int16Array[]>([]);
  const speechStartCallbackRef = useRef<(() => void) | null>(null);
  const speechEndCallbackRef = useRef<((audio: Int16Array) => void) | null>(null);

  // Consecutive speech/silence frame counters for hysteresis
  const speechFramesRef = useRef(0);
  const silenceFramesRef = useRef(0);

  // VAD model instance (reuse across calls)
  const modelRef = useRef<NonRealTimeVAD | null>(null);

  const SPEECH_THRESHOLD = 3;   // ~96ms of speech to trigger start
  const SILENCE_THRESHOLD = 10; // ~320ms of silence to trigger end
  const POSITIVE_PROB = 0.5;
  const NEGATIVE_PROB = 0.35;

  const initialize = useCallback(async () => {
    try {
      const vad = await NonRealTimeVAD.new({
        positiveSpeechThreshold: POSITIVE_PROB,
        negativeSpeechThreshold: NEGATIVE_PROB,
        redemptionMs: 300,
        preSpeechPadMs: 100,
        minSpeechMs: 100,
        submitUserSpeechOnPause: false,
      });
      modelRef.current = vad;
      setIsLoaded(true);
      console.log("[SileroVAD] Model loaded");
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      console.error("[SileroVAD] Failed to load:", msg);
      setError(msg);
    }
  }, []);

  /**
   * Process a 16kHz Int16Array audio chunk through Silero VAD.
   * Uses the NonRealTimeVAD.run() to detect speech segments.
   * Maintains hysteresis counters for stable speech start/end detection.
   */
  const processFrame = useCallback(async (samples16k: Int16Array) => {
    if (!modelRef.current) return;

    // Convert Int16 to Float32 [-1, 1]
    const float32 = new Float32Array(samples16k.length);
    for (let i = 0; i < samples16k.length; i++) {
      float32[i] = samples16k[i] / 32768.0;
    }

    // Compute simple RMS to check if there's any signal at all
    let sumSq = 0;
    for (let i = 0; i < float32.length; i++) sumSq += float32[i] * float32[i];
    const rms = Math.sqrt(sumSq / float32.length);
    if (rms < 0.005) {
      // Near silence — count as non-speech
      speechFramesRef.current = 0;
      silenceFramesRef.current++;
      if (isSpeakingRef.current && silenceFramesRef.current >= SILENCE_THRESHOLD) {
        isSpeakingRef.current = false;
        const combined = combineBuffers(audioBufferRef.current);
        audioBufferRef.current = [];
        speechEndCallbackRef.current?.(combined);
      }
      return;
    }

    // Run VAD on the chunk — check if any speech is detected
    let hasSpeech = false;
    try {
      for await (const segment of modelRef.current.run(float32, 16000)) {
        if (segment.audio.length > 0) {
          hasSpeech = true;
          break;
        }
      }
    } catch {
      // VAD error — treat as no speech
      return;
    }

    if (hasSpeech) {
      speechFramesRef.current++;
      silenceFramesRef.current = 0;

      // Buffer audio during speech
      audioBufferRef.current.push(new Int16Array(samples16k));

      if (!isSpeakingRef.current && speechFramesRef.current >= SPEECH_THRESHOLD) {
        isSpeakingRef.current = true;
        speechStartCallbackRef.current?.();
      }
    } else {
      speechFramesRef.current = 0;
      silenceFramesRef.current++;

      // Still buffer during redemption period
      if (isSpeakingRef.current) {
        audioBufferRef.current.push(new Int16Array(samples16k));
      }

      if (isSpeakingRef.current && silenceFramesRef.current >= SILENCE_THRESHOLD) {
        isSpeakingRef.current = false;
        const combined = combineBuffers(audioBufferRef.current);
        audioBufferRef.current = [];
        speechEndCallbackRef.current?.(combined);
      }
    }
  }, []);

  const onSpeechStart = useCallback((cb: () => void) => {
    speechStartCallbackRef.current = cb;
  }, []);

  const onSpeechEnd = useCallback((cb: (audio: Int16Array) => void) => {
    speechEndCallbackRef.current = cb;
  }, []);

  const reset = useCallback(() => {
    isSpeakingRef.current = false;
    speechFramesRef.current = 0;
    silenceFramesRef.current = 0;
    audioBufferRef.current = [];
  }, []);

  const destroy = useCallback(() => {
    modelRef.current = null;
    reset();
    setIsLoaded(false);
  }, [reset]);

  return {
    isLoaded,
    error,
    initialize,
    processFrame,
    onSpeechStart,
    onSpeechEnd,
    reset,
    destroy,
    get isSpeaking() { return isSpeakingRef.current; },
  };
}

function combineBuffers(buffers: Int16Array[]): Int16Array {
  const totalLen = buffers.reduce((s, b) => s + b.length, 0);
  const combined = new Int16Array(totalLen);
  let offset = 0;
  for (const buf of buffers) {
    combined.set(buf, offset);
    offset += buf.length;
  }
  return combined;
}
