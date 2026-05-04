"use client";

import { useCallback, useRef, useState } from "react";
import { fetchSttToken } from "@/lib/api";
import { int16ToBase64 } from "@/lib/audioUtils";
import { contextBuffer } from "@/lib/contextBuffer";
import { COMMAND_TIMEOUT_MS, COMMIT_SILENCE_MS } from "@/lib/constants";

interface UseCommandSTTReturn {
  isListening: boolean;
  partialText: string;
  committedText: string;
  error: string | null;
  startListening: (sampleRate: number) => Promise<void>;
  stopListening: () => string;
  sendAudioChunk: (samples: Int16Array, sampleRate: number) => void;
}

export function useCommandSTT(
  onCommandComplete: (command: string) => void
): UseCommandSTTReturn {
  const [isListening, setIsListening] = useState(false);
  const [partialText, setPartialText] = useState("");
  const [committedText, setCommittedText] = useState("");
  const [error, setError] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const committedPartsRef = useRef<string[]>([]);
  const silenceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const timeoutTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const sampleRateRef = useRef(48000);
  const isActiveRef = useRef(false);
  const pendingChunksRef = useRef<string[]>([]);
  const isFirstChunkRef = useRef(true);

  const finalize = useCallback(() => {
    if (!isActiveRef.current) return;
    isActiveRef.current = false;

    if (silenceTimerRef.current) {
      clearTimeout(silenceTimerRef.current);
      silenceTimerRef.current = null;
    }
    if (timeoutTimerRef.current) {
      clearTimeout(timeoutTimerRef.current);
      timeoutTimerRef.current = null;
    }

    if (wsRef.current) {
      wsRef.current.onclose = null;
      wsRef.current.close();
      wsRef.current = null;
    }

    pendingChunksRef.current = [];
    isFirstChunkRef.current = true;
    const fullCommand = committedPartsRef.current.join(" ").trim();
    committedPartsRef.current = [];
    setIsListening(false);
    setPartialText("");

    if (fullCommand.length > 0) {
      onCommandComplete(fullCommand);
    }
  }, [onCommandComplete]);

  /**
   * Build previous_text from passive transcription context.
   * Sent with the first audio chunk to improve command accuracy.
   */
  const getPreviousText = useCallback((): string => {
    const entries = contextBuffer.getEntries();
    if (entries.length === 0) return "";
    // Last ~500 chars of passive transcription
    const texts = entries.map((e) => e.text);
    const joined = texts.join(" ");
    return joined.length > 500 ? joined.slice(-500) : joined;
  }, []);

  const flushPendingChunks = useCallback((ws: WebSocket) => {
    const sr = sampleRateRef.current;
    const previousText = getPreviousText();

    for (let i = 0; i < pendingChunksRef.current.length; i++) {
      const payload: Record<string, unknown> = {
        message_type: "input_audio_chunk",
        audio_base_64: pendingChunksRef.current[i],
        commit: false,
        sample_rate: sr,
      };

      // Send previous_text only with the very first chunk
      if (isFirstChunkRef.current && previousText) {
        payload.previous_text = previousText;
        isFirstChunkRef.current = false;
      }

      ws.send(JSON.stringify(payload));
    }
    console.log("[CMD-STT] Flushed", pendingChunksRef.current.length, "buffered chunks");
    pendingChunksRef.current = [];
  }, [getPreviousText]);

  const startListening = useCallback(
    async (sampleRate: number) => {
      try {
        // Close any previous WebSocket before opening a new one
        if (wsRef.current) {
          wsRef.current.onclose = null;
          wsRef.current.onmessage = null;
          wsRef.current.onerror = null;
          if (wsRef.current.readyState === WebSocket.OPEN || wsRef.current.readyState === WebSocket.CONNECTING) {
            wsRef.current.close();
          }
          wsRef.current = null;
        }
        if (silenceTimerRef.current) {
          clearTimeout(silenceTimerRef.current);
          silenceTimerRef.current = null;
        }
        if (timeoutTimerRef.current) {
          clearTimeout(timeoutTimerRef.current);
          timeoutTimerRef.current = null;
        }

        setError(null);
        setPartialText("");
        setCommittedText("");
        committedPartsRef.current = [];
        pendingChunksRef.current = [];
        isFirstChunkRef.current = true;
        sampleRateRef.current = sampleRate;
        isActiveRef.current = true;

        const token = await fetchSttToken();
        const audioFormat =
          sampleRate === 44100
            ? "pcm_44100"
            : sampleRate === 48000
              ? "pcm_48000"
              : "pcm_16000";

        const params = new URLSearchParams({
          model_id: "scribe_v2_realtime",
          token,
          audio_format: audioFormat,
          language_code: "fr",
          commit_strategy: "vad",
          // Faster VAD for commands: user finishes speaking → commit quickly
          vad_silence_threshold_secs: "0.8",
          vad_threshold: "0.5",
          min_speech_duration_ms: "100",
          min_silence_duration_ms: "200",
          include_timestamps: "true",
        });

        const wsUrl = `wss://api.elevenlabs.io/v1/speech-to-text/realtime?${params.toString()}`;
        const ws = new WebSocket(wsUrl);

        ws.onmessage = (event) => {
          const data = JSON.parse(event.data);

          switch (data.message_type) {
            case "session_started":
              setIsListening(true);
              flushPendingChunks(ws);
              timeoutTimerRef.current = setTimeout(finalize, COMMAND_TIMEOUT_MS);
              break;

            case "partial_transcript":
              if (data.text) {
                setPartialText(data.text);
                if (silenceTimerRef.current) {
                  clearTimeout(silenceTimerRef.current);
                  silenceTimerRef.current = null;
                }
              }
              break;

            case "committed_transcript":
              // Ignore — we use committed_transcript_with_timestamps instead
              break;
            case "committed_transcript_with_timestamps":
              if (data.text) {
                committedPartsRef.current.push(data.text);
                setCommittedText(committedPartsRef.current.join(" "));
                setPartialText("");

                if (silenceTimerRef.current) {
                  clearTimeout(silenceTimerRef.current);
                }
                silenceTimerRef.current = setTimeout(finalize, COMMIT_SILENCE_MS);
              }
              break;

            // --- Error handling ---
            case "auth_error":
              console.error("[CMD-STT] Auth error:", data.error);
              setError("Erreur auth STT commande");
              finalize();
              break;
            case "quota_exceeded":
              console.error("[CMD-STT] Quota exceeded:", data.error);
              setError("Quota STT dépassé");
              finalize();
              break;
            case "session_time_limit_exceeded":
              console.warn("[CMD-STT] Session time limit exceeded");
              finalize();
              break;
            case "error":
            case "input_error":
            case "transcriber_error":
            case "chunk_size_exceeded":
              console.error("[CMD-STT]", data.message_type, ":", data.error);
              break;
          }
        };

        ws.onerror = (e) => {
          console.error("[CMD-STT] WebSocket error:", e);
          setError("Erreur connexion STT commande");
          finalize();
        };

        ws.onclose = (e) => {
          console.log("[CMD-STT] WebSocket closed:", e.code, e.reason);
          if (isActiveRef.current) finalize();
        };

        wsRef.current = ws;
      } catch (err) {
        setError(
          err instanceof Error ? err.message : "Erreur démarrage STT commande"
        );
        isActiveRef.current = false;
        setIsListening(false);
      }
    },
    [finalize, flushPendingChunks]
  );

  const stopListening = useCallback((): string => {
    const command = committedPartsRef.current.join(" ").trim();
    finalize();
    return command;
  }, [finalize]);

  const sendAudioChunk = useCallback(
    (samples: Int16Array, _sampleRate: number) => {
      if (!isActiveRef.current) return;

      const base64 = int16ToBase64(samples);

      // Buffer chunks while WebSocket is still connecting
      if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
        pendingChunksRef.current.push(base64);
        return;
      }

      const payload: Record<string, unknown> = {
        message_type: "input_audio_chunk",
        audio_base_64: base64,
        commit: false,
        sample_rate: sampleRateRef.current,
      };

      // Send previous_text only with the very first chunk
      if (isFirstChunkRef.current) {
        const previousText = getPreviousText();
        if (previousText) {
          payload.previous_text = previousText;
        }
        isFirstChunkRef.current = false;
      }

      wsRef.current.send(JSON.stringify(payload));
    },
    [getPreviousText]
  );

  return {
    isListening,
    partialText,
    committedText,
    error,
    startListening,
    stopListening,
    sendAudioChunk,
  };
}
