"""
MKT-7: twitter_search_conversations
Strategy D — find profiles actively discussing pain points relevant to Dronor.
"""
import sys, logging
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from infra.twitter_client import get_twitter_client, TwitterRateLimitError
from infra.db import get_connection
log = logging.getLogger(__name__)

PAIN_POINT_QUERIES = [
    '("I wish" OR "why is there no") ("automation" OR "AI workflow") -is:retweet lang:en',
    '("manually" OR "repetitive task") ("automate" OR "tool") -is:retweet lang:en',
    '("n8n" OR "zapier") ("problem" OR "broken" OR "alternative") -is:retweet lang:en',
    '"personal assistant" ("AI" OR "automation") ("build" OR "need") -is:retweet lang:en',
    '("too much time" OR "wasting time") ("workflow" OR "process") -is:retweet lang:en',
]

def twitter_search_conversations(
    query: str = "",
    max_profiles: int = 100,
    use_default_queries: bool = False,
    collection_source: str = "strategy_d"
) -> dict:
    client = get_twitter_client()
    queries = PAIN_POINT_QUERIES if use_default_queries else [query]
    total_saved = total_skipped = 0

    for q in queries:
        if not q.strip(): continue
        try:
            result = client.search_recent_tweets(q, max_results=min(max_profiles, 100))
            users_by_id = {u["id"]: u for u in result.get("includes",{}).get("users",[])}
            tweet_by_author = {}
            for tweet in result.get("data", []):
                aid = tweet.get("author_id")
                if aid and aid not in tweet_by_author:
                    tweet_by_author[aid] = tweet

            s, sk = _upsert_authors(tweet_by_author, users_by_id, collection_source)
            total_saved += s
            total_skipped += sk
        except TwitterRateLimitError as e:
            log.warning(f"Rate limit: {e}")
            break

    return {"status": "success", "saved": total_saved, "skipped": total_skipped,
            "budget": client.get_budget_report()}

def _upsert_authors(tweet_by_author, users_by_id, source) -> tuple[int, int]:
    saved = skipped = 0
    with get_connection() as conn:
        with conn.cursor() as cur:
            for author_id, tweet in tweet_by_author.items():
                u = users_by_id.get(author_id)
                if not u: skipped += 1; continue
                m = u.get("public_metrics", {})
                cur.execute("""
                    INSERT INTO twitter_profiles (twitter_id, username, display_name, bio,
                        followers_count, following_count, primary_language, collection_source)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (twitter_id) DO NOTHING
                """, (u["id"], u["username"], u.get("name"), u.get("description"),
                      m.get("followers_count",0), m.get("following_count",0),
                      tweet.get("lang"), source))
                if cur.rowcount: saved += 1
                else: skipped += 1
    return saved, skipped
