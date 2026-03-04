"""
MKT-13: need_context_extractor
Extracts structured context from profile tweets for personalised outreach.
Outputs: specific pain points with source tweets, tech mentions, conversation topics.
"""
import sys, json, re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import anthropic
from infra.db import execute_query, get_connection
try:
    from infra.config import ANTHROPIC_API_KEY
except ImportError:
    raise RuntimeError("infra/config.py missing")

SYSTEM_PROMPT = """You extract structured context from Twitter profiles for B2B outreach personalisation.
Return ONLY valid JSON, no markdown, no explanation."""

USER_PROMPT = """Analyse this Twitter profile and extract outreach context.

Username: @{username}
Bio: {bio}
Recent tweets (newest first):
{tweets}

Return JSON:
{{
  "pain_points": [
    {{"pain": "specific pain in their own words", "evidence": "direct quote or paraphrase from tweet", "urgency": "high|medium|low"}}
  ],
  "tech_mentions": ["list of tools, languages, frameworks mentioned"],
  "topics": ["recurring themes in their tweets"],
  "tone": "technical|founder|casual|researcher",
  "best_angle": "one sentence: what specific value prop would resonate most",
  "avoid": "what NOT to say (generic pitches, topics they seem negative about)"
}}

Extract max 3 pain_points, max 10 tech_mentions, max 5 topics.
Be specific — use their actual words and context, not generic descriptions."""


def need_context_extractor(
    profile_id: int = 0,
    batch_size: int = 20,
    overwrite: bool = False    # re-extract even if context already exists
) -> dict:
    """
    Extract rich outreach context from profile tweets using Claude Haiku.
    Writes to twitter_profiles: outreach_context (JSONB), topics_of_interest,
    tech_stack fields.

    Returns: {status, processed, extracted, skipped, errors, tokens_used}
    """
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    profiles = _get_profiles(profile_id, batch_size, overwrite)

    if not profiles:
        return {"status": "success", "processed": 0, "extracted": 0,
                "skipped": 0, "errors": 0, "tokens_used": 0}

    processed = extracted = skipped = errors = 0
    tokens_used = 0

    for p in profiles:
        processed += 1
        try:
            tweets_text = _format_tweets(p)
            if not tweets_text and not p.get("bio"):
                skipped += 1
                continue

            prompt = USER_PROMPT.format(
                username=p["username"],
                bio=(p.get("bio") or "(no bio)")[:300],
                tweets=tweets_text or "(no tweets available)",
            )

            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=600,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}]
            )
            tokens_used += response.usage.input_tokens + response.usage.output_tokens
            raw = response.content[0].text.strip()

            context = _parse_json(raw)
            if not context:
                errors += 1
                continue

            _save_context(p["id"], context)
            extracted += 1

        except anthropic.RateLimitError:
            errors += 1
            break
        except Exception:
            errors += 1

    return {
        "status": "success",
        "processed": processed,
        "extracted": extracted,
        "skipped": skipped,
        "errors": errors,
        "tokens_used": tokens_used,
    }


# ---------------------------------------------------------------------------
def _get_profiles(profile_id, batch_size, overwrite):
    if profile_id:
        return execute_query(
            "SELECT * FROM twitter_profiles WHERE id = %s", (profile_id,))

    extra = "" if overwrite else "AND (outreach_context IS NULL OR outreach_context = '{}'::jsonb)"
    return execute_query(f"""
        SELECT * FROM twitter_profiles
        WHERE tier IS NOT NULL {extra}
        ORDER BY
            CASE tier WHEN 'S' THEN 1 WHEN 'A' THEN 2
                      WHEN 'B' THEN 3 WHEN 'C' THEN 4 ELSE 5 END,
            followers_count DESC
        LIMIT %s
    """, (batch_size,))


def _format_tweets(profile: dict) -> str:
    """Format recent_tweets JSONB field into a readable string."""
    raw = profile.get("recent_tweets")
    if not raw:
        return ""
    try:
        tweets = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(tweets, list):
            return ""
        lines = []
        for t in tweets[:15]:  # max 15 tweets for context window
            text = ""
            if isinstance(t, dict):
                text = t.get("text") or t.get("full_text") or ""
            elif isinstance(t, str):
                text = t
            text = text.strip()
            if text and len(text) > 10:
                # strip URLs to save tokens
                text = re.sub(r'https?://\S+', '[url]', text)
                lines.append(f"- {text[:200]}")
        return "\n".join(lines)
    except Exception:
        return ""


def _parse_json(raw: str) -> dict | None:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except Exception:
                pass
    return None


def _save_context(profile_id: int, ctx: dict) -> None:
    tech = ctx.get("tech_mentions", [])
    topics = ctx.get("topics", [])
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE twitter_profiles SET
                    outreach_context   = %s,
                    tech_stack         = %s,
                    topics_of_interest = %s
                WHERE id = %s
            """, (
                json.dumps(ctx),
                json.dumps(tech),
                json.dumps(topics),
                profile_id,
            ))
