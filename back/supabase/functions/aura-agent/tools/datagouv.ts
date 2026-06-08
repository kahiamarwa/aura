// MCP session state (module-level)
let mcpSessionId: string | null = null;
let mcpInitialized = false;

async function initMcpSession(): Promise<void> {
  if (mcpInitialized) return;
  mcpInitialized = true;
  console.log("[MCP] Initialisation session data.gouv.fr...");

  const response = await fetch("https://mcp.data.gouv.fr/mcp", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Accept": "application/json, text/event-stream",
    },
    body: JSON.stringify({
      jsonrpc: "2.0",
      id: 0,
      method: "initialize",
      params: {
        protocolVersion: "2025-03-26",
        capabilities: {},
        clientInfo: { name: "aura-agent", version: "1.0.0" },
      },
    }),
  });

  const sessionId = response.headers.get("mcp-session-id");
  if (sessionId) {
    mcpSessionId = sessionId;
    console.log(`[MCP] Session ID obtenu: ${sessionId.substring(0, 16)}...`);
  }

  // Consommer la réponse
  await parseMcpResponse(response);
}

// deno-lint-ignore no-explicit-any
async function parseMcpResponse(response: Response): Promise<string> {
  const contentType = response.headers.get("content-type") || "";

  if (contentType.includes("text/event-stream")) {
    const text = await response.text();
    const lines = text.split("\n");
    let lastData = "";
    for (const line of lines) {
      if (line.startsWith("data: ")) {
        lastData = line.substring(6);
      }
    }
    if (!lastData) return text;
    try {
      const parsed = JSON.parse(lastData);
      if (parsed.error) return `Erreur MCP: ${parsed.error.message}`;
      const content = parsed.result?.content;
      if (Array.isArray(content)) {
        return content.map((c: any) => c.text || "").join("\n");
      }
      return JSON.stringify(parsed.result);
    } catch {
      return lastData;
    }
  } else {
    const result = await response.json();
    if (result.error) return `Erreur MCP: ${result.error.message}`;
    const content = result.result?.content;
    if (Array.isArray(content)) {
      return content.map((c: any) => c.text || "").join("\n");
    }
    return JSON.stringify(result.result);
  }
}

async function callMcpTool(toolName: string, args: Record<string, unknown>): Promise<string> {
  await initMcpSession();

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
  };
  if (mcpSessionId) headers["Mcp-Session-Id"] = mcpSessionId;

  const response = await fetch("https://mcp.data.gouv.fr/mcp", {
    method: "POST",
    headers,
    body: JSON.stringify({
      jsonrpc: "2.0",
      id: 1,
      method: "tools/call",
      params: { name: toolName, arguments: args },
    }),
  });

  if (!response.ok) {
    return `Erreur MCP (${response.status}): ${await response.text()}`;
  }

  const newSessionId = response.headers.get("mcp-session-id");
  if (newSessionId) mcpSessionId = newSessionId;

  return parseMcpResponse(response);
}

export async function executeDatagouvSearch(params: {
  query: string; page_size?: number;
}): Promise<string> {
  console.log(`[datagouv_search] Recherche: "${params.query}"`);
  return callMcpTool("search_datasets", {
    query: params.query,
    page_size: Math.min(params.page_size || 5, 10),
  });
}

export async function executeDatagouvGetDataset(params: {
  dataset_id: string;
}): Promise<string> {
  console.log(`[datagouv_get_dataset] Dataset: ${params.dataset_id}`);
  const info = await callMcpTool("get_dataset_info", { dataset_id: params.dataset_id });
  const resources = await callMcpTool("list_dataset_resources", { dataset_id: params.dataset_id });
  return `${info}\n\n--- Ressources ---\n${resources}`;
}

export async function executeDatagouvQueryData(params: {
  question: string; resource_id: string; page_size?: number;
  filter_column?: string; filter_value?: string; filter_operator?: string;
  sort_column?: string; sort_direction?: string;
}): Promise<string> {
  console.log(`[datagouv_query_data] Resource: ${params.resource_id}, Question: "${params.question}"`);
  const args: Record<string, unknown> = {
    question: params.question,
    resource_id: params.resource_id,
    page_size: Math.min(params.page_size || 20, 200),
  };
  if (params.filter_column) args.filter_column = params.filter_column;
  if (params.filter_value) args.filter_value = params.filter_value;
  if (params.filter_operator) args.filter_operator = params.filter_operator;
  if (params.sort_column) args.sort_column = params.sort_column;
  if (params.sort_direction) args.sort_direction = params.sort_direction;
  return callMcpTool("query_resource_data", args);
}

export async function executeDatagouvGetResourceInfo(params: {
  resource_id: string;
}): Promise<string> {
  console.log(`[datagouv_get_resource_info] Resource: ${params.resource_id}`);
  return callMcpTool("get_resource_info", { resource_id: params.resource_id });
}

export async function executeDatagouvGetMetrics(): Promise<string> {
  console.log(`[datagouv_get_metrics] Récupération des métriques`);
  return callMcpTool("get_metrics", {});
}

export async function executeDatagouvSearchDataservices(params: {
  query: string; page_size?: number;
}): Promise<string> {
  console.log(`[datagouv_search_dataservices] Recherche: "${params.query}"`);
  return callMcpTool("search_dataservices", {
    query: params.query,
    page_size: Math.min(params.page_size || 5, 10),
  });
}
