-- MKT: Add M7 columns to target_tweets
-- tweet_url, tweet_author, thread_type, engagement_score, found_at

ALTER TABLE target_tweets
    ADD COLUMN IF NOT EXISTS tweet_url        TEXT,
    ADD COLUMN IF NOT EXISTS tweet_author     VARCHAR(100),
    ADD COLUMN IF NOT EXISTS thread_type      VARCHAR(30) DEFAULT 'own_tweet',
    ADD COLUMN IF NOT EXISTS engagement_score FLOAT DEFAULT 0.0,
    ADD COLUMN IF NOT EXISTS found_at         TIMESTAMP WITH TIME ZONE DEFAULT NOW();

CREATE INDEX IF NOT EXISTS idx_target_tweets_thread_type ON target_tweets(thread_type);
CREATE INDEX IF NOT EXISTS idx_target_tweets_found_at ON target_tweets(found_at);
