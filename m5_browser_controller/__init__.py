"""
M5 Browser Controller Module

Chrome automation for Twitter via Playwright.
LOCAL expert - requires target device.
"""

from .browser_controller import (
    BrowserController,
    browser_controller
)

__all__ = [
    "BrowserController",
    "browser_controller"
]
