"use client";

import { useEffect, useRef } from "react";

export interface ConversationEntry {
  id: string;
  timestamp: Date;
  command: string;
  response: string;
}

interface TranscriptPanelProps {
  history: ConversationEntry[];
}

export function TranscriptPanel({ history }: TranscriptPanelProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [history]);

  if (history.length === 0) return null;

  return null; // Messages are now shown inline in page.tsx
}
