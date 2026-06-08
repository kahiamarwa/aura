import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { getUserFromRequest } from "../_shared/auth.ts";

// ============================================================
// AURA — Edge Function: send-sms
//
// Handles:
//   POST /send-sms { action: "send", to, message }
//   POST /send-sms { action: "save-config", account_sid, auth_token, phone_number }
//   POST /send-sms { action: "get-config" }
//   POST /send-sms { action: "delete-config" }
//
// Uses per-user Twilio credentials from twilio_integrations table
// ============================================================

const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const SUPABASE_SERVICE_ROLE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
  "Access-Control-Allow-Headers":
    "Content-Type, Authorization, X-Client-Info, Apikey",
};

function jsonResponse(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { ...corsHeaders, "Content-Type": "application/json" },
  });
}

// ─── Normalize phone number to E.164 format ─────────────────
function normalizePhoneNumber(phone: string): string {
  let cleaned = phone.replace(/[\s.\-()]/g, "");

  // French number without country code: 06... → +336...
  if (cleaned.startsWith("0") && cleaned.length === 10) {
    cleaned = "+33" + cleaned.substring(1);
  }

  if (!cleaned.startsWith("+")) {
    cleaned = "+" + cleaned;
  }

  return cleaned;
}

// ─── Send SMS via Twilio ─────────────────────────────────────
async function sendTwilioSMS(
  accountSid: string,
  authToken: string,
  fromNumber: string,
  to: string,
  message: string,
): Promise<{ sid: string }> {
  const url = `https://api.twilio.com/2010-04-01/Accounts/${accountSid}/Messages.json`;
  const credentials = btoa(`${accountSid}:${authToken}`);

  const res = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Basic ${credentials}`,
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body: new URLSearchParams({
      To: to,
      From: fromNumber,
      Body: message,
    }),
  });

  if (!res.ok) {
    const err = await res.text();
    throw new Error(`Twilio SMS failed (${res.status}): ${err}`);
  }

  const result = await res.json();
  return { sid: result.sid };
}

// ─── Get user's Twilio config from DB ────────────────────────
// deno-lint-ignore no-explicit-any
async function getUserTwilioConfig(supabase: any, userId: string) {
  const { data, error } = await supabase
    .from("twilio_integrations")
    .select("account_sid, auth_token, phone_number")
    .eq("user_id", userId)
    .single();

  if (error || !data) return null;
  return data;
}

// ═══════════════════════════════════════════════════════════════
// HANDLER PRINCIPAL
// ═══════════════════════════════════════════════════════════════

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") {
    return new Response(null, { status: 200, headers: corsHeaders });
  }

  try {
    let userId: string;
    try {
      const authUser = await getUserFromRequest(req);
      userId = authUser.user_id;
    } catch (authErr) {
      return jsonResponse({ error: authErr instanceof Error ? authErr.message : "Non autorisé" }, 401);
    }

    const { action, ...params } = await req.json();
    const supabase = createClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY);

    // ── ACTION: Save Twilio config ──
    if (action === "save-config") {
      const { account_sid, auth_token, phone_number } = params;

      if (!account_sid || !auth_token || !phone_number) {
        return jsonResponse({ error: "account_sid, auth_token et phone_number sont requis" }, 400);
      }

      // Validate by making a test API call to Twilio
      try {
        const testUrl = `https://api.twilio.com/2010-04-01/Accounts/${account_sid}.json`;
        const testCreds = btoa(`${account_sid}:${auth_token}`);
        const testRes = await fetch(testUrl, {
          headers: { Authorization: `Basic ${testCreds}` },
        });
        if (!testRes.ok) {
          return jsonResponse({ error: "Identifiants Twilio invalides. Vérifiez votre Account SID et Auth Token." }, 400);
        }
      } catch {
        return jsonResponse({ error: "Impossible de vérifier les identifiants Twilio." }, 400);
      }

      // Upsert: delete existing then insert
      await supabase
        .from("twilio_integrations")
        .delete()
        .eq("user_id", userId);

      const { error: insertError } = await supabase
        .from("twilio_integrations")
        .insert({
          user_id: userId,
          account_sid,
          auth_token,
          phone_number: normalizePhoneNumber(phone_number),
        });

      if (insertError) {
        throw new Error(`DB insert failed: ${insertError.message}`);
      }

      console.log(`[send-sms] Twilio config saved for user ${userId}`);
      return jsonResponse({
        success: true,
        phone_number: normalizePhoneNumber(phone_number),
      });
    }

    // ── ACTION: Get Twilio config ──
    if (action === "get-config") {
      const config = await getUserTwilioConfig(supabase, userId);
      if (!config) {
        return jsonResponse({ configured: false });
      }
      return jsonResponse({
        configured: true,
        phone_number: config.phone_number,
        account_sid_preview: config.account_sid.slice(0, 8) + "...",
      });
    }

    // ── ACTION: Delete Twilio config ──
    if (action === "delete-config") {
      await supabase
        .from("twilio_integrations")
        .delete()
        .eq("user_id", userId);

      console.log(`[send-sms] Twilio config deleted for user ${userId}`);
      return jsonResponse({ success: true });
    }

    // ── ACTION: Send SMS ──
    if (action === "send") {
      const { to, message } = params;

      if (!to || !message) {
        return jsonResponse({ error: "to et message sont requis" }, 400);
      }

      // Get user's Twilio config from DB
      const config = await getUserTwilioConfig(supabase, userId);
      if (!config) {
        return jsonResponse(
          { error: "Twilio n'est pas configuré. Allez dans Paramètres → Connecteurs pour ajouter vos identifiants Twilio." },
          400
        );
      }

      const normalizedTo = normalizePhoneNumber(to);
      const result = await sendTwilioSMS(
        config.account_sid,
        config.auth_token,
        config.phone_number,
        normalizedTo,
        message,
      );

      console.log(`[send-sms] SMS envoyé à ${normalizedTo} (SID: ${result.sid})`);

      // Save SMS to database
      const { error: dbError } = await supabase
        .from("sms_messages")
        .insert({
          user_id: userId,
          twilio_sid: result.sid,
          to_number: normalizedTo,
          from_number: config.phone_number,
          message,
          status: "sent",
        });

      if (dbError) {
        console.warn(`[send-sms] DB save failed (SMS was sent): ${dbError.message}`);
      }

      return jsonResponse({
        success: true,
        sid: result.sid,
        message: `SMS envoyé à ${normalizedTo}.`,
      });
    }

    return jsonResponse({ error: `Action inconnue: ${action}` }, 400);
  } catch (err) {
    console.error("[send-sms] Error:", err);
    return jsonResponse(
      { error: err instanceof Error ? err.message : "Erreur inconnue" },
      500
    );
  }
});
