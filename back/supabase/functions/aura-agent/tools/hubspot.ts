import { SUPABASE_URL, SUPABASE_ANON_KEY } from "../types.ts";

// ─── Tool 14: hubspot_search_contacts (via hubspot-api edge function) ──
export async function executeHubspotSearchContacts(params: {
  query: string;
}, userJwt: string): Promise<string> {
  const url = `${SUPABASE_URL}/functions/v1/hubspot-api`;
  console.log(`[hubspot_search_contacts] Recherche "${params.query}"`);

  try {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "apikey": SUPABASE_ANON_KEY,
        Authorization: userJwt,
      },
      body: JSON.stringify({ action: "search-contacts", query: params.query }),
    });

    const responseText = await response.text();
    console.log(`[hubspot_search_contacts] HTTP ${response.status}: ${responseText.substring(0, 200)}`);

    let result;
    try { result = JSON.parse(responseText); } catch {
      return `Erreur: réponse non-JSON (HTTP ${response.status}): ${responseText.substring(0, 100)}`;
    }

    if (!response.ok) {
      return `Erreur: ${result.error || responseText.substring(0, 100)}`;
    }

    if (!result.contacts || result.contacts.length === 0) {
      return `Aucun contact HubSpot trouvé pour "${params.query}".`;
    }

    // deno-lint-ignore no-explicit-any
    return result.contacts
      .map((c: any) => {
        let line = `- [ID:${c.id}] ${c.firstname} ${c.lastname}`;
        if (c.email) line += ` | Email: ${c.email}`;
        if (c.phone) line += ` | Tél: ${c.phone}`;
        if (c.mobilephone) line += ` | Mobile: ${c.mobilephone}`;
        if (c.company) line += ` | Entreprise: ${c.company}`;
        if (c.jobtitle) line += ` | Poste: ${c.jobtitle}`;
        if (c.address || c.city || c.zip || c.country) {
          line += ` | Adresse: ${[c.address, c.zip, c.city, c.country].filter(Boolean).join(", ")}`;
        }
        if (c.website) line += ` | Web: ${c.website}`;
        if (c.hs_lead_status) line += ` | Statut lead: ${c.hs_lead_status}`;
        if (c.lifecyclestage) line += ` | Cycle: ${c.lifecyclestage}`;
        return line;
      })
      .join("\n");
  } catch (err) {
    const errMsg = err instanceof Error ? err.message : String(err);
    console.error(`[hubspot_search_contacts] Exception:`, errMsg);
    return `Erreur recherche HubSpot contacts: ${errMsg}`;
  }
}

// ─── Tool 14: hubspot_create_contact (via hubspot-api edge function) ──
export async function executeHubspotCreateContact(params: {
  firstname: string;
  lastname?: string;
  email?: string;
  phone?: string;
  company?: string;
  jobtitle?: string;
  mobilephone?: string;
  address?: string;
  city?: string;
  zip?: string;
  country?: string;
  website?: string;
  hs_lead_status?: string;
  lifecyclestage?: string;
}, userJwt: string): Promise<string> {
  const url = `${SUPABASE_URL}/functions/v1/hubspot-api`;
  console.log(`[hubspot_create_contact] Création: ${params.firstname} ${params.lastname || ""}`);

  try {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "apikey": SUPABASE_ANON_KEY,
        Authorization: userJwt,
      },
      body: JSON.stringify({ action: "create-contact", ...params }),
    });

    const responseText = await response.text();
    console.log(`[hubspot_create_contact] HTTP ${response.status}: ${responseText.substring(0, 200)}`);

    let result;
    try { result = JSON.parse(responseText); } catch {
      return `Erreur: réponse non-JSON (HTTP ${response.status}): ${responseText.substring(0, 100)}`;
    }

    if (!response.ok) {
      return `Erreur: ${result.error || responseText.substring(0, 100)}`;
    }

    return result.message || `Contact créé dans HubSpot (ID: ${result.contact_id}).`;
  } catch (err) {
    const errMsg = err instanceof Error ? err.message : String(err);
    console.error(`[hubspot_create_contact] Exception:`, errMsg);
    return `Erreur création contact HubSpot: ${errMsg}`;
  }
}

// ─── Tool 14b: hubspot_update_contact (via hubspot-api edge function) ──
export async function executeHubspotUpdateContact(params: {
  contact_id: string;
  firstname?: string;
  lastname?: string;
  email?: string;
  phone?: string;
  company?: string;
  jobtitle?: string;
  mobilephone?: string;
  address?: string;
  city?: string;
  zip?: string;
  country?: string;
  website?: string;
  hs_lead_status?: string;
  lifecyclestage?: string;
}, userJwt: string): Promise<string> {
  const url = `${SUPABASE_URL}/functions/v1/hubspot-api`;
  console.log(`[hubspot_update_contact] Mise à jour contact ID: ${params.contact_id}`);

  try {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "apikey": SUPABASE_ANON_KEY,
        Authorization: userJwt,
      },
      body: JSON.stringify({ action: "update-contact", ...params }),
    });

    const responseText = await response.text();
    console.log(`[hubspot_update_contact] HTTP ${response.status}: ${responseText.substring(0, 200)}`);

    let result;
    try { result = JSON.parse(responseText); } catch {
      return `Erreur: réponse non-JSON (HTTP ${response.status}): ${responseText.substring(0, 100)}`;
    }

    if (!response.ok) {
      return `Erreur: ${result.error || responseText.substring(0, 100)}`;
    }

    return result.message || `Contact mis à jour dans HubSpot (ID: ${params.contact_id}).`;
  } catch (err) {
    const errMsg = err instanceof Error ? err.message : String(err);
    console.error(`[hubspot_update_contact] Exception:`, errMsg);
    return `Erreur mise à jour contact HubSpot: ${errMsg}`;
  }
}

// ─── Tool 14c: hubspot_delete_contact (via hubspot-api edge function) ──
export async function executeHubspotDeleteContact(params: {
  contact_id: string;
}, userJwt: string): Promise<string> {
  const url = `${SUPABASE_URL}/functions/v1/hubspot-api`;
  console.log(`[hubspot_delete_contact] Suppression contact ID: ${params.contact_id}`);

  try {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "apikey": SUPABASE_ANON_KEY,
        Authorization: userJwt,
      },
      body: JSON.stringify({ action: "delete-contact", contact_id: params.contact_id }),
    });

    const responseText = await response.text();
    console.log(`[hubspot_delete_contact] HTTP ${response.status}: ${responseText.substring(0, 200)}`);

    let result;
    try { result = JSON.parse(responseText); } catch {
      return `Erreur: réponse non-JSON (HTTP ${response.status}): ${responseText.substring(0, 100)}`;
    }

    if (!response.ok) {
      return `Erreur: ${result.error || responseText.substring(0, 100)}`;
    }

    return result.message || `Contact ${params.contact_id} supprimé de HubSpot.`;
  } catch (err) {
    const errMsg = err instanceof Error ? err.message : String(err);
    console.error(`[hubspot_delete_contact] Exception:`, errMsg);
    return `Erreur suppression contact HubSpot: ${errMsg}`;
  }
}

// ─── Tool 15: hubspot_search_deals (via hubspot-api edge function) ──
export async function executeHubspotSearchDeals(params: {
  query?: string;
  dealstage?: string;
}, userJwt: string): Promise<string> {
  const url = `${SUPABASE_URL}/functions/v1/hubspot-api`;
  console.log(`[hubspot_search_deals] Recherche deals: query="${params.query || ""}" stage="${params.dealstage || ""}"`);

  try {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "apikey": SUPABASE_ANON_KEY,
        Authorization: userJwt,
      },
      body: JSON.stringify({ action: "search-deals", ...params }),
    });

    const responseText = await response.text();
    console.log(`[hubspot_search_deals] HTTP ${response.status}: ${responseText.substring(0, 200)}`);

    let result;
    try { result = JSON.parse(responseText); } catch {
      return `Erreur: réponse non-JSON (HTTP ${response.status}): ${responseText.substring(0, 100)}`;
    }

    if (!response.ok) {
      return `Erreur: ${result.error || responseText.substring(0, 100)}`;
    }

    if (!result.deals || result.deals.length === 0) {
      return "Aucun deal trouvé dans HubSpot.";
    }

    return result.deals
      .map((d: { id: string; dealname: string; amount: string; dealstage: string; closedate: string }) =>
        `- [ID:${d.id}] ${d.dealname} | Montant: ${d.amount || "N/A"}€ | Étape: ${d.dealstage} | Clôture: ${d.closedate || "N/A"}`
      )
      .join("\n");
  } catch (err) {
    const errMsg = err instanceof Error ? err.message : String(err);
    console.error(`[hubspot_search_deals] Exception:`, errMsg);
    return `Erreur recherche deals HubSpot: ${errMsg}`;
  }
}

// ─── Tool 16: hubspot_create_deal (via hubspot-api edge function) ──
export async function executeHubspotCreateDeal(params: {
  dealname: string;
  amount?: string;
  dealstage?: string;
  pipeline?: string;
  closedate?: string;
}, userJwt: string): Promise<string> {
  const url = `${SUPABASE_URL}/functions/v1/hubspot-api`;
  console.log(`[hubspot_create_deal] Création: ${params.dealname}`);

  try {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "apikey": SUPABASE_ANON_KEY,
        Authorization: userJwt,
      },
      body: JSON.stringify({ action: "create-deal", ...params }),
    });

    const responseText = await response.text();
    console.log(`[hubspot_create_deal] HTTP ${response.status}: ${responseText.substring(0, 200)}`);

    let result;
    try { result = JSON.parse(responseText); } catch {
      return `Erreur: réponse non-JSON (HTTP ${response.status}): ${responseText.substring(0, 100)}`;
    }

    if (!response.ok) {
      return `Erreur: ${result.error || responseText.substring(0, 100)}`;
    }

    return result.message || `Deal "${params.dealname}" créé dans HubSpot (ID: ${result.deal_id}).`;
  } catch (err) {
    const errMsg = err instanceof Error ? err.message : String(err);
    console.error(`[hubspot_create_deal] Exception:`, errMsg);
    return `Erreur création deal HubSpot: ${errMsg}`;
  }
}

// ─── Tool 17: hubspot_update_deal (via hubspot-api edge function) ──
export async function executeHubspotUpdateDeal(params: {
  deal_id: string;
  dealname?: string;
  amount?: string;
  dealstage?: string;
  closedate?: string;
}, userJwt: string): Promise<string> {
  const url = `${SUPABASE_URL}/functions/v1/hubspot-api`;
  console.log(`[hubspot_update_deal] Mise à jour deal: ${params.deal_id}`);

  try {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "apikey": SUPABASE_ANON_KEY,
        Authorization: userJwt,
      },
      body: JSON.stringify({ action: "update-deal", ...params }),
    });

    const responseText = await response.text();
    console.log(`[hubspot_update_deal] HTTP ${response.status}: ${responseText.substring(0, 200)}`);

    let result;
    try { result = JSON.parse(responseText); } catch {
      return `Erreur: réponse non-JSON (HTTP ${response.status}): ${responseText.substring(0, 100)}`;
    }

    if (!response.ok) {
      return `Erreur: ${result.error || responseText.substring(0, 100)}`;
    }

    return result.message || "Deal mis à jour dans HubSpot.";
  } catch (err) {
    const errMsg = err instanceof Error ? err.message : String(err);
    console.error(`[hubspot_update_deal] Exception:`, errMsg);
    return `Erreur mise à jour deal HubSpot: ${errMsg}`;
  }
}

// ─── Tool 18: hubspot_get_pipeline (via hubspot-api edge function) ──
export async function executeHubspotGetPipeline(_params: Record<string, never>, userJwt: string): Promise<string> {
  const url = `${SUPABASE_URL}/functions/v1/hubspot-api`;
  console.log(`[hubspot_get_pipeline] Récupération pipelines`);

  try {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "apikey": SUPABASE_ANON_KEY,
        Authorization: userJwt,
      },
      body: JSON.stringify({ action: "get-pipeline" }),
    });

    const responseText = await response.text();
    console.log(`[hubspot_get_pipeline] HTTP ${response.status}: ${responseText.substring(0, 200)}`);

    let result;
    try { result = JSON.parse(responseText); } catch {
      return `Erreur: réponse non-JSON (HTTP ${response.status}): ${responseText.substring(0, 100)}`;
    }

    if (!response.ok) {
      return `Erreur: ${result.error || responseText.substring(0, 100)}`;
    }

    if (!result.pipelines || result.pipelines.length === 0) {
      return "Aucun pipeline trouvé dans HubSpot.";
    }

    return result.pipelines
      .map((p: { id: string; label: string; stages: { id: string; label: string }[] }) =>
        `Pipeline: ${p.label} (${p.id})\n` +
        p.stages.map((s, i) => `  ${i + 1}. ${s.label} (id: ${s.id})`).join("\n")
      )
      .join("\n\n");
  } catch (err) {
    const errMsg = err instanceof Error ? err.message : String(err);
    console.error(`[hubspot_get_pipeline] Exception:`, errMsg);
    return `Erreur récupération pipeline HubSpot: ${errMsg}`;
  }
}

// ─── Tool 19: hubspot_get_notes (via hubspot-api edge function) ──
export async function executeHubspotGetNotes(params: {
  contact_id: string;
  limit?: number;
}, userJwt: string): Promise<string> {
  const url = `${SUPABASE_URL}/functions/v1/hubspot-api`;
  console.log(`[hubspot_get_notes] Notes pour contact: ${params.contact_id}`);

  try {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "apikey": SUPABASE_ANON_KEY,
        Authorization: userJwt,
      },
      body: JSON.stringify({ action: "get-notes", contact_id: params.contact_id, limit: params.limit }),
    });

    const responseText = await response.text();
    console.log(`[hubspot_get_notes] HTTP ${response.status}: ${responseText.substring(0, 200)}`);

    let result;
    try { result = JSON.parse(responseText); } catch {
      return `Erreur: réponse non-JSON (HTTP ${response.status}): ${responseText.substring(0, 100)}`;
    }

    if (!response.ok) {
      return `Erreur: ${result.error || responseText.substring(0, 100)}`;
    }

    if (!result.notes || result.notes.length === 0) {
      return `Aucune note trouvée pour le contact ${params.contact_id}.`;
    }

    return result.notes
      .map((n: { id: string; body: string; timestamp: string; lastModified: string }) => {
        const date = n.timestamp ? new Date(n.timestamp).toLocaleString("fr-FR", { timeZone: "Europe/Paris" }) : "N/A";
        const bodyPreview = n.body.length > 200 ? n.body.substring(0, 200) + "..." : n.body;
        return `- [ID:${n.id}] (${date}) ${bodyPreview}`;
      })
      .join("\n");
  } catch (err) {
    const errMsg = err instanceof Error ? err.message : String(err);
    console.error(`[hubspot_get_notes] Exception:`, errMsg);
    return `Erreur récupération notes HubSpot: ${errMsg}`;
  }
}

// ─── Tool 20: hubspot_create_note (via hubspot-api edge function) ──
export async function executeHubspotCreateNote(params: {
  contact_id: string;
  body: string;
}, userJwt: string): Promise<string> {
  const url = `${SUPABASE_URL}/functions/v1/hubspot-api`;
  console.log(`[hubspot_create_note] Création note pour contact: ${params.contact_id}`);

  try {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "apikey": SUPABASE_ANON_KEY,
        Authorization: userJwt,
      },
      body: JSON.stringify({ action: "create-note", contact_id: params.contact_id, body: params.body }),
    });

    const responseText = await response.text();
    console.log(`[hubspot_create_note] HTTP ${response.status}: ${responseText.substring(0, 200)}`);

    let result;
    try { result = JSON.parse(responseText); } catch {
      return `Erreur: réponse non-JSON (HTTP ${response.status}): ${responseText.substring(0, 100)}`;
    }

    if (!response.ok) {
      return `Erreur: ${result.error || responseText.substring(0, 100)}`;
    }

    return result.message || `Note créée pour le contact ${params.contact_id} (ID: ${result.note_id}).`;
  } catch (err) {
    const errMsg = err instanceof Error ? err.message : String(err);
    console.error(`[hubspot_create_note] Exception:`, errMsg);
    return `Erreur création note HubSpot: ${errMsg}`;
  }
}

// ─── Tool 21: hubspot_update_note (via hubspot-api edge function) ──
export async function executeHubspotUpdateNote(params: {
  note_id: string;
  body: string;
}, userJwt: string): Promise<string> {
  const url = `${SUPABASE_URL}/functions/v1/hubspot-api`;
  console.log(`[hubspot_update_note] Mise à jour note: ${params.note_id}`);

  try {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "apikey": SUPABASE_ANON_KEY,
        Authorization: userJwt,
      },
      body: JSON.stringify({ action: "update-note", note_id: params.note_id, body: params.body }),
    });

    const responseText = await response.text();
    console.log(`[hubspot_update_note] HTTP ${response.status}: ${responseText.substring(0, 200)}`);

    let result;
    try { result = JSON.parse(responseText); } catch {
      return `Erreur: réponse non-JSON (HTTP ${response.status}): ${responseText.substring(0, 100)}`;
    }

    if (!response.ok) {
      return `Erreur: ${result.error || responseText.substring(0, 100)}`;
    }

    return result.message || `Note ${params.note_id} mise à jour.`;
  } catch (err) {
    const errMsg = err instanceof Error ? err.message : String(err);
    console.error(`[hubspot_update_note] Exception:`, errMsg);
    return `Erreur mise à jour note HubSpot: ${errMsg}`;
  }
}
