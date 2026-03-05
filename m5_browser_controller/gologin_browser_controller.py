"""
GoLogin Browser Controller — replaces AdsPower in M5 Outreach Sender.

Flow:
    1. GoLogin desktop app running on localhost:36912
    2. start_profile(profile_id) → CDP WebSocket URL
    3. Playwright connects via connect_over_cdp(wsUrl)
    4. All Twitter actions run inside antidetect context
    5. stop_profile() closes browser

Same public interface as AdsPowerBrowserController so cc_backend.py
needs zero changes.
"""

import logging
import time
from typing import Optional, Dict, Any

import requests
from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

GOLOGIN_API = "http://localhost:36912"


class GoLoginAPI:
    """Thin wrapper around GoLogin local REST API."""

    def __init__(self, base_url: str = GOLOGIN_API):
        self.base = base_url
        self.s = requests.Session()
        self.s.headers["Content-Type"] = "application/json"

    def _get(self, path):
        r = self.s.get(f"{self.base}{path}", timeout=15)
        r.raise_for_status()
        return r.json()

    def _post(self, path, body=None):
        r = self.s.post(f"{self.base}{path}", json=body or {}, timeout=30)
        r.raise_for_status()
        return r.json()

    def _delete(self, path):
        r = self.s.delete(f"{self.base}{path}", timeout=15)
        r.raise_for_status()
        return r.json() if r.content else {}

    def is_running(self) -> bool:
        try:
            self._get("/browser/v2")
            return True
        except Exception:
            return False

    def create_profile(self, name: str, proxy: Optional[Dict] = None) -> dict:
        body = {
            "name": name,
            "os": "win",
            "navigator": {
                "language": "en-US,en;q=0.9",
                "userAgent": "auto",
                "resolution": "1920x1080",
                "platform": "Win32",
            },
            "webGL": {"mode": "noise"},
            "canvas": {"mode": "noise"},
            "fonts": {"enableMasking": True},
            "timezone": {"enabled": True, "fillBasedOnIp": True},
        }
        if proxy:
            body["proxy"] = proxy
        return self._post("/browser/v2", body)

    def start_profile(self, profile_id: str) -> str:
        """Start profile and return CDP WebSocket URL."""
        data = self._get(f"/browser/{profile_id}/web-driver-url")
        ws = data.get("url") or data.get("wsUrl") or data.get("ws")
        if not ws:
            raise RuntimeError(f"GoLogin returned no wsUrl: {data}")
        return ws

    def stop_profile(self, profile_id: str):
        try:
            self._get(f"/browser/{profile_id}/stop")
        except Exception as e:
            logger.warning(f"stop_profile: {e}")

    def delete_profile(self, profile_id: str):
        return self._delete(f"/browser/{profile_id}")


class GoLoginBrowserController:
    """
    Twitter automation via GoLogin antidetect browser.
    Drop-in replacement for AdsPowerBrowserController.
    """

    def __init__(
        self,
        profile_id: Optional[str] = None,
        proxy: Optional[Dict] = None,
        timeout_ms: int = 30000,
    ):
        self.profile_id = profile_id
        self.proxy = proxy
        self.timeout_ms = timeout_ms
        self._api = GoLoginAPI()
        self._playwright = None
        self._browser = None
        self._page = None
        self._auto_created_id = None

    def _ensure_page(self):
        if self._page:
            return self._page

        if not self._api.is_running():
            raise RuntimeError(
                "GoLogin is not running. Start the GoLogin desktop app first."
            )

        pid = self.profile_id
        if not pid:
            name = f"mkt_{int(time.time())}"
            data = self._api.create_profile(name=name, proxy=self.proxy)
            pid = data.get("id") or data.get("profile_id")
            self._auto_created_id = pid
            logger.info(f"GoLogin: created profile {pid}")

        ws_url = self._api.start_profile(pid)
        self.profile_id = pid
        logger.info(f"GoLogin: profile {pid} → {ws_url}")

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.connect_over_cdp(ws_url)
        ctx = self._browser.contexts[0] if self._browser.contexts else self._browser.new_context()
        self._page = ctx.pages[0] if ctx.pages else ctx.new_page()
        self._page.set_default_timeout(self.timeout_ms)
        return self._page

    def close(self):
        try:
            if self._browser:
                self._browser.close()
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
        finally:
            self._browser = None
            self._playwright = None
            self._page = None
        if self.profile_id:
            self._api.stop_profile(self.profile_id)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    # ─── Twitter actions ───

    def open_twitter_page(self, tweet_url: str) -> dict:
        page = self._ensure_page()
        page.goto(tweet_url, wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        return {"status": "success", "url": tweet_url, "title": page.title()}

    def paste_reply_text(self, text: str) -> dict:
        page = self._ensure_page()
        selectors = [
            "[data-testid='tweetTextarea_0']",
            "[data-testid='dmComposerTextInput']",
            "div[contenteditable='true'][role='textbox']",
        ]
        for sel in selectors:
            try:
                page.wait_for_selector(sel, timeout=5000)
                page.click(sel)
                page.type(sel, text, delay=60)
                return {"status": "success", "typed": len(text)}
            except Exception:
                continue
        return {"status": "error", "message": "Text input not found"}

    def open_dm(self, username: str) -> dict:
        username = username.lstrip("@")
        page = self._ensure_page()
        page.goto(
            f"https://twitter.com/messages/compose?recipient_id={username}",
            wait_until="domcontentloaded",
        )
        page.wait_for_timeout(2000)
        return {"status": "success", "dm_to": username}

    def take_screenshot(self, path: str = "") -> dict:
        page = self._ensure_page()
        save_path = path or f"/tmp/gl_{int(time.time())}.png"
        page.screenshot(path=save_path)
        return {"status": "success", "path": save_path}

    def send_dm(self, username: str, message: str) -> dict:
        """Open DM, type message and send."""
        result = self.open_dm(username)
        if result["status"] != "success":
            return result
        page = self._ensure_page()
        result2 = self.paste_reply_text(message)
        if result2["status"] != "success":
            return result2
        # Hit Enter to send
        page.keyboard.press("Enter")
        page.wait_for_timeout(1000)
        return {"status": "success", "sent_to": username, "chars": len(message)}


# ─── Entry point used by cc_backend.py ───

def browser_controller(
    action: str = "",
    tweet_url: str = "",
    text: str = "",
    username: str = "",
    screenshot_path: str = "",
    timeout_ms: int = 30000,
    profile_id: str = "",
    proxy_host: str = "",
    proxy_port: int = 0,
    proxy_user: str = "",
    proxy_pass: str = "",
    proxy_mode: str = "http",
) -> dict:
    """
    Same signature as the original browser_controller() in browser_controller.py.
    Extended with profile_id + proxy_* params for GoLogin.
    """
    if not action:
        return {
            "status": "error",
            "message": "action required: open_twitter_page | paste_reply_text | open_dm | send_dm | take_screenshot | close",
        }

    proxy = None
    if proxy_host and proxy_port:
        proxy = {
            "mode": proxy_mode,
            "host": proxy_host,
            "port": proxy_port,
            "username": proxy_user,
            "password": proxy_pass,
        }

    ctrl = GoLoginBrowserController(
        profile_id=profile_id or None,
        proxy=proxy,
        timeout_ms=timeout_ms,
    )

    try:
        if action == "open_twitter_page":
            return ctrl.open_twitter_page(tweet_url)
        elif action == "paste_reply_text":
            return ctrl.paste_reply_text(text)
        elif action == "open_dm":
            return ctrl.open_dm(username)
        elif action == "send_dm":
            return ctrl.send_dm(username, text)
        elif action == "take_screenshot":
            return ctrl.take_screenshot(screenshot_path)
        elif action == "close":
            ctrl.close()
            return {"status": "success", "message": "closed"}
        else:
            return {"status": "error", "message": f"Unknown action: {action}"}
    except Exception as e:
        logger.error(f"browser_controller error: {e}")
        return {"status": "error", "message": str(e)}
    finally:
        ctrl.close()


if __name__ == "__main__":
    api = GoLoginAPI()
    print("GoLogin running:", api.is_running())
