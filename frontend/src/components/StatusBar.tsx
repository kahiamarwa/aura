"use client";

export type AppState =
  | "initializing"
  | "idle"
  | "listening"
  | "thinking"
  | "speaking"
  | "conversing"
  | "error";

interface StatusBarProps {
  state: AppState;
  fallbackMode?: string;
}

function getIdleMessage(fallbackMode?: string) {
  if (fallbackMode === "custom") return 'Dites "Dis Aura" pour commencer';
  if (fallbackMode === "builtin") return 'Dites "Computer" pour commencer';
  return "Cliquez le bouton pour parler";
}

const STATUS_MESSAGES: Record<AppState, string> = {
  initializing: "Initialisation...",
  idle: "",
  listening: "Je vous écoute...",
  thinking: "Réflexion...",
  speaking: "AURA répond...",
  conversing: "Continuez...",
  error: "Erreur",
};

export function StatusBar({ state, fallbackMode }: StatusBarProps) {
  const message =
    state === "idle" ? getIdleMessage(fallbackMode) : STATUS_MESSAGES[state];

  return (
    <div style={{ textAlign: "center" }}>
      <p
        style={{
          fontSize: 14,
          fontWeight: 500,
          color: "#6b6560",
          margin: 0,
        }}
      >
        {message}
      </p>
      {fallbackMode === "custom" && state === "idle" && (
        <p style={{ marginTop: 6, fontSize: 12, color: "#a39e97" }}>
          Mot-clé personnalisé actif
        </p>
      )}
      {fallbackMode === "builtin" && state === "idle" && (
        <p style={{ marginTop: 6, fontSize: 12, color: "#a39e97" }}>
          Mot-clé de secours
        </p>
      )}
      {fallbackMode === "push-to-talk" && state === "idle" && (
        <p style={{ marginTop: 6, fontSize: 12, color: "#a39e97" }}>
          Mode push-to-talk (wake word indisponible)
        </p>
      )}
      {state === "conversing" && (
        <p style={{ marginTop: 6, fontSize: 12, color: "#a39e97" }}>
          Mode conversation actif
        </p>
      )}
    </div>
  );
}
