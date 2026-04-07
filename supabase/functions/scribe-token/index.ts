import "jsr:@supabase/functions-js/edge-runtime.d.ts";

// ============================================================
// AURA — Edge Function: scribe-token
//
// Generates a single-use token for ElevenLabs Scribe Realtime v2
// Used by the frontend to authenticate WebSocket connections
// ============================================================

const ELEVENLABS_API_KEY = Deno.env.get("ELEVENLABS_API_KEY") || "";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers":
    "Content-Type, Authorization, X-Client-Info, Apikey",
};

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") {
    return new Response(null, { status: 200, headers: corsHeaders });
  }

  try {
    if (!ELEVENLABS_API_KEY) {
      return new Response(
        JSON.stringify({ error: "ELEVENLABS_API_KEY non configuré." }),
        { status: 400, headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

    const response = await fetch(
      "https://api.elevenlabs.io/v1/single-use-token/realtime_scribe",
      {
        method: "POST",
        headers: {
          "xi-api-key": ELEVENLABS_API_KEY,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({}),
      }
    );

    if (!response.ok) {
      const errText = await response.text();
      console.error("[scribe-token] ElevenLabs error:", response.status, errText);
      return new Response(
        JSON.stringify({ error: `ElevenLabs HTTP ${response.status}: ${errText}` }),
        { status: 502, headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

    const data = await response.json();
    console.log("[scribe-token] Token generated successfully");

    return new Response(JSON.stringify(data), {
      headers: { ...corsHeaders, "Content-Type": "application/json" },
    });
  } catch (err) {
    console.error("[scribe-token] Error:", err);
    return new Response(
      JSON.stringify({ error: err instanceof Error ? err.message : "Erreur inconnue" }),
      { status: 500, headers: { ...corsHeaders, "Content-Type": "application/json" } }
    );
  }
});
