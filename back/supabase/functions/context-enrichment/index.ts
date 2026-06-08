import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "jsr:@supabase/supabase-js@2";

var ANTHROPIC_API_KEY = Deno.env.get("ANTHROPIC_API_KEY")!;
var SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
var SUPABASE_ANON_KEY = Deno.env.get("SUPABASE_ANON_KEY") || "";

var corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, Authorization, Apikey, X-Client-Info",
};

var STOP_WORDS = new Set(
  ("le la les un une des de du a au aux en et ou que qui quoi dont "
  + "ce cette ces mon ma mes ton ta tes son sa ses notre nos votre vos leur leurs "
  + "je tu il elle on nous vous ils elles me te se lui y "
  + "ne pas plus rien jamais "
  + "est sont ont fait faire etre avoir "
  + "pour par avec dans sur sous entre vers chez sans contre "
  + "tout tous toute toutes meme aussi tres bien peu trop "
  + "ca c mais donc car si alors quand comment pourquoi "
  + "moi toi soi ici la quel quelle quels quelles "
  + "ai as avons avez eu ete suis es sommes etes etait etais "
  + "dit dis fais vas va vais allez allons veux veut "
  + "resume resumer envoie envoyer mail email").split(/\s+/)
);

// Single unified prompt: classify + extract time if needed
var UNIFIED_SYSTEM = 'Tu es un assistant qui analyse les commandes vocales en francais. Tu fais DEUX choses en une seule reponse:\n\n1. DETERMINER si la commande necessite de chercher dans la memoire/contexte (transcriptions, reunions, conversations passees)\n2. SI OUI, extraire la fenetre temporelle\n\nReponds UNIQUEMENT en JSON valide, sans markdown:\n\nSi la commande NE NECESSITE PAS de contexte (question generale, meteo, calcul, SMS simple, traduction, heure, salutation, creation rdv, recherche web, etc.):\n{"needs_context": false}\n\nSi la commande NECESSITE du contexte (resume, recherche memoire, "qu\'est-ce qu\'on a dit", rappel de reunion, email recapitulatif, etc.):\n{"needs_context": true, "date_start": "YYYY-MM-DDTHH:MM:SS", "date_end": "YYYY-MM-DDTHH:MM:SS", "keywords": ["mot1", "mot2"]}\n\nIMPORTANT: Les dates retournees sont dans le fuseau horaire LOCAL de l\'utilisateur (meme fuseau que la date fournie).\n\nRegles temporelles (seulement si needs_context=true):\n- "maintenant" ou "juste termine" -> derniere heure\n- "entre 16h et 17h30" -> 16:00:00 a 17:30:00\n- "ce matin" -> 06:00:00 a 12:00:00\n- "l\'aprem" -> 12:00:00 a 18:00:00\n- "le soir" -> 18:00:00 a 23:00:00\n- "hier" -> jour precedent, journee entiere\n- "hier matin" -> jour precedent 06:00 a 12:00\n- "mardi dernier" -> mardi precedent, journee entiere\n- "il y a 20 minutes" -> maintenant - 20min a maintenant\n- "la semaine derniere" -> lundi 00:00 a dimanche 23:59\n- Si aucune ref temporelle precise -> 3 dernieres heures\n- keywords = mots-cles importants (sujets, noms, projets), max 5';

// --- Timezone helpers ---

function getLocalNow(tz: string): string {
  var now = new Date();
  var parts: Record<string, string> = {};
  var fmt = new Intl.DateTimeFormat("en-CA", {
    timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  });
  for (var p of fmt.formatToParts(now)) { parts[p.type] = p.value; }
  return parts.year + "-" + parts.month + "-" + parts.day + "T" + parts.hour + ":" + parts.minute + ":" + parts.second;
}

function getTimezoneOffsetStr(tz: string): string {
  var formatter = new Intl.DateTimeFormat("en-US", { timeZone: tz, timeZoneName: "longOffset" });
  var parts = formatter.formatToParts(new Date());
  var tzPart = parts.find(function(p) { return p.type === "timeZoneName"; });
  var raw = tzPart ? tzPart.value.replace("GMT", "") : "+00:00";
  return raw === "" ? "+00:00" : raw;
}

function localToUtc(localIso: string, offsetStr: string): Date {
  return new Date(localIso + offsetStr);
}

function extractKeywords(command: string): string[] {
  var words = command.toLowerCase().match(/[a-z]+/g) || [];
  return words.filter(function(w) { return !STOP_WORDS.has(w) && w.length > 2; }).slice(0, 5);
}

function formatTime(isoStr: string, tz: string): string {
  try { return new Date(isoStr).toLocaleTimeString("fr-FR", { timeZone: tz, hour: "2-digit", minute: "2-digit" }); }
  catch { return "??:??"; }
}

function formatDateTime(isoStr: string, tz: string): string {
  try {
    var dt = new Date(isoStr);
    return dt.toLocaleDateString("fr-FR", { timeZone: tz, day: "2-digit", month: "2-digit" })
      + " " + dt.toLocaleTimeString("fr-FR", { timeZone: tz, hour: "2-digit", minute: "2-digit" });
  } catch { return "??/?? ??:??"; }
}

// --- Unified LLM call: classify + extract ---

interface AnalysisResult {
  needsContext: boolean;
  dateStart: Date | null;
  dateEnd: Date | null;
  keywords: string[];
}

async function analyzeCommand(
  command: string, userTimezone: string
): Promise<AnalysisResult> {
  var localNow = getLocalNow(userTimezone);
  var offsetStr = getTimezoneOffsetStr(userTimezone);
  var userPrompt = "Date/heure LOCALE actuelle (" + userTimezone + "): " + localNow + "\nCommande: " + command;

  console.log("[context-enrichment] Haiku analysis: localNow=" + localNow + " offset=" + offsetStr);

  try {
    var response = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: { "Content-Type": "application/json", "x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01" },
      body: JSON.stringify({
        model: "claude-haiku-4-5-20251001", max_tokens: 200,
        system: UNIFIED_SYSTEM,
        messages: [{ role: "user", content: userPrompt }],
      }),
    });
    if (!response.ok) throw new Error("Anthropic error: " + response.status);
    var result = await response.json();
    var raw = result.content?.[0]?.text?.trim() || "";
    if (raw.startsWith("```")) raw = raw.replace(/^```(?:json)?\s*/, "").replace(/\s*```$/, "");
    var parsed = JSON.parse(raw);
    console.log("[context-enrichment] Haiku result: " + JSON.stringify(parsed));

    if (!parsed.needs_context) {
      return { needsContext: false, dateStart: null, dateEnd: null, keywords: [] };
    }

    // needs_context = true, extract time range
    var dateStart = localToUtc(parsed.date_start, offsetStr);
    var dateEnd = localToUtc(parsed.date_end, offsetStr);
    console.log("[context-enrichment] Local: " + parsed.date_start + " -> " + parsed.date_end + " (" + offsetStr + ")");
    console.log("[context-enrichment] UTC:   " + dateStart.toISOString() + " -> " + dateEnd.toISOString());
    return { needsContext: true, dateStart: dateStart, dateEnd: dateEnd, keywords: parsed.keywords || [] };
  } catch (e) {
    console.warn("[context-enrichment] Haiku failed, fallback to context with 3h window:", e);
    var now = new Date();
    return { needsContext: true, dateStart: new Date(now.getTime() - 3*3600000), dateEnd: now, keywords: [] };
  }
}

interface Segment { text: string; timestamp: string; is_partial?: boolean; }
interface Body { command: string; immediate_context?: Segment[]; user_timezone?: string; }

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") return new Response(null, { status: 200, headers: corsHeaders });

  try {
    var authHeader = req.headers.get("Authorization") || "";
    var userJwt = authHeader.replace(/^Bearer\s+/i, "").trim();
    if (!userJwt) return new Response(JSON.stringify({ error: "Missing Authorization" }), { status: 401, headers: { ...corsHeaders, "Content-Type": "application/json" } });

    var supabase = createClient(SUPABASE_URL, SUPABASE_ANON_KEY, { global: { headers: { Authorization: "Bearer " + userJwt } } });
    var authResult = await supabase.auth.getUser(userJwt);
    var user = authResult.data.user;
    if (authResult.error || !user) return new Response(JSON.stringify({ error: "Invalid token" }), { status: 401, headers: { ...corsHeaders, "Content-Type": "application/json" } });

    var body: Body = await req.json();
    var command = body.command || "";
    var immediate_context = body.immediate_context || [];
    var user_timezone = body.user_timezone || "Europe/Paris";
    if (!command) return new Response(JSON.stringify({ error: "Missing command" }), { status: 400, headers: { ...corsHeaders, "Content-Type": "application/json" } });

    console.log("\n[context-enrichment] === REQUEST ===");
    console.log("[context-enrichment] Command: \"" + command + "\"");

    // Single Haiku call: classify + extract time if needed
    var analysis = await analyzeCommand(command, user_timezone);

    if (!analysis.needsContext) {
      // No context needed - return just recent immediate context for conversational continuity
      var fallback = immediate_context.length > 0
        ? immediate_context.slice(-10).map(function(s: Segment) { return s.text; }).join(" ")
        : "";
      console.log("[context-enrichment] SKIP: no context needed. Returning " + fallback.length + " chars immediate only\n");
      return new Response(JSON.stringify({
        enriched_context: fallback,
        time_range: null,
        keywords: [],
        sections_count: 0,
        skipped: true,
      }), { status: 200, headers: { ...corsHeaders, "Content-Type": "application/json" } });
    }

    // Context needed - proceed with 3 tiers
    var dateStart = analysis.dateStart!;
    var dateEnd = analysis.dateEnd!;
    var llmKeywords = analysis.keywords;

    console.log("[context-enrichment] Context needed. Segments: " + immediate_context.length + ", tz: " + user_timezone);

    var sections: string[] = [];

    // Tier 1: Immediate context
    if (immediate_context.length > 0) {
      var tier1Lines = immediate_context.slice(-30).map(function(seg: Segment) { return "[" + formatTime(seg.timestamp, user_timezone) + "] " + seg.text; });
      sections.push("--- CONTEXTE IMMEDIAT (dernieres minutes) ---\n" + tier1Lines.join("\n"));
      console.log("[context-enrichment] Tier1: " + tier1Lines.length + " segments");
    }

    // Tier 2: Historical
    try {
      var sumResp = await supabase.from("context_summaries").select("summary_text, time_start, time_end, summary_type").gte("time_end", dateStart.toISOString()).lte("time_start", dateEnd.toISOString()).order("time_end", { ascending: false }).limit(5);
      var summaryList = sumResp.data || [];
      var latestSummaryEnd = summaryList.length > 0 ? summaryList[0].time_end : dateStart.toISOString();
      var segResp = await supabase.from("live_segments").select("text, spoken_at").gte("spoken_at", latestSummaryEnd).lte("spoken_at", dateEnd.toISOString()).order("spoken_at", { ascending: true }).limit(100);
      var segmentList = segResp.data || [];
      console.log("[context-enrichment] Tier2: " + summaryList.length + " summaries, " + segmentList.length + " segments");
      if (summaryList.length > 0 || segmentList.length > 0) {
        var tier2Lines: string[] = [];
        for (var s of summaryList) tier2Lines.push("[Resume " + formatDateTime(s.time_start, user_timezone) + " -> " + formatDateTime(s.time_end, user_timezone) + "] " + s.summary_text);
        for (var seg of segmentList) tier2Lines.push("[" + formatTime(seg.spoken_at, user_timezone) + "] " + seg.text);
        sections.push("--- CONTEXTE HISTORIQUE (" + formatDateTime(dateStart.toISOString(), user_timezone) + " -> " + formatDateTime(dateEnd.toISOString(), user_timezone) + ") ---\n" + tier2Lines.join("\n"));
      }
    } catch (e) { console.warn("[context-enrichment] Tier2 error:", e); }

    // Tier 3: FTS
    var keywords = llmKeywords.length > 0 ? llmKeywords : extractKeywords(command);
    if (keywords.length > 0) {
      try {
        var ftsQuery = keywords.join(" & ");
        var searchSince = new Date(dateStart.getTime() - 30*24*3600000).toISOString();
        var ftsResp = await supabase.from("live_segments").select("text, spoken_at").textSearch("fts", ftsQuery, { config: "french" }).gte("spoken_at", searchSince).order("spoken_at", { ascending: false }).limit(15);
        var resultList = ftsResp.data || [];
        console.log("[context-enrichment] Tier3: " + resultList.length + " FTS results [" + keywords.join(", ") + "]");
        if (resultList.length > 0) {
          var tier3Lines = resultList.map(function(r: { text: string; spoken_at: string }) { return "[" + formatDateTime(r.spoken_at, user_timezone) + "] " + r.text; });
          sections.push("--- RESULTATS RECHERCHE (mots-cles: " + keywords.join(", ") + ") ---\n" + tier3Lines.join("\n"));
        }
      } catch (e) { console.warn("[context-enrichment] Tier3 error:", e); }
    }

    // Assemble
    var enrichedContext = sections.length === 0 ? immediate_context.map(function(s: Segment) { return s.text; }).join(" ") : sections.join("\n\n");

    console.log("\n[context-enrichment] === ENRICHED CONTEXT (" + enrichedContext.length + " chars, " + sections.length + " sections) ===");
    console.log(enrichedContext);
    console.log("[context-enrichment] === END CONTEXT ===\n");

    return new Response(JSON.stringify({ enriched_context: enrichedContext, time_range: { start: dateStart.toISOString(), end: dateEnd.toISOString() }, keywords: keywords, sections_count: sections.length, skipped: false }), { status: 200, headers: { ...corsHeaders, "Content-Type": "application/json" } });
  } catch (e) {
    console.error("[context-enrichment] Error:", e);
    return new Response(JSON.stringify({ error: e instanceof Error ? e.message : "Internal error" }), { status: 500, headers: { ...corsHeaders, "Content-Type": "application/json" } });
  }
});
