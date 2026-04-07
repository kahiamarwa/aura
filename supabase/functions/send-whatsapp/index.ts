import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

// ============================================================
// AURA — Edge Function: send-whatsapp
//
// Handles:
//   POST /send-whatsapp { action: "send", to, message }
//   POST /send-whatsapp { action: "save-config", access_token, phone_number_id }
//   POST /send-whatsapp { action: "get-config" }
//   POST /send-whatsapp { action: "delete-config" }
//   POST /send-whatsapp { action: "oauth-callback", code }  ← Meta Embedded Signup
//
// Uses per-user Meta WhatsApp Business API credentials
// ============================================================

const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const SUPABASE_ANON_KEY = Deno.env.get("SUPABASE_ANON_KEY")!;
const SUPABASE_SERVICE_ROLE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
const META_APP_ID = Deno.env.get("META_APP_ID") || "";
const META_APP_SECRET = Deno.env.get("META_APP_SECRET") || "";

// ─── Auth helper (inlined from _shared/auth.ts) ─────────────
async function getUserFromRequest(
  req: Request
): Promise<{ user_id: string; email: string }> {
  const authHeader = req.headers.get("Authorization");
  if (!authHeader?.startsWith("Bearer ")) {
    throw new Error("Token d'authentification manquant");
  }

  const token = authHeader.replace("Bearer ", "");

  const supabase = createClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
    global: { headers: { Authorization: `Bearer ${token}` } },
  });

  const {
    data: { user },
    error,
  } = await supabase.auth.getUser(token);

  if (error || !user) {
    throw new Error("Token invalide ou expiré. Veuillez vous reconnecter.");
  }

  return { user_id: user.id, email: user.email || "" };
}

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

  if (cleaned.startsWith("0") && cleaned.length === 10) {
    cleaned = "+33" + cleaned.substring(1);
  }

  if (!cleaned.startsWith("+")) {
    cleaned = "+" + cleaned;
  }

  return cleaned;
}

// ─── Send WhatsApp message via Meta API ──────────────────────
async function sendWhatsAppMessage(
  accessToken: string,
  phoneNumberId: string,
  to: string,
  message: string,
  useTemplate = false,
  templateName = "hello_world",
  templateLang = "en_US",
): Promise<{ messageId: string; mode: "text" | "template" }> {
  const url = `https://graph.facebook.com/v22.0/${phoneNumberId}/messages`;
  const toNumber = to.replace("+", "");

  // Build payload based on mode
  // deno-lint-ignore no-explicit-any
  let payload: Record<string, any>;

  if (useTemplate) {
    // Template message — works outside 24h window
    payload = {
      messaging_product: "whatsapp",
      to: toNumber,
      type: "template",
      template: {
        name: templateName,
        language: { code: templateLang },
      },
    };
    console.log(`[send-whatsapp] Envoi template "${templateName}" à ${to}`);
  } else {
    // Free-form text — only works inside 24h window
    payload = {
      messaging_product: "whatsapp",
      recipient_type: "individual",
      to: toNumber,
      type: "text",
      text: { body: message },
    };
  }

  const res = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });

  if (!res.ok) {
    const err = await res.text();
    throw new Error(`WhatsApp API failed (${res.status}): ${err}`);
  }

  const result = await res.json();
  const messageId = result.messages?.[0]?.id || "unknown";
  return { messageId, mode: useTemplate ? "template" : "text" };
}

// ─── Send WhatsApp media (image or document) via Meta API ────
async function sendWhatsAppMedia(
  accessToken: string,
  phoneNumberId: string,
  to: string,
  mediaType: "image" | "document",
  mediaUrl: string,
  caption?: string,
  filename?: string,
): Promise<{ messageId: string }> {
  const url = `https://graph.facebook.com/v22.0/${phoneNumberId}/messages`;
  const toNumber = to.replace("+", "");

  // deno-lint-ignore no-explicit-any
  let mediaPayload: Record<string, any>;

  if (mediaType === "image") {
    mediaPayload = {
      messaging_product: "whatsapp",
      to: toNumber,
      type: "image",
      image: {
        link: mediaUrl,
        ...(caption ? { caption } : {}),
      },
    };
  } else {
    mediaPayload = {
      messaging_product: "whatsapp",
      to: toNumber,
      type: "document",
      document: {
        link: mediaUrl,
        ...(filename ? { filename } : {}),
        ...(caption ? { caption } : {}),
      },
    };
  }

  console.log(`[send-whatsapp] Envoi ${mediaType} à ${to} — URL: ${mediaUrl.substring(0, 80)}...`);

  const res = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(mediaPayload),
  });

  if (!res.ok) {
    const err = await res.text();
    throw new Error(`WhatsApp Media API failed (${res.status}): ${err}`);
  }

  const result = await res.json();
  const messageId = result.messages?.[0]?.id || "unknown";
  return { messageId };
}

// ─── Meta Embedded Signup: register phone number ─────────────
async function registerPhoneNumber(accessToken: string, phoneNumberId: string): Promise<void> {
  const url = `https://graph.facebook.com/v22.0/${phoneNumberId}/register`;
  const res = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      messaging_product: "whatsapp",
      pin: "123456",
    }),
  });
  if (!res.ok) {
    const err = await res.text();
    console.warn(`[send-whatsapp] Register phone failed (${res.status}): ${err}`);
    // Don't throw — phone may already be registered
  } else {
    console.log(`[send-whatsapp] Phone ${phoneNumberId} registered successfully`);
  }
}

// ─── Meta Embedded Signup: exchange code for token ───────────
async function exchangeCodeForToken(code: string): Promise<string> {
  const url = new URL("https://graph.facebook.com/v22.0/oauth/access_token");
  url.searchParams.set("client_id", META_APP_ID);
  url.searchParams.set("client_secret", META_APP_SECRET);
  url.searchParams.set("code", code);

  const res = await fetch(url.toString());
  if (!res.ok) {
    const err = await res.text();
    throw new Error(`Meta token exchange failed (${res.status}): ${err}`);
  }
  const data = await res.json();
  return data.access_token;
}

// ─── Meta Embedded Signup: get WABA + phone number from token ─
async function getWABAInfo(userAccessToken: string): Promise<{
  waba_id: string;
  phone_number_id: string;
  display_phone: string;
  verified_name: string;
}> {
  // Step 1: debug_token to extract WABA ID from granted scopes
  const debugRes = await fetch(
    `https://graph.facebook.com/v22.0/debug_token?input_token=${userAccessToken}&access_token=${META_APP_ID}|${META_APP_SECRET}`
  );
  if (!debugRes.ok) {
    const err = await debugRes.text();
    throw new Error(`debug_token failed (${debugRes.status}): ${err}`);
  }
  const debugData = await debugRes.json();

  // deno-lint-ignore no-explicit-any
  const scopes = debugData.data?.granular_scopes || [];
  console.log("[send-whatsapp] debug_token granular_scopes:", JSON.stringify(scopes));

  // Try whatsapp_business_management first, then whatsapp_business_messaging
  // deno-lint-ignore no-explicit-any
  const waScope = scopes.find((s: any) =>
    s.permission === "whatsapp_business_management" && s.target_ids?.length
  ) || scopes.find((s: any) =>
    s.permission === "whatsapp_business_messaging" && s.target_ids?.length
  );
  const wabaId = waScope?.target_ids?.[0];

  if (!wabaId) {
    throw new Error(
      "Aucun compte WhatsApp Business trouvé. Scopes disponibles: " +
      scopes.map((s: any) => `${s.permission}(${s.target_ids?.join(",") || "none"})`).join(", ")
    );
  }

  // Step 2: Get phone numbers for this WABA
  const phonesRes = await fetch(
    `https://graph.facebook.com/v22.0/${wabaId}/phone_numbers?fields=display_phone_number,verified_name,id`,
    { headers: { Authorization: `Bearer ${userAccessToken}` } }
  );
  if (!phonesRes.ok) {
    const err = await phonesRes.text();
    throw new Error(`Failed to fetch phone numbers (${phonesRes.status}): ${err}`);
  }
  const phonesData = await phonesRes.json();
  const phone = phonesData.data?.[0];

  if (!phone) {
    throw new Error(
      "Aucun numéro de téléphone trouvé sur le compte WhatsApp Business."
    );
  }

  return {
    waba_id: wabaId,
    phone_number_id: phone.id,
    display_phone: phone.display_phone_number || "",
    verified_name: phone.verified_name || "",
  };
}

// ─── Get user's WhatsApp config from DB ──────────────────────
// deno-lint-ignore no-explicit-any
async function getUserWhatsAppConfig(supabase: any, userId: string) {
  const { data, error } = await supabase
    .from("whatsapp_integrations")
    .select("access_token, phone_number_id, display_phone")
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

    // ── ACTION: Save WhatsApp config ──
    if (action === "save-config") {
      const { access_token, phone_number_id } = params;

      if (!access_token || !phone_number_id) {
        return jsonResponse({ error: "access_token et phone_number_id sont requis" }, 400);
      }

      // Validate by calling Meta API
      let displayPhone = "";
      try {
        const testRes = await fetch(
          `https://graph.facebook.com/v22.0/${phone_number_id}?fields=display_phone_number,verified_name`,
          { headers: { Authorization: `Bearer ${access_token}` } }
        );
        if (!testRes.ok) {
          const errBody = await testRes.text();
          console.error("[send-whatsapp] Validation failed:", errBody);
          return jsonResponse({ error: "Identifiants WhatsApp invalides. Vérifiez votre Access Token et Phone Number ID." }, 400);
        }
        const phoneInfo = await testRes.json();
        displayPhone = phoneInfo.display_phone_number || "";
      } catch {
        return jsonResponse({ error: "Impossible de vérifier les identifiants WhatsApp." }, 400);
      }

      // Upsert
      await supabase
        .from("whatsapp_integrations")
        .delete()
        .eq("user_id", userId);

      const { error: insertError } = await supabase
        .from("whatsapp_integrations")
        .insert({
          user_id: userId,
          access_token,
          phone_number_id,
          display_phone: displayPhone,
        });

      if (insertError) {
        throw new Error(`DB insert failed: ${insertError.message}`);
      }

      console.log(`[send-whatsapp] Config saved for user ${userId} (${displayPhone})`);
      return jsonResponse({
        success: true,
        display_phone: displayPhone,
      });
    }

    // ── ACTION: Get WhatsApp config ──
    if (action === "get-config") {
      const config = await getUserWhatsAppConfig(supabase, userId);
      if (!config) {
        return jsonResponse({ configured: false });
      }
      return jsonResponse({
        configured: true,
        display_phone: config.display_phone || config.phone_number_id,
      });
    }

    // ── ACTION: Delete WhatsApp config ──
    if (action === "delete-config") {
      await supabase
        .from("whatsapp_integrations")
        .delete()
        .eq("user_id", userId);

      console.log(`[send-whatsapp] Config deleted for user ${userId}`);
      return jsonResponse({ success: true });
    }

    // ── ACTION: OAuth callback — Meta Embedded Signup ──
    if (action === "oauth-callback") {
      const { code, waba_id: frontendWabaId, phone_number_id: frontendPhoneId } = params;

      if (!code) {
        return jsonResponse({ error: "code est requis" }, 400);
      }
      if (!META_APP_ID || !META_APP_SECRET) {
        return jsonResponse({ error: "Configuration Meta (APP_ID/APP_SECRET) manquante côté serveur." }, 500);
      }

      console.log("[send-whatsapp] oauth-callback received:", { frontendWabaId, frontendPhoneId, hasCode: !!code });

      // Exchange authorization code for access token
      const accessToken = await exchangeCodeForToken(code as string);

      let wabaId = frontendWabaId as string | undefined;
      let phoneNumberId = frontendPhoneId as string | undefined;
      let displayPhone = "";
      let verifiedName = "";

      if (wabaId && phoneNumberId) {
        // Use info sent directly from Embedded Signup session event
        console.log(`[send-whatsapp] Using Embedded Signup session info: WABA=${wabaId}, phone=${phoneNumberId}`);

        // Fetch display phone from the phone number ID
        try {
          const phoneRes = await fetch(
            `https://graph.facebook.com/v22.0/${phoneNumberId}?fields=display_phone_number,verified_name`,
            { headers: { Authorization: `Bearer ${accessToken}` } }
          );
          if (phoneRes.ok) {
            const phoneData = await phoneRes.json();
            displayPhone = phoneData.display_phone_number || "";
            verifiedName = phoneData.verified_name || "";
          }
        } catch (e) {
          console.warn("[send-whatsapp] Could not fetch phone details:", e);
        }
      } else {
        // Fallback: try to extract WABA info from the token
        console.log("[send-whatsapp] No session info from frontend, falling back to debug_token...");
        const wabaInfo = await getWABAInfo(accessToken);
        wabaId = wabaInfo.waba_id;
        phoneNumberId = wabaInfo.phone_number_id;
        displayPhone = wabaInfo.display_phone;
        verifiedName = wabaInfo.verified_name;
      }

      // Register the phone number with Meta (required after Embedded Signup)
      if (phoneNumberId) {
        await registerPhoneNumber(accessToken, phoneNumberId);
      }

      // Upsert: delete existing then insert
      await supabase
        .from("whatsapp_integrations")
        .delete()
        .eq("user_id", userId);

      const { error: insertError } = await supabase
        .from("whatsapp_integrations")
        .insert({
          user_id: userId,
          access_token: accessToken,
          phone_number_id: phoneNumberId,
          display_phone: displayPhone,
          waba_id: wabaId,
          signup_method: "embedded_signup",
        });

      if (insertError) {
        throw new Error(`DB insert failed: ${insertError.message}`);
      }

      console.log(
        `[send-whatsapp] Embedded Signup OK for user ${userId} — WABA: ${wabaId}, phone: ${displayPhone}`
      );

      return jsonResponse({
        success: true,
        display_phone: displayPhone,
        verified_name: verifiedName,
      });
    }

    // ── ACTION: Send WhatsApp message ──
    if (action === "send") {
      const {
        to, message, use_template, template_name, template_lang,
        media_type, file_path, media_url, file_name, caption,
      } = params;

      if (!to) {
        return jsonResponse({ error: "Le champ 'to' est requis" }, 400);
      }

      const config = await getUserWhatsAppConfig(supabase, userId);
      if (!config) {
        return jsonResponse(
          { error: "WhatsApp n'est pas configuré. Allez dans Paramètres → Connecteurs pour ajouter vos identifiants WhatsApp Business." },
          400
        );
      }

      const normalizedTo = normalizePhoneNumber(to);

      // ── Media mode (image or document) ──
      if (media_type === "image" || media_type === "document") {
        // Resolve media URL
        let resolvedUrl = media_url as string | undefined;

        if (!resolvedUrl && file_path) {
          // Generate signed URL from Supabase Storage
          const { data: signedData, error: signedError } = await supabase
            .storage
            .from("presentations")
            .createSignedUrl(file_path as string, 3600); // 1 hour

          if (signedError || !signedData?.signedUrl) {
            return jsonResponse(
              { error: `Impossible de générer l'URL pour le fichier: ${signedError?.message || "fichier introuvable"}` },
              400
            );
          }
          resolvedUrl = signedData.signedUrl;
          console.log(`[send-whatsapp] Signed URL générée pour ${file_path}`);
        }

        if (!resolvedUrl) {
          return jsonResponse(
            { error: "media_url ou file_path est requis pour l'envoi de média" },
            400
          );
        }

        const result = await sendWhatsAppMedia(
          config.access_token,
          config.phone_number_id,
          normalizedTo,
          media_type as "image" | "document",
          resolvedUrl,
          caption as string | undefined,
          (file_name || undefined) as string | undefined,
        );

        console.log(`[send-whatsapp] ${media_type} envoyé à ${normalizedTo} (ID: ${result.messageId})`);

        // Save to database
        const dbMsg = media_type === "image"
          ? `[Image] ${caption || ""}`
          : `[Document: ${file_name || "fichier"}] ${caption || ""}`;

        const { error: dbError } = await supabase
          .from("whatsapp_messages")
          .insert({
            user_id: userId,
            wa_message_id: result.messageId,
            to_number: normalizedTo,
            message: dbMsg.trim(),
            status: "sent",
          });

        if (dbError) {
          console.warn(`[send-whatsapp] DB save failed: ${dbError.message}`);
        }

        const typeLabel = media_type === "image" ? "Image" : `Document "${file_name || "fichier"}"`;
        return jsonResponse({
          success: true,
          message_id: result.messageId,
          mode: media_type,
          message: `${typeLabel} envoyé par WhatsApp à ${normalizedTo}.`,
        });
      }

      // ── Text / Template mode ──
      // Message is required for text mode, not for template mode
      if (!use_template && !message) {
        return jsonResponse({ error: "Le champ 'message' est requis pour l'envoi en texte libre" }, 400);
      }

      const result = await sendWhatsAppMessage(
        config.access_token,
        config.phone_number_id,
        normalizedTo,
        message || "",
        !!use_template,
        template_name as string || "hello_world",
        template_lang as string || "en_US",
      );

      console.log(`[send-whatsapp] Message envoyé à ${normalizedTo} (mode: ${result.mode}, ID: ${result.messageId})`);

      // Save to database
      const { error: dbError } = await supabase
        .from("whatsapp_messages")
        .insert({
          user_id: userId,
          wa_message_id: result.messageId,
          to_number: normalizedTo,
          message: use_template ? `[Template: ${template_name || "hello_world"}]` : message,
          status: "sent",
        });

      if (dbError) {
        console.warn(`[send-whatsapp] DB save failed: ${dbError.message}`);
      }

      const warning = result.mode === "text"
        ? " Note : le message texte libre ne sera reçu que si le destinataire a envoyé un message à votre numéro Business dans les dernières 24h. Sinon, utilisez un template."
        : "";

      return jsonResponse({
        success: true,
        message_id: result.messageId,
        mode: result.mode,
        message: `Message WhatsApp envoyé à ${normalizedTo} (mode: ${result.mode}).${warning}`,
      });
    }

    return jsonResponse({ error: `Action inconnue: ${action}` }, 400);
  } catch (err) {
    console.error("[send-whatsapp] Error:", err);
    return jsonResponse(
      { error: err instanceof Error ? err.message : "Erreur inconnue" },
      500
    );
  }
});
