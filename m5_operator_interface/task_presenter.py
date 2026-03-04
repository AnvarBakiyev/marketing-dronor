"""
MKT-22a: task_presenter
Formats outreach tasks for human operator review in the Command Center.
Presents profile + message context in a structured, actionable format.
"""
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from infra.db import execute_query


def task_presenter(
    queue_item_id: int = 0,   # present specific item
    account_id: int = 0,      # present next task for this account
    batch_size: int = 10,     # how many tasks to return
    format_type: str = "full" # full | compact | ids_only
) -> dict:
    """
    Fetch and format pending outreach tasks for operator review.

    Returns structured task objects with:
    - Profile context (tier, bio, pain points, best_angle)
    - Message variants A and B
    - Account assignment
    - Compliance status
    """
    items = _get_items(queue_item_id, account_id, batch_size)
    if not items:
        return {"status": "success", "tasks": [], "count": 0}

    if format_type == "ids_only":
        return {"status": "success", "tasks": [i["id"] for i in items], "count": len(items)}

    tasks = []
    for item in items:
        profile = _get_profile(item.get("profile_id"))
        account = _get_account(item.get("account_id"))
        task = _format_task(item, profile, account, format_type)
        tasks.append(task)

    return {"status": "success", "tasks": tasks, "count": len(tasks)}


def _get_items(queue_item_id, account_id, batch_size):
    if queue_item_id:
        return execute_query(
            "SELECT * FROM message_queue WHERE id = %s", (queue_item_id,))
    if account_id:
        return execute_query("""
            SELECT * FROM message_queue
            WHERE account_id = %s AND status = 'pending'
            ORDER BY created_at ASC LIMIT %s
        """, (account_id, batch_size))
    return execute_query("""
        SELECT mq.* FROM message_queue mq
        JOIN twitter_profiles tp ON tp.id = mq.profile_id
        WHERE mq.status = 'pending' AND mq.account_id IS NOT NULL
        ORDER BY
            CASE tp.tier WHEN 'S' THEN 1 WHEN 'A' THEN 2
                         WHEN 'B' THEN 3 WHEN 'C' THEN 4 ELSE 5 END,
            mq.created_at ASC
        LIMIT %s
    """, (batch_size,))


def _get_profile(profile_id):
    if not profile_id:
        return {}
    rows = execute_query(
        "SELECT id, username, bio, tier, category, followers_count, "
        "identified_needs, tech_stack "
        "FROM twitter_profiles WHERE id = %s", (profile_id,))
    return rows[0] if rows else {}


def _get_account(account_id):
    if not account_id:
        return {}
    rows = execute_query(
        "SELECT id, username, state, health_score, replies_today "
        "FROM twitter_accounts WHERE id = %s", (account_id,))
    return rows[0] if rows else {}


def _format_task(item, profile, account, fmt):
    ctx = {}
    if profile.get("outreach_context"):
        try:
            ctx = json.loads(profile["outreach_context"]) \
                if isinstance(profile["outreach_context"], str) \
                else profile["outreach_context"]
        except Exception:
            pass

    task = {
        "queue_item_id": item["id"],
        "variant": item.get("variant"),
        "message": item.get("message_text", ""),
        "profile": {
            "id": profile.get("id"),
            "username": profile.get("username"),
            "tier": profile.get("tier"),
            "followers": profile.get("followers_count"),
            "url": f"https://twitter.com/{profile.get('username', '')}",
        },
        "account": {
            "id": account.get("id"),
            "username": account.get("username"),
            "health": account.get("health_score"),
            "replies_today": account.get("replies_today"),
        },
    }

    if fmt == "full":
        task["profile"]["bio"] = profile.get("bio", "")
        task["profile"]["category"] = profile.get("category", "")
        task["context"] = {
            "best_angle": ctx.get("best_angle", ""),
            "avoid": ctx.get("avoid", ""),
            "pain_points": ctx.get("pain_points", []),
            "tone": ctx.get("tone", ""),
        }

    return task
