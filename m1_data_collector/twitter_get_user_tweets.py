"""
MKT-4: twitter_get_user_tweets
Fetches recent tweets for profiles in DB, stores in profile_tweets.
Used by profile_enricher (MKT-5) for LLM analysis.
"""
import sys
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from infra.twitter_client import get_twitter_client, TwitterRateLimitError, TwitterAPIError
from infra.db import get_connection, execute_query

log = logging.getLogger(__name__)


def twitter_get_user_tweets(
    profile_id: int = 0,
    twitter_id: str = "",
    max_tweets: int = 20,
    batch_size: int = 50
) -> dict:
    """
    Fetch recent tweets for a profile and store in profile_tweets.
    Provide either profile_id (DB id) or twitter_id (Twitter user id).

    Args:
        profile_id: DB id from twitter_profiles
        twitter_id: Twitter user id string
        max_tweets: max tweets to fetch (default 20, max 100)
        batch_size: how many profiles to process if profile_id=0 and twitter_id=''
                    (batch mode: process oldest-updated profiles)

    Returns:
        {status, processed, tweets_saved, tweets_skipped, errors}
    """
    client = get_twitter_client()
    max_tweets = min(max_tweets, 100)

    # Single profile mode
    if profile_id or twitter_id:
        profiles = _get_profiles(profile_id=profile_id, twitter_id=twitter_id)
    else:
        # Batch mode: profiles with no tweets yet, oldest first
        profiles = _get_profiles_needing_tweets(limit=batch_size)

    if not profiles:
        return {"status": "success", "processed": 0, "tweets_saved": 0,
                "tweets_skipped": 0, "errors": 0}

    processed = saved = skipped = errors = 0

    for profile in profiles:
        try:
            s, sk = _fetch_and_store(client, profile, max_tweets)
            saved += s
            skipped += sk
            processed += 1
        except TwitterRateLimitError as e:
            log.warning(f"Rate limit, stopping batch: {e}")
            errors += 1
            break
        except TwitterAPIError as e:
            log.error(f"API error for profile {profile['id']}: {e}")
            errors += 1
        except Exception as e:
            log.error(f"Unexpected error for profile {profile['id']}: {e}")
            errors += 1

    return {
        "status": "success",
        "processed": processed,
        "tweets_saved": saved,
        "tweets_skipped": skipped,
        "errors": errors,
        "budget": client.get_budget_report()
    }


def _get_profiles(profile_id: int = 0, twitter_id: str = "") -> list:
    if profile_id:
        return execute_query(
            "SELECT id, twitter_id FROM twitter_profiles WHERE id = %s",
            (profile_id,)
        )
    return execute_query(
        "SELECT id, twitter_id FROM twitter_profiles WHERE twitter_id = %s",
        (twitter_id,)
    )


def _get_profiles_needing_tweets(limit: int) -> list:
    """Profiles with fewest tweets, prioritizing never-fetched."""
    return execute_query("""
        SELECT p.id, p.twitter_id
        FROM twitter_profiles p
        LEFT JOIN (
            SELECT profile_id, COUNT(*) as tweet_count
            FROM profile_tweets GROUP BY profile_id
        ) t ON t.profile_id = p.id
        WHERE p.outreach_status = 'pending'
        ORDER BY COALESCE(t.tweet_count, 0) ASC, p.collected_at ASC
        LIMIT %s
    """, (limit,))


def _fetch_and_store(client, profile: dict, max_tweets: int) -> tuple[int, int]:
    """Fetch tweets for one profile, store new ones. Returns (saved, skipped)."""
    result = client.get_user_tweets(
        user_id=str(profile["twitter_id"]),
        max_results=max_tweets
    )

    tweets = result.get("data", [])
    if not tweets:
        return 0, 0

    saved = skipped = 0
    with get_connection() as conn:
        with conn.cursor() as cur:
            for tweet in tweets:
                metrics = tweet.get("public_metrics", {})
                try:
                    cur.execute("""
                        INSERT INTO profile_tweets (
                            profile_id, tweet_id, text, created_at,
                            likes_count, retweets_count, replies_count,
                            language
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (tweet_id) DO NOTHING
                    """, (
                        profile["id"],
                        tweet["id"],
                        tweet["text"],
                        tweet.get("created_at"),
                        metrics.get("like_count", 0),
                        metrics.get("retweet_count", 0),
                        metrics.get("reply_count", 0),
                        tweet.get("lang"),
                    ))
                    if cur.rowcount:
                        saved += 1
                    else:
                        skipped += 1
                except Exception as e:
                    log.error(f"Tweet insert error {tweet['id']}: {e}")
                    skipped += 1
    return saved, skipped
