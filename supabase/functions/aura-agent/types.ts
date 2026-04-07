// ============================================================
// AURA Agent — Shared Types & Constants
// ============================================================

// ─── Environment Variables ───────────────────────────────────
export const ANTHROPIC_API_KEY = Deno.env.get("ANTHROPIC_API_KEY")!;
export const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
export const SUPABASE_SERVICE_ROLE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
export const SUPABASE_ANON_KEY = Deno.env.get("SUPABASE_ANON_KEY") || "";
export const TAVILY_API_KEY = Deno.env.get("TAVILY_API_KEY") || "";

// ─── CORS ────────────────────────────────────────────────────
export const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
  "Access-Control-Allow-Headers":
    "Content-Type, Authorization, X-Client-Info, Apikey",
};

// ─── Types ───────────────────────────────────────────────────
// deno-lint-ignore no-explicit-any
export type SupabaseClient = any;

export interface AgentAttachment {
  file_path: string;
  file_name: string;
  type: string;
  pdf_file_path?: string;
  pdf_file_name?: string;
}

export interface AgentResult {
  response: string;
  tools_used: string[];
  summary_id?: string;
  attachments?: AgentAttachment[];
}
