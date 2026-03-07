-- Add lock columns to message_queue for operator UI locking (MKT)
ALTER TABLE message_queue ADD COLUMN IF NOT EXISTS locked_by TEXT;
ALTER TABLE message_queue ADD COLUMN IF NOT EXISTS locked_at TIMESTAMPTZ;
