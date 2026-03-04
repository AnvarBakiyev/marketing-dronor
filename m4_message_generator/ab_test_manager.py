"""
MKT-17: ab_test_manager
Manages A/B testing of message variants.
Tracks send counts, reply rates, and declares winners.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from infra.db import execute_query, get_connection

MIN_SENDS_FOR_WINNER = 30   # minimum sends per variant before declaring winner
MIN_REPLY_RATE_DIFF = 0.05  # 5pp difference required to call a winner


def ab_test_manager(
    action: str = "stats",        # stats | pick_variant | record_reply | declare_winner | reset
    profile_tier: str = "",       # for pick_variant: S/A/B/C/D
    queue_item_id: int = 0,       # for record_reply
    force_variant: str = "",      # for pick_variant: force A or B
) -> dict:
    """
    A/B test manager for message variants.

    actions:
      stats          -> current stats per variant per tier
      pick_variant   -> returns A or B for next send (balances until winner known)
      record_reply   -> marks queue_item as replied, updates variant stats
      declare_winner -> evaluate stats and mark winning variant per tier
      reset          -> clear stats for a tier (use with caution)
    """
    if action == "stats":
        return _get_stats(profile_tier)

    if action == "pick_variant":
        return _pick_variant(profile_tier, force_variant)

    if action == "record_reply":
        return _record_reply(queue_item_id)

    if action == "declare_winner":
        return _declare_winner(profile_tier)

    if action == "reset":
        return _reset_stats(profile_tier)

    return {"status": "error", "message": f"Unknown action: {action}"}


# ---------------------------------------------------------------------------
def _get_stats(tier_filter: str) -> dict:
    where = "WHERE tier = %s" if tier_filter else ""
    params = (tier_filter,) if tier_filter else ()
    rows = execute_query(f"""
        SELECT
            tp.tier,
            mq.variant,
            COUNT(*) AS sends,
            SUM(CASE WHEN mq.replied_at IS NOT NULL THEN 1 ELSE 0 END) AS replies,
            ROUND(
                100.0 * SUM(CASE WHEN mq.replied_at IS NOT NULL THEN 1 ELSE 0 END)
                / NULLIF(COUNT(*), 0), 2
            ) AS reply_rate_pct
        FROM message_queue mq
        JOIN twitter_profiles tp ON tp.id = mq.profile_id
        WHERE mq.status IN ('sent', 'replied') AND mq.variant IS NOT NULL
        {('AND tp.tier = %s' if tier_filter else '')}
        GROUP BY tp.tier, mq.variant
        ORDER BY tp.tier, mq.variant
    """, params)

    winners = execute_query(
        "SELECT tier, winning_variant FROM ab_test_winners"
        + (" WHERE tier = %s" if tier_filter else ""),
        params
    ) if _table_exists('ab_test_winners') else []
    winner_map = {w['tier']: w['winning_variant'] for w in (winners or [])}

    return {
        "status": "success",
        "stats": rows or [],
        "winners": winner_map,
    }


def _pick_variant(tier: str, force: str) -> dict:
    if force in ('A', 'B'):
        return {"status": "success", "variant": force, "reason": "forced"}

    # Check if winner already declared for this tier
    if _table_exists('ab_test_winners') and tier:
        rows = execute_query(
            "SELECT winning_variant FROM ab_test_winners WHERE tier = %s", (tier,))
        if rows and rows[0].get('winning_variant'):
            return {"status": "success", "variant": rows[0]['winning_variant'],
                    "reason": "winner_declared"}

    # Balance: pick whichever variant has fewer sends for this tier
    if tier:
        rows = execute_query("""
            SELECT mq.variant, COUNT(*) AS sends
            FROM message_queue mq
            JOIN twitter_profiles tp ON tp.id = mq.profile_id
            WHERE tp.tier = %s AND mq.variant IS NOT NULL
            GROUP BY mq.variant
        """, (tier,))
    else:
        rows = execute_query("""
            SELECT variant, COUNT(*) AS sends
            FROM message_queue
            WHERE variant IS NOT NULL
            GROUP BY variant
        """)

    counts = {r['variant']: r['sends'] for r in (rows or [])}
    a_sends = counts.get('A', 0)
    b_sends = counts.get('B', 0)
    variant = 'A' if a_sends <= b_sends else 'B'

    return {
        "status": "success",
        "variant": variant,
        "reason": "balanced",
        "a_sends": a_sends,
        "b_sends": b_sends,
    }


def _record_reply(queue_item_id: int) -> dict:
    if not queue_item_id:
        return {"status": "error", "message": "queue_item_id required"}
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE message_queue
                SET replied_at = NOW(), status = 'replied'
                WHERE id = %s AND status = 'sent'
            """, (queue_item_id,))
            updated = cur.rowcount
    return {
        "status": "success",
        "updated": updated,
        "message": "Reply recorded" if updated else "Item not found or not in sent status",
    }


def _declare_winner(tier: str) -> dict:
    tiers = [tier] if tier else ['S', 'A', 'B', 'C', 'D']
    results = []

    for t in tiers:
        rows = execute_query("""
            SELECT
                mq.variant,
                COUNT(*) AS sends,
                SUM(CASE WHEN mq.replied_at IS NOT NULL THEN 1 ELSE 0 END) AS replies
            FROM message_queue mq
            JOIN twitter_profiles tp ON tp.id = mq.profile_id
            WHERE tp.tier = %s AND mq.variant IS NOT NULL
              AND mq.status IN ('sent', 'replied')
            GROUP BY mq.variant
        """, (t,))

        stats = {r['variant']: r for r in (rows or [])}
        a = stats.get('A', {})
        b = stats.get('B', {})

        a_sends = a.get('sends', 0) or 0
        b_sends = b.get('sends', 0) or 0

        if a_sends < MIN_SENDS_FOR_WINNER or b_sends < MIN_SENDS_FOR_WINNER:
            results.append({"tier": t, "winner": None,
                             "reason": f"Not enough data (A:{a_sends}, B:{b_sends})"})
            continue

        a_rate = (a.get('replies', 0) or 0) / a_sends
        b_rate = (b.get('replies', 0) or 0) / b_sends

        if abs(a_rate - b_rate) < MIN_REPLY_RATE_DIFF:
            results.append({"tier": t, "winner": None,
                             "reason": f"No significant diff (A:{a_rate:.1%}, B:{b_rate:.1%})"})
            continue

        winner = 'A' if a_rate > b_rate else 'B'
        _save_winner(t, winner)
        results.append({"tier": t, "winner": winner,
                         "a_rate": f"{a_rate:.1%}", "b_rate": f"{b_rate:.1%}"})

    return {"status": "success", "results": results}


def _save_winner(tier: str, variant: str) -> None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS ab_test_winners (
                    tier VARCHAR(1) PRIMARY KEY,
                    winning_variant VARCHAR(1) NOT NULL,
                    declared_at TIMESTAMP DEFAULT NOW()
                )
            """)
            cur.execute("""
                INSERT INTO ab_test_winners (tier, winning_variant)
                VALUES (%s, %s)
                ON CONFLICT (tier) DO UPDATE
                SET winning_variant = EXCLUDED.winning_variant,
                    declared_at = NOW()
            """, (tier, variant))


def _reset_stats(tier: str) -> dict:
    if not tier:
        return {"status": "error", "message": "tier required for reset"}
    if _table_exists('ab_test_winners'):
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM ab_test_winners WHERE tier = %s", (tier,))
    return {"status": "success", "message": f"Winner reset for tier {tier}"}


def _table_exists(table: str) -> bool:
    rows = execute_query(
        "SELECT 1 FROM information_schema.tables WHERE table_name = %s", (table,))
    return bool(rows)
