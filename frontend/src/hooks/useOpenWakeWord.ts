"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { BACKEND_URL } from "@/lib/constants";

type FallbackMode = "custom" | "push-to-talk";
type WakeWordType = "activate" | "interrupt";

interface UseOpenWakeWordReturn {
  isLoaded: boolean;
  fallbackMode: FallbackMode;
  error: string | null;
  startListening: (stream: MediaStream) => Promise<void>;
  stopListening: () => void;
  onKeywordDetected: (callback: (type: WakeWordType) => void) => void;
  triggerManual: () => void;
  sendAudio: (samples: Int16Array) => void;
}

export function useOpenWakeWord(): UseOpenWakeWordReturn {
  const [isLoaded, setIsLoaded] = useState(false);
  const [fallbackMode, setFallbackMode] = useState<FallbackMode>("push-to-talk");
  const [error, setError] = useState<string | null>(null);

  const callbackRef = useRef<((type: WakeWordType) => void) | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reconnectAttemptsRef = useRef(0);
  const stoppedRef = useRef(false);

  const onKeywordDetected = useCallback((callback: (type: WakeWordType) => void) => {
    callbackRef.current = callback;
  }, []);

  const triggerManual = useCallback(() => {
    callbackRef.current?.("activate");
  }, []);

  const sendAudio = useCallback((samples: Int16Array) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(samples.buffer);
    }
  }, []);

  const connectWebSocket = useCallback(() => {
    if (stoppedRef.current) return;
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    try {
      const wsUrl = BACKEND_URL.replace(/^http/, "ws") + "/api/wakeword";
      const ws = new WebSocket(wsUrl);
      ws.binaryType = "arraybuffer";

      ws.onopen = () => {
        console.log("[OpenWakeWord] WebSocket connected");
        wsRef.current = ws;
        reconnectAttemptsRef.current = 0;
        setFallbackMode("custom");
        setIsLoaded(true);
        setError(null);
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.event === "wake_word_detected") {
            const modelName: string = data.model || "";
            const type: WakeWordType = modelName.toLowerCase().includes("stop")
              ? "interrupt"
              : "activate";
            console.log(`[OpenWakeWord] Detected (${type})! model=${modelName} score=${data.score}`);
            callbackRef.current?.(type);
          }
        } catch {
          // ignore parse errors
        }
      };

      ws.onerror = (err) => {
        console.error("[OpenWakeWord] WebSocket error:", err);
      };

      ws.onclose = () => {
        console.log("[OpenWakeWord] WebSocket closed");
        wsRef.current = null;

        if (stoppedRef.current) return;
        reconnectAttemptsRef.current += 1;
        const delay = Math.min(1000 * 2 ** Math.min(reconnectAttemptsRef.current, 5), 30000);
        console.log(`[OpenWakeWord] Reconnecting in ${delay}ms (attempt ${reconnectAttemptsRef.current})`);
        reconnectTimerRef.current = setTimeout(connectWebSocket, delay);
      };
    } catch (err) {
      console.warn("OpenWakeWord init failed:", err);
      setError("Wake word error");
    }
  }, []);

  const startListening = useCallback(async (_stream: MediaStream) => {
    stoppedRef.current = false;
    reconnectAttemptsRef.current = 0;
    connectWebSocket();
  }, [connectWebSocket]);

  const stopListening = useCallback(() => {
    stoppedRef.current = true;
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
    setIsLoaded(false);
  }, []);

  useEffect(() => {
    return () => { stopListening(); };
  }, [stopListening]);

  return {
    isLoaded,
    fallbackMode,
    error,
    startListening,
    stopListening,
    onKeywordDetected,
    triggerManual,
    sendAudio,
  };
}
