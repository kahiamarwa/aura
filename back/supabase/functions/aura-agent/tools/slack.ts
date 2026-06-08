import { SUPABASE_URL, SUPABASE_ANON_KEY } from "../types.ts";

// ── Slack execute functions ──────────────────────────────────

export async function executeSlackSendMessage(
  params: { channel: string; message: string; file_path?: string; file_name?: string },
  userJwt: string
): Promise<string> {
  try {
    const url = `${SUPABASE_URL}/functions/v1/slack-api`;
    // deno-lint-ignore no-explicit-any
    const body: Record<string, any> = {
      action: "send-message",
      channel: params.channel,
      message: params.message,
    };
    if (params.file_path) body.file_path = params.file_path;
    if (params.file_name) body.file_name = params.file_name;

    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "apikey": SUPABASE_ANON_KEY,
        Authorization: userJwt,
      },
      body: JSON.stringify(body),
    });

    const responseText = await response.text();
    let result;
    try { result = JSON.parse(responseText); } catch {
      return `Erreur: réponse non-JSON (HTTP ${response.status}): ${responseText.substring(0, 100)}`;
    }

    if (!response.ok || !result.success) {
      return `Erreur: ${result.error || result.message || responseText.substring(0, 100)}`;
    }

    return result.message || `Message envoyé dans ${params.channel}.`;
  } catch (err) {
    const errMsg = err instanceof Error ? err.message : String(err);
    console.error(`[slack_send_message] Exception:`, errMsg);
    return `Erreur envoi Slack: ${errMsg}`;
  }
}

export async function executeSlackSendDm(
  params: { user_id: string; message: string; file_path?: string; file_name?: string },
  userJwt: string
): Promise<string> {
  try {
    const url = `${SUPABASE_URL}/functions/v1/slack-api`;
    // deno-lint-ignore no-explicit-any
    const body: Record<string, any> = {
      action: "send-dm",
      user_id: params.user_id,
      message: params.message,
    };
    if (params.file_path) body.file_path = params.file_path;
    if (params.file_name) body.file_name = params.file_name;

    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "apikey": SUPABASE_ANON_KEY,
        Authorization: userJwt,
      },
      body: JSON.stringify(body),
    });

    const responseText = await response.text();
    let result;
    try { result = JSON.parse(responseText); } catch {
      return `Erreur: réponse non-JSON (HTTP ${response.status}): ${responseText.substring(0, 100)}`;
    }

    if (!response.ok || !result.success) {
      return `Erreur: ${result.error || result.message || responseText.substring(0, 100)}`;
    }

    return result.message || `Message privé envoyé.`;
  } catch (err) {
    const errMsg = err instanceof Error ? err.message : String(err);
    console.error(`[slack_send_dm] Exception:`, errMsg);
    return `Erreur DM Slack: ${errMsg}`;
  }
}

export async function executeSlackListChannels(
  _params: Record<string, unknown>,
  userJwt: string
): Promise<string> {
  try {
    const url = `${SUPABASE_URL}/functions/v1/slack-api`;
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "apikey": SUPABASE_ANON_KEY,
        Authorization: userJwt,
      },
      body: JSON.stringify({ action: "list-channels" }),
    });

    const responseText = await response.text();
    let result;
    try { result = JSON.parse(responseText); } catch {
      return `Erreur: réponse non-JSON (HTTP ${response.status}): ${responseText.substring(0, 100)}`;
    }

    if (!response.ok) {
      return `Erreur: ${result.error || responseText.substring(0, 100)}`;
    }

    if (!result.channels || result.channels.length === 0) {
      return "Aucun canal Slack trouvé.";
    }

    // deno-lint-ignore no-explicit-any
    return result.channels.map((c: any) =>
      `- #${c.name} (${c.is_private ? 'privé' : 'public'}, ${c.num_members} membres)${c.topic ? ' — ' + c.topic : ''}`
    ).join("\n");
  } catch (err) {
    const errMsg = err instanceof Error ? err.message : String(err);
    console.error(`[slack_list_channels] Exception:`, errMsg);
    return `Erreur liste canaux Slack: ${errMsg}`;
  }
}

export async function executeSlackListUsers(
  _params: Record<string, unknown>,
  userJwt: string
): Promise<string> {
  try {
    const url = `${SUPABASE_URL}/functions/v1/slack-api`;
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "apikey": SUPABASE_ANON_KEY,
        Authorization: userJwt,
      },
      body: JSON.stringify({ action: "list-users" }),
    });

    const responseText = await response.text();
    let result;
    try { result = JSON.parse(responseText); } catch {
      return `Erreur: réponse non-JSON (HTTP ${response.status}): ${responseText.substring(0, 100)}`;
    }

    if (!response.ok) {
      return `Erreur: ${result.error || responseText.substring(0, 100)}`;
    }

    if (!result.users || result.users.length === 0) {
      return "Aucun utilisateur Slack trouvé.";
    }

    // deno-lint-ignore no-explicit-any
    return result.users.map((u: any) =>
      `- [ID:${u.id}] ${u.real_name || u.name}${u.email ? ' (' + u.email + ')' : ''}${u.title ? ' — ' + u.title : ''}`
    ).join("\n");
  } catch (err) {
    const errMsg = err instanceof Error ? err.message : String(err);
    console.error(`[slack_list_users] Exception:`, errMsg);
    return `Erreur liste utilisateurs Slack: ${errMsg}`;
  }
}

export async function executeSlackGetChannelHistory(
  params: { channel: string; limit?: number },
  userJwt: string
): Promise<string> {
  try {
    const url = `${SUPABASE_URL}/functions/v1/slack-api`;
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "apikey": SUPABASE_ANON_KEY,
        Authorization: userJwt,
      },
      body: JSON.stringify({
        action: "get-channel-history",
        channel: params.channel,
        limit: params.limit || 20,
      }),
    });

    const responseText = await response.text();
    let result;
    try { result = JSON.parse(responseText); } catch {
      return `Erreur: réponse non-JSON (HTTP ${response.status}): ${responseText.substring(0, 100)}`;
    }

    if (!response.ok) {
      return `Erreur: ${result.error || responseText.substring(0, 100)}`;
    }

    if (!result.messages || result.messages.length === 0) {
      return "Aucun message récent dans ce canal.";
    }

    // deno-lint-ignore no-explicit-any
    return result.messages.map((m: any) => {
      const time = m.ts ? new Date(parseFloat(m.ts) * 1000).toLocaleString("fr-FR", { timeZone: "Europe/Paris", timeStyle: "short", dateStyle: "short" }) : "";
      return `[${time}] ${m.user}: ${m.text}`;
    }).join("\n");
  } catch (err) {
    const errMsg = err instanceof Error ? err.message : String(err);
    console.error(`[slack_get_channel_history] Exception:`, errMsg);
    return `Erreur historique Slack: ${errMsg}`;
  }
}
