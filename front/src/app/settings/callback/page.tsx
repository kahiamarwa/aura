"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuthContext } from "@/context/AuthContext";
import { handleOAuthCallback } from "@/lib/integrations";

export default function OAuthCallbackPage() {
  const router = useRouter();
  const { session } = useAuthContext();
  const [status, setStatus] = useState<"processing" | "success" | "error">("processing");
  const [message, setMessage] = useState("Connexion en cours...");
  const processedRef = useRef(false);

  useEffect(() => {
    if (processedRef.current) return;
    if (!session?.access_token) return;

    const params = new URLSearchParams(window.location.search);
    const code = params.get("code");
    const state = params.get("state");
    const error = params.get("error");

    if (error) {
      processedRef.current = true;
      setStatus("error");
      setMessage(`OAuth refuse: ${error}`);
      setTimeout(() => {
        router.replace(`/settings?error=${encodeURIComponent(`OAuth refuse: ${error}`)}`);
      }, 2000);
      return;
    }

    if (!code || !state) {
      processedRef.current = true;
      setStatus("error");
      setMessage("Parametres OAuth manquants");
      setTimeout(() => {
        router.replace("/settings?error=Param%C3%A8tres%20OAuth%20manquants");
      }, 2000);
      return;
    }

    processedRef.current = true;
    const provider = state;

    setMessage(`Connexion de ${provider}...`);

    handleOAuthCallback(provider, code, session.access_token)
      .then((result) => {
        setStatus("success");
        const detail =
          (result as Record<string, string>).email ||
          (result as Record<string, string>).team_name ||
          (result as Record<string, string>).portal_name ||
          provider;
        setMessage(`${provider} connecte : ${detail}`);

        setTimeout(() => {
          router.replace(
            `/settings?success=${encodeURIComponent(`${provider} connecte avec succes`)}`
          );
        }, 1500);
      })
      .catch((err) => {
        setStatus("error");
        const errMsg = err instanceof Error ? err.message : "Erreur inconnue";
        setMessage(errMsg);

        setTimeout(() => {
          router.replace(`/settings?error=${encodeURIComponent(errMsg)}`);
        }, 3000);
      });
  }, [session?.access_token, router]);

  const orbColor =
    status === "error"
      ? "#d44040"
      : status === "success"
        ? "#2d9e6a"
        : "#e36b2b";

  const orbGradient =
    status === "error"
      ? "linear-gradient(135deg, #d44040, #e06060)"
      : status === "success"
        ? "linear-gradient(135deg, #2d9e6a, #3dbf80)"
        : "linear-gradient(135deg, #e36b2b, #f08c42, #f5a623)";

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        minHeight: "100vh",
        background: "#faf6f1",
        padding: 16,
      }}
    >
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 32,
          padding: "48px 40px",
          borderRadius: 20,
          background: "#ffffff",
          border: "1px solid #e8e2d9",
          boxShadow: "0 4px 24px rgba(0,0,0,0.06)",
          minWidth: 320,
          textAlign: "center",
        }}
      >
        {/* Orb */}
        <div style={{ position: "relative" }}>
          <div
            style={{
              position: "absolute",
              inset: -8,
              borderRadius: "50%",
              background: orbGradient,
              filter: "blur(16px)",
              opacity: 0.3,
              animation: status === "processing" ? "orbIdle 2s ease-in-out infinite" : undefined,
            }}
          />
          <div
            style={{
              position: "relative",
              width: 80,
              height: 80,
              borderRadius: "50%",
              background: orbGradient,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              boxShadow: `0 0 40px ${orbColor}30`,
              animation: status === "processing" ? "orbIdle 2s ease-in-out infinite" : undefined,
            }}
          >
            <span style={{ fontSize: 28, color: "white", fontWeight: 700 }}>
              {status === "error" ? "\u2717" : status === "success" ? "\u2713" : "..."}
            </span>
          </div>
        </div>

        <div>
          <h1 style={{ fontSize: 18, fontWeight: 700, color: "#1a1a1a", margin: 0 }}>
            {status === "processing"
              ? "Traitement en cours"
              : status === "success"
                ? "Connexion reussie"
                : "Erreur de connexion"}
          </h1>
          <p style={{ fontSize: 14, color: "#a39e97", marginTop: 8 }}>
            {message}
          </p>
        </div>

        {status !== "processing" && (
          <button
            onClick={() => router.replace("/settings")}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              padding: "10px 20px",
              borderRadius: 10,
              border: "1px solid #ddd6cc",
              background: "#ffffff",
              color: "#6b6560",
              fontSize: 14,
              fontWeight: 500,
              cursor: "pointer",
              transition: "all 0.15s",
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.borderColor = "#e36b2b";
              e.currentTarget.style.color = "#e36b2b";
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.borderColor = "#ddd6cc";
              e.currentTarget.style.color = "#6b6560";
            }}
          >
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
              <path d="M10 12L6 8L10 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
            Retour aux parametres
          </button>
        )}
      </div>
    </div>
  );
}
