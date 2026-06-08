"use client";

import { useCallback, useRef, useState } from "react";

/**
 * Silero VAD hook — runs speech detection on 16kHz Int16 audio frames.
 *
 * Accumulates audio in 500ms batches, then runs NonRealTimeVAD to detect speech.
 * Uses dynamic import to avoid onnxruntime-web bundling issues with Next.js/Turbopack.
 */

const BATCH_SAMPLES = 8000; // 500ms at 16kHz
const SPEECH_BATCHES_THRESHOLD = 2;  // 2 consecutive batches with speech (~1s)
const SILENCE_BATCHES_THRESHOLD = 3; // 3 consecutive batches without speech (~1.5s)

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type VADInstance = any;

export function useSileroVAD() {
  const [isLoaded, setIsLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const vadRef = useRef<VADInstance>(null);
  const isSpeakingRef = useRef(false);
  const batchBufferRef = useRef<Float32Array[]>([]);
  const batchSamplesRef = useRef(0);
  const speechBatchCountRef = useRef(0);
  const silenceBatchCountRef = useRef(0);

  const speechStartCallbackRef = useRef<(() => void) | null>(null);
  const speechEndCallbackRef = useRef<((audio: Int16Array) => void) | null>(null);
  const allAudioRef = useRef<Int16Array[]>([]);

  const initialize = useCallback(async () => {
    try {
      console.log("[SileroVAD] Initializing...");

      // Dynamic import to avoid Turbopack bundling onnxruntime-web
      const ortModule = await import("onnxruntime-web");
      // Set WASM paths BEFORE creating the VAD
      ortModule.env.wasm.numThreads = 1;
      ortModule.env.wasm.simd = true;

      // Load the ONNX model directly
      const modelResponse = await fetch("/silero_vad_legacy.onnx");
      const modelBuffer = await modelResponse.arrayBuffer();

      const session = await ortModule.InferenceSession.create(modelBuffer, {
        executionProviders: ["wasm"],
      });

      vadRef.current = {
        session,
        ort: ortModule,
        // Silero VAD state (h, c tensors for LSTM)
        h: new ortModule.Tensor("float32", new Float32Array(2 * 64), [2, 1, 64]),
        c: new ortModule.Tensor("float32", new Float32Array(2 * 64), [2, 1, 64]),
        sr: new ortModule.Tensor("int64", BigInt64Array.from([BigInt(16000)]), []),
      };

      setIsLoaded(true);
      console.log("[SileroVAD] Model loaded successfully (direct ONNX)");
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      console.error("[SileroVAD] Failed to initialize:", msg, e);
      setError(msg);
    }
  }, []);

  /**
   * Run inference on a single 512-sample frame.
   * Returns speech probability (0-1).
   */
  const runInference = useCallback(async (frame: Float32Array): Promise<number> => {
    const vad = vadRef.current;
    if (!vad) return 0;

    const { session, ort: ortModule, sr } = vad;

    const inputTensor = new ortModule.Tensor("float32", frame, [1, frame.length]);

    const feeds: Record<string, unknown> = {
      input: inputTensor,
      h: vad.h,
      c: vad.c,
      sr: sr,
    };

    const results = await session.run(feeds);

    // Update LSTM state
    vad.h = results.hn;
    vad.c = results.cn;

    // Output is speech probability
    return results.output.data[0] as number;
  }, []);

  /**
   * Feed a 16kHz Int16Array audio chunk.
   * Accumulates into 500ms batches, then runs VAD analysis.
   */
  const processFrame = useCallback(async (samples16k: Int16Array) => {
    if (!vadRef.current) return;

    // Convert to Float32
    const float32 = new Float32Array(samples16k.length);
    for (let i = 0; i < samples16k.length; i++) {
      float32[i] = samples16k[i] / 32768.0;
    }

    batchBufferRef.current.push(float32);
    batchSamplesRef.current += float32.length;

    // Keep raw Int16 for speaker verification
    if (isSpeakingRef.current) {
      allAudioRef.current.push(new Int16Array(samples16k));
    }

    if (batchSamplesRef.current < BATCH_SAMPLES) return;

    // Combine batch
    const combined = new Float32Array(batchSamplesRef.current);
    let offset = 0;
    for (const buf of batchBufferRef.current) {
      combined.set(buf, offset);
      offset += buf.length;
    }
    batchBufferRef.current = [];
    batchSamplesRef.current = 0;

    // Run VAD on 512-sample sub-frames, take max probability
    let maxProb = 0;
    const FRAME_SIZE = 512;
    for (let i = 0; i + FRAME_SIZE <= combined.length; i += FRAME_SIZE) {
      const frame = combined.slice(i, i + FRAME_SIZE);
      try {
        const prob = await runInference(frame);
        if (prob > maxProb) maxProb = prob;
      } catch {
        // Skip frame on error
      }
    }

    const hasSpeech = maxProb > 0.5;

    if (hasSpeech) {
      speechBatchCountRef.current++;
      silenceBatchCountRef.current = 0;

      if (!isSpeakingRef.current && speechBatchCountRef.current >= SPEECH_BATCHES_THRESHOLD) {
        console.log("[SileroVAD] Speech START (prob:", maxProb.toFixed(3), ")");
        isSpeakingRef.current = true;
        allAudioRef.current = [];
        speechStartCallbackRef.current?.();
      }
    } else {
      silenceBatchCountRef.current++;
      speechBatchCountRef.current = 0;

      if (isSpeakingRef.current && silenceBatchCountRef.current >= SILENCE_BATCHES_THRESHOLD) {
        console.log("[SileroVAD] Speech END");
        isSpeakingRef.current = false;
        const audioOut = combineInt16Buffers(allAudioRef.current);
        allAudioRef.current = [];
        speechEndCallbackRef.current?.(audioOut);
      }
    }
  }, [runInference]);

  const onSpeechStart = useCallback((cb: () => void) => {
    speechStartCallbackRef.current = cb;
  }, []);

  const onSpeechEnd = useCallback((cb: (audio: Int16Array) => void) => {
    speechEndCallbackRef.current = cb;
  }, []);

  const reset = useCallback(() => {
    isSpeakingRef.current = false;
    batchBufferRef.current = [];
    batchSamplesRef.current = 0;
    speechBatchCountRef.current = 0;
    silenceBatchCountRef.current = 0;
    allAudioRef.current = [];
  }, []);

  const destroy = useCallback(() => {
    vadRef.current = null;
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

function combineInt16Buffers(buffers: Int16Array[]): Int16Array {
  const totalLen = buffers.reduce((s, b) => s + b.length, 0);
  const combined = new Int16Array(totalLen);
  let offset = 0;
  for (const buf of buffers) {
    combined.set(buf, offset);
    offset += buf.length;
  }
  return combined;
}
