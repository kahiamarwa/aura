-- Add logo_path column to user_settings for report branding
ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS logo_path TEXT DEFAULT NULL;
