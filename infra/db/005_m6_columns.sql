-- Migration 005: columns needed by M6 Inbox Monitor & M4 Message Crafter
ALTER TABLE twitter_profiles
    ADD COLUMN IF NOT EXISTS outreach_context JSONB,
    ADD COLUMN IF NOT EXISTS followup_count   INTEGER DEFAULT 0;

ALTER TABLE message_queue
    ADD COLUMN IF NOT EXISTS replied_at TIMESTAMPTZ;
