-- Activity logs: historique de toutes les actions effectuées par AURA
CREATE TABLE IF NOT EXISTS activity_logs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES auth.users(id),
  action_type TEXT NOT NULL,
  tool_name TEXT NOT NULL,
  description TEXT,
  metadata JSONB DEFAULT '{}',
  status TEXT DEFAULT 'success',
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index pour requêtes rapides par user + date
CREATE INDEX idx_activity_logs_user_date ON activity_logs(user_id, created_at DESC);

-- RLS
ALTER TABLE activity_logs ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view own activity" ON activity_logs
  FOR SELECT USING (auth.uid() = user_id);

CREATE POLICY "Service role can insert" ON activity_logs
  FOR INSERT WITH CHECK (true);
