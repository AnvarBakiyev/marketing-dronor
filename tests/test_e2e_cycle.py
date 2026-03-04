"""
MKT-26: E2E Integration Test
Cycle: M1 -> M2 -> M4 -> M3(DB check) -> M5(DB sim) -> M6
"""
import sys, time, uuid
from pathlib import Path
sys.path.insert(0, str(Path.home() / 'marketing-dronor'))

from infra.db import execute_query, get_connection
from infra.config import DB_CONFIG, ANTHROPIC_API_KEY
import psycopg2.extras

PASS, FAIL = [], []
DB_CONN_STR = "postgresql://{user}@{host}:{port}/{dbname}".format(**DB_CONFIG)

def check(name, cond, detail=''):
    if cond: PASS.append(name); print(f'  OK {name}')
    else: FAIL.append(name); print(f'  FAIL {name}: {detail}')

def dc(conn):
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

def setup_account():
    with get_connection() as conn:
        with dc(conn) as cur:
            cur.execute(
                "INSERT INTO twitter_accounts (username, display_name, persona_type, state) "
                "VALUES ('e2e_acct','E2E Account','saas_founder','active') "
                "ON CONFLICT (username) DO UPDATE SET state='active' RETURNING id"
            )
            return cur.fetchone()['id']

def cleanup():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM message_queue WHERE account_id IN (SELECT id FROM twitter_accounts WHERE username='e2e_acct')")
            cur.execute("DELETE FROM twitter_profiles WHERE username LIKE 'e2e_%'")
            cur.execute("DELETE FROM twitter_accounts WHERE username='e2e_acct'")

def step_m1(n=3):
    print('\n[M1] Inserting synthetic profiles...')
    ids = []
    with get_connection() as conn:
        with dc(conn) as cur:
            for i in range(n):
                cur.execute(
                    "INSERT INTO twitter_profiles "
                    "(twitter_id, username, display_name, bio, "
                    " followers_count, following_count, tweets_count, "
                    " tier, category, identified_needs, professional_role, outreach_status) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,'B','saas_founder','[]','Founder','pending') "
                    "RETURNING id",
                    (
                        'e2e' + uuid.uuid4().hex[:14],
                        'e2e_' + uuid.uuid4().hex[:6],
                        'E2E User ' + str(i),
                        'Building AI automation tools for solopreneurs. Love productivity hacks.',
                        1200 + i*400, 250, 180 + i*40
                    )
                )
                ids.append(cur.fetchone()['id'])
    rows = execute_query("SELECT id FROM twitter_profiles WHERE username LIKE 'e2e_%'")
    check('M1: profiles in DB', len(rows) == n, str(len(rows)) + '/' + str(n))
    return ids

def step_m2(profile_ids):
    print('\n[M2] Classifying profiles (Anthropic API)...')
    from m2_profile_analyzer.wave_classifier import wave_classifier
    from m2_profile_analyzer.category_detector import category_detector
    rows = execute_query(
        'SELECT id, username, display_name, bio, followers_count, following_count, '
        'tweets_count, tier, category, identified_needs FROM twitter_profiles WHERE id = %s',
        (profile_ids[0],)
    )
    profile = dict(rows[0])
    tweets = [
        {'text': 'Just shipped a new automation workflow. Saved 3 hours/day #automation #ai'},
        {'text': 'The best solopreneur tools are invisible - they just work.'},
    ]
    r1 = wave_classifier(profile=profile, tweets=tweets, anthropic_api_key=ANTHROPIC_API_KEY)
    check('M2: wave_classifier success', r1.get('status') == 'success', str(r1)[:150])
    check('M2: tier in result', 'tier' in r1 or 'wave' in r1, str(r1)[:150])
    r2 = category_detector(profile=profile, tweets=tweets, anthropic_api_key=ANTHROPIC_API_KEY)
    check('M2: category_detector success', r2.get('status') == 'success', str(r2)[:150])

def step_m4(profile_ids):
    print('\n[M4] Generating messages + compliance...')
    from m4_message_generator.message_generator import message_generator
    from m4_message_generator.compliance_checker import compliance_checker
    r1 = message_generator(batch_size=len(profile_ids), dry_run=False)
    check('M4: message_generator success', r1.get('status') == 'success', str(r1)[:150])
    check('M4: generated > 0', r1.get('generated', 0) > 0, 'generated=' + str(r1.get('generated')))
    rows = execute_query('SELECT id FROM message_queue WHERE profile_id = ANY(%s)', (profile_ids,))
    check('M4: messages in DB', len(rows) > 0, str(len(rows)) + ' rows')
    r2 = compliance_checker(batch_size=len(profile_ids))
    check('M4: compliance_checker success', r2.get('status') == 'success', str(r2)[:150])

def step_m3_db(profile_ids):
    print('\n[M3] Verifying queue state in DB...')
    rows = execute_query('SELECT id, status FROM message_queue WHERE profile_id = ANY(%s)', (profile_ids,))
    check('M3: messages in queue', len(rows) > 0, str(len(rows)) + ' rows')
    statuses = {r['status'] for r in rows}
    check('M3: has pending/in_review', bool(statuses & {'pending', 'in_review'}), 'statuses=' + str(statuses))

def step_m5_db(profile_ids):
    print('\n[M5] Simulating operator approval...')
    from m5_operator_interface.task_presenter import task_presenter
    r1 = task_presenter(batch_size=3)
    check('M5: task_presenter success', r1.get('status') == 'success', str(r1)[:150])
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE message_queue SET status='in_review', operator_id='e2e_op' "
                "WHERE profile_id = ANY(%s) AND status = 'pending'",
                (profile_ids,)
            )
            updated = cur.rowcount
    check('M5: moved to in_review', updated > 0, 'updated=' + str(updated))
    rows = execute_query("SELECT id FROM message_queue WHERE profile_id=ANY(%s) AND status='in_review'", (profile_ids,))
    check('M5: in_review in DB', len(rows) > 0, str(len(rows)))

def step_m6(profile_ids):
    print('\n[M6] Response detection...')
    from m6_response_tracker.response_detector import response_detector
    from m6_response_tracker.response_matcher import response_matcher
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE message_queue SET status='sent', sent_at=NOW() "
                "WHERE profile_id=ANY(%s) AND status IN ('pending','in_review')",
                (profile_ids,)
            )
    rows = execute_query("SELECT id FROM message_queue WHERE profile_id=ANY(%s) AND status='sent'", (profile_ids,))
    check('M6: sent in DB', len(rows) > 0, str(len(rows)))
    r1 = response_detector(
        account_ids='', db_connection_string=DB_CONN_STR,
        twitter_bearer_token_key='', lookback_hours=1, batch_size=5
    )
    check('M6: response_detector callable', r1.get('status') in ('success','error'), str(r1)[:150])
    r2 = response_matcher(
        response_id='e2e_001', response_author_id='e2e_author',
        response_text='Thanks! Tell me more about Dronor.',
        response_type='reply', db_connection_string=DB_CONN_STR
    )
    check('M6: response_matcher callable', r2.get('status') in ('success','no_match','error'), str(r2)[:150])

if __name__ == '__main__':
    print('=' * 60)
    print('Marketing Dronor - E2E Test [MKT-26]')
    print('=' * 60)
    t0 = time.time()
    account_id = setup_account()
    try:
        pids = step_m1(3)
        step_m2(pids)
        step_m4(pids)
        step_m3_db(pids)
        step_m5_db(pids)
        step_m6(pids)
    finally:
        cleanup()
    elapsed = time.time() - t0
    total = len(PASS) + len(FAIL)
    print('\n' + '=' * 60)
    print('Result: %d/%d passed in %.1fs' % (len(PASS), total, elapsed))
    if FAIL:
        print('Failed: ' + ', '.join(FAIL))
    print('=' * 60)
    sys.exit(0 if not FAIL else 1)
