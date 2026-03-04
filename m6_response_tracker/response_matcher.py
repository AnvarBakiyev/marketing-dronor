"""M6 Response Matcher - Match incoming responses to original outreach."""

import json
from datetime import datetime
from typing import Optional


def response_matcher(
    response_id: str = "",
    response_author_id: str = "",
    response_text: str = "",
    response_type: str = "",
    conversation_id: str = "",
    in_reply_to_user_id: str = "",
    db_connection_string: str = "",
    match_threshold: float = 0.7
) -> dict:
    """
    Match incoming response to original outreach.
    Links response to profile and original message.
    
    Args:
        response_id: Twitter ID of the response
        response_author_id: Twitter user ID of responder
        response_text: Text content of response
        response_type: Type of response (reply, quote, mention, dm)
        conversation_id: Twitter conversation ID if available
        in_reply_to_user_id: User ID being replied to
        db_connection_string: PostgreSQL connection string
        match_threshold: Minimum confidence for match (0-1)
        
    Returns:
        conversation_id: Our internal conversation ID
        match_confidence: Confidence score of match
        profile_id: Matched profile ID
        original_message_id: Original outreach message ID
    """
    import psycopg2
    from psycopg2.extras import RealDictCursor
    
    if not response_id or not response_author_id:
        return {"status": "error", "message": "response_id and response_author_id required"}
    if not db_connection_string:
        return {"status": "error", "message": "db_connection_string required"}
    
    # Connect to database
    try:
        conn = psycopg2.connect(db_connection_string)
        cur = conn.cursor(cursor_factory=RealDictCursor)
    except Exception as e:
        return {"status": "error", "message": f"Database connection failed: {str(e)}"}
    
    # Ensure conversations table exists
    cur.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id SERIAL PRIMARY KEY,
            profile_id INTEGER,
            profile_twitter_id VARCHAR(64),
            account_id VARCHAR(64),
            original_message_id VARCHAR(64),
            original_message_text TEXT,
            original_message_type VARCHAR(20),
            original_sent_at TIMESTAMP,
            status VARCHAR(20) DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        )
    """)
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS conversation_messages (
            id SERIAL PRIMARY KEY,
            conversation_id INTEGER REFERENCES conversations(id),
            message_id VARCHAR(64),
            direction VARCHAR(10),
            message_type VARCHAR(20),
            text TEXT,
            author_id VARCHAR(64),
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    conn.commit()
    
    match_confidence = 0.0
    matched_conversation = None
    match_method = None
    
    # Method 1: Direct conversation_id match (highest confidence)
    if conversation_id:
        cur.execute("""
            SELECT c.*, p.twitter_handle, p.display_name
            FROM conversations c
            LEFT JOIN profiles p ON c.profile_id = p.id
            WHERE c.original_message_id = %s
               OR EXISTS (
                   SELECT 1 FROM conversation_messages cm 
                   WHERE cm.conversation_id = c.id 
                   AND cm.message_id = %s
               )
        """, (conversation_id, conversation_id))
        result = cur.fetchone()
        if result:
            matched_conversation = dict(result)
            match_confidence = 0.95
            match_method = "conversation_id"
    
    # Method 2: Match by reply structure (in_reply_to_user_id + author)
    if not matched_conversation and in_reply_to_user_id:
        cur.execute("""
            SELECT c.*, p.twitter_handle, p.display_name
            FROM conversations c
            LEFT JOIN profiles p ON c.profile_id = p.id
            WHERE c.profile_twitter_id = %s
              AND c.account_id = %s
              AND c.status IN ('pending', 'active')
            ORDER BY c.original_sent_at DESC
            LIMIT 1
        """, (response_author_id, in_reply_to_user_id))
        result = cur.fetchone()
        if result:
            matched_conversation = dict(result)
            match_confidence = 0.85
            match_method = "reply_structure"
    
    # Method 3: Match by author Twitter ID (medium confidence)
    if not matched_conversation:
        cur.execute("""
            SELECT c.*, p.twitter_handle, p.display_name
            FROM conversations c
            LEFT JOIN profiles p ON c.profile_id = p.id
            WHERE c.profile_twitter_id = %s
              AND c.status IN ('pending', 'active')
            ORDER BY c.original_sent_at DESC
            LIMIT 1
        """, (response_author_id,))
        result = cur.fetchone()
        if result:
            matched_conversation = dict(result)
            match_confidence = 0.70
            match_method = "author_id"
    
    # Method 4: Try to find profile by Twitter ID even without conversation
    if not matched_conversation:
        cur.execute("""
            SELECT id, twitter_id, twitter_handle, display_name
            FROM profiles
            WHERE twitter_id = %s
            LIMIT 1
        """, (response_author_id,))
        profile = cur.fetchone()
        
        if profile:
            # Profile exists but no conversation - could be unsolicited contact
            match_confidence = 0.50
            match_method = "profile_only"
            matched_conversation = {
                "id": None,
                "profile_id": profile["id"],
                "profile_twitter_id": response_author_id,
                "twitter_handle": profile.get("twitter_handle"),
                "display_name": profile.get("display_name"),
                "is_new_conversation": True
            }
    
    # No match at all
    if not matched_conversation:
        cur.close()
        conn.close()
        return {
            "status": "no_match",
            "response_id": response_id,
            "response_author_id": response_author_id,
            "match_confidence": 0.0,
            "message": "Could not match response to any known profile or conversation"
        }
    
    # Check if confidence meets threshold
    if match_confidence < match_threshold:
        cur.close()
        conn.close()
        return {
            "status": "low_confidence",
            "response_id": response_id,
            "match_confidence": match_confidence,
            "match_method": match_method,
            "matched_conversation": matched_conversation,
            "message": f"Match confidence {match_confidence:.2f} below threshold {match_threshold}"
        }
    
    # If new conversation needed, create it
    if matched_conversation.get("is_new_conversation"):
        cur.execute("""
            INSERT INTO conversations (
                profile_id, profile_twitter_id, status, created_at
            ) VALUES (%s, %s, 'active', NOW())
            RETURNING id
        """, (matched_conversation["profile_id"], response_author_id))
        new_conv_id = cur.fetchone()["id"]
        matched_conversation["id"] = new_conv_id
        conn.commit()
    
    # Add response to conversation messages
    if matched_conversation.get("id"):
        cur.execute("""
            INSERT INTO conversation_messages (
                conversation_id, message_id, direction, message_type, text, author_id
            ) VALUES (%s, %s, 'inbound', %s, %s, %s)
            ON CONFLICT DO NOTHING
        """, (
            matched_conversation["id"],
            response_id,
            response_type,
            response_text,
            response_author_id
        ))
        
        # Update conversation status to active
        cur.execute("""
            UPDATE conversations 
            SET status = 'active', updated_at = NOW()
            WHERE id = %s
        """, (matched_conversation["id"],))
        conn.commit()
    
    cur.close()
    conn.close()
    
    return {
        "status": "matched",
        "response_id": response_id,
        "conversation_id": matched_conversation.get("id"),
        "profile_id": matched_conversation.get("profile_id"),
        "profile_twitter_id": matched_conversation.get("profile_twitter_id"),
        "twitter_handle": matched_conversation.get("twitter_handle"),
        "display_name": matched_conversation.get("display_name"),
        "original_message_id": matched_conversation.get("original_message_id"),
        "match_confidence": match_confidence,
        "match_method": match_method,
        "timestamp": datetime.utcnow().isoformat()
    }
