# Module Interfaces — Marketing Dronor

> This file is the **CONTRACT** between modules. Changes require agreement across all instances.
> Do NOT change without updating all consuming modules.

---

## M1 → M2: Profile data contract

M1 writes to `twitter_profiles` table. M2 reads from it.

**Key fields M2 depends on:**
```python
{
  "twitter_id": str,
  "username": str,
  "bio": str,
  "followers_count": int,
  "following_count": int,
  "tweets_count": int,
  # Enriched by profile_enricher:
  "professional_role": str,
  "industry": str,
  "tech_stack": list[str],     # JSONB
  "topics_of_interest": list[str],  # JSONB
  "primary_language": str,
  # Status:
  "outreach_status": str,  # pending | contacted | responded | converted
  "tier": str | None       # NULL until M2 fills it
}
```

---

## M2 → M3/M4: Classification result

M2 writes tier/category back to `twitter_profiles`.
M3 reads queue. M4 reads classification for message generation.

**Fields M2 writes:**
```python
{
  "tier": str,                    # S | A | B | C | D
  "category": str,                # one of 9 categories
  "identified_needs": list[dict], # JSONB [{need, context, url, urgency}]
  "dronor_use_cases": list[str],  # JSONB
  "assigned_expert_account": str  # account username
}
```

---

## M2 → M7: Profile ready for tweet finding

M7 reads from `twitter_profiles` WHERE tier IS NOT NULL.

**Fields M7 depends on:**
```python
{
  "id": int,                      # FK for target_tweets
  "username": str,                # for Twitter search query
  "identified_needs": list[dict], # to extract keywords for relevance scoring
  "category": str,                # for context
  "topics_of_interest": list[str] # for keyword matching
}
```

---

## M7 → M4: Target tweets for outreach

M7 writes to `target_tweets`. M4 reads unused tweets for message generation.

### M7a: tweet_finder (Variant A — own tweets)

**Input:** enriched profiles with identified_needs
**Output:** user's own tweets that match their pain points

```python
# Twitter API v2 call
GET /2/tweets/search/recent
    ?query=from:{username} {keywords_from_needs}
    &max_results=10
    &tweet.fields=created_at,public_metrics
```

**Filter:** `relevance_score >= 0.6` (computed from keyword match + recency)

**Writes to target_tweets:**
```python
{
  "profile_id": int,
  "tweet_id": str,
  "tweet_url": str,             # https://twitter.com/{author}/status/{id}
  "tweet_text": str,
  "tweet_author": str,          # same as profile.username
  "thread_type": "own_tweet",
  "relevance_score": float,     # 0-1
  "engagement_score": int,      # likes + replies + retweets
  "matched_need": str,          # which need this tweet addresses
  "matched_keywords": list[str],
  "tweet_created_at": timestamp,
  "expires_at": timestamp       # tweet_created_at + 7 days
}
```

### M7b: thread_finder (Variant B — popular threads)

**Input:** enriched profiles with category + topics_of_interest
**Output:** popular threads in niche where @mention outreach makes sense

```python
# Twitter API v2 call
GET /2/tweets/search/recent
    ?query={topic_keywords} -is:retweet min_replies:5
    &max_results=20
    &tweet.fields=created_at,public_metrics,author_id
    &expansions=author_id
```

**Filter:** `engagement_score >= 25` (likes + replies)

**Writes to target_tweets:**
```python
{
  "profile_id": int,            # target profile to mention
  "tweet_id": str,
  "tweet_url": str,
  "tweet_text": str,
  "tweet_author": str,          # thread author (different from profile!)
  "thread_type": "mention_thread",
  "relevance_score": float,     # topic match score
  "engagement_score": int,
  "matched_keywords": list[str],
  "tweet_created_at": timestamp,
  "expires_at": timestamp       # tweet_created_at + 3 days (threads move fast)
}
```

---

## M7 → M4: Target tweet selection

M4 reads from `target_tweets` to decide outreach type.

**Selection query:**
```sql
SELECT * FROM target_tweets
WHERE profile_id = %s
  AND used_for_outreach = FALSE
  AND (expires_at IS NULL OR expires_at > NOW())
ORDER BY relevance_score DESC, engagement_score DESC
LIMIT 1
```

**Decision logic in M4:**
```python
if target_tweet and target_tweet.thread_type == 'own_tweet':
    outreach_type = 'reply'
    # Generate reply referencing their tweet
elif target_tweet and target_tweet.thread_type == 'mention_thread':
    outreach_type = 'mention'
    # Generate reply to thread with @mention of target
else:
    outreach_type = 'dm'
    # Fallback to direct message (old logic)
```

---

## M3 → M4: Account availability

M4 calls `account_state_manager` before generating messages.
**Contract:**
```python
# Input
{"account_id": str}
# Output  
{
  "can_send": bool,
  "state": str,         # warming | active | cooling | suspended
  "actions_remaining": int,
  "reset_at": str       # ISO datetime
}
```

---

## M4 → M5: Outreach task

M4 writes to `message_queue`. M5 operator reads from it.

**Queue record:**
```python
{
  "profile_id": int,           # FK to twitter_profiles
  "account_id": int,           # FK to twitter_accounts
  "message_text": str,         # final generated message
  "message_type": str,         # reply | quote | mention | dm
  "outreach_type": str,        # NEW: dm | reply | mention
  "target_tweet_url": str,     # URL to reply to (required for reply/mention)
  "target_tweet_id": int,      # NEW: FK to target_tweets
  "priority": str,             # P0 | P1 | P2 | P3 | P4
  "tier": str,
  "category": str,
  "identified_need": str,      # 1-sentence context for operator
  "ab_variant": str,           # A | B | C
  "status": str                # pending | in_review | sent | rejected
}
```

**Priority rules:**
- P0: Tier S + reply to own tweet
- P1: Tier S DM or Tier A reply
- P2: Tier A DM or Tier B reply
- P3: Tier B/C any
- P4: Tier D or low relevance

---

## M5 → M6: Sent confirmation

M5 updates `message_queue.status = 'sent'` + writes to `outreach_sent`.
M6 polls for new sent records to start tracking.

**Trigger for M6:**
```sql
SELECT * FROM message_queue WHERE status = 'sent' AND tracked = FALSE
```

---

## M6 → M2/M3/M4: Feedback (weekly)

M6 writes to `feedback_log`:
```python
{
  "target_module": str,   # M2 | M3 | M4
  "feedback_type": str,   # response_rate | template_performance | account_health
  "data": dict,           # JSONB, module-specific
  "applied": bool
}
```

---

## Shared: PostgreSQL connection

```python
# infra/config.py (not committed, see config.example.py)
DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "dbname": "marketing_dronor",
    "user": "...",
    "password": "..."
}

# Twitter API (for M7)
TWITTER_BEARER_TOKEN = "..."  # Twitter API v2 bearer token
```

All modules import: `from infra.db import get_connection`
