-- Migration 002 DOWN: Drop conversations + conversation_messages tables
-- Reverts: 002_create_conversations.up.sql

DROP POLICY IF EXISTS "own_messages" ON conversation_messages;
DROP POLICY IF EXISTS "own_conversations" ON conversations;
DROP INDEX IF EXISTS idx_conv_messages_conv;
DROP INDEX IF EXISTS idx_conversations_user;
DROP TABLE IF EXISTS conversation_messages;
DROP TABLE IF EXISTS conversations;
