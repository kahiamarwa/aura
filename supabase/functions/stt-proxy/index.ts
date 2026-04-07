import "jsr:@supabase/functions-js/edge-runtime.d.ts";

// ============================================================
// AURA — Edge Function: stt-proxy
//
// CORS proxy for STT providers that block direct browser access:
// - AssemblyAI (token + transcription)
// - OpenAI (transcription)
// - Mistral (transcription)
// - Google Gemini (transcription)
// ============================================================

const ASSEMBLYAI_API_KEY = Deno.env.get("ASSEMBLYAI_API_KEY") || "";
const OPENAI_API_KEY = Deno.env.get("OPENAI_API_KEY") || "";
const MISTRAL_API_KEY = Deno.env.get("MISTRAL_API_KEY") || "";
const GOOGLE_GEMINI_API_KEY = Deno.env.get("GOOGLE_GEMINI_API_KEY") || "";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers":
    "Content-Type, Authorization, X-Client-Info, Apikey",
};

function jsonResponse(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { ...corsHeaders, "Content-Type": "application/json" },
  });
}

function errorResponse(msg: string, status = 500) {
  return jsonResponse({ error: msg }, status);
}

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") {
    return new Response(null, { status: 200, headers: corsHeaders });
  }

  try {
    const body = await req.json();
    const { action } = body;

    switch (action) {
      // ── AssemblyAI: get v3 streaming token ──
      case "assemblyai-token": {
        if (!ASSEMBLYAI_API_KEY) return errorResponse("ASSEMBLYAI_API_KEY not set", 400);
        const res = await fetch(
          "https://streaming.assemblyai.com/v3/token?expires_in_seconds=600",
          {
            method: "GET",
            headers: {
              Authorization: ASSEMBLYAI_API_KEY,
            },
          }
        );
        if (!res.ok) {
          const err = await res.text();
          return errorResponse(`AssemblyAI ${res.status}: ${err}`, 502);
        }
        return jsonResponse(await res.json());
      }

      // ── OpenAI: transcribe audio ──
      case "openai-transcribe": {
        if (!OPENAI_API_KEY) return errorResponse("OPENAI_API_KEY not set", 400);
        const { audio_base64, model, language } = body;
        // Decode base64 WAV
        const binaryString = atob(audio_base64);
        const bytes = new Uint8Array(binaryString.length);
        for (let i = 0; i < binaryString.length; i++) {
          bytes[i] = binaryString.charCodeAt(i);
        }
        const blob = new Blob([bytes], { type: "audio/wav" });

        const formData = new FormData();
        formData.append("file", blob, "audio.wav");
        formData.append("model", model || "gpt-4o-mini-transcribe");
        formData.append("language", language || "fr");

        const res = await fetch("https://api.openai.com/v1/audio/transcriptions", {
          method: "POST",
          headers: { Authorization: `Bearer ${OPENAI_API_KEY}` },
          body: formData,
        });
        if (!res.ok) {
          const err = await res.text();
          return errorResponse(`OpenAI ${res.status}: ${err}`, 502);
        }
        return jsonResponse(await res.json());
      }

      // ── Mistral: transcribe audio ──
      case "mistral-transcribe": {
        if (!MISTRAL_API_KEY) return errorResponse("MISTRAL_API_KEY not set", 400);
        const { audio_base64: mAudio } = body;
        const res = await fetch("https://api.mistral.ai/v1/audio/transcriptions", {
          method: "POST",
          headers: {
            Authorization: `Bearer ${MISTRAL_API_KEY}`,
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            model: "mistral-small-latest",
            audio: { type: "base64", data: mAudio },
            language: "fr",
          }),
        });
        if (!res.ok) {
          const err = await res.text();
          return errorResponse(`Mistral ${res.status}: ${err}`, 502);
        }
        return jsonResponse(await res.json());
      }

      // ── Google Gemini: transcribe audio ──
      case "gemini-transcribe": {
        if (!GOOGLE_GEMINI_API_KEY) return errorResponse("GOOGLE_GEMINI_API_KEY not set", 400);
        const { audio_base64: gAudio, model: gModel } = body;
        const geminiModel = gModel || "gemini-3-flash-preview";
        const res = await fetch(
          `https://generativelanguage.googleapis.com/v1beta/models/${geminiModel}:generateContent?key=${GOOGLE_GEMINI_API_KEY}`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              contents: [{
                parts: [
                  { text: "Transcris cet audio en français. Retourne UNIQUEMENT le texte transcrit, sans aucune explication ni formatage." },
                  { inline_data: { mime_type: "audio/wav", data: gAudio } },
                ],
              }],
            }),
          }
        );
        if (!res.ok) {
          const err = await res.text();
          return errorResponse(`Gemini ${res.status}: ${err}`, 502);
        }
        const result = await res.json();
        const text = result.candidates?.[0]?.content?.parts?.[0]?.text?.trim() || "";
        return jsonResponse({ text });
      }

      default:
        return errorResponse(`Unknown action: ${action}`, 400);
    }
  } catch (err) {
    console.error("[stt-proxy] Error:", err);
    return errorResponse(err instanceof Error ? err.message : "Unknown error");
  }
});
