-- ============================================================
-- Table: hubspot_integrations
-- Stores per-user HubSpot OAuth tokens for multi-tenant access
-- ============================================================

CREATE TABLE hubspot_integrations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    access_token TEXT NOT NULL,
    refresh_token TEXT NOT NULL,
    token_expiry TIMESTAMPTZ NOT NULL,
    hub_id TEXT,
    portal_name TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id)
);

CREATE INDEX idx_hubspot_integrations_user ON hubspot_integrations(user_id);

ALTER TABLE hubspot_integrations ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can manage own hubspot integration"
    ON hubspot_integrations
    FOR ALL
    TO authenticated
    USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);
