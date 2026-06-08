-- ============================================================
-- Contacts & Dossiers Clients
-- ============================================================

CREATE TABLE contacts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    email TEXT,
    phone TEXT,
    company TEXT,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_contacts_name ON contacts USING gin (name gin_trgm_ops);
CREATE INDEX idx_contacts_company ON contacts(company);

ALTER TABLE contacts ENABLE ROW LEVEL SECURITY;

-- Dossier réunions par contact
CREATE TABLE contact_meetings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    meeting_date TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_contact_meetings_contact ON contact_meetings(contact_id);
CREATE INDEX idx_contact_meetings_date ON contact_meetings(meeting_date DESC);

ALTER TABLE contact_meetings ENABLE ROW LEVEL SECURITY;
