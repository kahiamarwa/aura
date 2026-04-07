"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuthContext } from "@/context/AuthContext";
import { useAuraSessionContext } from "@/context/AuraSessionContext";
import { AuraOrb } from "@/components/AuraOrb";
import { StatusBar } from "@/components/StatusBar";
import { VolumeIndicator } from "@/components/VolumeIndicator";
import { LiveTranscript } from "@/components/LiveTranscript";
import { ContextPanel } from "@/components/ContextPanel";
import ReactMarkdown from "react-markdown";
import { supabase } from "@/lib/supabase";

export default function Home() {
  const router = useRouter();
  const { user, loading: authLoading } = useAuthContext();
  const session = useAuraSessionContext();

  useEffect(() => {
    if (!authLoading && !user) {
      router.replace("/login");
    }
  }, [authLoading, user, router]);

  if (!authLoading && !user) {
    return null;
  }

  if (authLoading || session.state === "initializing") {
    return (
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          height: "100%",
          minHeight: "100vh",
          gap: 24,
          background: "#faf6f1",
        }}
      >
        <div
          style={{
            width: 80,
            height: 80,
            borderRadius: "50%",
            background: "linear-gradient(135deg, #e36b2b, #f08c42, #f5a623)",
            boxShadow: "0 0 60px rgba(227,107,43,0.2)",
            animation: "orbIdle 3s ease-in-out infinite",
          }}
        />
        <p style={{ fontSize: 14, color: "#a39e97", fontWeight: 300 }}>
          Initialisation...
        </p>
      </div>
    );
  }

  // ── Active session ──
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        background: "#faf6f1",
      }}
    >
      <div
        style={{
          flex: 1,
          overflowY: "auto",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
        }}
      >
        {/* Orb section */}
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: 12,
            paddingTop: 40,
            paddingBottom: 16,
          }}
        >
          <AuraOrb state={session.state} volume={session.volume} size={140} />
          <StatusBar state={session.state} fallbackMode={session.fallbackMode} />
          <VolumeIndicator volume={session.volume} />

          {/* Mute button under the orb */}
          <button
            onClick={session.toggleMute}
            title={session.muted ? "Activer le micro" : "Couper le micro"}
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              width: 44,
              height: 44,
              borderRadius: "50%",
              border: session.muted ? "2px solid #d44040" : "1px solid #ddd6cc",
              background: session.muted ? "rgba(212,64,64,0.08)" : "#ffffff",
              color: session.muted ? "#d44040" : "#a39e97",
              cursor: "pointer",
              marginTop: 4,
              transition: "all 0.2s",
              boxShadow: session.muted
                ? "0 0 12px rgba(212,64,64,0.15)"
                : "0 1px 4px rgba(0,0,0,0.06)",
            }}
            onMouseEnter={(e) => {
              if (!session.muted) {
                e.currentTarget.style.borderColor = "#a39e97";
                e.currentTarget.style.color = "#6b6560";
              }
            }}
            onMouseLeave={(e) => {
              if (!session.muted) {
                e.currentTarget.style.borderColor = "#ddd6cc";
                e.currentTarget.style.color = "#a39e97";
              }
            }}
          >
            {session.muted ? (
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <line x1="1" y1="1" x2="23" y2="23" />
                <path d="M9 9v3a3 3 0 0 0 5.12 2.12M15 9.34V4a3 3 0 0 0-5.94-.6" />
                <path d="M17 16.95A7 7 0 0 1 5 12v-2m14 0v2c0 .67-.08 1.32-.22 1.94" />
                <line x1="12" y1="19" x2="12" y2="23" />
                <line x1="8" y1="23" x2="16" y2="23" />
              </svg>
            ) : (
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
                <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
                <line x1="12" y1="19" x2="12" y2="23" />
                <line x1="8" y1="23" x2="16" y2="23" />
              </svg>
            )}
          </button>
        </div>

        {/* Push-to-talk */}
        {session.fallbackMode === "push-to-talk" &&
          (session.state === "idle" || session.state === "conversing") && (
          <button
            onClick={session.triggerWakeWord}
            style={{
              marginBottom: 16,
              padding: "12px 32px",
              borderRadius: 50,
              border: "1px solid #2d9e6a",
              background: "#ffffff",
              color: "#2d9e6a",
              fontSize: 14,
              fontWeight: 500,
              letterSpacing: "0.05em",
              textTransform: "uppercase" as const,
              cursor: "pointer",
              boxShadow: "0 2px 8px rgba(45,158,106,0.12)",
              transition: "transform 0.2s",
            }}
            onMouseEnter={(e) => { e.currentTarget.style.transform = "scale(1.05)"; }}
            onMouseLeave={(e) => { e.currentTarget.style.transform = "scale(1)"; }}
          >
            Parler
          </button>
        )}

        {/* Live transcript */}
        <div style={{ width: "100%", maxWidth: 512, padding: "0 16px" }}>
          <LiveTranscript
            partialText={session.commandPartial}
            committedText={session.commandCommitted}
            isActive={session.state === "listening"}
          />
        </div>

        {/* Errors */}
        {session.errors.length > 0 && (
          <div
            style={{
              margin: "16px 16px 0",
              width: "100%",
              maxWidth: 512,
              padding: 12,
              borderRadius: 12,
              background: "rgba(212,64,64,0.06)",
              border: "1px solid rgba(212,64,64,0.15)",
            }}
          >
            {session.errors.map((err, i) => (
              <p key={i} style={{ fontSize: 12, color: "#d44040", margin: 0 }}>
                {err}
              </p>
            ))}
          </div>
        )}

        {/* Messages / history */}
        <div style={{ marginTop: 16, width: "100%", maxWidth: 960, padding: "0 32px 32px" }}>
          {session.history.map((entry) => (
            <div key={entry.id} style={{ marginBottom: 20, animation: "msgIn 0.3s ease-out" }}>
              {/* User */}
              <div style={{ display: "flex", gap: 12, marginBottom: 12, flexDirection: "row-reverse" }}>
                <div
                  style={{
                    width: 28,
                    height: 28,
                    borderRadius: "50%",
                    background: "#1a1a1a",
                    color: "white",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    fontSize: 11,
                    fontWeight: 600,
                    flexShrink: 0,
                    marginTop: 2,
                  }}
                >
                  {user?.email?.charAt(0).toUpperCase() || "U"}
                </div>
                <div style={{ maxWidth: "80%" }}>
                  <div
                    style={{
                      fontSize: 11,
                      fontWeight: 600,
                      color: "#6b6560",
                      marginBottom: 4,
                      textAlign: "right",
                    }}
                  >
                    Vous
                  </div>
                  <div
                    style={{
                      display: "inline-block",
                      float: "right",
                      padding: "10px 14px",
                      borderRadius: "16px 16px 4px 16px",
                      background: "#e36b2b",
                      color: "white",
                      fontSize: 14,
                      lineHeight: 1.7,
                    }}
                  >
                    {entry.command}
                  </div>
                </div>
              </div>
              {/* Aura */}
              {entry.response && (
                <div style={{ display: "flex", gap: 12 }}>
                  <div
                    style={{
                      width: 28,
                      height: 28,
                      borderRadius: "50%",
                      background: "linear-gradient(135deg, #e36b2b, #f08c42, #f5a623)",
                      color: "white",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      fontSize: 11,
                      fontWeight: 600,
                      flexShrink: 0,
                      marginTop: 2,
                    }}
                  >
                    A
                  </div>
                  <div style={{ flex: 1, minWidth: 0, maxWidth: "90%" }}>
                    <div
                      style={{
                        fontSize: 11,
                        fontWeight: 600,
                        color: "#6b6560",
                        marginBottom: 4,
                      }}
                    >
                      Aura
                    </div>
                    <div
                      style={{
                        fontSize: 14,
                        lineHeight: 1.7,
                        color: "#1a1a1a",
                      }}
                    >
                      <ReactMarkdown>{entry.response}</ReactMarkdown>
                      {entry.isStreaming && (
                        <span
                          style={{
                            display: "inline-block",
                            width: 7,
                            height: 16,
                            background: "#e07a3a",
                            borderRadius: 2,
                            marginLeft: 2,
                            animation: "blink 1s step-end infinite",
                          }}
                        />
                      )}
                    </div>
                    {entry.toolInProgress && (
                      <div
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: 8,
                          marginTop: 8,
                          padding: "8px 12px",
                          borderRadius: 8,
                          background: "rgba(224, 122, 58, 0.08)",
                          border: "1px solid rgba(224, 122, 58, 0.2)",
                          fontSize: 13,
                          color: "#6b6560",
                        }}
                      >
                        <div
                          style={{
                            width: 16,
                            height: 16,
                            border: "2px solid #e07a3a",
                            borderTopColor: "transparent",
                            borderRadius: "50%",
                            animation: "spin 0.8s linear infinite",
                          }}
                        />
                        <span>
                          {entry.toolInProgress === "create_report"
                            ? "Création du rapport..."
                            : entry.toolInProgress === "create_presentation"
                              ? "Création de la présentation..."
                              : entry.toolInProgress === "web_search"
                                ? "Recherche sur internet..."
                                : entry.toolInProgress === "send_email"
                                  ? "Envoi de l'email..."
                                  : entry.toolInProgress === "send_whatsapp"
                                    ? "Envoi WhatsApp..."
                                    : `${entry.toolInProgress}...`}
                        </span>
                      </div>
                    )}
                    {entry.attachments && entry.attachments.length > 0 && (
                      <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 6 }}>
                        {entry.attachments.map((att, idx) => {
                          const isReport = att.type === "report";
                          const accentColor = isReport ? "50, 100, 180" : "210, 120, 50";
                          const accentHex = isReport ? "#3264b4" : "#d27832";
                          const downloadUrl = supabase.storage
                            .from("presentations")
                            .getPublicUrl(att.file_path).data.publicUrl;
                          // Reports: direct PDF URL (browsers render natively); Presentations: Google Docs viewer
                          const viewerUrl = isReport
                            ? downloadUrl
                            : `https://docs.google.com/gview?url=${encodeURIComponent(downloadUrl)}&embedded=true`;
                          return (
                            <div key={idx} style={{ display: "flex", flexDirection: "column", gap: 0 }}>
                              {/* Header bar */}
                              <div
                                style={{
                                  display: "flex",
                                  alignItems: "center",
                                  gap: 10,
                                  padding: "10px 14px",
                                  borderRadius: "10px 10px 0 0",
                                  background: `rgba(${accentColor}, 0.08)`,
                                  border: `1px solid rgba(${accentColor}, 0.2)`,
                                  borderBottom: "none",
                                  color: "#1a1a1a",
                                }}
                              >
                                {isReport ? (
                                  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke={accentHex} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                                    <polyline points="14 2 14 8 20 8" />
                                    <line x1="16" y1="13" x2="8" y2="13" />
                                    <line x1="16" y1="17" x2="8" y2="17" />
                                    <polyline points="10 9 9 9 8 9" />
                                  </svg>
                                ) : (
                                  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke={accentHex} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                                    <polyline points="14 2 14 8 20 8" />
                                  </svg>
                                )}
                                <div style={{ flex: 1 }}>
                                  <div style={{ fontSize: 13, fontWeight: 600 }}>{att.file_name}</div>
                                  {isReport && (
                                    <div style={{ fontSize: 11, color: "#7f8c8d", marginTop: 1 }}>Rapport PDF</div>
                                  )}
                                </div>
                                {/* For reports: single PDF button. For presentations: PPTX + PDF buttons */}
                                {isReport ? (
                                  <a
                                    href={downloadUrl}
                                    download={att.file_name}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    title="Télécharger PDF"
                                    style={{
                                      display: "flex",
                                      alignItems: "center",
                                      gap: 6,
                                      padding: "4px 10px",
                                      borderRadius: 6,
                                      border: "1px solid rgba(220, 50, 50, 0.3)",
                                      background: "#fff",
                                      color: "#c0392b",
                                      fontSize: 12,
                                      textDecoration: "none",
                                      cursor: "pointer",
                                    }}
                                  >
                                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                                      <polyline points="7 10 12 15 17 10" />
                                      <line x1="12" y1="15" x2="12" y2="3" />
                                    </svg>
                                    PDF
                                  </a>
                                ) : (
                                  <>
                                    <a
                                      href={downloadUrl}
                                      download={att.file_name}
                                      target="_blank"
                                      rel="noopener noreferrer"
                                      title="Télécharger"
                                      style={{
                                        display: "flex",
                                        alignItems: "center",
                                        gap: 6,
                                        padding: "4px 10px",
                                        borderRadius: 6,
                                        border: `1px solid rgba(${accentColor}, 0.3)`,
                                        background: "#fff",
                                        color: "#6b6560",
                                        fontSize: 12,
                                        textDecoration: "none",
                                        cursor: "pointer",
                                      }}
                                    >
                                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                                        <polyline points="7 10 12 15 17 10" />
                                        <line x1="12" y1="15" x2="12" y2="3" />
                                      </svg>
                                      PPTX
                                    </a>
                                    {att.pdf_file_path && (() => {
                                      const pdfUrl = supabase.storage
                                        .from("presentations")
                                        .getPublicUrl(att.pdf_file_path).data.publicUrl;
                                      return (
                                        <a
                                          href={pdfUrl}
                                          download={att.pdf_file_name || att.file_name?.replace(/\.pptx$/, ".pdf")}
                                          target="_blank"
                                          rel="noopener noreferrer"
                                          title="Telecharger PDF"
                                          style={{
                                            display: "flex",
                                            alignItems: "center",
                                            gap: 6,
                                            padding: "4px 10px",
                                            borderRadius: 6,
                                            border: "1px solid rgba(220, 50, 50, 0.3)",
                                            background: "#fff",
                                            color: "#c0392b",
                                            fontSize: 12,
                                            textDecoration: "none",
                                            cursor: "pointer",
                                          }}
                                        >
                                          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                                            <polyline points="7 10 12 15 17 10" />
                                            <line x1="12" y1="15" x2="12" y2="3" />
                                          </svg>
                                          PDF
                                        </a>
                                      );
                                    })()}
                                  </>
                                )}
                              </div>
                              {/* Inline viewer */}
                              <iframe
                                src={viewerUrl}
                                style={{
                                  width: "100%",
                                  height: 450,
                                  border: `1px solid rgba(${accentColor}, 0.2)`,
                                  borderRadius: "0 0 10px 10px",
                                  background: "#f5f5f5",
                                }}
                                title={`Aperçu de ${att.file_name}`}
                                allowFullScreen
                              />
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>
          ))}

          <ContextPanel
            entries={session.passiveEntries}
            currentPartial={session.passivePartial}
          />
        </div>
      </div>
    </div>
  );
}
