import { MAX_CONTEXT_AGE_MS, MAX_CONTEXT_SEGMENTS } from "./constants";
import type { TranscriptionSegment } from "./api";

export interface ContextEntry {
  text: string;
  timestamp: Date;
}

class ContextBuffer {
  private entries: ContextEntry[] = [];

  add(text: string) {
    if (!text.trim()) return;
    this.entries.push({ text: text.trim(), timestamp: new Date() });
    this.prune();
  }

  getContext(): TranscriptionSegment[] {
    this.prune();
    return this.entries.map((e) => ({
      text: e.text,
      timestamp: e.timestamp.toISOString(),
      is_partial: false,
    }));
  }

  getEntries(): ContextEntry[] {
    this.prune();
    return [...this.entries];
  }

  clear() {
    this.entries = [];
  }

  get size() {
    return this.entries.length;
  }

  private prune() {
    const now = Date.now();
    // Remove old entries
    this.entries = this.entries.filter(
      (e) => now - e.timestamp.getTime() < MAX_CONTEXT_AGE_MS
    );
    // Keep only last N entries
    if (this.entries.length > MAX_CONTEXT_SEGMENTS) {
      this.entries = this.entries.slice(-MAX_CONTEXT_SEGMENTS);
    }
  }
}

// Singleton
export const contextBuffer = new ContextBuffer();
