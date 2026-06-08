import { TAVILY_API_KEY } from "../types.ts";

export async function executeWebSearch(params: {
  query: string;
  search_depth?: string;
  topic?: string;
  max_results?: number;
}): Promise<string> {
  if (!TAVILY_API_KEY) return "Erreur: TAVILY_API_KEY non configurée.";

  console.log(`[web_search] Recherche: "${params.query}"`);

  try {
    const response = await fetch("https://api.tavily.com/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        api_key: TAVILY_API_KEY,
        query: params.query,
        search_depth: params.search_depth || "basic",
        topic: params.topic || "general",
        max_results: Math.min(params.max_results || 5, 10),
        include_answer: true,
      }),
    });

    if (!response.ok) {
      const err = await response.text();
      return `Erreur recherche web (${response.status}): ${err}`;
    }

    const result = await response.json();
    let output = "";

    if (result.answer) {
      output += `Réponse synthétisée:\n${result.answer}\n\n`;
    }

    if (result.results?.length > 0) {
      output += `Sources (${result.results.length}):\n`;
      // deno-lint-ignore no-explicit-any
      result.results.forEach((r: any, i: number) => {
        output += `${i + 1}. ${r.title}\n   ${r.content}\n   Source: ${r.url}\n\n`;
      });
    } else if (!output) {
      output = "Aucun résultat trouvé pour cette recherche.";
    }

    return output;
  } catch (err) {
    const errMsg = err instanceof Error ? err.message : String(err);
    console.error(`[web_search] Exception:`, errMsg);
    return `Erreur recherche web: ${errMsg}`;
  }
}
