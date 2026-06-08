CREATE TABLE IF NOT EXISTS slack_integrations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  access_token TEXT NOT NULL,
  authed_user_id TEXT,
  team_id TEXT NOT NULL,
  team_name TEXT,
  bot_user_id TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(user_id)
);

ALTER TABLE slack_integrations ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users manage own slack" ON slack_integrations
  FOR ALL USING (auth.uid() = user_id);
