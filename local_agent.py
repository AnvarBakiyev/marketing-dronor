"""
local_agent.py — Human-in-the-Loop send agent.

Runs on operator's machine. Polls Railway PostgreSQL for queued send_jobs,
opens AdsPower browser profile, navigates to Twitter, inserts message text,
then WAITS for operator to press Enter/Send manually in the browser.

Usage:
    python local_agent.py --operator-id 1 --machine-id mypc
    python local_agent.py --operator-id 1 --machine-id mypc --adspower-url http://localhost:50325
"""

import argparse
import time
import socket
import sys
import traceback
from datetime import datetime
from pathlib import Path

import psycopg2
import psycopg2.extras
import requests
from playwright.sync_api import sync_playwright

# ── Config ──────────────────────────────────────────────────────────────────
import os
from pathlib import Path as _Path

# Load .env if present (local development)
_env_file = _Path(__file__).parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"' '))

DB_URL       = os.environ["DATABASE_URL"]  # Required — set in .env or environment
ADSPOWER_URL = os.environ.get("ADSPOWER_URL", "http://localhost:50325")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "2"))
TWITTER_DM_URL    = "https://twitter.com/messages/compose?recipient_id={uid}"
TWITTER_REPLY_URL = "https://twitter.com/i/web/status/{tweet_id}"

# ── DB helpers ───────────────────────────────────────────────────────────────
def get_conn():
    return psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)

def claim_job(machine_id: str, operator_id: int):
    """Atomically claim one queued job. Returns job dict or None."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE send_jobs
                SET status='claimed', claimed_by=%s, claimed_at=NOW(), operator_id=%s
                WHERE id = (
                    SELECT id FROM send_jobs
                    WHERE status='queued'
                    ORDER BY created_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                RETURNING id, msg_queue_id
            """, (machine_id, operator_id))
            row = cur.fetchone()
        conn.commit()
    return dict(row) if row else None

def get_message(msg_queue_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT mq.id, mq.message_text, mq.send_type,
                       mq.target_tweet_id,
                       tp.username   AS target_username,
                       tp.twitter_id AS target_twitter_id,
                       ta.username   AS sender_username,
                       ta.adspower_profile_id
                FROM message_queue mq
                JOIN twitter_profiles tp ON tp.id = mq.profile_id
                JOIN twitter_accounts ta ON ta.id = mq.account_id
                WHERE mq.id = %s
            """, (msg_queue_id,))
            row = cur.fetchone()
    return dict(row) if row else None

def update_job(job_id: int, status: str, error: str = None):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE send_jobs
                SET status=%s, completed_at=NOW(), error_msg=%s
                WHERE id=%s
            """, (status, error, job_id))
        conn.commit()

def mark_browser_ready(job_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE send_jobs SET status='browser_ready', browser_ready_at=NOW()
                WHERE id=%s
            """, (job_id,))
        conn.commit()

def mark_message_sent(msg_queue_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE message_queue SET status='sent', sent_at=NOW()
                WHERE id=%s
            """, (msg_queue_id,))
        conn.commit()

# ── Job recovery ─────────────────────────────────────────────────────────────────
def recover_stale_jobs(machine_id: str, timeout_minutes: int = 10):
    """Reset jobs stuck in 'claimed' by this machine for too long (agent crash recovery)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE send_jobs
                SET status='queued', claimed_by=NULL, claimed_at=NULL, operator_id=NULL
                WHERE status='claimed'
                  AND claimed_by=%s
                  AND claimed_at < NOW() - INTERVAL '%s minutes'
            """, (machine_id, timeout_minutes))
            count = cur.rowcount
        conn.commit()
    if count:
        print(f"[recovery] Reset {count} stale jobs back to queued", flush=True)

# ── AdsPower ─────────────────────────────────────────────────────────────────
def open_adspower_profile(profile_id: str) -> str:
    """Start AdsPower profile, return CDP websocket URL for Playwright."""
    r = requests.get(
        f"{ADSPOWER_URL}/api/v1/browser/start",
        params={"user_id": profile_id},
        timeout=30
    )
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"AdsPower error: {data.get('msg')}")
    ws_url = data["data"]["ws"]["puppeteer"]
    return ws_url

def close_adspower_profile(profile_id: str):
    try:
        requests.get(
            f"{ADSPOWER_URL}/api/v1/browser/stop",
            params={"user_id": profile_id},
            timeout=10
        )
    except:
        pass

# ── Twitter automation ────────────────────────────────────────────────────────
def send_dm(page, target_username: str, target_twitter_id: str, message_text: str):
    """Navigate to DM compose, insert text, wait for operator to send."""
    # Build URL — prefer twitter_id if available
    if target_twitter_id:
        url = f"https://twitter.com/messages/compose?recipient_id={target_twitter_id}"
    else:
        # fallback: go to profile, click Message button
        url = f"https://twitter.com/{target_username}"

    print(f"  → Navigating to {url}")
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(3000)

    # If went to profile, click Message button
    if target_twitter_id is None:
        try:
            msg_btn = page.locator('[data-testid="sendDMFromProfile"]')
            msg_btn.wait_for(timeout=8000)
            msg_btn.click()
            page.wait_for_timeout(2000)
        except:
            raise RuntimeError("Could not find Message button on profile page")

    # Find DM input box
    dm_input = page.locator('[data-testid="dmComposerTextInput"]')
    dm_input.wait_for(timeout=10000)
    dm_input.click()

    # Type message naturally (human-like)
    page.keyboard.type(message_text, delay=30)
    print(f"  ✓ Text inserted. Waiting for operator to press Send in Twitter...")

def send_reply(page, tweet_id: str, message_text: str):
    """Navigate to tweet, open reply, insert text, wait for operator."""
    url = f"https://twitter.com/i/web/status/{tweet_id}"
    print(f"  → Navigating to tweet {url}")
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(3000)

    # Click Reply button
    reply_btn = page.locator('[data-testid="reply"]').first
    reply_btn.wait_for(timeout=8000)
    reply_btn.click()
    page.wait_for_timeout(1500)

    # Find reply input
    reply_input = page.locator('[data-testid="tweetTextarea_0"]')
    reply_input.wait_for(timeout=8000)
    reply_input.click()
    page.keyboard.type(message_text, delay=30)
    print(f"  ✓ Reply text inserted. Waiting for operator to press Send in Twitter...")

# ── Wait for operator send ────────────────────────────────────────────────────
def wait_for_operator_send(page, send_type: str) -> bool:
    """
    Wait indefinitely until operator sends the message.
    Detects send by watching URL change or success indicator.
    Returns True if sent, False if browser was closed.
    """
    print("  ⏳ Browser is open. Operator should press Send in Twitter.")
    print("     (Press Ctrl+C here to skip this message)")

    if send_type == 'dm':
        # After DM send, Twitter redirects to conversation page
        initial_url = page.url
        while True:
            try:
                time.sleep(1)
                current_url = page.url
                # DM sent → URL changes to /messages/{conversation_id}
                if '/messages/' in current_url and current_url != initial_url:
                    print("  ✅ DM sent! URL changed to conversation.")
                    return True
                # Check for sent indicator
                sent_indicators = page.locator('[data-testid="dmComposerTextInput"]')
                if sent_indicators.count() > 0:
                    # Input cleared = message was sent
                    content = sent_indicators.inner_text()
                    if content.strip() == '':
                        # Wait a moment to confirm it's really cleared
                        time.sleep(1)
                        content2 = sent_indicators.inner_text()
                        if content2.strip() == '':
                            print("  ✅ DM input cleared — message sent.")
                            return True
            except KeyboardInterrupt:
                print("  ⏭️  Skipped by operator.")
                return False
            except Exception:
                # Browser might have been closed
                return False
    else:  # reply
        initial_url = page.url
        while True:
            try:
                time.sleep(1)
                # After reply, modal closes and URL stays on tweet
                # Check if reply textarea disappeared
                reply_inputs = page.locator('[data-testid="tweetTextarea_0"]')
                if reply_inputs.count() == 0:
                    print("  ✅ Reply sent — textarea closed.")
                    return True
            except KeyboardInterrupt:
                print("  ⏭️  Skipped by operator.")
                return False
            except Exception:
                return False

# ── Main loop ─────────────────────────────────────────────────────────────────
def process_job(job: dict, machine_id: str):
    job_id = job['id']
    msg = get_message(job['msg_queue_id'])
    if not msg:
        update_job(job_id, 'failed', 'Message not found in DB')
        return

    profile_id = msg.get('adspower_profile_id')
    if not profile_id:
        update_job(job_id, 'failed', 'No AdsPower profile_id on twitter account')
        print(f"  ✗ Account {msg['sender_username']} has no AdsPower profile ID")
        return

    print(f"\n{'='*60}")
    print(f"Job #{job_id} | {msg['send_type'].upper()} → @{msg['target_username']}")
    print(f"Account: @{msg['sender_username']} (AdsPower: {profile_id})")
    print(f"Message preview: {msg['message_text'][:80]}...")
    print(f"{'='*60}")

    ws_url = None
    try:
        print("  → Opening AdsPower profile...")
        ws_url = open_adspower_profile(profile_id)
        print(f"  ✓ Browser started: {ws_url[:60]}...")

        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(ws_url)
            context = browser.contexts[0]
            page = context.pages[0] if context.pages else context.new_page()

            # Navigate and insert text
            if msg['send_type'] == 'dm':
                send_dm(page, msg['target_username'],
                        msg.get('target_twitter_id'), msg['message_text'])
            else:
                if not msg.get('target_tweet_id'):
                    raise RuntimeError('send_type=reply but no target_tweet_id')
                send_reply(page, msg['target_tweet_id'], msg['message_text'])

            # Signal Railway that browser is ready
            mark_browser_ready(job_id)

            # Wait for operator
            sent = wait_for_operator_send(page, msg['send_type'])

            if sent:
                mark_message_sent(job['msg_queue_id'])
                update_job(job_id, 'sent')
                print(f"  ✓ Job #{job_id} completed — marked as sent")
            else:
                update_job(job_id, 'skipped')
                print(f"  → Job #{job_id} skipped")

    except Exception as e:
        err = str(e)[:300]
        update_job(job_id, 'failed', err)
        print(f"  ✗ Job #{job_id} failed: {err}")
        traceback.print_exc()
    finally:
        if ws_url and profile_id:
            close_adspower_profile(profile_id)


def main():
    parser = argparse.ArgumentParser(description='Marketing Dronor — Local Send Agent')
    parser.add_argument('--operator-id', type=int, required=True, help='Operator ID from DB')
    parser.add_argument('--machine-id', type=str, default=socket.gethostname(),
                        help='Unique machine identifier (default: hostname)')
    parser.add_argument('--adspower-url', type=str, default=ADSPOWER_URL)
    parser.add_argument('--max-parallel', type=int, default=3,
                        help='Max parallel browser sessions')
    args = parser.parse_args()

    global ADSPOWER_URL
    ADSPOWER_URL = args.adspower_url

    print(f"""
╔══════════════════════════════════════════════╗
║   Marketing Dronor — Local Send Agent        ║
╠══════════════════════════════════════════════╣
║  Operator ID : {args.operator_id:<28} ║
║  Machine     : {args.machine_id:<28} ║
║  AdsPower    : {ADSPOWER_URL:<28} ║
║  Max parallel: {args.max_parallel:<28} ║
╚══════════════════════════════════════════════╝

Waiting for queued send jobs... (Ctrl+C to stop)
""")

    import threading
    active = []
    recovery_done = False

    while True:
        try:
            # One-time stale job recovery on startup
            if not recovery_done:
                recover_stale_jobs(args.machine_id)
                recovery_done = True

            # Clean up finished threads
            active = [t for t in active if t.is_alive()]

            if len(active) < args.max_parallel:
                job = claim_job(args.machine_id, args.operator_id)
                if job:
                    print(f"\n→ Claimed job #{job['id']} for msg #{job['msg_queue_id']}")
                    t = threading.Thread(
                        target=process_job,
                        args=(job, args.machine_id),
                        daemon=True
                    )
                    t.start()
                    active.append(t)
                else:
                    # No jobs — quiet wait
                    pass
            else:
                print(f"  (max parallel reached: {len(active)} active)", end='\r')

            time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            print("\n\nStopping agent. Waiting for active jobs to finish...")
            for t in active:
                t.join(timeout=10)
            print("Agent stopped.")
            sys.exit(0)
        except Exception as e:
            print(f"Agent error: {e}")
            time.sleep(5)


if __name__ == '__main__':
    main()
