import { SUPABASE_URL, SUPABASE_ANON_KEY } from "../types.ts";

// ─── Tool 6: search_contacts ──────────────────────────────
export async function executeSearchContacts(params: { query: string }, userJwt: string): Promise<string> {
  const url = `${SUPABASE_URL}/functions/v1/contacts-api`;
  try {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "apikey": SUPABASE_ANON_KEY,
        Authorization: userJwt,
      },
      body: JSON.stringify({ action: "search", query: params.query }),
    });
    const result = await response.json();
    if (!response.ok) return `Erreur: ${result.error}`;
    if (!result.contacts || result.contacts.length === 0) {
      return `Aucun contact trouvé pour "${params.query}".`;
    }
    return result.contacts
      .map((c: { name: string; email?: string; phone?: string; company?: string }) =>
        `- ${c.name}${c.email ? ` | Email: ${c.email}` : ""}${c.phone ? ` | Tél: ${c.phone}` : ""}${c.company ? ` | Entreprise: ${c.company}` : ""}`
      )
      .join("\n");
  } catch (err) {
    return `Erreur recherche contacts: ${err instanceof Error ? err.message : String(err)}`;
  }
}

// ─── Tool 7: save_contact ─────────────────────────────────
export async function executeSaveContact(params: {
  name: string;
  email?: string;
  phone?: string;
  company?: string;
}, userJwt: string): Promise<string> {
  const url = `${SUPABASE_URL}/functions/v1/contacts-api`;
  try {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "apikey": SUPABASE_ANON_KEY,
        Authorization: userJwt,
      },
      body: JSON.stringify({ action: "create", ...params }),
    });
    const result = await response.json();
    if (!response.ok) return `Erreur: ${result.error}`;
    return `Contact "${params.name}" créé avec succès.${params.email ? ` Email: ${params.email}` : ""}`;
  } catch (err) {
    return `Erreur création contact: ${err instanceof Error ? err.message : String(err)}`;
  }
}

// ─── Tool 8: add_meeting_note ─────────────────────────────
export async function executeAddMeetingNote(params: {
  contact_name: string;
  title: string;
  summary: string;
}, userJwt: string): Promise<string> {
  const url = `${SUPABASE_URL}/functions/v1/contacts-api`;
  try {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "apikey": SUPABASE_ANON_KEY,
        Authorization: userJwt,
      },
      body: JSON.stringify({
        action: "add-meeting",
        contact_name: params.contact_name,
        title: params.title,
        summary: params.summary,
      }),
    });
    const result = await response.json();
    if (!response.ok) return `Erreur: ${result.error}`;
    return result.message || `Réunion "${params.title}" ajoutée au dossier de ${params.contact_name}.`;
  } catch (err) {
    return `Erreur ajout réunion: ${err instanceof Error ? err.message : String(err)}`;
  }
}

// ─── Tool 9: create_calendar_event (via calendar-api edge function) ──
export async function executeCreateCalendarEvent(params: {
  title: string;
  start_datetime: string;
  end_datetime?: string;
  description?: string;
  location?: string;
  attendees?: string[];
}, userJwt: string): Promise<string> {
  const url = `${SUPABASE_URL}/functions/v1/calendar-api`;
  console.log(`[create_calendar_event] Appel ${url} pour "${params.title}"`);

  try {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "apikey": SUPABASE_ANON_KEY,
        Authorization: userJwt,
      },
      body: JSON.stringify({
        action: "create-event",
        title: params.title,
        start: params.start_datetime,
        end: params.end_datetime,
        description: params.description,
        location: params.location,
        attendees: params.attendees,
      }),
    });

    const responseText = await response.text();
    console.log(`[create_calendar_event] HTTP ${response.status}: ${responseText.substring(0, 200)}`);

    let result;
    try {
      result = JSON.parse(responseText);
    } catch {
      return `Erreur: réponse non-JSON du serveur (HTTP ${response.status}): ${responseText.substring(0, 100)}`;
    }

    if (!response.ok) {
      return `Erreur lors de la création du rendez-vous: ${result.error || responseText.substring(0, 100)}`;
    }

    return result.message || `Rendez-vous "${params.title}" créé avec succès.`;
  } catch (err) {
    const errMsg = err instanceof Error ? err.message : String(err);
    console.error(`[create_calendar_event] Exception:`, errMsg);
    return `Erreur lors de la création du rendez-vous: ${errMsg}`;
  }
}

// ─── Tool 10: list_calendar_events (via calendar-api edge function) ──
export async function executeListCalendarEvents(params: {
  query?: string;
  date_start?: string;
  date_end?: string;
}, userJwt: string): Promise<string> {
  const url = `${SUPABASE_URL}/functions/v1/calendar-api`;
  try {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "apikey": SUPABASE_ANON_KEY,
        Authorization: userJwt,
      },
      body: JSON.stringify({
        action: "list-events",
        query: params.query,
        date_start: params.date_start,
        date_end: params.date_end,
      }),
    });
    const result = await response.json();
    if (!response.ok) return `Erreur: ${result.error}`;
    if (!result.events || result.events.length === 0) {
      return "Aucun rendez-vous trouvé.";
    }
    return result.events
      .map((e: { id: string; title: string; start_time: string; end_time: string; attendees: string[]; provider: string; location?: string }) => {
        const start = new Date(e.start_time).toLocaleString("fr-FR", { timeZone: "Europe/Paris" });
        const end = new Date(e.end_time).toLocaleString("fr-FR", { timeZone: "Europe/Paris" });
        const attendeesList = e.attendees && e.attendees.length > 0 ? ` | Participants: ${e.attendees.join(", ")}` : "";
        const loc = e.location ? ` | Lieu: ${e.location}` : "";
        return `- [${e.id}] "${e.title}" | ${start} → ${end}${loc}${attendeesList} (${e.provider})`;
      })
      .join("\n");
  } catch (err) {
    return `Erreur recherche rendez-vous: ${err instanceof Error ? err.message : String(err)}`;
  }
}

// ─── Tool 11: update_calendar_event (via calendar-api edge function) ──
export async function executeUpdateCalendarEvent(params: {
  event_id: string;
  title?: string;
  start_datetime?: string;
  end_datetime?: string;
  description?: string;
  location?: string;
  attendees?: string[];
}, userJwt: string): Promise<string> {
  const url = `${SUPABASE_URL}/functions/v1/calendar-api`;
  console.log(`[update_calendar_event] Appel ${url} pour event ${params.event_id}`);

  try {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "apikey": SUPABASE_ANON_KEY,
        Authorization: userJwt,
      },
      body: JSON.stringify({
        action: "update-event",
        event_id: params.event_id,
        title: params.title,
        start: params.start_datetime,
        end: params.end_datetime,
        description: params.description,
        location: params.location,
        attendees: params.attendees,
      }),
    });

    const responseText = await response.text();
    console.log(`[update_calendar_event] HTTP ${response.status}: ${responseText.substring(0, 200)}`);

    let result;
    try {
      result = JSON.parse(responseText);
    } catch {
      return `Erreur: réponse non-JSON (HTTP ${response.status}): ${responseText.substring(0, 100)}`;
    }

    if (!response.ok) {
      return `Erreur modification: ${result.error || responseText.substring(0, 100)}`;
    }

    return result.message || "Rendez-vous modifié avec succès.";
  } catch (err) {
    const errMsg = err instanceof Error ? err.message : String(err);
    console.error(`[update_calendar_event] Exception:`, errMsg);
    return `Erreur modification rendez-vous: ${errMsg}`;
  }
}
