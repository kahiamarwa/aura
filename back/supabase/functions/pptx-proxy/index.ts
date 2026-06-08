import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

// ============================================================
// PPTX Proxy — Edge Function
//
// Proxy entre aura-agent et le PPTX Server (REST API).
// 1. Reçoit une description de présentation (titre + slides)
// 2. Appelle POST /generate pour créer le PPTX (base64)
// 3. Appelle POST /convert-to-pdf pour le PDF (base64)
// 4. Upload les deux dans Supabase Storage (bucket "presentations")
// 5. Retourne les chemins des fichiers
// ============================================================

const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const SUPABASE_ANON_KEY = Deno.env.get("SUPABASE_ANON_KEY")!;
const SUPABASE_SERVICE_ROLE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
const PPTX_SERVER_URL = Deno.env.get("PPTX_MCP_SERVER_URL") || Deno.env.get("PPTX_SERVER_URL") || "http://localhost:8200";
const PPTX_API_KEY = Deno.env.get("PPTX_API_KEY") || "aura-pptx-secret-key";
const GOTENBERG_URL = Deno.env.get("GOTENBERG_URL") || "https://u2bp2irgwt.eu-west-3.awsapprunner.com";

// ─── Auth helper (inlined) ──────────────────────────────────
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
  const { data: { user }, error } = await supabase.auth.getUser(token);
  if (error || !user) {
    throw new Error("Token invalide ou expiré. Veuillez vous reconnecter.");
  }
  return { user_id: user.id, email: user.email || "" };
}

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Access-Control-Allow-Headers":
    "Content-Type, Authorization, X-Client-Info, Apikey",
};

function jsonResponse(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { ...corsHeaders, "Content-Type": "application/json" },
  });
}

// ─── Helper: decode base64 to Uint8Array ─────────────────────
function base64ToBytes(b64: string): Uint8Array {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

// ─── Helper: encode Uint8Array to base64 ─────────────────────
function bytesToBase64(bytes: Uint8Array): string {
  let binary = "";
  for (let i = 0; i < bytes.length; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  return btoa(binary);
}

// ─── Generate Report PDF via REST API ─────────────────────────
interface ReportResult {
  base64_data: string;
  file_name: string;
  pages_count: number;
  size_bytes: number;
}

// deno-lint-ignore no-explicit-any
async function generateReport(body: Record<string, any>): Promise<ReportResult> {
  const url = `${PPTX_SERVER_URL}/generate-report`;
  console.log(`[pptx-proxy] POST ${url} — "${body.title}" (${body.sections?.length} sections)`);

  const response = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-API-Key": PPTX_API_KEY,
    },
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    const errText = await response.text();
    throw new Error(`Report server error (${response.status}): ${errText}`);
  }

  return await response.json();
}

// ─── Generate PPTX via REST API ──────────────────────────────
interface GenerateResult {
  base64_data: string;
  file_name: string;
  slides_count: number;
  size_bytes: number;
}

// deno-lint-ignore no-explicit-any
async function generatePptx(body: Record<string, any>): Promise<GenerateResult> {
  const url = `${PPTX_SERVER_URL}/generate`;
  console.log(`[pptx-proxy] POST ${url} — "${body.title}" (${body.slides?.length} slides)`);

  const response = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-API-Key": PPTX_API_KEY,
    },
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    const errText = await response.text();
    throw new Error(`PPTX server error (${response.status}): ${errText}`);
  }

  return await response.json();
}

// ─── Convert PPTX to PDF via REST API ────────────────────────
async function convertToPdf(pptxBase64: string): Promise<{ base64_data: string; size_bytes: number }> {
  const url = `${PPTX_SERVER_URL}/convert-to-pdf`;
  console.log(`[pptx-proxy] Converting to PDF via: ${url}`);

  const response = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-API-Key": PPTX_API_KEY,
    },
    body: JSON.stringify({ base64_data: pptxBase64 }),
  });

  if (!response.ok) {
    const errText = await response.text();
    throw new Error(`PDF conversion failed (${response.status}): ${errText}`);
  }

  return await response.json();
}

// ─── Generate PDF from HTML via Gotenberg (Chromium) ────────
async function generateReportFromHtml(htmlContent: string): Promise<Uint8Array> {
  const url = `${GOTENBERG_URL}/forms/chromium/convert/html`;
  console.log(`[pptx-proxy] POST ${url} (Gotenberg HTML→PDF)`);

  const formData = new FormData();
  const htmlBlob = new Blob([htmlContent], { type: "text/html" });
  formData.append("files", htmlBlob, "index.html");
  // Zero margins — let the HTML control layout via @page CSS
  formData.append("marginTop", "0");
  formData.append("marginBottom", "0");
  formData.append("marginLeft", "0");
  formData.append("marginRight", "0");
  formData.append("preferCssPageSize", "true");

  const response = await fetch(url, {
    method: "POST",
    body: formData,
  });

  if (!response.ok) {
    const errText = await response.text();
    throw new Error(`Gotenberg error (${response.status}): ${errText.substring(0, 200)}`);
  }

  // Gotenberg returns raw PDF bytes
  const pdfBuffer = await response.arrayBuffer();
  return new Uint8Array(pdfBuffer);
}

// ─── Main handler ───────────────────────────────────────────
Deno.serve(async (req) => {
  if (req.method === "OPTIONS") {
    return new Response(null, { headers: corsHeaders });
  }

  try {
    // Auth
    const { user_id: userId } = await getUserFromRequest(req);
    console.log(`[pptx-proxy] User: ${userId}`);

    // Parse request
    const body = await req.json();

    const isReport = Array.isArray(body.sections) && body.sections.length > 0;
    const isPresentation = Array.isArray(body.slides) && body.slides.length > 0;

    if (!body.title || (!isReport && !isPresentation)) {
      return jsonResponse({ error: "title et slides[] ou sections[] requis" }, 400);
    }

    const supabase = createClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY);
    const fileId = crypto.randomUUID();

    // ─── Report path ────────────────────────────────────────────
    if (isReport) {
      let pdfData: Uint8Array;
      let pagesCount = 0;

      // ── Inject logo URL (shared by both paths) ──
      let logoUrl: string | null = null;
      if (body.include_logo !== false) {
        try {
          const { data: settings } = await supabase
            .from("user_settings")
            .select("logo_path")
            .eq("user_id", userId)
            .single();
          if (settings?.logo_path) {
            const { data: signedUrl } = await supabase.storage
              .from("logos")
              .createSignedUrl(settings.logo_path, 300);
            if (signedUrl?.signedUrl) {
              logoUrl = signedUrl.signedUrl;
              console.log(`[pptx-proxy] Logo URL obtained for user ${userId}`);
            }
          }
        } catch (logoErr) {
          console.warn(`[pptx-proxy] Logo lookup failed (non-fatal):`, logoErr);
        }
      }

      if (body.html_content) {
        // ── Gotenberg path: HTML → PDF (Chromium) ──
        console.log(`[pptx-proxy] HTML report via Gotenberg: "${body.title}"`);

        // Replace {{LOGO_URL}} placeholder with actual signed URL
        if (logoUrl) {
          body.html_content = body.html_content.replace(/\{\{LOGO_URL\}\}/g, logoUrl);
        } else {
          // Remove logo img tags if no logo available
          body.html_content = body.html_content.replace(
            /<img[^>]*src=["']?\{\{LOGO_URL\}\}["']?[^>]*\/?>/g,
            ""
          );
        }

        pdfData = await generateReportFromHtml(body.html_content);
        console.log(`[pptx-proxy] Gotenberg PDF generated: ${pdfData.length} bytes`);
      } else {
        // ── Legacy path: JSON sections → Python/fpdf2 ──
        console.log(
          `[pptx-proxy] Legacy report: "${body.title}" (${body.sections.length} sections)`
        );
        if (logoUrl) {
          body.logo_url = logoUrl;
        }

        const reportResult = await generateReport(body);
        pdfData = base64ToBytes(reportResult.base64_data);
        pagesCount = reportResult.pages_count;
        console.log(`[pptx-proxy] fpdf2 PDF: ${pdfData.length} bytes, ${pagesCount} pages`);
      }

      // ── Upload to Storage (shared) ──
      const storagePath = `${userId}/${fileId}.pdf`;
      const displayName = body.title
        .replace(/[^a-zA-Z0-9àâäéèêëïîôùûüçÀÂÄÉÈÊËÏÎÔÙÛÜÇ\s-]/g, "")
        .replace(/\s+/g, "_")
        .substring(0, 50) + ".pdf";

      const { error: uploadError } = await supabase.storage
        .from("presentations")
        .upload(storagePath, pdfData, {
          contentType: "application/pdf",
          upsert: false,
        });

      if (uploadError) {
        throw new Error(`Storage upload failed: ${uploadError.message}`);
      }

      console.log(`[pptx-proxy] Rapport PDF uploadé: ${storagePath}`);

      return jsonResponse({
        success: true,
        file_path: storagePath,
        file_name: displayName,
        pages_count: pagesCount,
        size_bytes: pdfData.length,
      });
    }

    // ─── Presentation path (slides → PPTX + PDF conversion) ──
    console.log(
      `[pptx-proxy] Création présentation: "${body.title}" (${body.slides.length} slides)`
    );

    // 1. Generate PPTX via REST API (returns base64)
    const genResult = await generatePptx(body);
    const pptxData = base64ToBytes(genResult.base64_data);
    console.log(`[pptx-proxy] PPTX généré: ${pptxData.length} bytes, ${genResult.slides_count} slides`);

    // 2. Upload PPTX to Supabase Storage
    const storagePath = `${userId}/${fileId}.pptx`;
    const displayName = genResult.file_name || (body.title
      .replace(/[^a-zA-Z0-9àâäéèêëïîôùûüçÀÂÄÉÈÊËÏÎÔÙÛÜÇ\s-]/g, "")
      .replace(/\s+/g, "_")
      .substring(0, 50) + ".pptx");

    const { error: uploadError } = await supabase.storage
      .from("presentations")
      .upload(storagePath, pptxData, {
        contentType:
          "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        upsert: false,
      });

    if (uploadError) {
      throw new Error(`Storage upload failed: ${uploadError.message}`);
    }

    console.log(`[pptx-proxy] PPTX uploadé: ${storagePath}`);

    // 3. Convert to PDF and upload
    let pdfStoragePath = "";
    let pdfDisplayName = "";
    let pdfSizeBytes = 0;

    try {
      const pdfResult = await convertToPdf(genResult.base64_data);
      const pdfData = base64ToBytes(pdfResult.base64_data);
      pdfStoragePath = `${userId}/${fileId}.pdf`;
      pdfDisplayName = displayName.replace(/\.pptx$/, ".pdf");
      pdfSizeBytes = pdfData.length;

      const { error: pdfUploadError } = await supabase.storage
        .from("presentations")
        .upload(pdfStoragePath, pdfData, {
          contentType: "application/pdf",
          upsert: false,
        });

      if (pdfUploadError) {
        console.warn(`[pptx-proxy] PDF upload failed: ${pdfUploadError.message}`);
        pdfStoragePath = "";
      } else {
        console.log(`[pptx-proxy] PDF uploadé: ${pdfStoragePath}`);
      }
    } catch (pdfErr) {
      console.warn(`[pptx-proxy] PDF conversion failed (non-fatal):`, pdfErr);
    }

    return jsonResponse({
      success: true,
      file_path: storagePath,
      file_name: displayName,
      pdf_file_path: pdfStoragePath || undefined,
      pdf_file_name: pdfDisplayName || undefined,
      pdf_size_bytes: pdfSizeBytes || undefined,
      slides_count: genResult.slides_count,
      size_bytes: pptxData.length,
    });
  } catch (err) {
    const errMsg = err instanceof Error ? err.message : String(err);
    console.error(`[pptx-proxy] Erreur:`, errMsg);
    return jsonResponse({ error: errMsg }, 500);
  }
});
