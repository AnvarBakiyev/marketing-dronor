"""
MKT-14: message_generator
Generates personalised outreach messages via Claude Haiku.
Consumes enriched profile data (tier, category, needs, tech_stack).
"""
import sys, json, logging, re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import anthropic
from infra.db import get_connection, execute_query
try:
    from infra.config import ANTHROPIC_API_KEY
except ImportError:
    raise RuntimeError("infra/config.py missing")

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt templates per tier
# ---------------------------------------------------------------------------
TIER_CONTEXT = {
    "S": "This is a top-tier influencer (50K+ followers). Be very concise, peer-to-peer tone. No fluff.",
    "A": "Founder/senior engineer with 5-50K followers. Lead with specific technical value.",
    "B": "Active practitioner in AI/SaaS/devtools. Show you understand their work.",
    "C": "Engaged community member. Friendly, curious tone. Invite a conversation.",
    "D": "Low-engagement profile. Keep it minimal and non-intrusive.",
}

CATEGORY_ANGLE = {
    "automation_builder":  "Dronor lets you build modular automation pipelines with experts that compose like Lego.",
    "ai_researcher":       "Dronor is an open platform to deploy and chain your own AI experts with full control.",
    "solopreneur":         "Dronor acts as your personal AI partner — handles workflows so you focus on building.",
    "dev_productivity":    "Dronor turns repetitive dev tasks into experts you run once and reuse forever.",
    "content_creator":     "Dronor automates your content pipeline — research, drafting, scheduling, all modular.",
    "data_analyst":        "Dronor chains data-fetching and analysis experts into reproducible pipelines.",
    "startup_founder":     "Dronor gives your startup an autonomous AI layer without a dedicated ops team.",
    "enterprise_ops":      "Dronor standardises complex workflows into auditable, reusable expert pipelines.",
    "other":               "Dronor is a Personal AGI platform — modular, composable, open-source.",
}

SYSTEM_PROMPT = """You write short, genuine Twitter DMs for Dronor outreach.
Rules:
- Max 3 sentences. Never exceed 280 characters.
- No emojis unless the profile uses them heavily.
- No generic AI hype. Be specific to the person.
- End with a single soft CTA: either a question or a link offer.
- Never mention "AI automation" generically — use the person's actual context.
- Sound like a real founder reaching out, not a marketing bot."""

USER_PROMPT = """Write a Twitter DM for this profile:

Username: @{username}
Bio: {bio}
Tier: {tier} — {tier_context}
Category: {category} — {category_angle}
Pain points: {needs}
Tech stack: {tech_stack}
Recent tweet themes: {topics}

Generate 2 variants (A and B) with different angles. Return JSON only:
{{"variant_a": "...", "variant_b": "..."}}"""


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------
def message_generator(
    profile_id: int = 0,
    batch_size: int = 20,
    tier_filter: str = "",       # e.g. "A" or "B" — empty = all tiers
    dry_run: bool = False        # True = return messages without saving
) -> dict:
    """
    Generate personalised outreach messages for enriched profiles.
    Saves results to message_queue table.

    Returns: {status, processed, generated, skipped, errors, tokens_used}
    """
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    profiles = _get_profiles(profile_id, batch_size, tier_filter)
    if not profiles:
        return {"status": "success", "processed": 0, "generated": 0,
                "skipped": 0, "errors": 0, "tokens_used": 0}

    processed = generated = skipped = errors = 0
    tokens_used = 0

    for p in profiles:
        processed += 1
        try:
            if not p.get("tier") or not p.get("category"):
                log.info(f"Skipping @{p['username']}: not enriched")
                skipped += 1
                continue

            prompt = USER_PROMPT.format(
                username=p["username"],
                bio=(p.get("bio") or "")[:300],
                tier=p["tier"],
                tier_context=TIER_CONTEXT.get(p["tier"], ""),
                category=p["category"],
                category_angle=CATEGORY_ANGLE.get(p["category"], CATEGORY_ANGLE["other"]),
                needs=_fmt_needs(p.get("identified_needs")),
                tech_stack=_fmt_list(p.get("tech_stack")),
                topics=_fmt_list(p.get("topics_of_interest")),
            )

            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=300,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}]
            )
            tokens_used += response.usage.input_tokens + response.usage.output_tokens
            raw = response.content[0].text.strip()

            variants = _parse_variants(raw)
            if not variants:
                log.warning(f"Bad JSON for @{p['username']}: {raw[:80]}")
                errors += 1
                continue

            if not dry_run:
                _save_to_queue(p["id"], variants)

            generated += 1
            log.info(f"Generated for @{p['username']} (tier={p['tier']})")

        except anthropic.RateLimitError:
            log.warning("Rate limit — stopping batch")
            errors += 1
            break
        except Exception as e:
            log.error(f"Error for @{p.get('username')}: {e}")
            errors += 1

    return {
        "status": "success",
        "processed": processed,
        "generated": generated,
        "skipped": skipped,
        "errors": errors,
        "tokens_used": tokens_used,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _get_profiles(profile_id, batch_size, tier_filter):
    if profile_id:
        return execute_query(
            "SELECT * FROM twitter_profiles WHERE id = %s", (profile_id,))

    where = "outreach_status = 'pending' AND professional_role IS NOT NULL AND tier IS NOT NULL"
    params = [batch_size]
    if tier_filter:
        where += " AND tier = %s"
        params.insert(0, tier_filter)
        return execute_query(
            f"SELECT * FROM twitter_profiles WHERE {where} ORDER BY followers_count DESC LIMIT %s",
            tuple(params))

    return execute_query(
        f"SELECT * FROM twitter_profiles WHERE {where} ORDER BY "
        "CASE tier WHEN 'S' THEN 1 WHEN 'A' THEN 2 WHEN 'B' THEN 3 WHEN 'C' THEN 4 ELSE 5 END, "
        "followers_count DESC LIMIT %s",
        (batch_size,))


def _fmt_needs(needs_json) -> str:
    if not needs_json:
        return "not identified"
    try:
        needs = json.loads(needs_json) if isinstance(needs_json, str) else needs_json
        return "; ".join(n.get("need", "") for n in needs[:3] if n.get("need"))
    except Exception:
        return str(needs_json)[:100]


def _fmt_list(val) -> str:
    if not val:
        return "none"
    try:
        lst = json.loads(val) if isinstance(val, str) else val
        return ", ".join(lst[:5]) if lst else "none"
    except Exception:
        return str(val)[:80]


def _parse_variants(raw: str) -> dict | None:
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


def _save_to_queue(profile_id: int, variants: dict) -> None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            for variant_key in ("variant_a", "variant_b"):
                msg = variants.get(variant_key, "")
                if not msg:
                    continue
                cur.execute("""
                    INSERT INTO message_queue
                        (profile_id, message_text, variant, status)
                    VALUES (%s, %s, %s, 'pending')
                    ON CONFLICT DO NOTHING
                """, (profile_id, msg, variant_key[-1].upper()))
