import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

// ============================================================
// AURA — Edge Function: transcribe-and-summarize
//
// Pattern: Claude Sonnet + Tool Use (structured output)
//
// Flow:
//   1. Receive transcription text + summaryMode (short/detailed)
//   2. Call Claude Sonnet with Tool Use → { title, summary (markdown) }
//   3. Store in Supabase DB
//   4. Return result
// ============================================================

const ANTHROPIC_API_KEY = Deno.env.get("ANTHROPIC_API_KEY")!;
const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const SUPABASE_SERVICE_ROLE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;

// ====== SYSTEM_PROMPT ======
const SYSTEM_PROMPT = `Tu es un assistant expert en synthèse de réunions. Tu transformes des transcriptions en comptes rendus structurés, clairs et actionnables.
SORTIE REQUISE - Format JSON :
{
  "title": "Titre concis de la réunion (5-8 mots)",
  "summary": "Contenu markdown complet du résumé"
}
MÉTHODOLOGIE :
1. Lis l'intégralité de la transcription
2. Identifie 3-5 thèmes principaux
3. Structure le contenu de manière logique
4. Rédige en voix active avec des phrases affirmatives directes
STRUCTURE DU SUMMARY :
### Contexte et besoins
(2-4 points décrivant la situation et les objectifs)
### [Thème principal 1]
(3-4 points avec détails spécifiques)
### [Thème principal 2]
(3-4 points avec détails spécifiques)
[Autres thèmes selon le besoin...]
**Décisions** (si applicables)
- [ ] Décision 1 avec contexte
- [ ] Décision 2 avec justification
**Actions** (si applicables)
- [ ] Action à réaliser
RÈGLES DE RÉDACTION :
Style :
- Utilise la voix active : "Marie propose" plutôt que "Il est proposé par Marie"
- Écris des affirmations directes : "L'équipe valide" plutôt que "La validation semble acquise"
- Conserve tous les noms propres, chiffres exacts, dates et termes techniques
- Élimine les hésitations de l'oral
Contenu :
- Chaque section contient 3-4 points essentiels (pas plus de 4)
- Développe chaque point avec le contexte nécessaire
- Inclus les arguments, justifications et exemples concrets mentionnés
- Préserve les citations importantes
Actions :
- Format strict : "- [ ] Description de l'action"
- Ajoute le nom du responsable UNIQUEMENT s'il est explicitement mentionné dans la transcription
- Exemples corrects :
  ✓ - [ ] Finaliser le rapport - Sophie
  ✓ - [ ] Vérifier les données
- Exemples incorrects :
  ✗ - [ ] Action - Responsable non spécifié
  ✗ - [ ] Action - Équipe technique
EXEMPLE DE STRUCTURE :
### Contexte et besoins
- L'équipe refond l'interface avec une nouvelle palette de couleurs
- Objectif : se différencier des concurrents IA utilisant bleu/violet
- Besoin de 11 couleurs fonctionnelles pour tous les cas d'usage
- Contrainte d'accessibilité avec contraste suffisant
### Options de couleurs analysées
- Proposition 1 : palette orange-rose offrant chaleur et originalité
- Proposition 2 : bleu traditionnel écarté pour éviter la confusion sectorielle
- Tests réalisés sur fonds blanc, noir et coloré
- L'orange-rose présente des défis de contraste à résoudre
**Décisions**
- [ ] Adoption de la direction orange-rose comme palette principale
- [ ] Abandon des options bleu et violet
**Actions**
- [ ] Ajuster le contraste de la palette orange-rose
- [ ] Développer les 11 couleurs complètes - Lucie
- [ ] Tester sur différents fonds`;

// ====== Mode instructions ======
const SHORT_SUMMARY_INSTRUCTIONS = `
MODE: RÉSUMÉ COURT ET CONCIS (ÉQUILIBRÉ)
OBJECTIF:
Produire un résumé clair et complet sans excès de détails, adapté à une lecture rapide mais informative.
STRUCTURE OBLIGATOIRE:
- Contexte (2-3 puces)
- Thèmes (2-3 puces)
- Décisions (2-3 puces)
- Actions (2-3 puces)
CONTRAINTES DE RÉDACTION:
✓ 2 à 3 points par section
✓ 1 à 2 phrases par point
✓ Inclure les informations importantes et les éléments clés
✓ Équilibre entre concision et complétude
✓ Mentionner les aspects principaux sans entrer dans les détails techniques poussés
PRINCIPE:
Capturer l'essentiel avec suffisamment de contexte pour comprendre les enjeux et décisions.
Ni trop court (perte d'information), ni trop long (surcharge).
FORMAT PAR POINT:
[Sujet principal] : [Description en 1-2 phrases avec informations clés]
`;

const DETAILED_SUMMARY_INSTRUCTIONS = `
MODE: RÉSUMÉ DÉTAILLÉ ET EXHAUSTIF
OBJECTIF:
Produire un résumé structuré et complet en appliquant rigoureusement la structure définie dans les règles de référence.
EXIGENCES:
- Structure: 3 à 6 points par section (respecter le format établi)
- Contenu: Inclure tous les détails techniques importants
- Exhaustivité: Ne rien omettre d'essentiel
- Précision: Mentionner les spécifications, chiffres, et éléments clés
- Complétude: Chaque point doit contenir des informations détaillées et complètes
CONSIGNES:
Parcourir systématiquement le contenu source et extraire:
✓ Tous les aspects techniques
✓ Tous les points importants
✓ Toutes les informations critiques
✓ Tous les détails significatifs
Ne pas résumer de manière superficielle - privilégier la précision et l'exhaustivité.
`;

// ─── Tool Definition for Claude (structured summary) ─────────
// On force Claude à appeler ce tool → sortie structurée garantie.
// Le summary est du markdown complet (pas des champs séparés).
const SUMMARY_TOOL = {
  name: "generate_structured_summary",
  description:
    "Génère un résumé structuré à partir d'une transcription audio. " +
    "Retourne un titre concis et un résumé complet en markdown.",
  input_schema: {
    type: "object" as const,
    properties: {
      title: {
        type: "string",
        description: "Titre concis de la réunion (5-8 mots)",
      },
      summary: {
        type: "string",
        description:
          "Contenu markdown complet du résumé structuré (sections, décisions, actions)",
      },
    },
    required: ["title", "summary"],
  },
};

// ─── Claude Sonnet: Tool Use for structured summary ─────────
async function generateSummaryWithToolUse(
  transcript: string,
  summaryMode: string = "detailed"
): Promise<{ title: string; summary: string }> {
  const modeInstructions =
    summaryMode === "short"
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
      system: SYSTEM_PROMPT,
      messages: [
        {
          role: "user",
          content:
            modeInstructions +
            "\nTranscription:\n" +
            transcript +
            '\n\nUtilise le tool generate_structured_summary pour retourner ton analyse. ' +
            'Respecte STRICTEMENT le format avec les champs "title" et "summary".',
        },
      ],
      tools: [SUMMARY_TOOL],
      // Force Claude à utiliser notre tool → sortie structurée garantie
      tool_choice: { type: "tool", name: "generate_structured_summary" },
    }),
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(`Anthropic API error (${response.status}): ${errorText}`);
  }

  const result = await response.json();

  // Extraire le block tool_use de la réponse de Claude
  const toolUseBlock = result.content?.find(
    (block: { type: string }) => block.type === "tool_use"
  );

  if (!toolUseBlock) {
    throw new Error("Claude n'a pas retourné de tool_use block");
  }

  return toolUseBlock.input as { title: string; summary: string };
}

// ─── CORS headers ────────────────────────────────────────────
const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
  "Access-Control-Allow-Headers":
    "Content-Type, Authorization, X-Client-Info, Apikey",
};

// ─── Main handler ────────────────────────────────────────────
Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") {
    return new Response(null, { status: 200, headers: corsHeaders });
  }

  try {
    const body = await req.json();
    const { transcript, summaryMode } = body;
    const mode = summaryMode === "short" ? "short" : "detailed";

    // Validation
    if (!transcript || typeof transcript !== "string") {
      return new Response(
        JSON.stringify({ error: "No transcript provided" }),
        {
          status: 400,
          headers: { ...corsHeaders, "Content-Type": "application/json" },
        }
      );
    }

    if (transcript.trim().length < 50) {
      return new Response(
        JSON.stringify({
          title: "Pas de données",
          summary: "Pas de données",
        }),
        {
          headers: { ...corsHeaders, "Content-Type": "application/json" },
        }
      );
    }

    // ── Step 1: Claude Sonnet Tool Use → markdown summary ──
    console.log(
      `[1/2] Generating ${mode} summary with Claude Sonnet (Tool Use)...`
    );
    const { title, summary } = await generateSummaryWithToolUse(
      transcript,
      mode
    );
    console.log(`[1/2] Summary generated: ${title}`);

    // ── Step 2: Store in Supabase ──
    console.log("[2/2] Storing in Supabase...");
    const supabase = createClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY);

    const { data: insertedRow, error: dbError } = await supabase
      .from("transcriptions")
      .insert({
        transcription_text: transcript,
        summary: { title, summary },
      })
      .select()
      .single();

    if (dbError) {
      throw new Error(`Supabase insert error: ${dbError.message}`);
    }

    console.log(`[2/2] Stored with id: ${insertedRow.id}`);

    // ── Return result ──
    return new Response(
      JSON.stringify({
        success: true,
        id: insertedRow.id,
        title,
        summary,
        metadata: {
          summaryMode: mode,
          created_at: insertedRow.created_at,
        },
      }),
      {
        status: 200,
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      }
    );
  } catch (error) {
    console.error("Error:", error);
    return new Response(
      JSON.stringify({
        error: error instanceof Error ? error.message : "Unknown error",
      }),
      {
        status: 500,
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      }
    );
  }
});
