"""
MKT-5: profile_enricher
Uses Claude Haiku to extract structured professional data from bio + tweets.
Fills: professional_role, industry, tech_stack, topics_of_interest, company_size.
"""
import sys
import json
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import anthropic
from infra.db import get_connection, execute_query

try:
    from infra.config import ANTHROPIC_API_KEY
except ImportError:
    raise RuntimeError("infra/config.py missing")

log = logging.getLogger(__name__)

EXTRACTION_PROMPT = """Analyze this Twitter profile and recent tweets. Extract structured professional data.

Profile:
Username: {username}
Bio: {bio}
Followers: {followers}
Following: {following}

Recent tweets (latest {tweet_count}):
{tweets}

Respond ONLY with valid JSON, no other text:
{{
  "professional_role": "<founder|engineer|marketer|researcher|investor|designer|product_manager|consultant|other>",
  "industry": "<SaaS|fintech|devtools|AI/ML|crypto|ecommerce|healthcare|education|agency|other>",
  "company_size": "<solo|startup_1_10|startup_11_50|mid_50_200|enterprise_200plus|unknown>",
  "tech_stack": ["<tool1>", "<tool2>"],
  "topics_of_interest": ["<topic1>", "<topic2>", "<topic3>"],
  "primary_language": "<en|ru|es|de|fr|other>",
  "avg_tweets_per_week": <float or null>,
  "confidence": <0.0-1.0>
}}

Rules:
- tech_stack: only actual tools/languages mentioned (max 8)
- topics_of_interest: main themes from tweets (max 5)
- If insufficient data, use "other" / "unknown" and set confidence < 0.4
- Never invent data not present in bio/tweets"""


def profile_enricher(
    profile_id: int = 0,
    batch_size: int = 20,
    min_tweets_required: int = 3
) -> dict:
    """
    Enrich profiles with LLM-extracted professional data.
    Single mode: provide profile_id.
    Batch mode: profile_id=0 processes oldest un-enriched profiles.

    Returns:
        {status, processed, enriched, skipped, errors, tokens_used}
    """
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    if profile_id:
        profiles = execute_query(
            "SELECT id, username, bio, followers_count, following_count FROM twitter_profiles WHERE id = %s",
            (profile_id,)
        )
    else:
        profiles = execute_query("""
            SELECT id, username, bio, followers_count, following_count
            FROM twitter_profiles
            WHERE professional_role IS NULL
              AND outreach_status = 'pending'
            ORDER BY followers_count DESC
            LIMIT %s
        """, (batch_size,))

    if not profiles:
        return {"status": "success", "processed": 0, "enriched": 0,
                "skipped": 0, "errors": 0, "tokens_used": 0}

    processed = enriched = skipped = errors = 0
    tokens_used = 0

    for profile in profiles:
        processed += 1
        try:
            tweets = execute_query("""
                SELECT text FROM profile_tweets
                WHERE profile_id = %s
                ORDER BY created_at DESC LIMIT 15
            """, (profile["id"],))

            if len(tweets) < min_tweets_required:
                log.info(f"Skipping {profile['username']}: only {len(tweets)} tweets")
                skipped += 1
                continue

            tweet_text = "\n".join(f"- {t['text'][:200]}" for t in tweets)

            prompt = EXTRACTION_PROMPT.format(
                username=profile["username"],
                bio=profile["bio"] or "",
                followers=profile["followers_count"],
                following=profile["following_count"],
                tweet_count=len(tweets),
                tweets=tweet_text
            )

            response = client.messages.create(
                model="claude-3-haiku-20240307",
                max_tokens=400,
                messages=[{"role": "user", "content": prompt}]
            )

            tokens_used += response.usage.input_tokens + response.usage.output_tokens
            raw = response.content[0].text.strip()

            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                # Try to extract JSON if model added extra text
                import re
                match = re.search(r'\{.*\}', raw, re.DOTALL)
                if match:
                    data = json.loads(match.group())
                else:
                    raise ValueError(f"No valid JSON in response: {raw[:100]}")

            _update_profile(profile["id"], data)
            enriched += 1
            log.info(f"Enriched {profile['username']}: role={data.get('professional_role')}, conf={data.get('confidence')}")

        except anthropic.RateLimitError:
            log.warning("Anthropic rate limit, stopping batch")
            errors += 1
            break
        except Exception as e:
            log.error(f"Error enriching {profile.get('username')}: {e}")
            errors += 1

    return {
        "status": "success",
        "processed": processed,
        "enriched": enriched,
        "skipped": skipped,
        "errors": errors,
        "tokens_used": tokens_used
    }


def _update_profile(profile_id: int, data: dict) -> None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE twitter_profiles SET
                    professional_role    = %s,
                    industry             = %s,
                    company_size         = %s,
                    tech_stack           = %s,
                    topics_of_interest   = %s,
                    primary_language     = COALESCE(%s, primary_language),
                    avg_tweets_per_week  = %s,
                    last_updated         = NOW()
                WHERE id = %s
            """, (
                data.get("professional_role"),
                data.get("industry"),
                data.get("company_size"),
                json.dumps(data.get("tech_stack", [])),
                json.dumps(data.get("topics_of_interest", [])),
                data.get("primary_language"),
                data.get("avg_tweets_per_week"),
                profile_id
            ))
