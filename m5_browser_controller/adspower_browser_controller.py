"""
AdsPower Browser Controller for Marketing Dronor
MKT-31 | Sprint 4: Real Infrastructure

Features:
- AdsPower Local API integration
- Playwright browser automation
- Bell curve activity distribution (peak 14:00-15:00)
- Random delays 45-240 seconds
- Activity logging to PostgreSQL
"""

import requests
import psycopg2
import psycopg2.extras
import time
import random
import math
from datetime import datetime
from playwright.sync_api import sync_playwright
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from infra.config import DB_CONFIG


class AdsPowerBrowserController:
    """Controller for AdsPower browser profiles with Playwright."""
    
    def __init__(self, adspower_api_url: str = "http://localhost:50325"):
        self.adspower_api_url = adspower_api_url
        self.db_config = DB_CONFIG
    
    # --- Bell curve delay calculation ---
    def get_activity_multiplier(self) -> float:
        """Returns 0.3-1.0 based on time of day (bell curve, peak at 14:30)."""
        hour = datetime.now().hour + datetime.now().minute / 60
        peak_hour = 14.5
        sigma = 3.0
        multiplier = math.exp(-((hour - peak_hour) ** 2) / (2 * sigma ** 2))
        return max(0.3, min(1.0, multiplier))
    
    def get_random_delay(self) -> float:
        """Random delay 45-240 seconds, adjusted by bell curve."""
        base_delay = random.uniform(45, 240)
        multiplier = self.get_activity_multiplier()
        adjusted_delay = base_delay / multiplier
        return min(300, max(45, adjusted_delay))
    
    # --- Database ---
    def get_db_connection(self):
        return psycopg2.connect(**self.db_config, cursor_factory=psycopg2.extras.RealDictCursor)
    
    def log_activity(self, profile_id: int, account_id: int, action_type: str,
                     status: str, target_username: str = None, target_tweet_id: str = None,
                     target_url: str = None, error_message: str = None,
                     duration_ms: int = None, msg_queue_id: int = None) -> int:
        """Insert activity into activity_log table."""
        conn = self.get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO activity_log 
                    (adspower_profile_id, account_id, action_type, status, 
                     target_username, target_tweet_id, target_url, 
                     error_message, duration_ms, message_queue_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (profile_id, account_id, action_type, status,
                      target_username, target_tweet_id, target_url,
                      error_message, duration_ms, msg_queue_id))
                activity_id = cur.fetchone()['id']
                conn.commit()
                return activity_id
        finally:
            conn.close()
    
    def get_profile_info(self, profile_id: int) -> dict:
        """Get adspower_profile with account_id."""
        conn = self.get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT ap.*, ta.id as twitter_account_id
                    FROM adspower_profiles ap
                    LEFT JOIN twitter_accounts ta ON ta.id = ap.account_id
                    WHERE ap.id = %s
                """, (profile_id,))
                return cur.fetchone()
        finally:
            conn.close()
    
    def update_profile_session(self, profile_id: int, started: bool = True):
        """Update last_started_at or last_stopped_at."""
        conn = self.get_db_connection()
        try:
            with conn.cursor() as cur:
                if started:
                    cur.execute("""
                        UPDATE adspower_profiles 
                        SET last_started_at = NOW(), total_sessions = total_sessions + 1
                        WHERE id = %s
                    """, (profile_id,))
                else:
                    cur.execute("""
                        UPDATE adspower_profiles SET last_stopped_at = NOW()
                        WHERE id = %s
                    """, (profile_id,))
                conn.commit()
        finally:
            conn.close()
    
    def update_warmup_progress(self, profile_id: int, action_type: str):
        """Update warmup_schedule counters after action."""
        action_map = {
            'like_tweet': 'likes_done',
            'reply_tweet': 'replies_done',
            'follow_user': 'follows_done',
            'send_dm': 'dms_done'
        }
        column = action_map.get(action_type)
        if not column:
            return
        
        conn = self.get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(f"""
                    UPDATE warmup_schedule 
                    SET {column} = {column} + 1
                    WHERE adspower_profile_id = %s AND schedule_date = CURRENT_DATE
                """, (profile_id,))
                conn.commit()
        finally:
            conn.close()
    
    # --- AdsPower API ---
    def start_browser(self, serial_number: str) -> dict:
        """Start AdsPower profile and get debug port."""
        resp = requests.get(
            f"{self.adspower_api_url}/api/v1/browser/start",
            params={"serial_number": serial_number}
        )
        data = resp.json()
        if data.get("code") != 0:
            raise Exception(f"AdsPower start failed: {data.get('msg')}")
        return data["data"]
    
    def stop_browser(self, serial_number: str) -> bool:
        """Stop AdsPower profile."""
        resp = requests.get(
            f"{self.adspower_api_url}/api/v1/browser/stop",
            params={"serial_number": serial_number}
        )
        return resp.json().get("code") == 0
    
    # --- Playwright actions ---
    @staticmethod
    def human_delay(min_sec=1, max_sec=3):
        """Small human-like delay between micro-actions."""
        time.sleep(random.uniform(min_sec, max_sec))
    
    def execute_action(self, page, action_type: str, **kwargs) -> dict:
        """Execute Twitter action with Playwright."""
        start_time = time.time()
        result = {"status": "success", "error": None}
        
        try:
            if action_type == "like_tweet":
                url = kwargs.get("url")
                page.goto(url, wait_until="networkidle")
                self.human_delay(2, 4)
                like_btn = page.locator('[data-testid="like"]').first
                if like_btn.is_visible():
                    like_btn.click()
                    self.human_delay(1, 2)
                else:
                    result["status"] = "skipped"
                    result["error"] = "Already liked or button not found"
            
            elif action_type == "follow_user":
                username = kwargs.get("username")
                page.goto(f"https://twitter.com/{username}", wait_until="networkidle")
                self.human_delay(2, 4)
                follow_btn = page.locator('[data-testid$="-follow"]').first
                if follow_btn.is_visible():
                    follow_btn.click()
                    self.human_delay(1, 2)
                else:
                    result["status"] = "skipped"
                    result["error"] = "Already following or button not found"
            
            elif action_type == "reply_tweet":
                url = kwargs.get("url")
                text = kwargs.get("text", "")
                page.goto(url, wait_until="networkidle")
                self.human_delay(2, 4)
                reply_btn = page.locator('[data-testid="reply"]').first
                reply_btn.click()
                self.human_delay(1, 2)
                text_input = page.locator('[data-testid="tweetTextarea_0"]').first
                text_input.fill(text)
                self.human_delay(1, 2)
                send_btn = page.locator('[data-testid="tweetButton"]').first
                send_btn.click()
                self.human_delay(2, 3)
            
            elif action_type == "send_dm":
                username = kwargs.get("username")
                text = kwargs.get("text", "")
                page.goto("https://twitter.com/messages/compose", wait_until="networkidle")
                self.human_delay(2, 3)
                search_input = page.locator('[data-testid="searchPeople"]').first
                search_input.fill(username)
                self.human_delay(2, 3)
                page.locator('[data-testid="typeaheadResult"]').first.click()
                self.human_delay(1, 2)
                page.locator('[data-testid="nextButton"]').click()
                self.human_delay(1, 2)
                msg_input = page.locator('[data-testid="dmComposerTextInput"]').first
                msg_input.fill(text)
                self.human_delay(1, 2)
                page.locator('[data-testid="dmComposerSendButton"]').click()
                self.human_delay(2, 3)
            
            elif action_type == "scroll_feed":
                page.goto("https://twitter.com/home", wait_until="networkidle")
                self.human_delay(3, 5)
                for _ in range(random.randint(5, 15)):
                    page.mouse.wheel(0, random.randint(300, 800))
                    self.human_delay(2, 5)
            
            elif action_type == "profile_view":
                username = kwargs.get("username")
                page.goto(f"https://twitter.com/{username}", wait_until="networkidle")
                self.human_delay(3, 6)
                for _ in range(random.randint(2, 5)):
                    page.mouse.wheel(0, random.randint(200, 500))
                    self.human_delay(1, 3)
        
        except Exception as e:
            result["status"] = "failed"
            result["error"] = str(e)
        
        result["duration_ms"] = int((time.time() - start_time) * 1000)
        return result
    
    def run_action(self, adspower_profile_id: int, action_type: str,
                   target_url: str = None, target_username: str = None,
                   target_tweet_id: str = None, message_text: str = None,
                   message_queue_id: int = None) -> dict:
        """Main entry point: run an action on a profile."""
        
        profile = self.get_profile_info(adspower_profile_id)
        if not profile:
            return {"status": "error", "message": f"Profile {adspower_profile_id} not found"}
        
        serial_number = profile["serial_number"]
        account_id = profile.get("twitter_account_id")
        
        try:
            # Start browser
            browser_data = self.start_browser(serial_number)
            ws_endpoint = browser_data.get("ws", {}).get("puppeteer")
            
            if not ws_endpoint:
                raise Exception("No WebSocket endpoint returned")
            
            self.update_profile_session(adspower_profile_id, started=True)
            
            # Apply bell curve delay
            delay = self.get_random_delay()
            time.sleep(delay)
            
            # Connect Playwright and execute
            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(ws_endpoint)
                context = browser.contexts[0]
                page = context.pages[0] if context.pages else context.new_page()
                
                result = self.execute_action(
                    page, action_type,
                    url=target_url, username=target_username, text=message_text
                )
                
                browser.close()
            
            # Log activity
            self.log_activity(
                adspower_profile_id, account_id, action_type, result["status"],
                target_username=target_username, target_tweet_id=target_tweet_id,
                target_url=target_url, error_message=result.get("error"),
                duration_ms=result.get("duration_ms"), msg_queue_id=message_queue_id
            )
            
            # Update warmup progress
            if result["status"] == "success":
                self.update_warmup_progress(adspower_profile_id, action_type)
            
            # Stop browser
            self.stop_browser(serial_number)
            self.update_profile_session(adspower_profile_id, started=False)
            
            return {
                "status": result["status"],
                "action": action_type,
                "duration_ms": result.get("duration_ms"),
                "delay_applied": delay,
                "error": result.get("error")
            }
        
        except Exception as e:
            self.log_activity(
                adspower_profile_id, account_id, action_type, "failed",
                target_url=target_url, error_message=str(e)
            )
            return {"status": "error", "message": str(e)}


if __name__ == "__main__":
    # Example usage
    controller = AdsPowerBrowserController()
    # result = controller.run_action(1, "scroll_feed")
    # print(result)
