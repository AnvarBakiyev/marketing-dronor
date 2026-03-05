"""
Marketing Dronor — Command Center Backend
MKT-33, MKT-49, MKT-50, MKT-51 | L2: Command Center + Auth + Queue Isolation + Admin
"""

import os
import uuid
import bcrypt
import psycopg2
import psycopg2.extras
from functools import wraps
from flask import Flask, jsonify, request
from datetime import datetime

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


def db_query_one(sql, params=None):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or [])
            return cur.fetchone()


def db_execute(sql, params=None):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or [])
            conn.commit()
            try:
                return cur.fetchall()
            except:
                return None


# =============================================================================
# MKT-49: AUTH SYSTEM
# =============================================================================

def ensure_auth_tables():
    """Create auth tables if not exist"""
    db_execute("""
        CREATE TABLE IF NOT EXISTS operators (
            id serial PRIMARY KEY,
            name varchar NOT NULL UNIQUE,
            pin_hash varchar NOT NULL,
            role varchar DEFAULT 'operator' CHECK (role IN ('operator','admin')),
            is_active bool DEFAULT true,
            created_at timestamp DEFAULT now()
        )
    """)
    db_execute("""
        CREATE TABLE IF NOT EXISTS operator_sessions (
            token varchar PRIMARY KEY,
            operator_id int REFERENCES operators(id),
            created_at timestamp DEFAULT now(),
            last_active timestamp DEFAULT now()
        )
    """)
    # Add claimed_by to message_queue if not exists
    try:
        db_execute("""
            ALTER TABLE message_queue 
            ADD COLUMN IF NOT EXISTS claimed_by int REFERENCES operators(id),
            ADD COLUMN IF NOT EXISTS claimed_at timestamp
        """)
    except:
        pass


# Initialize tables on startup
try:
    ensure_auth_tables()
except:
    pass  # Tables may already exist


def check_setup_required():
    """Check if no operators exist (first setup)"""
    result = db_query_one("SELECT COUNT(*) as cnt FROM operators")
    return result["cnt"] == 0 if result else True


def get_operator_from_token(token):
    """Get operator from session token"""
    if not token:
        return None
    session = db_query_one("""
        SELECT o.id, o.name, o.role, o.is_active
        FROM operator_sessions os
        JOIN operators o ON o.id = os.operator_id
        WHERE os.token = %s AND o.is_active = true
    """, [token])
    if session:
        # Update last_active
        db_execute("""
            UPDATE operator_sessions SET last_active = now() WHERE token = %s
        """, [token])
    return session


def require_auth(f):
    """Decorator to require authentication"""
    @wraps(f)
    def decorated(*args, **kwargs):
        # Check if setup required
        if check_setup_required():
            return jsonify({"setup_required": True}), 401
        
        token = request.headers.get("X-Operator-Token")
        operator = get_operator_from_token(token)
        if not operator:
            return jsonify({"error": "Unauthorized"}), 401
        
        request.operator = operator
        return f(*args, **kwargs)
    return decorated


def require_admin(f):
    """Decorator to require admin role"""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get("X-Operator-Token")
        operator = get_operator_from_token(token)
        if not operator or operator["role"] != "admin":
            return jsonify({"error": "Admin required"}), 403
        
        request.operator = operator
        return f(*args, **kwargs)
    return decorated


# -- Auth endpoints
@app.route("/api/auth/login", methods=["POST"])
def auth_login():
    data = request.json or {}
    name = data.get("name", "").strip()
    pin = data.get("pin", "")
    
    if not name or not pin:
        return jsonify({"error": "Name and PIN required"}), 400
    
    operator = db_query_one("""
        SELECT id, name, pin_hash, role, is_active
        FROM operators WHERE name = %s
    """, [name])
    
    if not operator:
        return jsonify({"error": "Invalid credentials"}), 401
    
    if not operator["is_active"]:
        return jsonify({"error": "Account disabled"}), 401
    
    # Verify PIN
    if not bcrypt.checkpw(pin.encode(), operator["pin_hash"].encode()):
        return jsonify({"error": "Invalid credentials"}), 401
    
    # Create session
    token = str(uuid.uuid4())
    db_execute("""
        INSERT INTO operator_sessions (token, operator_id)
        VALUES (%s, %s)
    """, [token, operator["id"]])
    
    return jsonify({
        "token": token,
        "name": operator["name"],
        "role": operator["role"]
    })


@app.route("/api/auth/me")
def auth_me():
    token = request.headers.get("X-Operator-Token")
    if check_setup_required():
        return jsonify({"setup_required": True})
    
    operator = get_operator_from_token(token)
    if not operator:
        return jsonify({"error": "Unauthorized"}), 401
    
    return jsonify({
        "id": operator["id"],
        "name": operator["name"],
        "role": operator["role"]
    })


@app.route("/api/auth/logout", methods=["POST"])
def auth_logout():
    token = request.headers.get("X-Operator-Token")
    if token:
        db_execute("DELETE FROM operator_sessions WHERE token = %s", [token])
    return jsonify({"status": "ok"})


@app.route("/api/auth/setup", methods=["POST"])
def auth_setup():
    """First-time setup: create admin account"""
    if not check_setup_required():
        return jsonify({"error": "Setup already completed"}), 400
    
    data = request.json or {}
    name = data.get("name", "").strip()
    pin = data.get("pin", "")
    
    if not name or len(pin) < 4:
        return jsonify({"error": "Name and PIN (min 4 chars) required"}), 400
    
    # Hash PIN
    pin_hash = bcrypt.hashpw(pin.encode(), bcrypt.gensalt()).decode()
    
    db_execute("""
        INSERT INTO operators (name, pin_hash, role)
        VALUES (%s, %s, 'admin')
    """, [name, pin_hash])
    
    return jsonify({"status": "ok", "message": "Admin account created"})


# =============================================================================
# MKT-50: QUEUE ISOLATION (claim/lock system)
# =============================================================================

@app.route("/api/tasks/claim", methods=["POST"])
@require_auth
def claim_tasks():
    """Atomically claim pending tasks for operator"""
    data = request.json or {}
    limit = min(data.get("limit", 20), 50)  # Max 50
    operator_id = request.operator["id"]
    
    # Atomic claim with FOR UPDATE SKIP LOCKED
    claimed = db_execute("""
        UPDATE message_queue 
        SET claimed_by = %(op)s, claimed_at = now()
        WHERE id IN (
            SELECT id FROM message_queue 
            WHERE status = 'pending' AND claimed_by IS NULL 
            ORDER BY 
                CASE priority
                    WHEN 'P0' THEN 0 WHEN 'P1' THEN 1 WHEN 'P2' THEN 2
                    WHEN 'P3' THEN 3 WHEN 'P4' THEN 4 ELSE 5
                END, 
                created_at ASC
            LIMIT %(limit)s
            FOR UPDATE SKIP LOCKED
        )
        RETURNING id, message_type, priority, tier, category,
            identified_need, status, created_at, message_text
    """, {"op": operator_id, "limit": limit})
    
    return jsonify({"claimed": len(claimed) if claimed else 0, "tasks": claimed or []})


@app.route("/api/tasks/release", methods=["POST"])
@require_auth
def release_tasks():
    """Release claimed tasks back to queue"""
    data = request.json or {}
    task_ids = data.get("task_ids", [])
    operator_id = request.operator["id"]
    
    if task_ids:
        db_execute("""
            UPDATE message_queue 
            SET claimed_by = NULL, claimed_at = NULL
            WHERE id = ANY(%s) AND claimed_by = %s
        """, [task_ids, operator_id])
    
    return jsonify({"status": "ok"})


@app.route("/api/tasks/my")
@require_auth
def my_tasks():
    """Get tasks claimed by current operator"""
    operator_id = request.operator["id"]
    
    rows = db_query("""
        SELECT mq.id, mq.message_type, mq.priority, mq.tier, mq.category,
            mq.identified_need, mq.status, mq.created_at, mq.claimed_at,
            tp.username AS target_username, tp.display_name AS target_display,
            ta.username AS account_username, mq.message_text
        FROM message_queue mq
        LEFT JOIN twitter_profiles tp ON tp.id = mq.profile_id
        LEFT JOIN twitter_accounts ta ON ta.id = mq.account_id
        WHERE mq.claimed_by = %s AND mq.status IN ('pending', 'in_review')
        ORDER BY
            CASE mq.priority
                WHEN 'P0' THEN 0 WHEN 'P1' THEN 1 WHEN 'P2' THEN 2
                WHEN 'P3' THEN 3 WHEN 'P4' THEN 4 ELSE 5
            END, mq.created_at ASC
    """, [operator_id])
    
    return jsonify([dict(r) for r in rows])


@app.route("/api/tasks/<int:task_id>/approve", methods=["POST"])
@require_auth
def approve_task(task_id):
    """Approve a task (operator can only approve their claimed tasks)"""
    data = request.json or {}
    edited_text = data.get("message_text")
    operator_id = request.operator["id"]
    
    # Check task belongs to operator
    task = db_query_one("""
        SELECT id, claimed_by FROM message_queue WHERE id = %s
    """, [task_id])
    
    if not task or task["claimed_by"] != operator_id:
        return jsonify({"error": "Task not found or not claimed by you"}), 403
    
    if edited_text:
        db_execute("""
            UPDATE message_queue 
            SET status = 'approved', message_text = %s 
            WHERE id = %s
        """, [edited_text, task_id])
    else:
        db_execute("""
            UPDATE message_queue SET status = 'approved' WHERE id = %s
        """, [task_id])
    
    return jsonify({"status": "approved"})


@app.route("/api/tasks/<int:task_id>/reject", methods=["POST"])
@require_auth
def reject_task(task_id):
    """Reject a task"""
    data = request.json or {}
    reason = data.get("reason", "")
    operator_id = request.operator["id"]
    
    task = db_query_one("""
        SELECT id, claimed_by FROM message_queue WHERE id = %s
    """, [task_id])
    
    if not task or task["claimed_by"] != operator_id:
        return jsonify({"error": "Task not found or not claimed by you"}), 403
    
    db_execute("""
        UPDATE message_queue 
        SET status = 'rejected', claimed_by = NULL, claimed_at = NULL
        WHERE id = %s
    """, [task_id])
    
    return jsonify({"status": "rejected"})


# =============================================================================
# MKT-51: ADMIN PANEL
# =============================================================================

@app.route("/api/admin/operators")
@require_admin
def admin_operators():
    """Get operators with stats"""
    rows = db_query("""
        SELECT 
            o.id, o.name, o.role, o.is_active, o.created_at,
            COUNT(mq.id) FILTER (WHERE mq.status = 'approved') AS approved,
            COUNT(mq.id) FILTER (WHERE mq.status = 'sent') AS sent,
            COUNT(mq.id) FILTER (WHERE mq.status = 'rejected') AS rejected,
            MAX(os.last_active) AS last_active
        FROM operators o
        LEFT JOIN message_queue mq ON mq.claimed_by = o.id
        LEFT JOIN operator_sessions os ON os.operator_id = o.id
        GROUP BY o.id
        ORDER BY o.created_at
    """)
    return jsonify([dict(r) for r in rows])


@app.route("/api/admin/operators", methods=["POST"])
@require_admin
def admin_create_operator():
    """Create new operator"""
    data = request.json or {}
    name = data.get("name", "").strip()
    pin = data.get("pin", "")
    role = data.get("role", "operator")
    
    if not name or len(pin) < 4:
        return jsonify({"error": "Name and PIN (min 4 chars) required"}), 400
    
    if role not in ("operator", "admin"):
        role = "operator"
    
    pin_hash = bcrypt.hashpw(pin.encode(), bcrypt.gensalt()).decode()
    
    try:
        db_execute("""
            INSERT INTO operators (name, pin_hash, role)
            VALUES (%s, %s, %s)
        """, [name, pin_hash, role])
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/admin/operators/<int:op_id>", methods=["DELETE"])
@require_admin
def admin_delete_operator(op_id):
    """Deactivate operator"""
    # Don't delete self
    if request.operator["id"] == op_id:
        return jsonify({"error": "Cannot delete yourself"}), 400
    
    db_execute("UPDATE operators SET is_active = false WHERE id = %s", [op_id])
    db_execute("DELETE FROM operator_sessions WHERE operator_id = %s", [op_id])
    
    return jsonify({"status": "ok"})


@app.route("/api/admin/funnel")
@require_admin
def admin_funnel():
    """Get funnel metrics"""
    result = db_query_one("""
        SELECT 
            (SELECT COUNT(*) FROM twitter_profiles) AS collected,
            (SELECT COUNT(*) FROM twitter_profiles WHERE tier IS NOT NULL) AS enriched,
            (SELECT COUNT(*) FROM message_queue) AS generated,
            (SELECT COUNT(*) FROM message_queue WHERE status = 'approved') AS approved,
            (SELECT COUNT(*) FROM message_queue WHERE status = 'sent') AS sent,
            (SELECT COUNT(*) FROM conversations) AS responded
    """)
    return jsonify(dict(result) if result else {})


@app.route("/api/admin/system")
@require_admin
def admin_system():
    """Get system health stats"""
    fleet = db_query_one("""
        SELECT 
            COUNT(*) AS total_accounts,
            COUNT(*) FILTER (WHERE warmup_stage = 'active') AS active,
            COUNT(*) FILTER (WHERE warmup_stage = 'warming') AS warming,
            COUNT(*) FILTER (WHERE warmup_stage IN ('restricted', 'banned')) AS restricted
        FROM adspower_profiles
    """)
    
    api_costs = db_query_one("""
        SELECT 
            COALESCE(SUM(cost_usd) FILTER (WHERE called_at::date = CURRENT_DATE), 0) AS today,
            COALESCE(SUM(cost_usd) FILTER (WHERE called_at >= CURRENT_DATE - INTERVAL '7 days'), 0) AS week,
            COALESCE(SUM(cost_usd) FILTER (WHERE called_at >= CURRENT_DATE - INTERVAL '30 days'), 0) AS month
        FROM twitterapi_calls_log
    """)
    
    return jsonify({
        "fleet": dict(fleet) if fleet else {},
        "api_costs": dict(api_costs) if api_costs else {}
    })


# =============================================================================
# MKT-53: M6 RESPONSES INTEGRATION
# =============================================================================

@app.route("/api/responses")
@require_auth
def responses():
    """Get conversations with responses"""
    status_filter = request.args.get("status", "pending")  # pending, replied, archived
    
    rows = db_query("""
        SELECT 
            c.id, c.status, c.first_response_at, c.last_response_at,
            c.response_count, c.sentiment_score, c.detected_intent,
            tp.username AS target_username, tp.display_name AS target_display,
            ta.username AS account_username,
            c.last_response_text
        FROM conversations c
        LEFT JOIN twitter_profiles tp ON tp.id = c.profile_id
        LEFT JOIN twitter_accounts ta ON ta.id = c.account_id
        WHERE c.status = %s
        ORDER BY c.last_response_at DESC
        LIMIT 100
    """, [status_filter])
    
    return jsonify([dict(r) for r in rows])


@app.route("/api/responses/<int:conv_id>")
@require_auth
def response_detail(conv_id):
    """Get conversation detail with messages"""
    conv = db_query_one("""
        SELECT c.*, 
            tp.username AS target_username, tp.display_name AS target_display,
            ta.username AS account_username
        FROM conversations c
        LEFT JOIN twitter_profiles tp ON tp.id = c.profile_id
        LEFT JOIN twitter_accounts ta ON ta.id = c.account_id
        WHERE c.id = %s
    """, [conv_id])
    
    if not conv:
        return jsonify({"error": "Not found"}), 404
    
    messages = db_query("""
        SELECT id, direction, message_text, sent_at, dm_id
        FROM dm_history
        WHERE conversation_id = %s
        ORDER BY sent_at ASC
    """, [conv_id])
    
    return jsonify({
        "conversation": dict(conv),
        "messages": [dict(m) for m in messages]
    })


@app.route("/api/responses/<int:conv_id>/generate-reply", methods=["POST"])
@require_auth
def generate_reply(conv_id):
    """Generate AI reply suggestion using M6"""
    conv = db_query_one("SELECT * FROM conversations WHERE id = %s", [conv_id])
    if not conv:
        return jsonify({"error": "Not found"}), 404
    
    # Get last message
    last_msg = db_query_one("""
        SELECT message_text FROM dm_history 
        WHERE conversation_id = %s ORDER BY sent_at DESC LIMIT 1
    """, [conv_id])
    
    # TODO: Call M6 response_generator here
    # For now return placeholder
    return jsonify({
        "suggestion": f"Thanks for reaching out! I'd love to discuss this further.",
        "confidence": 0.85
    })


@app.route("/api/responses/<int:conv_id>/reply", methods=["POST"])
@require_auth  
def send_reply(conv_id):
    """Queue reply to conversation"""
    data = request.json or {}
    reply_text = data.get("message", "").strip()
    
    if not reply_text:
        return jsonify({"error": "Message required"}), 400
    
    conv = db_query_one("SELECT * FROM conversations WHERE id = %s", [conv_id])
    if not conv:
        return jsonify({"error": "Not found"}), 404
    
    # Add to message queue as reply
    db_execute("""
        INSERT INTO message_queue 
        (profile_id, account_id, message_type, message_text, status, priority, conversation_id)
        VALUES (%s, %s, 'dm_reply', %s, 'approved', 'P0', %s)
    """, [conv["profile_id"], conv["account_id"], reply_text, conv_id])
    
    return jsonify({"status": "queued"})


# =============================================================================
# EXISTING ENDPOINTS (with auth added)
# =============================================================================

@app.route("/api/accounts")
@require_auth
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
@require_admin
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
@require_admin
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
@require_auth
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


@app.route("/api/tasks")
@require_auth
def tasks():
    status_filter = request.args.get("status", "pending")
    rows = db_query("""
        SELECT mq.id, mq.message_type, mq.priority, mq.tier, mq.category,
            mq.identified_need, mq.status, mq.created_at,
            mq.claimed_by, mq.claimed_at,
            tp.username AS target_username, tp.display_name AS target_display,
            ta.username AS account_username, mq.message_text,
            op.name AS claimed_by_name
        FROM message_queue mq
        LEFT JOIN twitter_profiles tp ON tp.id = mq.profile_id
        LEFT JOIN twitter_accounts ta ON ta.id = mq.account_id
        LEFT JOIN operators op ON op.id = mq.claimed_by
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
@require_admin
def pause_all():
    db_execute("UPDATE message_queue SET status = 'skipped' WHERE status = 'pending'")
    return jsonify({"status": "paused"})


@app.route("/api/tasks/resume-all", methods=["POST"])
@require_admin
def resume_all():
    db_execute("UPDATE message_queue SET status = 'pending' WHERE status = 'skipped'")
    return jsonify({"status": "resumed"})


@app.route("/api/metrics")
@require_auth
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


@app.route("/api/timeline")
@require_auth
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
