"use client";

import type { AppState } from "./StatusBar";

interface AuraOrbProps {
  state: AppState;
  volume: number;
  size?: number;
}

const STATE_CONFIG: Record<
  AppState,
  { gradient: string; color: string; glowColor: string; coreColor: string }
> = {
  initializing: {
    gradient: "radial-gradient(circle at 35% 35%, #d4c4b0, #b8a896, #a39e97)",
    color: "#a39e97",
    glowColor: "#a39e9740",
    coreColor: "#d4c4b0",
  },
  idle: {
    gradient: "radial-gradient(circle at 35% 35%, #f5a623, #f08c42, #e36b2b)",
    color: "#e36b2b",
    glowColor: "#e36b2b40",
    coreColor: "#f5a623",
  },
  listening: {
    gradient: "radial-gradient(circle at 30% 30%, #4ade80, #22c55e, #16a34a)",
    color: "#22c55e",
    glowColor: "#22c55e40",
    coreColor: "#4ade80",
  },
  thinking: {
    gradient: "radial-gradient(circle at 30% 30%, #fbbf24, #f59e0b, #d97706)",
    color: "#f59e0b",
    glowColor: "#f59e0b40",
    coreColor: "#fbbf24",
  },
  speaking: {
    gradient: "radial-gradient(circle at 30% 30%, #c4b5fd, #a78bfa, #7c3aed)",
    color: "#a78bfa",
    glowColor: "#a78bfa40",
    coreColor: "#c4b5fd",
  },
  conversing: {
    gradient: "radial-gradient(circle at 30% 30%, #67e8f9, #22d3ee, #0891b2)",
    color: "#22d3ee",
    glowColor: "#22d3ee40",
    coreColor: "#67e8f9",
  },
  error: {
    gradient: "radial-gradient(circle at 30% 30%, #fca5a5, #ef4444, #dc2626)",
    color: "#ef4444",
    glowColor: "#ef444440",
    coreColor: "#fca5a5",
  },
};

export function AuraOrb({ state, volume, size = 140 }: AuraOrbProps) {
  const config = STATE_CONFIG[state];
  const isListening = state === "listening";
  const isThinking = state === "thinking";
  const isError = state === "error";

  // Volume reactivity for listening state
  const volumeFactor = isListening ? volume / 100 : 0;
  const orbScale = isListening ? 1 + volumeFactor * 0.2 : 1;
  const glowExpand = isListening ? 1 + volumeFactor * 0.5 : 1;

  // Container is 2x orb size for glow space
  const containerSize = size * 2;
  const orbOffset = (containerSize - size) / 2;

  // Animation class per state
  const animClass = isError
    ? ""
    : state === "initializing"
      ? "aura-pulse-subtle"
      : state === "idle"
        ? "aura-breathe"
        : state === "listening"
          ? "aura-pulse-fast"
          : state === "thinking"
            ? "aura-pulse-medium"
            : state === "speaking"
              ? "aura-pulse-medium"
              : state === "conversing"
                ? "aura-pulse-soft"
                : "";

  const ringAnimClass = isThinking ? "aura-spin" : "";
  const haloAnimClass = isError
    ? ""
    : state === "initializing"
      ? "aura-halo-subtle"
      : state === "idle"
        ? "aura-halo-breathe"
        : state === "listening"
          ? "aura-halo-fast"
          : state === "speaking"
            ? "aura-halo-medium"
            : state === "conversing"
              ? "aura-halo-soft"
              : "";

  // Proportional sizes based on orb size
  const haloSize = size * 1.1;
  const haloOffset = (containerSize - haloSize) / 2;
  const washSize = size * 1.2;
  const washOffset = (containerSize - washSize) / 2;
  const ringSize = size * 1.12;
  const ringOffset = (containerSize - ringSize) / 2;
  const ring2Size = size * 1.14;
  const ring2Offset = (containerSize - ring2Size) / 2;
  const highlightW = size * 0.8;
  const highlightH = size * 0.4;
  const highlightLeft = orbOffset + size * 0.06;
  const highlightTop = orbOffset + size * 0.04;
  const coreSize = size * 0.3;
  const coreLeft = orbOffset + size * 0.28;
  const coreTop = orbOffset + size * 0.26;
  const specSize = size * 0.12;
  const specLeft = orbOffset + size * 0.34;
  const specTop = orbOffset + size * 0.23;

  return (
    <>
      <style jsx global>{`
        /* ─── Breathe (idle, 4s) ─── */
        @keyframes aura-breathe-kf {
          0%, 100% { transform: scale(1); }
          50% { transform: scale(1.04); }
        }
        .aura-breathe { animation: aura-breathe-kf 4s ease-in-out infinite; }

        /* ─── Fast pulse (listening, 1s) ─── */
        @keyframes aura-pulse-fast-kf {
          0%, 100% { transform: scale(1); }
          50% { transform: scale(1.06); }
        }
        .aura-pulse-fast { animation: aura-pulse-fast-kf 1s ease-in-out infinite; }

        /* ─── Medium pulse (speaking, 2.5s) ─── */
        @keyframes aura-pulse-medium-kf {
          0%, 100% { transform: scale(1); }
          50% { transform: scale(1.05); }
        }
        .aura-pulse-medium { animation: aura-pulse-medium-kf 2.5s ease-in-out infinite; }

        /* ─── Soft pulse (conversing, 2s) ─── */
        @keyframes aura-pulse-soft-kf {
          0%, 100% { transform: scale(1); opacity: 1; }
          50% { transform: scale(1.03); opacity: 0.92; }
        }
        .aura-pulse-soft { animation: aura-pulse-soft-kf 2s ease-in-out infinite; }

        /* ─── Subtle pulse (initializing) ─── */
        @keyframes aura-pulse-subtle-kf {
          0%, 100% { opacity: 0.6; }
          50% { opacity: 1; }
        }
        .aura-pulse-subtle { animation: aura-pulse-subtle-kf 2s ease-in-out infinite; }

        /* ─── Spin (thinking outer ring) ─── */
        @keyframes aura-spin-kf {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
        .aura-spin { animation: aura-spin-kf 2.5s linear infinite; }

        /* ─── Halo pulse variants ─── */
        @keyframes aura-halo-breathe-kf {
          0%, 100% { transform: scale(1); opacity: 0.5; }
          50% { transform: scale(1.08); opacity: 0.8; }
        }
        .aura-halo-breathe { animation: aura-halo-breathe-kf 4s ease-in-out infinite; }

        @keyframes aura-halo-fast-kf {
          0%, 100% { transform: scale(1); opacity: 0.6; }
          50% { transform: scale(1.15); opacity: 1; }
        }
        .aura-halo-fast { animation: aura-halo-fast-kf 1s ease-in-out infinite; }

        @keyframes aura-halo-medium-kf {
          0%, 100% { transform: scale(1); opacity: 0.5; }
          50% { transform: scale(1.1); opacity: 0.85; }
        }
        .aura-halo-medium { animation: aura-halo-medium-kf 2.5s ease-in-out infinite; }

        @keyframes aura-halo-soft-kf {
          0%, 100% { transform: scale(1); opacity: 0.5; }
          50% { transform: scale(1.06); opacity: 0.75; }
        }
        .aura-halo-soft { animation: aura-halo-soft-kf 2s ease-in-out infinite; }

        @keyframes aura-halo-subtle-kf {
          0%, 100% { transform: scale(1); opacity: 0.3; }
          50% { transform: scale(1.04); opacity: 0.5; }
        }
        .aura-halo-subtle { animation: aura-halo-subtle-kf 2s ease-in-out infinite; }

        /* ─── Core shimmer ─── */
        @keyframes aura-core-shimmer {
          0%, 100% { opacity: 0.7; transform: scale(1); }
          50% { opacity: 1; transform: scale(1.1); }
        }

        /* ─── Thinking ring secondary spin ─── */
        @keyframes aura-ring-secondary {
          from { transform: rotate(0deg); }
          to { transform: rotate(-360deg); }
        }

        /* ─── Particle orbit ─── */
        @keyframes aura-orbit {
          from { transform: rotate(0deg) translateX(80px) rotate(0deg); }
          to { transform: rotate(360deg) translateX(80px) rotate(-360deg); }
        }
        @keyframes aura-orbit-reverse {
          from { transform: rotate(180deg) translateX(75px) rotate(-180deg); }
          to { transform: rotate(-180deg) translateX(75px) rotate(180deg); }
        }
      `}</style>

      <div
        className="relative flex items-center justify-center"
        style={{ width: containerSize, height: containerSize }}
      >
        {/* ════════ Layer 1: Outer ambient glow ════════ */}
        <div
          className="absolute inset-0 rounded-full transition-all duration-500"
          style={{
            background: `radial-gradient(circle, ${config.glowColor} 0%, transparent 70%)`,
            filter: "blur(30px)",
            transform: `scale(${glowExpand})`,
            opacity: isError ? 0.3 : 0.5,
          }}
        />

        {/* ════════ Layer 2: Diffused color wash ════════ */}
        <div
          className="absolute rounded-full transition-all duration-500"
          style={{
            width: washSize,
            height: washSize,
            left: washOffset,
            top: washOffset,
            background: `radial-gradient(circle at 40% 40%, ${config.color}25 0%, ${config.color}08 50%, transparent 80%)`,
            filter: "blur(15px)",
          }}
        />

        {/* ════════ Layer 3: Pulsing halo ring ════════ */}
        <div
          className={`absolute rounded-full transition-all duration-500 ${haloAnimClass}`}
          style={{
            width: haloSize,
            height: haloSize,
            left: haloOffset,
            top: haloOffset,
            border: `1.5px solid ${config.color}30`,
            boxShadow: `
              0 0 15px ${config.color}15,
              inset 0 0 15px ${config.color}08
            `,
          }}
        />

        {/* ════════ Layer 4: Thinking outer ring ════════ */}
        {isThinking && (
          <>
            <div
              className="absolute rounded-full aura-spin"
              style={{
                width: ringSize,
                height: ringSize,
                left: ringOffset,
                top: ringOffset,
                border: "2px solid transparent",
                borderTopColor: config.color,
                borderRightColor: `${config.color}60`,
              }}
            />
            <div
              className="absolute rounded-full"
              style={{
                width: ring2Size,
                height: ring2Size,
                left: ring2Offset,
                top: ring2Offset,
                border: "1px solid transparent",
                borderBottomColor: `${config.color}50`,
                borderLeftColor: `${config.color}30`,
                animation: "aura-ring-secondary 4s linear infinite",
              }}
            />
          </>
        )}

        {/* ════════ Layer 5: Orbiting particles ════════ */}
        {(state === "listening" || state === "conversing" || state === "speaking") && (
          <>
            <div
              className="absolute"
              style={{
                width: 5,
                height: 5,
                left: "50%",
                top: "50%",
                marginLeft: -2.5,
                marginTop: -2.5,
                borderRadius: "50%",
                background: config.coreColor,
                boxShadow: `0 0 6px ${config.color}`,
                animation: `aura-orbit ${state === "listening" ? "3s" : "5s"} linear infinite`,
                opacity: 0.7,
              }}
            />
            <div
              className="absolute"
              style={{
                width: 3,
                height: 3,
                left: "50%",
                top: "50%",
                marginLeft: -1.5,
                marginTop: -1.5,
                borderRadius: "50%",
                background: config.coreColor,
                boxShadow: `0 0 4px ${config.color}`,
                animation: `aura-orbit-reverse ${state === "listening" ? "4s" : "6s"} linear infinite`,
                opacity: 0.5,
              }}
            />
          </>
        )}

        {/* ════════ Layer 6: Main orb sphere ════════ */}
        <div
          className={`absolute rounded-full transition-all duration-500 ${animClass}`}
          style={{
            width: size,
            height: size,
            left: orbOffset,
            top: orbOffset,
            background: config.gradient,
            boxShadow: `
              0 0 30px ${config.color}30,
              0 0 60px ${config.color}18,
              inset 0 0 30px ${config.color}20,
              inset -6px -6px 20px ${config.color}15
            `,
            transform: `scale(${orbScale})`,
          }}
        />

        {/* ════════ Layer 7: Glass reflection highlight ════════ */}
        <div
          className="absolute rounded-full pointer-events-none transition-all duration-500"
          style={{
            width: highlightW,
            height: highlightH,
            left: highlightLeft,
            top: highlightTop,
            background: `linear-gradient(
              180deg,
              rgba(255, 255, 255, 0.18) 0%,
              rgba(255, 255, 255, 0.06) 60%,
              transparent 100%
            )`,
            borderRadius: "50%",
            transform: `scale(${orbScale})`,
          }}
        />

        {/* ════════ Layer 8: Inner bright core ════════ */}
        <div
          className="absolute rounded-full pointer-events-none transition-all duration-500"
          style={{
            width: coreSize,
            height: coreSize,
            left: coreLeft,
            top: coreTop,
            background: `radial-gradient(
              circle,
              ${config.coreColor}cc 0%,
              ${config.coreColor}40 40%,
              transparent 70%
            )`,
            filter: "blur(6px)",
            animation: isError ? "none" : "aura-core-shimmer 3s ease-in-out infinite",
            transform: `scale(${orbScale})`,
          }}
        />

        {/* ════════ Layer 9: Specular hot spot ════════ */}
        <div
          className="absolute rounded-full pointer-events-none transition-all duration-500"
          style={{
            width: specSize,
            height: specSize,
            left: specLeft,
            top: specTop,
            background: `radial-gradient(
              circle,
              rgba(255, 255, 255, 0.4) 0%,
              rgba(255, 255, 255, 0.1) 50%,
              transparent 100%
            )`,
            filter: "blur(3px)",
            transform: `scale(${orbScale})`,
          }}
        />
      </div>
    </>
  );
}
