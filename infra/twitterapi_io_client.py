"""
TwitterAPI.io client for MarketingDronor.
Drop-in replacement for twitter_client.py with same interface.
MKT-71
"""
import time, logging, requests
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)
BASE_URL = "https://api.twitterapi.io"

class TwitterRateLimitError(Exception):
    def __init__(self, endpoint, reset_at=None):
        self.endpoint = endpoint
        self.reset_at = reset_at
        super().__init__(f"Rate limit hit for {endpoint}")

class TwitterAPIError(Exception): pass

class TwitterAPIioClient:
    def __init__(self, api_key):
        if not api_key: raise ValueError("API key required")
        self.api_key = api_key
        self._call_count = 0
        self._call_log = []
        self.session = requests.Session()
        self.session.headers.update({"X-API-Key": api_key, "Content-Type": "application/json"})

    def search_recent_tweets(self, query, max_results=10, next_token=None):
        params = {"query": query, "queryType": "Latest"}
        if next_token: params["cursor"] = next_token
        raw = self._request("/twitter/tweet/advanced_search", params)
        return self._normalize_search(raw, max_results)

    def get_user_by_username(self, username):
        raw = self._request("/twitter/user/info", {"userName": username})
        return self._normalize_user(raw)

    def get_user_tweets(self, user_id, max_results=10, next_token=None):
        params = {"userId": user_id}
        if next_token: params["cursor"] = next_token
        raw = self._request("/twitter/user/last_tweets", params)
        tweets = raw.get("tweets", [])[:max_results]
        nt = raw.get("next_cursor") if raw.get("has_next_page") else None
        return {"data": [{"id": t.get("id"), "text": t.get("text","")} for t in tweets], "meta": {"next_token": nt}}

    def get_user_followers(self, user_id, max_results=20, next_token=None):
        params = {"userId": user_id}
        if next_token: params["cursor"] = next_token
        raw = self._request("/twitter/user/followers", params)
        users_raw = raw.get("users", [])[:max_results]
        data = [{"id": u.get("id") or u.get("userId"), "username": u.get("userName",""),
                 "name": u.get("name",""), "description": u.get("description",""),
                 "public_metrics": {"followers_count": u.get("followers",0),
                     "following_count": u.get("following",0), "tweet_count": u.get("statusesCount",0)}}
                for u in users_raw]
        nt = raw.get("next_cursor") if raw.get("has_next_page") else None
        return {"data": data, "meta": {"next_token": nt}}

    def get_budget_report(self):
        return {"provider": "twitterapi.io", "total_calls": self._call_count}

    def _request(self, endpoint, params):
        url = f"{BASE_URL}{endpoint}"
        self._call_count += 1
        try:
            resp = self.session.get(url, params=params, timeout=30)
        except requests.RequestException as e:
            raise TwitterAPIError(f"Network error: {e}") from e
        self._call_log.append({"endpoint": endpoint, "status": resp.status_code})
        if resp.status_code == 429: raise TwitterRateLimitError(endpoint)
        if resp.status_code != 200:
            raise TwitterAPIError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    def _normalize_search(self, raw, max_results):
        tweets_raw = raw.get("tweets", [])[:max_results]
        data, users = [], {}
        for t in tweets_raw:
            author = t.get("author", {})
            aid = author.get("id") or author.get("userId")
            tid = t.get("id") or t.get("tweetId")
            if not aid or not tid: continue
            data.append({"id": tid, "author_id": aid, "text": t.get("text",""), "lang": t.get("lang","en")})
            if aid not in users:
                m = author.get("public_metrics") or {"followers_count": author.get("followers",0),
                    "following_count": author.get("following",0), "tweet_count": author.get("statusesCount",0)}
                users[aid] = {"id": aid, "username": author.get("userName","") or author.get("username",""),
                    "name": author.get("name",""), "description": author.get("description",""),
                    "location": author.get("location",""), "url": author.get("url",""),
                    "public_metrics": m}
        nt = raw.get("next_cursor") if raw.get("has_next_page") else None
        return {"data": data, "includes": {"users": list(users.values())}, "meta": {"next_token": nt, "result_count": len(data)}}

    def _normalize_user(self, raw):
        u = raw.get("data") or raw
        return {"data": {"id": u.get("id") or u.get("userId"),
            "username": u.get("userName") or u.get("username"),
            "name": u.get("name"), "description": u.get("description"),
            "public_metrics": {"followers_count": u.get("followers",0),
                "following_count": u.get("following",0), "tweet_count": u.get("statusesCount",0)}}}


class MockTwitterClient:
    """Mock client for dev/testing without API key."""
    def __init__(self):
        self._call_count = 0
        log.warning("[MockTwitterClient] Using mock data - set TWITTERAPI_IO_KEY in config.py")
    def search_recent_tweets(self, query, max_results=10, next_token=None):
        self._call_count += 1
        mu = [{"id": f"mock_{i}", "username": f"mock_user_{i}", "name": f"Mock {i}",
               "description": f"AI builder #{i}", "location": "", "url": "",
               "public_metrics": {"followers_count": 500*i, "following_count": 100, "tweet_count": 500}}
              for i in range(1, min(max_results,5)+1)]
        mt = [{"id": f"t_{i}", "author_id": f"mock_{i}", "text": f"About {query[:20]} #{i}", "lang": "en"}
              for i in range(1, len(mu)+1)]
        return {"data": mt, "includes": {"users": mu}, "meta": {"next_token": None}}
    def get_user_tweets(self, user_id, max_results=10, next_token=None):
        self._call_count += 1
        return {"data": [{"id": "t1", "text": "Mock tweet"}], "meta": {"next_token": None}}
    def get_user_followers(self, user_id, max_results=20, next_token=None):
        self._call_count += 1
        return {"data": [], "meta": {"next_token": None}}
    def get_budget_report(self):
        return {"provider": "mock", "total_calls": self._call_count}


def get_twitterapi_io_client(api_key=None):
    """Factory: real client if key, mock otherwise."""
    key = api_key
    if not key:
        try:
            import sys, pathlib
            sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
            from infra.config import TWITTERAPI_IO_KEY
            key = TWITTERAPI_IO_KEY
        except (ImportError, AttributeError):
            key = None
    return TwitterAPIioClient(api_key=key) if key else MockTwitterClient()
