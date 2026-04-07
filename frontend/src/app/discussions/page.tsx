"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { useAuthContext } from "@/context/AuthContext";
import { useAuraSessionContext } from "@/context/AuraSessionContext";
import { listConversations, deleteConversation } from "@/lib/api";

interface Conversation {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

function formatRelativeTime(iso: string): string {
  const now = Date.now();
  const then = new Date(iso).getTime();
  if (isNaN(then)) return "";
  const diff = now - then;

  const minutes = Math.floor(diff / 60000);
  const hours = Math.floor(diff / 3600000);
  const days = Math.floor(diff / 86400000);

  if (minutes < 1) return "il y a quelques secondes";
  if (minutes < 60) return `il y a ${minutes} minute${minutes > 1 ? "s" : ""}`;
  if (hours < 24) return `il y a ${hours} heure${hours > 1 ? "s" : ""}`;
  if (days < 30) return `il y a ${days} jour${days > 1 ? "s" : ""}`;
  return new Date(iso).toLocaleDateString("fr-FR", { day: "numeric", month: "long" });
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "Date inconnue";
  const today = new Date();
  const yesterday = new Date();
  yesterday.setDate(yesterday.getDate() - 1);
  const weekAgo = new Date();
  weekAgo.setDate(weekAgo.getDate() - 7);

  if (d.toDateString() === today.toDateString()) return "Aujourd'hui";
  if (d.toDateString() === yesterday.toDateString()) return "Hier";
  if (d > weekAgo) return "Cette semaine";

  return d.toLocaleDateString("fr-FR", { month: "long", year: "numeric" });
}

function formatTime(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  return d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
}

function groupByDate(items: Conversation[]): Record<string, Conversation[]> {
  const groups: Record<string, Conversation[]> = {};
  for (const item of items) {
    const key = formatDate(item.updated_at || item.created_at);
    if (!groups[key]) groups[key] = [];
    groups[key].push(item);
  }
  return groups;
}

export default function DiscussionsPage() {
  const router = useRouter();
  const { user, session, loading: authLoading } = useAuthContext();
  const auraSession = useAuraSessionContext();
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [deleting, setDeleting] = useState<string | null>(null);

  const load = useCallback(() => {
    if (!session) return;
    setLoading(true);
    listConversations(session.access_token, 50)
      .then((data) => {
        const list: Conversation[] = Array.isArray(data) ? data : data.conversations ?? [];
        setConversations(list);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [session]);

  useEffect(() => {
    if (authLoading) return;
    if (!user || !session) {
      router.replace("/login");
      return;
    }
    load();
  }, [user, session, authLoading, router, load]);

  const handleDelete = async (id: string) => {
    if (!session?.access_token) return;
    if (!confirm("Supprimer cette conversation ?")) return;
    setDeleting(id);
    try {
      await deleteConversation(session.access_token, id);
      setConversations((prev) => prev.filter((c) => c.id !== id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erreur de suppression");
    } finally {
      setDeleting(null);
    }
  };

  if (authLoading || (!user && !error)) {
    return (
      <div style={{ display: "flex", justifyContent: "center", alignItems: "center", minHeight: "100vh" }}>
        <div style={{ color: "var(--text-muted)", fontSize: 14 }}>Chargement...</div>
      </div>
    );
  }

  const filtered = search
    ? conversations.filter((c) => (c.title || "").toLowerCase().includes(search.toLowerCase()))
    : conversations;

  const grouped = groupByDate(filtered);

  return (
    <div style={{ maxWidth: 960, margin: "0 auto", padding: "48px 24px" }}>
      {/* Header */}
      <div style={{ marginBottom: 32 }}>
        <h1 style={{ fontSize: 28, fontWeight: 700, color: "var(--text)", marginBottom: 6 }}>
          Discussions
        </h1>
        <p style={{ fontSize: 14, color: "var(--text-muted)" }}>
          Vos conversations avec Aura
        </p>
      </div>

      {/* Search bar */}
      <div style={{ position: "relative", marginBottom: 28 }}>
        <svg
          style={{ position: "absolute", left: 16, top: "50%", transform: "translateY(-50%)", opacity: 0.35 }}
          width="16" height="16" viewBox="0 0 24 24" fill="none"
          stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
        >
          <circle cx="11" cy="11" r="8" />
          <line x1="21" y1="21" x2="16.65" y2="16.65" />
        </svg>
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Rechercher dans vos conversations..."
          style={{
            width: "100%", padding: "14px 16px 14px 44px", fontSize: 14,
            borderRadius: 12, border: "1px solid var(--border)",
            background: "var(--surface)", color: "var(--text)", outline: "none",
            transition: "border-color 0.2s",
          }}
          onFocus={(e) => (e.target.style.borderColor = "var(--orange)")}
          onBlur={(e) => (e.target.style.borderColor = "var(--border)")}
        />
      </div>

      {/* Error */}
      {error && (
        <div style={{
          padding: "12px 16px", borderRadius: 12,
          background: "rgba(255,71,87,0.1)", border: "1px solid rgba(255,71,87,0.2)",
          color: "#ff6b6b", fontSize: 13, marginBottom: 24,
        }}>
          {error}
        </div>
      )}

      {/* Loading */}
      {loading && (
        <div style={{ textAlign: "center", padding: 60, color: "var(--text-muted)", fontSize: 14 }}>
          Chargement des conversations...
        </div>
      )}

      {/* Empty */}
      {!loading && !error && filtered.length === 0 && (
        <div style={{ textAlign: "center", padding: 60, background: "var(--surface)", border: "1px solid var(--border-light)", borderRadius: 16 }}>
          <div style={{ fontSize: 40, marginBottom: 16, opacity: 0.3 }}>💬</div>
          <p style={{ color: "var(--text-secondary)", fontSize: 14 }}>Aucune discussion</p>
          <p style={{ color: "var(--text-muted)", fontSize: 12, marginTop: 4 }}>
            Vos conversations avec Aura apparaitront ici
          </p>
        </div>
      )}

      {/* Conversations grouped by date */}
      {Object.entries(grouped).map(([date, items]) => (
        <div key={date} style={{ marginBottom: 28 }}>
          <h2 style={{
            fontSize: 11, fontWeight: 600, textTransform: "uppercase",
            letterSpacing: "0.08em", color: "var(--text-muted)", marginBottom: 8,
          }}>
            {date}
          </h2>
          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            {items.map((c) => (
              <ConversationItem
                key={c.id}
                conversation={c}
                onClick={() => {
                  auraSession.loadConversation(c.id);
                  router.push("/");
                }}
                onDelete={() => handleDelete(c.id)}
                isDeleting={deleting === c.id}
              />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function ConversationItem({ conversation, onClick, onDelete, isDeleting }: {
  conversation: Conversation; onClick: () => void; onDelete: () => void; isDeleting: boolean;
}) {
  const [hovered, setHovered] = useState(false);
  const ts = conversation.updated_at || conversation.created_at;

  return (
    <div
      onClick={onClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        display: "flex", alignItems: "center", gap: 14,
        padding: "12px 14px", borderRadius: 12, cursor: "pointer",
        background: hovered ? "var(--surface)" : "transparent",
        transition: "background 0.15s",
      }}
    >
      <div style={{
        width: 40, height: 40, borderRadius: 10,
        background: "rgba(255,107,53,0.1)",
        display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0,
      }}>
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none"
          stroke="#ff8c42" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
        </svg>
      </div>

      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{
          fontSize: 13, fontWeight: 500, color: "var(--text)",
          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
        }}>
          {conversation.title || "Conversation sans titre"}
        </div>
        <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 2 }}>
          {formatRelativeTime(ts)}
        </div>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
        <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
          {formatTime(ts)}
        </span>
        {hovered && (
          <button
            onClick={(e) => { e.stopPropagation(); onDelete(); }}
            disabled={isDeleting}
            style={{
              padding: "4px 8px", fontSize: 11, fontWeight: 500,
              color: "#d44040", background: "rgba(212,64,64,0.08)",
              border: "1px solid rgba(212,64,64,0.15)", borderRadius: 6,
              cursor: isDeleting ? "wait" : "pointer", opacity: isDeleting ? 0.5 : 1,
            }}
          >
            {isDeleting ? "..." : "Suppr."}
          </button>
        )}
      </div>
    </div>
  );
}
