"""
MKT-3: twitter_search_profiles
Strategy A — find profiles via keyword search in recent tweets.
Searches Twitter, extracts unique authors, upserts into twitter_profiles.
"""
import sys
import json
import logging
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent))

from infra.twitter_client import get_twitter_client, TwitterRateLimitError, TwitterAPIError
from infra.db import get_connection

log = logging.getLogger(__name__)

# Dronor-relevant search queries (Strategy A)
DEFAULT_QUERIES = [
    '("automation" OR "workflow automation") ("AI" OR "LLM") -is:retweet lang:en',
    '("n8n" OR "zapier" OR "make.com") "automation" -is:retweet lang:en',
    '("personal AI" OR "AI assistant") ("build" OR "building") -is:retweet lang:en',
    '"vibe coding" ("workflow" OR "automation") -is:retweet lang:en',
    '("solopreneur" OR "indie hacker") ("AI tools" OR "automation") -is:retweet lang:en',
]


def twitter_search_profiles(
    query: str = "",
    max_profiles: int = 100,
    use_default_queries: bool = False,
    collection_source: str = "strategy_a"
) -> dict:
    """
    Search Twitter for profiles matching query, save to DB.

    Args:
        query: Twitter search query string
        max_profiles: max unique profiles to collect per query
        use_default_queries: if True, runs all DEFAULT_QUERIES (ignores query param)
        collection_source: label for collection tracking

    Returns:
        {status, profiles_found, profiles_saved, profiles_skipped, budget}
    """
    client = get_twitter_client()
    queries = DEFAULT_QUERIES if use_default_queries else [query]

    total_found = 0
    total_saved = 0
    total_skipped = 0

    for q in queries:
        if not q.strip():
            continue
        log.info(f"Searching: {q[:80]}")
        try:
            saved, skipped = _search_and_save(client, q, max_profiles, collection_source)
            total_found += saved + skipped
            total_saved += saved
            total_skipped += skipped
        except TwitterRateLimitError as e:
            log.error(f"Rate limit, stopping: {e}")
            break
        except TwitterAPIError as e:
            log.error(f"API error on query '{q[:40]}': {e}")
            continue

    return {
        "status": "success",
        "profiles_found": total_found,
        "profiles_saved": total_saved,
        "profiles_skipped": total_skipped,
        "budget": client.get_budget_report()
    }


def _search_and_save(client, query: str, max_profiles: int,
                     collection_source: str) -> tuple[int, int]:
    """Run paginated search, upsert authors into DB. Returns (saved, skipped)."""
    seen_ids = set()
    saved = skipped = 0
    next_token = None

    while len(seen_ids) < max_profiles:
        batch_size = min(100, max_profiles - len(seen_ids))
        result = client.search_recent_tweets(query, max_results=batch_size,
                                              next_token=next_token)
        if not result or "data" not in result:
            break

        # Extract authors from expansions
        users_by_id = {}
        for user in result.get("includes", {}).get("users", []):
            users_by_id[user["id"]] = user

        for tweet in result["data"]:
            author_id = tweet.get("author_id")
            if not author_id or author_id in seen_ids:
                continue
            seen_ids.add(author_id)

            user = users_by_id.get(author_id)
            if not user:
                skipped += 1
                continue

            ok = _upsert_profile(user, tweet, collection_source)
            if ok:
                saved += 1
            else:
                skipped += 1

        next_token = result.get("meta", {}).get("next_token")
        if not next_token:
            break

    return saved, skipped


def _upsert_profile(user: dict, tweet: dict, collection_source: str) -> bool:
    """Upsert profile into twitter_profiles. Returns True on success."""
    metrics = user.get("public_metrics", {})
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO twitter_profiles (
                        twitter_id, username, display_name, bio,
                        location, website,
                        followers_count, following_count, tweets_count,
                        primary_language, collection_source, last_updated
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                    ON CONFLICT (twitter_id) DO UPDATE SET
                        followers_count = EXCLUDED.followers_count,
                        following_count = EXCLUDED.following_count,
                        tweets_count    = EXCLUDED.tweets_count,
                        last_updated    = NOW()
                    WHERE twitter_profiles.last_updated < NOW() - INTERVAL '24 hours'
                """, (
                    user["id"],
                    user["username"],
                    user.get("name"),
                    user.get("description"),
                    user.get("location"),
                    user.get("url"),
                    metrics.get("followers_count", 0),
                    metrics.get("following_count", 0),
                    metrics.get("tweet_count", 0),
                    tweet.get("lang"),
                    collection_source,
                ))
        return True
    except Exception as e:
        log.error(f"DB upsert failed for {user.get('username')}: {e}")
        return False
