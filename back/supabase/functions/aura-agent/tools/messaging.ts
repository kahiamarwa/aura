import { SUPABASE_URL, SUPABASE_ANON_KEY } from "../types.ts";

// ─── Tool 12: send_sms (via send-sms edge function) ──
export async function executeSendSMS(params: {
  to: string;
  message: string;
}, userJwt: string): Promise<string> {
  const url = `${SUPABASE_URL}/functions/v1/send-sms`;
  console.log(`[send_sms] Appel ${url} pour ${params.to}`);

  try {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "apikey": SUPABASE_ANON_KEY,
        Authorization: userJwt,
      },
      body: JSON.stringify({
        action: "send",
        to: params.to,
        message: params.message,
      }),
    });

    const responseText = await response.text();
    console.log(`[send_sms] HTTP ${response.status}: ${responseText.substring(0, 200)}`);

    let result;
    try {
      result = JSON.parse(responseText);
    } catch {
      return `Erreur: réponse non-JSON (HTTP ${response.status}): ${responseText.substring(0, 100)}`;
    }

    if (!response.ok) {
      return `Erreur envoi SMS: ${result.error || responseText.substring(0, 100)}`;
    }

    return result.message || `SMS envoyé à ${params.to}.`;
  } catch (err) {
    const errMsg = err instanceof Error ? err.message : String(err);
    console.error(`[send_sms] Exception:`, errMsg);
    return `Erreur envoi SMS: ${errMsg}`;
  }
}

// ─── Tool 13: send_whatsapp (via send-whatsapp edge function) ──
export async function executeSendWhatsApp(params: {
  to: string;
  message?: string;
  use_template?: boolean;
  media_type?: "image" | "document";
  file_path?: string;
  media_url?: string;
  file_name?: string;
  caption?: string;
}, userJwt: string): Promise<string> {
  const url = `${SUPABASE_URL}/functions/v1/send-whatsapp`;
  const mode = params.media_type || (params.use_template ? "template" : "text");
  console.log(`[send_whatsapp] Appel ${url} pour ${params.to} (mode: ${mode})`);

  try {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "apikey": SUPABASE_ANON_KEY,
        Authorization: userJwt,
      },
      body: JSON.stringify({
        action: "send",
        to: params.to,
        message: params.message || "",
        use_template: params.use_template || false,
        media_type: params.media_type,
        file_path: params.file_path,
        media_url: params.media_url,
        file_name: params.file_name,
        caption: params.caption,
      }),
    });

    const responseText = await response.text();
    console.log(`[send_whatsapp] HTTP ${response.status}: ${responseText.substring(0, 200)}`);

    let result;
    try {
      result = JSON.parse(responseText);
    } catch {
      return `Erreur: réponse non-JSON (HTTP ${response.status}): ${responseText.substring(0, 100)}`;
    }

    if (!response.ok) {
      return `Erreur envoi WhatsApp: ${result.error || responseText.substring(0, 100)}`;
    }

    return result.message || `Message WhatsApp envoyé à ${params.to}.`;
  } catch (err) {
    const errMsg = err instanceof Error ? err.message : String(err);
    console.error(`[send_whatsapp] Exception:`, errMsg);
    return `Erreur envoi WhatsApp: ${errMsg}`;
  }
}
