-- Human-in-the-loop send jobs table
-- Each approved message gets a job that local_agent picks up

CREATE TABLE IF NOT EXISTS send_jobs (
    id              SERIAL PRIMARY KEY,
    msg_queue_id    INTEGER NOT NULL REFERENCES message_queue(id) ON DELETE CASCADE,
    operator_id     INTEGER REFERENCES operators(id),
    status          TEXT NOT NULL DEFAULT 'queued'
                    CHECK (status IN ('queued','claimed','browser_ready','sent','failed','skipped')),
    claimed_by      TEXT,          -- operator machine identifier
    claimed_at      TIMESTAMPTZ,
    browser_ready_at TIMESTAMPTZ,  -- AdsPower opened, text inserted, waiting for Enter
    completed_at    TIMESTAMPTZ,
    error_msg       TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_send_jobs_status ON send_jobs(status);
CREATE INDEX IF NOT EXISTS idx_send_jobs_msg ON send_jobs(msg_queue_id);

-- Add send_type to message_queue if not exists
ALTER TABLE message_queue
    ADD COLUMN IF NOT EXISTS send_type TEXT DEFAULT 'dm'
    CHECK (send_type IN ('dm','reply'));

ALTER TABLE message_queue
    ADD COLUMN IF NOT EXISTS target_tweet_id TEXT;  -- for reply type
