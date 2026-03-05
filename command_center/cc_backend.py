"""
Marketing Dronor — Command Center Backend
MKT-33 | L2: Command Center
"""

import os
import psycopg2
import psycopg2.extras
from flask import Flask, jsonify, request

app = Flask(__name__, static_folder=".", static_url_path="")

DB_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://localhost/marketing_dronor"
)


def get_db():
    return psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)


def db_query(sql, params=None):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or [])
            return cur.fetchall()


def db_execute(sql, params=None):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or [])
            conn.commit()


# -- /api/accounts
@app.route("/api/accounts")
def accounts():
    rows = db_query("""
        SELECT
            ap.id, ap.serial_number, ap.adspower_profile_id,
            ta.username, ta.display_name, ta.category_focus,
            ap.warmup_stage, ap.warmup_day, ap.proxy_country, ap.proxy_city,
            ap.acquired_at, ap.last_started_at,
            ta.health_score, ta.suspended, ta.shadowban_detected,
            ta.total_sent, ta.total_responses,
            COALESCE(al.actions_today, 0) AS actions_today,
            COALESCE(al.dms_today, 0) AS dms_today,
            ar.restriction_type AS active_restriction
        FROM adspower_profiles ap
        LEFT JOIN twitter_accounts ta ON ta.id = ap.account_id
        LEFT JOIN (
            SELECT adspower_profile_id,
                COUNT(*) AS actions_today,
                COUNT(*) FILTER (WHERE action_type = 'send_dm') AS dms_today
            FROM activity_log
            WHERE executed_at::date = CURRENT_DATE AND status = 'success'
            GROUP BY adspower_profile_id
        ) al ON al.adspower_profile_id = ap.id
        LEFT JOIN (
            SELECT DISTINCT ON (adspower_profile_id)
                adspower_profile_id, restriction_type
            FROM account_restrictions
            WHERE is_active = TRUE
            ORDER BY adspower_profile_id, detected_at DESC
        ) ar ON ar.adspower_profile_id = ap.id
        ORDER BY ap.warmup_stage DESC, ap.warmup_day DESC
    """)
    return jsonify([dict(r) for r in rows])


@app.route("/api/accounts/create", methods=["POST"])
def create_account():
    data = request.json
    db_execute("""
        INSERT INTO twitter_accounts (username, display_name, category_focus, state)
        VALUES (%(username)s, %(display_name)s, %(category_focus)s, 'warming')
        ON CONFLICT (username) DO NOTHING
    """, data)
    acct = db_query("SELECT id FROM twitter_accounts WHERE username = %s", [data["username"]])[0]
    db_execute("""
        INSERT INTO adspower_profiles
            (account_id, serial_number, adspower_profile_id,
             proxy_host, proxy_port, proxy_user, proxy_pass,
             proxy_type, proxy_country, proxy_city, notes)
        VALUES
            (%(account_id)s, %(serial_number)s, %(adspower_profile_id)s,
             %(proxy_host)s, %(proxy_port)s, %(proxy_user)s, %(proxy_pass)s,
             %(proxy_type)s, %(proxy_country)s, %(proxy_city)s, %(notes)s)
    """, {**data, "account_id": acct["id"]})
    return jsonify({"status": "created"})


@app.route("/api/accounts/bulk-import", methods=["POST"])
def bulk_import():
    accounts_data = request.json
    created = 0
    for a in accounts_data:
        try:
            db_execute("""
                INSERT INTO twitter_accounts (username, category_focus, state)
                VALUES (%(username)s, %(category_focus)s, 'warming')
                ON CONFLICT (username) DO NOTHING
            """, a)
            acct = db_query("SELECT id FROM twitter_accounts WHERE username = %s", [a["username"]])[0]
            db_execute("""
                INSERT INTO adspower_profiles
                    (account_id, serial_number, proxy_host, proxy_port, proxy_user, proxy_pass, proxy_country)
                VALUES (%(account_id)s, %(serial_number)s, %(proxy_host)s, %(proxy_port)s,
                    %(proxy_user)s, %(proxy_pass)s, %(proxy_country)s)
                ON CONFLICT (serial_number) DO NOTHING
            """, {**a, "account_id": acct["id"]})
            created += 1
        except Exception:
            pass
    return jsonify({"status": "ok", "created": created})


@app.route("/api/accounts/<int:account_id>/status")
def account_status(account_id):
    profile = db_query("""
        SELECT ap.*, ta.username, ta.health_score, ta.total_sent, ta.total_responses
        FROM adspower_profiles ap
        LEFT JOIN twitter_accounts ta ON ta.id = ap.account_id
        WHERE ap.id = %s
    """, [account_id])
    if not profile:
        return jsonify({"error": "not found"}), 404
    activity = db_query("""
        SELECT action_type, status, target_username, executed_at, error_message
        FROM activity_log WHERE adspower_profile_id = %s
        ORDER BY executed_at DESC LIMIT 50
    """, [account_id])
    restrictions = db_query("""
        SELECT restriction_type, detected_at, lifted_at, is_active, notes
        FROM account_restrictions WHERE adspower_profile_id = %s
        ORDER BY detected_at DESC
    """, [account_id])
    schedule = db_query("""
        SELECT * FROM warmup_schedule
        WHERE adspower_profile_id = %s AND schedule_date = CURRENT_DATE
    """, [account_id])
    return jsonify({
        "profile": dict(profile[0]),
        "activity": [dict(r) for r in activity],
        "restrictions": [dict(r) for r in restrictions],
        "today_schedule": dict(schedule[0]) if schedule else None
    })


# -- /api/tasks
@app.route("/api/tasks")
def tasks():
    status_filter = request.args.get("status", "pending")
    rows = db_query("""
        SELECT mq.id, mq.message_type, mq.priority, mq.tier, mq.category,
            mq.identified_need, mq.status, mq.created_at,
            tp.username AS target_username, tp.display_name AS target_display,
            ta.username AS account_username, mq.message_text
        FROM message_queue mq
        LEFT JOIN twitter_profiles tp ON tp.id = mq.profile_id
        LEFT JOIN twitter_accounts ta ON ta.id = mq.account_id
        WHERE mq.status = %s
        ORDER BY
            CASE mq.priority
                WHEN 'P0' THEN 0 WHEN 'P1' THEN 1 WHEN 'P2' THEN 2
                WHEN 'P3' THEN 3 WHEN 'P4' THEN 4 ELSE 5
            END, mq.created_at ASC
        LIMIT 100
    """, [status_filter])
    return jsonify([dict(r) for r in rows])


@app.route("/api/tasks/pause-all", methods=["POST"])
def pause_all():
    db_execute("UPDATE message_queue SET status = 'skipped' WHERE status = 'pending'")
    return jsonify({"status": "paused"})


@app.route("/api/tasks/resume-all", methods=["POST"])
def resume_all():
    db_execute("UPDATE message_queue SET status = 'pending' WHERE status = 'skipped'")
    return jsonify({"status": "resumed"})


# -- /api/metrics
@app.route("/api/metrics")
def metrics():
    today = db_query("""
        SELECT
            COUNT(*) FILTER (WHERE action_type = 'send_dm' AND status = 'success') AS dms_sent,
            COUNT(*) FILTER (WHERE action_type = 'like_tweet' AND status = 'success') AS likes_done,
            COUNT(*) FILTER (WHERE action_type = 'follow_user' AND status = 'success') AS follows_done,
            COUNT(*) FILTER (WHERE status = 'failed') AS errors_today
        FROM activity_log WHERE executed_at::date = CURRENT_DATE
    """)[0]
    fleet = db_query("""
        SELECT COUNT(*) AS total,
            COUNT(*) FILTER (WHERE warmup_stage = 'active') AS active,
            COUNT(*) FILTER (WHERE warmup_stage = 'warming') AS warming,
            COUNT(*) FILTER (WHERE warmup_stage = 'new') AS new_accts,
            COUNT(*) FILTER (WHERE warmup_stage IN ('restricted','banned')) AS restricted
        FROM adspower_profiles
    """)[0]
    responses = db_query("""
        SELECT COUNT(*) AS count FROM conversations
        WHERE first_response_at::date = CURRENT_DATE
    """)[0]
    queue_stats = db_query("""
        SELECT COUNT(*) FILTER (WHERE status = 'pending') AS pending,
            COUNT(*) FILTER (WHERE status = 'in_review') AS in_review,
            COUNT(*) FILTER (WHERE status = 'sent') AS sent_total
        FROM message_queue
    """)[0]
    cost_today = db_query("""
        SELECT COALESCE(SUM(cost_usd), 0) AS total_cost
        FROM twitterapi_calls_log WHERE called_at::date = CURRENT_DATE
    """)[0]
    return jsonify({
        "today": dict(today), "fleet": dict(fleet),
        "responses_today": responses["count"],
        "queue": dict(queue_stats),
        "api_cost_today_usd": float(cost_today["total_cost"])
    })


# -- /api/timeline
@app.route("/api/timeline")
def timeline():
    rows = db_query("""
        SELECT al.id, al.action_type, al.target_username, al.status,
            al.executed_at, al.error_message, al.duration_ms,
            ap.serial_number, ta.username AS account_username
        FROM activity_log al
        LEFT JOIN adspower_profiles ap ON ap.id = al.adspower_profile_id
        LEFT JOIN twitter_accounts ta ON ta.id = al.account_id
        ORDER BY al.executed_at DESC LIMIT 50
    """)
    return jsonify([dict(r) for r in rows])


@app.route("/")
def index():
    return app.send_static_file("cc_frontend.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5555, debug=True)

# ── NEW OPERATOR ENDPOINTS ────────────────────────────────────────────────────

import subprocess, sys, json as _json
from pathlib import Path

PROJECT = Path(__file__).parent.parent
PYTHON  = sys.executable

# Settings: read/write infra/config.py
@app.route("/api/settings", methods=["GET"])
def get_settings():
    cfg = PROJECT / "infra" / "config.py"
    result = {"twitter_bearer_token": "", "anthropic_api_key": "", "adspower_url": "http://localhost:50325"}
    if cfg.exists():
        txt = cfg.read_text()
        import re
        for key, var in [("twitter_bearer_token","TWITTER_BEARER_TOKEN"),("anthropic_api_key","ANTHROPIC_API_KEY")]:
            m = re.search(rf'{var}\s*=\s*["\']([^"\']*)["\']', txt)
            if m: result[key] = "***" if m.group(1) else ""
        result["config_exists"] = True
    else:
        result["config_exists"] = False
    # check adspower
    try:
        import urllib.request
        with urllib.request.urlopen("http://localhost:50325/status", timeout=2): result["adspower_online"] = True
    except: result["adspower_online"] = False
    return jsonify(result)

@app.route("/api/settings", methods=["POST"])
def save_settings():
    data = request.json
    cfg = PROJECT / "infra" / "config.py"
    existing = {"TWITTER_BEARER_TOKEN": "", "ANTHROPIC_API_KEY": ""}
    if cfg.exists():
        import re
        txt = cfg.read_text()
        for var in existing:
            m = re.search(rf'{var}\s*=\s*["\']([^"\']*)["\']', txt)
            if m and m.group(1): existing[var] = m.group(1)
    tw  = data.get("twitter_bearer_token") or existing["TWITTER_BEARER_TOKEN"]
    ant = data.get("anthropic_api_key")    or existing["ANTHROPIC_API_KEY"]
    import getpass
    cfg.write_text(f'DB_CONFIG = {{"host": "localhost", "port": 5432, "dbname": "marketing_dronor", "user": "{getpass.getuser()}", "password": ""}}\nTWITTER_BEARER_TOKEN = "{tw}"\nANTHROPIC_API_KEY = "{ant}"\n')
    return jsonify({"status": "saved"})

# Tasks: approve / reject single message
@app.route("/api/tasks/<int:task_id>/approve", methods=["POST"])
def approve_task(task_id):
    db_execute("UPDATE message_queue SET status='approved' WHERE id=%s", [task_id])
    return jsonify({"status": "approved"})

@app.route("/api/tasks/<int:task_id>/reject", methods=["POST"])
def reject_task(task_id):
    db_execute("UPDATE message_queue SET status='rejected' WHERE id=%s", [task_id])
    return jsonify({"status": "rejected"})

@app.route("/api/tasks/approve-all", methods=["POST"])
def approve_all():
    db_execute("UPDATE message_queue SET status='approved' WHERE status='in_review'")
    return jsonify({"status": "ok"})

# Pipeline: run module
@app.route("/api/run/<module>", methods=["POST"])
def run_module(module):
    allowed = {"m1": "m1_data_collector", "m4": "m4_message_generator",
               "m3": "m3_account_manager", "m2": "m2_profile_analyzer"}
    if module not in allowed: return jsonify({"error": "unknown module"}), 400
    params = request.json or {}
    script = f"""
import sys; sys.path.insert(0,'{PROJECT}')
"""
    if module == "m1":
        script += "from m1_data_collector.collection_scheduler import run_collection; print(run_collection(**" + repr(params) + "))"
    elif module == "m4":
        script += "from m4_message_generator.message_generator import message_generator; print(message_generator(**" + repr(params) + "))"
    elif module == "m2":
        script += "from m2_profile_analyzer.wave_classifier import classify_batch; print(classify_batch(**" + repr(params) + "))"
    elif module == "m3":
        script += "from m3_account_manager.warmup_scheduler import run_warmup_cycle; print(run_warmup_cycle(**" + repr(params) + "))"
    try:
        result = subprocess.run([PYTHON, "-c", script], capture_output=True, text=True, timeout=120, cwd=str(PROJECT))
        return jsonify({"status": "ok", "stdout": result.stdout[-2000:], "stderr": result.stderr[-500:], "returncode": result.returncode})
    except subprocess.TimeoutExpired:
        return jsonify({"status": "timeout", "stdout": "", "stderr": "timed out after 120s", "returncode": -1})

# Accounts: delete
@app.route("/api/accounts/<int:account_id>", methods=["DELETE"])
def delete_account(account_id):
    db_execute("DELETE FROM adspower_profiles WHERE id=%s", [account_id])
    return jsonify({"status": "deleted"})

# Profiles count for dashboard
@app.route("/api/profiles/stats")
def profiles_stats():
    r = db_query("SELECT COUNT(*) AS total, COUNT(*) FILTER(WHERE outreach_status='pending') AS pending, COUNT(*) FILTER(WHERE tier IS NOT NULL) AS enriched, COUNT(*) FILTER(WHERE outreach_status='sent') AS contacted FROM twitter_profiles")[0]
    return jsonify(dict(r))
