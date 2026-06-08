"use client";

import { usePathname } from "next/navigation";
import { useAuthContext } from "@/context/AuthContext";
import { Sidebar } from "@/components/Sidebar";

const NO_SIDEBAR_ROUTES = ["/login", "/settings/callback"];

export function LayoutShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { user, loading } = useAuthContext();

  const hideSidebar =
    NO_SIDEBAR_ROUTES.some((r) => pathname.startsWith(r)) ||
    loading ||
    !user;

  if (hideSidebar) {
    return <>{children}</>;
  }

  return (
    <div style={{ display: "flex", height: "100vh", overflow: "hidden" }}>
      <Sidebar />
      <main
        style={{
          flex: 1,
          overflow: "auto",
          background: "var(--bg-warm)",
          minWidth: 0,
        }}
      >
        {children}
      </main>
    </div>
  );
}
