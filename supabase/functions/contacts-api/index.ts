import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { getUserFromRequest } from "../_shared/auth.ts";

// ============================================================
// AURA — Edge Function: contacts-api
//
// CRUD pour les contacts et dossiers réunions clients
// Actions: list, get, create, update, delete, search, add-meeting
// ============================================================

const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const SUPABASE_SERVICE_ROLE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;

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

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") {
    return new Response(null, { status: 200, headers: corsHeaders });
  }

  try {
    // Verify user identity
    let userId: string;
    try {
      const authUser = await getUserFromRequest(req);
      userId = authUser.user_id;
    } catch (authErr) {
      return jsonResponse({ error: authErr instanceof Error ? authErr.message : "Non autorisé" }, 401);
    }

    const { action, ...params } = await req.json();
    const supabase = createClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY);

    // ── LIST: tous les contacts ──
    if (action === "list") {
      const { data, error } = await supabase
        .from("contacts")
        .select("*, contact_meetings(count)")
        .eq("user_id", userId)
        .order("name", { ascending: true });

      if (error) throw new Error(error.message);

      const contacts = (data || []).map((c: { contact_meetings: { count: number }[] } & Record<string, unknown>) => ({
        ...c,
        meetings_count: c.contact_meetings?.[0]?.count || 0,
        contact_meetings: undefined,
      }));

      return jsonResponse({ contacts });
    }

    // ── GET: un contact + ses réunions ──
    if (action === "get") {
      const { id } = params;
      if (!id) return jsonResponse({ error: "id requis" }, 400);

      const { data: contact, error } = await supabase
        .from("contacts")
        .select("*")
        .eq("id", id)
        .eq("user_id", userId)
        .single();

      if (error) throw new Error(error.message);

      const { data: meetings } = await supabase
        .from("contact_meetings")
        .select("*")
        .eq("contact_id", id)
        .eq("user_id", userId)
        .order("meeting_date", { ascending: false });

      return jsonResponse({ contact, meetings: meetings || [] });
    }

    // ── CREATE: créer un contact ──
    if (action === "create") {
      const { name, email, phone, company, notes } = params;
      if (!name) return jsonResponse({ error: "name requis" }, 400);

      const { data, error } = await supabase
        .from("contacts")
        .insert({ name, email, phone, company, notes, user_id: userId })
        .select()
        .single();

      if (error) throw new Error(error.message);

      console.log(`[contacts-api] Contact créé: ${name}`);
      return jsonResponse({ contact: data });
    }

    // ── UPDATE: modifier un contact ──
    if (action === "update") {
      const { id, ...fields } = params;
      if (!id) return jsonResponse({ error: "id requis" }, 400);

      const updateData: Record<string, unknown> = { updated_at: new Date().toISOString() };
      if (fields.name !== undefined) updateData.name = fields.name;
      if (fields.email !== undefined) updateData.email = fields.email;
      if (fields.phone !== undefined) updateData.phone = fields.phone;
      if (fields.company !== undefined) updateData.company = fields.company;
      if (fields.notes !== undefined) updateData.notes = fields.notes;

      const { data, error } = await supabase
        .from("contacts")
        .update(updateData)
        .eq("id", id)
        .eq("user_id", userId)
        .select()
        .single();

      if (error) throw new Error(error.message);

      console.log(`[contacts-api] Contact mis à jour: ${data.name}`);
      return jsonResponse({ contact: data });
    }

    // ── DELETE: supprimer un contact ──
    if (action === "delete") {
      const { id } = params;
      if (!id) return jsonResponse({ error: "id requis" }, 400);

      const { error } = await supabase
        .from("contacts")
        .delete()
        .eq("id", id)
        .eq("user_id", userId);

      if (error) throw new Error(error.message);

      console.log(`[contacts-api] Contact supprimé: ${id}`);
      return jsonResponse({ success: true });
    }

    // ── SEARCH: chercher par nom ou entreprise ──
    if (action === "search") {
      const { query } = params;
      if (!query) return jsonResponse({ error: "query requis" }, 400);

      const pattern = `%${query}%`;

      const { data, error } = await supabase
        .from("contacts")
        .select("*")
        .eq("user_id", userId)
        .or(`name.ilike.${pattern},company.ilike.${pattern},email.ilike.${pattern}`)
        .order("name")
        .limit(10);

      if (error) throw new Error(error.message);

      return jsonResponse({ contacts: data || [] });
    }

    // ── ADD-MEETING: ajouter une réunion au dossier d'un contact ──
    if (action === "add-meeting") {
      const { contact_name, contact_id, title, summary } = params;

      let targetContactId = contact_id;

      // Si pas de contact_id, chercher par nom ou créer
      if (!targetContactId && contact_name) {
        const pattern = `%${contact_name}%`;
        const { data: found } = await supabase
          .from("contacts")
          .select("id, name")
          .eq("user_id", userId)
          .ilike("name", pattern)
          .limit(1)
          .single();

        if (found) {
          targetContactId = found.id;
          console.log(`[contacts-api] Contact trouvé: ${found.name}`);
        } else {
          // Créer le contact automatiquement
          const { data: created, error: createErr } = await supabase
            .from("contacts")
            .insert({ name: contact_name, user_id: userId })
            .select()
            .single();

          if (createErr) throw new Error(createErr.message);

          targetContactId = created.id;
          console.log(`[contacts-api] Contact auto-créé: ${contact_name}`);
        }
      }

      if (!targetContactId) {
        return jsonResponse({ error: "contact_name ou contact_id requis" }, 400);
      }

      if (!title || !summary) {
        return jsonResponse({ error: "title et summary requis" }, 400);
      }

      const { data: meeting, error } = await supabase
        .from("contact_meetings")
        .insert({
          contact_id: targetContactId,
          title,
          summary,
          meeting_date: new Date().toISOString(),
          user_id: userId,
        })
        .select()
        .single();

      if (error) throw new Error(error.message);

      console.log(`[contacts-api] Réunion ajoutée au dossier: ${title}`);
      return jsonResponse({
        success: true,
        meeting,
        message: `Réunion "${title}" ajoutée au dossier du contact.`,
      });
    }

    return jsonResponse({ error: `Action inconnue: ${action}` }, 400);
  } catch (err) {
    console.error("[contacts-api] Error:", err);
    return jsonResponse(
      { error: err instanceof Error ? err.message : "Erreur inconnue" },
      500
    );
  }
});
