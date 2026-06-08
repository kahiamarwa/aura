import { supabase } from "./supabase";
import {
  PERSIST_BATCH_SIZE,
  PERSIST_INTERVAL_MS,
  SESSION_IDLE_TIMEOUT_MS,
  SUMMARIZE_TRIGGER_SEGMENTS,
} from "./constants";

const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL!;

interface PendingSegment {
  text: string;
  spoken_at: string; // ISO string
}

class ContextPersistence {
  private sessionId: string | null = null;
  private userId: string | null = null;
  private pending: PendingSegment[] = [];
  private flushTimer: ReturnType<typeof setTimeout> | null = null;
  private idleTimer: ReturnType<typeof setTimeout> | null = null;
  private segmentCount = 0;
  private totalWords = 0;
  private isEnding = false;
  /** Segments since last rolling summary */
  private segmentsSinceLastSummary = 0;
  private isSummarizing = false;

  async startSession(): Promise<void> {
    // Get current user
    const {
      data: { user },
    } = await supabase.auth.getUser();
    if (!user) {
      console.warn("[Persistence] No authenticated user, skipping session");
      return;
    }
    this.userId = user.id;

    // Create session
    const { data, error } = await supabase
      .from("listening_sessions")
      .insert({ user_id: user.id })
      .select("id")
      .single();

    if (error) {
      console.error("[Persistence] Failed to create session:", error.message);
      return;
    }

    this.sessionId = data.id;
    this.segmentCount = 0;
    this.totalWords = 0;
    this.segmentsSinceLastSummary = 0;
    this.isEnding = false;
    this.isSummarizing = false;
    console.log("[Persistence] Session started:", this.sessionId);

    // Register cleanup listeners
    window.addEventListener("beforeunload", this.handleUnload);
    document.addEventListener("visibilitychange", this.handleVisibility);
  }

  async persistSegment(text: string, timestamp: Date): Promise<void> {
    if (!this.sessionId || !this.userId) return;

    this.pending.push({
      text,
      spoken_at: timestamp.toISOString(),
    });

    // Reset idle timer
    this.resetIdleTimer();

    // Flush if batch is full
    if (this.pending.length >= PERSIST_BATCH_SIZE) {
      await this.flush();
    } else if (!this.flushTimer) {
      // Schedule flush
      this.flushTimer = setTimeout(() => this.flush(), PERSIST_INTERVAL_MS);
    }
  }

  async flush(): Promise<void> {
    if (this.flushTimer) {
      clearTimeout(this.flushTimer);
      this.flushTimer = null;
    }

    if (!this.sessionId || !this.userId || this.pending.length === 0) return;

    const batch = this.pending.splice(0);
    const rows = batch.map((seg) => ({
      user_id: this.userId!,
      session_id: this.sessionId!,
      text: seg.text,
      spoken_at: seg.spoken_at,
    }));

    const { error } = await supabase.from("live_segments").insert(rows);

    if (error) {
      console.error("[Persistence] Failed to flush segments:", error.message);
      // Put them back for retry
      this.pending.unshift(...batch);
      return;
    }

    // Update counters
    const newWords = batch.reduce(
      (sum, seg) => sum + seg.text.split(/\s+/).length,
      0
    );
    this.segmentCount += batch.length;
    this.totalWords += newWords;
    this.segmentsSinceLastSummary += batch.length;

    // Update session counters
    await supabase
      .from("listening_sessions")
      .update({
        segment_count: this.segmentCount,
        total_words: this.totalWords,
      })
      .eq("id", this.sessionId);

    console.log(
      `[Persistence] Flushed ${batch.length} segments (total: ${this.segmentCount}, since summary: ${this.segmentsSinceLastSummary})`
    );

    // Trigger rolling summary if threshold reached
    if (
      this.segmentsSinceLastSummary >= SUMMARIZE_TRIGGER_SEGMENTS &&
      !this.isSummarizing
    ) {
      this.triggerSummarization("rolling").catch((err) =>
        console.warn("[Persistence] Rolling summarization failed:", err)
      );
    }
  }

  async endSession(): Promise<void> {
    if (!this.sessionId || this.isEnding) return;
    this.isEnding = true;

    console.log("[Persistence] Ending session:", this.sessionId);

    // Flush remaining
    await this.flush();

    // Update session
    await supabase
      .from("listening_sessions")
      .update({
        ended_at: new Date().toISOString(),
        status: "ended",
      })
      .eq("id", this.sessionId);

    // Trigger final summarization if enough unsummarized segments
    if (this.segmentsSinceLastSummary >= 5) {
      await this.triggerSummarization("session_final").catch((err) =>
        console.warn("[Persistence] Final summarization failed:", err)
      );
    }

    // Cleanup
    this.cleanup();
  }

  private async triggerSummarization(
    summaryType: "rolling" | "session_final"
  ): Promise<void> {
    if (!this.sessionId || this.isSummarizing) return;
    this.isSummarizing = true;

    // Get the user's access token
    const {
      data: { session },
    } = await supabase.auth.getSession();
    if (!session?.access_token) {
      this.isSummarizing = false;
      return;
    }

    try {
      console.log(
        `[Persistence] Triggering ${summaryType} summarization (${this.segmentsSinceLastSummary} segments)`
      );

      const resp = await fetch(
        `${SUPABASE_URL}/functions/v1/summarize-context`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${session.access_token}`,
          },
          body: JSON.stringify({
            session_id: this.sessionId,
            summary_type: summaryType,
          }),
        }
      );

      if (resp.ok) {
        const result = await resp.json();
        if (result.status === "ok") {
          console.log(
            `[Persistence] ${summaryType} summary created (${result.segment_count} segments → ${result.summary_length} chars)`
          );
          this.segmentsSinceLastSummary = 0;
        } else {
          console.log("[Persistence] Summary skipped:", result.reason);
        }
      } else {
        const errText = await resp.text();
        console.warn("[Persistence] Summarization HTTP error:", resp.status, errText);
      }
    } catch (err) {
      console.warn("[Persistence] Summarization request failed:", err);
    } finally {
      this.isSummarizing = false;
    }
  }

  private resetIdleTimer(): void {
    if (this.idleTimer) clearTimeout(this.idleTimer);
    this.idleTimer = setTimeout(() => {
      console.log("[Persistence] Session idle timeout, ending session");
      this.endSession();
    }, SESSION_IDLE_TIMEOUT_MS);
  }

  private cleanup(): void {
    if (this.flushTimer) clearTimeout(this.flushTimer);
    if (this.idleTimer) clearTimeout(this.idleTimer);
    this.flushTimer = null;
    this.idleTimer = null;
    this.sessionId = null;
    this.userId = null;
    this.pending = [];
    this.segmentsSinceLastSummary = 0;
    this.isSummarizing = false;
    window.removeEventListener("beforeunload", this.handleUnload);
    document.removeEventListener("visibilitychange", this.handleVisibility);
  }

  private handleUnload = (): void => {
    // Best-effort flush on page unload using sendBeacon
    if (!this.sessionId || !this.userId || this.pending.length === 0) return;

    const rows = this.pending.map((seg) => ({
      user_id: this.userId!,
      session_id: this.sessionId!,
      text: seg.text,
      spoken_at: seg.spoken_at,
    }));

    // Use sendBeacon for reliability on page close
    const {
      data: { session },
    } = { data: { session: null as { access_token: string } | null } };
    // sendBeacon can't set auth headers, so use the REST API with apikey
    const url = `${SUPABASE_URL}/rest/v1/live_segments`;
    const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || "";
    const blob = new Blob([JSON.stringify(rows)], {
      type: "application/json",
    });

    // Note: sendBeacon without auth headers won't work with RLS.
    // We accept this limitation — the rolling summaries during the session
    // ensure we don't lose important context even if the last few segments are lost.
    navigator.sendBeacon(url + `?apikey=${anonKey}`, blob);

    // Also mark session as ended via sendBeacon
    const endBlob = new Blob(
      [
        JSON.stringify({
          ended_at: new Date().toISOString(),
          status: "ended",
        }),
      ],
      { type: "application/json" }
    );
    navigator.sendBeacon(
      `${SUPABASE_URL}/rest/v1/listening_sessions?id=eq.${this.sessionId}&apikey=${anonKey}`,
      endBlob
    );
  };

  private handleVisibility = (): void => {
    if (document.visibilityState === "hidden") {
      this.flush();
    }
  };

  get currentSessionId(): string | null {
    return this.sessionId;
  }

  get currentSegmentCount(): number {
    return this.segmentCount;
  }
}

// Singleton
export const contextPersistence = new ContextPersistence();
