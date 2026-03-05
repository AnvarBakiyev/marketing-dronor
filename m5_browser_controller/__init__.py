"""
M5 Browser Controller Module
AdsPower + Playwright automation for Twitter

Components:
- AdsPowerBrowserController: Browser automation with bell curve delays
- WarmupScheduler: 4-phase warmup cycle management
"""

from .adspower_browser_controller import AdsPowerBrowserController
from .warmup_scheduler import WarmupScheduler

__all__ = ['AdsPowerBrowserController', 'WarmupScheduler']
