"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { int16ToBase64 } from "@/lib/audioUtils";
import { contextBuffer } from "@/lib/contextBuffer";
import { contextPersistence } from "@/lib/contextPersistence";
import { BACKEND_URL } from "@/lib/constants";

const RECONNECT_DELAY_MS = 3000;
const MAX_RETRIES = 5;

interface UsePassiveSTTReturn {
  isConnected: boolean;
  currentPartial: string;
  transcriptCount: number;
  error: string | null;
  start: (sampleRate: number) => Promise<void>;
  stop: () => void;
  pause: () => void;
  resume: () => void;
  sendAudioChunk: (samples: Int16Array, sampleRate: number) => void;
}

export function usePassiveSTT(): UsePassiveSTTReturn {
  const [isConnected, setIsConnected] = useState(false);
  const [currentPartial, setCurrentPartial] = useState("");
  const [transcriptCount, setTranscriptCount] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const isPausedRef = useRef(false);
  const sampleRateRef = useRef(48000);
  const isStoppedRef = useRef(false);
  const retryCountRef = useRef(0);

  const closeWebSocket = useCallback(() => {
    if (wsRef.current) {
      wsRef.current.onclose = null;
      wsRef.current.onerror = null;
      wsRef.current.onmessage = null;
      if (
        wsRef.current.readyState === WebSocket.OPEN ||
        wsRef.current.readyState === WebSocket.CONNECTING
      ) {
        wsRef.current.close();
      }
      wsRef.current = null;
    }
    setIsConnected(false);
  }, []);

  const connectWebSocket = useCallback(
    async (sr: number) => {
      if (isStoppedRef.current) return;

      try {
        setError(null);

        // Connect to our backend Gemini STT proxy
        const wsUrl = BACKEND_URL.replace(/^http/, "ws") + "/api/gemini-stt";
        const ws = new WebSocket(wsUrl);

        ws.onmessage = (event) => {
          const data = JSON.parse(event.data);

          switch (data.type) {
            case "session_started":
              retryCountRef.current = 0;
              setIsConnected(true);
              console.log("[STT-Passive] Gemini session started");
              break;

            case "partial_transcript":
              if (data.text) {
                setCurrentPartial(data.text);
              }
              break;

            case "committed_transcript":
              if (data.text) {
                contextBuffer.add(data.text);
                contextPersistence.persistSegment(data.text, new Date());
                setTranscriptCount(contextBuffer.size);
                setCurrentPartial("");
              }
              break;

            case "error":
              console.error("[STT-Passive] Gemini error:", data.message);
              setError(data.message || "Erreur Gemini STT");
              break;

            default:
              break;
          }
        };

        ws.onerror = () => {
          setError("Connexion STT passive (Gemini) perdue");
          setIsConnected(false);
        };

        ws.onclose = () => {
          setIsConnected(false);
          if (!isStoppedRef.current && retryCountRef.current < MAX_RETRIES) {
            retryCountRef.current++;
            const delay = Math.min(
              RECONNECT_DELAY_MS * Math.pow(2, retryCountRef.current - 1),
              30000
            );
            console.warn(
              `[STT-Passive] Reconnecting ${retryCountRef.current}/${MAX_RETRIES} in ${delay / 1000}s`
            );
            setTimeout(() => connectWebSocket(sr), delay);
          } else if (retryCountRef.current >= MAX_RETRIES) {
            setError("STT passif Gemini : trop de tentatives, arrêt");
          }
        };

        wsRef.current = ws;
      } catch (err) {
        setError(
          err instanceof Error
            ? err.message
            : "Erreur connexion STT passive Gemini"
        );
        if (!isStoppedRef.current && retryCountRef.current < MAX_RETRIES) {
          retryCountRef.current++;
          const delay = Math.min(
            5000 * Math.pow(2, retryCountRef.current - 1),
            60000
          );
          setTimeout(() => connectWebSocket(sr), delay);
        }
      }
    },
    [closeWebSocket]
  );

  const start = useCallback(
    async (sampleRate: number) => {
      isStoppedRef.current = false;
      isPausedRef.current = false;
      sampleRateRef.current = sampleRate;
      await connectWebSocket(sampleRate);
    },
    [connectWebSocket]
  );

  const stop = useCallback(() => {
    isStoppedRef.current = true;
    closeWebSocket();
    setCurrentPartial("");
  }, [closeWebSocket]);

  const pause = useCallback(() => {
    isPausedRef.current = true;
  }, []);

  const resume = useCallback(() => {
    isPausedRef.current = false;
  }, []);

  const sendAudioChunk = useCallback(
    (samples: Int16Array, _sampleRate: number) => {
      if (
        isPausedRef.current ||
        !wsRef.current ||
        wsRef.current.readyState !== WebSocket.OPEN
      ) {
        return;
      }

      const base64 = int16ToBase64(samples);
      wsRef.current.send(
        JSON.stringify({
          type: "audio",
          audio_base_64: base64,
          sample_rate: sampleRateRef.current,
        })
      );
    },
    []
  );

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      isStoppedRef.current = true;
      closeWebSocket();
    };
  }, [closeWebSocket]);

  return {
    isConnected,
    currentPartial,
    transcriptCount,
    error,
    start,
    stop,
    pause,
    resume,
    sendAudioChunk,
  };
}
