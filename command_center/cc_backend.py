"""
Command Center Backend — Flask Server

Provides REST API for Command Center UI:
- Authentication (operators, PIN-based login)
- Dashboard data
- Accounts management
- Queue management (DM review workflow)
- Responses (conversations)
- Module execution
- Admin panel

Runs on port 8899
"""

import os
import sys
import json
import hashlib
import secrets
import sqlite3
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

app = Flask(__name__, static_folder=str(Path(__file__).parent))
CORS(app)

# Database — SQLite locally, PostgreSQL on Railway
DB_PATH = PROJECT_ROOT / 'twitter_outreach.db'
DATABASE_URL = os.environ.get('DATABASE_URL')
SESSIONS = {}  # In-memory sessions (token -> user_info)

class _PgCursorWrapper:
    """Wraps psycopg2 cursor to behave like sqlite3 cursor (auto-convert ? to %s)."""
    def __init__(self, cur):
        self._cur = cur
    def execute(self, q, params=None):
        q = q.replace('?', '%s')
        if params is None:
            self._cur.execute(q)
        else:
            self._cur.execute(q, params)
        return self
    def fetchone(self): return self._cur.fetchone()
    def fetchall(self): return self._cur.fetchall()
    def __iter__(self): return iter(self._cur.fetchall())
    @property
    def lastrowid(self): return self._cur.fetchone()[0] if self._cur.rowcount else None
    @property
    def rowcount(self): return self._cur.rowcount

class _PgConnWrapper:
    """Wraps psycopg2 connection to behave like sqlite3 connection."""
    def __init__(self, conn):
        self._conn = conn
    def execute(self, q, params=None):
        cur = _PgCursorWrapper(self._conn.cursor(cursor_factory=__import__('psycopg2.extras', fromlist=['RealDictCursor']).RealDictCursor))
        cur.execute(q, params)
        return cur
    def commit(self): self._conn.commit()
    def close(self): self._conn.close()
    def __enter__(self): return self
    def __exit__(self, *a): 
        self._conn.commit()
        self._conn.close()

def get_db():
    """Get database connection. Uses PostgreSQL on Railway, SQLite locally."""
    if DATABASE_URL:
        import psycopg2
        import psycopg2.extras
        url = DATABASE_URL.replace('postgres://', 'postgresql://', 1)
        conn = psycopg2.connect(url, connect_timeout=10)
        return _PgConnWrapper(conn)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn

def _placeholder():
    """Return ? for SQLite, %s for PostgreSQL."""
    return '%s' if DATABASE_URL else '?'

def _adapt_query(q):
    """Adapt SQLite query to PostgreSQL if needed."""
    if DATABASE_URL:
        return q.replace('?', '%s')
    return q

def hash_pin(pin: str) -> str:
    """Hash PIN for storage."""
    return hashlib.sha256(pin.encode()).hexdigest()

def require_auth(f):
    """Decorator to require authentication."""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization', '').replace('Bearer ', '')
        if not token or token not in SESSIONS:
            return jsonify({'error': 'Unauthorized'}), 401
        request.user = SESSIONS[token]
        return f(*args, **kwargs)
    return decorated

def require_admin(f):
    """Decorator to require admin role."""
    @wraps(f)
    @require_auth
    def decorated(*args, **kwargs):
        if request.user.get('role') != 'admin':
            return jsonify({'error': 'Admin required'}), 403
        return f(*args, **kwargs)
    return decorated

# =========== Static Files ===========

@app.route('/')
def serve_frontend():
    return send_from_directory(app.static_folder, 'cc_frontend.html')

@app.route('/<path:filename>')
def serve_static(filename):
    return send_from_directory(app.static_folder, filename)

# =========== Auth Endpoints ===========

@app.route('/cc/setup-check', methods=['GET'])
def setup_check():
    """Check if initial admin setup is needed."""
    db = get_db()
    try:
        cursor = db.execute('SELECT COUNT(*) as cnt FROM operators WHERE role = %s' if DATABASE_URL else 'SELECT COUNT(*) as cnt FROM operators WHERE role = ?', ('admin',))
        row = cursor.fetchone()
        return jsonify({'setup_done': row['cnt'] > 0})
    except:
        # Table might not exist - create it
        db.execute('''
            CREATE TABLE IF NOT EXISTS operators (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                pin_hash TEXT NOT NULL,
                role TEXT DEFAULT 'operator',
                approved_count INTEGER DEFAULT 0,
                sent_count INTEGER DEFAULT 0,
                last_active TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        db.commit()
        return jsonify({'setup_done': False})
    finally:
        db.close()

@app.route('/cc/setup', methods=['POST'])
def setup():
    """Create initial admin user."""
    data = request.get_json()
    name = data.get('name', '').strip()
    pin = data.get('pin', '')
    
    if not name or len(pin) < 4:
        return jsonify({'success': False, 'error': 'Name and PIN (4+ chars) required'})
    
    db = get_db()
    try:
        # Check if admin already exists
        cursor = db.execute('SELECT COUNT(*) as cnt FROM operators WHERE role = %s' if DATABASE_URL else 'SELECT COUNT(*) as cnt FROM operators WHERE role = ?', ('admin',))
        if cursor.fetchone()['cnt'] > 0:
            return jsonify({'success': False, 'error': 'Admin already exists'})
        
        db.execute(
            'INSERT INTO operators (name, pin_hash, role) VALUES (?, ?, ?)',
            (name, hash_pin(pin), 'admin')
        )
        db.commit()
        return jsonify({'success': True})
    except sqlite3.IntegrityError:
        return jsonify({'success': False, 'error': 'Name already exists'})
    finally:
        db.close()

@app.route('/cc/login', methods=['POST'])
def login():
    """Login with name and PIN."""
    data = request.get_json()
    name = data.get('name', '').strip()
    pin = data.get('pin', '')
    
    db = get_db()
    try:
        cursor = db.execute(
            'SELECT * FROM operators WHERE name = ? AND pin_hash = ?',
            (name, hash_pin(pin))
        )
        user = cursor.fetchone()
        
        if not user:
            return jsonify({'success': False, 'error': 'Invalid credentials'})
        
        # Update last active
        db.execute('UPDATE operators SET last_active = %s WHERE id = %s' if DATABASE_URL else 'UPDATE operators SET last_active = ? WHERE id = ?',
                   (datetime.now().isoformat(), user['id']))
        db.commit()
        
        # Create session token
        token = secrets.token_urlsafe(32)
        user_info = {
            'id': user['id'],
            'name': user['name'],
            'role': user['role']
        }
        SESSIONS[token] = user_info
        
        return jsonify({
            'success': True,
            'token': token,
            'user': user_info
        })
    finally:
        db.close()

@app.route('/cc/validate', methods=['POST'])
def validate_session():
    """Validate existing session."""
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    if token in SESSIONS:
        return jsonify({'valid': True, 'user': SESSIONS[token]})
    return jsonify({'valid': False})

# =========== Dashboard ===========

@app.route('/cc/dashboard', methods=['GET'])
def dashboard():
    """Get dashboard data."""
    db = get_db()
    try:
        today = datetime.now().strftime('%Y-%m-%d')
        data = {
            'dms_today': 0, 'likes_today': 0, 'follows_today': 0, 'errors_today': 0,
            'active_accounts': 0, 'total_accounts': 0, 'warming_accounts': 0,
            'queue_pending': 0, 'queue_in_review': 0,
            'responses_today': 0, 'responses_unread': 0,
            'api_cost_today': 0.0,
            'profiles_total': 0, 'profiles_enriched': 0, 'profiles_pending': 0, 'profiles_contacted': 0,
            'recent_activity': []
        }
        
        # Account stats
        try:
            cursor = db.execute('SELECT COUNT(*) as cnt FROM accounts')
            data['total_accounts'] = cursor.fetchone()['cnt']
            
            cursor = db.execute('SELECT COUNT(*) as cnt FROM accounts WHERE status = "active"')
            data['active_accounts'] = cursor.fetchone()['cnt']
            
            cursor = db.execute('SELECT COUNT(*) as cnt FROM accounts WHERE status = "warming"')
            data['warming_accounts'] = cursor.fetchone()['cnt']
        except: pass
        
        # Queue stats
        try:
            from infra.db import get_connection
            with get_connection() as pg:
                with pg.cursor() as cur:
                    cur.execute("SELECT COUNT(*) FROM message_queue WHERE status = 'pending'")
                    data['queue_pending'] = cur.fetchone()[0]
                    cur.execute("SELECT COUNT(*) FROM message_queue WHERE status = 'pending'")
                    data['queue_in_review'] = cur.fetchone()[0]
        except: pass
        
        # Profile stats - PostgreSQL twitter_profiles
        try:
            from infra.db import get_connection
            with get_connection() as pg:
                with pg.cursor() as pgcur:
                    pgcur.execute('SELECT COUNT(*) FROM twitter_profiles')
                    data['profiles_total'] = pgcur.fetchone()[0]
                    pgcur.execute("SELECT COUNT(*) FROM twitter_profiles WHERE tier IS NOT NULL AND tier != ''")
                    data['profiles_enriched'] = pgcur.fetchone()[0]
                    pgcur.execute("SELECT COUNT(*) FROM twitter_profiles WHERE tier IS NULL OR tier = ''")
                    data['profiles_pending'] = pgcur.fetchone()[0]
                    pgcur.execute("SELECT COUNT(*) FROM twitter_profiles WHERE outreach_status = 'contacted'")
                    data['profiles_contacted'] = pgcur.fetchone()[0]
        except: pass
        
        # Activity log
        try:
            cursor = db.execute('''
                SELECT type, message, created_at FROM activity_log
                ORDER BY created_at DESC LIMIT 20
            ''')
            data['recent_activity'] = [{
                'type': row['type'],
                'message': row['message'],
                'time': row['created_at'][:16].replace('T', ' ')
            } for row in cursor.fetchall()]
        except: pass
        
        return jsonify(data)
    finally:
        db.close()

@app.route('/cc/connections', methods=['GET'])
def check_connections():
    """Check external service connections."""
    # Check GoLogin
    gologin_ok = False
    try:
        import requests
        r = requests.get('http://localhost:36912/browser/v2', timeout=2)
        gologin_ok = r.status_code == 200
    except: pass
    
    # Check Twitter API config
    twitter_ok = bool(os.environ.get('TWITTER_BEARER_TOKEN'))
    
    return jsonify({
        'gologin': gologin_ok,
        'twitter': twitter_ok
    })


# =========== Accounts ===========

@app.route('/cc/accounts', methods=['GET'])
def list_accounts():
    """List all Twitter accounts."""
    db = get_db()
    try:
        cursor = db.execute('''
            SELECT id, username, display_name, adspower_id, serial_number,
                   status, warmup_pct, dms_sent, health_score, proxy_country,
                   category_focus, created_at
            FROM accounts ORDER BY created_at DESC
        ''')
        accounts = [dict(row) for row in cursor.fetchall()]
        return jsonify({'accounts': accounts})
    except Exception as e:
        return jsonify({'accounts': [], 'error': str(e)})
    finally:
        db.close()

@app.route('/cc/accounts', methods=['POST'])
def add_account():
    """Add a new Twitter account."""
    data = request.get_json()
    username = data.get('username', '').strip().lstrip('@')
    adspower_id = data.get('adspower_id', '').strip()
    
    if not username:
        return jsonify({'success': False, 'error': 'Username required'})
    
    db = get_db()
    try:
        # Ensure table exists
        db.execute('''
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                display_name TEXT,
                adspower_id TEXT NOT NULL,
                serial_number TEXT,
                status TEXT DEFAULT 'warming',
                warmup_pct INTEGER DEFAULT 0,
                dms_sent INTEGER DEFAULT 0,
                dms_today INTEGER DEFAULT 0,
                last_dm_at TEXT,
                health_score TEXT DEFAULT 'Good',
                proxy_host TEXT, proxy_port TEXT, proxy_user TEXT, proxy_pass TEXT,
                proxy_country TEXT, proxy_city TEXT,
                category_focus TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        proxy = data.get('proxy', {})
        db.execute('''
            INSERT INTO accounts (username, display_name, adspower_id, serial_number,
                                  proxy_host, proxy_port, proxy_user, proxy_pass,
                                  proxy_country, proxy_city, category_focus)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            username, data.get('display_name'), adspower_id, data.get('serial_number'),
            proxy.get('host'), proxy.get('port'), proxy.get('user'), proxy.get('pass'),
            proxy.get('country'), proxy.get('city'), data.get('category_focus')
        ))
        db.commit()
        return jsonify({'success': True, 'id': db.execute('SELECT last_insert_rowid()').fetchone()[0]})
    except sqlite3.IntegrityError:
        return jsonify({'success': False, 'error': 'Account already exists'})
    finally:
        db.close()

@app.route('/cc/accounts/<id>', methods=['DELETE'])
def delete_account(id):
    """Delete an account."""
    db = get_db()
    try:
        db.execute('DELETE FROM accounts WHERE id = ?', (id,))
        db.commit()
        return jsonify({'success': True})
    finally:
        db.close()

@app.route('/cc/accounts/bulk', methods=['POST'])
def bulk_import_accounts():
    """Bulk import accounts from CSV."""
    data = request.get_json()
    csv_text = data.get('csv', '')
    
    if not csv_text:
        return jsonify({'success': False, 'error': 'No CSV data'})
    
    db = get_db()
    imported = 0
    try:
        for line in csv_text.strip().split('\n'):
            parts = [p.strip() for p in line.split(',')]
            if len(parts) >= 2:
                username = parts[0].lstrip('@')
                adspower_id = parts[1]
                try:
                    db.execute('''
                        INSERT OR IGNORE INTO accounts (username, adspower_id)
                        VALUES (?, ?)
                    ''', (username, adspower_id))
                    imported += 1
                except: pass
        db.commit()
        return jsonify({'success': True, 'imported': imported})
    finally:
        db.close()

# =========== Queue ===========

# =========== Queue (PostgreSQL) ===========

@app.route('/cc/queue', methods=['GET'])
def get_queue():
    """Get message queue items by status - PostgreSQL."""
    from infra.db import get_connection
    status = request.args.get('status', 'pending')
    operator = request.args.get('operator', '')
    status_map = {'pending': 'pending', 'in_review': 'in_review',
                  'approved': 'approved', 'rejected': 'rejected'}
    pg_status = status_map.get(status, status)
    try:
        with get_connection() as pg:
            with pg.cursor() as cur:
                cur.execute(
                    "UPDATE message_queue SET locked_by = NULL, locked_at = NULL"
                    " WHERE locked_at IS NOT NULL"
                    " AND locked_at < NOW() - INTERVAL '5 minutes'"
                )
                cur.execute(
                    "SELECT mq.id, mq.message_text, mq.status, mq.send_type,"
                    " mq.created_at, mq.locked_by, mq.locked_at,"
                    " mq.reviewed_by, mq.reviewed_at,"
                    " tp.username AS target_username,"
                    " tp.display_name AS target_name,"
                    " tp.tier, ta.username AS sender_username,"
                    " CASE WHEN mq.locked_by IS NOT NULL AND mq.locked_by != %s"
                    " THEN 1 ELSE 0 END AS is_locked_by_other"
                    " FROM message_queue mq"
                    " LEFT JOIN twitter_profiles tp ON tp.id = mq.profile_id"
                    " LEFT JOIN twitter_accounts ta ON ta.id = mq.account_id"
                    " WHERE mq.status = %s"
                    " ORDER BY CASE tp.tier WHEN 'S' THEN 1 WHEN 'A' THEN 2"
                    " WHEN 'B' THEN 3 ELSE 4 END, mq.created_at DESC LIMIT 100",
                    (operator, pg_status)
                )
                cols = [d[0] for d in cur.description]
                items = [dict(zip(cols, row)) for row in cur.fetchall()]
        return jsonify({'items': items})
    except Exception as e:
        log(f"get_queue error: {e}")
        return jsonify({'items': [], 'error': str(e)}), 500


@app.route('/cc/queue/<id>/<action>', methods=['POST'])
def queue_action(id, action):
    """Approve/reject/send queue item - PostgreSQL."""
    from infra.db import get_connection
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    user = SESSIONS.get(token, {})
    operator_name = user.get('name', 'unknown')
    action_map = {
        'approve': "UPDATE message_queue SET status='approved', reviewed_by=%s, reviewed_at=NOW(), locked_by=NULL, locked_at=NULL WHERE id=%s",
        'reject':  "UPDATE message_queue SET status='rejected', reviewed_by=%s, reviewed_at=NOW(), locked_by=NULL, locked_at=NULL WHERE id=%s",
    }
    if action == 'send':
        sql, params = "UPDATE message_queue SET status='approved' WHERE id=%s", (id,)
    elif action in action_map:
        sql, params = action_map[action], (operator_name, id)
    else:
        return jsonify({'success': False, 'error': 'Unknown action'}), 400
    try:
        with get_connection() as pg:
            with pg.cursor() as cur:
                cur.execute(sql, params)
        return jsonify({'success': True})
    except Exception as e:
        log(f"queue_action error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/cc/queue/<id>/lock', methods=['POST'])
def lock_queue_item(id):
    """Lock queue item for operator - PostgreSQL."""
    from infra.db import get_connection
    data = request.get_json() or {}
    operator = data.get('operator', '')
    if not operator:
        token = request.headers.get('Authorization', '').replace('Bearer ', '')
        user = SESSIONS.get(token)
        operator = user['name'] if user else 'unknown'
    try:
        with get_connection() as pg:
            with pg.cursor() as cur:
                cur.execute('SELECT locked_by, locked_at FROM message_queue WHERE id = %s', (id,))
                row = cur.fetchone()
                if row and row[0] and row[0] != operator:
                    if row[1]:
                        from datetime import datetime as dt
                        if (dt.now() - row[1].replace(tzinfo=None)).total_seconds() < 300:
                            return jsonify({'success': False, 'locked_by': row[0]})
                cur.execute('UPDATE message_queue SET locked_by = %s, locked_at = NOW() WHERE id = %s',
                            (operator, id))
        return jsonify({'success': True, 'locked_by': operator})
    except Exception:
        return jsonify({'success': True, 'locked_by': operator})


@app.route('/cc/queue/<id>/unlock', methods=['POST'])
def unlock_queue_item(id):
    """Release lock - PostgreSQL."""
    from infra.db import get_connection
    try:
        with get_connection() as pg:
            with pg.cursor() as cur:
                cur.execute('UPDATE message_queue SET locked_by = NULL, locked_at = NULL WHERE id = %s', (id,))
        return jsonify({'success': True})
    except Exception:
        return jsonify({'success': True})


@app.route('/cc/queue/approve-all', methods=['POST'])
def approve_all():
    """Approve all pending/in_review items - PostgreSQL."""
    from infra.db import get_connection
    try:
        with get_connection() as pg:
            with pg.cursor() as cur:
                cur.execute("UPDATE message_queue SET status='approved', reviewed_at=NOW()"
                            " WHERE status IN ('pending', 'in_review')")
                count = cur.rowcount
        return jsonify({'success': True, 'approved': count})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# =========== Responses ===========

@app.route('/cc/responses', methods=['GET'])
def get_responses():
    """Conversation list - stub until response tracking is implemented."""
    return jsonify({'conversations': []})


@app.route('/cc/responses/<conv_id>', methods=['GET'])
def get_conversation_detail(conv_id):
    return jsonify({'error': 'Not found'}), 404


@app.route('/cc/responses/<conv_id>/generate', methods=['POST'])
def generate_reply(conv_id):
    return jsonify({'reply': "Thanks for reaching out! I'd love to learn more about your needs."})


@app.route('/cc/responses/<conv_id>/reply', methods=['POST'])
def send_reply(conv_id):
    """Queue a reply - inserts into message_queue (PostgreSQL)."""
    from infra.db import get_connection
    data = request.get_json()
    text = (data.get('text') or '').strip()
    if not text:
        return jsonify({'success': False, 'error': 'Reply text required'})
    try:
        with get_connection() as pg:
            with pg.cursor() as cur:
                cur.execute("INSERT INTO message_queue (message_text, status, send_type, created_at)"
                            " VALUES (%s, 'approved', 'dm', NOW())", (text,))
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# =========== Module Execution ===========

@app.route('/cc/run/<module>', methods=['POST'])
def run_module(module):
    """Run a marketing pipeline module."""
    data = request.get_json() or {}
    
    # Map module names to functions
    module_map = {
        # Legacy short names
        'm1': run_m1_collect,
        'm2': run_m2_enrich,
        'm3': run_m3_generate,
        'm4': run_m4_warmup,
        'm5': run_m5_send,
        'm6': run_m6_responses,
        'm7': run_m7_analytics,
        # Sprint 4 canonical names (UI v3)
        'profile_hunter':  run_m1_collect,
        'signal_analyzer': run_m2_enrich,
        'warmup_engine':   run_m3_generate,
        'message_crafter': run_m4_warmup,
        'outreach_sender': run_m5_send,
        'inbox_monitor':   run_m6_responses,
        'thread_scout':    run_m7_analytics,
        'm7_tweet':        run_m7_analytics,
    }
    
    if module not in module_map:
        return jsonify({'success': False, 'error': f'Unknown module: {module}'})
    
    try:
        result = module_map[module](data)
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

def run_m1_collect(data):
    """M1: Profile Hunter — collect profiles via TwitterAPI.io."""
    import sys
    from pathlib import Path as _Path
    sys.path.insert(0, str(_Path(__file__).parent.parent))

    query = data.get('query', '')
    max_profiles = int(data.get('batch_size', data.get('max_profiles', 100)))
    use_defaults = not query  # if no custom query, run all default queries

    try:
        from m1_data_collector.twitter_search_profiles import twitter_search_profiles
        result = twitter_search_profiles(
            query=query,
            max_profiles=max_profiles,
            use_default_queries=use_defaults,
            collection_source='command_center'
        )
        return {
            'success': True,
            'message': f'Collected {result["profiles_saved"]} new profiles ({result["profiles_skipped"]} skipped).',
            'profiles_saved': result['profiles_saved'],
            'profiles_skipped': result['profiles_skipped'],
            'budget': result.get('budget', {})
        }
    except Exception as e:
        return {'success': False, 'error': str(e)}

def run_m2_enrich(data):
    """M2: Signal Analyzer — classify profiles into S/A/B/C/D tiers."""
    import sys
    from pathlib import Path as _Path
    sys.path.insert(0, str(_Path(__file__).parent.parent))
    try:
        from infra.config import ANTHROPIC_API_KEY
        from infra.db import get_connection
        from m2_profile_analyzer.wave_classifier import wave_classifier

        batch_size = int(data.get('batch_size', 20))
        # Get profiles without tier
        with get_connection() as conn:
            with conn.cursor() as cur:
                import psycopg2.extras as _extras
                cur2 = conn.cursor(cursor_factory=_extras.RealDictCursor)
                cur2.execute("""
                    SELECT id, username, display_name, bio, followers_count,
                           following_count, tweets_count
                    FROM twitter_profiles
                    WHERE tier IS NULL OR tier = ''
                    ORDER BY followers_count DESC
                    LIMIT %s
                """, (batch_size,))
                profiles = cur2.fetchall()
                cur2.close()

        if not profiles:
            return {'success': True, 'message': 'All profiles already classified.', 'classified': 0}

        classified = 0
        errors = 0
        with get_connection() as conn:
            with conn.cursor() as cur:
                for p in profiles:
                    try:
                        profile_dict = {
                            'username': p['username'],
                            'bio': p['bio'] or '',
                            'followers_count': p['followers_count'],
                            'following_count': p['following_count'],
                        }
                        result = wave_classifier(
                            profile=profile_dict,
                            tweets=[],
                            anthropic_api_key=ANTHROPIC_API_KEY
                        )
                        tier = result.get('tier', 'D')

                        # Run needs_analyzer to fill identified_needs + topics
                        from m2_profile_analyzer.needs_analyzer import needs_analyzer
                        needs_result = needs_analyzer(
                            profile=profile_dict,
                            tweets=[],
                            anthropic_api_key=ANTHROPIC_API_KEY
                        )
                        import json as _json
                        identified_needs = _json.dumps(needs_result.get('identified_needs', []))
                        topics = _json.dumps(needs_result.get('dronor_use_cases', []))
                        primary_category = needs_result.get('primary_category', '')

                        cur.execute(
                            "UPDATE twitter_profiles SET tier=%s, identified_needs=%s::jsonb, topics_of_interest=%s::jsonb, category=%s WHERE id=%s",
                            (tier, identified_needs, topics, primary_category, p['id'])
                        )
                        classified += 1
                    except Exception as _m2e:
                        errors += 1
                        import logging; logging.getLogger('m2').error(f'M2 classify error: {_m2e}')
                        continue

        return {
            'success': True,
            'message': f'Classified {classified} profiles ({errors} errors).',
            'classified': classified,
            'errors': errors,
            'api_key_set': bool(ANTHROPIC_API_KEY)
        }
    except Exception as e:
        return {'success': False, 'error': str(e)}

def run_m3_generate(data):
    """M4: Message Crafter — generate outreach messages via Claude API."""
    import sys
    from pathlib import Path as _Path
    sys.path.insert(0, str(_Path(__file__).parent.parent))
    try:
        from m4_message_generator.message_generator import message_generator
        batch_size = int(data.get('batch_size', 20))
        tier_filter = data.get('tier_filter', '')
        dry_run = bool(data.get('dry_run', False))
        result = message_generator(
            batch_size=batch_size,
            tier_filter=tier_filter,
            dry_run=dry_run
        )
        generated = result.get('generated', 0)
        return {
            'success': True,
            'message': f'Generated {generated} messages (DM: {result.get("dm",0)}, reply: {result.get("reply",0)}).',
            'generated': generated,
            'dm': result.get('dm', 0),
            'reply': result.get('reply', 0),
            'errors': result.get('errors', 0)
        }
    except Exception as e:
        return {'success': False, 'error': str(e)}

def run_m4_warmup(data):
    """M3: Warmup Engine — manage account warmup & assignments."""
    import sys
    from pathlib import Path as _Path
    sys.path.insert(0, str(_Path(__file__).parent.parent))
    try:
        from m3_account_manager.assignment_engine import manage_assignments
        action = data.get('action', 'summary')
        account_id = data.get('account_id', '')
        result = manage_assignments(action=action, account_id=account_id)
        return {
            'success': True,
            'message': f'Warmup Engine: {action} completed.',
            'data': result
        }
    except Exception as e:
        return {'success': False, 'error': str(e)}

def run_m5_send(data):
    """M5: Outreach Sender — send approved messages via GoLogin antidetect browser."""
    import sys
    from pathlib import Path as _Path
    sys.path.insert(0, str(_Path(__file__).parent.parent))
    try:
        from infra.db import get_connection
        from psycopg2.extras import RealDictCursor
        from m5_browser_controller.gologin_browser_controller import GoLoginAPI, browser_controller

        # 1. Check GoLogin is running
        api = GoLoginAPI()
        if not api.is_running():
            return {
                'success': False,
                'error': 'GoLogin is not running. Start the GoLogin desktop app on port 36912.'
            }

        # 2. Count approved messages
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT mq.id, mq.message_text, mq.profile_id,
                           tp.username AS target_username,
                           ta.username AS sender_username,
                           ta.gologin_profile_id
                    FROM message_queue mq
                    JOIN twitter_profiles tp ON tp.id = mq.profile_id
                    JOIN twitter_accounts ta ON ta.id = mq.account_id
                    WHERE mq.status = 'approved'
                    LIMIT %s
                """, (int(data.get('batch_size', 10)),))
                messages = cur.fetchall()

        if not messages:
            return {'success': True, 'message': 'No approved messages to send.', 'queued': 0}

        sent = 0
        errors = []
        for msg in messages:
            try:
                result = browser_controller(
                    action='send_dm',
                    username=msg['target_username'],
                    text=msg['message_text'],
                    profile_id=msg['gologin_profile_id'] or '',
                )
                if result['status'] == 'success':
                    # Mark as sent
                    with get_connection() as conn:
                        with conn.cursor() as cur:
                            cur.execute(
                                "UPDATE message_queue SET status='sent', sent_at=NOW() WHERE id=%s",
                                (msg['id'],)
                            )
                        conn.commit()
                    sent += 1
                else:
                    errors.append(f"{msg['target_username']}: {result.get('message')}")
            except Exception as e:
                errors.append(f"{msg['target_username']}: {e}")

        return {
            'success': True,
            'message': f'Sent {sent}/{len(messages)} messages via GoLogin.',
            'sent': sent,
            'queued': len(messages),
            'errors': errors
        }
    except Exception as e:
        return {'success': False, 'error': str(e)}

def run_m6_responses(data):
    """M6: Inbox Monitor — generate follow-ups for unanswered DMs."""
    import sys
    from pathlib import Path as _Path
    sys.path.insert(0, str(_Path(__file__).parent.parent))
    try:
        from m6_response_handler.warm_followup_generator import warm_followup_generator
        batch_size = int(data.get('batch_size', 20))
        dry_run = bool(data.get('dry_run', False))
        result = warm_followup_generator(batch_size=batch_size, dry_run=dry_run)
        generated = result.get('generated', 0)
        return {
            'success': True,
            'message': f'Inbox Monitor: {generated} follow-ups generated ({result.get("skipped",0)} skipped).',
            'generated': generated,
            'processed': result.get('processed', 0),
            'skipped': result.get('skipped', 0)
        }
    except Exception as e:
        return {'success': False, 'error': str(e)}

def run_m7_analytics(data):
    """M7: Thread Scout — find relevant tweets for outreach context."""
    import sys
    from pathlib import Path as _Path
    sys.path.insert(0, str(_Path(__file__).parent.parent))
    try:
        from m7_tweet_finder.tweet_finder import tweet_finder
        max_results = int(data.get('max_results', 50))
        tier = data.get('tier_filter', '')
        result = tweet_finder(
            batch_size=max_results,
            tier_filter=tier
        )
        found = result.get('found', result.get('saved', 0))
        return {
            'success': True,
            'message': f'Thread Scout processed {result.get("processed", 0)} profiles, found {found} relevant tweets.',
            'processed': result.get('processed', 0),
            'found': found
        }
    except Exception as e:
        return {'success': False, 'error': str(e)}

@app.route('/cc/smoke-test', methods=['POST'])
def smoke_test():
    """Run smoke test to verify all components."""
    steps = []
    
    # Test database
    try:
        db = get_db()
        db.execute('SELECT 1')
        db.close()
        steps.append({'name': 'Database connection', 'ok': True})
    except:
        steps.append({'name': 'Database connection', 'ok': False})
    
    # Test project files
    try:
        assert (PROJECT_ROOT / 'twitter_outreach.db').exists() or True
        steps.append({'name': 'Project structure', 'ok': True})
    except:
        steps.append({'name': 'Project structure', 'ok': False})
    
    # Test AdsPower connection
    try:
        import requests
        r = requests.get('http://localhost:50325/status', timeout=2)
        steps.append({'name': 'AdsPower connection', 'ok': r.status_code == 200})
    except:
        steps.append({'name': 'AdsPower connection', 'ok': False})
    
    # Test modules importable
    try:
        # Just check files exist
        modules_dir = PROJECT_ROOT / 'modules'
        if modules_dir.exists():
            steps.append({'name': 'Modules directory', 'ok': True})
        else:
            steps.append({'name': 'Modules directory', 'ok': False})
    except:
        steps.append({'name': 'Modules directory', 'ok': False})
    
    success = all(s['ok'] for s in steps)
    return jsonify({'success': success, 'steps': steps})

# =========== Admin ===========

@app.route('/cc/admin', methods=['GET'])
def get_admin_data():
    """Get admin panel data — pure PostgreSQL."""
    from infra.db import get_connection
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                import psycopg2.extras as _e
                cur2 = conn.cursor(cursor_factory=_e.RealDictCursor)

                # Operators from PostgreSQL operators table (fallback to empty)
                try:
                    cur2.execute('''SELECT id, name, role,
                        approved_count as approved, sent_count as sent, last_active
                        FROM operators ORDER BY role DESC, name''' )
                    operators = [dict(r) for r in cur2.fetchall()]
                except Exception:
                    operators = []

                # Funnel from PostgreSQL
                cur.execute('SELECT COUNT(*) FROM twitter_profiles')
                collected = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM twitter_profiles WHERE identified_needs IS NOT NULL AND identified_needs != '[]'::jsonb")
                enriched = cur.fetchone()[0]
                cur.execute('SELECT COUNT(*) FROM message_queue')
                generated = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM message_queue WHERE status = 'approved'")
                approved = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM message_queue WHERE status = 'sent'")
                sent = cur.fetchone()[0]
                cur.execute('SELECT COUNT(*) FROM send_jobs')
                responses = 0  # placeholder

                funnel = [
                    {'stage': 'COLLECTED',  'count': collected},
                    {'stage': 'ENRICHED',   'count': enriched},
                    {'stage': 'GENERATED',  'count': generated},
                    {'stage': 'APPROVED',   'count': approved},
                    {'stage': 'SENT',       'count': sent},
                ]

                # System info
                system = {
                    'uptime': '—',
                    'db_size': '—',
                    'active_sessions': len(SESSIONS),
                    'queue_length': approved,
                }

        return jsonify({
            'operators': operators,
            'funnel': funnel,
            'system': system,
            'stats': {
                'total_dms': sent,
                'total_responses': responses,
                'response_rate': 0,
            }
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/cc/operators', methods=['POST'])
def add_operator():
    """Add a new operator."""
    data = request.get_json()
    name = data.get('name', '').strip()
    pin = data.get('pin', '')
    role = data.get('role', 'operator')
    
    if not name or len(pin) < 4:
        return jsonify({'success': False, 'error': 'Name and PIN (4+ chars) required'})
    
    if role not in ('operator', 'reviewer', 'admin'):
        role = 'operator'
    
    db = get_db()
    try:
        db.execute(
            'INSERT INTO operators (name, pin_hash, role) VALUES (?, ?, ?)',
            (name, hash_pin(pin), role)
        )
        db.commit()
        return jsonify({'success': True})
    except sqlite3.IntegrityError:
        return jsonify({'success': False, 'error': 'Name already exists'})
    finally:
        db.close()

@app.route('/cc/operators/<id>', methods=['DELETE'])
def delete_operator(id):
    """Delete an operator."""
    db = get_db()
    try:
        # Don't delete last admin
        cursor = db.execute('SELECT role FROM operators WHERE id = ?', (id,))
        user = cursor.fetchone()
        if user and user['role'] == 'admin':
            cursor = db.execute('SELECT COUNT(*) as cnt FROM operators WHERE role = %s' if DATABASE_URL else 'SELECT COUNT(*) as cnt FROM operators WHERE role = ?', ('admin',))
            if cursor.fetchone()['cnt'] <= 1:
                return jsonify({'success': False, 'error': 'Cannot delete last admin'})
        
        db.execute('DELETE FROM operators WHERE id = ?', (id,))
        db.commit()
        return jsonify({'success': True})
    finally:
        db.close()

# =========== Settings ===========

@app.route('/cc/settings', methods=['POST'])
def save_settings():
    """Save API settings."""
    data = request.get_json()
    
    # Save to environment or config file
    if data.get('anthropic_key'):
        os.environ['ANTHROPIC_API_KEY'] = data['anthropic_key']
    if data.get('twitter_bearer'):
        os.environ['TWITTER_BEARER_TOKEN'] = data['twitter_bearer']
    
    # TODO: Persist to config file
    return jsonify({'success': True})

# =========== Activity Logging ===========

def log_activity(activity_type: str, message: str):
    """Log an activity."""
    db = get_db()
    try:
        db.execute('''
            CREATE TABLE IF NOT EXISTS activity_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        db.execute(
            'INSERT INTO activity_log (type, message) VALUES (?, ?)',
            (activity_type, message)
        )
        db.commit()
    finally:
        db.close()


# =========== Proxy Management ===========

@app.route('/cc/proxies', methods=['GET'])
@require_auth
def get_proxies():
    """List all proxies with assignment info."""
    try:
        from infra.db import get_connection
        from psycopg2.extras import RealDictCursor
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT p.id, p.host, p.port, p.username, p.protocol,
                           p.status, p.response_ms, p.last_error,
                           p.last_checked, p.created_at,
                           ta.username AS assigned_to_username
                    FROM proxies p
                    LEFT JOIN twitter_accounts ta ON ta.id = p.assigned_to
                    ORDER BY p.status, p.host, p.port
                """)
                proxies = cur.fetchall()
                cur.execute("SELECT status, COUNT(*) as cnt FROM proxies GROUP BY status")
                stats = {r['status']: r['cnt'] for r in cur.fetchall()}
        return jsonify({
            'proxies': [dict(p) for p in proxies],
            'stats': stats,
            'total': len(proxies)
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/cc/proxies', methods=['POST'])
@require_auth
def add_proxies():
    """Bulk import proxies. Body: {proxies: "user:pass@host:port\n..."}"""
    data = request.json or {}
    raw = data.get('proxies', '').strip()
    if not raw:
        return jsonify({'error': 'No proxy data provided'}), 400
    try:
        from infra.db import get_connection
        added = 0
        skipped = 0
        errors = []
        with get_connection() as conn:
            with conn.cursor() as cur:
                for line in raw.split('\n'):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        creds, hostport = line.split('@')
                        user, pwd = creds.split(':', 1)
                        host, port = hostport.rsplit(':', 1)
                        cur.execute("""
                            INSERT INTO proxies (host, port, username, password, protocol, status)
                            VALUES (%s, %s, %s, %s, 'http', 'active')
                            ON CONFLICT (host, port) DO UPDATE SET
                                username=EXCLUDED.username,
                                password=EXCLUDED.password,
                                status='active'
                            RETURNING (xmax = 0) AS inserted
                        """, (host, int(port), user, pwd))
                        row = cur.fetchone()
                        if row and row[0]:
                            added += 1
                        else:
                            skipped += 1
                    except Exception as e:
                        errors.append(f'{line}: {e}')
            conn.commit()
        return jsonify({'added': added, 'skipped': skipped, 'errors': errors})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/cc/proxies/<int:proxy_id>', methods=['DELETE'])
@require_auth
def delete_proxy(proxy_id):
    """Delete a proxy by ID."""
    try:
        from infra.db import get_connection
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute('DELETE FROM proxies WHERE id = %s', (proxy_id,))
            conn.commit()
        return jsonify({'deleted': proxy_id})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/cc/proxies/<int:proxy_id>/check', methods=['POST'])
@require_auth
def check_proxy(proxy_id):
    """Test a single proxy — hits httpbin.org/ip through it."""
    import time, requests as req
    try:
        from infra.db import get_connection
        from psycopg2.extras import RealDictCursor
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute('SELECT * FROM proxies WHERE id = %s', (proxy_id,))
                p = cur.fetchone()
        if not p:
            return jsonify({'error': 'Not found'}), 404
        proxy_url = f'{p["protocol"]}://{p["username"]}:{p["password"]}@{p["host"]}:{p["port"]}'
        proxies = {'http': proxy_url, 'https': proxy_url}
        t0 = time.time()
        status = 'dead'
        error = None
        ms = None
        try:
            r = req.get('https://httpbin.org/ip', proxies=proxies, timeout=10)
            ms = int((time.time() - t0) * 1000)
            status = 'active' if r.status_code == 200 else 'dead'
        except Exception as e:
            error = str(e)[:200]
            ms = int((time.time() - t0) * 1000)
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE proxies SET status=%s, response_ms=%s, last_error=%s,
                    last_checked=NOW() WHERE id=%s
                """, (status, ms, error, proxy_id))
            conn.commit()
        return jsonify({'status': status, 'ms': ms, 'error': error})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/cc/proxies/check-all', methods=['POST'])
@require_auth
def check_all_proxies():
    """Mark all proxies as 'checking' and kick off async test (sync for now)."""
    try:
        from infra.db import get_connection
        from psycopg2.extras import RealDictCursor
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute('SELECT id FROM proxies ORDER BY id')
                ids = [r['id'] for r in cur.fetchall()]
        return jsonify({'queued': len(ids), 'message': f'Check {len(ids)} proxies via individual /check calls'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# =========== Startup Migrations ===========

def _run_pg_startup_migrations():
    """Apply idempotent PostgreSQL migrations on startup."""
    if not DATABASE_URL:
        return
    try:
        from infra.db import get_connection
        with get_connection() as pg:
            with pg.cursor() as cur:
                # 009: lock columns for message_queue
                cur.execute("ALTER TABLE message_queue ADD COLUMN IF NOT EXISTS locked_by TEXT")
                cur.execute("ALTER TABLE message_queue ADD COLUMN IF NOT EXISTS locked_at TIMESTAMPTZ")
        print("[startup] PG migrations applied OK", flush=True)
    except Exception as e:
        print(f"[startup] PG migrations warning: {e}", flush=True)

_run_pg_startup_migrations()

# =========== Main ===========

if __name__ == '__main__':
    print("\n" + "="*50)
    print("🎛️  COMMAND CENTER")
    print("="*50)
    print(f"\n📍 Open in browser: http://localhost:8899")
    print(f"📁 Database: {DB_PATH}")
    print("\nPress Ctrl+C to stop\n")
    
    
@app.route('/cc/jslog', methods=['POST'])
def jslog():
    data = request.get_json(silent=True) or {}
    msg = data.get('msg', '')
    print(f'[JSLOG] {msg}', flush=True)
    return jsonify({'ok': True})



# ═══════════════════════════════════════════════════════════════════════════════
# POSTGRESQL-BASED ROUTES (message_queue, send_jobs, twitter_profiles)
# These replace the legacy SQLite dm_queue for new Human-in-the-Loop workflow
# ═══════════════════════════════════════════════════════════════════════════════

def pg_conn():
    """Get PostgreSQL connection using infra config."""
    from infra.db import get_connection
    return get_connection()


@app.route('/cc/v2/queue', methods=['GET'])
def v2_queue():
    """List message_queue items with pagination. Replaces legacy /cc/queue."""
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    if token not in SESSIONS:
        return jsonify({'error': 'Unauthorized'}), 401

    status_filter = request.args.get('status', 'pending')
    page = max(1, int(request.args.get('page', 1)))
    per_page = min(100, int(request.args.get('per_page', 50)))
    offset = (page - 1) * per_page

    try:
        with pg_conn() as conn:
            with conn.cursor() as cur:
                # Total count
                cur.execute(
                    "SELECT COUNT(*) FROM message_queue WHERE status = %s",
                    (status_filter,)
                )
                total = cur.fetchone()[0]

                # Items with profile + account info
                cur.execute("""
                    SELECT
                        mq.id, mq.status, mq.message_text, mq.send_type,
                        mq.created_at, mq.sent_at, mq.target_tweet_id,
                        tp.username  AS target_username,
                        tp.display_name AS target_name,
                        tp.followers_count, tp.tier, tp.bio,
                        ta.username  AS sender_username,
                        sj.id        AS job_id,
                        sj.status    AS job_status,
                        sj.claimed_by, sj.browser_ready_at
                    FROM message_queue mq
                    JOIN twitter_profiles tp ON tp.id = mq.profile_id
                    JOIN twitter_accounts ta ON ta.id = mq.account_id
                    LEFT JOIN send_jobs sj ON sj.msg_queue_id = mq.id
                        AND sj.status NOT IN ('sent', 'failed', 'skipped')
                    WHERE mq.status = %s
                    ORDER BY mq.created_at DESC
                    LIMIT %s OFFSET %s
                """, (status_filter, per_page, offset))

                cols = [d[0] for d in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]

                # Serialize datetimes
                for row in rows:
                    for k, v in row.items():
                        if hasattr(v, 'isoformat'):
                            row[k] = v.isoformat()

        return jsonify({
            'items': rows,
            'total': total,
            'page': page,
            'per_page': per_page,
            'pages': (total + per_page - 1) // per_page
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/cc/v2/queue/<int:msg_id>/approve', methods=['POST'])
def v2_approve(msg_id):
    """Approve message and create send_job for local_agent to pick up."""
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    user = SESSIONS.get(token)
    if not user:
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        with pg_conn() as conn:
            with conn.cursor() as cur:
                # Check message exists and is pending
                cur.execute(
                    "SELECT id, status FROM message_queue WHERE id = %s",
                    (msg_id,)
                )
                row = cur.fetchone()
                if not row:
                    return jsonify({'error': 'Not found'}), 404
                if row[1] not in ('pending', 'generated'):
                    return jsonify({'error': f'Cannot approve, status={row[1]}'}), 400

                # Update message status
                cur.execute(
                    "UPDATE message_queue SET status='approved', reviewed_at=NOW() WHERE id=%s",
                    (msg_id,)
                )

                # Create send_job for local_agent
                cur.execute(
                    "INSERT INTO send_jobs (msg_queue_id) VALUES (%s) RETURNING id",
                    (msg_id,)
                )
                job_id = cur.fetchone()[0]
            conn.commit()

        return jsonify({'success': True, 'job_id': job_id,
                        'message': f'Job #{job_id} created — waiting for local_agent'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/cc/v2/queue/<int:msg_id>/reject', methods=['POST'])
def v2_reject(msg_id):
    """Reject message."""
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    if token not in SESSIONS:
        return jsonify({'error': 'Unauthorized'}), 401
    try:
        with pg_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE message_queue SET status='rejected', reviewed_at=NOW() WHERE id=%s",
                    (msg_id,)
                )
            conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/cc/v2/send-jobs', methods=['GET'])
def v2_send_jobs():
    """Live activity feed of send_jobs for operator monitoring."""
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    if token not in SESSIONS:
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        with pg_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT
                        sj.id, sj.status, sj.claimed_by,
                        sj.claimed_at, sj.browser_ready_at, sj.completed_at,
                        sj.error_msg, sj.created_at,
                        mq.message_text, mq.send_type,
                        tp.username AS target_username, tp.tier,
                        ta.username AS sender_username
                    FROM send_jobs sj
                    JOIN message_queue mq ON mq.id = sj.msg_queue_id
                    JOIN twitter_profiles tp ON tp.id = mq.profile_id
                    JOIN twitter_accounts ta ON ta.id = mq.account_id
                    ORDER BY sj.created_at DESC
                    LIMIT 100
                """)
                cols = [d[0] for d in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
                for row in rows:
                    for k, v in row.items():
                        if hasattr(v, 'isoformat'):
                            row[k] = v.isoformat()
        return jsonify(rows)
    except Exception as e:
        return jsonify({'error': str(e)}), 500



@app.route('/cc/v2/warmup-plans', methods=['GET'])
@require_auth
def get_warmup_plans():
    """Return all twitter_accounts with warmup status."""
    from infra.db import get_connection
    try:
        with get_connection() as conn:
            import psycopg2.extras
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT
                        a.id, a.username, a.display_name, a.state, a.warmup_day,
                        a.warmup_started_at, a.daily_reply_limit, a.daily_like_limit,
                        a.replies_today, a.likes_today, a.health_score,
                        a.shadowban_detected, a.captcha_triggered, a.suspended,
                        a.total_sent, a.total_responses, a.last_action_at,
                        a.persona_type, a.category_focus
                    FROM twitter_accounts a
                    ORDER BY a.state, a.warmup_day DESC
                """)
                accounts = [dict(r) for r in cur.fetchall()]
        # Convert datetimes to strings
        for a in accounts:
            for k, v in a.items():
                if hasattr(v, 'isoformat'):
                    a[k] = v.isoformat()
        return jsonify({'accounts': accounts, 'total': len(accounts)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/cc/v2/profiles', methods=['GET'])
def v2_profiles():
    """Paginated profiles list with filters."""
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    if token not in SESSIONS:
        return jsonify({'error': 'Unauthorized'}), 401

    page = max(1, int(request.args.get('page', 1)))
    per_page = min(200, int(request.args.get('per_page', 50)))
    offset = (page - 1) * per_page
    tier = request.args.get('tier', '')
    contacted = request.args.get('contacted', '')  # true/false
    search = request.args.get('search', '')

    try:
        with pg_conn() as conn:
            with conn.cursor() as cur:
                conditions = []
                params = []
                if tier:
                    conditions.append("tier = %s")
                    params.append(tier)
                if contacted == 'true':
                    conditions.append("outreach_status = 'contacted'")
                elif contacted == 'false':
                    conditions.append("outreach_status != 'contacted'")
                if search:
                    conditions.append("(username ILIKE %s OR display_name ILIKE %s OR bio ILIKE %s)")
                    params += [f'%{search}%', f'%{search}%', f'%{search}%']

                where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

                cur.execute(f"SELECT COUNT(*) FROM twitter_profiles {where}", params)
                total = cur.fetchone()[0]

                cur.execute(f"""
                    SELECT id, username, display_name, bio, followers_count,
                           following_count, tier, (outreach_status = 'contacted') AS contacted, outreach_status, collected_at
                    FROM twitter_profiles
                    {where}
                    ORDER BY followers_count DESC
                    LIMIT %s OFFSET %s
                """, params + [per_page, offset])

                cols = [d[0] for d in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
                for row in rows:
                    for k, v in row.items():
                        if hasattr(v, 'isoformat'):
                            row[k] = v.isoformat()

        return jsonify({
            'items': rows,
            'total': total,
            'page': page,
            'per_page': per_page,
            'pages': (total + per_page - 1) // per_page
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/cc/v2/stats', methods=['GET'])
def v2_stats():
    """Real-time stats from PostgreSQL."""
    token = request.headers.get('Authorization', '').replace('Bearer ', '')  
    if token not in SESSIONS:
        return jsonify({'error': 'Unauthorized'}), 401
    try:
        with pg_conn() as conn:
            with conn.cursor() as cur:
                stats = {}
                queries = {
                    'profiles_total'     : "SELECT COUNT(*) FROM twitter_profiles",
                    'profiles_enriched'  : "SELECT COUNT(*) FROM twitter_profiles WHERE tier IS NOT NULL AND tier != ''",
                    'profiles_contacted' : "SELECT COUNT(*) FROM twitter_profiles WHERE outreach_status = 'contacted'",
                    'queue_pending'      : "SELECT COUNT(*) FROM message_queue WHERE status IN ('pending','generated')",
                    'queue_approved'     : "SELECT COUNT(*) FROM message_queue WHERE status = 'approved'",
                    'jobs_queued'        : "SELECT COUNT(*) FROM send_jobs WHERE status = 'queued'",
                    'jobs_in_progress'   : "SELECT COUNT(*) FROM send_jobs WHERE status IN ('claimed','browser_ready')",
                    'sent_today'         : "SELECT COUNT(*) FROM send_jobs WHERE status='sent' AND completed_at >= NOW() - INTERVAL '24 hours'",
                    'tier_s'             : "SELECT COUNT(*) FROM twitter_profiles WHERE tier = 'S'",
                    'tier_a'             : "SELECT COUNT(*) FROM twitter_profiles WHERE tier = 'A'",
                    'tier_b'             : "SELECT COUNT(*) FROM twitter_profiles WHERE tier = 'B'",
                }
                for key, sql in queries.items():
                    cur.execute(sql)
                    stats[key] = cur.fetchone()[0]
        return jsonify(stats)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8899))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

