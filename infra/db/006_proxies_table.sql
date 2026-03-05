-- Migration 006: standalone proxies table
CREATE TABLE IF NOT EXISTS proxies (
    id              SERIAL PRIMARY KEY,
    host            VARCHAR(200) NOT NULL,
    port            INTEGER NOT NULL,
    username        VARCHAR(100) NOT NULL,
    password        VARCHAR(200) NOT NULL,
    protocol        VARCHAR(10) DEFAULT 'http',
    status          VARCHAR(20) DEFAULT 'active',
    assigned_to     INTEGER REFERENCES twitter_accounts(id) ON DELETE SET NULL,
    last_checked    TIMESTAMPTZ,
    last_error      TEXT,
    response_ms     INTEGER,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(host, port)
);
CREATE INDEX IF NOT EXISTS idx_proxies_status   ON proxies(status);
CREATE INDEX IF NOT EXISTS idx_proxies_assigned ON proxies(assigned_to);
