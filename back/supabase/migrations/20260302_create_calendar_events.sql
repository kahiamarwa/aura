-- Calendar events table — stores events created by AURA
CREATE TABLE IF NOT EXISTS calendar_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider TEXT NOT NULL CHECK (provider IN ('gmail', 'outlook')),
    provider_event_id TEXT,
    title TEXT NOT NULL,
    description TEXT,
    location TEXT,
    start_time TIMESTAMPTZ NOT NULL,
    end_time TIMESTAMPTZ NOT NULL,
    attendees JSONB DEFAULT '[]',
    event_link TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for fast lookups by date
CREATE INDEX IF NOT EXISTS idx_calendar_events_start ON calendar_events (start_time DESC);

-- Enable RLS
ALTER TABLE calendar_events ENABLE ROW LEVEL SECURITY;
