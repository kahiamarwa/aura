-- Migration 013 UP : enrôlement vocal DEPUIS l'enceinte (canal web → device).
-- Le web écrit `enroll_request` = {"id", "name", "requested_at"} ; le device le lit
-- (poll /api/device/control), guide la capture (LED + voix), envoie l'audio à
-- /api/device/enroll (ECAPA côté serveur), puis efface la demande (enroll_request = null).
-- But : enrôler avec le MÊME micro que la vérif → score fiable (vs micro navigateur).
ALTER TABLE device_status ADD COLUMN IF NOT EXISTS enroll_request JSONB;
