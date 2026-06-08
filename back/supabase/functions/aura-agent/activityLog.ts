// ─── Activity Logging ─────────────────────────────────────────
// deno-lint-ignore no-explicit-any
export function buildActivityInfo(toolName: string, input: any, _result: string): {
  actionType: string; description: string; metadata: Record<string, unknown>;
} {
  switch (toolName) {
    case "send_email":
      return {
        actionType: "email_sent",
        description: `Email envoyé — ${input.subject || "Sans objet"} à ${input.to}`,
        metadata: { to: input.to, subject: input.subject },
      };
    case "send_sms":
      return {
        actionType: "sms_sent",
        description: `SMS envoyé à ${input.to}`,
        metadata: { to: input.to },
      };
    case "send_whatsapp":
      return {
        actionType: "whatsapp_sent",
        description: input.media_type
          ? `${input.media_type === "image" ? "Image" : "Document"} WhatsApp envoyé à ${input.to}`
          : `Message WhatsApp envoyé à ${input.to}`,
        metadata: { to: input.to, media_type: input.media_type, file_name: input.file_name },
      };
    case "generate_summary":
      return {
        actionType: "summary_generated",
        description: `Résumé généré — ${input.format || "standard"}`,
        metadata: { format: input.format },
      };
    case "save_summary":
      return {
        actionType: "summary_saved",
        description: `Résumé sauvegardé — ${input.title || ""}`,
        metadata: { title: input.title },
      };
    case "get_recent_context":
      return {
        actionType: "context_enriched",
        description: `Contexte enrichi — ${input.minutes || 180} min`,
        metadata: { minutes: input.minutes },
      };
    case "search_memory":
      return {
        actionType: "memory_searched",
        description: `Mémoire consultée — "${input.query}"`,
        metadata: { query: input.query },
      };
    case "create_presentation":
      return {
        actionType: "presentation_created",
        description: `Présentation créée — ${input.title || "Sans titre"} (${input.slides?.length || 0} slides)`,
        metadata: { title: input.title, slides_count: input.slides?.length },
      };
    case "send_email_with_attachment":
      return {
        actionType: "email_with_attachment_sent",
        description: `Email avec pièce jointe envoyé à ${input.to} — ${input.file_name || ""}`,
        metadata: { to: input.to, subject: input.subject, file_name: input.file_name },
      };
    case "search_contacts":
      return {
        actionType: "contact_found",
        description: `Recherche contact — "${input.query || input.name || ""}"`,
        metadata: { query: input.query || input.name },
      };
    case "save_contact":
      return {
        actionType: "contact_saved",
        description: `Contact créé — ${input.name}`,
        metadata: { name: input.name, email: input.email },
      };
    case "add_meeting_note":
      return {
        actionType: "meeting_note_added",
        description: `Note de réunion — ${input.contact_name}`,
        metadata: { contact: input.contact_name, title: input.title },
      };
    case "create_calendar_event":
      return {
        actionType: "calendar_created",
        description: `Événement créé — "${input.title}"`,
        metadata: { title: input.title, start: input.start_datetime },
      };
    case "list_calendar_events":
      return {
        actionType: "calendar_listed",
        description: `Agenda consulté`,
        metadata: { query: input.query, date: input.date },
      };
    case "update_calendar_event":
      return {
        actionType: "calendar_updated",
        description: `Événement modifié — "${input.title || input.event_id}"`,
        metadata: { event_id: input.event_id },
      };
    case "slack_send_message":
      return {
        actionType: "slack_message",
        description: input.file_name
          ? `Fichier Slack — ${input.file_name} dans #${input.channel}`
          : `Message Slack — #${input.channel}`,
        metadata: { channel: input.channel, file_name: input.file_name },
      };
    case "slack_send_dm":
      return {
        actionType: "slack_dm",
        description: `Message privé Slack`,
        metadata: { user_id: input.user_id },
      };
    case "web_search":
      return {
        actionType: "web_search",
        description: `Recherche web — "${input.query}"`,
        metadata: { query: input.query },
      };
    default:
      if (toolName.startsWith("datagouv_")) {
        return {
          actionType: "datagouv_search",
          description: `Recherche data.gouv.fr — "${input.query || input.dataset_id || input.resource_id || ""}"`,
          metadata: input,
        };
      }
      if (toolName.startsWith("hubspot_")) {
        return {
          actionType: "hubspot_action",
          description: `Action HubSpot — ${toolName.replace("hubspot_", "")}`,
          metadata: input,
        };
      }
      return {
        actionType: toolName,
        description: `Action — ${toolName}`,
        metadata: input,
      };
  }
}

// deno-lint-ignore no-explicit-any
export async function logActivity(
  supabase: any,
  userId: string,
  toolName: string,
  // deno-lint-ignore no-explicit-any
  toolInput: any,
  toolResult: string,
  status: "success" | "error"
): Promise<void> {
  try {
    const { actionType, description, metadata } = buildActivityInfo(toolName, toolInput, toolResult);
    await supabase.from("activity_logs").insert({
      user_id: userId,
      action_type: actionType,
      tool_name: toolName,
      description,
      metadata,
      status,
    });
  } catch (err) {
    console.error("[Activity Log] Erreur:", err);
  }
}
