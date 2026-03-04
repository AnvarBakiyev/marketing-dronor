-- Marketing Dronor — PostgreSQL Schema v001
-- All 6 modules. Generated: 2026-03-04
-- MKT-1

-- ============================================================
-- CORE: Twitter Profiles (M1 writes, M2 reads/writes)
-- ============================================================
CREATE TABLE IF NOT EXISTS twitter_profiles (
    id                      SERIAL PRIMARY KEY,
    twitter_id              VARCHAR(30) UNIQUE NOT NULL,
    username                VARCHAR(100) NOT NULL,
    display_name            VARCHAR(200),
    bio                     TEXT,
    location                VARCHAR(200),
    website                 VARCHAR(500),
    followers_count         INTEGER DEFAULT 0,
    following_count         INTEGER DEFAULT 0,
    tweets_count            INTEGER DEFAULT 0,
    created_at_twitter      TIMESTAMP,
    verified                BOOLEAN DEFAULT FALSE,
    profile_image_url       TEXT,

    -- Extended fields (collected + computed by M1)
    last_tweet_date         TIMESTAMP,
    avg_tweets_per_week     FLOAT,
    engagement_rate         FLOAT,
    primary_language        VARCHAR(10),
    topics_of_interest      JSONB DEFAULT '[]',
    professional_role       VARCHAR(200),
    industry                VARCHAR(200),
    company_size            VARCHAR(50),
    tech_stack              JSONB DEFAULT '[]',

    -- Campaign fields (M2 fills classification)
    tier                    VARCHAR(5),           -- S/A/B/C/D
    category                VARCHAR(100),         -- one of 9 Dronor categories
    identified_needs        JSONB DEFAULT '[]',   -- [{need, context, tweet_url, urgency}]
    dronor_use_cases        JSONB DEFAULT '[]',
    thread_urls             JSONB DEFAULT '[]',
    collection_source       VARCHAR(50),          -- strategy_a/b/c/d
    assigned_expert_account VARCHAR(100),         -- which of 56 accounts

    -- Status tracking
    outreach_status         VARCHAR(30) DEFAULT 'pending',  -- pending/contacted/responded/converted
    collected_at            TIMESTAMP DEFAULT NOW(),
    last_updated            TIMESTAMP DEFAULT NOW(),

    CONSTRAINT chk_tier CHECK (tier IN ('S','A','B','C','D') OR tier IS NULL),
    CONSTRAINT chk_outreach_status CHECK (outreach_status IN ('pending','contacted','responded','converted','opted_out'))
);

CREATE INDEX IF NOT EXISTS idx_profiles_tier ON twitter_profiles(tier);
CREATE INDEX IF NOT EXISTS idx_profiles_outreach_status ON twitter_profiles(outreach_status);
CREATE INDEX IF NOT EXISTS idx_profiles_collection_source ON twitter_profiles(collection_source);
CREATE INDEX IF NOT EXISTS idx_profiles_assigned_account ON twitter_profiles(assigned_expert_account);
CREATE INDEX IF NOT EXISTS idx_profiles_language ON twitter_profiles(primary_language);
CREATE INDEX IF NOT EXISTS idx_profiles_category ON twitter_profiles(category);

-- ============================================================
-- M1: Tweet storage for analysis
-- ============================================================
CREATE TABLE IF NOT EXISTS profile_tweets (
    id              SERIAL PRIMARY KEY,
    profile_id      INTEGER NOT NULL REFERENCES twitter_profiles(id) ON DELETE CASCADE,
    tweet_id        VARCHAR(30) UNIQUE NOT NULL,
    text            TEXT NOT NULL,
    created_at      TIMESTAMP NOT NULL,
    likes_count     INTEGER DEFAULT 0,
    retweets_count  INTEGER DEFAULT 0,
    replies_count   INTEGER DEFAULT 0,
    is_reply        BOOLEAN DEFAULT FALSE,
    is_retweet      BOOLEAN DEFAULT FALSE,
    reply_to_tweet  VARCHAR(30),
    language        VARCHAR(10),
    collected_at    TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tweets_profile_id ON profile_tweets(profile_id);
CREATE INDEX IF NOT EXISTS idx_tweets_created_at ON profile_tweets(created_at);

-- ============================================================
-- M1: API usage tracking
-- ============================================================
CREATE TABLE IF NOT EXISTS api_usage_log (
    id              SERIAL PRIMARY KEY,
    endpoint        VARCHAR(100) NOT NULL,
    calls_used      INTEGER NOT NULL,
    query_id        VARCHAR(100),
    strategy        VARCHAR(20),
    logged_at       TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_api_usage_endpoint ON api_usage_log(endpoint);
CREATE INDEX IF NOT EXISTS idx_api_usage_logged_at ON api_usage_log(logged_at);

-- ============================================================
-- M3: Twitter Accounts Fleet (56 accounts)
-- ============================================================
CREATE TABLE IF NOT EXISTS twitter_accounts (
    id                  SERIAL PRIMARY KEY,
    username            VARCHAR(100) UNIQUE NOT NULL,
    display_name        VARCHAR(200),
    persona_type        VARCHAR(50),          -- operator/builder/researcher etc
    category_focus      VARCHAR(100),         -- which of 9 categories this account covers
    language            VARCHAR(10) DEFAULT 'en',
    state               VARCHAR(20) DEFAULT 'warming',  -- warming/active/cooling/suspended
    state_since         TIMESTAMP DEFAULT NOW(),
    warmup_started_at   TIMESTAMP,
    warmup_day          INTEGER DEFAULT 0,    -- 0-28
    daily_reply_limit   INTEGER DEFAULT 10,
    daily_like_limit    INTEGER DEFAULT 30,
    replies_today       INTEGER DEFAULT 0,
    likes_today         INTEGER DEFAULT 0,
    reset_at            TIMESTAMP,            -- when daily counters reset
    health_score        FLOAT DEFAULT 1.0,    -- 0.0-1.0
    shadowban_detected  BOOLEAN DEFAULT FALSE,
    captcha_triggered   BOOLEAN DEFAULT FALSE,
    suspended           BOOLEAN DEFAULT FALSE,
    total_sent          INTEGER DEFAULT 0,
    total_responses     INTEGER DEFAULT 0,
    created_at          TIMESTAMP DEFAULT NOW(),
    last_action_at      TIMESTAMP,

    CONSTRAINT chk_state CHECK (state IN ('warming','active','cooling','suspended'))
);

CREATE INDEX IF NOT EXISTS idx_accounts_state ON twitter_accounts(state);
CREATE INDEX IF NOT EXISTS idx_accounts_category ON twitter_accounts(category_focus);

CREATE TABLE IF NOT EXISTS account_state_history (
    id              SERIAL PRIMARY KEY,
    account_id      INTEGER NOT NULL REFERENCES twitter_accounts(id),
    from_state      VARCHAR(20),
    to_state        VARCHAR(20) NOT NULL,
    reason          TEXT,
    changed_at      TIMESTAMP DEFAULT NOW()
);

-- ============================================================
-- M4 → M5: Message Queue
-- ============================================================
CREATE TABLE IF NOT EXISTS message_queue (
    id                  SERIAL PRIMARY KEY,
    profile_id          INTEGER NOT NULL REFERENCES twitter_profiles(id),
    account_id          INTEGER NOT NULL REFERENCES twitter_accounts(id),
    message_text        TEXT NOT NULL,
    message_type        VARCHAR(20) NOT NULL,  -- reply/quote/mention/dm
    target_tweet_url    TEXT,
    priority            VARCHAR(5) DEFAULT 'P3',  -- P0/P1/P2/P3/P4
    tier                VARCHAR(5),
    category            VARCHAR(100),
    identified_need     TEXT,                  -- 1-sentence context for operator
    ab_variant          VARCHAR(5),            -- A/B/C
    template_id         VARCHAR(100),
    status              VARCHAR(20) DEFAULT 'pending',  -- pending/in_review/sent/rejected/skipped
    tracked             BOOLEAN DEFAULT FALSE, -- M6 picked it up
    operator_id         VARCHAR(100),
    edited_by_operator  BOOLEAN DEFAULT FALSE,
    final_message_text  TEXT,                  -- after operator edit
    created_at          TIMESTAMP DEFAULT NOW(),
    sent_at             TIMESTAMP,
    reviewed_at         TIMESTAMP,

    CONSTRAINT chk_message_type CHECK (message_type IN ('reply','quote','mention','dm')),
    CONSTRAINT chk_priority CHECK (priority IN ('P0','P1','P2','P3','P4')),
    CONSTRAINT chk_msg_status CHECK (status IN ('pending','in_review','sent','rejected','skipped'))
);

CREATE INDEX IF NOT EXISTS idx_queue_status ON message_queue(status);
CREATE INDEX IF NOT EXISTS idx_queue_priority ON message_queue(priority, status);
CREATE INDEX IF NOT EXISTS idx_queue_tracked ON message_queue(tracked) WHERE tracked = FALSE;
CREATE INDEX IF NOT EXISTS idx_queue_account ON message_queue(account_id);

-- ============================================================
-- M6: Conversations & Response Tracking
-- ============================================================
CREATE TABLE IF NOT EXISTS conversations (
    id                      SERIAL PRIMARY KEY,
    profile_id              INTEGER NOT NULL REFERENCES twitter_profiles(id),
    outreach_message_id     INTEGER REFERENCES message_queue(id),
    state                   VARCHAR(30) DEFAULT 'response_received',
    state_since             TIMESTAMP DEFAULT NOW(),
    messages_count          INTEGER DEFAULT 0,
    our_messages_count      INTEGER DEFAULT 0,
    their_messages_count    INTEGER DEFAULT 0,
    first_response_at       TIMESTAMP,
    last_activity_at        TIMESTAMP DEFAULT NOW(),
    qualification_signals   TEXT[] DEFAULT '{}',
    sentiment_avg           FLOAT,
    escalated               BOOLEAN DEFAULT FALSE,
    escalation_reason       TEXT,
    converted               BOOLEAN DEFAULT FALSE,
    conversion_date         TIMESTAMP,
    created_at              TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_conversations_profile ON conversations(profile_id);
CREATE INDEX IF NOT EXISTS idx_conversations_state ON conversations(state);
CREATE INDEX IF NOT EXISTS idx_conversations_converted ON conversations(converted);

CREATE TABLE IF NOT EXISTS conversation_messages (
    id                  SERIAL PRIMARY KEY,
    conversation_id     INTEGER NOT NULL REFERENCES conversations(id),
    tweet_id            VARCHAR(50),
    direction           VARCHAR(10) NOT NULL,  -- inbound/outbound
    message_text        TEXT NOT NULL,
    sender_id           VARCHAR(50),
    intent              VARCHAR(30),           -- positive/negative/question/neutral/conversion_signal
    sentiment           FLOAT,
    created_at          TIMESTAMP DEFAULT NOW(),
    processed_at        TIMESTAMP,

    CONSTRAINT chk_direction CHECK (direction IN ('inbound','outbound'))
);

CREATE INDEX IF NOT EXISTS idx_conv_messages_conversation ON conversation_messages(conversation_id);

CREATE TABLE IF NOT EXISTS conversation_state_history (
    id                  SERIAL PRIMARY KEY,
    conversation_id     INTEGER NOT NULL REFERENCES conversations(id),
    from_state          VARCHAR(30),
    to_state            VARCHAR(30) NOT NULL,
    trigger             TEXT,
    changed_at          TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS response_queue (
    id                      SERIAL PRIMARY KEY,
    conversation_id         INTEGER NOT NULL REFERENCES conversations(id),
    incoming_message_id     INTEGER REFERENCES conversation_messages(id),
    assigned_account_id     INTEGER REFERENCES twitter_accounts(id),
    suggested_response      TEXT,
    response_template_id    VARCHAR(50),
    alternatives            JSONB DEFAULT '[]',
    priority                VARCHAR(5) DEFAULT 'P2',
    status                  VARCHAR(20) DEFAULT 'pending',  -- pending/sent/rejected
    operator_id             VARCHAR(50),
    created_at              TIMESTAMP DEFAULT NOW(),
    sent_at                 TIMESTAMP,

    CONSTRAINT chk_resp_priority CHECK (priority IN ('P0','P1','P2','P3','P4')),
    CONSTRAINT chk_resp_status CHECK (status IN ('pending','sent','rejected'))
);

CREATE INDEX IF NOT EXISTS idx_resp_queue_status ON response_queue(status);
CREATE INDEX IF NOT EXISTS idx_resp_queue_priority ON response_queue(priority, status);

-- ============================================================
-- M6: Conversions
-- ============================================================
CREATE TABLE IF NOT EXISTS conversions (
    id                  SERIAL PRIMARY KEY,
    profile_id          INTEGER NOT NULL REFERENCES twitter_profiles(id),
    conversation_id     INTEGER REFERENCES conversations(id),
    conversion_type     VARCHAR(30) NOT NULL,  -- signup/trial/paid
    conversion_date     TIMESTAMP DEFAULT NOW(),
    attribution         JSONB DEFAULT '{}',    -- {account, wave, category, pct}
    customer_value      DECIMAL(10,2),
    source              VARCHAR(30),

    CONSTRAINT chk_conversion_type CHECK (conversion_type IN ('signup','trial','paid'))
);

CREATE INDEX IF NOT EXISTS idx_conversions_profile ON conversions(profile_id);
CREATE INDEX IF NOT EXISTS idx_conversions_date ON conversions(conversion_date);

-- ============================================================
-- Analytics
-- ============================================================
CREATE TABLE IF NOT EXISTS analytics_daily (
    id                          SERIAL PRIMARY KEY,
    date                        DATE NOT NULL,
    dimension_type              VARCHAR(30) NOT NULL,  -- total/wave/category/account
    dimension_value             VARCHAR(50),
    outreach_sent               INTEGER DEFAULT 0,
    responses_received          INTEGER DEFAULT 0,
    response_rate               FLOAT DEFAULT 0,
    qualified_count             INTEGER DEFAULT 0,
    converted_count             INTEGER DEFAULT 0,
    avg_response_time_hours     FLOAT,
    avg_conversation_length     FLOAT,
    UNIQUE(date, dimension_type, dimension_value)
);

CREATE INDEX IF NOT EXISTS idx_analytics_date ON analytics_daily(date);
CREATE INDEX IF NOT EXISTS idx_analytics_dimension ON analytics_daily(dimension_type, dimension_value);

-- ============================================================
-- M6 → M2/M3/M4: Feedback loop
-- ============================================================
CREATE TABLE IF NOT EXISTS feedback_log (
    id              SERIAL PRIMARY KEY,
    target_module   VARCHAR(10) NOT NULL,  -- M2/M3/M4
    feedback_type   VARCHAR(30) NOT NULL,
    data            JSONB NOT NULL,
    applied         BOOLEAN DEFAULT FALSE,
    applied_at      TIMESTAMP,
    created_at      TIMESTAMP DEFAULT NOW(),

    CONSTRAINT chk_target_module CHECK (target_module IN ('M2','M3','M4'))
);

-- ============================================================
-- M5: Operator sessions
-- ============================================================
CREATE TABLE IF NOT EXISTS operator_sessions (
    id                  SERIAL PRIMARY KEY,
    operator_id         VARCHAR(100) NOT NULL,
    started_at          TIMESTAMP DEFAULT NOW(),
    ended_at            TIMESTAMP,
    tasks_reviewed      INTEGER DEFAULT 0,
    tasks_sent          INTEGER DEFAULT 0,
    tasks_edited        INTEGER DEFAULT 0,
    tasks_rejected      INTEGER DEFAULT 0,
    tasks_skipped       INTEGER DEFAULT 0
);

