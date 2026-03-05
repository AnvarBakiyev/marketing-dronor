"""
MKT-45: thread_finder (M7b)
Finds popular threads in target's niche for @mention outreach.
Writes to target_tweets for mention strategy outreach.
"""
import sys, json, logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

import requests
from infra.db import get_connection, execute_query
try:
    from infra.config import TWITTER_BEARER_TOKEN
except ImportError:
    raise RuntimeError("infra/config.py missing TWITTER_BEARER_TOKEN")

log = logging.getLogger(__name__)

# Constants
TWITTER_API_BASE = "https://api.twitter.com/2"
MAX_THREADS_PER_CATEGORY = 20
MIN_ENGAGEMENT_SCORE = 25
THREAD_EXPIRY_DAYS = 3

# Category to topic mapping
CATEGORY_TOPICS = {
    "AI Builders": ["AI agents", "LLM", "GPT", "Claude", "automation", "machine learning"],
    "Indie Hackers": ["indie hacker", "side project", "bootstrapped", "SaaS", "MRR"],
    "Automation Engineers": ["workflow automation", "no-code", "Zapier", "n8n", "API integration"],
    "Freelancers": ["freelance", "client work", "remote work", "consulting"],
    "Small Agency Owners": ["agency", "client management", "marketing agency", "dev agency"],
    "Startup Founders": ["startup", "founder", "venture", "product market fit", "YC"],
    "DevOps Engineers": ["devops", "kubernetes", "CI/CD", "infrastructure", "docker"],
    "Data Scientists": ["data science", "analytics", "python", "pandas", "ML pipeline"],
    "Product Managers": ["product management", "roadmap", "user research", "agile"]
}

def thread_finder(
    profile_id: int = 0,
    batch_size: int = 20,
    category_filter: str = "",
    dry_run: bool = False
) -> dict:
    """
    Find popular threads for mention outreach.
    
    Algorithm:
    1. Get enriched profiles with category/topics
    2. Search for popular threads in their niche
    3. Score relevance based on topic match
    4. Save high-engagement threads to target_tweets
    
    Returns: {status, processed, found, saved, skipped, errors, api_calls}
    """
    profiles = _get_profiles(profile_id, batch_size, category_filter)
    if not profiles:
        return {"status": "success", "processed": 0, "found": 0,
                "saved": 0, "skipped": 0, "errors": 0, "api_calls": 0}
    
    processed = found = saved = skipped = errors = api_calls = 0
    categories_searched = set()
    
    for p in profiles:
        processed += 1
        category = p.get("category", "")
        
        if category in categories_searched:
            continue
        
        try:
            topics = _get_topics(category, p.get("topics_of_interest"))
            if not topics:
                log.info(f"No topics for category {category}, skipping")
                skipped += 1
                continue
            
            threads = _search_threads(topics)
            api_calls += 1
            categories_searched.add(category)
            
            if not threads:
                log.info(f"No threads found for {category}")
                skipped += 1
                continue
            
            found += len(threads)
            
            for thread in threads:
                if thread["engagement"] < MIN_ENGAGEMENT_SCORE:
                    continue
                
                relevance = _score_thread_relevance(thread["text"], topics)
                if relevance < 0.5:
                    continue
                
                if not dry_run:
                    _save_target_tweet(
                        profile_id=p["id"],
                        tweet=thread,
                        relevance_score=relevance,
                        matched_keywords=topics[:3]
                    )
                saved += 1
            
            log.info(f"Category {category}: found {len(threads)} threads, saved {saved}")
            
        except Exception as e:
            log.error(f"Error for {category}: {e}")
            errors += 1
    
    return {
        "status": "success",
        "processed": processed,
        "found": found,
        "saved": saved,
        "skipped": skipped,
        "errors": errors,
        "api_calls": api_calls,
        "categories_searched": list(categories_searched)
    }


def _search_threads(topics: list[str]) -> list[dict]:
    """Search for popular threads matching topics."""
    topic_query = " OR ".join(f'"{t}"' for t in topics[:3])
    query = f"({topic_query}) -is:retweet min_replies:5 min_faves:10"
    
    url = f"{TWITTER_API_BASE}/tweets/search/recent"
    params = {
        "query": query,
        "max_results": MAX_THREADS_PER_CATEGORY,
        "tweet.fields": "created_at,public_metrics,author_id",
        "expansions": "author_id",
        "user.fields": "username,name"
    }
    headers = {
        "Authorization": f"Bearer {TWITTER_BEARER_TOKEN}",
        "Content-Type": "application/json"
    }
    
    response = requests.get(url, params=params, headers=headers, timeout=30)
    
    if response.status_code == 429:
        log.warning("Twitter API rate limited")
        return []
    
    if response.status_code != 200:
        log.error(f"Twitter API error: {response.status_code} - {response.text[:200]}")
        return []
    
    data = response.json()
    tweets = data.get("data", [])
    users = {u["id"]: u for u in data.get("includes", {}).get("users", [])}
    
    results = []
    for t in tweets:
        metrics = t.get("public_metrics", {})
        author = users.get(t.get("author_id"), {})
        engagement = (metrics.get("like_count", 0) + 
                     metrics.get("reply_count", 0) + 
                     metrics.get("retweet_count", 0))
        
        results.append({
            "id": t["id"],
            "text": t["text"],
            "created_at": t.get("created_at"),
            "author_id": t.get("author_id"),
            "author_username": author.get("username", ""),
            "likes": metrics.get("like_count", 0),
            "replies": metrics.get("reply_count", 0),
            "retweets": metrics.get("retweet_count", 0),
            "engagement": engagement
        })
    
    return sorted(results, key=lambda x: x["engagement"], reverse=True)


def _get_profiles(profile_id: int, batch_size: int, category_filter: str) -> list[dict]:
    """Get profiles ready for thread finding."""
    if profile_id:
        return execute_query(
            "SELECT * FROM twitter_profiles WHERE id = %s", (profile_id,))
    
    where = """
        tier IS NOT NULL 
        AND category IS NOT NULL
        AND NOT EXISTS (
            SELECT 1 FROM target_tweets tt 
            WHERE tt.profile_id = twitter_profiles.id 
            AND tt.thread_type = 'mention_thread'
            AND tt.found_at > NOW() - INTERVAL '1 day'
        )
    """
    params = [batch_size]
    
    if category_filter:
        where += " AND category = %s"
        params.insert(0, category_filter)
    
    return execute_query(f"""
        SELECT * FROM twitter_profiles 
        WHERE {where}
        ORDER BY 
            CASE tier WHEN 'S' THEN 1 WHEN 'A' THEN 2 WHEN 'B' THEN 3 ELSE 4 END,
            followers_count DESC
        LIMIT %s
    """, tuple(params))


def _get_topics(category: str, topics_json) -> list[str]:
    """Get topics for search based on category and profile topics."""
    topics = list(CATEGORY_TOPICS.get(category, []))
    
    if topics_json:
        try:
            profile_topics = json.loads(topics_json) if isinstance(topics_json, str) else topics_json
            topics.extend(t for t in profile_topics if t not in topics)
        except Exception:
            pass
    
    return topics[:6]


def _score_thread_relevance(tweet_text: str, topics: list[str]) -> float:
    """Score thread relevance to target topics."""
    tweet_lower = tweet_text.lower()
    matched = sum(1 for t in topics if t.lower() in tweet_lower)
    return min(matched / max(len(topics), 1) * 1.5, 1.0)


def _save_target_tweet(profile_id: int, tweet: dict, relevance_score: float,
                       matched_keywords: list[str]) -> None:
    """Save thread to target_tweets table."""
    tweet_url = f"https://twitter.com/{tweet['author_username']}/status/{tweet['id']}"
    
    expires_at = None
    tweet_created_at = None
    if tweet.get("created_at"):
        try:
            tweet_created_at = datetime.fromisoformat(tweet["created_at"].replace("Z", "+00:00"))
            expires_at = tweet_created_at + timedelta(days=THREAD_EXPIRY_DAYS)
        except Exception:
            pass
    
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO target_tweets (
                    profile_id, tweet_id, tweet_url, tweet_text, tweet_author,
                    tweet_author_id, thread_type, relevance_score, engagement_score,
                    likes_count, replies_count, retweets_count,
                    matched_keywords, tweet_created_at, expires_at
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, 'mention_thread', %s, %s,
                    %s, %s, %s,
                    %s, %s, %s
                )
                ON CONFLICT (tweet_id) DO UPDATE SET
                    relevance_score = GREATEST(target_tweets.relevance_score, EXCLUDED.relevance_score),
                    engagement_score = EXCLUDED.engagement_score,
                    likes_count = EXCLUDED.likes_count,
                    replies_count = EXCLUDED.replies_count,
                    retweets_count = EXCLUDED.retweets_count
            """, (
                profile_id, tweet["id"], tweet_url, tweet["text"], tweet["author_username"],
                tweet.get("author_id"), relevance_score, tweet["engagement"],
                tweet["likes"], tweet["replies"], tweet["retweets"],
                json.dumps(matched_keywords), tweet_created_at, expires_at
            ))

# Aliases for test compatibility
def _calculate_engagement_score(tweet: dict) -> int:
    """Return raw engagement count for a tweet."""
    m = tweet.get("public_metrics", {})
    return (m.get("like_count", 0) + m.get("reply_count", 0) +
            m.get("retweet_count", 0) + m.get("quote_count", 0))

def _should_include_thread(tweet: dict, profile: dict) -> bool:
    """Return True if tweet is not authored by the target profile itself."""
    return str(tweet.get("author_id", "")) != str(profile.get("twitter_id", ""))
