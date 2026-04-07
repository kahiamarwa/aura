"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuthContext } from "@/context/AuthContext";
import {
  fetchIntegrations,
  disconnectIntegration,
  handleOAuthCallback,
  callEdgeFunction,
  getGmailOAuthUrl,
  getOutlookOAuthUrl,
  getHubSpotOAuthUrl,
  getSlackOAuthUrl,
  type Integration,
  type Provider,
} from "@/lib/integrations";
import { fetchSettings, updateSettings } from "@/lib/api";
import { useTheme } from "@/context/ThemeContext";
import { supabase } from "@/lib/supabase";

const OAUTH_URLS: Partial<Record<Provider, () => string>> = {
  gmail: getGmailOAuthUrl,
  outlook: getOutlookOAuthUrl,
  hubspot: getHubSpotOAuthUrl,
  slack: getSlackOAuthUrl,
};

type Section = "general" | "account" | "voice" | "passive" | "connectors" | "danger";

const SECTIONS: { id: Section; label: string }[] = [
  { id: "general", label: "General" },
  { id: "account", label: "Compte" },
  { id: "voice", label: "Assistant vocal" },
  { id: "passive", label: "Ecoute passive" },
  { id: "connectors", label: "Connecteurs" },
  { id: "danger", label: "Zone danger" },
];

export default function SettingsPage() {
  const router = useRouter();
  const { user, session, loading: authLoading, signOut } = useAuthContext();
  const { theme: currentTheme, setTheme: applyTheme } = useTheme();
  const [activeSection, setActiveSection] = useState<Section>("general");
  const [integrations, setIntegrations] = useState<Integration[]>([]);
  const [loading, setLoading] = useState(true);
  const [disconnecting, setDisconnecting] = useState<Provider | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [settingsLoaded, setSettingsLoaded] = useState(false);

  // Local settings state
  const [langue, setLangue] = useState("fr");
  const [theme, setThemeLocal] = useState("light");
  const [timezone, setTimezone] = useState("Europe/Paris");
  const [notifications, setNotifications] = useState(true);
  const [wakeWord, setWakeWord] = useState(true);
  const [voix, setVoix] = useState("alloy");
  const [vitesse, setVitesse] = useState("1.0");
  const [bargeIn, setBargeIn] = useState(true);
  const [continuite, setContinuite] = useState(true);
  const [sonConfirmation, setSonConfirmation] = useState(true);
  const [passiveActive, setPassiveActive] = useState(true);
  const [resumesAuto, setResumesAuto] = useState(true);
  const [passiveTimeout, setPassiveTimeout] = useState("30");
  const [retention, setRetention] = useState("7");
  const [langueTranscription, setLangueTranscription] = useState("fr");

  const [connecting, setConnecting] = useState(false);

  // Logo upload state
  const [logoPath, setLogoPath] = useState<string | null>(null);
  const [logoUploading, setLogoUploading] = useState(false);
  const [logoPreview, setLogoPreview] = useState<string | null>(null);

  // Twilio config form
  const [showTwilioForm, setShowTwilioForm] = useState(false);
  const [twilioSid, setTwilioSid] = useState("");
  const [twilioToken, setTwilioToken] = useState("");
  const [twilioPhone, setTwilioPhone] = useState("");
  const [twilioSaving, setTwilioSaving] = useState(false);

  // WhatsApp config form
  const [showWhatsappForm, setShowWhatsappForm] = useState(false);
  const [waToken, setWaToken] = useState("");
  const [waPhoneId, setWaPhoneId] = useState("");
  const [waSaving, setWaSaving] = useState(false);
  const [waMethod, setWaMethod] = useState<"embedded" | "manual">("embedded");
  const [fbReady, setFbReady] = useState(false);

  // Theme change handler — applies immediately + updates local state
  const handleThemeChange = useCallback((value: string) => {
    setThemeLocal(value);
    if (value === "light" || value === "dark") {
      applyTheme(value);
    }
  }, [applyTheme]);

  // Logo upload handler
  const handleLogoUpload = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file || !user?.id || !session?.access_token) return;
    if (file.size > 500 * 1024) {
      setError("Logo trop volumineux (max 500 Ko)");
      return;
    }
    if (!file.type.startsWith("image/")) {
      setError("Format invalide — utilisez PNG ou JPG");
      return;
    }
    setLogoUploading(true);
    setError(null);
    try {
      const path = `${user.id}/logo${file.name.substring(file.name.lastIndexOf("."))}`;
      const { error: upErr } = await supabase.storage.from("logos").upload(path, file, { upsert: true });
      if (upErr) throw upErr;
      setLogoPath(path);
      setLogoPreview(URL.createObjectURL(file));
      await updateSettings(session.access_token, { logo_path: path });
      setSuccessMsg("Logo sauvegarde");
      setTimeout(() => setSuccessMsg(null), 3000);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erreur upload logo");
    } finally {
      setLogoUploading(false);
    }
  }, [user?.id, session?.access_token]);

  const handleLogoDelete = useCallback(async () => {
    if (!logoPath || !session?.access_token) return;
    try {
      await supabase.storage.from("logos").remove([logoPath]);
      await updateSettings(session.access_token, { logo_path: null });
      setLogoPath(null);
      setLogoPreview(null);
      setSuccessMsg("Logo supprime");
      setTimeout(() => setSuccessMsg(null), 3000);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erreur suppression logo");
    }
  }, [logoPath, session?.access_token]);

  // Load settings from backend on mount
  useEffect(() => {
    if (!session?.access_token) return;
    fetchSettings(session.access_token)
      .then((data) => {
        if (data.langue) setLangue(data.langue);
        if (data.theme) {
          setThemeLocal(data.theme);
          if (data.theme === "light" || data.theme === "dark") {
            applyTheme(data.theme);
          }
        }
        if (data.timezone) setTimezone(data.timezone);
        if (typeof data.notifications === "boolean") setNotifications(data.notifications);
        if (typeof data.wake_word === "boolean") setWakeWord(data.wake_word);
        if (data.voix) setVoix(data.voix);
        if (data.vitesse) setVitesse(data.vitesse);
        if (typeof data.barge_in === "boolean") setBargeIn(data.barge_in);
        if (typeof data.continuite === "boolean") setContinuite(data.continuite);
        if (typeof data.son_confirmation === "boolean") setSonConfirmation(data.son_confirmation);
        if (typeof data.passive_active === "boolean") setPassiveActive(data.passive_active);
        if (typeof data.resumes_auto === "boolean") setResumesAuto(data.resumes_auto);
        if (data.passive_timeout) setPassiveTimeout(data.passive_timeout);
        if (data.retention) setRetention(data.retention);
        if (data.langue_transcription) setLangueTranscription(data.langue_transcription);
        if (data.logo_path) {
          setLogoPath(data.logo_path);
          // Public bucket — build direct URL
          const publicUrl = `${process.env.NEXT_PUBLIC_SUPABASE_URL}/storage/v1/object/public/logos/${data.logo_path}`;
          setLogoPreview(publicUrl);
        }
        setSettingsLoaded(true);
      })
      .catch((err) => {
        console.error("[settings] Failed to load settings:", err);
        setSettingsLoaded(true); // use defaults
      });
  }, [session?.access_token, applyTheme]);

  // Save settings to backend
  const handleSaveSettings = async () => {
    if (!session?.access_token) return;
    setSaving(true);
    setError(null);
    try {
      await updateSettings(session.access_token, {
        langue,
        theme,
        timezone,
        notifications,
        wake_word: wakeWord,
        voix,
        vitesse,
        barge_in: bargeIn,
        continuite,
        son_confirmation: sonConfirmation,
        passive_active: passiveActive,
        resumes_auto: resumesAuto,
        passive_timeout: passiveTimeout,
        retention,
        langue_transcription: langueTranscription,
      });
      setSuccessMsg("Parametres sauvegardes");
      setTimeout(() => setSuccessMsg(null), 3000);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erreur lors de la sauvegarde");
    } finally {
      setSaving(false);
    }
  };

  // Process OAuth callback from popup
  const processOAuthCallback = useCallback(
    async (provider: string, code: string) => {
      if (!session?.access_token) return;
      setConnecting(true);
      setError(null);
      try {
        await handleOAuthCallback(provider, code, session.access_token);
        // Refetch integrations to update status
        const updated = await fetchIntegrations(session.access_token);
        setIntegrations(updated);
        setSuccessMsg(`${provider} connecte avec succes`);
        setTimeout(() => setSuccessMsg(null), 4000);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Erreur de connexion OAuth");
      } finally {
        setConnecting(false);
      }
    },
    [session?.access_token]
  );

  // Listen for OAuth callback via postMessage (popup sends this)
  useEffect(() => {
    function handleMessage(event: MessageEvent) {
      if (event.origin !== window.location.origin) return;
      if (event.data?.type !== "oauth-callback") return;
      processOAuthCallback(event.data.provider, event.data.code);
    }
    window.addEventListener("message", handleMessage);
    return () => window.removeEventListener("message", handleMessage);
  }, [processOAuthCallback]);

  // Listen for OAuth callback via localStorage (fallback when popup blocks postMessage)
  useEffect(() => {
    function handleStorage(event: StorageEvent) {
      if (event.key !== "oauth-callback" || !event.newValue) return;
      try {
        const data = JSON.parse(event.newValue);
        if (data.type === "oauth-callback" && data.provider && data.code) {
          processOAuthCallback(data.provider, data.code);
        }
      } catch {
        // ignore parse errors
      }
      localStorage.removeItem("oauth-callback");
    }
    window.addEventListener("storage", handleStorage);
    return () => window.removeEventListener("storage", handleStorage);
  }, [processOAuthCallback]);

  // Redirect if not authenticated
  useEffect(() => {
    if (!authLoading && !user) {
      router.replace("/login");
    }
  }, [authLoading, user, router]);

  // Load integrations
  useEffect(() => {
    if (!session?.access_token) return;

    setLoading(true);
    fetchIntegrations(session.access_token)
      .then(setIntegrations)
      .catch((err) => {
        console.error("[settings] Failed to load integrations:", err);
        setError("Impossible de charger les integrations");
      })
      .finally(() => setLoading(false));
  }, [session?.access_token]);

  const handleConnect = (provider: Provider) => {
    if (provider === "twilio") {
      setShowTwilioForm(true);
      return;
    }
    if (provider === "whatsapp") {
      setShowWhatsappForm(true);
      return;
    }
    const urlFn = OAUTH_URLS[provider];
    if (urlFn) {
      window.open(urlFn(), `${provider}-oauth`, "width=500,height=650,left=200,top=100");
    }
  };

  const handleTwilioSave = async () => {
    if (!session?.access_token || !twilioSid || !twilioToken || !twilioPhone) return;
    setTwilioSaving(true);
    setError(null);
    try {
      const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL!;
      const res = await fetch(`${supabaseUrl}/functions/v1/send-sms`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${session.access_token}`,
        },
        body: JSON.stringify({
          action: "save-config",
          account_sid: twilioSid,
          auth_token: twilioToken,
          phone_number: twilioPhone,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Erreur");
      setIntegrations((prev) =>
        prev.map((i) =>
          i.provider === "twilio"
            ? { ...i, connected: true, detail: data.phone_number }
            : i
        )
      );
      setShowTwilioForm(false);
      setTwilioSid("");
      setTwilioToken("");
      setTwilioPhone("");
      setSuccessMsg("Twilio connecte avec succes");
      setTimeout(() => setSuccessMsg(null), 3000);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erreur de configuration Twilio");
    } finally {
      setTwilioSaving(false);
    }
  };

  const handleWhatsappSave = async () => {
    if (!session?.access_token || !waToken || !waPhoneId) return;
    setWaSaving(true);
    setError(null);
    try {
      const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL!;
      const res = await fetch(`${supabaseUrl}/functions/v1/send-whatsapp`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${session.access_token}`,
        },
        body: JSON.stringify({
          action: "save-config",
          access_token: waToken,
          phone_number_id: waPhoneId,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Erreur");
      setIntegrations((prev) =>
        prev.map((i) =>
          i.provider === "whatsapp"
            ? { ...i, connected: true, detail: data.display_phone }
            : i
        )
      );
      setShowWhatsappForm(false);
      setWaToken("");
      setWaPhoneId("");
      setSuccessMsg("WhatsApp connecte avec succes");
      setTimeout(() => setSuccessMsg(null), 3000);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erreur de configuration WhatsApp");
    } finally {
      setWaSaving(false);
    }
  };

  // Load Facebook JS SDK for WhatsApp Embedded Signup
  useEffect(() => {
    if (activeSection !== "connectors") return;
    // deno-lint-ignore no-explicit-any
    if ((window as any).FB) { setFbReady(true); return; }

    (window as any).fbAsyncInit = function () {
      (window as any).FB.init({
        appId: process.env.NEXT_PUBLIC_META_APP_ID,
        cookie: true,
        xfbml: false,
        version: "v22.0",
      });
      setFbReady(true);
    };

    const script = document.createElement("script");
    script.src = "https://connect.facebook.net/en_US/sdk.js";
    script.async = true;
    script.defer = true;
    document.body.appendChild(script);
  }, [activeSection]);

  // WhatsApp Embedded Signup via Facebook Login
  const handleWhatsappEmbeddedSignup = () => {
    const FB = (window as any).FB;
    if (!FB || !fbReady) {
      setError("Facebook SDK non charge. Veuillez reessayer.");
      return;
    }
    if (!process.env.NEXT_PUBLIC_META_CONFIG_ID) {
      setError("Configuration Meta manquante (NEXT_PUBLIC_META_CONFIG_ID).");
      return;
    }

    // FB.login requires HTTPS — check before calling
    if (window.location.protocol !== "https:") {
      setError("Facebook Login requiert HTTPS. Utilisez l'onglet 'Configuration manuelle' en dev local, ou accedez via HTTPS.");
      return;
    }

    setConnecting(true);
    setError(null);

    // Capture WABA info from Embedded Signup session event
    let signupInfo: { waba_id?: string; phone_number_id?: string } = {};

    const messageListener = (event: MessageEvent) => {
      if (
        event.origin !== "https://www.facebook.com" &&
        event.origin !== "https://web.facebook.com"
      ) return;

      try {
        const data = typeof event.data === "string" ? JSON.parse(event.data) : event.data;
        if (data.type === "WA_EMBEDDED_SIGNUP") {
          signupInfo = data.data || {};
          console.log("[WhatsApp] Embedded Signup info received:", signupInfo);
        }
      } catch {
        // Ignore non-JSON messages
      }
    };
    window.addEventListener("message", messageListener);

    // FB.login callback must be a plain function (not async)
    FB.login(
      (response: any) => {
        if (response.authResponse?.code) {
          const code = response.authResponse.code;

          // Wait briefly for the WA_EMBEDDED_SIGNUP message event (may arrive after FB.login callback)
          const sendToBackend = () => {
            window.removeEventListener("message", messageListener);
            console.log("[WhatsApp] Sending to backend:", { waba_id: signupInfo.waba_id, phone_number_id: signupInfo.phone_number_id });
            callEdgeFunction(
              "send-whatsapp",
              {
                action: "oauth-callback",
                code,
                waba_id: signupInfo.waba_id,
                phone_number_id: signupInfo.phone_number_id,
              },
              session!.access_token
            )
              .then(() => fetchIntegrations(session!.access_token))
              .then((updated) => {
                setIntegrations(updated);
                setShowWhatsappForm(false);
                setSuccessMsg("WhatsApp connecte avec succes");
                setTimeout(() => setSuccessMsg(null), 4000);
              })
              .catch((err) => {
                setError(err instanceof Error ? err.message : "Erreur de connexion WhatsApp");
              })
              .finally(() => setConnecting(false));
          };

          // If signup info already captured, send immediately; otherwise wait 1s
          if (signupInfo.waba_id && signupInfo.phone_number_id) {
            sendToBackend();
          } else {
            console.log("[WhatsApp] Waiting for Embedded Signup session event...");
            setTimeout(sendToBackend, 1000);
          }
        } else {
          window.removeEventListener("message", messageListener);
          setError("Connexion WhatsApp annulee.");
          setConnecting(false);
        }
      },
      {
        config_id: process.env.NEXT_PUBLIC_META_CONFIG_ID,
        response_type: "code",
        override_default_response_type: true,
        extras: {
          setup: {},
          featureType: "",
          sessionInfoVersion: 2,
        },
      }
    );
  };

  const handleDisconnect = async (provider: Provider) => {
    if (!session?.access_token) return;
    if (!confirm("Voulez-vous vraiment deconnecter cette integration ?")) return;

    setDisconnecting(provider);
    setError(null);
    try {
      await disconnectIntegration(provider, session.access_token);
      setIntegrations((prev) =>
        prev.map((i) =>
          i.provider === provider
            ? { ...i, connected: false, detail: undefined, connectedAt: undefined }
            : i
        )
      );
      setSuccessMsg(`${provider} deconnecte avec succes`);
      setTimeout(() => setSuccessMsg(null), 3000);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erreur de deconnexion");
    } finally {
      setDisconnecting(null);
    }
  };

  if (authLoading || !user) {
    return (
      <div
        className="flex items-center justify-center"
        style={{ background: "var(--bg-warm)", height: "100vh" }}
      >
        <div
          className="h-16 w-16 animate-spin rounded-full"
          style={{
            border: "3px solid var(--border)",
            borderTopColor: "var(--orange)",
            borderRightColor: "var(--orange-light)",
          }}
        />
      </div>
    );
  }

  return (
    <div
      style={{
        display: "flex",
        height: "100vh",
        background: "var(--bg-warm)",
      }}
    >
      {/* Left nav */}
      <nav
        style={{
          width: 220,
          minWidth: 220,
          borderRight: "1px solid var(--border-light)",
          padding: "32px 0",
          display: "flex",
          flexDirection: "column",
          gap: 2,
        }}
      >
        <h1
          style={{
            fontSize: 20,
            fontWeight: 700,
            color: "var(--text)",
            padding: "0 24px 24px",
            margin: 0,
          }}
        >
          Parametres
        </h1>
        {SECTIONS.map((s) => {
          const isActive = activeSection === s.id;
          return (
            <button
              key={s.id}
              onClick={() => setActiveSection(s.id)}
              style={{
                display: "block",
                width: "100%",
                textAlign: "left",
                padding: "10px 24px",
                fontSize: 14,
                fontWeight: isActive ? 600 : 400,
                color: isActive ? "var(--orange)" : "var(--text-secondary)",
                background: "transparent",
                border: "none",
                borderLeft: isActive ? "2px solid var(--orange)" : "2px solid transparent",
                cursor: "pointer",
                transition: "all 0.15s ease",
              }}
              onMouseEnter={(e) => {
                if (!isActive) {
                  e.currentTarget.style.color = "var(--text)";
                  e.currentTarget.style.background = "var(--surface-hover)";
                }
              }}
              onMouseLeave={(e) => {
                if (!isActive) {
                  e.currentTarget.style.color = "var(--text-secondary)";
                  e.currentTarget.style.background = "transparent";
                }
              }}
            >
              {s.label}
            </button>
          );
        })}
      </nav>

      {/* Main content */}
      <div
        style={{
          flex: 1,
          overflow: "auto",
          padding: "32px 40px",
          maxWidth: 700,
        }}
      >
        {/* Success message */}
        {successMsg && (
          <div
            style={{
              marginBottom: 20,
              padding: "12px 16px",
              borderRadius: 10,
              background: "rgba(45,158,106,0.08)",
              border: "1px solid rgba(45,158,106,0.2)",
              color: "var(--green)",
              fontSize: 14,
            }}
          >
            {successMsg}
          </div>
        )}

        {/* Error message */}
        {error && (
          <div
            style={{
              marginBottom: 20,
              padding: "12px 16px",
              borderRadius: 10,
              background: "rgba(212,64,64,0.06)",
              border: "1px solid rgba(212,64,64,0.15)",
              color: "var(--red)",
              fontSize: 14,
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
            }}
          >
            <span>{error}</span>
            <button
              onClick={() => setError(null)}
              style={{
                background: "none",
                border: "none",
                color: "var(--red)",
                cursor: "pointer",
                fontSize: 12,
                textDecoration: "underline",
              }}
            >
              Fermer
            </button>
          </div>
        )}

        {/* ─── General Section ─── */}
        {activeSection === "general" && (
          <div>
            <SectionTitle>General</SectionTitle>
            <SettingRow
              label="Langue"
              description="Langue de l'interface"
            >
              <Select value={langue} onChange={setLangue} options={[
                { value: "fr", label: "Francais" },
                { value: "en", label: "English" },
                { value: "es", label: "Espanol" },
              ]} />
            </SettingRow>
            <SettingRow
              label="Theme"
              description="Apparence de l'application"
            >
              <Select value={theme} onChange={handleThemeChange} options={[
                { value: "light", label: "Clair" },
                { value: "dark", label: "Sombre" },
              ]} />
            </SettingRow>
            <SettingRow
              label="Fuseau horaire"
              description="Fuseau horaire pour les resumes et les notifications"
            >
              <Select value={timezone} onChange={setTimezone} options={[
                { value: "Europe/Paris", label: "Europe/Paris" },
                { value: "America/New_York", label: "America/New York" },
                { value: "America/Los_Angeles", label: "America/Los Angeles" },
                { value: "Asia/Tokyo", label: "Asia/Tokyo" },
              ]} />
            </SettingRow>
            <SettingRow
              label="Notifications"
              description="Recevoir des notifications push"
            >
              <Toggle checked={notifications} onChange={setNotifications} />
            </SettingRow>

            {/* Logo upload for reports */}
            <div style={{
              background: "var(--surface)",
              border: "1px solid var(--border-light)",
              borderRadius: 12,
              padding: 20,
              marginTop: 16,
            }}>
              <h3 style={{ fontSize: 15, fontWeight: 600, color: "var(--text)", margin: "0 0 4px" }}>
                Logo pour les rapports
              </h3>
              <p style={{ fontSize: 13, color: "var(--text-muted)", margin: "0 0 12px" }}>
                Ce logo sera automatiquement insere sur vos rapports PDF
              </p>
              <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
                {(logoPreview || logoPath) && (
                  <div style={{
                    width: 64, height: 64, borderRadius: 8,
                    border: "1px solid var(--border-light)",
                    overflow: "hidden", display: "flex", alignItems: "center", justifyContent: "center",
                    background: "var(--bg)",
                  }}>
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={logoPreview || ""}
                      alt="Logo"
                      style={{ maxWidth: "100%", maxHeight: "100%", objectFit: "contain" }}
                    />
                  </div>
                )}
                <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                  <label style={{
                    padding: "8px 16px", borderRadius: 8,
                    border: "1px solid var(--border)",
                    background: "var(--surface)",
                    color: "var(--text-secondary)",
                    fontSize: 13, fontWeight: 500, cursor: "pointer",
                    display: "inline-block", textAlign: "center",
                    opacity: logoUploading ? 0.5 : 1,
                  }}>
                    {logoUploading ? "Upload..." : logoPath ? "Changer" : "Choisir un logo"}
                    <input
                      type="file"
                      accept="image/png,image/jpeg,image/webp"
                      onChange={handleLogoUpload}
                      disabled={logoUploading}
                      style={{ display: "none" }}
                    />
                  </label>
                  {logoPath && (
                    <button
                      onClick={handleLogoDelete}
                      style={{
                        padding: "6px 12px", borderRadius: 8,
                        border: "1px solid var(--border)",
                        background: "transparent",
                        color: "var(--red, #e74c3c)",
                        fontSize: 12, cursor: "pointer",
                      }}
                    >
                      Supprimer
                    </button>
                  )}
                </div>
              </div>
            </div>
          </div>
        )}

        {/* ─── Account Section ─── */}
        {activeSection === "account" && (
          <div>
            <SectionTitle>Compte</SectionTitle>
            <div
              style={{
                background: "var(--surface)",
                border: "1px solid var(--border-light)",
                borderRadius: 12,
                padding: 20,
                marginBottom: 16,
              }}
            >
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                <div>
                  <p style={{ fontSize: 15, fontWeight: 600, color: "var(--text)", margin: 0 }}>
                    {user.email}
                  </p>
                  <p style={{ fontSize: 13, color: "var(--text-muted)", margin: "4px 0 0" }}>
                    {user.user_metadata?.full_name || ""}
                  </p>
                </div>
                <button
                  onClick={signOut}
                  style={{
                    padding: "8px 16px",
                    borderRadius: 8,
                    border: "1px solid var(--border)",
                    background: "var(--surface)",
                    color: "var(--text-secondary)",
                    fontSize: 13,
                    fontWeight: 500,
                    cursor: "pointer",
                    transition: "all 0.15s ease",
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.borderColor = "var(--red)";
                    e.currentTarget.style.color = "var(--red)";
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.borderColor = "var(--border)";
                    e.currentTarget.style.color = "var(--text-secondary)";
                  }}
                >
                  Deconnexion
                </button>
              </div>
            </div>
          </div>
        )}

        {/* ─── Voice Assistant Section ─── */}
        {activeSection === "voice" && (
          <div>
            <SectionTitle>Assistant vocal</SectionTitle>
            <SettingRow
              label="Mot de reveil"
              description="Activer la detection du mot de reveil 'Aura'"
            >
              <Toggle checked={wakeWord} onChange={setWakeWord} />
            </SettingRow>
            <SettingRow
              label="Voix"
              description="Voix de synthese vocale"
            >
              <Select value={voix} onChange={setVoix} options={[
                { value: "alloy", label: "Alloy" },
                { value: "echo", label: "Echo" },
                { value: "fable", label: "Fable" },
                { value: "onyx", label: "Onyx" },
                { value: "nova", label: "Nova" },
                { value: "shimmer", label: "Shimmer" },
              ]} />
            </SettingRow>
            <SettingRow
              label="Vitesse"
              description="Vitesse de la synthese vocale (0.5 - 2.0)"
            >
              <Select value={vitesse} onChange={setVitesse} options={[
                { value: "0.5", label: "0.5x" },
                { value: "0.75", label: "0.75x" },
                { value: "1.0", label: "1.0x" },
                { value: "1.25", label: "1.25x" },
                { value: "1.5", label: "1.5x" },
                { value: "2.0", label: "2.0x" },
              ]} />
            </SettingRow>
            <SettingRow
              label="Barge-in"
              description="Permettre d'interrompre Aura en parlant"
            >
              <Toggle checked={bargeIn} onChange={setBargeIn} />
            </SettingRow>
            <SettingRow
              label="Mode conversation continue"
              description="Rester en mode ecoute apres une reponse"
            >
              <Toggle checked={continuite} onChange={setContinuite} />
            </SettingRow>
            <SettingRow
              label="Son de confirmation"
              description="Jouer un son quand le mot de reveil est detecte"
            >
              <Toggle checked={sonConfirmation} onChange={setSonConfirmation} />
            </SettingRow>
          </div>
        )}

        {/* ─── Passive Listening Section ─── */}
        {activeSection === "passive" && (
          <div>
            <SectionTitle>Ecoute passive</SectionTitle>
            <SettingRow
              label="Ecoute passive"
              description="Ecouter en arriere-plan pour capturer le contexte"
            >
              <Toggle checked={passiveActive} onChange={setPassiveActive} />
            </SettingRow>
            <SettingRow
              label="Resumes automatiques"
              description="Generer automatiquement des resumes des conversations"
            >
              <Toggle checked={resumesAuto} onChange={setResumesAuto} />
            </SettingRow>
            <SettingRow
              label="Timeout (minutes)"
              description="Duree d'inactivite avant de couper l'ecoute"
            >
              <Select value={passiveTimeout} onChange={setPassiveTimeout} options={[
                { value: "15", label: "15 min" },
                { value: "30", label: "30 min" },
                { value: "60", label: "1 heure" },
                { value: "120", label: "2 heures" },
              ]} />
            </SettingRow>
            <SettingRow
              label="Retention (jours)"
              description="Duree de conservation des transcriptions passives"
            >
              <Select value={retention} onChange={setRetention} options={[
                { value: "1", label: "1 jour" },
                { value: "3", label: "3 jours" },
                { value: "7", label: "7 jours" },
                { value: "14", label: "14 jours" },
                { value: "30", label: "30 jours" },
              ]} />
            </SettingRow>
            <SettingRow
              label="Langue de transcription"
              description="Langue utilisee pour la transcription passive"
            >
              <Select value={langueTranscription} onChange={setLangueTranscription} options={[
                { value: "fr", label: "Francais" },
                { value: "en", label: "English" },
                { value: "auto", label: "Detection auto" },
              ]} />
            </SettingRow>
          </div>
        )}

        {/* ─── Connectors Section ─── */}
        {activeSection === "connectors" && (
          <div>
            <SectionTitle>Connecteurs</SectionTitle>
            <p style={{ fontSize: 14, color: "var(--text-secondary)", marginBottom: 20 }}>
              Connectez vos outils pour qu&apos;Aura puisse agir en votre nom
            </p>

            {loading ? (
              <div style={{ display: "flex", justifyContent: "center", padding: "40px 0" }}>
                <div
                  className="h-10 w-10 animate-spin rounded-full"
                  style={{
                    border: "3px solid #ddd6cc",
                    borderTopColor: "var(--orange)",
                    borderRightColor: "var(--orange-light)",
                  }}
                />
              </div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                {integrations.map((integration) => (
                  <IntegrationCard
                    key={integration.provider}
                    integration={integration}
                    onConnect={() => handleConnect(integration.provider)}
                    onDisconnect={() => handleDisconnect(integration.provider)}
                    isDisconnecting={disconnecting === integration.provider}
                  />
                ))}
              </div>
            )}

            {/* Twilio config form */}
            {showTwilioForm && (
              <div
                style={{
                  marginTop: 16,
                  padding: 20,
                  borderRadius: 12,
                  background: "var(--surface)",
                  border: "1px solid var(--border-light)",
                  borderLeft: "3px solid #e36b2b",
                }}
              >
                <h3 style={{ fontSize: 14, fontWeight: 600, color: "var(--text)", margin: "0 0 16px" }}>
                  Configuration Twilio
                </h3>
                <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                  <div>
                    <label style={{ fontSize: 12, fontWeight: 500, color: "var(--text-secondary)", display: "block", marginBottom: 4 }}>
                      Account SID
                    </label>
                    <input
                      type="text"
                      value={twilioSid}
                      onChange={(e) => setTwilioSid(e.target.value)}
                      placeholder="ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
                      style={{
                        width: "100%",
                        padding: "8px 12px",
                        borderRadius: 8,
                        border: "1px solid var(--border-light)",
                        background: "var(--bg)",
                        color: "var(--text)",
                        fontSize: 13,
                      }}
                    />
                  </div>
                  <div>
                    <label style={{ fontSize: 12, fontWeight: 500, color: "var(--text-secondary)", display: "block", marginBottom: 4 }}>
                      Auth Token
                    </label>
                    <input
                      type="password"
                      value={twilioToken}
                      onChange={(e) => setTwilioToken(e.target.value)}
                      placeholder="Votre Auth Token Twilio"
                      style={{
                        width: "100%",
                        padding: "8px 12px",
                        borderRadius: 8,
                        border: "1px solid var(--border-light)",
                        background: "var(--bg)",
                        color: "var(--text)",
                        fontSize: 13,
                      }}
                    />
                  </div>
                  <div>
                    <label style={{ fontSize: 12, fontWeight: 500, color: "var(--text-secondary)", display: "block", marginBottom: 4 }}>
                      Numero de telephone Twilio
                    </label>
                    <input
                      type="text"
                      value={twilioPhone}
                      onChange={(e) => setTwilioPhone(e.target.value)}
                      placeholder="+33612345678"
                      style={{
                        width: "100%",
                        padding: "8px 12px",
                        borderRadius: 8,
                        border: "1px solid var(--border-light)",
                        background: "var(--bg)",
                        color: "var(--text)",
                        fontSize: 13,
                      }}
                    />
                  </div>
                  <div style={{ display: "flex", gap: 8, marginTop: 4 }}>
                    <button
                      onClick={handleTwilioSave}
                      disabled={twilioSaving || !twilioSid || !twilioToken || !twilioPhone}
                      style={{
                        padding: "8px 20px",
                        borderRadius: 8,
                        border: "none",
                        background: "#e36b2b",
                        color: "white",
                        fontSize: 13,
                        fontWeight: 600,
                        cursor: twilioSaving ? "wait" : "pointer",
                        opacity: twilioSaving || !twilioSid || !twilioToken || !twilioPhone ? 0.6 : 1,
                      }}
                    >
                      {twilioSaving ? "Verification..." : "Connecter"}
                    </button>
                    <button
                      onClick={() => setShowTwilioForm(false)}
                      style={{
                        padding: "8px 20px",
                        borderRadius: 8,
                        border: "1px solid var(--border-light)",
                        background: "transparent",
                        color: "var(--text-secondary)",
                        fontSize: 13,
                        cursor: "pointer",
                      }}
                    >
                      Annuler
                    </button>
                  </div>
                </div>
              </div>
            )}

            {/* ── WhatsApp config form (dual method: Embedded Signup + Manual) ── */}
            {showWhatsappForm && (
              <div
                style={{
                  marginTop: 16,
                  padding: 20,
                  borderRadius: 12,
                  border: "1px solid var(--border-light)",
                  background: "var(--surface)",
                }}
              >
                <h4 style={{ margin: "0 0 12px", fontSize: 14, fontWeight: 600, color: "var(--text)" }}>
                  Configurer WhatsApp Business
                </h4>

                {/* ── Tab selector ── */}
                <div style={{ display: "flex", gap: 0, marginBottom: 16, borderBottom: "1px solid var(--border-light)" }}>
                  <button
                    onClick={() => setWaMethod("embedded")}
                    style={{
                      padding: "8px 16px",
                      fontSize: 13,
                      fontWeight: waMethod === "embedded" ? 600 : 400,
                      color: waMethod === "embedded" ? "#25D366" : "var(--text-secondary)",
                      background: "transparent",
                      border: "none",
                      borderBottom: waMethod === "embedded" ? "2px solid #25D366" : "2px solid transparent",
                      cursor: "pointer",
                      marginBottom: -1,
                    }}
                  >
                    Connexion Facebook
                  </button>
                  <button
                    onClick={() => setWaMethod("manual")}
                    style={{
                      padding: "8px 16px",
                      fontSize: 13,
                      fontWeight: waMethod === "manual" ? 600 : 400,
                      color: waMethod === "manual" ? "#25D366" : "var(--text-secondary)",
                      background: "transparent",
                      border: "none",
                      borderBottom: waMethod === "manual" ? "2px solid #25D366" : "2px solid transparent",
                      cursor: "pointer",
                      marginBottom: -1,
                    }}
                  >
                    Configuration manuelle
                  </button>
                </div>

                {/* ── Tab: Embedded Signup ── */}
                {waMethod === "embedded" && (
                  <div>
                    <p style={{ margin: "0 0 16px", fontSize: 12, color: "var(--text-muted)" }}>
                      Connectez votre compte WhatsApp Business en un clic via Facebook.
                      Aucune configuration manuelle requise.
                    </p>
                    <div style={{ display: "flex", gap: 8 }}>
                      <button
                        onClick={handleWhatsappEmbeddedSignup}
                        disabled={connecting || !fbReady}
                        style={{
                          padding: "10px 24px",
                          borderRadius: 8,
                          border: "none",
                          background: "#1877F2",
                          color: "white",
                          fontSize: 13,
                          fontWeight: 600,
                          cursor: connecting ? "wait" : "pointer",
                          opacity: connecting || !fbReady ? 0.6 : 1,
                          display: "flex",
                          alignItems: "center",
                          gap: 8,
                        }}
                      >
                        {connecting ? "Connexion en cours..." : "Se connecter avec Facebook"}
                      </button>
                      <button
                        onClick={() => setShowWhatsappForm(false)}
                        style={{
                          padding: "8px 20px",
                          borderRadius: 8,
                          border: "1px solid var(--border-light)",
                          background: "transparent",
                          color: "var(--text-secondary)",
                          fontSize: 13,
                          cursor: "pointer",
                        }}
                      >
                        Annuler
                      </button>
                    </div>
                    {!fbReady && (
                      <p style={{ margin: "8px 0 0", fontSize: 11, color: "var(--text-muted)" }}>
                        Chargement du SDK Facebook...
                      </p>
                    )}
                  </div>
                )}

                {/* ── Tab: Manual config ── */}
                {waMethod === "manual" && (
                  <div>
                    <p style={{ margin: "0 0 16px", fontSize: 12, color: "var(--text-muted)" }}>
                      Pour les utilisateurs avances. Entrez vos identifiants depuis le{" "}
                      <a
                        href="https://developers.facebook.com/"
                        target="_blank"
                        rel="noopener noreferrer"
                        style={{ color: "var(--orange)" }}
                      >
                        Meta for Developers Dashboard
                      </a>.
                    </p>
                    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                      <div>
                        <label style={{ display: "block", fontSize: 12, fontWeight: 500, color: "var(--text-secondary)", marginBottom: 4 }}>
                          Access Token (Permanent)
                        </label>
                        <input
                          type="password"
                          placeholder="EAAxxxxxxx..."
                          value={waToken}
                          onChange={(e) => setWaToken(e.target.value)}
                          style={{
                            width: "100%",
                            padding: "8px 12px",
                            borderRadius: 8,
                            border: "1px solid var(--border-light)",
                            background: "var(--bg)",
                            color: "var(--text)",
                            fontSize: 13,
                          }}
                        />
                      </div>
                      <div>
                        <label style={{ display: "block", fontSize: 12, fontWeight: 500, color: "var(--text-secondary)", marginBottom: 4 }}>
                          Phone Number ID
                        </label>
                        <input
                          type="text"
                          placeholder="1234567890..."
                          value={waPhoneId}
                          onChange={(e) => setWaPhoneId(e.target.value)}
                          style={{
                            width: "100%",
                            padding: "8px 12px",
                            borderRadius: 8,
                            border: "1px solid var(--border-light)",
                            background: "var(--bg)",
                            color: "var(--text)",
                            fontSize: 13,
                          }}
                        />
                      </div>
                      <div style={{ display: "flex", gap: 8, marginTop: 4 }}>
                        <button
                          onClick={handleWhatsappSave}
                          disabled={waSaving || !waToken || !waPhoneId}
                          style={{
                            padding: "8px 20px",
                            borderRadius: 8,
                            border: "none",
                            background: "#25D366",
                            color: "white",
                            fontSize: 13,
                            fontWeight: 600,
                            cursor: waSaving ? "wait" : "pointer",
                            opacity: waSaving || !waToken || !waPhoneId ? 0.6 : 1,
                          }}
                        >
                          {waSaving ? "Verification..." : "Connecter"}
                        </button>
                        <button
                          onClick={() => setShowWhatsappForm(false)}
                          style={{
                            padding: "8px 20px",
                            borderRadius: 8,
                            border: "1px solid var(--border-light)",
                            background: "transparent",
                            color: "var(--text-secondary)",
                            fontSize: 13,
                            cursor: "pointer",
                          }}
                        >
                          Annuler
                        </button>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {/* ─── Danger Zone Section ─── */}
        {activeSection === "danger" && (
          <div>
            <SectionTitle>Zone danger</SectionTitle>
            <p style={{ fontSize: 14, color: "var(--text-secondary)", marginBottom: 20 }}>
              Ces actions sont irreversibles. Procedez avec prudence.
            </p>

            <DangerAction
              label="Supprimer l'historique"
              description="Supprimer toutes les conversations et transcriptions"
              buttonLabel="Supprimer"
              onClick={() => {
                if (confirm("Etes-vous sur de vouloir supprimer tout l'historique ? Cette action est irreversible.")) {
                  setSuccessMsg("Historique supprime");
                  setTimeout(() => setSuccessMsg(null), 3000);
                }
              }}
            />
            <DangerAction
              label="Deconnecter tous les services"
              description="Revoquer l'acces a tous les connecteurs"
              buttonLabel="Deconnecter tout"
              onClick={() => {
                if (confirm("Etes-vous sur de vouloir deconnecter tous les services ?")) {
                  setSuccessMsg("Tous les services ont ete deconnectes");
                  setTimeout(() => setSuccessMsg(null), 3000);
                }
              }}
            />
            <DangerAction
              label="Supprimer le compte"
              description="Supprimer definitivement votre compte et toutes vos donnees"
              buttonLabel="Supprimer le compte"
              onClick={() => {
                if (confirm("ATTENTION : Cette action est definitive. Supprimer votre compte ?")) {
                  // Would call account deletion API
                }
              }}
            />
          </div>
        )}

        {/* Save button — visible on all sections except connectors/danger */}
        {activeSection !== "connectors" && activeSection !== "danger" && activeSection !== "account" && (
          <div style={{ marginTop: 32, display: "flex", justifyContent: "flex-end" }}>
            <button
              onClick={handleSaveSettings}
              disabled={saving}
              style={{
                padding: "10px 28px",
                borderRadius: 10,
                border: "none",
                background: "linear-gradient(135deg, var(--orange), var(--orange-light))",
                color: "#ffffff",
                fontSize: 14,
                fontWeight: 600,
                cursor: saving ? "not-allowed" : "pointer",
                opacity: saving ? 0.6 : 1,
                transition: "all 0.15s ease",
              }}
            >
              {saving ? "Sauvegarde..." : "Sauvegarder"}
            </button>
          </div>
        )}

        <p
          style={{
            textAlign: "center",
            fontSize: 12,
            color: "var(--text-muted)",
            marginTop: 48,
            paddingBottom: 16,
            fontWeight: 500,
            letterSpacing: "0.04em",
          }}
        >
          Propulse par Hallia
        </p>
      </div>
    </div>
  );
}

// ─── Reusable Sub-components ─────────────────────────────

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <h2
      style={{
        fontSize: 18,
        fontWeight: 700,
        color: "var(--text)",
        margin: "0 0 24px",
      }}
    >
      {children}
    </h2>
  );
}

function SettingRow({
  label,
  description,
  children,
}: {
  label: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "16px 0",
        borderBottom: "1px solid var(--border-light)",
      }}
    >
      <div style={{ flex: 1, minWidth: 0, paddingRight: 24 }}>
        <p style={{ fontSize: 14, fontWeight: 500, color: "var(--text)", margin: 0 }}>
          {label}
        </p>
        <p style={{ fontSize: 13, color: "var(--text-muted)", margin: "2px 0 0" }}>
          {description}
        </p>
      </div>
      {children}
    </div>
  );
}

function Toggle({
  checked,
  onChange,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <button
      onClick={() => onChange(!checked)}
      style={{
        width: 44,
        height: 24,
        borderRadius: 12,
        border: "none",
        background: checked ? "var(--orange)" : "var(--border)",
        position: "relative",
        cursor: "pointer",
        transition: "background 0.2s ease",
        flexShrink: 0,
      }}
    >
      <div
        style={{
          width: 18,
          height: 18,
          borderRadius: "50%",
          background: "var(--surface)",
          position: "absolute",
          top: 3,
          left: checked ? 23 : 3,
          transition: "left 0.2s ease",
          boxShadow: "0 1px 3px rgba(0,0,0,0.15)",
        }}
      />
    </button>
  );
}

function Select({
  value,
  onChange,
  options,
}: {
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
}) {
  const { theme: selectTheme } = useTheme();
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      style={{
        padding: "8px 12px",
        borderRadius: 8,
        border: "1px solid var(--border)",
        background: "var(--surface)",
        color: "var(--text)",
        fontSize: 13,
        cursor: "pointer",
        outline: "none",
        minWidth: 140,
        colorScheme: selectTheme === "dark" ? "dark" : "light",
      }}
    >
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  );
}

function DangerAction({
  label,
  description,
  buttonLabel,
  onClick,
}: {
  label: string;
  description: string;
  buttonLabel: string;
  onClick: () => void;
}) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "16px 20px",
        borderRadius: 12,
        background: "var(--surface)",
        border: "1px solid var(--border-light)",
        marginBottom: 12,
      }}
    >
      <div style={{ flex: 1, minWidth: 0, paddingRight: 16 }}>
        <p style={{ fontSize: 14, fontWeight: 500, color: "var(--text)", margin: 0 }}>
          {label}
        </p>
        <p style={{ fontSize: 13, color: "var(--text-muted)", margin: "2px 0 0" }}>
          {description}
        </p>
      </div>
      <button
        onClick={onClick}
        style={{
          padding: "8px 16px",
          borderRadius: 8,
          border: "1px solid rgba(212,64,64,0.3)",
          background: "rgba(212,64,64,0.06)",
          color: "var(--red)",
          fontSize: 13,
          fontWeight: 500,
          cursor: "pointer",
          transition: "all 0.15s ease",
          flexShrink: 0,
        }}
        onMouseEnter={(e) => {
          e.currentTarget.style.background = "rgba(212,64,64,0.12)";
          e.currentTarget.style.borderColor = "rgba(212,64,64,0.4)";
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.background = "rgba(212,64,64,0.06)";
          e.currentTarget.style.borderColor = "rgba(212,64,64,0.3)";
        }}
      >
        {buttonLabel}
      </button>
    </div>
  );
}

// ─── Integration Card Component ─────────────────────────────

function IntegrationCard({
  integration,
  onConnect,
  onDisconnect,
  isDisconnecting,
}: {
  integration: Integration;
  onConnect: () => void;
  onDisconnect: () => void;
  isDisconnecting: boolean;
}) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "16px 20px",
        borderRadius: 12,
        background: "var(--surface)",
        border: "1px solid var(--border-light)",
        borderLeft: integration.connected
          ? "3px solid var(--green)"
          : "3px solid var(--border)",
        transition: "all 0.15s ease",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
        <span style={{ fontSize: 28 }}>{integration.icon}</span>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <h3 style={{ fontSize: 14, fontWeight: 600, color: "var(--text)", margin: 0 }}>
              {integration.label}
            </h3>
            {integration.connected && (
              <span
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 5,
                  padding: "2px 8px",
                  borderRadius: 10,
                  fontSize: 11,
                  fontWeight: 600,
                  background: "rgba(45,158,106,0.1)",
                  color: "var(--green)",
                }}
              >
                <span
                  style={{
                    width: 6,
                    height: 6,
                    borderRadius: "50%",
                    background: "var(--green)",
                    display: "inline-block",
                  }}
                />
                Connecte
              </span>
            )}
          </div>
          <p style={{ fontSize: 12, color: "var(--text-muted)", margin: "3px 0 0" }}>
            {integration.connected && integration.detail
              ? integration.detail
              : integration.description}
          </p>
        </div>
      </div>

      {integration.connected ? (
        <button
          onClick={onDisconnect}
          disabled={isDisconnecting}
          style={{
            padding: "8px 14px",
            borderRadius: 8,
            border: "1px solid var(--border)",
            background: "var(--surface)",
            color: "var(--red)",
            fontSize: 12,
            fontWeight: 600,
            cursor: isDisconnecting ? "not-allowed" : "pointer",
            opacity: isDisconnecting ? 0.4 : 1,
            transition: "all 0.15s ease",
          }}
          onMouseEnter={(e) => {
            if (!isDisconnecting) {
              e.currentTarget.style.background = "rgba(212,64,64,0.06)";
              e.currentTarget.style.borderColor = "rgba(212,64,64,0.3)";
            }
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = "var(--surface)";
            e.currentTarget.style.borderColor = "var(--border)";
          }}
        >
          {isDisconnecting ? "..." : "Deconnecter"}
        </button>
      ) : (
        <button
          onClick={onConnect}
          style={{
            padding: "8px 18px",
            borderRadius: 8,
            border: "none",
            background: "linear-gradient(135deg, #e36b2b, #f08c42, #f5a623)",
            color: "#ffffff",
            fontSize: 12,
            fontWeight: 700,
            cursor: "pointer",
            transition: "all 0.15s ease",
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.boxShadow = "0 2px 12px rgba(227,107,43,0.25)";
            e.currentTarget.style.transform = "translateY(-1px)";
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.boxShadow = "none";
            e.currentTarget.style.transform = "translateY(0)";
          }}
        >
          Connecter
        </button>
      )}
    </div>
  );
}
