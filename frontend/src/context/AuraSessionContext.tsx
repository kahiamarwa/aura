"use client";

import { createContext, useContext, useEffect, useRef, type ReactNode } from "react";
import { useAuraSession } from "@/hooks/useAuraSession";
import { useAuthContext } from "@/context/AuthContext";

type AuraSessionReturn = ReturnType<typeof useAuraSession>;

const AuraSessionContext = createContext<AuraSessionReturn | null>(null);

export function AuraSessionProvider({ children }: { children: ReactNode }) {
  const session = useAuraSession();
  const { user, loading } = useAuthContext();
  const hasInitialized = useRef(false);

  // Auto-initialize when user is authenticated
  useEffect(() => {
    if (!loading && user && !hasInitialized.current && session.state === "initializing") {
      hasInitialized.current = true;
      session.initialize();
    }
  }, [loading, user, session.state, session.initialize]);

  // Stop services when user logs out
  useEffect(() => {
    if (!loading && !user && hasInitialized.current) {
      hasInitialized.current = false;
      session.cleanup?.();
    }
  }, [loading, user, session.cleanup]);

  return (
    <AuraSessionContext.Provider value={session}>
      {children}
    </AuraSessionContext.Provider>
  );
}

export function useAuraSessionContext() {
  const context = useContext(AuraSessionContext);
  if (!context) {
    throw new Error("useAuraSessionContext must be used within AuraSessionProvider");
  }
  return context;
}
