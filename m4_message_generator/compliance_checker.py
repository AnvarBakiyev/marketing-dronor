"""
MKT-15 (covers MKT-16 scope): compliance_checker
Validates outreach messages before sending.
Checks: length, spam signals, Twitter ToS keywords, duplicate detection.
"""
import sys, json, re, hashlib
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from infra.db import execute_query, get_connection

# Phrases that trigger Twitter spam detection or violate ToS
SPAM_SIGNALS = [
    r'\bfollow back\b', r'\bcheck out my\b', r'\bmake money\b',
    r'\bearn \$', r'\bclick here\b', r'\blimited time\b',
    r'\b100%\s+free\b', r'\bguaranteed\b', r'\bno risk\b',
    r'\bact now\b', r'\bspecial offer\b', r'\bdm me\b',
]

SALESY_PHRASES = [
    r'\bour product\b', r'\bbuy now\b', r'\bprice\b',
    r'\bdiscount\b', r'\bsubscribe\b', r'\bsign up today\b',
    r'\bfree trial\b',
]

MAX_LENGTH = 280
MIN_LENGTH = 40


def compliance_checker(
    queue_item_id: int = 0,   # check specific item
    batch_size: int = 50,     # or check next N pending items
    auto_reject: bool = False # if True, mark failing items as failed
) -> dict:
    """
    Validate pending messages in message_queue.
    Returns per-item verdict and summary stats.
    """
    items = _get_items(queue_item_id, batch_size)
    if not items:
        return {"status": "success", "checked": 0, "passed": 0,
                "rejected": 0, "warnings": 0}

    checked = passed = rejected = warnings = 0
    results = []

    for item in items:
        checked += 1
        verdict = _check_message(item)

        if verdict["result"] == "pass":
            passed += 1
        elif verdict["result"] == "reject":
            rejected += 1
            if auto_reject:
                _mark_failed(item["id"], verdict["reason"])
        else:  # warning
            warnings += 1

        results.append({"id": item["id"], **verdict})

    return {
        "status": "success",
        "checked": checked,
        "passed": passed,
        "rejected": rejected,
        "warnings": warnings,
        "results": results,
    }


# ---------------------------------------------------------------------------
# Core check logic
# ---------------------------------------------------------------------------
def _check_message(item: dict) -> dict:
    text = (item.get("message_text") or "").strip()
    issues = []

    # 1. Length
    if len(text) > MAX_LENGTH:
        return {"result": "reject", "reason": f"Too long: {len(text)} chars (max {MAX_LENGTH})"}
    if len(text) < MIN_LENGTH:
        return {"result": "reject", "reason": f"Too short: {len(text)} chars (min {MIN_LENGTH})"}

    # 2. Spam signals (hard reject)
    for pattern in SPAM_SIGNALS:
        if re.search(pattern, text, re.IGNORECASE):
            return {"result": "reject", "reason": f"Spam signal: '{pattern}'"}

    # 3. Salesy phrases (warning)
    for pattern in SALESY_PHRASES:
        if re.search(pattern, text, re.IGNORECASE):
            issues.append(f"Salesy: '{pattern}'")

    # 4. Duplicate detection (same hash already sent/pending for same profile)
    text_hash = hashlib.md5(text.lower().strip().encode()).hexdigest()
    if _is_duplicate(item.get("profile_id"), text_hash, item["id"]):
        return {"result": "reject", "reason": "Duplicate message for this profile"}

    # 5. Placeholder check (unfilled template vars)
    if re.search(r'\{[a-z_]+\}', text):
        return {"result": "reject", "reason": "Unfilled template placeholder"}

    # 6. All-caps check
    words = text.split()
    caps_ratio = sum(1 for w in words if w.isupper() and len(w) > 2) / max(len(words), 1)
    if caps_ratio > 0.3:
        issues.append("High CAPS ratio")

    if issues:
        return {"result": "warning", "reason": "; ".join(issues)}

    return {"result": "pass", "reason": ""}


def _get_items(queue_item_id, batch_size):
    if queue_item_id:
        return execute_query(
            "SELECT * FROM message_queue WHERE id = %s", (queue_item_id,))
    return execute_query(
        "SELECT * FROM message_queue WHERE status = 'pending' "
        "ORDER BY created_at ASC LIMIT %s", (batch_size,))


def _is_duplicate(profile_id, text_hash, current_id):
    if not profile_id:
        return False
    rows = execute_query(
        "SELECT id FROM message_queue WHERE profile_id = %s "
        "AND md5(lower(trim(message_text))) = %s "
        "AND status IN ('pending','sent') AND id != %s",
        (profile_id, text_hash, current_id))
    return bool(rows)


def _mark_failed(item_id, reason):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE message_queue SET status='failed', failed_reason=%s "
                "WHERE id=%s", (reason, item_id))
