-- Add columns for Meta Embedded Signup support
-- waba_id: WhatsApp Business Account ID (returned by Embedded Signup)
-- signup_method: distinguishes between manual credential entry and Embedded Signup

ALTER TABLE whatsapp_integrations
  ADD COLUMN IF NOT EXISTS waba_id TEXT,
  ADD COLUMN IF NOT EXISTS signup_method TEXT DEFAULT 'manual';
