import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { getUserFromRequest } from "../_shared/auth.ts";

// ─── Module imports ──────────────────────────────────────────
import {
  ANTHROPIC_API_KEY,
  SUPABASE_URL,
  SUPABASE_SERVICE_ROLE_KEY,
  corsHeaders,
} from "./types.ts";
import type { AgentAttachment, AgentResult } from "./types.ts";
import { buildSystemPrompt } from "./systemPrompt.ts";
import { AGENT_TOOLS } from "./toolDefinitions.ts";
import { logActivity } from "./activityLog.ts";

// ─── Tool implementations ───────────────────────────────────
import { executeGetRecentContext, executeGenerateSummary, executeSaveSummary, executeSearchMemory } from "./tools/core.ts";
import { executeSendEmail, executeListEmails, executeReadEmail, executeSendEmailWithAttachment } from "./tools/email.ts";
import { executeSearchContacts, executeSaveContact, executeAddMeetingNote, executeCreateCalendarEvent, executeListCalendarEvents, executeUpdateCalendarEvent } from "./tools/contacts.ts";
import { executeSendSMS, executeSendWhatsApp } from "./tools/messaging.ts";
import { executeHubspotSearchContacts, executeHubspotCreateContact, executeHubspotUpdateContact, executeHubspotDeleteContact, executeHubspotSearchDeals, executeHubspotCreateDeal, executeHubspotUpdateDeal, executeHubspotGetPipeline, executeHubspotGetNotes, executeHubspotCreateNote, executeHubspotUpdateNote } from "./tools/hubspot.ts";
import { executeSlackSendMessage, executeSlackSendDm, executeSlackListChannels, executeSlackListUsers, executeSlackGetChannelHistory } from "./tools/slack.ts";
import { executeWebSearch } from "./tools/web.ts";
import { executeDatagouvSearch, executeDatagouvGetDataset, executeDatagouvQueryData, executeDatagouvGetResourceInfo, executeDatagouvGetMetrics, executeDatagouvSearchDataservices } from "./tools/datagouv.ts";
import { executeCreatePresentation } from "./tools/presentation.ts";
import { executeCreateReport } from "./tools/report.ts";

// ═══════════════════════════════════════════════════════════════
// SSE HELPERS
// ═══════════════════════════════════════════════════════════════

const encoder = new TextEncoder();

function sseEvent(controller: ReadableStreamDefaultController, event: string, data: unknown) {
  controller.enqueue(encoder.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`));
}

// Rappel de mode appendé au DERNIER message user (juste avant la génération).
// Contre le "format priming" : l'historique peut contenir des réponses riches
// (tapées en chat) qui pousseraient l'agent à répondre richement même en voix.
function modeReminder(outputMode: string): string {
  if (outputMode === "chat") return "";
  return "\n\n[MODE VOIX — IMPÉRATIF : ta réponse est LUE à voix haute. Réponds en 2-3 phrases COURTES," +
    " français parlé, AUCUN markdown/liste/titre/gras ni URL lue. Même si des réponses précédentes de" +
    " l'historique sont longues et riches (elles ont été tapées en mode chat), n'imite PAS leur format." +
    " Exécute quand même les actions ; les fichiers et la section « Sources » s'affichent dans le chat (non lus).]";
}

// ═══════════════════════════════════════════════════════════════
// TOOL EXECUTOR (shared between streaming and non-streaming)
// ═══════════════════════════════════════════════════════════════

// deno-lint-ignore no-explicit-any
async function executeTool(toolName: string, toolInput: any, supabase: any, userId: string, userJwt: string): Promise<{ text: string; summaryId?: string }> {
  switch (toolName) {
    case "get_recent_context":
      return { text: await executeGetRecentContext(supabase, toolInput, userId) };
    case "generate_summary":
      return { text: await executeGenerateSummary(toolInput) };
    case "save_summary": {
      const r = await executeSaveSummary(supabase, toolInput, userId);
      return { text: r.text, summaryId: r.id };
    }
    case "search_memory":
      return { text: await executeSearchMemory(supabase, toolInput) };
    case "send_email":
      return { text: await executeSendEmail(toolInput, userJwt) };
    case "list_emails":
      return { text: await executeListEmails(toolInput, userJwt) };
    case "read_email":
      return { text: await executeReadEmail(toolInput, userJwt) };
    case "search_contacts":
      return { text: await executeSearchContacts(toolInput, userJwt) };
    case "save_contact":
      return { text: await executeSaveContact(toolInput, userJwt) };
    case "add_meeting_note":
      return { text: await executeAddMeetingNote(toolInput, userJwt) };
    case "create_calendar_event":
      return { text: await executeCreateCalendarEvent(toolInput, userJwt) };
    case "list_calendar_events":
      return { text: await executeListCalendarEvents(toolInput, userJwt) };
    case "update_calendar_event":
      return { text: await executeUpdateCalendarEvent(toolInput, userJwt) };
    case "send_sms":
      return { text: await executeSendSMS(toolInput, userJwt) };
    case "send_whatsapp":
      return { text: await executeSendWhatsApp(toolInput, userJwt) };
    case "hubspot_search_contacts":
      return { text: await executeHubspotSearchContacts(toolInput, userJwt) };
    case "hubspot_create_contact":
      return { text: await executeHubspotCreateContact(toolInput, userJwt) };
    case "hubspot_update_contact":
      return { text: await executeHubspotUpdateContact(toolInput, userJwt) };
    case "hubspot_delete_contact":
      return { text: await executeHubspotDeleteContact(toolInput, userJwt) };
    case "hubspot_search_deals":
      return { text: await executeHubspotSearchDeals(toolInput, userJwt) };
    case "hubspot_create_deal":
      return { text: await executeHubspotCreateDeal(toolInput, userJwt) };
    case "hubspot_update_deal":
      return { text: await executeHubspotUpdateDeal(toolInput, userJwt) };
    case "hubspot_get_pipeline":
      return { text: await executeHubspotGetPipeline(toolInput, userJwt) };
    case "hubspot_get_notes":
      return { text: await executeHubspotGetNotes(toolInput, userJwt) };
    case "hubspot_create_note":
      return { text: await executeHubspotCreateNote(toolInput, userJwt) };
    case "hubspot_update_note":
      return { text: await executeHubspotUpdateNote(toolInput, userJwt) };
    case "slack_send_message":
      return { text: await executeSlackSendMessage(toolInput, userJwt) };
    case "slack_send_dm":
      return { text: await executeSlackSendDm(toolInput, userJwt) };
    case "slack_list_channels":
      return { text: await executeSlackListChannels(toolInput, userJwt) };
    case "slack_list_users":
      return { text: await executeSlackListUsers(toolInput, userJwt) };
    case "slack_get_channel_history":
      return { text: await executeSlackGetChannelHistory(toolInput, userJwt) };
    case "web_search":
      return { text: await executeWebSearch(toolInput) };
    case "datagouv_search":
      return { text: await executeDatagouvSearch(toolInput) };
    case "datagouv_get_dataset":
      return { text: await executeDatagouvGetDataset(toolInput) };
    case "datagouv_query_data":
      return { text: await executeDatagouvQueryData(toolInput) };
    case "datagouv_get_resource_info":
      return { text: await executeDatagouvGetResourceInfo(toolInput) };
    case "datagouv_get_metrics":
      return { text: await executeDatagouvGetMetrics() };
    case "datagouv_search_dataservices":
      return { text: await executeDatagouvSearchDataservices(toolInput) };
    case "create_presentation":
      return { text: await executeCreatePresentation(toolInput, userJwt) };
    case "create_report":
      return { text: await executeCreateReport(toolInput, userJwt) };
    case "send_email_with_attachment":
      return { text: await executeSendEmailWithAttachment(toolInput, userJwt) };
    default:
      return { text: `Outil inconnu: ${toolName}` };
  }
}

// ═══════════════════════════════════════════════════════════════
// ATTACHMENT EXTRACTION (shared)
// ═══════════════════════════════════════════════════════════════

function extractAttachments(toolName: string, toolResult: string, attachments: AgentAttachment[]) {
  if (toolResult.startsWith("Erreur")) return;

  if (toolName === "create_presentation") {
    const filePathMatch = toolResult.match(/Chemin PPTX \(file_path\): (.+)/);
    const fileNameMatch = toolResult.match(/PPTX: (.+?) \(/);
    if (filePathMatch && fileNameMatch) {
      const att: AgentAttachment = {
        file_path: filePathMatch[1].trim(),
        file_name: fileNameMatch[1].trim(),
        type: "presentation",
      };
      const pdfPathMatch = toolResult.match(/Chemin PDF \(pdf_file_path\): (.+)/);
      const pdfNameMatch = toolResult.match(/PDF: (.+?) \(/);
      if (pdfPathMatch) att.pdf_file_path = pdfPathMatch[1].trim();
      if (pdfNameMatch) att.pdf_file_name = pdfNameMatch[1].trim();
      attachments.push(att);
    }
  } else if (toolName === "create_report") {
    const reportPathMatch = toolResult.match(/Chemin PDF \(file_path\): (.+)/);
    const reportNameMatch = toolResult.match(/PDF: (.+?) \(/);
    if (reportPathMatch && reportNameMatch) {
      attachments.push({
        file_path: reportPathMatch[1].trim(),
        file_name: reportNameMatch[1].trim(),
        type: "report",
        pdf_file_path: reportPathMatch[1].trim(),
        pdf_file_name: reportNameMatch[1].trim(),
      });
    }
  }
}

// ═══════════════════════════════════════════════════════════════
// PARSE ANTHROPIC SSE STREAM
// ═══════════════════════════════════════════════════════════════

interface StreamedMessage {
  // deno-lint-ignore no-explicit-any
  content: any[];
  stop_reason: string | null;
}

async function parseAnthropicStream(
  response: Response,
  controller: ReadableStreamDefaultController | null,
): Promise<StreamedMessage> {
  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  // deno-lint-ignore no-explicit-any
  const contentBlocks: any[] = [];
  let stopReason: string | null = null;

  // Track current content block for accumulation
  // deno-lint-ignore no-explicit-any
  const blockAccumulators: Record<number, any> = {};

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop()!;

    for (const line of lines) {
      if (!line.startsWith("data: ")) continue;
      const dataStr = line.slice(6).trim();
      if (dataStr === "[DONE]") continue;

      let event;
      try {
        event = JSON.parse(dataStr);
      } catch {
        continue;
      }

      switch (event.type) {
        case "content_block_start": {
          const idx = event.index;
          const block = event.content_block;
          if (block.type === "text") {
            blockAccumulators[idx] = { type: "text", text: "" };
          } else if (block.type === "tool_use") {
            blockAccumulators[idx] = {
              type: "tool_use",
              id: block.id,
              name: block.name,
              input: "",
            };
            // Emit tool_start event
            if (controller) {
              sseEvent(controller, "tool_start", { name: block.name });
            }
          }
          break;
        }

        case "content_block_delta": {
          const idx = event.index;
          const delta = event.delta;
          if (delta.type === "text_delta" && blockAccumulators[idx]) {
            blockAccumulators[idx].text += delta.text;
            // Emit text_delta to client
            if (controller) {
              sseEvent(controller, "text_delta", { delta: delta.text });
            }
          } else if (delta.type === "input_json_delta" && blockAccumulators[idx]) {
            blockAccumulators[idx].input += delta.partial_json;
          }
          break;
        }

        case "content_block_stop": {
          const idx = event.index;
          const acc = blockAccumulators[idx];
          if (acc) {
            if (acc.type === "tool_use") {
              // Parse accumulated JSON input
              try {
                acc.input = JSON.parse(acc.input || "{}");
              } catch {
                acc.input = {};
              }
            }
            contentBlocks.push(acc);
          }
          break;
        }

        case "message_delta": {
          if (event.delta?.stop_reason) {
            stopReason = event.delta.stop_reason;
          }
          break;
        }
      }
    }
  }

  return { content: contentBlocks, stop_reason: stopReason };
}

// ═══════════════════════════════════════════════════════════════
// STREAMING AGENT LOOP
// ═══════════════════════════════════════════════════════════════

async function agentLoopStreaming(
  controller: ReadableStreamDefaultController,
  userMessage: string,
  // deno-lint-ignore no-explicit-any
  supabase: any,
  userId: string,
  userJwt: string,
  userContext?: string,
  conversationId?: string,
  outputMode: string = "voice",
) {
  const MAX_TURNS = 5;
  const toolsUsed: string[] = [];
  const attachments: AgentAttachment[] = [];
  let summaryId: string | undefined;

  // deno-lint-ignore no-explicit-any
  const messages: Array<{ role: string; content: any }> = [];

  // Load conversation history
  if (conversationId) {
    try {
      const { data: historyRows, error: histError } = await supabase
        .from("conversation_messages")
        .select("role, content, attachments")
        .eq("conversation_id", conversationId)
        .order("created_at", { ascending: true })
        .limit(20);

      if (!histError && historyRows && historyRows.length > 0) {
        for (const row of historyRows) {
          let content = row.content;
          if (row.role === "assistant" && row.attachments && Array.isArray(row.attachments) && row.attachments.length > 0) {
            const attachInfo = row.attachments
              // deno-lint-ignore no-explicit-any
              .map((a: any) => `[Fichier créé: ${a.file_name} — file_path: ${a.file_path}]`)
              .join("\n");
            content += `\n\n${attachInfo}`;
          }
          messages.push({ role: row.role, content });
        }
        console.log(`[Agent] Loaded ${historyRows.length} previous messages from conversation ${conversationId}`);
      }
    } catch (err) {
      console.warn("[Agent] Failed to load conversation history:", err);
    }
  }

  const baseUserMessage = userContext
    ? `${userMessage}\n\n--- CONTEXT FOURNI ---\n${userContext}`
    : userMessage;
  // Rappel de mode JUSTE avant la génération : bat le "format priming" de
  // l'historique (des réponses précédentes peuvent être riches/tapées en chat).
  const fullUserMessage = baseUserMessage + modeReminder(outputMode);
  messages.push({ role: "user", content: fullUserMessage });

  let fullResponseText = "";

  for (let turn = 0; turn < MAX_TURNS; turn++) {
    console.log(`[Agent] Tour ${turn + 1}/${MAX_TURNS} (streaming)`);

    const response = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
      },
      body: JSON.stringify({
        model: "claude-sonnet-4-6",
        max_tokens: 16384,
        stream: true,
        system: buildSystemPrompt(outputMode),
        messages,
        tools: AGENT_TOOLS,
      }),
    });

    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(`Anthropic API error (${response.status}): ${errorText}`);
    }

    // Parse the Anthropic SSE stream, emitting text_delta events to client
    const result = await parseAnthropicStream(response, controller);

    // Add assistant message to conversation
    messages.push({ role: "assistant", content: result.content });

    // Case 1: end_turn → done
    if (result.stop_reason === "end_turn") {
      const textBlock = result.content.find(
        (block: { type: string }) => block.type === "text"
      );
      fullResponseText = textBlock?.text || "Désolé, je n'ai pas pu formuler de réponse.";

      const agentResult: AgentResult = {
        response: fullResponseText,
        tools_used: toolsUsed,
      };
      if (summaryId) agentResult.summary_id = summaryId;
      if (attachments.length > 0) agentResult.attachments = attachments;

      sseEvent(controller, "done", agentResult);
      controller.close();
      return;
    }

    // Case 2: tool_use → execute tools
    if (result.stop_reason === "tool_use") {
      const toolUseBlocks = result.content.filter(
        (block: { type: string }) => block.type === "tool_use"
      );

      const toolResults = await Promise.all(
        // deno-lint-ignore no-explicit-any
        toolUseBlocks.map(async (toolCall: any) => {
          console.log(`[Agent] → Appel outil: ${toolCall.name}`, toolCall.input);
          toolsUsed.push(toolCall.name);

          let toolResultText: string;
          try {
            const execResult = await executeTool(toolCall.name, toolCall.input, supabase, userId, userJwt);
            toolResultText = execResult.text;
            if (execResult.summaryId) summaryId = execResult.summaryId;
          } catch (err) {
            toolResultText = `Erreur lors de l'exécution de ${toolCall.name}: ${
              err instanceof Error ? err.message : "Erreur inconnue"
            }`;
          }

          // Extract attachments
          extractAttachments(toolCall.name, toolResultText, attachments);

          console.log(`[Agent] ← Résultat ${toolCall.name}: ${toolResultText.substring(0, 100)}...`);

          // Log activity (non-blocking)
          const actStatus = toolResultText.startsWith("Erreur") ? "error" as const : "success" as const;
          logActivity(supabase, userId, toolCall.name, toolCall.input, toolResultText, actStatus);

          // Emit tool_result event
          sseEvent(controller, "tool_result", {
            name: toolCall.name,
            status: actStatus,
            summary: toolResultText.substring(0, 200),
          });

          return {
            type: "tool_result" as const,
            tool_use_id: toolCall.id,
            content: toolResultText,
          };
        })
      );

      messages.push({ role: "user", content: toolResults });
      continue;
    }

    // Case 3: unexpected stop
    const fallbackText = result.content?.find(
      (block: { type: string }) => block.type === "text"
    );
    fullResponseText = fallbackText?.text || "Désolé, la réponse a été interrompue.";

    const fallbackResult: AgentResult = {
      response: fullResponseText,
      tools_used: toolsUsed,
    };
    if (attachments.length > 0) fallbackResult.attachments = attachments;
    sseEvent(controller, "done", fallbackResult);
    controller.close();
    return;
  }

  // Max turns reached
  const maxTurnResult: AgentResult = {
    response: "Désolé, j'ai atteint la limite de traitement. Essaie de simplifier ta demande.",
    tools_used: toolsUsed,
  };
  if (attachments.length > 0) maxTurnResult.attachments = attachments;
  sseEvent(controller, "done", maxTurnResult);
  controller.close();
}

// ═══════════════════════════════════════════════════════════════
// NON-STREAMING AGENT LOOP (fallback / backward compatibility)
// ═══════════════════════════════════════════════════════════════

async function agentLoop(
  userMessage: string,
  // deno-lint-ignore no-explicit-any
  supabase: any,
  userId: string,
  userJwt: string,
  userContext?: string,
  conversationId?: string,
  outputMode: string = "voice",
): Promise<AgentResult> {
  const MAX_TURNS = 5;
  const toolsUsed: string[] = [];
  const attachments: AgentAttachment[] = [];
  let summaryId: string | undefined;

  // deno-lint-ignore no-explicit-any
  const messages: Array<{ role: string; content: any }> = [];

  // Charger l'historique de conversation depuis la DB si conversation_id fourni
  if (conversationId) {
    try {
      const { data: historyRows, error: histError } = await supabase
        .from("conversation_messages")
        .select("role, content, attachments")
        .eq("conversation_id", conversationId)
        .order("created_at", { ascending: true })
        .limit(20);

      if (!histError && historyRows && historyRows.length > 0) {
        for (const row of historyRows) {
          let content = row.content;
          if (row.role === "assistant" && row.attachments && Array.isArray(row.attachments) && row.attachments.length > 0) {
            const attachInfo = row.attachments
              // deno-lint-ignore no-explicit-any
              .map((a: any) => `[Fichier créé: ${a.file_name} — file_path: ${a.file_path}]`)
              .join("\n");
            content += `\n\n${attachInfo}`;
          }
          messages.push({ role: row.role, content });
        }
        console.log(`[Agent] Loaded ${historyRows.length} previous messages from conversation ${conversationId}`);
      }
    } catch (err) {
      console.warn("[Agent] Failed to load conversation history:", err);
    }
  }

  const fullUserMessage = (userContext
    ? `${userMessage}\n\n--- CONTEXT FOURNI ---\n${userContext}`
    : userMessage) + modeReminder(outputMode);
  messages.push({ role: "user", content: fullUserMessage });

  for (let turn = 0; turn < MAX_TURNS; turn++) {
    console.log(`[Agent] Tour ${turn + 1}/${MAX_TURNS}`);

    const response = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
      },
      body: JSON.stringify({
        model: "claude-sonnet-4-6",
        max_tokens: 16384,
        system: buildSystemPrompt(outputMode),
        messages,
        tools: AGENT_TOOLS,
      }),
    });

    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(`Anthropic API error (${response.status}): ${errorText}`);
    }

    const result = await response.json();
    messages.push({ role: "assistant", content: result.content });

    if (result.stop_reason === "end_turn") {
      const textBlock = result.content.find(
        (block: { type: string }) => block.type === "text"
      );
      const agentResult: AgentResult = {
        response: textBlock?.text || "Désolé, je n'ai pas pu formuler de réponse.",
        tools_used: toolsUsed,
      };
      if (summaryId) agentResult.summary_id = summaryId;
      if (attachments.length > 0) agentResult.attachments = attachments;
      return agentResult;
    }

    if (result.stop_reason === "tool_use") {
      const toolUseBlocks = result.content.filter(
        (block: { type: string }) => block.type === "tool_use"
      );

      const toolResults = await Promise.all(
        // deno-lint-ignore no-explicit-any
        toolUseBlocks.map(async (toolCall: any) => {
          console.log(`[Agent] → Appel outil: ${toolCall.name}`, toolCall.input);
          toolsUsed.push(toolCall.name);

          let toolResultText: string;
          try {
            const execResult = await executeTool(toolCall.name, toolCall.input, supabase, userId, userJwt);
            toolResultText = execResult.text;
            if (execResult.summaryId) summaryId = execResult.summaryId;
          } catch (err) {
            toolResultText = `Erreur lors de l'exécution de ${toolCall.name}: ${
              err instanceof Error ? err.message : "Erreur inconnue"
            }`;
          }

          extractAttachments(toolCall.name, toolResultText, attachments);

          console.log(`[Agent] ← Résultat ${toolCall.name}: ${toolResultText.substring(0, 100)}...`);

          const actStatus = toolResultText.startsWith("Erreur") ? "error" as const : "success" as const;
          logActivity(supabase, userId, toolCall.name, toolCall.input, toolResultText, actStatus);

          return {
            type: "tool_result" as const,
            tool_use_id: toolCall.id,
            content: toolResultText,
          };
        })
      );

      messages.push({ role: "user", content: toolResults });
      continue;
    }

    const fallbackText = result.content?.find(
      (block: { type: string }) => block.type === "text"
    );
    return {
      response: fallbackText?.text || "Désolé, la réponse a été interrompue. Essaie de reformuler.",
      tools_used: toolsUsed,
    };
  }

  return {
    response: "Désolé, j'ai atteint la limite de traitement. Essaie de simplifier ta demande.",
    tools_used: toolsUsed,
  };
}

// ═══════════════════════════════════════════════════════════════
// HANDLER PRINCIPAL
// ═══════════════════════════════════════════════════════════════

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") {
    return new Response(null, { status: 200, headers: corsHeaders });
  }

  try {
    const body = await req.json();
    const { message, context, conversation_id, stream: wantStream, output_mode } = body;
    // 'voice' (enceinte/TTS : concis, parlé) ou 'chat' (web : riche, markdown). Défaut voice.
    const outputMode = output_mode === "chat" ? "chat" : "voice";

    if (!message || typeof message !== "string" || message.trim().length === 0) {
      return new Response(
        JSON.stringify({ error: "Le champ 'message' est requis." }),
        {
          status: 400,
          headers: { ...corsHeaders, "Content-Type": "application/json" },
        }
      );
    }

    const userContext =
      context && typeof context === "string" && context.trim().length > 0
        ? context.trim()
        : undefined;

    let userId: string;
    let userJwt: string;
    try {
      const authUser = await getUserFromRequest(req);
      userId = authUser.user_id;
      userJwt = req.headers.get("Authorization") || "";
    } catch {
      userId = "anonymous";
      userJwt = "";
    }

    const supabase = createClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY);
    const convId = conversation_id && typeof conversation_id === "string" ? conversation_id.trim() : undefined;
    console.log(`[aura-agent] Message: "${message.substring(0, 100)}" | User: ${userId}${userContext ? ` | Context: ${userContext.length} chars` : ""}${convId ? ` | Conv: ${convId}` : ""} | Stream: ${!!wantStream}`);

    // ── STREAMING MODE ──
    if (wantStream) {
      const stream = new ReadableStream({
        start(controller) {
          agentLoopStreaming(
            controller,
            message.trim(),
            supabase,
            userId,
            userJwt,
            userContext,
            convId,
            outputMode,
          ).catch((error) => {
            console.error("[aura-agent] Streaming error:", error);
            try {
              sseEvent(controller, "error", {
                error: error instanceof Error ? error.message : "Erreur inconnue",
              });
              controller.close();
            } catch {
              // Controller may already be closed
            }
          });
        },
      });

      return new Response(stream, {
        status: 200,
        headers: {
          ...corsHeaders,
          "Content-Type": "text/event-stream",
          "Cache-Control": "no-cache",
          "Connection": "keep-alive",
        },
      });
    }

    // ── NON-STREAMING MODE (backward compatible) ──
    const result = await agentLoop(message.trim(), supabase, userId, userJwt, userContext, convId, outputMode);
    console.log(`[aura-agent] Terminé. Outils: [${result.tools_used.join(", ")}]`);

    return new Response(JSON.stringify(result), {
      status: 200,
      headers: { ...corsHeaders, "Content-Type": "application/json" },
    });
  } catch (error) {
    console.error("[aura-agent] Error:", error);
    return new Response(
      JSON.stringify({
        error: error instanceof Error ? error.message : "Erreur inconnue",
      }),
      {
        status: 500,
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      }
    );
  }
});
