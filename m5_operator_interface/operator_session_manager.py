"""
MKT-22b: operator_session_manager
Manages operator work sessions: tracks active browser sessions,
operator throughput, pause/resume, and shift handover.
"""
import sys, json
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import disnet
from infra.db import execute_query, get_connection

SESSION_PREFIX = "op_session_"


def operator_session_manager(
    action: str = "status",       # start | pause | resume | end | status | stats
    operator_id: str = "",        # unique operator identifier
    account_id: int = 0,          # which Twitter account they're operating
    browser_session_id: str = ""  # from browser_controller
) -> dict:
    """
    Track operator work sessions for audit and throughput monitoring.

    actions:
      start   -> open new session, link browser_session_id + account
      pause   -> mark session as paused (break, review, etc.)
      resume  -> resume paused session
      end     -> close session, compute stats
      status  -> current session state for operator
      stats   -> aggregate stats (sent today, reply rate, active time)
    """
    ds = disnet.Disnet()

    if action == "start":
        return _start_session(ds, operator_id, account_id, browser_session_id)
    if action == "pause":
        return _update_state(ds, operator_id, "paused")
    if action == "resume":
        return _update_state(ds, operator_id, "active")
    if action == "end":
        return _end_session(ds, operator_id)
    if action == "status":
        return _get_status(ds, operator_id)
    if action == "stats":
        return _get_stats(operator_id)

    return {"status": "error", "message": f"Unknown action: {action}"}


# ---------------------------------------------------------------------------
def _session_key(operator_id: str) -> str:
    return f"{SESSION_PREFIX}{operator_id}"


def _start_session(ds, operator_id, account_id, browser_session_id):
    if not operator_id:
        return {"status": "error", "message": "operator_id required"}

    now = datetime.now(timezone.utc).isoformat()
    session = {
        "operator_id": operator_id,
        "account_id": account_id,
        "browser_session_id": browser_session_id,
        "state": "active",
        "started_at": now,
        "paused_at": None,
        "total_pause_seconds": 0,
        "sent_this_session": 0,
    }
    ds.set_value(_session_key(operator_id), json.dumps(session))
    _log_event(operator_id, "session_start", account_id)

    return {
        "status": "success",
        "session": session,
        "message": f"Session started for operator {operator_id}"
    }


def _update_state(ds, operator_id, new_state):
    raw = ds.get_value(_session_key(operator_id))
    if not raw:
        return {"status": "error", "message": "No active session found"}

    session = json.loads(raw)
    now = datetime.now(timezone.utc)

    if new_state == "paused" and session["state"] == "active":
        session["paused_at"] = now.isoformat()
    elif new_state == "active" and session["state"] == "paused":
        if session.get("paused_at"):
            paused_since = datetime.fromisoformat(session["paused_at"])
            session["total_pause_seconds"] += int((now - paused_since).total_seconds())
        session["paused_at"] = None

    session["state"] = new_state
    ds.set_value(_session_key(operator_id), json.dumps(session))
    _log_event(operator_id, f"session_{new_state}", session.get("account_id"))

    return {"status": "success", "state": new_state, "session": session}


def _end_session(ds, operator_id):
    raw = ds.get_value(_session_key(operator_id))
    if not raw:
        return {"status": "error", "message": "No active session found"}

    session = json.loads(raw)
    now = datetime.now(timezone.utc)
    started = datetime.fromisoformat(session["started_at"])
    total_seconds = int((now - started).total_seconds())
    active_seconds = total_seconds - session.get("total_pause_seconds", 0)

    session["state"] = "ended"
    session["ended_at"] = now.isoformat()
    session["total_seconds"] = total_seconds
    session["active_seconds"] = active_seconds

    # Archive to DB, clean up storage
    _archive_session(session)
    ds.set_value(_session_key(operator_id), json.dumps({"state": "ended", "ended_at": now.isoformat()}))
    _log_event(operator_id, "session_end", session.get("account_id"))

    return {
        "status": "success",
        "summary": {
            "sent": session.get("sent_this_session", 0),
            "active_minutes": round(active_seconds / 60, 1),
            "total_minutes": round(total_seconds / 60, 1),
        }
    }


def _get_status(ds, operator_id):
    if not operator_id:
        # Return all active sessions
        return {"status": "success", "message": "Specify operator_id for session status"}
    raw = ds.get_value(_session_key(operator_id))
    if not raw:
        return {"status": "success", "session": None, "message": "No active session"}
    return {"status": "success", "session": json.loads(raw)}


def _get_stats(operator_id):
    where = "WHERE operator_id = %s" if operator_id else ""
    params = (operator_id,) if operator_id else ()
    rows = execute_query(f"""
        SELECT
            operator_id,
            COUNT(*) AS sessions,
            SUM(sent_count) AS total_sent,
            ROUND(AVG(active_seconds) / 60.0, 1) AS avg_active_minutes
        FROM operator_sessions
        {where}
        GROUP BY operator_id
        ORDER BY total_sent DESC
    """, params)
    return {"status": "success", "stats": rows or []}


def _archive_session(session: dict) -> None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS operator_sessions (
                    id SERIAL PRIMARY KEY,
                    operator_id VARCHAR(100),
                    account_id INT,
                    started_at TIMESTAMP,
                    ended_at TIMESTAMP,
                    active_seconds INT,
                    sent_count INT DEFAULT 0
                )
            """)
            cur.execute("""
                INSERT INTO operator_sessions
                    (operator_id, account_id, started_at, ended_at, active_seconds, sent_count)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (
                session.get("operator_id"),
                session.get("account_id"),
                session.get("started_at"),
                session.get("ended_at"),
                session.get("active_seconds", 0),
                session.get("sent_this_session", 0),
            ))


def _log_event(operator_id, event, account_id):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS operator_events (
                        id SERIAL PRIMARY KEY,
                        operator_id VARCHAR(100),
                        event VARCHAR(50),
                        account_id INT,
                        created_at TIMESTAMP DEFAULT NOW()
                    )
                """)
                cur.execute(
                    "INSERT INTO operator_events (operator_id, event, account_id) VALUES (%s,%s,%s)",
                    (operator_id, event, account_id)
                )
    except Exception:
        pass
