"use client";

import { useCallback, useRef } from "react";
import { int16ToBase64 } from "@/lib/audioUtils";
import { verifySpeaker, classifyIntent } from "@/lib/api";
import {
  SPEAKER_VERIFY_BUFFER_MS,
  SPEAKER_VERIFY_TIMEOUT_MS,
} from "@/lib/constants";

/**
 * Directed Speech Detection pipeline for the `conversing` state.
 *
 * Orchestrates 3 signals:
 *   1. Silero VAD (speech detected?) — handled externally, triggers onVADSpeechStart
 *   2. Speaker Verification (right person?) — runs after buffering audio
 *   3. Intent Classification (directed at Aura?) — runs on first transcript
 *
 * The hook exposes:
 *   - activate/deactivate: toggle pipeline on/off
 *   - onVADSpeechStart: called when VAD detects speech start
 *   - feedAudio: buffer 16kHz Int16 chunks during VAD detection
 *   - onEnterListening: callback to wire into useAuraSession's enterListeningFromConversation
 */

export type DirectedSpeechPhase =
  | "idle"
  | "buffering"        // VAD detected speech, accumulating audio for speaker verify
  | "verifying"         // Sent audio to speaker verification, awaiting result
  | "listening"         // Verified, command STT active
  | "classifying";      // Checking first transcript for intent

interface UseDirectedSpeechOptions {
  getAccessToken: () => string | undefined;
  getSampleRate: () => number;
  enterListening: () => void;
  getCommandSTT: () => {
    startListening: (sampleRate: number) => Promise<void>;
    sendAudioChunk: (samples: Int16Array, sampleRate: number) => void;
    stopListening: () => string;
  };
  getLastResponse: () => string;
}

export function useDirectedSpeech(opts: UseDirectedSpeechOptions) {
  const phaseRef = useRef<DirectedSpeechPhase>("idle");
  const activeRef = useRef(false);
  const audioBufferRef = useRef<Int16Array[]>([]);
  const bufferStartRef = useRef<number>(0);
  const noEnrollmentsRef = useRef(false); // cache: skip verify if no enrollments

  const activate = useCallback(() => {
    activeRef.current = true;
    phaseRef.current = "idle";
    audioBufferRef.current = [];
    bufferStartRef.current = 0;
  }, []);

  const deactivate = useCallback(() => {
    activeRef.current = false;
    phaseRef.current = "idle";
    audioBufferRef.current = [];
  }, []);

  /**
   * Called by the VAD hook when speech starts during conversing.
   * Begins buffering audio for speaker verification.
   */
  const onVADSpeechStart = useCallback(() => {
    if (!activeRef.current || phaseRef.current !== "idle") return;

    console.log("[DirectedSpeech] VAD speech start → buffering");
    phaseRef.current = "buffering";
    audioBufferRef.current = [];
    bufferStartRef.current = Date.now();
  }, []);

  /**
   * Called when VAD speech ends without enough audio to verify.
   * Resets to idle.
   */
  const onVADSpeechEnd = useCallback(() => {
    if (phaseRef.current === "buffering") {
      console.log("[DirectedSpeech] VAD speech end during buffering → reset");
      phaseRef.current = "idle";
      audioBufferRef.current = [];
    }
  }, []);

  /**
   * Feed 16kHz Int16 audio during buffering and listening phases.
   */
  const feedAudio = useCallback((samples16k: Int16Array) => {
    if (!activeRef.current) return;

    const phase = phaseRef.current;

    // Buffer audio during buffering and verifying phases
    if (phase === "buffering" || phase === "verifying") {
      audioBufferRef.current.push(new Int16Array(samples16k));

      // Check if we've accumulated enough audio for speaker verification
      if (phase === "buffering") {
        const elapsedMs = Date.now() - bufferStartRef.current;
        if (elapsedMs >= SPEAKER_VERIFY_BUFFER_MS) {
          startVerification();
        }
      }
    }

    // During listening: forward audio to command STT
    if (phase === "listening" || phase === "classifying") {
      opts.getCommandSTT().sendAudioChunk(samples16k, 16000);
    }
  }, [opts]);

  /**
   * Combine buffered audio and send for speaker verification.
   */
  const startVerification = useCallback(async () => {
    phaseRef.current = "verifying";
    const token = opts.getAccessToken();

    // Skip verification if no enrollments (cached from previous call)
    if (noEnrollmentsRef.current || !token) {
      console.log("[DirectedSpeech] No enrollments or no token → skip verify → listening");
      await transitionToListening();
      return;
    }

    // Combine buffered audio
    const totalLen = audioBufferRef.current.reduce((s, c) => s + c.length, 0);
    const combined = new Int16Array(totalLen);
    let offset = 0;
    for (const chunk of audioBufferRef.current) {
      combined.set(chunk, offset);
      offset += chunk.length;
    }

    const audioB64 = int16ToBase64(combined);
    console.log("[DirectedSpeech] Verifying speaker, samples:", combined.length);

    try {
      const result = await Promise.race([
        verifySpeaker(token, audioB64, 16000),
        new Promise<null>((resolve) =>
          setTimeout(() => resolve(null), SPEAKER_VERIFY_TIMEOUT_MS)
        ),
      ]);

      if (!activeRef.current || phaseRef.current !== "verifying") return; // cancelled

      if (result === null) {
        // Timeout — fail open
        console.warn("[DirectedSpeech] Speaker verify timeout → allowing through");
        await transitionToListening();
        return;
      }

      if (result.reason === "no_enrollments") {
        noEnrollmentsRef.current = true;
        console.log("[DirectedSpeech] No enrollments → caching, skip future verifications");
        await transitionToListening();
        return;
      }

      if (result.verified) {
        console.log("[DirectedSpeech] Speaker verified:", result.speaker_name, "score:", result.score);
        await transitionToListening();
      } else {
        console.log("[DirectedSpeech] Speaker rejected, score:", result.score, "→ ignore");
        phaseRef.current = "idle";
        audioBufferRef.current = [];
      }
    } catch (err) {
      console.warn("[DirectedSpeech] Speaker verify error → allowing through:", err);
      if (activeRef.current && phaseRef.current === "verifying") {
        await transitionToListening();
      }
    }
  }, [opts]);

  /**
   * Transition to listening: start command STT and flush buffered audio.
   */
  const transitionToListening = useCallback(async () => {
    if (!activeRef.current) return;

    phaseRef.current = "listening";
    console.log("[DirectedSpeech] → LISTENING, flushing buffer to command STT");

    // Call the enterListening callback (sets state, plays beep, etc.)
    opts.enterListening();

    // Start command STT
    const sampleRate = opts.getSampleRate();
    await opts.getCommandSTT().startListening(sampleRate);

    // Flush all buffered audio to command STT
    for (const chunk of audioBufferRef.current) {
      opts.getCommandSTT().sendAudioChunk(chunk, 16000);
    }
    audioBufferRef.current = [];
  }, [opts]);

  /**
   * Called with the first partial/committed transcript from command STT.
   * Runs intent classification to verify it's directed at Aura.
   * Returns true if directed, false if should cancel.
   */
  const classifyFirstTranscript = useCallback(async (text: string): Promise<boolean> => {
    if (phaseRef.current !== "listening") return true;
    phaseRef.current = "classifying";

    const token = opts.getAccessToken();
    if (!token) return true; // fail open

    try {
      const lastResponse = opts.getLastResponse();
      const context = lastResponse ? [lastResponse] : [];
      const result = await classifyIntent(token, text, context);

      console.log("[DirectedSpeech] Intent:", result.directed ? "DIRECTED" : "NOT DIRECTED",
        "confidence:", result.confidence, "method:", result.method);

      if (result.directed) {
        phaseRef.current = "listening"; // back to normal listening
        return true;
      } else {
        // Not directed at Aura — cancel
        phaseRef.current = "idle";
        return false;
      }
    } catch (err) {
      console.warn("[DirectedSpeech] Intent classify error → allowing through:", err);
      phaseRef.current = "listening";
      return true; // fail open
    }
  }, [opts]);

  return {
    activate,
    deactivate,
    onVADSpeechStart,
    onVADSpeechEnd,
    feedAudio,
    classifyFirstTranscript,
    get phase() { return phaseRef.current; },
    get isActive() { return activeRef.current; },
  };
}
