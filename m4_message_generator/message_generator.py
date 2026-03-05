"""
MKT-46: message_generator (M4)
Generates personalized outreach messages based on outreach strategy.
Supports: DM, Reply (to own tweet), Mention (in popular thread)
"""
import sys, json, logging
from pathlib import Path
from datetime import datetime
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

import openai
from infra.db import get_connection, execute_query
try:
    from infra.config import OPENAI_API_KEY
except ImportError:
    raise RuntimeError("infra/config.py missing OPENAI_API_KEY")

log = logging.getLogger(__name__)
openai.api_key = OPENAI_API_KEY

# Priority mapping
PRIORITY_RULES = {
    ('S', 'reply'): 'P0',
    ('S', 'dm'): 'P1',
    ('A', 'reply'): 'P1',
    ('S', 'mention'): 'P1',
    ('A', 'dm'): 'P2',
    ('A', 'mention'): 'P2',
    ('B', 'reply'): 'P2',
    ('B', 'dm'): 'P3',
    ('B', 'mention'): 'P3',
    ('C', 'dm'): 'P3',
    ('C', 'mention'): 'P3',
}

# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------
def message_generator(
    profile_id: int = 0,
    batch_size: int = 20,
    tier_filter: str = "",
    dry_run: bool = False
) -> dict:
    """
    Generate outreach messages for profiles.
    
    Strategy selection:
    1. Check target_tweets for unused reply/mention opportunities
    2. If target_tweet exists: generate reply/mention message
    3. Else: generate DM (fallback)
    
    Returns: {status, processed, generated, dm, reply, mention, errors}
    """
    profiles = _get_profiles(profile_id, batch_size, tier_filter)
    if not profiles:
        return {"status": "success", "processed": 0, "generated": 0,
                "dm": 0, "reply": 0, "mention": 0, "errors": 0}
    
    stats = {"processed": 0, "generated": 0, "dm": 0, "reply": 0, "mention": 0, "errors": 0}
    
    for p in profiles:
        stats["processed"] += 1
        try:
            # Find best outreach strategy
            target_tweet = _get_best_target_tweet(p["id"])
            
            if target_tweet and target_tweet["thread_type"] == "own_tweet":
                outreach_type = "reply"
                message = _generate_reply_message(p, target_tweet)
            elif target_tweet and target_tweet["thread_type"] == "mention_thread":
                outreach_type = "mention"
                message = _generate_mention_message(p, target_tweet)
            else:
                outreach_type = "dm"
                target_tweet = None
                message = _generate_dm_message(p)
            
            if not message:
                log.warning(f"Empty message for @{p['username']}")
                stats["errors"] += 1
                continue
            
            priority = _calculate_priority(p["tier"], outreach_type)
            
            if not dry_run:
                queue_id = _save_to_queue(
                    profile=p,
                    message=message,
                    outreach_type=outreach_type,
                    target_tweet=target_tweet,
                    priority=priority
                )
                
                if target_tweet:
                    _mark_tweet_used(target_tweet["id"], queue_id)
            
            stats["generated"] += 1
            stats[outreach_type] += 1
            
            log.info(f"@{p['username']}: {outreach_type} message (priority {priority})")
            
        except Exception as e:
            log.error(f"Error for @{p.get('username')}: {e}")
            stats["errors"] += 1
    
    return {"status": "success", **stats}


# ---------------------------------------------------------------------------
# Message generation
# ---------------------------------------------------------------------------
def _generate_dm_message(profile: dict) -> str:
    """Generate DM message."""
    needs = _parse_needs(profile.get("identified_needs"))
    primary_need = needs[0] if needs else "workflow automation"
    
    prompt = f"""Generate a short, personalized Twitter DM (max 280 chars) to {profile['username']}.

Context:
- Role: {profile.get('professional_role', 'professional')}
- Industry: {profile.get('industry', 'tech')}
- Identified need: {primary_need}

Tone: Casual, helpful, not salesy. Mention their specific need.
DO NOT mention Dronor directly — just offer help with their problem.
End with a soft question.
"""
    
    return _call_openai(prompt)


def _generate_reply_message(profile: dict, target_tweet: dict) -> str:
    """Generate reply to user's own tweet."""
    prompt = f"""Generate a Twitter reply (max 280 chars) to this tweet by @{profile['username']}:

"""Tweet: {target_tweet['tweet_text']}"""

Context:
- They expressed a need related to: {target_tweet.get('matched_need', 'automation')}
- Their role: {profile.get('professional_role', 'professional')}

Tone: Helpful, conversational. Acknowledge their point and offer insight/solution.
DO NOT be salesy. Just be genuinely helpful.
DO NOT use hashtags.
"""
    
    return _call_openai(prompt)


def _generate_mention_message(profile: dict, target_tweet: dict) -> str:
    """Generate reply to popular thread with @mention."""
    prompt = f"""Generate a Twitter reply (max 280 chars) to this popular thread:

"""Thread: {target_tweet['tweet_text']}"""

Your task: Add value to the discussion AND naturally mention @{profile['username']} who might find this relevant.

Context about @{profile['username']}:
- Role: {profile.get('professional_role', 'professional')}
- Interest: {profile.get('category', 'automation')}

Tone: Valuable contribution first, mention second. Be genuinely helpful to the thread.
Example pattern: "Great point! [your insight]. cc @{profile['username']} this might interest you"
DO NOT be salesy or promotional.
"""
    
    return _call_openai(prompt)


def _call_openai(prompt: str) -> str:
    """Call OpenAI API for message generation."""
    try:
        response = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=150,
            temperature=0.8
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        log.error(f"OpenAI error: {e}")
        return ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _get_profiles(profile_id: int, batch_size: int, tier_filter: str) -> list[dict]:
    """Get profiles ready for message generation."""
    if profile_id:
        return execute_query(
            "SELECT * FROM twitter_profiles WHERE id = %s", (profile_id,))
    
    where = """
        tier IS NOT NULL 
        AND outreach_status = 'pending'
        AND NOT EXISTS (
            SELECT 1 FROM message_queue mq 
            WHERE mq.profile_id = twitter_profiles.id 
            AND mq.status IN ('pending', 'in_review')
        )
    """
    params = [batch_size]
    
    if tier_filter:
        where += " AND tier = %s"
        params.insert(0, tier_filter)
    
    return execute_query(f"""
        SELECT * FROM twitter_profiles 
        WHERE {where}
        ORDER BY 
            CASE tier WHEN 'S' THEN 1 WHEN 'A' THEN 2 WHEN 'B' THEN 3 ELSE 4 END,
            followers_count DESC
        LIMIT %s
    """, tuple(params))


def _get_best_target_tweet(profile_id: int) -> Optional[dict]:
    """Get best available target tweet for outreach."""
    result = execute_query("""
        SELECT * FROM target_tweets
        WHERE profile_id = %s
          AND used_for_outreach = FALSE
          AND (expires_at IS NULL OR expires_at > NOW())
        ORDER BY 
            CASE thread_type WHEN 'own_tweet' THEN 1 ELSE 2 END,
            relevance_score DESC,
            engagement_score DESC
        LIMIT 1
    """, (profile_id,))
    
    return result[0] if result else None


def _parse_needs(needs_json) -> list[str]:
    """Extract needs from JSON."""
    if not needs_json:
        return []
    try:
        needs = json.loads(needs_json) if isinstance(needs_json, str) else needs_json
        return [n.get("need", "") for n in needs if n.get("need")]
    except Exception:
        return []


def _calculate_priority(tier: str, outreach_type: str) -> str:
    """Calculate message priority."""
    return PRIORITY_RULES.get((tier, outreach_type), 'P4')


def _save_to_queue(profile: dict, message: str, outreach_type: str,
                   target_tweet: Optional[dict], priority: str) -> int:
    """Save message to queue."""
    needs = _parse_needs(profile.get("identified_needs"))
    message_type = 'reply' if outreach_type in ['reply', 'mention'] else 'dm'
    
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO message_queue (
                    profile_id, account_id, message_text, message_type,
                    outreach_type, target_tweet_url, target_tweet_id,
                    priority, tier, category, identified_need, status
                ) VALUES (
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s, %s, 'pending'
                ) RETURNING id
            """, (
                profile["id"],
                profile.get("assigned_expert_account"),
                message,
                message_type,
                outreach_type,
                target_tweet["tweet_url"] if target_tweet else None,
                target_tweet["id"] if target_tweet else None,
                priority,
                profile.get("tier"),
                profile.get("category"),
                needs[0] if needs else None
            ))
            return cur.fetchone()[0]


def _mark_tweet_used(tweet_id: int, queue_id: int) -> None:
    """Mark target tweet as used."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE target_tweets 
                SET used_for_outreach = TRUE, 
                    used_at = NOW(),
                    message_queue_id = %s
                WHERE id = %s
            """, (queue_id, tweet_id))
