-- Speaker enrollments for voice verification
CREATE TABLE IF NOT EXISTS speaker_enrollments (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    speaker_name TEXT NOT NULL,
    -- 192-dim ECAPA-TDNN embedding stored as base64 encoded .npy
    embedding TEXT NOT NULL,
    -- Reference audio stored in Supabase Storage
    reference_audio_path TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- Index for fast lookup by user
CREATE INDEX idx_speaker_enrollments_user_id ON speaker_enrollments(user_id);

-- Unique constraint: one enrollment per speaker name per user
CREATE UNIQUE INDEX idx_speaker_enrollments_unique_name
    ON speaker_enrollments(user_id, speaker_name);

-- RLS
ALTER TABLE speaker_enrollments ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view their own enrollments"
    ON speaker_enrollments FOR SELECT
    USING (auth.uid() = user_id);

CREATE POLICY "Users can insert their own enrollments"
    ON speaker_enrollments FOR INSERT
    WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update their own enrollments"
    ON speaker_enrollments FOR UPDATE
    USING (auth.uid() = user_id);

CREATE POLICY "Users can delete their own enrollments"
    ON speaker_enrollments FOR DELETE
    USING (auth.uid() = user_id);

-- Storage bucket for reference audio
INSERT INTO storage.buckets (id, name, public)
VALUES ('speaker-audio', 'speaker-audio', false)
ON CONFLICT (id) DO NOTHING;

-- Storage RLS: users can manage their own audio files
CREATE POLICY "Users can upload speaker audio"
    ON storage.objects FOR INSERT
    WITH CHECK (
        bucket_id = 'speaker-audio'
        AND (storage.foldername(name))[1] = auth.uid()::text
    );

CREATE POLICY "Users can read their speaker audio"
    ON storage.objects FOR SELECT
    USING (
        bucket_id = 'speaker-audio'
        AND (storage.foldername(name))[1] = auth.uid()::text
    );

CREATE POLICY "Users can delete their speaker audio"
    ON storage.objects FOR DELETE
    USING (
        bucket_id = 'speaker-audio'
        AND (storage.foldername(name))[1] = auth.uid()::text
    );
