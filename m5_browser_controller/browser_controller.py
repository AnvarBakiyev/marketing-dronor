"""
Browser Controller for Twitter Automation

Chrome automation via Playwright for Twitter interactions.
LOCAL expert - requires target device with Chrome.

Actions:
    - open_twitter_page: Navigate to tweet URL
    - paste_reply_text: Enter text in reply field
    - open_dm: Open DM conversation with username
    - take_screenshot: Capture current page screenshot
    - close: Close browser

Usage:
    controller = BrowserController()
    controller.open_twitter_page("https://twitter.com/user/status/123")
    controller.paste_reply_text("Hello!")
    controller.take_screenshot("/path/to/screenshot.png")
    controller.close()
"""

from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page
from pathlib import Path
from typing import Optional, Dict, Any
import logging

logger = logging.getLogger(__name__)


class BrowserController:
    """Chrome browser automation for Twitter via Playwright."""
    
    def __init__(
        self,
        headless: bool = False,
        user_data_dir: Optional[str] = None,
        timeout_ms: int = 30000
    ):
        """
        Initialize browser controller.
        
        Args:
            headless: Run in headless mode (default False for operator visibility)
            user_data_dir: Chrome profile directory for persistent login
            timeout_ms: Default timeout for operations
        """
        self.headless = headless
        self.timeout_ms = timeout_ms
        
        if user_data_dir:
            self.user_data_dir = Path(user_data_dir)
        else:
            self.user_data_dir = Path.home() / ".browser_controller" / "chrome_profile"
        
        self.user_data_dir.mkdir(parents=True, exist_ok=True)
        
        self._playwright = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
    
    def _ensure_browser(self) -> Page:
        """Ensure browser is started and return page."""
        if self._page is not None:
            return self._page
        
        self._playwright = sync_playwright().start()
        
        self._context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.user_data_dir),
            headless=self.headless,
            viewport={"width": 1280, "height": 800},
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox"
            ]
        )
        
        if self._context.pages:
            self._page = self._context.pages[0]
        else:
            self._page = self._context.new_page()
        
        self._page.set_default_timeout(self.timeout_ms)
        logger.info("Browser started with persistent profile")
        
        return self._page
    
    def open_twitter_page(self, tweet_url: str) -> Dict[str, Any]:
        """
        Navigate to a tweet URL.
        
        Args:
            tweet_url: Full URL of the tweet
            
        Returns:
            dict with status, url, and page title
        """
        if not tweet_url:
            return {"status": "error", "message": "tweet_url is required"}
        
        try:
            page = self._ensure_browser()
            page.goto(tweet_url, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)
            
            return {
                "status": "success",
                "action": "open_twitter_page",
                "url": tweet_url,
                "title": page.title()
            }
        except Exception as e:
            logger.error(f"Failed to open tweet page: {e}")
            return {"status": "error", "message": str(e)}
    
    def paste_reply_text(self, text: str) -> Dict[str, Any]:
        """
        Enter text in reply field on current tweet page.
        
        Args:
            text: Reply text to enter
            
        Returns:
            dict with status and pasted text
        """
        if not text:
            return {"status": "error", "message": "text is required"}
        
        try:
            page = self._ensure_browser()
            
            # Click reply button if exists
            reply_button = page.locator('[data-testid="reply"]').first
            if reply_button.is_visible():
                reply_button.click()
                page.wait_for_timeout(1000)
            
            # Find reply text field
            reply_field = page.locator('[data-testid="tweetTextarea_0"]').first
            if not reply_field.is_visible():
                reply_field = page.locator('[role="textbox"][data-text="true"]').first
            
            if reply_field.is_visible():
                reply_field.click()
                reply_field.fill(text)
                
                return {
                    "status": "success",
                    "action": "paste_reply_text",
                    "text": text
                }
            else:
                return {"status": "error", "message": "Reply text field not found"}
                
        except Exception as e:
            logger.error(f"Failed to paste reply text: {e}")
            return {"status": "error", "message": str(e)}
    
    def open_dm(self, username: str) -> Dict[str, Any]:
        """
        Open DM conversation with a Twitter user.
        
        Args:
            username: Twitter username (with or without @)
            
        Returns:
            dict with status and username
        """
        if not username:
            return {"status": "error", "message": "username is required"}
        
        try:
            page = self._ensure_browser()
            clean_username = username.lstrip("@")
            
            # Navigate to user profile
            page.goto(f"https://twitter.com/{clean_username}", wait_until="domcontentloaded")
            page.wait_for_timeout(2000)
            
            # Click message button
            dm_button = page.locator('[data-testid="sendDMFromProfile"]').first
            if dm_button.is_visible():
                dm_button.click()
                page.wait_for_timeout(1500)
                
                return {
                    "status": "success",
                    "action": "open_dm",
                    "username": clean_username
                }
            else:
                # Fallback: go to messages directly
                page.goto("https://twitter.com/messages", wait_until="domcontentloaded")
                page.wait_for_timeout(1500)
                
                new_msg = page.locator('[data-testid="NewDM_Button"]').first
                if new_msg.is_visible():
                    new_msg.click()
                    page.wait_for_timeout(1000)
                    
                    search = page.locator('[data-testid="searchPeople"]').first
                    if search.is_visible():
                        search.fill(clean_username)
                        page.wait_for_timeout(1500)
                
                return {
                    "status": "success",
                    "action": "open_dm",
                    "username": clean_username,
                    "note": "Navigated via messages compose"
                }
                
        except Exception as e:
            logger.error(f"Failed to open DM: {e}")
            return {"status": "error", "message": str(e)}
    
    def take_screenshot(self, screenshot_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Capture screenshot of current page.
        
        Args:
            screenshot_path: Where to save screenshot (default: ~/Downloads/)
            
        Returns:
            dict with status and screenshot path
        """
        try:
            page = self._ensure_browser()
            
            if not screenshot_path:
                title = page.title()[:20].replace(" ", "_").replace("/", "_")
                screenshot_path = str(Path.home() / "Downloads" / f"screenshot_{title}.png")
            
            screenshot_path = str(Path(screenshot_path).expanduser())
            Path(screenshot_path).parent.mkdir(parents=True, exist_ok=True)
            
            page.screenshot(path=screenshot_path, full_page=False)
            
            return {
                "status": "success",
                "action": "take_screenshot",
                "screenshot_path": screenshot_path
            }
            
        except Exception as e:
            logger.error(f"Failed to take screenshot: {e}")
            return {"status": "error", "message": str(e)}
    
    def close(self) -> Dict[str, Any]:
        """Close browser and cleanup."""
        try:
            if self._context:
                self._context.close()
            if self._playwright:
                self._playwright.stop()
            
            self._page = None
            self._context = None
            self._playwright = None
            
            logger.info("Browser closed")
            return {"status": "success", "action": "close"}
            
        except Exception as e:
            logger.error(f"Error closing browser: {e}")
            return {"status": "error", "message": str(e)}
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


# === Standalone function for Dronor expert ===

def browser_controller(
    action: str = "",
    tweet_url: str = "",
    text: str = "",
    username: str = "",
    screenshot_path: str = "",
    timeout_ms: int = 30000
) -> dict:
    """
    Dronor expert wrapper for BrowserController.
    
    Args:
        action: One of 'open_twitter_page', 'paste_reply_text', 'open_dm', 'take_screenshot', 'close'
        tweet_url: URL of tweet (for open_twitter_page)
        text: Text to paste (for paste_reply_text)
        username: Twitter username without @ (for open_dm)
        screenshot_path: Path to save screenshot (for take_screenshot)
        timeout_ms: Timeout in milliseconds
    """
    if not action:
        return {
            "status": "error",
            "message": "Action required: open_twitter_page, paste_reply_text, open_dm, take_screenshot, close"
        }
    
    controller = BrowserController(headless=False, timeout_ms=timeout_ms)
    
    if action == "open_twitter_page":
        return controller.open_twitter_page(tweet_url)
    elif action == "paste_reply_text":
        return controller.paste_reply_text(text)
    elif action == "open_dm":
        return controller.open_dm(username)
    elif action == "take_screenshot":
        return controller.take_screenshot(screenshot_path)
    elif action == "close":
        return controller.close()
    else:
        return {"status": "error", "message": f"Unknown action: {action}"}


if __name__ == "__main__":
    # Test
    with BrowserController() as bc:
        result = bc.open_twitter_page("https://twitter.com/elonmusk")
        print(result)
        
        result = bc.take_screenshot()
        print(result)
