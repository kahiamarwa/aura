"use client";

interface PresentationViewerProps {
  url: string;
  fileName: string;
  onClose: () => void;
}

export function PresentationViewer({ url, fileName, onClose }: PresentationViewerProps) {
  const viewerUrl = `https://view.officeapps.live.com/op/embed.aspx?src=${encodeURIComponent(url)}`;

  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 1000,
        background: "rgba(0,0,0,0.6)",
        backdropFilter: "blur(4px)",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        padding: 24,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "100%",
          maxWidth: 960,
          height: "85vh",
          background: "#fff",
          borderRadius: 16,
          overflow: "hidden",
          display: "flex",
          flexDirection: "column",
          boxShadow: "0 20px 60px rgba(0,0,0,0.3)",
        }}
      >
        {/* Header */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "12px 20px",
            borderBottom: "1px solid #eee",
            background: "#faf6f1",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#d27832" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
              <polyline points="14 2 14 8 20 8" />
            </svg>
            <span style={{ fontSize: 14, fontWeight: 600, color: "#1a1a1a" }}>
              {fileName}
            </span>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <a
              href={url}
              download={fileName}
              target="_blank"
              rel="noopener noreferrer"
              onClick={(e) => e.stopPropagation()}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
                padding: "6px 14px",
                borderRadius: 8,
                border: "1px solid #ddd6cc",
                background: "#fff",
                color: "#6b6560",
                fontSize: 13,
                fontWeight: 500,
                textDecoration: "none",
                cursor: "pointer",
                transition: "background 0.2s",
              }}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                <polyline points="7 10 12 15 17 10" />
                <line x1="12" y1="15" x2="12" y2="3" />
              </svg>
              Télécharger
            </a>
            <button
              onClick={onClose}
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                width: 32,
                height: 32,
                borderRadius: 8,
                border: "none",
                background: "transparent",
                color: "#6b6560",
                cursor: "pointer",
                fontSize: 18,
                transition: "background 0.2s",
              }}
              onMouseEnter={(e) => { e.currentTarget.style.background = "rgba(0,0,0,0.06)"; }}
              onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
            >
              ✕
            </button>
          </div>
        </div>

        {/* Iframe viewer */}
        <iframe
          src={viewerUrl}
          style={{
            flex: 1,
            width: "100%",
            border: "none",
          }}
          title={`Aperçu de ${fileName}`}
          allowFullScreen
        />
      </div>
    </div>
  );
}
