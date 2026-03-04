"""M6 Response Detector - Poll Twitter for responses to outreach."""

import json
import time
import hashlib
from datetime import datetime, timedelta
from typing import Optional


def response_detector(
    account_ids: str = "",
    db_connection_string: str = "",
    twitter_bearer_token_key: str = "twitter_bearer_token",
    lookback_hours: int = 24,
    response_types: str = "reply,quote,mention,dm",
    batch_size: int = 100
) -> dict:
    """
    Poll Twitter for replies, quotes, DMs, mentions to our accounts.
    Deduplicates against already-processed responses.
    
    Args:
        account_ids: Comma-separated list of account IDs to monitor
        db_connection_string: PostgreSQL connection string
        twitter_bearer_token_key: Key name in KV store for Twitter bearer token
        lookback_hours: How far back to look for responses
        response_types: Comma-separated types to detect
        batch_size: Max responses per poll
        
    Returns:
        new_responses[]: List of new unprocessed responses
    """
    import psycopg2
    import requests
    
    if not account_ids:
        return {"status": "error", "message": "account_ids required"}
    if not db_connection_string:
        return {"status": "error", "message": "db_connection_string required"}
    
    account_list = [a.strip() for a in account_ids.split(",") if a.strip()]
    type_list = [t.strip() for t in response_types.split(",") if t.strip()]
    
    # Get Twitter bearer token from KV store
    try:
        kv_response = requests.post(
            "http://3.211.238.155:9100/api/kv/get",
            json={"key": twitter_bearer_token_key},
            timeout=10
        )
        if kv_response.status_code != 200:
            return {"status": "error", "message": f"Failed to get Twitter token: {kv_response.text}"}
        bearer_token = kv_response.json().get("value")
        if not bearer_token:
            return {"status": "error", "message": "Twitter bearer token not found in KV store"}
    except Exception as e:
        return {"status": "error", "message": f"KV store error: {str(e)}"}
    
    # Connect to database
    try:
        conn = psycopg2.connect(db_connection_string)
        cur = conn.cursor()
    except Exception as e:
        return {"status": "error", "message": f"Database connection failed: {str(e)}"}
    
    # Ensure processed_responses table exists
    cur.execute("""
        CREATE TABLE IF NOT EXISTS processed_responses (
            response_hash VARCHAR(64) PRIMARY KEY,
            response_id VARCHAR(64),
            response_type VARCHAR(20),
            account_id VARCHAR(64),
            processed_at TIMESTAMP DEFAULT NOW()
        )
    """)
    conn.commit()
    
    new_responses = []
    stats = {"checked": 0, "new": 0, "duplicate": 0, "errors": []}
    
    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "Content-Type": "application/json"
    }
    
    since_time = (datetime.utcnow() - timedelta(hours=lookback_hours)).isoformat() + "Z"
    
    for account_id in account_list:
        # Poll mentions
        if "mention" in type_list:
            try:
                url = f"https://api.twitter.com/2/users/{account_id}/mentions"
                params = {
                    "start_time": since_time,
                    "max_results": min(batch_size, 100),
                    "tweet.fields": "author_id,created_at,conversation_id,in_reply_to_user_id,text",
                    "expansions": "author_id"
                }
                resp = requests.get(url, headers=headers, params=params, timeout=30)
                
                if resp.status_code == 200:
                    data = resp.json()
                    tweets = data.get("data", [])
                    
                    for tweet in tweets:
                        stats["checked"] += 1
                        response_hash = hashlib.sha256(
                            f"{tweet['id']}_{account_id}_mention".encode()
                        ).hexdigest()
                        
                        # Check if already processed
                        cur.execute(
                            "SELECT 1 FROM processed_responses WHERE response_hash = %s",
                            (response_hash,)
                        )
                        if cur.fetchone():
                            stats["duplicate"] += 1
                            continue
                        
                        # New response
                        new_responses.append({
                            "response_id": tweet["id"],
                            "response_type": "mention",
                            "account_id": account_id,
                            "author_id": tweet.get("author_id"),
                            "text": tweet.get("text"),
                            "created_at": tweet.get("created_at"),
                            "conversation_id": tweet.get("conversation_id"),
                            "in_reply_to_user_id": tweet.get("in_reply_to_user_id"),
                            "response_hash": response_hash
                        })
                        stats["new"] += 1
                        
                        # Mark as processed
                        cur.execute(
                            "INSERT INTO processed_responses (response_hash, response_id, response_type, account_id) VALUES (%s, %s, %s, %s)",
                            (response_hash, tweet["id"], "mention", account_id)
                        )
                elif resp.status_code == 429:
                    stats["errors"].append(f"Rate limited for account {account_id}")
                else:
                    stats["errors"].append(f"API error {resp.status_code} for account {account_id}")
                    
            except Exception as e:
                stats["errors"].append(f"Error polling mentions for {account_id}: {str(e)}")
        
        # Poll replies (search for replies to account's tweets)
        if "reply" in type_list:
            try:
                url = "https://api.twitter.com/2/tweets/search/recent"
                params = {
                    "query": f"in_reply_to_user_id:{account_id}",
                    "start_time": since_time,
                    "max_results": min(batch_size, 100),
                    "tweet.fields": "author_id,created_at,conversation_id,in_reply_to_user_id,text"
                }
                resp = requests.get(url, headers=headers, params=params, timeout=30)
                
                if resp.status_code == 200:
                    data = resp.json()
                    tweets = data.get("data", [])
                    
                    for tweet in tweets:
                        stats["checked"] += 1
                        response_hash = hashlib.sha256(
                            f"{tweet['id']}_{account_id}_reply".encode()
                        ).hexdigest()
                        
                        cur.execute(
                            "SELECT 1 FROM processed_responses WHERE response_hash = %s",
                            (response_hash,)
                        )
                        if cur.fetchone():
                            stats["duplicate"] += 1
                            continue
                        
                        new_responses.append({
                            "response_id": tweet["id"],
                            "response_type": "reply",
                            "account_id": account_id,
                            "author_id": tweet.get("author_id"),
                            "text": tweet.get("text"),
                            "created_at": tweet.get("created_at"),
                            "conversation_id": tweet.get("conversation_id"),
                            "in_reply_to_user_id": tweet.get("in_reply_to_user_id"),
                            "response_hash": response_hash
                        })
                        stats["new"] += 1
                        
                        cur.execute(
                            "INSERT INTO processed_responses (response_hash, response_id, response_type, account_id) VALUES (%s, %s, %s, %s)",
                            (response_hash, tweet["id"], "reply", account_id)
                        )
                elif resp.status_code != 429:
                    stats["errors"].append(f"API error {resp.status_code} for replies to {account_id}")
                    
            except Exception as e:
                stats["errors"].append(f"Error polling replies for {account_id}: {str(e)}")
        
        # Poll quote tweets
        if "quote" in type_list:
            try:
                # Get recent tweets from account first, then check for quotes
                url = f"https://api.twitter.com/2/users/{account_id}/tweets"
                params = {
                    "start_time": since_time,
                    "max_results": 10,
                    "tweet.fields": "id"
                }
                resp = requests.get(url, headers=headers, params=params, timeout=30)
                
                if resp.status_code == 200:
                    user_tweets = resp.json().get("data", [])
                    
                    for user_tweet in user_tweets:
                        quote_url = f"https://api.twitter.com/2/tweets/{user_tweet['id']}/quote_tweets"
                        quote_resp = requests.get(quote_url, headers=headers, timeout=30)
                        
                        if quote_resp.status_code == 200:
                            quotes = quote_resp.json().get("data", [])
                            
                            for quote in quotes:
                                stats["checked"] += 1
                                response_hash = hashlib.sha256(
                                    f"{quote['id']}_{account_id}_quote".encode()
                                ).hexdigest()
                                
                                cur.execute(
                                    "SELECT 1 FROM processed_responses WHERE response_hash = %s",
                                    (response_hash,)
                                )
                                if cur.fetchone():
                                    stats["duplicate"] += 1
                                    continue
                                
                                new_responses.append({
                                    "response_id": quote["id"],
                                    "response_type": "quote",
                                    "account_id": account_id,
                                    "author_id": quote.get("author_id"),
                                    "text": quote.get("text"),
                                    "quoted_tweet_id": user_tweet["id"],
                                    "response_hash": response_hash
                                })
                                stats["new"] += 1
                                
                                cur.execute(
                                    "INSERT INTO processed_responses (response_hash, response_id, response_type, account_id) VALUES (%s, %s, %s, %s)",
                                    (response_hash, quote["id"], "quote", account_id)
                                )
                                
            except Exception as e:
                stats["errors"].append(f"Error polling quotes for {account_id}: {str(e)}")
    
    conn.commit()
    cur.close()
    conn.close()
    
    return {
        "status": "success",
        "new_responses": new_responses,
        "stats": stats,
        "accounts_checked": len(account_list),
        "timestamp": datetime.utcnow().isoformat()
    }
