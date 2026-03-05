"""
MKT-35: E2E Test v2 — Sprint 4 Real Stack Validation

Validates:
1. Schema v002 — all 5 new tables exist with correct columns (real schema)
2. AdsPower profiles CRUD
3. Warmup schedule logic
4. Activity log writes
5. Account restrictions tracking
6. TwitterAPI calls log
7. Command Center API endpoints (backend running)
8. Inter-module data flow: account → adspower_profile → warmup → activity

Run: python3 tests/test_e2e_sprint4.py
Requires: PostgreSQL running, cc_backend.py running on localhost:5555
"""

import sys
import json
import time
import unittest
import psycopg2
import psycopg2.extras
import requests
from datetime import date, datetime, timedelta

# ─── DB Connection ────────────────────────────────────────────────────────────
DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "database": "marketing_dronor",
    "user": "anvarbakiyev",
}
BACKEND_URL = "http://localhost:5555"

import socket as _socket
def _backend_running():
    try:
        s = _socket.create_connection(("localhost", 5555), timeout=1)
        s.close()
        return True
    except OSError:
        return False

_BACKEND_SKIP = not _backend_running()



def get_conn():
    return psycopg2.connect(**DB_CONFIG)


def cleanup(conn, account_username="e2e_test_account_v2"):
    """Remove all test data in correct FK order."""
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM twitter_accounts WHERE username = %s", (account_username,))
        row = cur.fetchone()
        if row:
            acct_id = row[0]
            # Clear FK-dependent tables first
            cur.execute("DELETE FROM activity_log WHERE account_id = %s", (acct_id,))
            # warmup_schedule links to adspower_profiles, not account directly
            cur.execute("""
                DELETE FROM warmup_schedule WHERE adspower_profile_id IN (
                    SELECT id FROM adspower_profiles WHERE account_id = %s
                )
            """, (acct_id,))
            cur.execute("DELETE FROM account_restrictions WHERE account_id = %s", (acct_id,))
            cur.execute("DELETE FROM adspower_profiles WHERE account_id = %s", (acct_id,))
            cur.execute("DELETE FROM twitter_accounts WHERE id = %s", (acct_id,))
        cur.execute("DELETE FROM twitterapi_calls_log WHERE endpoint = 'e2e_test_endpoint'")
    conn.commit()


# ─── Test Suite ───────────────────────────────────────────────────────────────

class TestSchemaV002(unittest.TestCase):
    """Verify all Sprint 4 tables exist with correct real columns."""

    def setUp(self):
        self.conn = get_conn()

    def tearDown(self):
        self.conn.close()

    def _get_columns(self, table):
        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = %s AND table_schema = 'public'
            """, (table,))
            return {r[0] for r in cur.fetchall()}

    def test_adspower_profiles_columns(self):
        cols = self._get_columns("adspower_profiles")
        required = {"id", "account_id", "adspower_profile_id", "serial_number",
                    "warmup_stage", "warmup_day", "proxy_host", "proxy_port"}
        missing = required - cols
        self.assertEqual(missing, set(), f"adspower_profiles missing columns: {missing}")

    def test_warmup_schedule_columns(self):
        """warmup_schedule links to adspower_profile_id, uses daily_*/done_* naming."""
        cols = self._get_columns("warmup_schedule")
        required = {"id", "adspower_profile_id", "schedule_date", "phase",
                    "daily_likes", "daily_replies", "daily_follows", "daily_dms",
                    "likes_done", "replies_done", "follows_done", "dms_done", "completed"}
        missing = required - cols
        self.assertEqual(missing, set(), f"warmup_schedule missing columns: {missing}")

    def test_activity_log_columns(self):
        """activity_log uses executed_at (not created_at), target_username (not target_user_id)."""
        cols = self._get_columns("activity_log")
        required = {"id", "account_id", "adspower_profile_id", "action_type", "status",
                    "target_username", "target_tweet_id", "duration_ms", "executed_at"}
        missing = required - cols
        self.assertEqual(missing, set(), f"activity_log missing columns: {missing}")

    def test_account_restrictions_columns(self):
        cols = self._get_columns("account_restrictions")
        required = {"id", "account_id", "restriction_type", "detected_at", "is_active"}
        missing = required - cols
        self.assertEqual(missing, set(), f"account_restrictions missing columns: {missing}")

    def test_twitterapi_calls_log_columns(self):
        cols = self._get_columns("twitterapi_calls_log")
        required = {"id", "endpoint", "provider", "cost_usd", "records_returned", "called_at"}
        missing = required - cols
        self.assertEqual(missing, set(), f"twitterapi_calls_log missing columns: {missing}")


class TestAdsPowerProfileCRUD(unittest.TestCase):
    """Test adspower_profiles table operations."""

    def setUp(self):
        self.conn = get_conn()
        cleanup(self.conn)
        # Create base twitter_account (uses category_focus, not category)
        with self.conn.cursor() as cur:
            cur.execute("""
                INSERT INTO twitter_accounts (username, category_focus, state)
                VALUES (%s, %s, %s) RETURNING id
            """, ("e2e_test_account_v2", "crypto", "warming"))
            self.account_id = cur.fetchone()[0]
        self.conn.commit()

    def tearDown(self):
        self.conn.rollback()  # Reset aborted tx if test failed
        cleanup(self.conn)
        self.conn.close()

    def test_create_adspower_profile(self):
        with self.conn.cursor() as cur:
            cur.execute("""
                INSERT INTO adspower_profiles
                    (account_id, adspower_profile_id, serial_number, warmup_stage, warmup_day)
                VALUES (%s, %s, %s, %s, %s) RETURNING id
            """, (self.account_id, "ap_test_001", "SN_001", "new", 1))
            profile_id = cur.fetchone()[0]
        self.conn.commit()
        self.assertIsNotNone(profile_id)

        with self.conn.cursor() as cur:
            cur.execute("SELECT warmup_stage FROM adspower_profiles WHERE id = %s", (profile_id,))
            row = cur.fetchone()
        self.assertEqual(row[0], "new")

    def test_update_warmup_stage(self):
        with self.conn.cursor() as cur:
            cur.execute("""
                INSERT INTO adspower_profiles
                    (account_id, adspower_profile_id, serial_number, warmup_stage, warmup_day)
                VALUES (%s, %s, %s, %s, %s) RETURNING id
            """, (self.account_id, "ap_test_002", "SN_002", "new", 1))
            profile_id = cur.fetchone()[0]
            cur.execute("""
                UPDATE adspower_profiles SET warmup_stage = %s, warmup_day = %s
                WHERE id = %s
            """, ("warming", 8, profile_id))
        self.conn.commit()

        with self.conn.cursor() as cur:
            cur.execute("SELECT warmup_stage, warmup_day FROM adspower_profiles WHERE id = %s", (profile_id,))
            row = cur.fetchone()
        self.assertEqual(row[0], "warming")
        self.assertEqual(row[1], 8)


class TestWarmupSchedule(unittest.TestCase):
    """Test warmup_schedule table with real schema (adspower_profile_id FK)."""

    def setUp(self):
        self.conn = get_conn()
        cleanup(self.conn)
        with self.conn.cursor() as cur:
            cur.execute("""
                INSERT INTO twitter_accounts (username, category_focus, state)
                VALUES (%s, %s, %s) RETURNING id
            """, ("e2e_test_account_v2", "crypto", "warming"))
            self.account_id = cur.fetchone()[0]
            cur.execute("""
                INSERT INTO adspower_profiles
                    (account_id, adspower_profile_id, serial_number, warmup_stage, warmup_day)
                VALUES (%s, %s, %s, %s, %s) RETURNING id
            """, (self.account_id, "ap_ws_001", "SN_WS_001", "new", 1))
            self.profile_id = cur.fetchone()[0]
        self.conn.commit()

    def tearDown(self):
        self.conn.rollback()  # Reset aborted tx if test failed
        cleanup(self.conn)
        self.conn.close()

    def test_create_warmup_schedule(self):
        """warmup_schedule uses daily_likes/daily_dms and likes_done/dms_done."""
        with self.conn.cursor() as cur:
            cur.execute("""
                INSERT INTO warmup_schedule
                    (adspower_profile_id, schedule_date, warmup_day, phase,
                     daily_likes, daily_replies, daily_follows, daily_dms)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
            """, (self.profile_id, date.today(), 1, "foundation", 10, 5, 3, 0))
            sched_id = cur.fetchone()[0]
        self.conn.commit()
        self.assertIsNotNone(sched_id)

    def test_increment_done_counter(self):
        with self.conn.cursor() as cur:
            cur.execute("""
                INSERT INTO warmup_schedule
                    (adspower_profile_id, schedule_date, warmup_day, phase, daily_likes)
                VALUES (%s, %s, %s, %s, %s) RETURNING id
            """, (self.profile_id, date.today(), 1, "foundation", 10))
            sched_id = cur.fetchone()[0]
            cur.execute("""
                UPDATE warmup_schedule SET likes_done = likes_done + 1 WHERE id = %s
            """, (sched_id,))
        self.conn.commit()

        with self.conn.cursor() as cur:
            cur.execute("SELECT likes_done FROM warmup_schedule WHERE id = %s", (sched_id,))
            row = cur.fetchone()
        self.assertEqual(row[0], 1)


class TestActivityLog(unittest.TestCase):
    """Test activity_log with real schema (executed_at, target_username)."""

    def setUp(self):
        self.conn = get_conn()
        cleanup(self.conn)
        with self.conn.cursor() as cur:
            cur.execute("""
                INSERT INTO twitter_accounts (username, category_focus, state)
                VALUES (%s, %s, %s) RETURNING id
            """, ("e2e_test_account_v2", "crypto", "warming"))
            self.account_id = cur.fetchone()[0]
            cur.execute("""
                INSERT INTO adspower_profiles
                    (account_id, adspower_profile_id, serial_number, warmup_stage, warmup_day)
                VALUES (%s, %s, %s, %s, %s) RETURNING id
            """, (self.account_id, "ap_al_001", "SN_AL_001", "new", 1))
            self.profile_id = cur.fetchone()[0]
        self.conn.commit()

    def tearDown(self):
        self.conn.rollback()  # Reset aborted tx if test failed
        cleanup(self.conn)
        self.conn.close()

    def test_log_send_dm(self):
        """activity_log uses target_username (not target_user_id), executed_at (not created_at)."""
        with self.conn.cursor() as cur:
            cur.execute("""
                INSERT INTO activity_log
                    (adspower_profile_id, account_id, action_type, target_username, status, duration_ms)
                VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
            """, (self.profile_id, self.account_id, "send_dm", "target_user_123", "success", 1500))
            log_id = cur.fetchone()[0]
        self.conn.commit()
        self.assertIsNotNone(log_id)

        with self.conn.cursor() as cur:
            cur.execute("SELECT action_type, target_username, executed_at FROM activity_log WHERE id = %s", (log_id,))
            row = cur.fetchone()
        self.assertEqual(row[0], "send_dm")
        self.assertEqual(row[1], "target_user_123")
        self.assertIsNotNone(row[2])  # executed_at auto-set

    def test_log_multiple_actions(self):
        actions = ["like_tweet", "follow_user", "scroll"]
        with self.conn.cursor() as cur:
            for action in actions:
                cur.execute("""
                    INSERT INTO activity_log
                        (adspower_profile_id, account_id, action_type, status)
                    VALUES (%s, %s, %s, %s)
                """, (self.profile_id, self.account_id, action, "success"))
        self.conn.commit()

        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) FROM activity_log
                WHERE account_id = %s AND action_type = ANY(%s)
            """, (self.account_id, actions))
            count = cur.fetchone()[0]
        self.assertEqual(count, 3)


class TestTwitterAPICallsLog(unittest.TestCase):
    """Test twitterapi_calls_log cost tracking."""

    def setUp(self):
        self.conn = get_conn()
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM twitterapi_calls_log WHERE endpoint = 'e2e_test_endpoint'")
        self.conn.commit()

    def tearDown(self):
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM twitterapi_calls_log WHERE endpoint = 'e2e_test_endpoint'")
        self.conn.commit()
        self.conn.close()

    def test_log_api_call(self):
        with self.conn.cursor() as cur:
            cur.execute("""
                INSERT INTO twitterapi_calls_log
                    (endpoint, provider, cost_usd, records_returned)
                VALUES (%s, %s, %s, %s) RETURNING id
            """, ("e2e_test_endpoint", "twitterapi_io", 0.18, 1000))
            log_id = cur.fetchone()[0]
        self.conn.commit()
        self.assertIsNotNone(log_id)

    def test_cost_aggregation(self):
        with self.conn.cursor() as cur:
            for i in range(3):
                cur.execute("""
                    INSERT INTO twitterapi_calls_log (endpoint, provider, cost_usd, records_returned)
                    VALUES (%s, %s, %s, %s)
                """, ("e2e_test_endpoint", "twitterapi_io", 0.18, 1000))
        self.conn.commit()

        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT SUM(cost_usd), SUM(records_returned)
                FROM twitterapi_calls_log
                WHERE endpoint = 'e2e_test_endpoint'
            """)
            total_cost, total_records = cur.fetchone()

        self.assertAlmostEqual(float(total_cost), 0.54, places=2)
        self.assertEqual(total_records, 3000)


@unittest.skipIf(_BACKEND_SKIP, "cc_backend not running")
class TestCommandCenterAPI(unittest.TestCase):
    """Test all Command Center Flask endpoints — validates real API responses."""

    def test_metrics_endpoint(self):
        r = requests.get(f"{BACKEND_URL}/api/metrics", timeout=5)
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("fleet", data)
        self.assertIn("queue", data)
        self.assertIn("today", data)

    def test_accounts_endpoint(self):
        r = requests.get(f"{BACKEND_URL}/api/accounts", timeout=5)
        self.assertEqual(r.status_code, 200)
        self.assertIsInstance(r.json(), list)

    def test_tasks_endpoint(self):
        """Tasks endpoint returns a list (not a dict with 'tasks' key)."""
        r = requests.get(f"{BACKEND_URL}/api/tasks", timeout=5)
        self.assertEqual(r.status_code, 200)
        self.assertIsInstance(r.json(), list)

    def test_timeline_endpoint(self):
        """Timeline endpoint returns a list (not a dict with 'timeline' key)."""
        r = requests.get(f"{BACKEND_URL}/api/timeline", timeout=5)
        self.assertEqual(r.status_code, 200)
        self.assertIsInstance(r.json(), list)

    def test_pause_resume_tasks(self):
        """Pause returns {"status": "paused"}, resume returns {"status": "resumed"}."""
        r = requests.post(f"{BACKEND_URL}/api/tasks/pause-all", timeout=5)
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data.get("status"), "paused")

        r2 = requests.post(f"{BACKEND_URL}/api/tasks/resume-all", timeout=5)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.json().get("status"), "resumed")

    def test_frontend_servers(self):
        r = requests.get(f"{BACKEND_URL}/", timeout=5)
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/html", r.headers.get("Content-Type", ""))


@unittest.skipIf(_BACKEND_SKIP, "cc_backend not running")
class TestEndToEndFlow(unittest.TestCase):
    """Full flow: account → adspower_profile → warmup_schedule → activity_log → metrics API."""

    def setUp(self):
        self.conn = get_conn()
        cleanup(self.conn)

    def tearDown(self):
        self.conn.rollback()  # Reset aborted tx if test failed
        cleanup(self.conn)
        self.conn.close()

    def test_full_account_warmup_flow(self):
        # Step 1: Create twitter account (uses category_focus)
        with self.conn.cursor() as cur:
            cur.execute("""
                INSERT INTO twitter_accounts
                    (username, category_focus, state, warmup_day)
                VALUES (%s, %s, %s, %s) RETURNING id
            """, ("e2e_test_account_v2", "crypto", "warming", 1))
            account_id = cur.fetchone()[0]
        self.conn.commit()
        self.assertIsNotNone(account_id)

        # Step 2: Create AdsPower profile for account
        with self.conn.cursor() as cur:
            cur.execute("""
                INSERT INTO adspower_profiles
                    (account_id, adspower_profile_id, serial_number, warmup_stage, warmup_day)
                VALUES (%s, %s, %s, %s, %s) RETURNING id
            """, (account_id, "ap_e2e_001", "SN_E2E_001", "new", 1))
            profile_id = cur.fetchone()[0]
        self.conn.commit()

        # Step 3: Create warmup schedule for today (FK to adspower_profile_id)
        with self.conn.cursor() as cur:
            cur.execute("""
                INSERT INTO warmup_schedule
                    (adspower_profile_id, schedule_date, warmup_day, phase,
                     daily_likes, daily_replies, daily_dms)
                VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id
            """, (profile_id, date.today(), 1, "foundation", 10, 3, 0))
            sched_id = cur.fetchone()[0]
        self.conn.commit()

        # Step 4: Log activity
        with self.conn.cursor() as cur:
            cur.execute("""
                INSERT INTO activity_log
                    (adspower_profile_id, account_id, action_type, target_username, status, duration_ms)
                VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
            """, (profile_id, account_id, "like_tweet", "some_influencer", "success", 800))
            log_id = cur.fetchone()[0]
            # Update done counter
            cur.execute("""
                UPDATE warmup_schedule SET likes_done = likes_done + 1 WHERE id = %s
            """, (sched_id,))
        self.conn.commit()

        # Step 5: Verify via DB
        with self.conn.cursor() as cur:
            cur.execute("SELECT likes_done FROM warmup_schedule WHERE id = %s", (sched_id,))
            self.assertEqual(cur.fetchone()[0], 1)

            cur.execute("SELECT COUNT(*) FROM activity_log WHERE account_id = %s", (account_id,))
            self.assertEqual(cur.fetchone()[0], 1)

        # Step 6: Verify via Command Center API
        r = requests.get(f"{BACKEND_URL}/api/metrics", timeout=5)
        self.assertEqual(r.status_code, 200)
        metrics = r.json()
        self.assertIn("fleet", metrics)
        # fleet.total should include our new account
        self.assertGreaterEqual(metrics["fleet"]["total"], 1)


# ─── Runner ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 70)
    print("MKT-35: E2E Test v2 — Sprint 4 Validation")
    print("=" * 70)

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    test_classes = [
        TestSchemaV002,
        TestAdsPowerProfileCRUD,
        TestWarmupSchedule,
        TestActivityLog,
        TestTwitterAPICallsLog,
        TestCommandCenterAPI,
        TestEndToEndFlow,
    ]

    for tc in test_classes:
        suite.addTests(loader.loadTestsFromTestCase(tc))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    print("\n" + "=" * 70)
    print(f"Total: {result.testsRun} | "
          f"OK: {result.testsRun - len(result.failures) - len(result.errors)} | "
          f"FAIL: {len(result.failures)} | "
          f"ERROR: {len(result.errors)}")
    print("=" * 70)

    sys.exit(0 if result.wasSuccessful() else 1)
