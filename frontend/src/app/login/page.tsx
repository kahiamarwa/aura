"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuthContext } from "@/context/AuthContext";

export default function LoginPage() {
  const router = useRouter();
  const { signIn, signUp, loading, error, clearError, user } = useAuthContext();
  const [isSignUp, setIsSignUp] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");

  // Redirect if already logged in
  useEffect(() => {
    if (user) {
      router.replace("/");
    }
  }, [user, router]);

  if (user) {
    return null;
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (isSignUp) {
      await signUp(email, password, name);
    } else {
      await signIn(email, password);
    }
  };

  const toggleMode = () => {
    setIsSignUp(!isSignUp);
    clearError();
  };

  return (
    <div
      className="relative flex min-h-screen items-center justify-center overflow-hidden px-4"
      style={{ background: "#0a0a0f" }}
    >
      {/* Background decoration - large blurred gradient circles */}
      <div
        className="pointer-events-none absolute"
        style={{
          width: 600,
          height: 600,
          top: "-15%",
          right: "-10%",
          borderRadius: "50%",
          background: "radial-gradient(circle, rgba(255,107,53,0.05) 0%, transparent 70%)",
          filter: "blur(80px)",
        }}
      />
      <div
        className="pointer-events-none absolute"
        style={{
          width: 500,
          height: 500,
          bottom: "-10%",
          left: "-8%",
          borderRadius: "50%",
          background: "radial-gradient(circle, rgba(255,167,38,0.04) 0%, transparent 70%)",
          filter: "blur(80px)",
        }}
      />

      <div className="relative z-10 w-full max-w-md">
        {/* Logo / Orb */}
        <div className="mb-8 flex flex-col items-center">
          <div
            className="mb-4 flex h-16 w-16 items-center justify-center rounded-full"
            style={{
              background: "radial-gradient(circle at 35% 35%, #ffa726, #ff6b35, #ff5722)",
              boxShadow:
                "0 0 30px rgba(255,107,53,0.4), 0 0 60px rgba(255,107,53,0.2), 0 0 90px rgba(255,107,53,0.1)",
              animation: "orbPulse 3s ease-in-out infinite",
            }}
          >
            <span className="text-lg font-bold text-white" style={{ textShadow: "0 1px 4px rgba(0,0,0,0.3)" }}>
              A
            </span>
          </div>
          <h1
            className="text-2xl font-bold tracking-[0.25em]"
            style={{
              background: "linear-gradient(135deg, #ff6b35, #ffa726)",
              WebkitBackgroundClip: "text",
              WebkitTextFillColor: "transparent",
              backgroundClip: "text",
            }}
          >
            AURA
          </h1>
          <p className="mt-1 text-sm" style={{ color: "rgba(255,255,255,0.35)" }}>
            Assistant Vocal IA
          </p>
        </div>

        {/* Card */}
        <div
          className="rounded-2xl border p-8 backdrop-blur-xl"
          style={{
            background: "rgba(255,255,255,0.03)",
            borderColor: "rgba(255,255,255,0.06)",
            boxShadow: "0 8px 32px rgba(0,0,0,0.3), inset 0 1px 0 rgba(255,255,255,0.04)",
          }}
        >
          <h2 className="mb-6 text-center text-lg font-semibold" style={{ color: "rgba(255,255,255,0.85)" }}>
            {isSignUp ? "Creer un compte" : "Se connecter"}
          </h2>

          <form onSubmit={handleSubmit} className="flex flex-col gap-4">
            {isSignUp && (
              <div>
                <label
                  className="mb-1.5 block text-xs font-medium tracking-wide"
                  style={{ color: "rgba(255,255,255,0.5)" }}
                >
                  Nom complet
                </label>
                <input
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Jean Dupont"
                  className="w-full rounded-xl border px-4 py-2.5 text-sm outline-none transition-colors"
                  style={{
                    background: "rgba(255,255,255,0.05)",
                    borderColor: "rgba(255,255,255,0.08)",
                    color: "#ffffff",
                  }}
                  onFocus={(e) => (e.target.style.borderColor = "#ff8c42")}
                  onBlur={(e) => (e.target.style.borderColor = "rgba(255,255,255,0.08)")}
                />
              </div>
            )}

            <div>
              <label
                className="mb-1.5 block text-xs font-medium tracking-wide"
                style={{ color: "rgba(255,255,255,0.5)" }}
              >
                Email
              </label>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="vous@exemple.com"
                required
                className="w-full rounded-xl border px-4 py-2.5 text-sm outline-none transition-colors"
                style={{
                  background: "rgba(255,255,255,0.05)",
                  borderColor: "rgba(255,255,255,0.08)",
                  color: "#ffffff",
                }}
                onFocus={(e) => (e.target.style.borderColor = "#ff8c42")}
                onBlur={(e) => (e.target.style.borderColor = "rgba(255,255,255,0.08)")}
              />
            </div>

            <div>
              <label
                className="mb-1.5 block text-xs font-medium tracking-wide"
                style={{ color: "rgba(255,255,255,0.5)" }}
              >
                Mot de passe
              </label>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
                required
                minLength={6}
                className="w-full rounded-xl border px-4 py-2.5 text-sm outline-none transition-colors"
                style={{
                  background: "rgba(255,255,255,0.05)",
                  borderColor: "rgba(255,255,255,0.08)",
                  color: "#ffffff",
                }}
                onFocus={(e) => (e.target.style.borderColor = "#ff8c42")}
                onBlur={(e) => (e.target.style.borderColor = "rgba(255,255,255,0.08)")}
              />
            </div>

            {error && (
              <p
                className="rounded-xl border px-3 py-2 text-sm"
                style={{
                  background: "rgba(255,71,87,0.1)",
                  borderColor: "rgba(255,71,87,0.2)",
                  color: "#ff6b6b",
                }}
              >
                {error}
              </p>
            )}

            <button
              type="submit"
              disabled={loading}
              className="mt-2 w-full rounded-xl py-3 text-sm font-semibold text-white transition-all disabled:opacity-50"
              style={{
                background: "linear-gradient(135deg, #ff6b35, #ff8c42, #ffa726)",
                boxShadow: "0 4px 20px rgba(255,107,53,0.3)",
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.boxShadow = "0 6px 30px rgba(255,107,53,0.5)";
                e.currentTarget.style.transform = "translateY(-1px)";
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.boxShadow = "0 4px 20px rgba(255,107,53,0.3)";
                e.currentTarget.style.transform = "translateY(0)";
              }}
            >
              {loading ? "Chargement..." : isSignUp ? "Creer le compte" : "Se connecter"}
            </button>
          </form>

          <div className="mt-6 text-center">
            <button
              onClick={toggleMode}
              className="text-sm font-medium transition-colors hover:underline"
              style={{ color: "#ff8c42" }}
            >
              {isSignUp ? "Deja un compte ? Se connecter" : "Pas de compte ? S'inscrire"}
            </button>
          </div>
        </div>

        <p className="mt-6 text-center text-xs" style={{ color: "rgba(255,255,255,0.15)" }}>
          Propulse par Hallia
        </p>
      </div>

      {/* Keyframe animation for orb pulse */}
      <style jsx>{`
        @keyframes orbPulse {
          0%,
          100% {
            box-shadow: 0 0 30px rgba(255, 107, 53, 0.4), 0 0 60px rgba(255, 107, 53, 0.2),
              0 0 90px rgba(255, 107, 53, 0.1);
          }
          50% {
            box-shadow: 0 0 40px rgba(255, 107, 53, 0.6), 0 0 80px rgba(255, 107, 53, 0.3),
              0 0 120px rgba(255, 107, 53, 0.15);
          }
        }
        input::placeholder {
          color: rgba(255, 255, 255, 0.25);
        }
      `}</style>
    </div>
  );
}
