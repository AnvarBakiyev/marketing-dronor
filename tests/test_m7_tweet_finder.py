"""
Smoke tests for M7 tweet_finder and thread_finder modules.
MKT-44, MKT-45 | Uses mock Twitter API
"""
import sys
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))


# ============================================================================
# Mock Twitter API responses
# ============================================================================

MOCK_TWEETS_OWN = {
    "data": [
        {
            "id": "1234567890123456789",
            "text": "Just implemented a new AI-powered feature for our CRM. The automation saves us 10 hours/week! #buildinpublic",
            "author_id": "111111111",
            "created_at": "2026-03-04T10:00:00.000Z",
            "public_metrics": {
                "like_count": 45,
                "reply_count": 12,
                "retweet_count": 8,
                "quote_count": 3
            }
        },
        {
            "id": "1234567890123456788",
            "text": "Morning coffee and code ☕",
            "author_id": "111111111",
            "created_at": "2026-03-03T08:30:00.000Z",
            "public_metrics": {
                "like_count": 5,
                "reply_count": 1,
                "retweet_count": 0,
                "quote_count": 0
            }
        }
    ],
    "includes": {
        "users": [
            {"id": "111111111", "username": "testuser"}
        ]
    },
    "meta": {"result_count": 2}
}

MOCK_TWEETS_POPULAR = {
    "data": [
        {
            "id": "9876543210987654321",
            "text": "Thread on how we scaled our SaaS to $1M ARR 🧵\n\n1/ First, we focused on product-market fit...",
            "author_id": "222222222",
            "created_at": "2026-03-04T14:00:00.000Z",
            "public_metrics": {
                "like_count": 234,
                "reply_count": 89,
                "retweet_count": 56,
                "quote_count": 12
            },
            "conversation_id": "9876543210987654321"
        },
        {
            "id": "9876543210987654320",
            "text": "Random tweet about nothing in particular",
            "author_id": "333333333",
            "created_at": "2026-03-04T12:00:00.000Z",
            "public_metrics": {
                "like_count": 3,
                "reply_count": 0,
                "retweet_count": 0,
                "quote_count": 0
            },
            "conversation_id": "9876543210987654320"
        }
    ],
    "includes": {
        "users": [
            {"id": "222222222", "username": "saas_founder"},
            {"id": "333333333", "username": "random_user"}
        ]
    },
    "meta": {"result_count": 2}
}

MOCK_PROFILE = {
    "id": 1,
    "twitter_id": "111111111",
    "username": "testuser",
    "tier": "A",
    "category": "SaaS Founder",
    "topics_of_interest": ["AI", "automation", "CRM", "SaaS"],
    "identified_needs": [
        {
            "need": "Improve customer automation",
            "solution_fit": "Our AI can automate their workflows",
            "keywords": ["automation", "AI", "workflow"]
        }
    ]
}


# ============================================================================
# Test tweet_finder
# ============================================================================

class TestTweetFinder:
    """Tests for M7a: tweet_finder — find user's own tweets"""
    
    @patch('m7_tweet_finder.tweet_finder.requests.get')
    @patch('m7_tweet_finder.tweet_finder.execute_query')
    @patch('m7_tweet_finder.tweet_finder.get_connection')
    def test_find_own_tweets_success(self, mock_conn, mock_exec, mock_get):
        """Should find and score user's own tweets"""
        # Setup mocks
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = MOCK_TWEETS_OWN
        mock_get.return_value = mock_response
        
        mock_exec.return_value = [MOCK_PROFILE]
        mock_conn.return_value.__enter__ = MagicMock()
        mock_conn.return_value.__exit__ = MagicMock()
        
        from m7_tweet_finder.tweet_finder import tweet_finder
        
        result = tweet_finder(profile_id=1, dry_run=True)
        
        assert result["status"] == "success"
        assert result["processed"] >= 1
        # First tweet should have high relevance (contains AI, automation)
        # Second tweet (coffee) should be filtered out

    def test_relevance_scoring_keywords(self):
        """Should score tweets higher when they contain target keywords"""
        from m7_tweet_finder.tweet_finder import _calculate_relevance_score
        
        # Tweet with matching keywords
        tweet_with_keywords = {
            "text": "Our AI automation saves time every day",
            "created_at": datetime.utcnow().isoformat() + "Z"
        }
        needs = [{"keywords": ["AI", "automation", "time"]}]
        
        score = _calculate_relevance_score(tweet_with_keywords, needs)
        assert score >= 0.6, "Tweet with 3 matching keywords should score >= 0.6"
        
        # Tweet without keywords
        tweet_no_keywords = {
            "text": "Good morning everyone!",
            "created_at": datetime.utcnow().isoformat() + "Z"
        }
        score2 = _calculate_relevance_score(tweet_no_keywords, needs)
        assert score2 < 0.3, "Tweet without keywords should score low"

    def test_recency_boost(self):
        """Should boost recent tweets over older ones"""
        from m7_tweet_finder.tweet_finder import _calculate_relevance_score
        
        now = datetime.utcnow()
        needs = [{"keywords": ["test"]}]
        
        recent_tweet = {
            "text": "test content",
            "created_at": now.isoformat() + "Z"
        }
        old_tweet = {
            "text": "test content",
            "created_at": (now - timedelta(days=6)).isoformat() + "Z"
        }
        
        score_recent = _calculate_relevance_score(recent_tweet, needs)
        score_old = _calculate_relevance_score(old_tweet, needs)
        
        assert score_recent > score_old, "Recent tweets should score higher"


# ============================================================================
# Test thread_finder
# ============================================================================

class TestThreadFinder:
    """Tests for M7b: thread_finder — find popular threads for mention"""
    
    @patch('m7_tweet_finder.thread_finder.requests.get')
    @patch('m7_tweet_finder.thread_finder.execute_query')
    @patch('m7_tweet_finder.thread_finder.get_connection')
    def test_find_popular_threads(self, mock_conn, mock_exec, mock_get):
        """Should find popular threads matching profile topics"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = MOCK_TWEETS_POPULAR
        mock_get.return_value = mock_response
        
        mock_exec.return_value = [MOCK_PROFILE]
        mock_conn.return_value.__enter__ = MagicMock()
        mock_conn.return_value.__exit__ = MagicMock()
        
        from m7_tweet_finder.thread_finder import thread_finder
        
        result = thread_finder(profile_id=1, dry_run=True)
        
        assert result["status"] == "success"
        assert result["processed"] >= 1

    def test_engagement_threshold(self):
        """Should filter out low-engagement tweets"""
        from m7_tweet_finder.thread_finder import _calculate_engagement_score
        
        high_engagement = {
            "public_metrics": {
                "like_count": 100,
                "reply_count": 50,
                "retweet_count": 25,
                "quote_count": 10
            }
        }
        low_engagement = {
            "public_metrics": {
                "like_count": 2,
                "reply_count": 0,
                "retweet_count": 0,
                "quote_count": 0
            }
        }
        
        score_high = _calculate_engagement_score(high_engagement)
        score_low = _calculate_engagement_score(low_engagement)
        
        assert score_high >= 25, "High engagement should meet threshold"
        assert score_low < 25, "Low engagement should not meet threshold"

    def test_exclude_own_tweets(self):
        """Should not return user's own tweets as mention targets"""
        from m7_tweet_finder.thread_finder import _should_include_thread
        
        profile = {"twitter_id": "111111111", "username": "testuser"}
        
        own_tweet = {"author_id": "111111111", "text": "My tweet"}
        other_tweet = {"author_id": "222222222", "text": "Other tweet"}
        
        assert not _should_include_thread(own_tweet, profile), "Should exclude own tweets"
        assert _should_include_thread(other_tweet, profile), "Should include other's tweets"


# ============================================================================
# Integration test (requires DB)
# ============================================================================

@pytest.mark.integration
@pytest.mark.skip(reason="Requires live DB connection")
class TestM7Integration:
    """Integration tests — run with: pytest -m integration"""
    
    def test_full_pipeline_own_tweets(self):
        """Full flow: profile → tweet_finder → target_tweets"""
        from m7_tweet_finder.tweet_finder import tweet_finder
        
        # Requires profile_id=1 with identified_needs in DB
        result = tweet_finder(profile_id=1, dry_run=False)
        
        assert result["status"] == "success"
        assert result["inserted"] > 0 or result["skipped"] > 0
    
    def test_full_pipeline_threads(self):
        """Full flow: profile → thread_finder → target_tweets"""
        from m7_tweet_finder.thread_finder import thread_finder
        
        result = thread_finder(profile_id=1, dry_run=False)
        
        assert result["status"] == "success"


# ============================================================================
# Run tests
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
