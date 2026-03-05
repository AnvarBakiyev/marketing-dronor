-- MKT-73: target_tweets table for Thread Scout (M7)
-- Stores relevant tweets from target profiles for outreach personalization

CREATE TABLE IF NOT EXISTS target_tweets (
    id              SERIAL PRIMARY KEY,
    profile_id      INTEGER NOT NULL REFERENCES twitter_profiles(id) ON DELETE CASCADE,
    tweet_id        TEXT UNIQUE NOT NULL,
    tweet_text      TEXT NOT NULL,
    relevance_score FLOAT DEFAULT 0.0,
    matched_keywords TEXT[],
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    collected_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_target_tweets_profile ON target_tweets(profile_id);
CREATE INDEX IF NOT EXISTS idx_target_tweets_score ON target_tweets(relevance_score DESC);
