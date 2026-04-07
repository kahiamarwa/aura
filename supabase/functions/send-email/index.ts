import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { getUserFromRequest } from "../_shared/auth.ts";

// ============================================================
// AURA — Edge Function: send-email
//
// Handles:
//   POST /send-email { action: "oauth-callback", provider, code, redirect_uri }
//   POST /send-email { action: "send", to, subject, body, attachments?[] }
//   POST /send-email { action: "list-emails", max_results?, query?, unread_only? }
//   POST /send-email { action: "read-email", email_id }
//   POST /send-email { action: "list-integrations" }
//   POST /send-email { action: "disconnect", provider }
// ============================================================

const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const SUPABASE_SERVICE_ROLE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;

// OAuth credentials (set via supabase secrets set)
const GOOGLE_CLIENT_ID = Deno.env.get("GOOGLE_CLIENT_ID") || "";
const GOOGLE_CLIENT_SECRET = Deno.env.get("GOOGLE_CLIENT_SECRET") || "";
const MICROSOFT_CLIENT_ID = Deno.env.get("MICROSOFT_CLIENT_ID") || "";
const MICROSOFT_CLIENT_SECRET = Deno.env.get("MICROSOFT_CLIENT_SECRET") || "";

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

// ─── GMAIL: Exchange auth code for tokens ──────────────────
async function gmailExchangeCode(
  code: string,
  redirectUri: string
): Promise<{ access_token: string; refresh_token: string; expires_in: number; email: string }> {
  const tokenRes = await fetch("https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      code,
      client_id: GOOGLE_CLIENT_ID,
      client_secret: GOOGLE_CLIENT_SECRET,
      redirect_uri: redirectUri,
      grant_type: "authorization_code",
    }),
  });

  if (!tokenRes.ok) {
    const err = await tokenRes.text();
    throw new Error(`Google token exchange failed: ${err}`);
  }

  const tokens = await tokenRes.json();

  // Get user email
  const userRes = await fetch("https://www.googleapis.com/oauth2/v2/userinfo", {
    headers: { Authorization: `Bearer ${tokens.access_token}` },
  });
  const userInfo = await userRes.json();

  return {
    access_token: tokens.access_token,
    refresh_token: tokens.refresh_token,
    expires_in: tokens.expires_in,
    email: userInfo.email,
  };
}

// ─── GMAIL: Refresh access token ───────────────────────────
async function gmailRefreshToken(refreshToken: string): Promise<{ access_token: string; expires_in: number }> {
  const res = await fetch("https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      refresh_token: refreshToken,
      client_id: GOOGLE_CLIENT_ID,
      client_secret: GOOGLE_CLIENT_SECRET,
      grant_type: "refresh_token",
    }),
  });

  if (!res.ok) {
    const err = await res.text();
    throw new Error(`Google token refresh failed: ${err}`);
  }

  return await res.json();
}

// ─── OUTLOOK: Exchange auth code for tokens ────────────────
async function outlookExchangeCode(
  code: string,
  redirectUri: string
): Promise<{ access_token: string; refresh_token: string; expires_in: number; email: string }> {
  const tokenRes = await fetch(
    "https://login.microsoftonline.com/common/oauth2/v2.0/token",
    {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        code,
        client_id: MICROSOFT_CLIENT_ID,
        client_secret: MICROSOFT_CLIENT_SECRET,
        redirect_uri: redirectUri,
        grant_type: "authorization_code",
      }),
    }
  );

  if (!tokenRes.ok) {
    const err = await tokenRes.text();
    throw new Error(`Microsoft token exchange failed: ${err}`);
  }

  const tokens = await tokenRes.json();

  // Get user email
  const userRes = await fetch("https://graph.microsoft.com/v1.0/me", {
    headers: { Authorization: `Bearer ${tokens.access_token}` },
  });
  const userInfo = await userRes.json();

  return {
    access_token: tokens.access_token,
    refresh_token: tokens.refresh_token,
    expires_in: tokens.expires_in,
    email: userInfo.mail || userInfo.userPrincipalName,
  };
}

// ─── OUTLOOK: Refresh access token ─────────────────────────
async function outlookRefreshToken(refreshToken: string): Promise<{ access_token: string; expires_in: number }> {
  const res = await fetch(
    "https://login.microsoftonline.com/common/oauth2/v2.0/token",
    {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        refresh_token: refreshToken,
        client_id: MICROSOFT_CLIENT_ID,
        client_secret: MICROSOFT_CLIENT_SECRET,
        grant_type: "refresh_token",
      }),
    }
  );

  if (!res.ok) {
    const err = await res.text();
    throw new Error(`Microsoft token refresh failed: ${err}`);
  }

  return await res.json();
}

// ─── Get valid access token (refresh if expired) ───────────
// deno-lint-ignore no-explicit-any
async function getValidToken(integration: any, supabase: any): Promise<string> {
  const now = new Date();
  const expiry = integration.token_expiry ? new Date(integration.token_expiry) : null;

  // If token is still valid (with 5min buffer), use it
  if (expiry && expiry.getTime() - now.getTime() > 5 * 60 * 1000) {
    return integration.access_token;
  }

  // Refresh token
  if (!integration.refresh_token) {
    throw new Error("Token expiré et pas de refresh token disponible. Reconnectez le compte.");
  }

  console.log(`[send-email] Refreshing ${integration.provider} token...`);

  let refreshed: { access_token: string; expires_in: number };
  if (integration.provider === "gmail") {
    refreshed = await gmailRefreshToken(integration.refresh_token);
  } else {
    refreshed = await outlookRefreshToken(integration.refresh_token);
  }

  // Update in DB
  const newExpiry = new Date(Date.now() + refreshed.expires_in * 1000).toISOString();
  await supabase
    .from("email_integrations")
    .update({
      access_token: refreshed.access_token,
      token_expiry: newExpiry,
      updated_at: new Date().toISOString(),
    })
    .eq("id", integration.id);

  return refreshed.access_token;
}

// ─── RFC 2047 MIME encode for non-ASCII Subject ───────────
function mimeEncodeSubject(subject: string): string {
  // Check if subject contains non-ASCII characters
  if (/^[\x20-\x7E]*$/.test(subject)) {
    return subject; // Pure ASCII, no encoding needed
  }
  // Encode as =?UTF-8?B?<base64>?=
  const encoder = new TextEncoder();
  const bytes = encoder.encode(subject);
  const base64 = btoa(String.fromCharCode(...bytes));
  return `=?UTF-8?B?${base64}?=`;
}

// ─── Attachment type ────────────────────────────────────────
interface EmailAttachment {
  fileName: string;
  mimeType: string;
  base64Data: string;
}

// ─── GMAIL: Send email ─────────────────────────────────────
async function sendGmail(
  accessToken: string,
  to: string,
  subject: string,
  body: string,
  fromEmail: string,
  attachments?: EmailAttachment[]
): Promise<void> {
  const encodedSubject = mimeEncodeSubject(subject);
  let message: string;

  if (attachments && attachments.length > 0) {
    // Build MIME multipart/mixed message with attachments
    const boundary = `boundary_${crypto.randomUUID().replace(/-/g, "")}`;
    const parts: string[] = [
      `From: ${fromEmail}`,
      `To: ${to}`,
      `Subject: ${encodedSubject}`,
      `MIME-Version: 1.0`,
      `Content-Type: multipart/mixed; boundary="${boundary}"`,
      "",
      `--${boundary}`,
      `Content-Type: text/plain; charset=UTF-8`,
      `Content-Transfer-Encoding: 8bit`,
      "",
      body,
    ];

    for (const att of attachments) {
      parts.push(
        `--${boundary}`,
        `Content-Type: ${att.mimeType}`,
        `Content-Disposition: attachment; filename="${att.fileName}"`,
        `Content-Transfer-Encoding: base64`,
        "",
        att.base64Data
      );
    }

    parts.push(`--${boundary}--`);
    message = parts.join("\r\n");
  } else {
    // Simple text/plain message (existing behavior)
    message = [
      `From: ${fromEmail}`,
      `To: ${to}`,
      `Subject: ${encodedSubject}`,
      `Content-Type: text/plain; charset=UTF-8`,
      `Content-Transfer-Encoding: 8bit`,
      "",
      body,
    ].join("\r\n");
  }

  // Base64url encode
  const encoded = btoa(unescape(encodeURIComponent(message)))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");

  const res = await fetch(
    "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ raw: encoded }),
    }
  );

  if (!res.ok) {
    const err = await res.text();
    throw new Error(`Gmail send failed: ${err}`);
  }
}

// ─── OUTLOOK: Send email ───────────────────────────────────
async function sendOutlook(
  accessToken: string,
  to: string,
  subject: string,
  body: string,
  attachments?: EmailAttachment[]
): Promise<void> {
  // deno-lint-ignore no-explicit-any
  const mailPayload: any = {
    message: {
      subject,
      body: { contentType: "Text", content: body },
      toRecipients: [
        { emailAddress: { address: to } },
      ],
    },
  };

  // Add attachments if present (Microsoft Graph file attachment)
  if (attachments && attachments.length > 0) {
    mailPayload.message.attachments = attachments.map((att) => ({
      "@odata.type": "#microsoft.graph.fileAttachment",
      name: att.fileName,
      contentType: att.mimeType,
      contentBytes: att.base64Data,
    }));
  }

  const res = await fetch(
    "https://graph.microsoft.com/v1.0/me/sendMail",
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(mailPayload),
    }
  );

  if (!res.ok) {
    const err = await res.text();
    throw new Error(`Outlook send failed: ${err}`);
  }
}

// ─── Helper: decode Gmail base64url body ─────────────────────
function decodeBase64Url(data: string): string {
  const base64 = data.replace(/-/g, "+").replace(/_/g, "/");
  const decoded = atob(base64);
  // Handle UTF-8
  try {
    return decodeURIComponent(escape(decoded));
  } catch {
    return decoded;
  }
}

// ─── Helper: extract text body from Gmail payload ────────────
// deno-lint-ignore no-explicit-any
function extractGmailBody(payload: any): string {
  // Simple message (no parts)
  if (!payload.parts && payload.body?.data) {
    return decodeBase64Url(payload.body.data);
  }

  // Multipart: search for text/plain first, fallback to text/html
  if (payload.parts) {
    for (const part of payload.parts) {
      if (part.mimeType === "text/plain" && part.body?.data) {
        return decodeBase64Url(part.body.data);
      }
    }
    // Fallback: text/html with tag stripping
    for (const part of payload.parts) {
      if (part.mimeType === "text/html" && part.body?.data) {
        const html = decodeBase64Url(part.body.data);
        return html.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim();
      }
    }
    // Nested multipart (e.g. multipart/alternative inside multipart/mixed)
    for (const part of payload.parts) {
      if (part.parts) {
        const nested = extractGmailBody(part);
        if (nested) return nested;
      }
    }
  }

  return "";
}

// ─── Helper: get Gmail header value ──────────────────────────
// deno-lint-ignore no-explicit-any
function getGmailHeader(headers: any[], name: string): string {
  // deno-lint-ignore no-explicit-any
  const h = headers.find((h: any) => h.name.toLowerCase() === name.toLowerCase());
  return h?.value || "";
}

// ─── Helper: strip HTML tags ─────────────────────────────────
function stripHtml(html: string): string {
  return html.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim();
}

// ─── GMAIL: List emails from inbox ───────────────────────────
async function listGmailEmails(
  accessToken: string,
  maxResults: number,
  query: string
): Promise<{ id: string; subject: string; from: string; date: string; snippet: string; isRead: boolean }[]> {
  const params = new URLSearchParams({
    q: query || "in:inbox",
    maxResults: String(Math.min(maxResults, 20)),
  });

  const listRes = await fetch(
    `https://gmail.googleapis.com/gmail/v1/users/me/messages?${params}`,
    { headers: { Authorization: `Bearer ${accessToken}` } }
  );

  if (!listRes.ok) {
    const err = await listRes.text();
    throw new Error(`Gmail list failed: ${err}`);
  }

  const listData = await listRes.json();
  if (!listData.messages || listData.messages.length === 0) {
    return [];
  }

  // Fetch metadata for each message in parallel
  const emails = await Promise.all(
    // deno-lint-ignore no-explicit-any
    listData.messages.map(async (msg: any) => {
      const metaRes = await fetch(
        `https://gmail.googleapis.com/gmail/v1/users/me/messages/${msg.id}?format=metadata&metadataHeaders=Subject&metadataHeaders=From&metadataHeaders=Date`,
        { headers: { Authorization: `Bearer ${accessToken}` } }
      );
      if (!metaRes.ok) return null;
      const meta = await metaRes.json();

      return {
        id: meta.id,
        subject: getGmailHeader(meta.payload.headers, "Subject") || "(sans sujet)",
        from: getGmailHeader(meta.payload.headers, "From"),
        date: getGmailHeader(meta.payload.headers, "Date"),
        snippet: meta.snippet || "",
        isRead: !meta.labelIds?.includes("UNREAD"),
      };
    })
  );

  // deno-lint-ignore no-explicit-any
  return emails.filter((e: any) => e !== null);
}

// ─── GMAIL: Read a single email ──────────────────────────────
async function readGmailEmail(
  accessToken: string,
  emailId: string
): Promise<{ id: string; subject: string; from: string; date: string; body: string }> {
  const res = await fetch(
    `https://gmail.googleapis.com/gmail/v1/users/me/messages/${emailId}?format=full`,
    { headers: { Authorization: `Bearer ${accessToken}` } }
  );

  if (!res.ok) {
    const err = await res.text();
    throw new Error(`Gmail read failed: ${err}`);
  }

  const msg = await res.json();
  let body = extractGmailBody(msg.payload);

  // Truncate long bodies
  if (body.length > 2000) {
    body = body.substring(0, 2000) + "... (tronqué)";
  }

  return {
    id: msg.id,
    subject: getGmailHeader(msg.payload.headers, "Subject") || "(sans sujet)",
    from: getGmailHeader(msg.payload.headers, "From"),
    date: getGmailHeader(msg.payload.headers, "Date"),
    body,
  };
}

// ─── OUTLOOK: List emails from inbox ─────────────────────────
async function listOutlookEmails(
  accessToken: string,
  maxResults: number,
  query: string,
  unreadOnly: boolean
): Promise<{ id: string; subject: string; from: string; date: string; snippet: string; isRead: boolean }[]> {
  let url = `https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages?$top=${Math.min(maxResults, 20)}&$orderby=receivedDateTime desc&$select=id,subject,from,receivedDateTime,bodyPreview,isRead`;

  if (unreadOnly) {
    url += `&$filter=isRead eq false`;
  }
  if (query) {
    url += `&$search="${encodeURIComponent(query)}"`;
  }

  const res = await fetch(url, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });

  if (!res.ok) {
    const err = await res.text();
    throw new Error(`Outlook list failed: ${err}`);
  }

  const data = await res.json();
  if (!data.value || data.value.length === 0) {
    return [];
  }

  // deno-lint-ignore no-explicit-any
  return data.value.map((msg: any) => ({
    id: msg.id,
    subject: msg.subject || "(sans sujet)",
    from: msg.from?.emailAddress?.address || msg.from?.emailAddress?.name || "Inconnu",
    date: msg.receivedDateTime,
    snippet: msg.bodyPreview || "",
    isRead: msg.isRead,
  }));
}

// ─── OUTLOOK: Read a single email ────────────────────────────
async function readOutlookEmail(
  accessToken: string,
  emailId: string
): Promise<{ id: string; subject: string; from: string; date: string; body: string }> {
  const res = await fetch(
    `https://graph.microsoft.com/v1.0/me/messages/${emailId}?$select=id,subject,from,body,receivedDateTime,isRead`,
    { headers: { Authorization: `Bearer ${accessToken}` } }
  );

  if (!res.ok) {
    const err = await res.text();
    throw new Error(`Outlook read failed: ${err}`);
  }

  const msg = await res.json();
  let body = msg.body?.content || "";

  // Strip HTML if needed
  if (msg.body?.contentType === "html") {
    body = stripHtml(body);
  }

  // Truncate long bodies
  if (body.length > 2000) {
    body = body.substring(0, 2000) + "... (tronqué)";
  }

  return {
    id: msg.id,
    subject: msg.subject || "(sans sujet)",
    from: msg.from?.emailAddress?.address || msg.from?.emailAddress?.name || "Inconnu",
    date: msg.receivedDateTime,
    body,
  };
}

// ═══════════════════════════════════════════════════════════════
// HANDLER PRINCIPAL
// ═══════════════════════════════════════════════════════════════

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") {
    return new Response(null, { status: 200, headers: corsHeaders });
  }

    // Verify user identity
    let userId: string;
    try {
      const authUser = await getUserFromRequest(req);
      userId = authUser.user_id;
    } catch (authErr) {
      return jsonResponse({ error: authErr instanceof Error ? authErr.message : "Non autorisé" }, 401);
    }

  try {
    const { action, ...params } = await req.json();
    const supabase = createClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY);

    // ── ACTION: OAuth callback — exchange code for tokens ──
    if (action === "oauth-callback") {
      const { provider, code, redirect_uri } = params;

      if (!provider || !code || !redirect_uri) {
        return jsonResponse({ error: "provider, code, redirect_uri requis" }, 400);
      }

      let result: { access_token: string; refresh_token: string; expires_in: number; email: string };

      if (provider === "gmail") {
        result = await gmailExchangeCode(code, redirect_uri);
      } else if (provider === "outlook") {
        result = await outlookExchangeCode(code, redirect_uri);
      } else {
        return jsonResponse({ error: "Provider invalide (gmail ou outlook)" }, 400);
      }

      const tokenExpiry = new Date(Date.now() + result.expires_in * 1000).toISOString();

      // Upsert: replace existing integration for this provider
      await supabase
        .from("email_integrations")
        .delete()
        .eq("provider", provider)
        .eq("user_id", userId);

      const { error: insertError } = await supabase
        .from("email_integrations")
        .insert({
          provider,
          email: result.email,
          access_token: result.access_token,
          refresh_token: result.refresh_token,
          token_expiry: tokenExpiry,
          user_id: userId,
        });

      if (insertError) {
        throw new Error(`DB insert failed: ${insertError.message}`);
      }

      console.log(`[send-email] ${provider} connecté: ${result.email}`);
      return jsonResponse({ success: true, email: result.email, provider });
    }

    // ── ACTION: List integrations ──
    if (action === "list-integrations") {
      const { data, error } = await supabase
        .from("email_integrations")
        .select("id, provider, email, created_at")
        .eq("user_id", userId)
        .order("created_at", { ascending: false });

      if (error) throw new Error(error.message);
      return jsonResponse({ integrations: data || [] });
    }

    // ── ACTION: Disconnect ──
    if (action === "disconnect") {
      const { provider } = params;
      if (!provider) return jsonResponse({ error: "provider requis" }, 400);

      await supabase
        .from("email_integrations")
        .delete()
        .eq("provider", provider)
        .eq("user_id", userId);

      console.log(`[send-email] ${provider} déconnecté`);
      return jsonResponse({ success: true });
    }

    // ── ACTION: Send email ──
    if (action === "send") {
      const { to, subject, body: emailBody, provider: preferredProvider, attachments: rawAttachments } = params;

      if (!to || !subject || !emailBody) {
        return jsonResponse({ error: "to, subject, body requis" }, 400);
      }

      // Find an integration (prefer specified provider, fallback to any)
      let query = supabase.from("email_integrations").select("*");
      if (preferredProvider) {
        query = query.eq("provider", preferredProvider);
      }
      query = query.eq("user_id", userId).limit(1).single();

      const { data: integration, error } = await query;

      if (error || !integration) {
        return jsonResponse(
          { error: "Aucun compte email connecté. Connectez Gmail ou Outlook dans les paramètres." },
          400
        );
      }

      // Get valid token (refresh if needed)
      const accessToken = await getValidToken(integration, supabase);

      // Process attachments if present (download from Supabase Storage)
      let emailAttachments: EmailAttachment[] | undefined;
      if (rawAttachments && Array.isArray(rawAttachments) && rawAttachments.length > 0) {
        emailAttachments = [];
        for (const att of rawAttachments) {
          if (!att.file_path || !att.file_name || !att.mime_type) {
            return jsonResponse(
              { error: "Chaque attachment nécessite file_path, file_name, mime_type" },
              400
            );
          }
          console.log(`[send-email] Téléchargement pièce jointe: ${att.file_path}`);
          const { data: fileData, error: dlError } = await supabase.storage
            .from("presentations")
            .download(att.file_path);

          if (dlError || !fileData) {
            return jsonResponse(
              { error: `Impossible de télécharger le fichier: ${dlError?.message || "fichier introuvable"}` },
              400
            );
          }

          const arrayBuffer = await fileData.arrayBuffer();
          const bytes = new Uint8Array(arrayBuffer);
          // Convert to base64 in chunks to avoid stack overflow on large files
          let binaryStr = "";
          const chunkSize = 8192;
          for (let offset = 0; offset < bytes.length; offset += chunkSize) {
            const chunk = bytes.subarray(offset, offset + chunkSize);
            binaryStr += String.fromCharCode(...chunk);
          }
          const base64 = btoa(binaryStr);
          emailAttachments.push({
            fileName: att.file_name,
            mimeType: att.mime_type,
            base64Data: base64,
          });
        }
        console.log(`[send-email] ${emailAttachments.length} pièce(s) jointe(s) prête(s)`);
      }

      // Send via appropriate provider
      if (integration.provider === "gmail") {
        await sendGmail(accessToken, to, subject, emailBody, integration.email, emailAttachments);
      } else {
        await sendOutlook(accessToken, to, subject, emailBody, emailAttachments);
      }

      // Keep attachments in Storage so users can download them from the chat UI
      // (previously deleted here after send, causing 404 on download links)

      const attachmentInfo = emailAttachments
        ? ` avec ${emailAttachments.length} pièce(s) jointe(s)`
        : "";
      console.log(`[send-email] Email envoyé via ${integration.provider} à ${to}${attachmentInfo}`);
      return jsonResponse({
        success: true,
        message: `Email envoyé à ${to} via ${integration.provider} (${integration.email})${attachmentInfo}`,
        provider: integration.provider,
        from: integration.email,
      });
    }

    // ── ACTION: List emails from inbox ──
    if (action === "list-emails") {
      const { max_results = 10, query = "", unread_only = false, provider: preferredProvider } = params;

      // Find an integration
      let query2 = supabase.from("email_integrations").select("*");
      if (preferredProvider) {
        query2 = query2.eq("provider", preferredProvider);
      }
      query2 = query2.eq("user_id", userId).limit(1).single();

      const { data: integration, error } = await query2;

      if (error || !integration) {
        return jsonResponse(
          { error: "Aucun compte email connecté. Connectez Gmail ou Outlook dans les paramètres." },
          400
        );
      }

      const accessToken = await getValidToken(integration, supabase);

      let emails;
      if (integration.provider === "gmail") {
        // Build Gmail query
        let gmailQuery = query || "in:inbox";
        if (unread_only && !gmailQuery.includes("is:unread")) {
          gmailQuery += " is:unread";
        }
        emails = await listGmailEmails(accessToken, max_results, gmailQuery);
      } else {
        emails = await listOutlookEmails(accessToken, max_results, query, unread_only);
      }

      console.log(`[send-email] Listed ${emails.length} emails via ${integration.provider}`);
      return jsonResponse({
        success: true,
        emails,
        provider: integration.provider,
        from: integration.email,
      });
    }

    // ── ACTION: Read a specific email ──
    if (action === "read-email") {
      const { email_id, provider: preferredProvider } = params;

      if (!email_id) {
        return jsonResponse({ error: "email_id requis" }, 400);
      }

      // Find an integration
      let query2 = supabase.from("email_integrations").select("*");
      if (preferredProvider) {
        query2 = query2.eq("provider", preferredProvider);
      }
      query2 = query2.eq("user_id", userId).limit(1).single();

      const { data: integration, error } = await query2;

      if (error || !integration) {
        return jsonResponse(
          { error: "Aucun compte email connecté. Connectez Gmail ou Outlook dans les paramètres." },
          400
        );
      }

      const accessToken = await getValidToken(integration, supabase);

      let email;
      if (integration.provider === "gmail") {
        email = await readGmailEmail(accessToken, email_id);
      } else {
        email = await readOutlookEmail(accessToken, email_id);
      }

      console.log(`[send-email] Read email ${email_id} via ${integration.provider}`);
      return jsonResponse({
        success: true,
        email,
        provider: integration.provider,
      });
    }

    return jsonResponse({ error: `Action inconnue: ${action}` }, 400);
  } catch (err) {
    console.error("[send-email] Error:", err);
    return jsonResponse(
      { error: err instanceof Error ? err.message : "Erreur inconnue" },
      500
    );
  }
});
