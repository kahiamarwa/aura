-- ============================================================
-- Email Integrations — OAuth tokens for Gmail & Outlook
-- ============================================================

CREATE TABLE email_integrations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider TEXT NOT NULL CHECK (provider IN ('gmail', 'outlook')),
    email TEXT NOT NULL,
    access_token TEXT NOT NULL,
    refresh_token TEXT,
    token_expiry TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index pour récupérer rapidement par provider
CREATE INDEX idx_email_integrations_provider ON email_integrations(provider);

-- RLS
ALTER TABLE email_integrations ENABLE ROW LEVEL SECURITY;

-- Les Edge Functions utilisent service_role (bypass RLS)
-- Aucune policy public nécessaire — accès uniquement via les edge functions
