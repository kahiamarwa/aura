"use client";

import { useCallback, useRef, useState } from "react";

interface UseAudioPlayerReturn {
  isPlaying: boolean;
  play: (audioBlob: Blob) => Promise<void>;
  stop: () => void;
}

export function useAudioPlayer(): UseAudioPlayerReturn {
  const [isPlaying, setIsPlaying] = useState(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const urlRef = useRef<string | null>(null);
  const resolveRef = useRef<(() => void) | null>(null);

  const cleanup = useCallback(() => {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.onended = null;
      audioRef.current.onerror = null;
      audioRef.current = null;
    }
    if (urlRef.current) {
      URL.revokeObjectURL(urlRef.current);
      urlRef.current = null;
    }
    // Resolve any pending play() promise so callers don't hang
    if (resolveRef.current) {
      resolveRef.current();
      resolveRef.current = null;
    }
    setIsPlaying(false);
  }, []);

  const play = useCallback(
    async (audioBlob: Blob) => {
      cleanup();

      const url = URL.createObjectURL(audioBlob);
      urlRef.current = url;

      const audio = new Audio(url);
      audioRef.current = audio;

      return new Promise<void>((resolve) => {
        resolveRef.current = resolve;
        audio.onended = () => {
          cleanup();
        };
        audio.onerror = () => {
          cleanup();
        };
        setIsPlaying(true);
        audio.play().catch(() => {
          cleanup();
        });
      });
    },
    [cleanup]
  );

  const stop = useCallback(() => {
    cleanup();
  }, [cleanup]);

  return { isPlaying, play, stop };
}
