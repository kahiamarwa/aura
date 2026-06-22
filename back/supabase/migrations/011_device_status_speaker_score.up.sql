-- Migration 011 UP : score de similarité du locuteur vérifié (affiché dans le front).
-- Le badge montrait "Score : 0%" car le score n'était jamais poussé (seuls speaker + verified l'étaient).
ALTER TABLE device_status ADD COLUMN IF NOT EXISTS speaker_score REAL;
