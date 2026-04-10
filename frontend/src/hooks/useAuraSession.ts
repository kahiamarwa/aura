"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { AppState } from "@/components/StatusBar";
import type { ConversationEntry } from "@/lib/types";
import { useAudioCapture } from "./useAudioCapture";
import { usePassiveSTT } from "./usePassiveSTT";
import { useCommandSTT } from "./useCommandSTT";
import { useOpenWakeWord } from "./useOpenWakeWord";
import { useAudioPlayer } from "./useAudioPlayer";
import { useAuthContext } from "@/context/AuthContext";
import { sendChat, sendChatStream, createConversation, addConversationMessage, listConversations, fetchConversationDetail, verifySpeaker } from "@/lib/api";
import { int16ToBase64 } from "@/lib/audioUtils";
import { contextBuffer } from "@/lib/contextBuffer";
import { contextPersistence } from "@/lib/contextPersistence";
import {
  BACKEND_URL,
  CONVERSATION_WINDOW_MS,
  BARGEIN_VOLUME_THRESHOLD,
  BARGEIN_VOLUME_THRESHOLD_SPEAKING,
  BARGEIN_CONSECUTIVE_FRAMES,
} from "@/lib/constants";

export interface VerificationResult {
  status: "none" | "verified" | "rejected";
  speakerName: string | null;
  score: number;
}

interface UseAuraSessionReturn {
  state: AppState;
  volume: number;
  sampleRate: number;
  passivePartial: string;
  commandPartial: string;
  commandCommitted: string;
  passiveTranscriptCount: number;
  passiveEntries: { text: string; timestamp: Date }[];
  history: ConversationEntry[];
  fallbackMode: string;
  errors: string[];
  muted: boolean;
  verificationResult: VerificationResult;
  initialize: () => Promise<void>;
  cleanup: () => void;
  triggerWakeWord: () => void;
  toggleMute: () => void;
  startNewConversation: () => void;
  loadConversation: (conversationId: string) => Promise<void>;
}

function playBeep() {
  try {
    const ctx = new AudioContext();
    const oscillator = ctx.createOscillator();
    const gain = ctx.createGain();
    oscillator.connect(gain);
    gain.connect(ctx.destination);
    oscillator.frequency.value = 880;
    gain.gain.value = 0.3;
    oscillator.start();
    oscillator.stop(ctx.currentTime + 0.2);
    setTimeout(() => ctx.close(), 500);
  } catch {
    // Ignore beep errors
  }
}

export function useAuraSession(): UseAuraSessionReturn {
  const [state, setState] = useState<AppState>("initializing");
  const [history, setHistory] = useState<ConversationEntry[]>([]);
  const [errors, setErrors] = useState<string[]>([]);
  const [passiveEntries, setPassiveEntries] = useState<
    { text: string; timestamp: Date }[]
  >([]);

  const [muted, setMuted] = useState(false);
  const [verificationResult, setVerificationResult] = useState<VerificationResult>({
    status: "none", speakerName: null, score: 0,
  });
  const mutedRef = useRef(false);

  const stateRef = useRef<AppState>("initializing");
  const audioRoutingRef = useRef<"passive" | "command">("passive");
  const conversationTimerRef = useRef<ReturnType<typeof setTimeout> | null>(
    null
  );
  const bargeInFrameCountRef = useRef(0);
  const conversationIdRef = useRef<string | null>(null);
  const commandAudioBufferRef = useRef<Int16Array[]>([]);

  const { session: authSession } = useAuthContext();
  const audio = useAudioCapture();
  const passiveSTT = usePassiveSTT();
  const player = useAudioPlayer();
  const wakeword = useOpenWakeWord();

  // Clear conversation timer helper
  const clearConversationTimer = useCallback(() => {
    if (conversationTimerRef.current) {
      clearTimeout(conversationTimerRef.current);
      conversationTimerRef.current = null;
    }
  }, []);

  // Start the conversation window timer (after speaking ends)
  const startConversationWindow = useCallback(() => {
    clearConversationTimer();
    setState("conversing");
    stateRef.current = "conversing";
    bargeInFrameCountRef.current = 0;
    // passiveSTT stays paused — we don't want ambient noise during conversation

    conversationTimerRef.current = setTimeout(() => {
      if (stateRef.current === "conversing") {
        // Window expired — return to idle and resume passive listening
        console.log("[AURA] Conversation window expired, returning to idle");
        setState("idle");
        stateRef.current = "idle";
        audioRoutingRef.current = "passive";
        passiveSTT.resume();
      }
    }, CONVERSATION_WINDOW_MS);
  }, [passiveSTT, clearConversationTimer]);

  const handleCommandComplete = useCallback(
    async (command: string) => {
      console.log("[AURA] handleCommandComplete called, command:", command);
      if (!command.trim()) {
        console.log("[AURA] Empty command, returning to idle");
        setState("idle");
        stateRef.current = "idle";
        audioRoutingRef.current = "passive";
        passiveSTT.resume();
        return;
      }

      // Transition to thinking
      setState("thinking");
      stateRef.current = "thinking";

      const accessToken = authSession?.access_token;

      // ── Speaker Verification ──────────────────────────────────────
      // Combine buffered command audio and verify speaker identity
      if (accessToken && commandAudioBufferRef.current.length > 0) {
        try {
          // Concatenate all buffered PCM chunks
          const totalLen = commandAudioBufferRef.current.reduce((s, c) => s + c.length, 0);
          const combined = new Int16Array(totalLen);
          let offset = 0;
          for (const chunk of commandAudioBufferRef.current) {
            combined.set(chunk, offset);
            offset += chunk.length;
          }
          commandAudioBufferRef.current = [];

          const audioB64 = int16ToBase64(combined);
          console.log("[AURA] Verifying speaker, audio samples:", combined.length);

          const verifyResult = await verifySpeaker(accessToken, audioB64, audio.sampleRate);
          console.log("[AURA] Speaker verification:", verifyResult);

          if (verifyResult.verified) {
            // Speaker recognized — show green flash
            setVerificationResult({
              status: "verified",
              speakerName: verifyResult.speaker_name,
              score: verifyResult.score,
            });
            // Auto-clear after 4s
            setTimeout(() => setVerificationResult({ status: "none", speakerName: null, score: 0 }), 4000);
          }

          if (!verifyResult.verified && verifyResult.reason !== "no_enrollments") {
            // Impostor detected — show red flash + reject command
            setVerificationResult({
              status: "rejected",
              speakerName: verifyResult.speaker_name,
              score: verifyResult.score,
            });
            // Auto-clear after 5s
            setTimeout(() => setVerificationResult({ status: "none", speakerName: null, score: 0 }), 5000);

            console.warn("[AURA] Speaker NOT verified, score:", verifyResult.score);

            // Play rejection TTS
            try {
              const ttsRes = await fetch(`${BACKEND_URL}/api/tts`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ text: "Personne non reconnue par notre systeme." }),
              });
              if (ttsRes.ok) {
                const blob = await ttsRes.blob();
                setState("speaking");
                stateRef.current = "speaking";
                await player.play(blob);
              }
            } catch {
              // TTS failed, just skip audio
            }

            // Return to conversing/idle without processing the command
            if (stateRef.current === "speaking") {
              startConversationWindow();
            } else {
              setState("idle");
              stateRef.current = "idle";
              audioRoutingRef.current = "passive";
              passiveSTT.resume();
            }
            return;
          }
        } catch (err) {
          // Verification service error — allow through (fail open)
          console.warn("[AURA] Speaker verification error, allowing through:", err);
          commandAudioBufferRef.current = [];
        }
      } else {
        commandAudioBufferRef.current = [];
      }

      const entryId = crypto.randomUUID();

      try {
        const context = contextBuffer.getContext();
        console.log("[AURA] Sending to API (streaming):", {
          command,
          contextCount: context.length,
          hasToken: !!accessToken,
        });

        // Add entry immediately with empty response (streaming will fill it)
        const entry: ConversationEntry = {
          id: entryId,
          timestamp: new Date(),
          command,
          response: "",
          isStreaming: true,
        };
        setHistory((prev) => [...prev, entry]);

        const result = await sendChatStream(
          command,
          context,
          accessToken,
          conversationIdRef.current,
          // onTextDelta — progressive text update
          (delta) => {
            setHistory((prev) =>
              prev.map((e) =>
                e.id === entryId
                  ? { ...e, response: e.response + delta }
                  : e
              )
            );
          },
          // onToolStart
          (name) => {
            setHistory((prev) =>
              prev.map((e) =>
                e.id === entryId
                  ? { ...e, toolInProgress: name }
                  : e
              )
            );
          },
          // onToolResult
          () => {
            setHistory((prev) =>
              prev.map((e) =>
                e.id === entryId
                  ? { ...e, toolInProgress: undefined }
                  : e
              )
            );
          },
        );

        console.log("[AURA] Streaming complete:", {
          text: result.text?.substring(0, 100),
          hasAudio: !!result.audioBlob,
        });

        // Finalize entry with complete data
        setHistory((prev) =>
          prev.map((e) =>
            e.id === entryId
              ? {
                  ...e,
                  response: result.text,
                  attachments: result.attachments,
                  isStreaming: false,
                  toolInProgress: undefined,
                }
              : e
          )
        );

        // Persist conversation to backend
        if (accessToken) {
          try {
            if (!conversationIdRef.current) {
              const conv = await createConversation(accessToken, {
                title: command.slice(0, 80),
                messages: [
                  { role: "user", content: command },
                  { role: "assistant", content: result.text, attachments: result.attachments },
                ],
              });
              conversationIdRef.current = conv.conversation?.id || conv.id;
            } else {
              await addConversationMessage(accessToken, conversationIdRef.current, "user", command);
              await addConversationMessage(accessToken, conversationIdRef.current, "assistant", result.text, result.attachments);
            }
          } catch (e) {
            console.warn("[AURA] Failed to persist conversation:", e);
          }
        }

        // Add Q&A to context buffer for conversational continuity
        contextBuffer.add(`[Commande utilisateur]: ${command}`);
        contextBuffer.add(`[Réponse Aura]: ${result.text}`);

        // Play TTS if available
        if (result.audioBlob && result.audioBlob.size > 0) {
          setState("speaking");
          stateRef.current = "speaking";
          await player.play(result.audioBlob);
        }
      } catch (err) {
        // Remove streaming entry or mark as error
        setHistory((prev) =>
          prev.map((e) =>
            e.id === entryId
              ? { ...e, isStreaming: false, response: e.response || "Erreur lors du traitement." }
              : e
          )
        );
        setErrors((prev) => [
          ...prev,
          err instanceof Error ? err.message : "Erreur LLM/TTS",
        ]);
      }

      // Check if we were interrupted by barge-in
      if (
        stateRef.current === "speaking" ||
        stateRef.current === "thinking"
      ) {
        startConversationWindow();
      } else {
        console.log(
          "[AURA] Barge-in detected, skipping conversing transition"
        );
      }
    },
    [passiveSTT, player, startConversationWindow, authSession?.access_token, audio.sampleRate]
  );

  const commandSTT = useCommandSTT(handleCommandComplete);

  // Enter listening from conversing or speaking state (barge-in / follow-up)
  const enterListeningFromConversation = useCallback(() => {
    clearConversationTimer();

    // If speaking, stop TTS immediately
    if (stateRef.current === "speaking") {
      console.log("[AURA] Barge-in: stopping TTS");
      player.stop();
    }

    console.log("[AURA] Entering listening from conversation/barge-in");
    playBeep();
    setState("listening");
    stateRef.current = "listening";
    audioRoutingRef.current = "command";
    bargeInFrameCountRef.current = 0;
    commandAudioBufferRef.current = [];
    commandSTT.startListening(audio.sampleRate);
  }, [player, commandSTT, audio.sampleRate, clearConversationTimer]);

  const handleWakeWord = useCallback(() => {
    console.log(
      "[AURA] handleWakeWord triggered, current state:",
      stateRef.current
    );
    if (stateRef.current === "speaking") {
      player.stop();
    }

    if (
      stateRef.current === "idle" ||
      stateRef.current === "speaking" ||
      stateRef.current === "conversing"
    ) {
      clearConversationTimer();
      playBeep();
      setState("listening");
      stateRef.current = "listening";

      passiveSTT.pause();
      audioRoutingRef.current = "command";
      commandAudioBufferRef.current = [];

      console.log(
        "[AURA] Starting command STT, sampleRate:",
        audio.sampleRate
      );
      commandSTT.startListening(audio.sampleRate);
    }
  }, [
    passiveSTT,
    commandSTT,
    player,
    audio.sampleRate,
    clearConversationTimer,
  ]);

  const toggleMute = useCallback(() => {
    const newMuted = !mutedRef.current;
    mutedRef.current = newMuted;
    setMuted(newMuted);
    if (newMuted) {
      // Pause passive STT when muted
      passiveSTT.pause();
    } else if (stateRef.current === "idle") {
      // Resume passive STT when unmuted (only if idle)
      passiveSTT.resume();
    }
  }, [passiveSTT]);

  const startNewConversation = useCallback(() => {
    conversationIdRef.current = null;
    setHistory([]);
    contextBuffer.clear();
  }, []);

  const loadConversation = useCallback(async (conversationId: string) => {
    const token = authSession?.access_token;
    if (!token) return;
    try {
      const detail = await fetchConversationDetail(token, conversationId);
      const msgs = detail.messages || [];
      const restored: ConversationEntry[] = [];
      for (let i = 0; i < msgs.length - 1; i += 2) {
        if (msgs[i].role === "user" && msgs[i + 1]?.role === "assistant") {
          restored.push({
            id: msgs[i].id || crypto.randomUUID(),
            timestamp: new Date(msgs[i].created_at),
            command: msgs[i].content,
            response: msgs[i + 1].content,
            attachments: msgs[i + 1].attachments || undefined,
          });
        }
      }
      conversationIdRef.current = conversationId;
      setHistory(restored);
      contextBuffer.clear();
      // Re-populate context buffer with conversation history
      for (const entry of restored) {
        contextBuffer.add(`[Commande utilisateur]: ${entry.command}`);
        contextBuffer.add(`[Réponse Aura]: ${entry.response}`);
      }
    } catch (e) {
      console.warn("[AURA] Failed to load conversation:", e);
    }
  }, [authSession?.access_token]);

  // Route PCM chunks to the correct STT + barge-in / conversation VAD
  const handlePCMChunk = useCallback(
    (samples: Int16Array, sampleRate: number) => {
      // Skip all audio processing when muted
      if (mutedRef.current) return;

      const currentState = stateRef.current;

      // --- Barge-in / Conversation VAD ---
      if (currentState === "speaking" || currentState === "conversing") {
        // Compute RMS volume from PCM samples
        let sumSq = 0;
        for (let i = 0; i < samples.length; i++) {
          sumSq += samples[i] * samples[i];
        }
        const rms = Math.sqrt(sumSq / samples.length);
        const volumePercent = Math.min(
          100,
          Math.round((rms / 32768) * 100 * 3)
        );

        const threshold =
          currentState === "speaking"
            ? BARGEIN_VOLUME_THRESHOLD_SPEAKING
            : BARGEIN_VOLUME_THRESHOLD;

        if (volumePercent > threshold) {
          bargeInFrameCountRef.current++;
          if (bargeInFrameCountRef.current >= BARGEIN_CONSECUTIVE_FRAMES) {
            enterListeningFromConversation();
            return; // Don't route this chunk yet
          }
        } else {
          bargeInFrameCountRef.current = 0;
        }
      }

      // --- Normal routing ---
      if (audioRoutingRef.current === "command") {
        commandSTT.sendAudioChunk(samples, sampleRate);
        // Also buffer for speaker verification
        commandAudioBufferRef.current.push(new Int16Array(samples));
      } else {
        passiveSTT.sendAudioChunk(samples, sampleRate);
      }
    },
    [commandSTT, passiveSTT, enterListeningFromConversation]
  );

  // Register PCM callback
  useEffect(() => {
    audio.onPCMChunk(handlePCMChunk);
  }, [audio, handlePCMChunk]);

  // Register PCM 16kHz callback for wake word
  useEffect(() => {
    audio.onPCM16kChunk((samples: Int16Array) => {
      wakeword.sendAudio(samples);
    });
  }, [audio, wakeword]);

  // Register wake word callback
  useEffect(() => {
    wakeword.onKeywordDetected(handleWakeWord);
  }, [wakeword, handleWakeWord]);

  // Update passive entries periodically
  useEffect(() => {
    const interval = setInterval(() => {
      setPassiveEntries(contextBuffer.getEntries());
    }, 1000);
    return () => clearInterval(interval);
  }, []);

  // Collect errors
  useEffect(() => {
    const errs: string[] = [];
    if (audio.error) errs.push(audio.error);
    if (passiveSTT.error) errs.push(passiveSTT.error);
    if (commandSTT.error) errs.push(commandSTT.error);
    if (wakeword.error) errs.push(wakeword.error);
    setErrors(errs);
  }, [audio.error, passiveSTT.error, commandSTT.error, wakeword.error]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (conversationTimerRef.current) {
        clearTimeout(conversationTimerRef.current);
      }
      contextPersistence.endSession();
    };
  }, []);

  const initialize = useCallback(async () => {
    setState("initializing");
    stateRef.current = "initializing";
    clearConversationTimer();

    try {
      // 0. Restore recent conversation in background (non-blocking)
      // Skip if user explicitly requested a new conversation
      const skipRestore = sessionStorage.getItem("aura_new_conversation") === "1";
      if (skipRestore) {
        sessionStorage.removeItem("aura_new_conversation");
      }
      const token = authSession?.access_token;
      if (token && !skipRestore) {
        (async () => {
          try {
            const convos = await listConversations(token, 1);
            const list = Array.isArray(convos) ? convos : convos.conversations ?? [];
            if (list.length > 0) {
              const recent = list[0];
              const age = Date.now() - new Date(recent.updated_at || recent.created_at).getTime();
              if (age < 30 * 60 * 1000) {
                const detail = await fetchConversationDetail(token, recent.id);
                const msgs = detail.messages || [];
                const restored: ConversationEntry[] = [];
                for (let i = 0; i < msgs.length - 1; i += 2) {
                  if (msgs[i].role === "user" && msgs[i + 1]?.role === "assistant") {
                    restored.push({
                      id: msgs[i].id || crypto.randomUUID(),
                      timestamp: new Date(msgs[i].created_at),
                      command: msgs[i].content,
                      response: msgs[i + 1].content,
                      attachments: msgs[i + 1].attachments || undefined,
                    });
                  }
                }
                if (restored.length > 0) {
                  setHistory(restored);
                  conversationIdRef.current = recent.id;
                }
              }
            }
          } catch (e) {
            console.warn("[AURA] Failed to restore conversation:", e);
          }
        })();
      }

      // 1. Request mic access
      const mic = await audio.requestMicAccess();

      // 2. Start context persistence session (listening sessions + segments)
      await contextPersistence.startSession();

      // 3. Start STT + WakeWord in parallel
      await Promise.allSettled([
        passiveSTT.start(mic.sampleRate),
        wakeword.startListening(mic.stream),
      ]);

      // Ready
      setState("idle");
      stateRef.current = "idle";
      audioRoutingRef.current = "passive";
    } catch (err) {
      // Only mic access failure reaches here
      setState("error");
      stateRef.current = "error";
      setErrors((prev) => [
        ...prev,
        err instanceof Error ? err.message : "Erreur initialisation",
      ]);
    }
  }, [audio, passiveSTT, wakeword, clearConversationTimer, authSession?.access_token]);

  const cleanup = useCallback(() => {
    clearConversationTimer();
    passiveSTT.stop();
    wakeword.stopListening();
    audio.stopMic();
    contextPersistence.endSession();
    setState("initializing");
    stateRef.current = "initializing";
  }, [passiveSTT, wakeword, audio, clearConversationTimer]);

  return {
    state,
    volume: audio.volume,
    sampleRate: audio.sampleRate,
    passivePartial: passiveSTT.currentPartial,
    commandPartial: commandSTT.partialText,
    commandCommitted: commandSTT.committedText,
    passiveTranscriptCount: passiveSTT.transcriptCount,
    passiveEntries,
    history,
    fallbackMode: wakeword.fallbackMode,
    errors,
    muted,
    verificationResult,
    initialize,
    cleanup,
    triggerWakeWord: handleWakeWord,
    toggleMute,
    startNewConversation,
    loadConversation,
  };
}
