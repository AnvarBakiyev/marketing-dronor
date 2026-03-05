"""
M5 Browser Controller Module
GoLogin + Playwright automation for Twitter

Components:
- GoLoginBrowserController: Antidetect browser via GoLogin API (port 36912)
- WarmupScheduler: 4-phase warmup cycle management

Note: AdsPowerBrowserController kept for reference but not active.
"""

from .gologin_browser_controller import GoLoginBrowserController, GoLoginAPI, browser_controller
from .warmup_scheduler import WarmupScheduler

__all__ = ['GoLoginBrowserController', 'GoLoginAPI', 'browser_controller', 'WarmupScheduler']
