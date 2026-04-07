import { SUPABASE_URL, SUPABASE_ANON_KEY } from "../types.ts";

// ─── Tool: create_presentation (via pptx-proxy edge function) ─
export async function executeCreatePresentation(
  // deno-lint-ignore no-explicit-any
  params: {
    title: string;
    slides: Array<{
      title: string;
      layout?: string;
      content?: string;
      bullets?: string[];
      image_url?: string;
      image_caption?: string;
      columns?: Array<{ title: string; bullets: string[] }>;
      key_metrics?: Array<{ value: string; label: string }>;
      quote?: string;
      quote_author?: string;
      table_data?: { headers: string[]; rows: string[][] };
    }>;
    theme?: string;
  },
  userJwt: string
): Promise<string> {
  const url = `${SUPABASE_URL}/functions/v1/pptx-proxy`;
  console.log(
    `[create_presentation] Appel ${url} — "${params.title}" (${params.slides.length} slides)`
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
        title: params.title,
        slides: params.slides,
        theme: params.theme || "professional",
      }),
    });

    const responseText = await response.text();
    console.log(
      `[create_presentation] HTTP ${response.status}: ${responseText.substring(0, 200)}`
    );

    let result;
    try {
      result = JSON.parse(responseText);
    } catch {
      return `Erreur: réponse non-JSON du serveur (HTTP ${response.status}): ${responseText.substring(0, 100)}`;
    }

    if (!response.ok) {
      return `Erreur lors de la création de la présentation: ${result.error || responseText.substring(0, 100)}`;
    }

    const pdfInfo = result.pdf_file_path
      ? `\nPDF: ${result.pdf_file_name} (${Math.round((result.pdf_size_bytes || 0) / 1024)} Ko)` +
        `\nChemin PDF (pdf_file_path): ${result.pdf_file_path}`
      : "";

    return (
      `Présentation créée avec succès!\n` +
      `Titre: ${params.title}\n` +
      `Slides: ${result.slides_count}\n` +
      `PPTX: ${result.file_name} (${Math.round((result.size_bytes || 0) / 1024)} Ko)\n` +
      `Chemin PPTX (file_path): ${result.file_path}` +
      pdfInfo +
      `\nPour envoyer par email, utilise send_email_with_attachment avec file_path="${result.file_path}" et file_name="${result.file_name}".` +
      (result.pdf_file_path ? ` Pour le PDF: file_path="${result.pdf_file_path}" et file_name="${result.pdf_file_name}".` : "")
    );
  } catch (err) {
    const errMsg = err instanceof Error ? err.message : String(err);
    console.error(`[create_presentation] Exception:`, errMsg);
    return `Erreur lors de la création de la présentation: ${errMsg}`;
  }
}
