"use client";

import { useState } from "react";
import type { ContextEntry } from "@/lib/contextBuffer";

interface ContextPanelProps {
  entries: ContextEntry[];
  currentPartial: string;
}

export function ContextPanel({ entries, currentPartial }: ContextPanelProps) {
  const [expanded, setExpanded] = useState(true);

  if (entries.length === 0 && !currentPartial) return null;

  return (
    <div style={{ width: "100%", maxWidth: 512 }}>
      {/* Header toggle */}
      <button
        onClick={() => setExpanded((v) => !v)}
        style={{
          display: "flex",
          width: "100%",
          alignItems: "center",
          gap: 8,
          padding: "8px 12px",
          textAlign: "left",
          background: expanded ? "#ffffff" : "transparent",
          border: expanded ? "1px solid #e8e2d9" : "1px solid transparent",
          borderRadius: expanded ? "16px 16px 0 0" : 16,
          cursor: "pointer",
          fontFamily: "inherit",
        }}
      >
        <span
          style={{
            fontSize: 10,
            color: "#a39e97",
            transform: expanded ? "rotate(90deg)" : "rotate(0deg)",
            transition: "transform 0.2s",
            display: "inline-block",
          }}
        >
          &#9654;
        </span>
        <span
          style={{
            fontSize: 11,
            fontWeight: 600,
            textTransform: "uppercase" as const,
            letterSpacing: "0.08em",
            color: "#a39e97",
          }}
        >
          Contexte ambiant
        </span>
        <span
          style={{
            marginLeft: "auto",
            fontSize: 10,
            fontFamily: "monospace",
            color: "#c4bdb4",
          }}
        >
          {entries.length} segment{entries.length !== 1 ? "s" : ""}
        </span>
      </button>

      {expanded && (
        <div
          style={{
            padding: "8px 16px 16px",
            background: "#ffffff",
            border: "1px solid #e8e2d9",
            borderTop: "none",
            borderRadius: "0 0 16px 16px",
          }}
        >
          <div style={{ maxHeight: 160, overflowY: "auto" }}>
            {entries.map((entry, i) => (
              <div
                key={i}
                style={{
                  display: "flex",
                  alignItems: "baseline",
                  gap: 8,
                  padding: "3px 0",
                }}
              >
                <span
                  style={{
                    flexShrink: 0,
                    fontSize: 10,
                    fontFamily: "monospace",
                    color: "#c4bdb4",
                  }}
                >
                  {entry.timestamp.toLocaleTimeString("fr-FR", {
                    hour: "2-digit",
                    minute: "2-digit",
                    second: "2-digit",
                  })}
                </span>
                <span style={{ fontSize: 12, color: "#6b6560", lineHeight: 1.4 }}>
                  {entry.text}
                </span>
              </div>
            ))}

            {currentPartial && (
              <div
                style={{
                  display: "flex",
                  alignItems: "baseline",
                  gap: 8,
                  padding: "3px 0",
                }}
              >
                <span style={{ flexShrink: 0, fontSize: 10, fontFamily: "monospace", color: "#c4bdb4" }}>
                  ...
                </span>
                <span style={{ fontSize: 12, fontStyle: "italic", color: "#a39e97", lineHeight: 1.4 }}>
                  {currentPartial}
                </span>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
