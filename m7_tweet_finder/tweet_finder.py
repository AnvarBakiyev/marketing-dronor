"""
MKT-44: tweet_finder (M7a)
Finds target user's own tweets matching their identified needs.
Writes to target_tweets for reply strategy outreach.
"""
import sys, json, logging, re
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

import requests
from infra.db import get_connection, execute_query
try:
    from infra.config import TWITTERAPI_IO_KEY
except ImportError:
    TWITTERAPI_IO_KEY = None
try:
    from infra.config import TWITTER_BEARER_TOKEN
except ImportError:
    TWITTER_BEARER_TOKEN = None
from infra.twitterapi_io_client import TwitterAPIioClient

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
TWITTER_API_BASE = "https://api.twitter.com/2"
MAX_TWEETS_PER_USER = 10
MIN_RELEVANCE_SCORE = 0.6
TWEET_EXPIRY_DAYS = 7

# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------
def tweet_finder(
    profile_id: int = 0,
    batch_size: int = 20,
    tier_filter: str = "",       # e.g. "S" or "A"
    dry_run: bool = False
) -> dict:
    """
    Find target users' own tweets that match their identified needs.
    
    Algorithm:
    1. Get enriched profiles with identified_needs
    2. For each profile, search their recent tweets
    3. Score relevance based on keyword matching
    4. Save high-relevance tweets to target_tweets
    
    Returns: {status, processed, found, saved, skipped, errors, api_calls}
    """
    profiles = _get_profiles(profile_id, batch_size, tier_filter)
    if not profiles:
        return {"status": "success", "processed": 0, "found": 0,
                "saved": 0, "skipped": 0, "errors": 0, "api_calls": 0}
    
    processed = found = saved = skipped = errors = api_calls = 0
    
    for p in profiles:
        processed += 1
        try:
            # Extract keywords from identified_needs
            keywords = _extract_keywords(p.get("identified_needs"), p.get("topics_of_interest"))
            if not keywords:
                log.info(f"No keywords for @{p['username']}, skipping")
                skipped += 1
                continue
            
            # Search user's tweets
            tweets = _search_user_tweets(p["username"], keywords)
            api_calls += 1
            
            if not tweets:
                log.info(f"No matching tweets for @{p['username']}")
                skipped += 1
                continue
            
            found += len(tweets)
            
            # Score and save relevant tweets
            for tweet in tweets:
                relevance, matched_need, matched_kw = _score_relevance(
                    tweet["text"], 
                    p.get("identified_needs"),
                    keywords
                )
                
                if relevance < MIN_RELEVANCE_SCORE:
                    continue
                
                if not dry_run:
                    _save_target_tweet(
                        profile_id=p["id"],
                        tweet=tweet,
                        relevance_score=relevance,
                        matched_need=matched_need,
                        matched_keywords=matched_kw,
                        author=p["username"]
                    )
                saved += 1
                
            log.info(f"@{p['username']}: found {len(tweets)} tweets, saved {saved}")
            
        except Exception as e:
            log.error(f"Error for @{p.get('username')}: {e}")
            errors += 1
    
    return {
        "status": "success",
        "processed": processed,
        "found": found,
        "saved": saved,
        "skipped": skipped,
        "errors": errors,
        "api_calls": api_calls
    }


# ---------------------------------------------------------------------------
# Twitter API
# ---------------------------------------------------------------------------
def _search_user_tweets(username: str, keywords: list[str]) -> list[dict]:
    """Search recent tweets from user matching keywords via TwitterAPI.io (MKT-81)."""
    if not TWITTERAPI_IO_KEY:
        log.warning("TWITTERAPI_IO_KEY not configured, skipping tweet search")
        return []
    
    try:
        client = TwitterAPIioClient(TWITTERAPI_IO_KEY)
        
        # Step 1: get user_id from username
        user_data = client.get_user_by_username(username)
        if not user_data:
            log.info(f"User @{username} not found")
            return []
        user_id = user_data.get("id")
        if not user_id:
            return []
        
        # Step 2: get recent tweets
        result = client.get_user_tweets(user_id, max_results=MAX_TWEETS_PER_USER)
        tweets_raw = result.get("data", [])
        
        # Step 3: filter by keywords
        kw_set = set(keywords[:5])
        matched = []
        for t in tweets_raw:
            text_lower = t.get("text", "").lower()
            if any(k in text_lower for k in kw_set):
                matched.append(t)
        
        return [
            {
                "id": t["id"],
                "text": t.get("text", ""),
                "created_at": t.get("created_at"),
                "likes": t.get("public_metrics", {}).get("like_count", 0) if isinstance(t.get("public_metrics"), dict) else 0,
                "replies": t.get("public_metrics", {}).get("reply_count", 0) if isinstance(t.get("public_metrics"), dict) else 0,
                "retweets": t.get("public_metrics", {}).get("retweet_count", 0) if isinstance(t.get("public_metrics"), dict) else 0,
            }
            for t in matched
        ]
    except Exception as e:
        log.error(f"TwitterAPI.io error for @{username}: {e}")
        return []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _get_profiles(profile_id: int, batch_size: int, tier_filter: str) -> list[dict]:
    """Get enriched profiles ready for tweet finding."""
    if profile_id:
        return execute_query(
            "SELECT * FROM twitter_profiles WHERE id = %s", (profile_id,))
    
    where = """
        tier IS NOT NULL 
        AND identified_needs IS NOT NULL 
        AND identified_needs != '[]'::jsonb
        AND NOT EXISTS (
            SELECT 1 FROM target_tweets tt 
            WHERE tt.profile_id = twitter_profiles.id 
            AND tt.thread_type = 'own_tweet'
            AND tt.found_at > NOW() - INTERVAL '3 days'
        )
    """
    params = [batch_size]
    
    if tier_filter:
        where += " AND tier = %s"
        params.insert(0, tier_filter)
    
    return execute_query(f"""
        SELECT * FROM twitter_profiles 
        WHERE {where}
        ORDER BY 
            CASE tier WHEN 'S' THEN 1 WHEN 'A' THEN 2 WHEN 'B' THEN 3 ELSE 4 END,
            followers_count DESC
        LIMIT %s
    """, tuple(params))


def _extract_keywords(needs_json, topics_json) -> list[str]:
    """Extract keywords from identified_needs and topics."""
    keywords = set()
    
    # From identified_needs
    if needs_json:
        try:
            needs = json.loads(needs_json) if isinstance(needs_json, str) else needs_json
            for n in needs:
                need_text = n.get("need", "")
                # Extract nouns/key phrases
                words = re.findall(r'\b[a-zA-Z]{4,}\b', need_text.lower())
                keywords.update(words)
        except Exception:
            pass
    
    # From topics_of_interest
    if topics_json:
        try:
            topics = json.loads(topics_json) if isinstance(topics_json, str) else topics_json
            keywords.update(t.lower() for t in topics if len(t) >= 3)
        except Exception:
            pass
    
    # Filter common words
    stopwords = {"that", "this", "with", "from", "have", "been", "would", "could", "should", "their", "about", "which", "when", "what", "they", "there", "these", "those"}
    keywords = [k for k in keywords if k not in stopwords]
    
    return keywords[:10]  # Top 10 keywords


def _score_relevance(tweet_text: str, needs_json, keywords: list[str]) -> tuple[float, str, list[str]]:
    """Score tweet relevance to profile's needs."""
    tweet_lower = tweet_text.lower()
    matched_keywords = [k for k in keywords if k in tweet_lower]
    
    if not matched_keywords:
        return 0.0, "", []
    
    # Base score from keyword density
    keyword_score = len(matched_keywords) / len(keywords) if keywords else 0
    
    # Find matched need
    matched_need = ""
    if needs_json:
        try:
            needs = json.loads(needs_json) if isinstance(needs_json, str) else needs_json
            for n in needs:
                need_text = n.get("need", "").lower()
                if any(k in need_text for k in matched_keywords):
                    matched_need = n.get("need", "")
                    break
        except Exception:
            pass
    
    # Boost for direct need match
    relevance = keyword_score * 0.7
    if matched_need:
        relevance += 0.3
    
    return min(relevance, 1.0), matched_need, matched_keywords


def _save_target_tweet(profile_id: int, tweet: dict, relevance_score: float,
                       matched_need: str, matched_keywords: list[str], author: str) -> None:
    """Save tweet to target_tweets table."""
    tweet_url = f"https://twitter.com/{author}/status/{tweet['id']}"
    engagement = tweet["likes"] + tweet["replies"] + tweet["retweets"]
    
    # Parse created_at
    expires_at = None
    tweet_created_at = None
    if tweet.get("created_at"):
        try:
            tweet_created_at = datetime.fromisoformat(tweet["created_at"].replace("Z", "+00:00"))
            expires_at = tweet_created_at + timedelta(days=TWEET_EXPIRY_DAYS)
        except Exception:
            pass
    
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO target_tweets (
                    profile_id, tweet_id, tweet_url, tweet_text, tweet_author,
                    thread_type, relevance_score, engagement_score,
                    likes_count, replies_count, retweets_count,
                    matched_need, matched_keywords, tweet_created_at, expires_at
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    'own_tweet', %s, %s,
                    %s, %s, %s,
                    %s, %s, %s, %s
                )
                ON CONFLICT (tweet_id) DO UPDATE SET
                    relevance_score = GREATEST(target_tweets.relevance_score, EXCLUDED.relevance_score),
                    engagement_score = EXCLUDED.engagement_score,
                    likes_count = EXCLUDED.likes_count,
                    replies_count = EXCLUDED.replies_count,
                    retweets_count = EXCLUDED.retweets_count
            """, (
                profile_id, tweet["id"], tweet_url, tweet["text"], author,
                relevance_score, engagement,
                tweet["likes"], tweet["replies"], tweet["retweets"],
                matched_need, json.dumps(matched_keywords), tweet_created_at, expires_at
            ))

# Aliases for test compatibility
def _calculate_relevance_score(tweet: dict, needs: list) -> float:
    """Test-compatible wrapper: (tweet_dict, needs_list) -> float.
    Extracts keywords from needs, scores text, applies recency boost.
    """
    from datetime import datetime, timezone, timedelta
    import json

    # Extract keywords from needs list
    keywords = []
    for n in (needs or []):
        keywords.extend(n.get("keywords", []))
    keywords = [k.lower() for k in keywords]

    text = tweet.get("text", "").lower()
    if not keywords:
        return 0.0

    matched = [k for k in keywords if k in text]
    base_score = len(matched) / len(keywords)

    # Recency boost: tweets < 1 day old get +0.2
    boost = 0.0
    created_at = tweet.get("created_at", "")
    if created_at:
        try:
            dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            age_days = (datetime.now(timezone.utc) - dt).total_seconds() / 86400
            if age_days < 1:
                boost = 0.2
        except Exception:
            pass

    # Return raw score (no cap) to preserve recency ordering
    return base_score + boost

