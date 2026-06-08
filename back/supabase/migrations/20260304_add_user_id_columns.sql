-- ============================================================
-- Add user_id to all existing tables for multi-tenant support
-- Nullable to preserve existing data during migration
-- ============================================================

-- transcriptions
ALTER TABLE transcriptions ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES auth.users(id);
CREATE INDEX IF NOT EXISTS idx_transcriptions_user ON transcriptions(user_id);

-- contacts
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES auth.users(id);
CREATE INDEX IF NOT EXISTS idx_contacts_user ON contacts(user_id);

-- contact_meetings
ALTER TABLE contact_meetings ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES auth.users(id);
CREATE INDEX IF NOT EXISTS idx_contact_meetings_user ON contact_meetings(user_id);

-- email_integrations
ALTER TABLE email_integrations ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES auth.users(id);
CREATE INDEX IF NOT EXISTS idx_email_integrations_user ON email_integrations(user_id);

-- calendar_events
ALTER TABLE calendar_events ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES auth.users(id);
CREATE INDEX IF NOT EXISTS idx_calendar_events_user ON calendar_events(user_id);

-- sms_messages
ALTER TABLE sms_messages ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES auth.users(id);
CREATE INDEX IF NOT EXISTS idx_sms_messages_user ON sms_messages(user_id);

-- ============================================================
-- RLS Policies: users can only access their own data
-- ============================================================

-- Drop existing overly-permissive policy on transcriptions
DROP POLICY IF EXISTS "Authenticated users can read transcriptions" ON transcriptions;

-- Transcriptions
CREATE POLICY "Users can manage own transcriptions"
    ON transcriptions FOR ALL TO authenticated
    USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);

-- Contacts
CREATE POLICY "Users can manage own contacts"
    ON contacts FOR ALL TO authenticated
    USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);

-- Contact meetings
CREATE POLICY "Users can manage own contact meetings"
    ON contact_meetings FOR ALL TO authenticated
    USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);

-- Email integrations
CREATE POLICY "Users can manage own email integrations"
    ON email_integrations FOR ALL TO authenticated
    USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);

-- Calendar events
CREATE POLICY "Users can manage own calendar events"
    ON calendar_events FOR ALL TO authenticated
    USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);

-- SMS messages
CREATE POLICY "Users can manage own sms messages"
    ON sms_messages FOR ALL TO authenticated
    USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);
