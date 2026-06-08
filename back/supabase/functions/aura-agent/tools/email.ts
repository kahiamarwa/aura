import { SUPABASE_URL, SUPABASE_ANON_KEY } from "../types.ts";

// ─── Tool 5: send_email (via send-email edge function) ────
export async function executeSendEmail(params: {
  to: string;
  subject: string;
  body: string;
}, userJwt: string): Promise<string> {
  const url = `${SUPABASE_URL}/functions/v1/send-email`;
  console.log(`[send_email] Appel ${url} pour ${params.to}`);

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
        subject: params.subject,
        body: params.body,
      }),
    });

    const responseText = await response.text();
    console.log(`[send_email] HTTP ${response.status}: ${responseText.substring(0, 200)}`);

    let result;
    try {
      result = JSON.parse(responseText);
    } catch {
      return `Erreur: réponse non-JSON du serveur (HTTP ${response.status}): ${responseText.substring(0, 100)}`;
    }

    if (!response.ok) {
      return `Erreur lors de l'envoi de l'email: ${result.error || responseText.substring(0, 100)}`;
    }

    return result.message || `Email envoyé avec succès à ${params.to}`;
  } catch (err) {
    const errMsg = err instanceof Error ? err.message : String(err);
    console.error(`[send_email] Exception:`, errMsg);
    return `Erreur lors de l'envoi de l'email: ${errMsg}`;
  }
}

// ─── Tool 5b: list_emails (via send-email edge function) ──
export async function executeListEmails(params: {
  max_results?: number;
  query?: string;
  unread_only?: boolean;
}, userJwt: string): Promise<string> {
  const url = `${SUPABASE_URL}/functions/v1/send-email`;
  console.log(`[list_emails] Appel ${url}`);

  try {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "apikey": SUPABASE_ANON_KEY,
        Authorization: userJwt,
      },
      body: JSON.stringify({
        action: "list-emails",
        max_results: params.max_results || 10,
        query: params.query || "",
        unread_only: params.unread_only || false,
      }),
    });

    const responseText = await response.text();
    console.log(`[list_emails] HTTP ${response.status}: ${responseText.substring(0, 200)}`);

    let result;
    try {
      result = JSON.parse(responseText);
    } catch {
      return `Erreur: réponse non-JSON du serveur (HTTP ${response.status}): ${responseText.substring(0, 100)}`;
    }

    if (!response.ok) {
      return `Erreur lors de la récupération des emails: ${result.error || responseText.substring(0, 100)}`;
    }

    if (!result.emails || result.emails.length === 0) {
      return "Aucun email trouvé dans la boîte de réception.";
    }

    return result.emails
      .map((e: { isRead: boolean; from: string; subject: string; date: string; snippet: string; id: string }, i: number) => {
        const status = e.isRead ? "Lu" : "Non lu";
        const date = e.date ? new Date(e.date).toLocaleString("fr-FR", { timeZone: "Europe/Paris", dateStyle: "short", timeStyle: "short" }) : "";
        return `${i + 1}. [${status}] De: ${e.from} | Sujet: ${e.subject} | ${date} | ID: ${e.id}`;
      })
      .join("\n");
  } catch (err) {
    const errMsg = err instanceof Error ? err.message : String(err);
    console.error(`[list_emails] Exception:`, errMsg);
    return `Erreur lors de la récupération des emails: ${errMsg}`;
  }
}

// ─── Tool 5c: read_email (via send-email edge function) ───
export async function executeReadEmail(params: {
  email_id: string;
}, userJwt: string): Promise<string> {
  const url = `${SUPABASE_URL}/functions/v1/send-email`;
  console.log(`[read_email] Appel ${url} pour ${params.email_id}`);

  try {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "apikey": SUPABASE_ANON_KEY,
        Authorization: userJwt,
      },
      body: JSON.stringify({
        action: "read-email",
        email_id: params.email_id,
      }),
    });

    const responseText = await response.text();
    console.log(`[read_email] HTTP ${response.status}: ${responseText.substring(0, 200)}`);

    let result;
    try {
      result = JSON.parse(responseText);
    } catch {
      return `Erreur: réponse non-JSON du serveur (HTTP ${response.status}): ${responseText.substring(0, 100)}`;
    }

    if (!response.ok) {
      return `Erreur lors de la lecture de l'email: ${result.error || responseText.substring(0, 100)}`;
    }

    const e = result.email;
    const date = e.date ? new Date(e.date).toLocaleString("fr-FR", { timeZone: "Europe/Paris", dateStyle: "short", timeStyle: "short" }) : "";
    return `De: ${e.from}\nSujet: ${e.subject}\nDate: ${date}\n\n${e.body}`;
  } catch (err) {
    const errMsg = err instanceof Error ? err.message : String(err);
    console.error(`[read_email] Exception:`, errMsg);
    return `Erreur lors de la lecture de l'email: ${errMsg}`;
  }
}

// ─── Tool: send_email_with_attachment (via send-email edge function) ─
export async function executeSendEmailWithAttachment(
  params: {
    to: string;
    subject: string;
    body: string;
    file_path: string;
    file_name: string;
  },
  userJwt: string
): Promise<string> {
  const url = `${SUPABASE_URL}/functions/v1/send-email`;
  console.log(
    `[send_email_with_attachment] Appel ${url} pour ${params.to} avec pièce jointe ${params.file_name}`
  );

  try {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        apikey: SUPABASE_ANON_KEY,
        Authorization: userJwt,
      },
      body: JSON.stringify({
        action: "send",
        to: params.to,
        subject: params.subject,
        body: params.body,
        attachments: [
          {
            file_path: params.file_path,
            file_name: params.file_name,
            mime_type:
              "application/vnd.openxmlformats-officedocument.presentationml.presentation",
          },
        ],
      }),
    });

    const responseText = await response.text();
    console.log(
      `[send_email_with_attachment] HTTP ${response.status}: ${responseText.substring(0, 200)}`
    );

    let result;
    try {
      result = JSON.parse(responseText);
    } catch {
      return `Erreur: réponse non-JSON du serveur (HTTP ${response.status}): ${responseText.substring(0, 100)}`;
    }

    if (!response.ok) {
      return `Erreur lors de l'envoi de l'email avec pièce jointe: ${result.error || responseText.substring(0, 100)}`;
    }

    return (
      result.message ||
      `Email avec pièce jointe "${params.file_name}" envoyé avec succès à ${params.to}`
    );
  } catch (err) {
    const errMsg = err instanceof Error ? err.message : String(err);
    console.error(`[send_email_with_attachment] Exception:`, errMsg);
    return `Erreur lors de l'envoi de l'email avec pièce jointe: ${errMsg}`;
  }
}
