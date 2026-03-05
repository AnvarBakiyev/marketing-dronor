#!/usr/bin/env python3
"""
SMOKE RUN — end-to-end pipeline test on synthetic data.
Does NOT require Twitter API or AdsPower.
Shows exactly where the pipeline breaks.
"""
import sys, json, logging
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
log = logging.getLogger('smoke')

from infra.db import execute_query, get_connection

STEP_OK  = "✅"
STEP_FAIL = "❌"
results = []

def step(name):
    def decorator(fn):
        def wrapper():
            try:
                out = fn()
                results.append((STEP_OK, name, out))
                print(f"{STEP_OK} {name}: {out}")
                return out
            except Exception as e:
                results.append((STEP_FAIL, name, str(e)))
                print(f"{STEP_FAIL} {name}: {e}")
                return None
        return wrapper
    return decorator

# ─── STEP 1: Insert synthetic twitter account ────────────────────────────────
@step("S1 — Insert twitter_account")
def step1():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO twitter_accounts
                    (username, state, daily_reply_limit, daily_like_limit, warmup_day)
                VALUES ('smoke_account', 'active', 50, 100, 15)
                ON CONFLICT (username) DO UPDATE SET state='active'
                RETURNING id
            """)
            acct_id = cur.fetchone()[0]
    return f"account_id={acct_id}"

# ─── STEP 2: Insert synthetic twitter profile ────────────────────────────────
@step("S2 — Insert twitter_profile (synthetic)")
def step2():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO twitter_profiles
                    (twitter_id, username, bio, followers_count, following_count,
                     tweets_count, outreach_status, tier, category,
                     professional_role, industry, primary_language,
                     tech_stack, topics_of_interest)
                VALUES
                    ('synthetic_001', 'smoke_target', 'Building AI automation tools. Founder @testco. Python, LangChain, GPT.',
                     12000, 800, 450, 'pending', 'A', 'automation_builder',
                     'Founder', 'AI/SaaS', 'en',
                     '["Python", "LangChain", "OpenAI"]',
                     '["AI agents", "automation", "SaaS"]')
                ON CONFLICT (twitter_id) DO UPDATE SET outreach_status='pending'
                RETURNING id
            """)
            pid = cur.fetchone()[0]
    return f"profile_id={pid}"

# ─── STEP 3: Generate message via M4 ─────────────────────────────────────────
@step("S3 — M4 message_generator (dry_run=True)")
def step3():
    from m4_message_generator.message_generator import message_generator
    result = message_generator(batch_size=1, dry_run=True)
    if result['errors'] > 0 and result['generated'] == 0:
        raise Exception(f"generator failed: {result}")
    return f"generated={result['generated']} tokens={result['tokens_used']}"

# ─── STEP 4: Generate message and save to queue ───────────────────────────────
@step("S4 — M4 message_generator (save to queue)")
def step4():
    from m4_message_generator.message_generator import message_generator
    result = message_generator(batch_size=1, dry_run=False)
    if result['errors'] > 0 and result['generated'] == 0:
        raise Exception(f"save failed: {result}")
    rows = execute_query("SELECT id, ab_variant, message_text, status FROM message_queue LIMIT 5")
    if not rows:
        raise Exception("message_queue still empty after generation")
    msg = rows[0]
    print(f"   variant={msg['ab_variant']} status={msg['status']}")
    print(f"   text: {str(msg['message_text'])[:120]}")
    return f"{len(rows)} messages in queue"

# ─── STEP 5: Operator review simulation ──────────────────────────────────────
@step("S5 — Operator approves message (status → in_review → approved)")
def step5():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE message_queue SET status='in_review'
                WHERE status='pending' RETURNING id
            """)
            reviewed = cur.rowcount
            cur.execute("""
                UPDATE message_queue SET status='approved'
                WHERE status='in_review' RETURNING id
            """)
            approved = cur.rowcount
    return f"moved {reviewed} to in_review, {approved} to approved"

# ─── STEP 6: Check CC backend can read the queue ─────────────────────────────
@step("S6 — CC backend /api/tasks reads queue")
def step6():
    import urllib.request
    try:
        with urllib.request.urlopen('http://localhost:5555/api/tasks', timeout=3) as r:
            data = json.loads(r.read())
            return f"{len(data)} tasks visible in UI"
    except Exception as e:
        raise Exception(f"CC backend not responding: {e}")

# ─── STEP 7: Check metrics endpoint ──────────────────────────────────────────
@step("S7 — CC backend /api/metrics")
def step7():
    import urllib.request
    with urllib.request.urlopen('http://localhost:5555/api/metrics', timeout=3) as r:
        data = json.loads(r.read())
        return f"fleet={data['fleet']} queue={data['queue']}"

# ─── STEP 8: Cleanup ─────────────────────────────────────────────────────────
@step("S8 — Cleanup synthetic data")
def step8():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM message_queue WHERE profile_id IN (SELECT id FROM twitter_profiles WHERE twitter_id='synthetic_001')")
            q = cur.rowcount
            cur.execute("DELETE FROM twitter_profiles WHERE twitter_id='synthetic_001'")
            p = cur.rowcount
            cur.execute("DELETE FROM twitter_accounts WHERE username='smoke_account'")
            a = cur.rowcount
    return f"cleaned queue={q} profiles={p} accounts={a}"


if __name__ == '__main__':
    print("\n" + "="*60)
    print("MARKETING DRONOR — E2E SMOKE RUN")
    print("="*60 + "\n")

    step1()
    step2()
    step3()
    step4()
    step5()
    step6()
    step7()
    step8()

    print("\n" + "="*60)
    ok  = sum(1 for r in results if r[0] == STEP_OK)
    fail = sum(1 for r in results if r[0] == STEP_FAIL)
    print(f"RESULT: {ok}/{len(results)} steps passed, {fail} failed")
    if fail:
        print("\nFailed steps:")
        for r in results:
            if r[0] == STEP_FAIL:
                print(f"  {r[1]}: {r[2]}")
    print("="*60 + "\n")
    sys.exit(0 if fail == 0 else 1)
