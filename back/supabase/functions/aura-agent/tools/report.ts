import { SUPABASE_URL, SUPABASE_ANON_KEY } from "../types.ts";

// ─── Tool: create_report (via pptx-proxy edge function) ─────
export async function executeCreateReport(
  params: {
    title: string;
    subtitle?: string;
    theme?: string;
    template?: string;
    document_type?: string;
    custom_color?: string;
    metadata?: Array<{ key: string; value: string }>;
    footer_text?: string;
    include_logo?: boolean;
    reference?: string;
    html_content?: string;
    sections?: Array<{
      type: string;
      level?: number;
      text?: string;
      items?: string[];
      headers?: string[];
      rows?: string[][];
      metrics?: Array<{ label: string; value: string }>;
      author?: string;
      box_type?: string;
      box_title?: string;
      metadata?: Array<{ key: string; value: string }>;
    }>;
  },
  userJwt: string
): Promise<string> {
  const url = `${SUPABASE_URL}/functions/v1/pptx-proxy`;
  const sectionCount = params.sections?.length || 0;
  const mode = params.html_content ? "HTML/Gotenberg" : `${sectionCount} sections`;
  console.log(`[create_report] Appel ${url} — "${params.title}" (${mode})`);

  try {
    // deno-lint-ignore no-explicit-any
    const body: Record<string, any> = {
      title: params.title,
      subtitle: params.subtitle,
      include_logo: params.include_logo,
      reference: params.reference,
    };

    if (params.html_content) {
      // Gotenberg path: HTML → PDF
      body.html_content = params.html_content;
      // sections still needed as marker for pptx-proxy isReport detection
      body.sections = params.sections || [{ type: "page_break" }];
    } else {
      // Legacy path: JSON sections → Python/fpdf2
      body.theme = params.theme || "professional";
      body.template = params.template || "executive";
      body.document_type = params.document_type || "custom";
      body.custom_color = params.custom_color;
      body.metadata = params.metadata;
      body.footer_text = params.footer_text;
      body.sections = params.sections || [];
    }

    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        apikey: SUPABASE_ANON_KEY,
        Authorization: userJwt,
      },
      body: JSON.stringify(body),
    });

    const responseText = await response.text();
    console.log(
      `[create_report] HTTP ${response.status}: ${responseText.substring(0, 200)}`
    );

    let result;
    try {
      result = JSON.parse(responseText);
    } catch {
      return `Erreur: réponse non-JSON du serveur (HTTP ${response.status}): ${responseText.substring(0, 100)}`;
    }

    if (!response.ok) {
      return `Erreur lors de la création du rapport: ${result.error || responseText.substring(0, 100)}`;
    }

    return (
      `Rapport créé avec succès!\n` +
      `Titre: ${params.title}\n` +
      `Pages: ${result.pages_count}\n` +
      `PDF: ${result.file_name} (${Math.round((result.size_bytes || 0) / 1024)} Ko)\n` +
      `Chemin PDF (file_path): ${result.file_path}` +
      `\nPour envoyer par email, utilise send_email_with_attachment avec file_path="${result.file_path}" et file_name="${result.file_name}".`
    );
  } catch (err) {
    const errMsg = err instanceof Error ? err.message : String(err);
    console.error(`[create_report] Exception:`, errMsg);
    return `Erreur lors de la création du rapport: ${errMsg}`;
  }
}
