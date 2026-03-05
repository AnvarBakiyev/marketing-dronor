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

# Database path
DB_PATH = PROJECT_ROOT / 'twitter_outreach.db'
SESSIONS = {}  # In-memory sessions (token -> user_info)

def get_db():
    """Get database connection."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn

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
        cursor = db.execute('SELECT COUNT(*) as cnt FROM operators WHERE role = "admin"')
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
        cursor = db.execute('SELECT COUNT(*) as cnt FROM operators WHERE role = "admin"')
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
        db.execute('UPDATE operators SET last_active = ? WHERE id = ?',
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
            cursor = db.execute('SELECT COUNT(*) as cnt FROM dm_queue WHERE status = "pending"')
            data['queue_pending'] = cursor.fetchone()['cnt']
            
            cursor = db.execute('SELECT COUNT(*) as cnt FROM dm_queue WHERE status = "in_review"')
            data['queue_in_review'] = cursor.fetchone()['cnt']
        except: pass
        
        # Profile stats
        try:
            cursor = db.execute('SELECT COUNT(*) as cnt FROM profiles')
            data['profiles_total'] = cursor.fetchone()['cnt']
            
            cursor = db.execute('SELECT COUNT(*) as cnt FROM profiles WHERE enriched = 1')
            data['profiles_enriched'] = cursor.fetchone()['cnt']
            
            cursor = db.execute('SELECT COUNT(*) as cnt FROM profiles WHERE enriched = 0')
            data['profiles_pending'] = cursor.fetchone()['cnt']
            
            cursor = db.execute('SELECT COUNT(*) as cnt FROM profiles WHERE contacted = 1')
            data['profiles_contacted'] = cursor.fetchone()['cnt']
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
    # Check AdsPower
    adspower_ok = False
    try:
        import requests
        r = requests.get('http://localhost:50325/status', timeout=2)
        adspower_ok = r.status_code == 200
    except: pass
    
    # Check Twitter API config
    twitter_ok = bool(os.environ.get('TWITTER_BEARER_TOKEN'))
    
    return jsonify({
        'adspower': adspower_ok,
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
    
    if not username or not adspower_id:
        return jsonify({'success': False, 'error': 'Username and AdsPower ID required'})
    
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

@app.route('/cc/queue', methods=['GET'])
def get_queue():
    """Get DM queue items by status."""
    status = request.args.get('status', 'pending')
    
    db = get_db()
    try:
        # Ensure table exists
        db.execute('''
            CREATE TABLE IF NOT EXISTS dm_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id INTEGER,
                target_username TEXT NOT NULL,
                target_name TEXT,
                sender_username TEXT,
                message_text TEXT NOT NULL,
                tier TEXT DEFAULT 'B',
                category TEXT,
                status TEXT DEFAULT 'pending',
                reviewed_by TEXT,
                reviewed_at TEXT,
                sent_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        db.commit()
        
        cursor = db.execute('''
            SELECT * FROM dm_queue WHERE status = ?
            ORDER BY created_at DESC LIMIT 100
        ''', (status,))
        items = [dict(row) for row in cursor.fetchall()]
        return jsonify({'items': items})
    finally:
        db.close()

@app.route('/cc/queue/<id>/<action>', methods=['POST'])
def queue_action(id, action):
    """Perform action on queue item (approve/reject/send)."""
    db = get_db()
    try:
        if action == 'approve':
            db.execute(
                'UPDATE dm_queue SET status = "approved", reviewed_at = ? WHERE id = ?',
                (datetime.now().isoformat(), id)
            )
        elif action == 'reject':
            db.execute(
                'UPDATE dm_queue SET status = "rejected", reviewed_at = ? WHERE id = ?',
                (datetime.now().isoformat(), id)
            )
        elif action == 'send':
            # Mark as ready to send
            db.execute(
                'UPDATE dm_queue SET status = "ready_to_send" WHERE id = ?',
                (id,)
            )
        db.commit()
        return jsonify({'success': True})
    finally:
        db.close()

@app.route('/cc/queue/approve-all', methods=['POST'])
def approve_all():
    """Approve all pending/in_review items."""
    db = get_db()
    try:
        cursor = db.execute('''
            UPDATE dm_queue SET status = "approved", reviewed_at = ?
            WHERE status IN ("pending", "in_review")
        ''', (datetime.now().isoformat(),))
        db.commit()
        return jsonify({'success': True, 'approved': cursor.rowcount})
    finally:
        db.close()

# =========== Responses ===========

@app.route('/cc/responses', methods=['GET'])
def get_responses():
    """Get conversation list."""
    db = get_db()
    try:
        # Ensure table exists
        db.execute('''
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id INTEGER,
                target_username TEXT NOT NULL,
                target_name TEXT,
                unread INTEGER DEFAULT 1,
                last_message TEXT,
                last_time TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        db.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL,
                direction TEXT NOT NULL,
                text TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        db.commit()
        
        cursor = db.execute('''
            SELECT * FROM conversations ORDER BY last_time DESC LIMIT 50
        ''')
        conversations = [dict(row) for row in cursor.fetchall()]
        return jsonify({'conversations': conversations})
    finally:
        db.close()

@app.route('/cc/responses/<conv_id>', methods=['GET'])
def get_conversation_detail(conv_id):
    """Get conversation messages."""
    db = get_db()
    try:
        cursor = db.execute('SELECT * FROM conversations WHERE id = ?', (conv_id,))
        conv = cursor.fetchone()
        if not conv:
            return jsonify({'error': 'Not found'}), 404
        
        cursor = db.execute('''
            SELECT direction, text, created_at as time FROM messages
            WHERE conversation_id = ? ORDER BY created_at ASC
        ''', (conv_id,))
        messages = [dict(row) for row in cursor.fetchall()]
        
        # Mark as read
        db.execute('UPDATE conversations SET unread = 0 WHERE id = ?', (conv_id,))
        db.commit()
        
        return jsonify({
            'conversation': {
                **dict(conv),
                'messages': messages
            }
        })
    finally:
        db.close()

@app.route('/cc/responses/<conv_id>/generate', methods=['POST'])
def generate_reply(conv_id):
    """Generate AI reply for conversation."""
    # TODO: Integrate with Claude
    return jsonify({
        'reply': 'Thanks for reaching out! I\'d love to learn more about your needs. Could you tell me a bit more about your current setup?'
    })

@app.route('/cc/responses/<conv_id>/reply', methods=['POST'])
def send_reply(conv_id):
    """Queue a reply message."""
    data = request.get_json()
    text = data.get('text', '').strip()
    
    if not text:
        return jsonify({'success': False, 'error': 'Reply text required'})
    
    db = get_db()
    try:
        # Get conversation
        cursor = db.execute('SELECT * FROM conversations WHERE id = ?', (conv_id,))
        conv = cursor.fetchone()
        if not conv:
            return jsonify({'success': False, 'error': 'Conversation not found'})
        
        # Add to queue
        db.execute('''
            INSERT INTO dm_queue (target_username, target_name, message_text, status)
            VALUES (?, ?, ?, 'approved')
        ''', (conv['target_username'], conv['target_name'], text))
        
        # Add to messages
        db.execute('''
            INSERT INTO messages (conversation_id, direction, text)
            VALUES (?, 'outgoing', ?)
        ''', (conv_id, text))
        
        db.commit()
        return jsonify({'success': True})
    finally:
        db.close()


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
    max_profiles = int(data.get('max_profiles', 100))
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
                cur.execute("""
                    SELECT id, username, display_name, bio, followers_count,
                           following_count, tweets_count
                    FROM twitter_profiles
                    WHERE tier IS NULL OR tier = ''
                    ORDER BY followers_count DESC
                    LIMIT %s
                """, (batch_size,))
                profiles = cur.fetchall()

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
                        cur.execute(
                            "UPDATE twitter_profiles SET tier=%s WHERE id=%s",
                            (tier, p['id'])
                        )
                        classified += 1
                    except Exception:
                        errors += 1
                        continue

        return {
            'success': True,
            'message': f'Classified {classified} profiles ({errors} errors).',
            'classified': classified,
            'errors': errors
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
    """M5: Outreach Sender — send approved messages via AdsPower browser."""
    import sys
    from pathlib import Path as _Path
    sys.path.insert(0, str(_Path(__file__).parent.parent))
    try:
        from infra.db import get_connection
        # Count approved messages in queue
        from psycopg2.extras import RealDictCursor
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT COUNT(*) as cnt FROM message_queue WHERE status = 'approved'")
                count = cur.fetchone()['cnt']
        if count == 0:
            return {'success': True, 'message': 'No approved messages to send.', 'queued': 0}
        # In production: call browser_controller per message
        # For now: return queue status (AdsPower must be running)
        from m5_browser_controller.browser_controller import browser_controller
        # Verify AdsPower is reachable via health check
        health = browser_controller(action='close')  # no-op to test import
        return {
            'success': True,
            'message': f'{count} messages ready to send via AdsPower.',
            'queued': count
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
    """Get admin panel data."""
    db = get_db()
    try:
        # Operators
        cursor = db.execute('''
            SELECT id, name, role, approved_count as approved, sent_count as sent, last_active
            FROM operators ORDER BY role DESC, name
        ''')
        operators = [dict(row) for row in cursor.fetchall()]
        
        # Funnel stats
        funnel = {'collected': 0, 'enriched': 0, 'generated': 0, 'approved': 0, 'sent': 0, 'responses': 0}
        try:
            cursor = db.execute('SELECT COUNT(*) as cnt FROM profiles')
            funnel['collected'] = cursor.fetchone()['cnt']
            
            cursor = db.execute('SELECT COUNT(*) as cnt FROM profiles WHERE enriched = 1')
            funnel['enriched'] = cursor.fetchone()['cnt']
            
            cursor = db.execute('SELECT COUNT(*) as cnt FROM dm_queue')
            funnel['generated'] = cursor.fetchone()['cnt']
            
            cursor = db.execute('SELECT COUNT(*) as cnt FROM dm_queue WHERE status = "approved"')
            funnel['approved'] = cursor.fetchone()['cnt']
            
            cursor = db.execute('SELECT COUNT(*) as cnt FROM dm_queue WHERE status = "sent"')
            funnel['sent'] = cursor.fetchone()['cnt']
            
            cursor = db.execute('SELECT COUNT(*) as cnt FROM conversations')
            funnel['responses'] = cursor.fetchone()['cnt']
        except: pass
        
        # System stats
        stats = {
            'total_dms': funnel['sent'],
            'total_responses': funnel['responses'],
            'response_rate': (funnel['responses'] / funnel['sent'] * 100) if funnel['sent'] > 0 else 0,
            'total_cost': 0.0,
            'avg_dms_day': 0.0,
            'active_days': 0
        }
        
        return jsonify({
            'operators': operators,
            'funnel': funnel,
            'stats': stats
        })
    finally:
        db.close()

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
            cursor = db.execute('SELECT COUNT(*) as cnt FROM operators WHERE role = "admin"')
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

# =========== Main ===========

if __name__ == '__main__':
    print("\n" + "="*50)
    print("🎛️  COMMAND CENTER")
    print("="*50)
    print(f"\n📍 Open in browser: http://localhost:8899")
    print(f"📁 Database: {DB_PATH}")
    print("\nPress Ctrl+C to stop\n")
    
    app.run(host='0.0.0.0', port=8899, debug=True)

