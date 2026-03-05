-- ============================================================
-- SCHEMA v003 — Reply Strategy Tables
-- MKT-44 | 2026-03-05
-- ============================================================

-- Target tweets: tweets found by M7 for reply/mention outreach
-- M7a (tweet_finder) finds user's own tweets to reply to
-- M7b (thread_finder) finds popular threads for mention strategy
CREATE TABLE IF NOT EXISTS target_tweets (
    id                  SERIAL PRIMARY KEY,
    profile_id          INTEGER NOT NULL REFERENCES twitter_profiles(id) ON DELETE CASCADE,
    tweet_id            VARCHAR(50) UNIQUE NOT NULL,
    tweet_url           VARCHAR(500) NOT NULL,
    tweet_text          TEXT,
    tweet_author        VARCHAR(100),           -- author username
    tweet_author_id     VARCHAR(30),            -- author twitter_id
    thread_type         VARCHAR(30) NOT NULL,   -- own_tweet | mention_thread
    
    -- Scoring
    relevance_score     FLOAT DEFAULT 0,        -- 0-1 how relevant to identified_needs
    engagement_score    INTEGER DEFAULT 0,      -- likes + replies + retweets
    likes_count         INTEGER DEFAULT 0,
    replies_count       INTEGER DEFAULT 0,
    retweets_count      INTEGER DEFAULT 0,
    
    -- Context for message generation
    matched_need        TEXT,                   -- which identified_need this tweet matches
    matched_keywords    JSONB DEFAULT '[]',     -- keywords that matched
    
    -- Usage tracking
    used_for_outreach   BOOLEAN DEFAULT FALSE,
    used_at             TIMESTAMP,
    message_queue_id    INTEGER REFERENCES message_queue(id),  -- which message used this
    
    -- Metadata
    tweet_created_at    TIMESTAMP,              -- when original tweet was posted
    found_at            TIMESTAMP DEFAULT NOW(),
    expires_at          TIMESTAMP,              -- tweets older than 7d not good for replies
    
    CONSTRAINT chk_thread_type CHECK (thread_type IN ('own_tweet', 'mention_thread')),
    CONSTRAINT chk_relevance_score CHECK (relevance_score >= 0 AND relevance_score <= 1)
);

CREATE INDEX IF NOT EXISTS idx_target_tweets_profile ON target_tweets(profile_id);
CREATE INDEX IF NOT EXISTS idx_target_tweets_type ON target_tweets(thread_type);
CREATE INDEX IF NOT EXISTS idx_target_tweets_unused ON target_tweets(used_for_outreach) WHERE used_for_outreach = FALSE;
CREATE INDEX IF NOT EXISTS idx_target_tweets_relevance ON target_tweets(relevance_score DESC);
CREATE INDEX IF NOT EXISTS idx_target_tweets_engagement ON target_tweets(engagement_score DESC);
CREATE INDEX IF NOT EXISTS idx_target_tweets_expires ON target_tweets(expires_at) WHERE expires_at > NOW();

-- Update message_queue to support new outreach types
ALTER TABLE message_queue 
    ADD COLUMN IF NOT EXISTS outreach_type VARCHAR(20) DEFAULT 'dm',
    ADD COLUMN IF NOT EXISTS target_tweet_id INTEGER REFERENCES target_tweets(id);

-- Constraint for outreach_type
DO $$ 
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_outreach_type'
    ) THEN
        ALTER TABLE message_queue 
            ADD CONSTRAINT chk_outreach_type CHECK (outreach_type IN ('dm', 'reply', 'mention'));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_queue_outreach_type ON message_queue(outreach_type);
CREATE INDEX IF NOT EXISTS idx_queue_target_tweet ON message_queue(target_tweet_id);

-- View: available tweets for outreach (not used, not expired)
CREATE OR REPLACE VIEW v_available_target_tweets AS
SELECT 
    tt.*,
    tp.username as profile_username,
    tp.tier,
    tp.category,
    tp.identified_needs
FROM target_tweets tt
JOIN twitter_profiles tp ON tt.profile_id = tp.id
WHERE tt.used_for_outreach = FALSE
  AND (tt.expires_at IS NULL OR tt.expires_at > NOW())
ORDER BY tt.relevance_score DESC, tt.engagement_score DESC;
