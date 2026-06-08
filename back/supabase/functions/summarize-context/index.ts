import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "jsr:@supabase/supabase-js@2";

const ANTHROPIC_API_KEY = Deno.env.get("ANTHROPIC_API_KEY")!;
const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const SUPABASE_ANON_KEY = Deno.env.get("SUPABASE_ANON_KEY") || "";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, Authorization, Apikey, X-Client-Info",
};

const SUMMARIZE_SYSTEM_PROMPT = `Tu es un assistant spécialisé dans la synthèse de conversations et réunions en français.
Tu reçois une série de segments de transcription horodatés.
Produis un résumé structuré et concis en français qui capture :
- Les sujets principaux abordés
- Les décisions prises
- Les actions à faire (si mentionnées)
- Les personnes mentionnées

Format le résumé en texte continu avec des sections claires. Sois concis mais ne perds pas d'information importante.
Le résumé doit faire environ 20% de la longueur originale.`;

interface RequestBody {
  session_id: string;
  summary_type?: "rolling" | "session_final";
  // For rolling: summarize only unsummarized segments since last summary
  // For session_final: summarize all remaining unsummarized + mark session as summarized
}

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") {
    return new Response(null, { status: 200, headers: corsHeaders });
  }

  try {
    // Auth
    const authHeader = req.headers.get("Authorization") || "";
    const userJwt = authHeader.replace(/^Bearer\s+/i, "").trim();
    if (!userJwt) {
      return new Response(
        JSON.stringify({ error: "Missing Authorization" }),
        { status: 401, headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

    const supabase = createClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
      global: { headers: { Authorization: `Bearer ${userJwt}` } },
    });

    // Verify user
    const { data: { user }, error: authErr } = await supabase.auth.getUser(userJwt);
    if (authErr || !user) {
      return new Response(
        JSON.stringify({ error: "Invalid token" }),
        { status: 401, headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

    const body: RequestBody = await req.json();
    const { session_id, summary_type = "rolling" } = body;

    if (!session_id) {
      return new Response(
        JSON.stringify({ error: "Missing session_id" }),
        { status: 400, headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

    // Verify session belongs to user
    const { data: session, error: sessErr } = await supabase
      .from("listening_sessions")
      .select("id, user_id, status")
      .eq("id", session_id)
      .single();

    if (sessErr || !session || session.user_id !== user.id) {
      return new Response(
        JSON.stringify({ error: "Session not found" }),
        { status: 404, headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

    // Find the last summary end time for this session (to get only unsummarized segments)
    const { data: lastSummary } = await supabase
      .from("context_summaries")
      .select("time_end")
      .eq("session_id", session_id)
      .order("time_end", { ascending: false })
      .limit(1);

    const lastSummaryEnd = lastSummary?.[0]?.time_end || null;

    // Get unsummarized segments
    let segQuery = supabase
      .from("live_segments")
      .select("text, spoken_at")
      .eq("session_id", session_id)
      .order("spoken_at", { ascending: true })
      .limit(500);

    if (lastSummaryEnd) {
      segQuery = segQuery.gt("spoken_at", lastSummaryEnd);
    }

    const { data: segments, error: segErr } = await segQuery;

    if (segErr) {
      console.error("[summarize-context] Segment query error:", segErr.message);
      return new Response(
        JSON.stringify({ error: segErr.message }),
        { status: 500, headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

    if (!segments || segments.length < 5) {
      console.log(`[summarize-context] Too few unsummarized segments (${segments?.length || 0}), skipping`);
      return new Response(
        JSON.stringify({ status: "skipped", reason: "too_few_segments", count: segments?.length || 0 }),
        { status: 200, headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

    // Build text block
    const textBlock = segments
      .map((seg: { spoken_at: string; text: string }) => {
        const dt = new Date(seg.spoken_at).toLocaleString("fr-FR", { timeZone: "Europe/Paris" });
        return `[${dt}] ${seg.text}`;
      })
      .join("\n");

    const totalWords = segments.reduce(
      (sum: number, seg: { text: string }) => sum + seg.text.split(/\s+/).length, 0
    );

    console.log(
      `[summarize-context] Summarizing ${segments.length} segments (${totalWords} words) for session ${session_id} [${summary_type}]`
    );

    // Call Claude for summarization
    const anthropicResp = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
      },
      body: JSON.stringify({
        model: "claude-haiku-4-5-20251001",
        max_tokens: 2000,
        system: SUMMARIZE_SYSTEM_PROMPT,
        messages: [
          {
            role: "user",
            content: `Voici les segments de transcription à résumer :\n\n${textBlock}`,
          },
        ],
      }),
    });

    if (!anthropicResp.ok) {
      const errText = await anthropicResp.text();
      console.error("[summarize-context] Claude API error:", anthropicResp.status, errText);
      return new Response(
        JSON.stringify({ error: `Claude API error: ${anthropicResp.status}` }),
        { status: 502, headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

    const claudeResult = await anthropicResp.json();
    const summaryText = claudeResult.content?.[0]?.text || "";

    if (!summaryText) {
      return new Response(
        JSON.stringify({ error: "Empty summary from Claude" }),
        { status: 502, headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

    console.log(`[summarize-context] Summary generated: ${summaryText.length} chars`);

    // Store summary
    const actualStart = segments[0].spoken_at;
    const actualEnd = segments[segments.length - 1].spoken_at;

    const { error: insertErr } = await supabase
      .from("context_summaries")
      .insert({
        user_id: user.id,
        session_id,
        time_start: actualStart,
        time_end: actualEnd,
        segment_count: segments.length,
        summary_text: summaryText,
        summary_type: summary_type,
      });

    if (insertErr) {
      console.error("[summarize-context] Insert error:", insertErr.message);
      return new Response(
        JSON.stringify({ error: insertErr.message }),
        { status: 500, headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

    // If session_final, update session status
    if (summary_type === "session_final") {
      await supabase
        .from("listening_sessions")
        .update({
          status: "summarized",
          summary: { text: summaryText, segment_count: segments.length },
        })
        .eq("id", session_id);
    }

    console.log(`[summarize-context] Summary stored for session ${session_id} [${summary_type}]`);

    return new Response(
      JSON.stringify({
        status: "ok",
        summary_type,
        segment_count: segments.length,
        summary_length: summaryText.length,
      }),
      { status: 200, headers: { ...corsHeaders, "Content-Type": "application/json" } }
    );
  } catch (e) {
    console.error("[summarize-context] Unhandled error:", e);
    return new Response(
      JSON.stringify({ error: e instanceof Error ? e.message : "Internal error" }),
      { status: 500, headers: { ...corsHeaders, "Content-Type": "application/json" } }
    );
  }
});
