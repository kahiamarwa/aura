-- Migration 012 UP : mute logiciel à distance (mode confidentiel).
-- Le web écrit `muted`, le device le lit (poll) → arrête micro + ambiant (rien ne
-- part au cloud). État rouge côté front. Ce n'est PAS une coupure matérielle.
ALTER TABLE device_status ADD COLUMN IF NOT EXISTS muted BOOLEAN DEFAULT false;
