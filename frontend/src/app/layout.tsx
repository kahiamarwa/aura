import type { Metadata } from "next";
import { Inter } from "next/font/google";
import { AuthProvider } from "@/context/AuthContext";
import { AuraSessionProvider } from "@/context/AuraSessionContext";
import { ThemeProvider } from "@/context/ThemeContext";
import { LayoutShell } from "@/components/LayoutShell";
import "./globals.css";

const inter = Inter({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "AURA",
  description: "AURA - Assistant Vocal IA",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="fr">
      <body
        className={`${inter.variable} antialiased`}
        style={{ background: "var(--bg)" }}
      >
        <AuthProvider>
          <ThemeProvider>
            <AuraSessionProvider>
              <LayoutShell>{children}</LayoutShell>
            </AuraSessionProvider>
          </ThemeProvider>
        </AuthProvider>
      </body>
    </html>
  );
}
