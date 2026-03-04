"""
MKT-6: twitter_get_followers_of
Strategy C — collect followers of target accounts (thought leaders, Dronor competitors).
"""
import sys, logging
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from infra.twitter_client import get_twitter_client, TwitterRateLimitError
from infra.db import get_connection
log = logging.getLogger(__name__)

TARGET_ACCOUNTS = [
    "levelsio", "marc_louvion", "patio11", "KyleAWard",
    "n8n_io", "zapier", "pipedream", "tldraw"
]

def twitter_get_followers_of(
    target_username: str = "",
    max_followers: int = 200,
    use_default_targets: bool = False,
    collection_source: str = "strategy_c"
) -> dict:
    client = get_twitter_client()
    usernames = TARGET_ACCOUNTS if use_default_targets else [target_username]
    total_saved = total_skipped = 0

    for username in usernames:
        if not username:
            continue
        user = client.get_user_by_username(username)
        if not user:
            log.warning(f"User not found: {username}")
            continue
        try:
            result = client.get_user_followers(user["id"], max_results=min(max_followers, 1000))
            followers = result.get("data", [])
            s, sk = _upsert_followers(followers, collection_source)
            total_saved += s
            total_skipped += sk
            log.info(f"@{username}: {s} saved, {sk} skipped")
        except TwitterRateLimitError as e:
            log.warning(f"Rate limit on @{username}: {e}")
            break

    return {"status": "success", "saved": total_saved, "skipped": total_skipped,
            "budget": client.get_budget_report()}

def _upsert_followers(followers: list, source: str) -> tuple[int, int]:
    saved = skipped = 0
    with get_connection() as conn:
        with conn.cursor() as cur:
            for u in followers:
                m = u.get("public_metrics", {})
                cur.execute("""
                    INSERT INTO twitter_profiles (twitter_id, username, display_name, bio,
                        followers_count, following_count, collection_source)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (twitter_id) DO NOTHING
                """, (u["id"], u["username"], u.get("name"), u.get("description"),
                      m.get("followers_count",0), m.get("following_count",0), source))
                if cur.rowcount: saved += 1
                else: skipped += 1
    return saved, skipped
