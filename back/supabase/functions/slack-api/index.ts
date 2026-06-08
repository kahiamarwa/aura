import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { getUserFromRequest } from "../_shared/auth.ts";

// ============================================================
// AURA — Edge Function: slack-api
//
// Slack integration — per-user OAuth 2.0 user tokens
// Actions:
//   oauth-callback      : Exchange OAuth code for bot token
//   get-connection       : Check if user has Slack connected
//   disconnect           : Remove user's Slack integration
//   send-message         : Send message to a channel
//   send-dm              : Send a direct message to a user
//   list-channels        : List accessible channels
//   list-users           : List workspace users
//   get-channel-history  : Get recent messages from a channel
// ============================================================

const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const SUPABASE_SERVICE_ROLE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
const SLACK_CLIENT_ID = Deno.env.get("SLACK_CLIENT_ID") || "";
const SLACK_CLIENT_SECRET = Deno.env.get("SLACK_CLIENT_SECRET") || "";
const SLACK_BASE_URL = "https://slack.com/api";

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

// ─── Generic Slack API helper ────────────────────────────────
// deno-lint-ignore no-explicit-any
async function slackFetch(
  token: string,
  method: string,
  body?: Record<string, unknown>
): Promise<{ ok: boolean; data: any }> {
  const res = await fetch(`${SLACK_BASE_URL}/${method}`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json; charset=utf-8",
    },
    body: body ? JSON.stringify(body) : undefined,
  });

  const data = await res.json();
  return { ok: data.ok === true, data };
}

// ─── Exchange OAuth code for user token ───────────────────────
async function slackExchangeCode(
  code: string,
  redirectUri: string
): Promise<{
  access_token: string;
  team_id: string;
  team_name: string;
  authed_user_id: string;
}> {
  const res = await fetch(`${SLACK_BASE_URL}/oauth.v2.access`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      client_id: SLACK_CLIENT_ID,
      client_secret: SLACK_CLIENT_SECRET,
      code,
      redirect_uri: redirectUri,
    }),
  });

  const data = await res.json();
  console.log("[slack-api] OAuth response:", JSON.stringify(data));

  if (!data.ok) {
    throw new Error(`Slack OAuth failed: ${data.error}`);
  }

  // With user_scope, the user token is in authed_user.access_token
  const userToken = data.authed_user?.access_token;
  if (!userToken) {
    throw new Error("No user access token received. Make sure user_scope is used.");
  }

  return {
    access_token: userToken,
    team_id: data.team?.id || "",
    team_name: data.team?.name || "",
    authed_user_id: data.authed_user?.id || "",
  };
}

// ─── Get user's Slack token from DB ──────────────────────────
// deno-lint-ignore no-explicit-any
async function getUserSlackToken(userId: string, supabase: any): Promise<string> {
  const { data: integration, error } = await supabase
    .from("slack_integrations")
    .select("*")
    .eq("user_id", userId)
    .single();

  if (error || !integration) {
    throw new Error("SLACK_NOT_CONNECTED");
  }

  // User tokens (xoxp-) don't expire when rotation is off — return directly
  return integration.access_token;
}

// ─── Translate Slack errors to French ────────────────────────
function slackError(error: string): string {
  const errors: Record<string, string> = {
    not_authed: "Token Slack invalide. Reconnectez votre compte Slack.",
    token_revoked: "Token Slack révoqué. Reconnectez votre compte Slack.",
    channel_not_found: "Canal Slack introuvable.",
    not_in_channel: "Vous n'êtes pas membre de ce canal. Rejoignez-le d'abord.",
    is_archived: "Ce canal est archivé.",
    user_not_found: "Utilisateur Slack introuvable.",
    no_text: "Le message ne peut pas être vide.",
  };
  return errors[error] || `Erreur Slack: ${error}`;
}

// ═══════════════════════════════════════════════════════════════
// HANDLER PRINCIPAL
// ═══════════════════════════════════════════════════════════════

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") {
    return new Response(null, { status: 200, headers: corsHeaders });
  }

  try {
    let userId = "00000000-0000-0000-0000-000000000000";
    try {
      const authUser = await getUserFromRequest(req);
      userId = authUser.user_id;
    } catch (_authErr) {
      console.log("[slack-api] No valid JWT, using default user");
    }

    const { action, ...params } = await req.json();
    const supabase = createClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY);

    // ── ACTION: oauth-callback — exchange OAuth code for bot token ──
    if (action === "oauth-callback") {
      const { code, redirect_uri } = params;

      if (!code || !redirect_uri) {
        return jsonResponse({ error: "code et redirect_uri requis" }, 400);
      }

      const result = await slackExchangeCode(code, redirect_uri);

      // Upsert: delete existing then insert
      await supabase
        .from("slack_integrations")
        .delete()
        .eq("user_id", userId);

      const { error: insertError } = await supabase
        .from("slack_integrations")
        .insert({
          user_id: userId,
          access_token: result.access_token,
          team_id: result.team_id,
          team_name: result.team_name,
          authed_user_id: result.authed_user_id,
        });

      if (insertError) {
        throw new Error(`DB insert failed: ${insertError.message}`);
      }

      console.log(`[slack-api] Slack connecté pour user ${userId} (team: ${result.team_name})`);
      return jsonResponse({
        success: true,
        team_name: result.team_name,
        team_id: result.team_id,
      });
    }

    // ── ACTION: get-connection ──
    if (action === "get-connection") {
      const { data: integration } = await supabase
        .from("slack_integrations")
        .select("team_id, team_name, created_at")
        .eq("user_id", userId)
        .single();

      return jsonResponse({ connection: integration || null });
    }

    // ── ACTION: disconnect ──
    if (action === "disconnect") {
      await supabase
        .from("slack_integrations")
        .delete()
        .eq("user_id", userId);

      console.log(`[slack-api] Slack déconnecté pour user ${userId}`);
      return jsonResponse({ success: true });
    }

    // ── For all Slack actions, get the user's token ──
    let slackToken: string;
    try {
      slackToken = await getUserSlackToken(userId, supabase);
    } catch (tokenErr) {
      const errMsg = tokenErr instanceof Error ? tokenErr.message : "Erreur token";
      if (errMsg === "SLACK_NOT_CONNECTED") {
        return jsonResponse(
          { error: "SLACK_NOT_CONNECTED", message: "Aucun compte Slack connecté. Connectez Slack dans les paramètres." },
          400
        );
      }
      return jsonResponse({ error: errMsg }, 400);
    }

    // ── ACTION: send-message — send to a channel (with optional file) ──
    if (action === "send-message") {
      const { channel, message, file_path, file_name } = params;

      if (!channel || !message) {
        return jsonResponse({ error: "channel et message sont requis" }, 400);
      }

      // ── File attachment mode ──
      if (file_path && file_name) {
        console.log(`[slack-api] Upload fichier ${file_name} dans ${channel}`);

        // 1. Generate signed URL from Supabase Storage
        const supabase = createClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY);
        const { data: signedData, error: signedError } = await supabase
          .storage
          .from("presentations")
          .createSignedUrl(file_path, 3600);

        if (signedError || !signedData?.signedUrl) {
          return jsonResponse(
            { error: `Impossible de générer l'URL pour le fichier: ${signedError?.message || "fichier introuvable"}` },
            400
          );
        }

        // 2. Download the file
        const fileResponse = await fetch(signedData.signedUrl);
        if (!fileResponse.ok) {
          return jsonResponse(
            { error: `Impossible de télécharger le fichier: HTTP ${fileResponse.status}` },
            400
          );
        }
        const fileBlob = await fileResponse.blob();
        console.log(`[slack-api] Fichier téléchargé: ${fileBlob.size} bytes`);

        // 3. Resolve channel name to ID (required for file upload)
        let channelId = channel;
        const channelName = channel.replace(/^#/, "");

        // If it's a user ID (starts with U), open a DM to get the channel ID
        if (channel.match(/^U[A-Za-z0-9]+$/)) {
          console.log(`[slack-api] User ID détecté: ${channel}, ouverture DM...`);
          const { ok: openOk, data: openData } = await slackFetch(slackToken, "conversations.open", {
            users: channel,
          });
          if (openOk && openData.channel?.id) {
            channelId = openData.channel.id;
            console.log(`[slack-api] DM ouvert: ${channel} → ${channelId}`);
          } else {
            console.error(`[slack-api] Impossible d'ouvrir un DM avec ${channel}`);
          }
        }
        // If it doesn't look like a Slack ID, resolve channel name to ID
        else if (!channel.match(/^[CGD][A-Za-z0-9]+$/)) {
          const { ok: listOk, data: listData } = await slackFetch(slackToken, "conversations.list", {
            types: "public_channel,private_channel",
            exclude_archived: true,
            limit: 200,
          });
          if (listOk && listData.channels) {
            // deno-lint-ignore no-explicit-any
            const found = listData.channels.find((c: any) => c.name === channelName);
            if (found) {
              channelId = found.id;
              console.log(`[slack-api] Canal résolu: ${channelName} → ${channelId}`);
            } else {
              console.warn(`[slack-api] Canal "${channelName}" introuvable, utilisation tel quel: ${channel}`);
            }
          }
        }

        // 4. Upload file via 3-step process

        // Step 4a: Get pre-signed upload URL (this endpoint requires form-urlencoded)
        const uploadParams = new URLSearchParams({
          filename: file_name,
          length: String(fileBlob.size),
        });
        const urlRes = await fetch(`${SLACK_BASE_URL}/files.getUploadURLExternal`, {
          method: "POST",
          headers: {
            Authorization: `Bearer ${slackToken}`,
            "Content-Type": "application/x-www-form-urlencoded",
          },
          body: uploadParams.toString(),
        });
        const urlData = await urlRes.json();
        const urlOk = urlData.ok === true;

        if (!urlOk || !urlData.upload_url || !urlData.file_id) {
          console.error(`[slack-api] getUploadURLExternal error:`, JSON.stringify(urlData));
          return jsonResponse({ error: slackError(urlData.error || "Impossible d'obtenir l'URL d'upload Slack") }, 400);
        }

        console.log(`[slack-api] Upload URL obtenue, file_id: ${urlData.file_id}`);

        // Step 4b: Upload file binary to the pre-signed URL
        const uploadRes = await fetch(urlData.upload_url, {
          method: "POST",
          body: fileBlob,
        });

        if (!uploadRes.ok) {
          const uploadErr = await uploadRes.text().catch(() => "");
          console.error(`[slack-api] Upload to pre-signed URL failed: HTTP ${uploadRes.status}`, uploadErr);
          return jsonResponse({ error: `Erreur upload fichier: HTTP ${uploadRes.status}` }, 400);
        }

        console.log(`[slack-api] Fichier uploadé, finalisation avec channel_id=${channelId}...`);

        // Step 4c: Complete upload and share to channel
        const completeBody = JSON.stringify({
          files: [{ id: urlData.file_id, title: file_name }],
          channel_id: channelId,
          initial_comment: message,
        });
        console.log(`[slack-api] completeUploadExternal body:`, completeBody);
        const completeRes = await fetch(`${SLACK_BASE_URL}/files.completeUploadExternal`, {
          method: "POST",
          headers: {
            Authorization: `Bearer ${slackToken}`,
            "Content-Type": "application/json; charset=utf-8",
          },
          body: completeBody,
        });
        const completeData = await completeRes.json();
        const completeOk = completeData.ok === true;

        if (!completeOk) {
          console.error(`[slack-api] completeUploadExternal error:`, JSON.stringify(completeData));
          return jsonResponse({ error: slackError(completeData.error) }, 400);
        }

        console.log(`[slack-api] Fichier ${file_name} uploadé et partagé dans ${channel}`);
        return jsonResponse({
          success: true,
          channel: channelId,
          message: `Fichier "${file_name}" envoyé dans le canal ${channel}.`,
        });
      }

      // ── Text-only mode (no file) ──
      const { ok, data } = await slackFetch(slackToken, "chat.postMessage", {
        channel,
        text: message,
      });

      if (!ok) {
        return jsonResponse({ error: slackError(data.error) }, 400);
      }

      console.log(`[slack-api] Message envoyé dans ${channel}`);
      return jsonResponse({
        success: true,
        ts: data.ts,
        channel: data.channel,
        message: `Message envoyé dans le canal ${channel}.`,
      });
    }

    // ── ACTION: send-dm — open DM and send message (with optional file) ──
    if (action === "send-dm") {
      const { user_id: slackUserId, message, file_path, file_name } = params;

      if (!slackUserId || !message) {
        return jsonResponse({ error: "user_id et message sont requis" }, 400);
      }

      // Open DM conversation
      const { ok: openOk, data: openData } = await slackFetch(slackToken, "conversations.open", {
        users: slackUserId,
      });

      if (!openOk) {
        return jsonResponse({ error: slackError(openData.error) }, 400);
      }

      const dmChannelId = openData.channel?.id;

      // ── File attachment mode ──
      if (file_path && file_name) {
        console.log(`[slack-api] Upload fichier DM ${file_name} à ${slackUserId}`);

        const supabaseForDm = createClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY);
        const { data: signedData, error: signedError } = await supabaseForDm
          .storage
          .from("presentations")
          .createSignedUrl(file_path, 3600);

        if (signedError || !signedData?.signedUrl) {
          return jsonResponse(
            { error: `Impossible de générer l'URL pour le fichier: ${signedError?.message || "fichier introuvable"}` },
            400
          );
        }

        const fileResponse = await fetch(signedData.signedUrl);
        if (!fileResponse.ok) {
          return jsonResponse({ error: `Impossible de télécharger le fichier: HTTP ${fileResponse.status}` }, 400);
        }
        const fileBlob = await fileResponse.blob();

        // Step 1: Get pre-signed upload URL
        const dmUploadParams = new URLSearchParams({
          filename: file_name,
          length: String(fileBlob.size),
        });
        const dmUrlRes = await fetch(`${SLACK_BASE_URL}/files.getUploadURLExternal`, {
          method: "POST",
          headers: {
            Authorization: `Bearer ${slackToken}`,
            "Content-Type": "application/x-www-form-urlencoded",
          },
          body: dmUploadParams.toString(),
        });
        const dmUrlData = await dmUrlRes.json();

        if (!dmUrlData.ok || !dmUrlData.upload_url || !dmUrlData.file_id) {
          console.error(`[slack-api] DM getUploadURLExternal error:`, JSON.stringify(dmUrlData));
          return jsonResponse({ error: slackError(dmUrlData.error || "Impossible d'obtenir l'URL d'upload") }, 400);
        }

        // Step 2: Upload file binary
        const dmUploadRes = await fetch(dmUrlData.upload_url, { method: "POST", body: fileBlob });
        if (!dmUploadRes.ok) {
          return jsonResponse({ error: `Erreur upload fichier DM: HTTP ${dmUploadRes.status}` }, 400);
        }

        // Step 3: Complete upload and share in DM
        const dmCompleteRes = await fetch(`${SLACK_BASE_URL}/files.completeUploadExternal`, {
          method: "POST",
          headers: {
            Authorization: `Bearer ${slackToken}`,
            "Content-Type": "application/json; charset=utf-8",
          },
          body: JSON.stringify({
            files: [{ id: dmUrlData.file_id, title: file_name }],
            channel_id: dmChannelId,
            initial_comment: message,
          }),
        });
        const dmCompleteData = await dmCompleteRes.json();
        const dmCompleteOk = dmCompleteData.ok === true;

        if (!dmCompleteOk) {
          console.error(`[slack-api] DM completeUploadExternal error:`, JSON.stringify(dmCompleteData));
          return jsonResponse({ error: slackError(dmCompleteData.error) }, 400);
        }

        console.log(`[slack-api] Fichier ${file_name} envoyé en DM à ${slackUserId}`);
        return jsonResponse({
          success: true,
          channel: dmChannelId,
          message: `Fichier "${file_name}" envoyé en message privé.`,
        });
      }

      // ── Text-only DM ──
      const { ok, data } = await slackFetch(slackToken, "chat.postMessage", {
        channel: dmChannelId,
        text: message,
      });

      if (!ok) {
        return jsonResponse({ error: slackError(data.error) }, 400);
      }

      console.log(`[slack-api] DM envoyé à ${slackUserId}`);
      return jsonResponse({
        success: true,
        ts: data.ts,
        channel: dmChannelId,
        message: `Message privé envoyé.`,
      });
    }

    // ── ACTION: list-channels ──
    if (action === "list-channels") {
      const { ok, data } = await slackFetch(slackToken, "conversations.list", {
        types: "public_channel,private_channel",
        exclude_archived: true,
        limit: params.limit || 100,
      });

      if (!ok) {
        return jsonResponse({ error: slackError(data.error) }, 400);
      }

      // deno-lint-ignore no-explicit-any
      const channels = (data.channels || []).map((c: any) => ({
        id: c.id,
        name: c.name,
        is_private: c.is_private,
        num_members: c.num_members,
        topic: c.topic?.value || "",
        purpose: c.purpose?.value || "",
      }));

      console.log(`[slack-api] list-channels → ${channels.length} canal(aux)`);
      return jsonResponse({ channels });
    }

    // ── ACTION: list-users ──
    if (action === "list-users") {
      const { ok, data } = await slackFetch(slackToken, "users.list", {});

      if (!ok) {
        return jsonResponse({ error: slackError(data.error) }, 400);
      }

      // deno-lint-ignore no-explicit-any
      const users = (data.members || [])
        // deno-lint-ignore no-explicit-any
        .filter((u: any) => !u.deleted && !u.is_bot && u.id !== "USLACKBOT")
        // deno-lint-ignore no-explicit-any
        .map((u: any) => ({
          id: u.id,
          name: u.name,
          real_name: u.real_name || u.profile?.real_name || "",
          display_name: u.profile?.display_name || "",
          email: u.profile?.email || "",
          title: u.profile?.title || "",
          is_admin: u.is_admin || false,
        }));

      console.log(`[slack-api] list-users → ${users.length} utilisateur(s)`);
      return jsonResponse({ users });
    }

    // ── ACTION: get-channel-history ──
    if (action === "get-channel-history") {
      const { channel, limit } = params;

      if (!channel) {
        return jsonResponse({ error: "channel est requis" }, 400);
      }

      const { ok, data } = await slackFetch(slackToken, "conversations.history", {
        channel,
        limit: limit || 20,
      });

      if (!ok) {
        return jsonResponse({ error: slackError(data.error) }, 400);
      }

      // deno-lint-ignore no-explicit-any
      const messages = (data.messages || []).map((m: any) => ({
        ts: m.ts,
        user: m.user || "",
        text: m.text || "",
        type: m.type || "message",
        subtype: m.subtype || "",
      }));

      console.log(`[slack-api] get-channel-history ${channel} → ${messages.length} message(s)`);
      return jsonResponse({ messages });
    }

    return jsonResponse({ error: `Action inconnue: ${action}` }, 400);
  } catch (err) {
    console.error("[slack-api] Error:", err);
    return jsonResponse(
      { error: err instanceof Error ? err.message : "Erreur inconnue" },
      500
    );
  }
});
