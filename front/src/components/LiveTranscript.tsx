"use client";

interface LiveTranscriptProps {
  partialText: string;
  committedText: string;
  isActive: boolean;
}

export function LiveTranscript({
  partialText,
  committedText,
  isActive,
}: LiveTranscriptProps) {
  if (!isActive && !committedText && !partialText) return null;

  return (
    <div
      style={{
        width: "100%",
        maxWidth: 512,
        padding: 20,
        background: "#ffffff",
        border: "1px solid #e8e2d9",
        borderRadius: 16,
      }}
    >
      {committedText && (
        <p style={{ fontSize: 18, fontWeight: 300, color: "#1a1a1a", margin: 0 }}>
          {committedText}
        </p>
      )}
      {partialText && (
        <p style={{ fontSize: 18, fontWeight: 300, color: "#a39e97", margin: 0 }}>
          {partialText}
          {isActive && (
            <span
              style={{
                display: "inline-block",
                width: 2,
                height: 20,
                marginLeft: 2,
                background: "#e36b2b",
                animation: "orbIdle 1s ease-in-out infinite",
                verticalAlign: "text-bottom",
              }}
            />
          )}
        </p>
      )}
      {isActive && !partialText && !committedText && (
        <p style={{ fontSize: 18, fontWeight: 300, color: "#a39e97", margin: 0 }}>
          En écoute...
          <span
            style={{
              display: "inline-block",
              width: 2,
              height: 20,
              marginLeft: 2,
              background: "#e36b2b",
              animation: "orbIdle 1s ease-in-out infinite",
              verticalAlign: "text-bottom",
            }}
          />
        </p>
      )}
    </div>
  );
}
