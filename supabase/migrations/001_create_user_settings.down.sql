-- Migration 001 DOWN: Drop user_settings table
-- Reverts: 001_create_user_settings.up.sql

DROP POLICY IF EXISTS "update_own" ON user_settings;
DROP POLICY IF EXISTS "insert_own" ON user_settings;
DROP POLICY IF EXISTS "select_own" ON user_settings;
DROP TABLE IF EXISTS user_settings;
