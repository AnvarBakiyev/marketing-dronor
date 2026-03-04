"""
Twitter API v2 client wrapper.
Handles rate limits, retries, budget tracking.
All M1 experts use this module — never call Twitter API directly.

MKT-2
"""
import time
import json
import logging
from datetime import datetime, timezone
from typing import Optional
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    from infra.config import TWITTER_BEARER_TOKEN
except ImportError:
    raise RuntimeError("infra/config.py missing. See infra/config.example.py")

log = logging.getLogger(__name__)

# ─── Rate limit budget (Twitter API v2 Basic tier) ───────────────────────────
# Adjust per your actual plan
RATE_LIMITS = {
    "search/recent":         {"per_15min": 60,   "per_month": 500_000},
    "users/by/username":     {"per_15min": 300,  "per_month": None},
    "users/:id/tweets":      {"per_15min": 150,  "per_month": 500_000},
    "users/:id/followers":   {"per_15min": 15,   "per_month": None},
    "tweets/:id/liking_users": {"per_15min": 75, "per_month": None},
}


class TwitterRateLimitError(Exception):
    """Raised when rate limit is hit and cannot be retried immediately."""
    def __init__(self, endpoint: str, reset_at: datetime):
        self.endpoint = endpoint
        self.reset_at = reset_at
        super().__init__(f"Rate limit hit for {endpoint}, resets at {reset_at}")


class TwitterAPIError(Exception):
    """Non-recoverable Twitter API error."""
    pass


class TwitterClient:
    BASE_URL = "https://api.twitter.com/2"

    def __init__(self, bearer_token: str = None):
        self.bearer_token = bearer_token or TWITTER_BEARER_TOKEN
        self._call_log: list[dict] = []  # in-memory budget tracker

        # Session with retry on transient errors (5xx, connection)
        self.session = requests.Session()
        retry = Retry(
            total=3,
            backoff_factor=2,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["GET"]
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("https://", adapter)
        self.session.headers.update({
            "Authorization": f"Bearer {self.bearer_token}",
            "User-Agent": "MarketingDronor/1.0"
        })

    def _request(self, endpoint: str, params: dict) -> dict:
        """
        Core GET request with rate limit handling.
        Returns parsed JSON or raises TwitterRateLimitError / TwitterAPIError.
        """
        url = f"{self.BASE_URL}/{endpoint.lstrip('/')}"
        log.debug(f"GET {url} params={params}")

        resp = self.session.get(url, params=params, timeout=30)

        # Log for budget tracking
        self._call_log.append({
            "endpoint": endpoint,
            "status": resp.status_code,
            "ts": datetime.now(timezone.utc).isoformat()
        })

        if resp.status_code == 429:
            reset_ts = int(resp.headers.get("x-rate-limit-reset", time.time() + 900))
            reset_at = datetime.fromtimestamp(reset_ts, tz=timezone.utc)
            wait_sec = max(0, reset_ts - time.time()) + 5  # 5s buffer
            log.warning(f"Rate limit hit on {endpoint}. Waiting {wait_sec:.0f}s")
            time.sleep(min(wait_sec, 60))  # wait max 60s inline, else raise
            if wait_sec > 60:
                raise TwitterRateLimitError(endpoint, reset_at)
            # retry once after short wait
            resp = self.session.get(url, params=params, timeout=30)

        if resp.status_code == 401:
            raise TwitterAPIError("Unauthorized — check TWITTER_BEARER_TOKEN")
        if resp.status_code == 403:
            raise TwitterAPIError(f"Forbidden on {endpoint} — check API plan permissions")
        if resp.status_code == 404:
            return {}  # profile deleted / tweet not found — treat as empty
        if not resp.ok:
            raise TwitterAPIError(f"HTTP {resp.status_code} on {endpoint}: {resp.text[:200]}")

        return resp.json()

    # ─── Public methods ───────────────────────────────────────────────────────

    def search_recent_tweets(self, query: str, max_results: int = 100,
                             next_token: str = None) -> dict:
        """
        Search recent tweets (last 7 days).
        Returns {data: [...], meta: {next_token, result_count}}
        """
        params = {
            "query": query,
            "max_results": min(max_results, 100),
            "tweet.fields": "created_at,lang,public_metrics,conversation_id,referenced_tweets",
            "user.fields": "id,name,username,description,public_metrics,created_at,verified,location,url",
            "expansions": "author_id",
        }
        if next_token:
            params["next_token"] = next_token
        return self._request("tweets/search/recent", params)

    def get_user_by_username(self, username: str) -> Optional[dict]:
        """
        Get user profile by username.
        Returns user dict or None if not found.
        """
        result = self._request(f"users/by/username/{username}", {
            "user.fields": "id,name,username,description,public_metrics,created_at,verified,location,url,pinned_tweet_id"
        })
        return result.get("data")

    def get_users_by_usernames(self, usernames: list[str]) -> list[dict]:
        """
        Batch get up to 100 users by username.
        """
        result = self._request("users/by", {
            "usernames": ",".join(usernames[:100]),
            "user.fields": "id,name,username,description,public_metrics,created_at,verified,location,url"
        })
        return result.get("data", [])

    def get_user_tweets(self, user_id: str, max_results: int = 100,
                        next_token: str = None, exclude: str = "retweets,replies") -> dict:
        """
        Get user's recent tweets (up to 3200).
        Returns {data: [...], meta: {next_token}}
        """
        params = {
            "max_results": min(max_results, 100),
            "tweet.fields": "created_at,lang,public_metrics,conversation_id",
            "exclude": exclude,
        }
        if next_token:
            params["pagination_token"] = next_token
        return self._request(f"users/{user_id}/tweets", params)

    def get_user_followers(self, user_id: str, max_results: int = 100,
                           next_token: str = None) -> dict:
        """
        Get user's followers (Strategy C).
        Returns {data: [...], meta: {next_token}}
        """
        params = {
            "max_results": min(max_results, 1000),
            "user.fields": "id,name,username,description,public_metrics,created_at,verified,location"
        }
        if next_token:
            params["pagination_token"] = next_token
        return self._request(f"users/{user_id}/followers", params)

    def get_tweet_liking_users(self, tweet_id: str, max_results: int = 100) -> dict:
        """
        Get users who liked a tweet (Strategy D - find engaged users).
        """
        params = {
            "max_results": min(max_results, 100),
            "user.fields": "id,name,username,description,public_metrics"
        }
        return self._request(f"tweets/{tweet_id}/liking_users", params)

    def get_tweet_retweeters(self, tweet_id: str, max_results: int = 100) -> dict:
        """Get users who retweeted a tweet."""
        params = {
            "max_results": min(max_results, 100),
            "user.fields": "id,name,username,description,public_metrics"
        }
        return self._request(f"tweets/{tweet_id}/retweeted_by", params)

    def get_budget_report(self) -> dict:
        """Return call stats for this session."""
        from collections import Counter
        by_endpoint = Counter(c["endpoint"] for c in self._call_log)
        return {
            "total_calls": len(self._call_log),
            "by_endpoint": dict(by_endpoint),
            "errors": sum(1 for c in self._call_log if str(c["status"]).startswith(("4","5")))
        }


# Singleton for reuse within a session
_client: Optional[TwitterClient] = None

def get_twitter_client() -> TwitterClient:
    global _client
    if _client is None:
        _client = TwitterClient()
    return _client
