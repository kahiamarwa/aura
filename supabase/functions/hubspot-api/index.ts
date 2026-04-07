import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { getUserFromRequest } from "../_shared/auth.ts";

// ============================================================
// AURA — Edge Function: hubspot-api
//
// HubSpot CRM integration — per-user OAuth 2.0 tokens
// Actions:
//   oauth-callback  : Exchange OAuth code for tokens
//   get-connection  : Check if user has HubSpot connected
//   disconnect      : Remove user's HubSpot integration
//   search-contacts : Search contacts by name, email, company
//   create-contact  : Create a new CRM contact
//   update-contact  : Update an existing contact by ID
//   delete-contact  : Delete a contact by ID
//   get-notes       : Get notes associated with a contact
//   create-note     : Create a note linked to a contact
//   update-note     : Update an existing note
//   search-deals    : Search deals by name, stage
//   create-deal     : Create a new deal
//   update-deal     : Update an existing deal
//   get-pipeline    : Get all pipeline stages
// ============================================================

const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const SUPABASE_SERVICE_ROLE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
const HUBSPOT_CLIENT_ID = Deno.env.get("HUBSPOT_CLIENT_ID") || "";
const HUBSPOT_CLIENT_SECRET = Deno.env.get("HUBSPOT_CLIENT_SECRET") || "";
const HUBSPOT_BASE_URL = "https://api.hubapi.com";

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

// ─── Generic HubSpot API helper (takes token as parameter) ──
// deno-lint-ignore no-explicit-any
async function hubspotFetch(
  token: string,
  path: string,
  method: "GET" | "POST" | "PATCH" | "DELETE" = "GET",
  body?: unknown
): Promise<{ ok: boolean; status: number; data: any }> {
  const options: RequestInit = {
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
  };
  if (body) options.body = JSON.stringify(body);

  const res = await fetch(`${HUBSPOT_BASE_URL}${path}`, options);
  const data = res.status === 204 ? {} : await res.json();
  return { ok: res.ok, status: res.status, data };
}

// ─── Translate HubSpot errors to French ─────────────────────
// deno-lint-ignore no-explicit-any
function hubspotError(status: number, data: any): string {
  if (status === 401) {
    return "Token HubSpot invalide ou expiré. Reconnectez votre compte HubSpot dans les paramètres.";
  }
  if (status === 429) {
    return "Limite d'appels HubSpot atteinte. Réessayez dans quelques secondes.";
  }
  if (status === 404) {
    return "Ressource non trouvée dans HubSpot.";
  }
  return data?.message || `Erreur HubSpot (${status})`;
}

// ─── HubSpot OAuth: Exchange auth code for tokens ────────
async function hubspotExchangeCode(
  code: string,
  redirectUri: string
): Promise<{ access_token: string; refresh_token: string; expires_in: number; portal_name: string; hub_id: string }> {
  const tokenRes = await fetch(`${HUBSPOT_BASE_URL}/oauth/v1/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "authorization_code",
      client_id: HUBSPOT_CLIENT_ID,
      client_secret: HUBSPOT_CLIENT_SECRET,
      redirect_uri: redirectUri,
      code,
    }),
  });

  if (!tokenRes.ok) {
    const err = await tokenRes.text();
    throw new Error(`HubSpot token exchange failed: ${err}`);
  }

  const tokens = await tokenRes.json();

  // Get portal info
  const infoRes = await fetch(`${HUBSPOT_BASE_URL}/account-info/v3/details`, {
    headers: { Authorization: `Bearer ${tokens.access_token}` },
  });
  let portal_name = "HubSpot";
  let hub_id = "";
  if (infoRes.ok) {
    const info = await infoRes.json();
    hub_id = String(info.portalId || "");
    portal_name = info.portalId ? `Portal ${info.portalId}` : "HubSpot";
  }

  return {
    access_token: tokens.access_token,
    refresh_token: tokens.refresh_token,
    expires_in: tokens.expires_in,
    portal_name,
    hub_id,
  };
}

// ─── HubSpot OAuth: Refresh access token ─────────────────
async function hubspotRefreshToken(
  refreshToken: string
): Promise<{ access_token: string; refresh_token: string; expires_in: number }> {
  const res = await fetch(`${HUBSPOT_BASE_URL}/oauth/v1/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "refresh_token",
      client_id: HUBSPOT_CLIENT_ID,
      client_secret: HUBSPOT_CLIENT_SECRET,
      refresh_token: refreshToken,
    }),
  });

  if (!res.ok) {
    const err = await res.text();
    throw new Error(`HubSpot token refresh failed: ${err}`);
  }

  return await res.json();
}

// ─── Get valid HubSpot token (refresh if expired) ────────
// deno-lint-ignore no-explicit-any
async function getUserHubspotToken(userId: string, supabase: any): Promise<string> {
  const { data: integration, error } = await supabase
    .from("hubspot_integrations")
    .select("*")
    .eq("user_id", userId)
    .single();

  if (error || !integration) {
    throw new Error("HUBSPOT_NOT_CONNECTED");
  }

  const now = new Date();
  const expiry = integration.token_expiry ? new Date(integration.token_expiry) : null;

  // If token is still valid (with 5min buffer), use it
  if (expiry && expiry.getTime() - now.getTime() > 5 * 60 * 1000) {
    return integration.access_token;
  }

  // If no refresh token (legacy Private App token), return as-is
  if (!integration.refresh_token) {
    return integration.access_token;
  }

  // Refresh the token
  console.log(`[hubspot-api] Refreshing HubSpot token for user ${userId}...`);

  const refreshed = await hubspotRefreshToken(integration.refresh_token);

  // Update in DB
  const newExpiry = new Date(Date.now() + refreshed.expires_in * 1000).toISOString();
  await supabase
    .from("hubspot_integrations")
    .update({
      access_token: refreshed.access_token,
      refresh_token: refreshed.refresh_token,
      token_expiry: newExpiry,
      updated_at: new Date().toISOString(),
    })
    .eq("id", integration.id);

  return refreshed.access_token;
}

// ═══════════════════════════════════════════════════════════════
// HANDLER PRINCIPAL
// ═══════════════════════════════════════════════════════════════

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") {
    return new Response(null, { status: 200, headers: corsHeaders });
  }

  try {
    // Verify user identity (optional — fallback to default user for now)
    let userId = "00000000-0000-0000-0000-000000000000";
    try {
      const authUser = await getUserFromRequest(req);
      userId = authUser.user_id;
    } catch (_authErr) {
      console.log("[hubspot-api] No valid JWT, using default user");
    }

    const { action, ...params } = await req.json();
    const supabase = createClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY);

    // ── ACTION: oauth-callback — exchange OAuth code for tokens ──
    if (action === "oauth-callback") {
      const { code, redirect_uri } = params;

      if (!code || !redirect_uri) {
        return jsonResponse({ error: "code et redirect_uri requis" }, 400);
      }

      const result = await hubspotExchangeCode(code, redirect_uri);
      const tokenExpiry = new Date(Date.now() + result.expires_in * 1000).toISOString();

      // Upsert: delete existing then insert
      await supabase
        .from("hubspot_integrations")
        .delete()
        .eq("user_id", userId);

      const { error: insertError } = await supabase
        .from("hubspot_integrations")
        .insert({
          user_id: userId,
          access_token: result.access_token,
          refresh_token: result.refresh_token,
          token_expiry: tokenExpiry,
          hub_id: result.hub_id,
          portal_name: result.portal_name,
        });

      if (insertError) {
        throw new Error(`DB insert failed: ${insertError.message}`);
      }

      console.log(`[hubspot-api] HubSpot connecté via OAuth pour user ${userId} (portal: ${result.portal_name})`);
      return jsonResponse({
        success: true,
        portal_name: result.portal_name,
        hub_id: result.hub_id,
      });
    }

    // ── ACTION: get-connection — check if user has HubSpot ──
    if (action === "get-connection") {
      const { data: integration } = await supabase
        .from("hubspot_integrations")
        .select("hub_id, portal_name, created_at")
        .eq("user_id", userId)
        .single();

      return jsonResponse({
        connection: integration || null,
      });
    }

    // ── ACTION: disconnect — remove HubSpot integration ──
    if (action === "disconnect") {
      await supabase
        .from("hubspot_integrations")
        .delete()
        .eq("user_id", userId);

      console.log(`[hubspot-api] HubSpot déconnecté pour user ${userId}`);
      return jsonResponse({ success: true });
    }

    // ── For all CRM actions, get the user's HubSpot token ──
    let hubspotToken: string;
    try {
      hubspotToken = await getUserHubspotToken(userId, supabase);
    } catch (tokenErr) {
      const errMsg = tokenErr instanceof Error ? tokenErr.message : "Erreur token";
      if (errMsg === "HUBSPOT_NOT_CONNECTED") {
        return jsonResponse(
          { error: "HUBSPOT_NOT_CONNECTED", message: "Aucun compte HubSpot connecté. Connectez votre CRM dans les paramètres." },
          400
        );
      }
      return jsonResponse({ error: errMsg }, 400);
    }

    // ── ACTION: search-contacts ──
    if (action === "search-contacts") {
      const { query, limit } = params;

      if (!query) {
        return jsonResponse({ error: "query est requis" }, 400);
      }

      const { ok, status, data } = await hubspotFetch(
        hubspotToken,
        "/crm/v3/objects/contacts/search",
        "POST",
        {
          filterGroups: [
            { filters: [{ propertyName: "firstname", operator: "CONTAINS_TOKEN", value: query }] },
            { filters: [{ propertyName: "lastname", operator: "CONTAINS_TOKEN", value: query }] },
            { filters: [{ propertyName: "email", operator: "CONTAINS_TOKEN", value: query }] },
            { filters: [{ propertyName: "company", operator: "CONTAINS_TOKEN", value: query }] },
          ],
          properties: ["firstname", "lastname", "email", "phone", "company", "jobtitle", "mobilephone", "address", "city", "zip", "country", "website", "hs_lead_status", "lifecyclestage"],
          limit: limit || 10,
        }
      );

      if (!ok) {
        return jsonResponse({ error: hubspotError(status, data) }, status);
      }

      // deno-lint-ignore no-explicit-any
      const contacts = (data.results || []).map((r: any) => ({
        id: r.id,
        firstname: r.properties?.firstname || "",
        lastname: r.properties?.lastname || "",
        email: r.properties?.email || "",
        phone: r.properties?.phone || "",
        company: r.properties?.company || "",
        jobtitle: r.properties?.jobtitle || "",
        mobilephone: r.properties?.mobilephone || "",
        address: r.properties?.address || "",
        city: r.properties?.city || "",
        zip: r.properties?.zip || "",
        country: r.properties?.country || "",
        website: r.properties?.website || "",
        hs_lead_status: r.properties?.hs_lead_status || "",
        lifecyclestage: r.properties?.lifecyclestage || "",
      }));

      console.log(`[hubspot-api] search-contacts "${query}" → ${contacts.length} résultat(s)`);
      return jsonResponse({ contacts });
    }

    // ── ACTION: create-contact ──
    if (action === "create-contact") {
      const { firstname, lastname, email, phone, company,
              jobtitle, mobilephone, address, city, zip, country, website,
              hs_lead_status, lifecyclestage } = params;

      if (!firstname) {
        return jsonResponse({ error: "firstname est requis" }, 400);
      }

      // deno-lint-ignore no-explicit-any
      const properties: any = { firstname };
      if (lastname) properties.lastname = lastname;
      if (email) properties.email = email;
      if (phone) properties.phone = phone;
      if (company) properties.company = company;
      if (jobtitle) properties.jobtitle = jobtitle;
      if (mobilephone) properties.mobilephone = mobilephone;
      if (address) properties.address = address;
      if (city) properties.city = city;
      if (zip) properties.zip = zip;
      if (country) properties.country = country;
      if (website) properties.website = website;
      if (hs_lead_status) properties.hs_lead_status = hs_lead_status;
      if (lifecyclestage) properties.lifecyclestage = lifecyclestage;

      const { ok, status, data } = await hubspotFetch(
        hubspotToken,
        "/crm/v3/objects/contacts",
        "POST",
        { properties }
      );

      if (!ok) {
        return jsonResponse({ error: hubspotError(status, data) }, status);
      }

      console.log(`[hubspot-api] Contact créé: ${data.id} (${firstname} ${lastname || ""})`);
      return jsonResponse({
        success: true,
        contact_id: data.id,
        message: `Contact "${firstname}${lastname ? " " + lastname : ""}" créé dans HubSpot.`,
      });
    }

    // ── ACTION: update-contact ──
    if (action === "update-contact") {
      const { contact_id, ...fields } = params;

      if (!contact_id) {
        return jsonResponse({ error: "contact_id est requis" }, 400);
      }

      const allowedFields = [
        "firstname", "lastname", "email", "phone", "company",
        "jobtitle", "mobilephone", "address", "city", "zip",
        "country", "website", "hs_lead_status", "lifecyclestage",
      ];
      // deno-lint-ignore no-explicit-any
      const properties: any = {};
      for (const key of allowedFields) {
        if (fields[key] !== undefined) {
          properties[key] = fields[key];
        }
      }

      const { ok, status, data } = await hubspotFetch(
        hubspotToken,
        `/crm/v3/objects/contacts/${contact_id}`,
        "PATCH",
        { properties }
      );

      if (!ok) {
        return jsonResponse({ error: hubspotError(status, data) }, status);
      }

      console.log(`[hubspot-api] Contact mis à jour: ${contact_id}`);
      return jsonResponse({
        success: true,
        message: `Contact HubSpot ${contact_id} mis à jour.`,
      });
    }

    // ── ACTION: delete-contact ──
    if (action === "delete-contact") {
      const { contact_id } = params;

      if (!contact_id) {
        return jsonResponse({ error: "contact_id est requis" }, 400);
      }

      const { ok, status, data } = await hubspotFetch(
        hubspotToken,
        `/crm/v3/objects/contacts/${contact_id}`,
        "DELETE"
      );

      if (!ok) {
        return jsonResponse({ error: hubspotError(status, data) }, status);
      }

      console.log(`[hubspot-api] Contact supprimé: ${contact_id}`);
      return jsonResponse({
        success: true,
        message: `Contact HubSpot ${contact_id} supprimé.`,
      });
    }

    // ── ACTION: get-notes ──
    if (action === "get-notes") {
      const { contact_id, limit } = params;

      if (!contact_id) {
        return jsonResponse({ error: "contact_id est requis" }, 400);
      }

      const assocResult = await hubspotFetch(
        hubspotToken,
        `/crm/v3/objects/contacts/${contact_id}/associations/notes`,
        "GET"
      );

      if (!assocResult.ok) {
        return jsonResponse({ error: hubspotError(assocResult.status, assocResult.data) }, assocResult.status);
      }

      const noteIds = (assocResult.data.results || [])
        // deno-lint-ignore no-explicit-any
        .map((a: any) => a.id)
        .slice(0, limit || 10);

      if (noteIds.length === 0) {
        console.log(`[hubspot-api] get-notes → aucune note pour contact ${contact_id}`);
        return jsonResponse({ notes: [] });
      }

      const notesResult = await hubspotFetch(
        hubspotToken,
        "/crm/v3/objects/notes/batch/read",
        "POST",
        {
          inputs: noteIds.map((id: string) => ({ id })),
          properties: ["hs_note_body", "hs_timestamp", "hs_lastmodifieddate"],
        }
      );

      if (!notesResult.ok) {
        return jsonResponse({ error: hubspotError(notesResult.status, notesResult.data) }, notesResult.status);
      }

      // deno-lint-ignore no-explicit-any
      const notes = (notesResult.data.results || []).map((n: any) => ({
        id: n.id,
        body: n.properties?.hs_note_body || "",
        timestamp: n.properties?.hs_timestamp || "",
        lastModified: n.properties?.hs_lastmodifieddate || "",
      }));

      console.log(`[hubspot-api] get-notes → ${notes.length} note(s) pour contact ${contact_id}`);
      return jsonResponse({ notes });
    }

    // ── ACTION: create-note ──
    if (action === "create-note") {
      const { contact_id, body } = params;

      if (!contact_id || !body) {
        return jsonResponse({ error: "contact_id et body sont requis" }, 400);
      }

      const { ok, status, data } = await hubspotFetch(
        hubspotToken,
        "/crm/v3/objects/notes",
        "POST",
        {
          properties: {
            hs_note_body: body,
            hs_timestamp: new Date().toISOString(),
          },
          associations: [
            {
              to: { id: contact_id },
              types: [
                {
                  associationCategory: "HUBSPOT_DEFINED",
                  associationTypeId: 202,
                },
              ],
            },
          ],
        }
      );

      if (!ok) {
        return jsonResponse({ error: hubspotError(status, data) }, status);
      }

      console.log(`[hubspot-api] Note créée: ${data.id} pour contact ${contact_id}`);
      return jsonResponse({
        success: true,
        note_id: data.id,
        message: `Note ajoutée au contact ${contact_id}.`,
      });
    }

    // ── ACTION: update-note ──
    if (action === "update-note") {
      const { note_id, body } = params;

      if (!note_id || !body) {
        return jsonResponse({ error: "note_id et body sont requis" }, 400);
      }

      const { ok, status, data } = await hubspotFetch(
        hubspotToken,
        `/crm/v3/objects/notes/${note_id}`,
        "PATCH",
        {
          properties: {
            hs_note_body: body,
          },
        }
      );

      if (!ok) {
        return jsonResponse({ error: hubspotError(status, data) }, status);
      }

      console.log(`[hubspot-api] Note mise à jour: ${note_id}`);
      return jsonResponse({
        success: true,
        message: `Note ${note_id} mise à jour.`,
      });
    }

    // ── ACTION: search-deals ──
    if (action === "search-deals") {
      const { query, dealstage, limit } = params;

      // deno-lint-ignore no-explicit-any
      const filterGroups: any[] = [];

      if (query) {
        filterGroups.push({
          filters: [{ propertyName: "dealname", operator: "CONTAINS_TOKEN", value: query }],
        });
      }

      if (dealstage) {
        filterGroups.push({
          filters: [{ propertyName: "dealstage", operator: "EQ", value: dealstage }],
        });
      }

      const { ok, status, data } = await hubspotFetch(
        hubspotToken,
        "/crm/v3/objects/deals/search",
        "POST",
        {
          ...(filterGroups.length > 0 ? { filterGroups } : {}),
          properties: ["dealname", "amount", "dealstage", "pipeline", "closedate", "createdate"],
          limit: limit || 10,
          sorts: [{ propertyName: "createdate", direction: "DESCENDING" }],
        }
      );

      if (!ok) {
        return jsonResponse({ error: hubspotError(status, data) }, status);
      }

      // deno-lint-ignore no-explicit-any
      const deals = (data.results || []).map((r: any) => ({
        id: r.id,
        dealname: r.properties?.dealname || "",
        amount: r.properties?.amount || "",
        dealstage: r.properties?.dealstage || "",
        pipeline: r.properties?.pipeline || "",
        closedate: r.properties?.closedate || "",
      }));

      console.log(`[hubspot-api] search-deals → ${deals.length} résultat(s)`);
      return jsonResponse({ deals });
    }

    // ── ACTION: create-deal ──
    if (action === "create-deal") {
      const { dealname, amount, dealstage, pipeline, closedate } = params;

      if (!dealname) {
        return jsonResponse({ error: "dealname est requis" }, 400);
      }

      // deno-lint-ignore no-explicit-any
      const properties: any = { dealname };
      if (amount) properties.amount = amount;
      if (dealstage) properties.dealstage = dealstage;
      if (pipeline) properties.pipeline = pipeline;
      if (closedate) properties.closedate = closedate;

      const { ok, status, data } = await hubspotFetch(
        hubspotToken,
        "/crm/v3/objects/deals",
        "POST",
        { properties }
      );

      if (!ok) {
        return jsonResponse({ error: hubspotError(status, data) }, status);
      }

      console.log(`[hubspot-api] Deal créé: ${data.id} (${dealname})`);
      return jsonResponse({
        success: true,
        deal_id: data.id,
        message: `Deal "${dealname}" créé dans HubSpot.`,
      });
    }

    // ── ACTION: update-deal ──
    if (action === "update-deal") {
      const { deal_id, dealname, amount, dealstage, closedate } = params;

      if (!deal_id) {
        return jsonResponse({ error: "deal_id est requis" }, 400);
      }

      // deno-lint-ignore no-explicit-any
      const properties: any = {};
      if (dealname) properties.dealname = dealname;
      if (amount) properties.amount = amount;
      if (dealstage) properties.dealstage = dealstage;
      if (closedate) properties.closedate = closedate;

      const { ok, status, data } = await hubspotFetch(
        hubspotToken,
        `/crm/v3/objects/deals/${deal_id}`,
        "PATCH",
        { properties }
      );

      if (!ok) {
        return jsonResponse({ error: hubspotError(status, data) }, status);
      }

      console.log(`[hubspot-api] Deal mis à jour: ${deal_id}`);
      return jsonResponse({
        success: true,
        message: `Deal HubSpot "${deal_id}" mis à jour.`,
      });
    }

    // ── ACTION: get-pipeline ──
    if (action === "get-pipeline") {
      const { ok, status, data } = await hubspotFetch(
        hubspotToken,
        "/crm/v3/pipelines/deals",
        "GET"
      );

      if (!ok) {
        return jsonResponse({ error: hubspotError(status, data) }, status);
      }

      // deno-lint-ignore no-explicit-any
      const pipelines = (data.results || []).map((p: any) => ({
        id: p.id,
        label: p.label,
        // deno-lint-ignore no-explicit-any
        stages: (p.stages || [])
          .sort((a: any, b: any) => a.displayOrder - b.displayOrder)
          .map((s: any) => ({
            id: s.id,
            label: s.label,
          })),
      }));

      console.log(`[hubspot-api] get-pipeline → ${pipelines.length} pipeline(s)`);
      return jsonResponse({ pipelines });
    }

    return jsonResponse({ error: `Action inconnue: ${action}` }, 400);
  } catch (err) {
    console.error("[hubspot-api] Error:", err);
    return jsonResponse(
      { error: err instanceof Error ? err.message : "Erreur inconnue" },
      500
    );
  }
});
