-- Migration 001 UP: Create user_settings table
-- Rollback: execute 001_create_user_settings.down.sql

CREATE TABLE IF NOT EXISTS user_settings (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES auth.users(id) UNIQUE NOT NULL,
  langue TEXT DEFAULT 'fr',
  theme TEXT DEFAULT 'light',
  timezone TEXT DEFAULT 'Europe/Paris',
  notifications BOOLEAN DEFAULT true,
  wake_word BOOLEAN DEFAULT true,
  voix TEXT DEFAULT 'alloy',
  vitesse TEXT DEFAULT '1.0',
  barge_in BOOLEAN DEFAULT true,
  continuite BOOLEAN DEFAULT true,
  son_confirmation BOOLEAN DEFAULT true,
  passive_active BOOLEAN DEFAULT true,
  resumes_auto BOOLEAN DEFAULT true,
  passive_timeout TEXT DEFAULT '30',
  retention TEXT DEFAULT '7',
  langue_transcription TEXT DEFAULT 'fr',
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE user_settings ENABLE ROW LEVEL SECURITY;

CREATE POLICY "select_own" ON user_settings FOR SELECT USING (auth.uid() = user_id);
CREATE POLICY "insert_own" ON user_settings FOR INSERT WITH CHECK (auth.uid() = user_id);
CREATE POLICY "update_own" ON user_settings FOR UPDATE USING (auth.uid() = user_id);
