import { BACKEND_URL, EDGE_URL } from "./constants";

export interface TranscriptionSegment {
  text: string;
  timestamp: string; // ISO string
  is_partial: boolean;
}

export async function fetchSttToken(): Promise<string> {
  const res = await fetch(`${BACKEND_URL}/api/stt-token`);
  if (!res.ok) throw new Error(`STT token error: ${res.status}`);
  const data = await res.json();
  return data.token;
}

export interface ChatAttachment {
  file_path: string;
  file_name: string;
  type: string;
}

export interface ChatResult {
  text: string;
  audioBlob: Blob | null;
  attachments?: ChatAttachment[];
}

/**
 * Step 1: Send command + context to agent → get text response.
 * Step 2: Send text to TTS → get audio blob.
 * If TTS fails, we still have the text — no data is lost.
 */
export async function sendChat(
  command: string,
  context: TranscriptionSegment[],
  accessToken?: string,
  conversationId?: string | null
): Promise<ChatResult> {
  // --- Step 1: Get text from agent ---
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (accessToken) {
    headers["Authorization"] = `Bearer ${accessToken}`;
  }

  // deno-lint-ignore no-explicit-any
  const body: Record<string, any> = {
    command,
    context,
    user_timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
  };
  if (conversationId) {
    body.conversation_id = conversationId;
  }

  const chatRes = await fetch(`${BACKEND_URL}/api/chat`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });

  if (!chatRes.ok) {
    const detail = await chatRes.text().catch(() => "");
    console.error("[API] Chat error:", chatRes.status, detail);
    throw new Error(`Agent error: ${chatRes.status}`);
  }

  const chatData = await chatRes.json();
  const text = chatData.text;
  const attachments = chatData.attachments;

  // --- Step 2: Get audio from TTS (non-blocking — text is already saved) ---
  let audioBlob: Blob | null = null;
  try {
    const ttsRes = await fetch(`${BACKEND_URL}/api/tts`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });

    if (ttsRes.ok) {
      audioBlob = await ttsRes.blob();
    } else {
      console.warn("[API] TTS failed:", ttsRes.status);
    }
  } catch (e) {
    console.warn("[API] TTS error, continuing without audio:", e);
  }

  return { text, audioBlob, attachments };
}

/**
 * Streaming version of sendChat — text appears progressively via SSE.
 * Callbacks fire as events arrive; final ChatResult returned when done.
 */
export async function sendChatStream(
  command: string,
  context: TranscriptionSegment[],
  accessToken?: string,
  conversationId?: string | null,
  onTextDelta?: (delta: string) => void,
  onToolStart?: (name: string) => void,
  onToolResult?: (name: string, status: string) => void,
): Promise<ChatResult> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    "Accept": "text/event-stream",
  };
  if (accessToken) {
    headers["Authorization"] = `Bearer ${accessToken}`;
  }

  // deno-lint-ignore no-explicit-any
  const body: Record<string, any> = {
    command,
    context,
    user_timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
  };
  if (conversationId) {
    body.conversation_id = conversationId;
  }

  const response = await fetch(`${BACKEND_URL}/api/chat`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    console.error("[API] Chat stream error:", response.status, detail);
    throw new Error(`Agent error: ${response.status}`);
  }

  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  let fullText = "";
  let attachments: ChatAttachment[] | undefined;
  let buffer = "";
  let currentEvent = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const lines = buffer.split("\n");
    buffer = lines.pop()!; // keep incomplete last line

    for (const line of lines) {
      if (line.startsWith("event: ")) {
        currentEvent = line.slice(7).trim();
      } else if (line.startsWith("data: ")) {
        try {
          const data = JSON.parse(line.slice(6));
          switch (currentEvent) {
            case "text_delta":
              fullText += data.delta;
              onTextDelta?.(data.delta);
              break;
            case "tool_start":
              onToolStart?.(data.name);
              break;
            case "tool_result":
              onToolResult?.(data.name, data.status);
              break;
            case "done":
              fullText = data.response || fullText;
              attachments = data.attachments;
              break;
            case "error":
              console.error("[API] Stream error event:", data);
              break;
          }
        } catch {
          // Skip malformed JSON lines
        }
      }
    }
  }

  // TTS after streaming complete
  let audioBlob: Blob | null = null;
  try {
    const ttsRes = await fetch(`${BACKEND_URL}/api/tts`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: fullText }),
    });
    if (ttsRes.ok) {
      audioBlob = await ttsRes.blob();
    } else {
      console.warn("[API] TTS failed:", ttsRes.status);
    }
  } catch (e) {
    console.warn("[API] TTS error, continuing without audio:", e);
  }

  return { text: fullText, audioBlob, attachments };
}

// Fetch summaries
export async function fetchSummaries(accessToken: string, limit = 20, offset = 0) {
  const res = await fetch(`${BACKEND_URL}/api/summaries?limit=${limit}&offset=${offset}`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!res.ok) throw new Error("Failed to fetch summaries");
  return res.json();
}

// Fetch contacts
export async function fetchContacts(accessToken: string, search = "") {
  const url = search
    ? `${BACKEND_URL}/api/contacts?search=${encodeURIComponent(search)}`
    : `${BACKEND_URL}/api/contacts`;
  const res = await fetch(url, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!res.ok) throw new Error("Failed to fetch contacts");
  return res.json();
}

// Create contact
export async function createContact(accessToken: string, contact: {
  name: string; email: string;
  phone: string; company: string; notes: string;
}) {
  const res = await fetch(`${BACKEND_URL}/api/contacts`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(contact),
  });
  if (!res.ok) throw new Error("Failed to create contact");
  return res.json();
}

// Delete contact
export async function deleteContact(accessToken: string, contactId: string) {
  const res = await fetch(`${BACKEND_URL}/api/contacts/${contactId}`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!res.ok) throw new Error("Failed to delete contact");
  return res.json();
}

// Fetch activity
export async function fetchActivity(accessToken: string, limit = 50) {
  const res = await fetch(`${BACKEND_URL}/api/activity?limit=${limit}`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!res.ok) throw new Error("Failed to fetch activity");
  return res.json();
}

// Fetch discussions
export async function fetchDiscussions(accessToken: string, search = "", limit = 20) {
  const url = `${BACKEND_URL}/api/discussions?limit=${limit}${search ? `&search=${encodeURIComponent(search)}` : ""}`;
  const res = await fetch(url, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!res.ok) throw new Error("Failed to fetch discussions");
  return res.json();
}

// Fetch discussion detail (session + segments)
export async function fetchDiscussionDetail(accessToken: string, id: string) {
  const res = await fetch(`${BACKEND_URL}/api/discussions/${id}`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!res.ok) throw new Error("Failed to fetch discussion detail");
  return res.json();
}

// Delete discussion
export async function deleteDiscussion(accessToken: string, id: string) {
  const res = await fetch(`${BACKEND_URL}/api/discussions/${id}`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!res.ok) throw new Error("Failed to delete discussion");
  return res.json();
}

// Delete summary
export async function deleteSummary(accessToken: string, id: string) {
  const res = await fetch(`${BACKEND_URL}/api/summaries/${id}`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!res.ok) throw new Error("Failed to delete summary");
  return res.json();
}

// Update contact
export async function updateContact(accessToken: string, contactId: string, contact: {
  name?: string; email?: string; phone?: string; company?: string; notes?: string;
}) {
  const res = await fetch(`${BACKEND_URL}/api/contacts/${contactId}`, {
    method: "PUT",
    headers: {
      Authorization: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(contact),
  });
  if (!res.ok) throw new Error("Failed to update contact");
  return res.json();
}

// Fetch user settings
export async function fetchSettings(accessToken: string) {
  const res = await fetch(`${BACKEND_URL}/api/settings`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!res.ok) throw new Error("Failed to fetch settings");
  const json = await res.json();
  return json.settings ?? json;
}

// Update user settings
export async function updateSettings(accessToken: string, settings: Record<string, unknown>) {
  const res = await fetch(`${BACKEND_URL}/api/settings`, {
    method: "PUT",
    headers: {
      Authorization: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(settings),
  });
  if (!res.ok) throw new Error("Failed to update settings");
  return res.json();
}

// Create conversation
export async function createConversation(accessToken: string, data: {
  title?: string; messages?: { role: string; content: string; attachments?: { file_path: string; file_name: string; type: string }[] }[];
}) {
  const res = await fetch(`${BACKEND_URL}/api/conversations`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error("Failed to create conversation");
  return res.json();
}

// List conversations
export async function listConversations(accessToken: string, limit = 20) {
  const res = await fetch(`${BACKEND_URL}/api/conversations?limit=${limit}`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!res.ok) throw new Error("Failed to list conversations");
  return res.json();
}

// Get conversation detail with messages
export async function fetchConversationDetail(accessToken: string, id: string) {
  const res = await fetch(`${BACKEND_URL}/api/conversations/${id}`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!res.ok) throw new Error("Failed to fetch conversation");
  return res.json();
}

// Add message to conversation
export async function addConversationMessage(
  accessToken: string, conversationId: string, role: string, content: string,
  attachments?: { file_path: string; file_name: string; type: string }[],
) {
  const payload: Record<string, unknown> = { role, content };
  if (attachments && attachments.length > 0) {
    payload.attachments = attachments;
  }
  const res = await fetch(`${BACKEND_URL}/api/conversations/${conversationId}/messages`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error("Failed to add message");
  return res.json();
}

// ── Speaker verification ───────────────────────────────────────────

export interface SpeakerEnrollment {
  id: string;
  speaker_name: string;
  created_at: string;
  updated_at: string;
}

// List enrolled speakers
export async function listSpeakers(accessToken: string): Promise<SpeakerEnrollment[]> {
  const res = await fetch(`${EDGE_URL}/api/speakers`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!res.ok) throw new Error("Failed to list speakers");
  const data = await res.json();
  return data.speakers ?? [];
}

// Enroll a speaker from audio file
export async function enrollSpeaker(
  accessToken: string,
  speakerName: string,
  audioBlob: Blob,
): Promise<{ success: boolean; enrollment_id: string; reference_duration_s: number }> {
  const formData = new FormData();
  formData.append("speaker_name", speakerName);
  formData.append("audio", audioBlob, "enrollment.wav");

  const res = await fetch(`${EDGE_URL}/api/speakers/enroll`, {
    method: "POST",
    headers: { Authorization: `Bearer ${accessToken}` },
    body: formData,
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(detail || `Enrollment failed: ${res.status}`);
  }
  return res.json();
}

// Delete enrolled speaker
export async function deleteSpeaker(accessToken: string, enrollmentId: string) {
  const res = await fetch(`${EDGE_URL}/api/speakers/${enrollmentId}`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!res.ok) throw new Error("Failed to delete speaker");
  return res.json();
}

// Verify speaker from audio
export async function verifySpeaker(
  accessToken: string,
  audioBase64: string,
  sampleRate: number,
): Promise<{ verified: boolean; speaker_name: string | null; score: number; threshold: number; reason?: string }> {
  const res = await fetch(`${EDGE_URL}/api/speakers/verify`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ audio_base64: audioBase64, sample_rate: sampleRate }),
  });
  if (!res.ok) throw new Error("Speaker verification failed");
  return res.json();
}

// Classify intent (directed speech detection)
export async function classifyIntent(
  accessToken: string,
  text: string,
  context: string[],
): Promise<{ directed: boolean; confidence: number; method: string }> {
  try {
    const res = await fetch(`${BACKEND_URL}/api/classify-intent`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ text, context }),
    });
    if (!res.ok) return { directed: true, confidence: 0.5, method: "fallback" };
    return res.json();
  } catch {
    return { directed: true, confidence: 0.5, method: "fallback" };
  }
}

// Delete conversation
export async function deleteConversation(accessToken: string, id: string) {
  const res = await fetch(`${BACKEND_URL}/api/conversations/${id}`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!res.ok) throw new Error("Failed to delete conversation");
  return res.json();
}
