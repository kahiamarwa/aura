-- ============================================================
-- Migration: Create transcriptions table
-- Project: AURA - AI Audio Pipeline
-- ============================================================

CREATE TABLE transcriptions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    audio_filename TEXT,
    audio_duration_seconds NUMERIC,
    language TEXT DEFAULT 'fr',
    transcription_text TEXT NOT NULL,
    whisper_segments JSONB,
    summary JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index pour trier par date de création
CREATE INDEX idx_transcriptions_created_at ON transcriptions(created_at DESC);

-- Activer RLS
ALTER TABLE transcriptions ENABLE ROW LEVEL SECURITY;

-- Policy: lecture seule pour les utilisateurs authentifiés
-- Note: l'Edge Function utilise le service_role key qui bypass RLS
CREATE POLICY "Authenticated users can read transcriptions"
    ON transcriptions
    FOR SELECT
    TO authenticated
    USING (true);
