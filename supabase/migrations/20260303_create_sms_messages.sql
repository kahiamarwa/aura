-- SMS messages table — stores SMS sent by AURA via Twilio
CREATE TABLE IF NOT EXISTS sms_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    twilio_sid TEXT,
    to_number TEXT NOT NULL,
    from_number TEXT NOT NULL,
    message TEXT NOT NULL,
    status TEXT DEFAULT 'sent',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for fast lookups by date
CREATE INDEX IF NOT EXISTS idx_sms_messages_created ON sms_messages (created_at DESC);

-- Enable RLS
ALTER TABLE sms_messages ENABLE ROW LEVEL SECURITY;
