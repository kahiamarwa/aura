import { ANTHROPIC_API_KEY } from "../types.ts";
import {
  SUMMARIZER_SYSTEM_PROMPT,
  SHORT_SUMMARY_INSTRUCTIONS,
  DETAILED_SUMMARY_INSTRUCTIONS,
  SUMMARY_TOOL_DEFINITION,
} from "../systemPrompt.ts";

// deno-lint-ignore no-explicit-any
type SupabaseClient = any;

// ─── Tool 1: get_recent_context ─────────────────────────────
export async function executeGetRecentContext(
  supabase: SupabaseClient,
  params: { minutes_back?: number },
  userId: string
): Promise<string> {
  const minutesBack = Math.min(Math.max(params.minutes_back || 60, 1), 180);
  const since = new Date(Date.now() - minutesBack * 60 * 1000).toISOString();

  const { data, error } = await supabase
    .from("transcriptions")
    .select("id, transcription_text, summary, created_at")
    .eq("user_id", userId)
    .gte("created_at", since)
    .order("created_at", { ascending: false })
    .limit(10);

  if (error) {
    return `Erreur lors de la récupération du contexte: ${error.message}`;
  }

  if (!data || data.length === 0) {
    return `Aucune transcription trouvée dans les ${minutesBack} dernières minutes.`;
  }

  return data
    .map(
      (row: {
        created_at: string;
        summary: { title?: string } | null;
        transcription_text: string;
      }) => {
        const date = new Date(row.created_at).toLocaleString("fr-FR", {
          timeZone: "Europe/Paris",
        });
        const title = row.summary?.title || "Sans titre";
        const text =
          row.transcription_text.length > 8000
            ? row.transcription_text.substring(0, 8000) + "\n[... tronqué]"
            : row.transcription_text;
        return `--- Transcription du ${date} | "${title}" ---\n${text}`;
      }
    )
    .join("\n\n");
}

// ─── Tool 2: generate_summary (appel Claude inline) ─────────
export async function executeGenerateSummary(params: {
  context: string;
  format?: string;
}): Promise<string> {
  const format = params.format === "short" ? "short" : "detailed";
  const modeInstructions =
    format === "short"
      ? SHORT_SUMMARY_INSTRUCTIONS
      : DETAILED_SUMMARY_INSTRUCTIONS;

  const response = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "x-api-key": ANTHROPIC_API_KEY,
      "anthropic-version": "2023-06-01",
    },
    body: JSON.stringify({
      model: "claude-sonnet-4-6",
      max_tokens: 10000,
      system: SUMMARIZER_SYSTEM_PROMPT,
      messages: [
        {
          role: "user",
          content:
            modeInstructions +
            "\nTranscription:\n" +
            params.context +
            '\n\nUtilise le tool generate_structured_summary pour retourner ton analyse. ' +
            'Respecte STRICTEMENT le format avec les champs "title" et "summary".',
        },
      ],
      tools: [SUMMARY_TOOL_DEFINITION],
      tool_choice: { type: "tool", name: "generate_structured_summary" },
    }),
  });

  if (!response.ok) {
    const errorText = await response.text();
    return `Erreur lors de la génération du résumé: ${errorText}`;
  }

  const result = await response.json();
  const toolUseBlock = result.content?.find(
    (block: { type: string }) => block.type === "tool_use"
  );

  if (!toolUseBlock) {
    return "Erreur: impossible de générer le résumé structuré.";
  }

  const { title, summary } = toolUseBlock.input;
  return `# ${title}\n\n${summary}`;
}

// ─── Tool 3: save_summary (sauvegarde en DB) ────────────────
export async function executeSaveSummary(
  supabase: SupabaseClient,
  params: { transcription_text: string; title: string; summary: string },
  userId: string
): Promise<{ text: string; id: string | null }> {
  const { data, error } = await supabase
    .from("transcriptions")
    .insert({
      transcription_text: params.transcription_text,
      summary: { title: params.title, summary: params.summary },
      user_id: userId,
    })
    .select("id")
    .single();

  if (error) {
    return { text: `Erreur lors de la sauvegarde: ${error.message}`, id: null };
  }

  return {
    text: `Résumé sauvegardé avec succès (ID: ${data.id}). Titre: "${params.title}"`,
    id: data.id,
  };
}

// ─── Tool 4: search_memory ──────────────────────────────────
export async function executeSearchMemory(
  supabase: SupabaseClient,
  params: { query: string; date_start?: string; date_end?: string }
): Promise<string> {
  const keywords = params.query.split(/\s+/).filter((w: string) => w.length > 2);

  if (keywords.length === 0) {
    return "Requête de recherche trop courte. Précise ta recherche avec plus de détails.";
  }

  const searchPattern = `%${keywords[0]}%`;

  let query = supabase
    .from("transcriptions")
    .select("id, transcription_text, summary, created_at")
    .order("created_at", { ascending: false })
    .limit(5);

  if (params.date_start) {
    query = query.gte("created_at", params.date_start);
  }
  if (params.date_end) {
    query = query.lte("created_at", params.date_end);
  }

  query = query.or(
    `transcription_text.ilike.${searchPattern},summary->>title.ilike.${searchPattern}`
  );

  const { data, error } = await query;

  if (error) {
    return `Erreur lors de la recherche: ${error.message}`;
  }

  if (!data || data.length === 0) {
    return `Aucun résultat trouvé pour "${params.query}".`;
  }

  return data
    .map(
      (row: {
        created_at: string;
        summary: { title?: string } | null;
        transcription_text: string;
      }) => {
        const date = new Date(row.created_at).toLocaleString("fr-FR", {
          timeZone: "Europe/Paris",
        });
        const title = row.summary?.title || "Sans titre";
        const snippet = extractSnippet(
          row.transcription_text,
          keywords[0],
          500
        );
        return `--- ${date} | "${title}" ---\n${snippet}`;
      }
    )
    .join("\n\n");
}

// ─── Helper: extractSnippet ─────────────────────────────────
export function extractSnippet(
  text: string,
  keyword: string,
  maxLength: number
): string {
  const lowerText = text.toLowerCase();
  const lowerKeyword = keyword.toLowerCase();
  const idx = lowerText.indexOf(lowerKeyword);

  if (idx === -1) {
    return (
      text.substring(0, maxLength) + (text.length > maxLength ? "..." : "")
    );
  }

  const start = Math.max(0, idx - Math.floor(maxLength / 2));
  const end = Math.min(text.length, start + maxLength);
  const prefix = start > 0 ? "..." : "";
  const suffix = end < text.length ? "..." : "";
  return prefix + text.substring(start, end) + suffix;
}
