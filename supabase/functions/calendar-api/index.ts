import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { getUserFromRequest } from "../_shared/auth.ts";

// ============================================================
// AURA — Edge Function: calendar-api
//
// Handles:
//   POST /calendar-api { action: "create-event", title, start, end?, description?, location?, attendees? }
//   POST /calendar-api { action: "list-events", query?, date_start?, date_end? }
//   POST /calendar-api { action: "update-event", event_id, title?, start?, end?, description?, location?, attendees? }
// ============================================================

const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const SUPABASE_SERVICE_ROLE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;

const GOOGLE_CLIENT_ID = Deno.env.get("GOOGLE_CLIENT_ID") || "";
const GOOGLE_CLIENT_SECRET = Deno.env.get("GOOGLE_CLIENT_SECRET") || "";
const MICROSOFT_CLIENT_ID = Deno.env.get("MICROSOFT_CLIENT_ID") || "";
const MICROSOFT_CLIENT_SECRET = Deno.env.get("MICROSOFT_CLIENT_SECRET") || "";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
  "Access-Control-Allow-Headers":
    "Content-Type, Authorization, X-Client-Info, Apikey",
};

function jsonResponse(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { ...corsHeaders, "Content-Type": "application/json" },
  });
}

// ─── Token refresh (same logic as send-email) ────────────────

async function gmailRefreshToken(
  refreshToken: string
): Promise<{ access_token: string; expires_in: number }> {
  const res = await fetch("https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      refresh_token: refreshToken,
      client_id: GOOGLE_CLIENT_ID,
      client_secret: GOOGLE_CLIENT_SECRET,
      grant_type: "refresh_token",
    }),
  });
  if (!res.ok) {
    const err = await res.text();
    throw new Error(`Google token refresh failed: ${err}`);
  }
  return await res.json();
}

async function outlookRefreshToken(
  refreshToken: string
): Promise<{ access_token: string; expires_in: number }> {
  const res = await fetch(
    "https://login.microsoftonline.com/common/oauth2/v2.0/token",
    {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        refresh_token: refreshToken,
        client_id: MICROSOFT_CLIENT_ID,
        client_secret: MICROSOFT_CLIENT_SECRET,
        grant_type: "refresh_token",
      }),
    }
  );
  if (!res.ok) {
    const err = await res.text();
    throw new Error(`Microsoft token refresh failed: ${err}`);
  }
  return await res.json();
}

// deno-lint-ignore no-explicit-any
async function getValidToken(integration: any, supabase: any): Promise<string> {
  const now = new Date();
  const expiry = integration.token_expiry
    ? new Date(integration.token_expiry)
    : null;

  if (expiry && expiry.getTime() - now.getTime() > 5 * 60 * 1000) {
    return integration.access_token;
  }

  if (!integration.refresh_token) {
    throw new Error(
      "Token expiré et pas de refresh token disponible. Reconnectez le compte."
    );
  }

  console.log(`[calendar-api] Refreshing ${integration.provider} token...`);

  let refreshed: { access_token: string; expires_in: number };
  if (integration.provider === "gmail") {
    refreshed = await gmailRefreshToken(integration.refresh_token);
  } else {
    refreshed = await outlookRefreshToken(integration.refresh_token);
  }

  const newExpiry = new Date(
    Date.now() + refreshed.expires_in * 1000
  ).toISOString();
  await supabase
    .from("email_integrations")
    .update({
      access_token: refreshed.access_token,
      token_expiry: newExpiry,
      updated_at: new Date().toISOString(),
    })
    .eq("id", integration.id);

  return refreshed.access_token;
}

// ─── Google Calendar: Create event ───────────────────────────

interface CalendarEventParams {
  title: string;
  start: string;
  end: string;
  description?: string;
  location?: string;
  attendees?: string[];
}

async function createGoogleCalendarEvent(
  accessToken: string,
  params: CalendarEventParams
): Promise<{ event_id: string; link: string }> {
  // deno-lint-ignore no-explicit-any
  const event: any = {
    summary: params.title,
    start: { dateTime: params.start, timeZone: "Europe/Paris" },
    end: { dateTime: params.end, timeZone: "Europe/Paris" },
  };

  if (params.description) event.description = params.description;
  if (params.location) event.location = params.location;
  if (params.attendees && params.attendees.length > 0) {
    event.attendees = params.attendees.map((email: string) => ({ email }));
  }

  const res = await fetch(
    "https://www.googleapis.com/calendar/v3/calendars/primary/events",
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(event),
    }
  );

  if (!res.ok) {
    const err = await res.text();
    throw new Error(`Google Calendar create event failed: ${err}`);
  }

  const result = await res.json();
  return {
    event_id: result.id,
    link: result.htmlLink || "",
  };
}

// ─── Outlook Calendar: Create event ──────────────────────────

async function createOutlookCalendarEvent(
  accessToken: string,
  params: CalendarEventParams
): Promise<{ event_id: string; link: string }> {
  // deno-lint-ignore no-explicit-any
  const event: any = {
    subject: params.title,
    start: { dateTime: params.start, timeZone: "Europe/Paris" },
    end: { dateTime: params.end, timeZone: "Europe/Paris" },
  };

  if (params.description) {
    event.body = { contentType: "Text", content: params.description };
  }
  if (params.location) {
    event.location = { displayName: params.location };
  }
  if (params.attendees && params.attendees.length > 0) {
    event.attendees = params.attendees.map((email: string) => ({
      emailAddress: { address: email },
      type: "required",
    }));
  }

  const res = await fetch("https://graph.microsoft.com/v1.0/me/events", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(event),
  });

  if (!res.ok) {
    const err = await res.text();
    throw new Error(`Outlook Calendar create event failed: ${err}`);
  }

  const result = await res.json();
  return {
    event_id: result.id,
    link: result.webLink || "",
  };
}

// ─── Google Calendar: Update event ───────────────────────────

async function updateGoogleCalendarEvent(
  accessToken: string,
  providerEventId: string,
  params: Partial<CalendarEventParams>
): Promise<void> {
  // deno-lint-ignore no-explicit-any
  const patch: any = {};
  if (params.title) patch.summary = params.title;
  if (params.start) patch.start = { dateTime: params.start, timeZone: "Europe/Paris" };
  if (params.end) patch.end = { dateTime: params.end, timeZone: "Europe/Paris" };
  if (params.description !== undefined) patch.description = params.description;
  if (params.location !== undefined) patch.location = params.location;
  if (params.attendees) {
    patch.attendees = params.attendees.map((email: string) => ({ email }));
  }

  const res = await fetch(
    `https://www.googleapis.com/calendar/v3/calendars/primary/events/${providerEventId}`,
    {
      method: "PATCH",
      headers: {
        Authorization: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(patch),
    }
  );

  if (!res.ok) {
    const err = await res.text();
    throw new Error(`Google Calendar update event failed: ${err}`);
  }
}

// ─── Outlook Calendar: Update event ──────────────────────────

async function updateOutlookCalendarEvent(
  accessToken: string,
  providerEventId: string,
  params: Partial<CalendarEventParams>
): Promise<void> {
  // deno-lint-ignore no-explicit-any
  const patch: any = {};
  if (params.title) patch.subject = params.title;
  if (params.start) patch.start = { dateTime: params.start, timeZone: "Europe/Paris" };
  if (params.end) patch.end = { dateTime: params.end, timeZone: "Europe/Paris" };
  if (params.description !== undefined) {
    patch.body = { contentType: "Text", content: params.description };
  }
  if (params.location !== undefined) {
    patch.location = { displayName: params.location };
  }
  if (params.attendees) {
    patch.attendees = params.attendees.map((email: string) => ({
      emailAddress: { address: email },
      type: "required",
    }));
  }

  const res = await fetch(
    `https://graph.microsoft.com/v1.0/me/events/${providerEventId}`,
    {
      method: "PATCH",
      headers: {
        Authorization: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(patch),
    }
  );

  if (!res.ok) {
    const err = await res.text();
    throw new Error(`Outlook Calendar update event failed: ${err}`);
  }
}

// ═══════════════════════════════════════════════════════════════
// HANDLER PRINCIPAL
// ═══════════════════════════════════════════════════════════════

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") {
    return new Response(null, { status: 200, headers: corsHeaders });
  }

  try {
    let userId: string;
    try {
      const authUser = await getUserFromRequest(req);
      userId = authUser.user_id;
    } catch (authErr) {
      return jsonResponse({ error: authErr instanceof Error ? authErr.message : "Non autorisé" }, 401);
    }

    const { action, ...params } = await req.json();
    const supabase = createClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY);

    // ── ACTION: Create calendar event ──
    if (action === "create-event") {
      const { title, start, end, description, location, attendees } = params;

      if (!title || !start) {
        return jsonResponse(
          { error: "title et start sont requis" },
          400
        );
      }

      // Default end = start + 1 hour
      const eventEnd =
        end || new Date(new Date(start).getTime() + 60 * 60 * 1000).toISOString();

      // Find a connected integration
      const { data: integration, error } = await supabase
        .from("email_integrations")
        .select("*")
        .eq("user_id", userId)
        .limit(1)
        .single();

      if (error || !integration) {
        return jsonResponse(
          {
            error:
              "Aucun compte connecté. Connectez Gmail ou Outlook dans les paramètres.",
          },
          400
        );
      }

      const accessToken = await getValidToken(integration, supabase);

      const eventParams: CalendarEventParams = {
        title,
        start,
        end: eventEnd,
        description,
        location,
        attendees,
      };

      let result: { event_id: string; link: string };

      if (integration.provider === "gmail") {
        result = await createGoogleCalendarEvent(accessToken, eventParams);
      } else {
        result = await createOutlookCalendarEvent(accessToken, eventParams);
      }

      console.log(
        `[calendar-api] Event created via ${integration.provider}: ${result.event_id}`
      );

      // Save event in database
      const { error: dbError } = await supabase
        .from("calendar_events")
        .insert({
          user_id: userId,
          provider: integration.provider,
          provider_event_id: result.event_id,
          title,
          description: description || null,
          location: location || null,
          start_time: start,
          end_time: eventEnd,
          attendees: attendees || [],
          event_link: result.link || null,
        });

      if (dbError) {
        console.warn(`[calendar-api] DB save failed (event was created): ${dbError.message}`);
      } else {
        console.log(`[calendar-api] Event saved in database`);
      }

      return jsonResponse({
        success: true,
        event_id: result.event_id,
        link: result.link,
        provider: integration.provider,
        message: `Rendez-vous "${title}" créé dans ${integration.provider === "gmail" ? "Google Calendar" : "Outlook Calendar"}.`,
      });
    }

    // ── ACTION: List calendar events ──
    if (action === "list-events") {
      const { query, date_start, date_end } = params;

      let dbQuery = supabase
        .from("calendar_events")
        .select("id, provider, provider_event_id, title, description, location, start_time, end_time, attendees, event_link, created_at")
        .eq("user_id", userId)
        .order("start_time", { ascending: false })
        .limit(10);

      if (query) {
        dbQuery = dbQuery.ilike("title", `%${query}%`);
      }
      if (date_start) {
        dbQuery = dbQuery.gte("start_time", date_start);
      }
      if (date_end) {
        dbQuery = dbQuery.lte("start_time", date_end);
      }

      const { data, error: dbError } = await dbQuery;

      if (dbError) {
        return jsonResponse({ error: dbError.message }, 500);
      }

      return jsonResponse({ events: data || [] });
    }

    // ── ACTION: Update calendar event ──
    if (action === "update-event") {
      const { event_id, title, start, end, description, location, attendees } = params;

      if (!event_id) {
        return jsonResponse({ error: "event_id est requis" }, 400);
      }

      // Get the event from DB
      const { data: calEvent, error: fetchError } = await supabase
        .from("calendar_events")
        .select("*")
        .eq("id", event_id)
        .eq("user_id", userId)
        .single();

      if (fetchError || !calEvent) {
        return jsonResponse({ error: "Rendez-vous introuvable" }, 404);
      }

      // Get the integration for this provider
      const { data: integration, error: intError } = await supabase
        .from("email_integrations")
        .select("*")
        .eq("provider", calEvent.provider)
        .eq("user_id", userId)
        .limit(1)
        .single();

      if (intError || !integration) {
        return jsonResponse({ error: "Aucun compte connecté pour ce provider." }, 400);
      }

      const accessToken = await getValidToken(integration, supabase);

      const updateParams: Partial<CalendarEventParams> = {};
      if (title) updateParams.title = title;
      if (start) updateParams.start = start;
      if (end) updateParams.end = end;
      if (description !== undefined) updateParams.description = description;
      if (location !== undefined) updateParams.location = location;
      if (attendees) updateParams.attendees = attendees;

      // Update on provider
      if (calEvent.provider === "gmail") {
        await updateGoogleCalendarEvent(accessToken, calEvent.provider_event_id, updateParams);
      } else {
        await updateOutlookCalendarEvent(accessToken, calEvent.provider_event_id, updateParams);
      }

      // Update in DB
      // deno-lint-ignore no-explicit-any
      const dbUpdate: any = { updated_at: new Date().toISOString() };
      if (title) dbUpdate.title = title;
      if (start) dbUpdate.start_time = start;
      if (end) dbUpdate.end_time = end;
      if (description !== undefined) dbUpdate.description = description;
      if (location !== undefined) dbUpdate.location = location;
      if (attendees) dbUpdate.attendees = attendees;

      await supabase
        .from("calendar_events")
        .update(dbUpdate)
        .eq("id", event_id);

      console.log(`[calendar-api] Event updated: ${event_id}`);

      return jsonResponse({
        success: true,
        message: `Rendez-vous "${calEvent.title}" modifié avec succès.`,
      });
    }

    return jsonResponse({ error: `Action inconnue: ${action}` }, 400);
  } catch (err) {
    console.error("[calendar-api] Error:", err);
    return jsonResponse(
      { error: err instanceof Error ? err.message : "Erreur inconnue" },
      500
    );
  }
});
