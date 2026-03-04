"""
MKT-25: warm_followup_generator
Generates personalised follow-up messages for profiles that didn't reply.
Uses reply gap, tier, and original message context to craft non-spammy followups.
"""
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import anthropic
from infra.db import execute_query, get_connection
try:
    from infra.config import ANTHROPIC_API_KEY
except ImportError:
    raise RuntimeError("infra/config.py missing")

MIN_DAYS_BEFORE_FOLLOWUP = 3
MAX_FOLLOWUPS_PER_PROFILE = 2

SYSTEM_PROMPT = """You write warm, non-spammy follow-up DMs for a B2B SaaS product (Dronor - personal AI automation platform).
The tone must feel like a genuine human reaching out again, not a sales bot.
Return ONLY the message text, no quotes, no explanation. Max 200 characters."""

USER_PROMPT = """Write a follow-up DM for this profile who didn't reply to the first message.

Profile: @{username} | Tier: {tier} | Category: {category}
Original message sent {days_ago} days ago:
"{original_message}"

Context about them:
- Best angle: {best_angle}
- Their tone: {tone}
- Followup #{followup_number} (of max 2)

Rules:
- Don't repeat the original pitch verbatim
- Acknowledge time passed naturally ("Hey, circling back..." / "Wanted to resurface this...")
- Add one new hook based on their category/tone
- For followup #2: be brief, low-pressure ("No worries if not relevant, just leaving this here")
- Max 200 characters"""


def warm_followup_generator(
    profile_id: int = 0,
    batch_size: int = 20,
    dry_run: bool = False,
    min_days: int = MIN_DAYS_BEFORE_FOLLOWUP
) -> dict:
    """
    Find profiles eligible for follow-up and generate personalised messages.

    Eligibility:
    - Original DM was sent >= min_days ago with no reply
    - Followup count < MAX_FOLLOWUPS_PER_PROFILE
    - Profile tier is S/A/B/C (not D for followups)

    Returns: {status, processed, generated, skipped, errors, tokens_used}
    """
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    profiles = _get_eligible_profiles(profile_id, batch_size, min_days)

    if not profiles:
        return {"status": "success", "processed": 0, "generated": 0,
                "skipped": 0, "errors": 0, "tokens_used": 0}

    processed = generated = skipped = errors = 0
    tokens_used = 0

    for p in profiles:
        processed += 1
        try:
            original = _get_last_sent_message(p["profile_id"])
            if not original:
                skipped += 1
                continue

            followup_count = p.get("followup_count", 0) or 0
            if followup_count >= MAX_FOLLOWUPS_PER_PROFILE:
                skipped += 1
                continue

            ctx = _parse_outreach_context(p)
            days_ago = p.get("days_since_sent", min_days)

            prompt = USER_PROMPT.format(
                username=p.get("username", ""),
                tier=p.get("tier", "C"),
                category=p.get("category", "other"),
                days_ago=int(days_ago),
                original_message=original["message_text"][:150],
                best_angle=ctx.get("best_angle", "automation saves time"),
                tone=ctx.get("tone", "casual"),
                followup_number=followup_count + 1,
            )

            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=120,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}]
            )
            tokens_used += response.usage.input_tokens + response.usage.output_tokens
            message_text = response.content[0].text.strip()[:280]

            if not dry_run:
                _save_followup(p["profile_id"], original["account_id"], message_text)

            generated += 1

        except anthropic.RateLimitError:
            errors += 1
            break
        except Exception:
            errors += 1

    return {
        "status": "success",
        "processed": processed,
        "generated": generated,
        "skipped": skipped,
        "errors": errors,
        "tokens_used": tokens_used,
        "dry_run": dry_run,
    }


# ---------------------------------------------------------------------------
def _get_eligible_profiles(profile_id, batch_size, min_days):
    if profile_id:
        return execute_query("""
            SELECT
                tp.id AS profile_id, tp.username, tp.tier, tp.category,
                tp.outreach_context,
                COALESCE(tp.followup_count, 0) AS followup_count,
                EXTRACT(DAY FROM NOW() - MAX(mq.sent_at)) AS days_since_sent
            FROM twitter_profiles tp
            JOIN message_queue mq ON mq.profile_id = tp.id
            WHERE tp.id = %s AND mq.status = 'sent'
            GROUP BY tp.id
        """, (profile_id,))

    return execute_query("""
        SELECT
            tp.id AS profile_id, tp.username, tp.tier, tp.category,
            tp.outreach_context,
            COALESCE(tp.followup_count, 0) AS followup_count,
            EXTRACT(DAY FROM NOW() - MAX(mq.sent_at)) AS days_since_sent
        FROM twitter_profiles tp
        JOIN message_queue mq ON mq.profile_id = tp.id
        WHERE
            mq.status = 'sent'
            AND tp.tier IN ('S', 'A', 'B', 'C')
            AND COALESCE(tp.followup_count, 0) < %s
            AND NOT EXISTS (
                SELECT 1 FROM message_queue mq2
                WHERE mq2.profile_id = tp.id AND mq2.replied_at IS NOT NULL
            )
        GROUP BY tp.id
        HAVING EXTRACT(DAY FROM NOW() - MAX(mq.sent_at)) >= %s
        ORDER BY
            CASE tp.tier WHEN 'S' THEN 1 WHEN 'A' THEN 2
                         WHEN 'B' THEN 3 ELSE 4 END,
            days_since_sent DESC
        LIMIT %s
    """, (MAX_FOLLOWUPS_PER_PROFILE, min_days, batch_size))


def _get_last_sent_message(profile_id):
    rows = execute_query("""
        SELECT message_text, account_id, sent_at
        FROM message_queue
        WHERE profile_id = %s AND status = 'sent'
        ORDER BY sent_at DESC LIMIT 1
    """, (profile_id,))
    return rows[0] if rows else None


def _parse_outreach_context(profile) -> dict:
    raw = profile.get("outreach_context")
    if not raw:
        return {}
    try:
        return json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return {}


def _save_followup(profile_id, account_id, message_text):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO message_queue
                    (profile_id, account_id, message_text, status, is_followup, variant)
                VALUES (%s, %s, %s, 'pending', true, 'A')
            """, (profile_id, account_id, message_text))
            cur.execute("""
                UPDATE twitter_profiles
                SET followup_count = COALESCE(followup_count, 0) + 1
                WHERE id = %s
            """, (profile_id,))
