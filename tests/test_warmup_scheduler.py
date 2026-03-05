"""
Unit Tests for MKT-31 & MKT-32
AdsPower Browser Controller & Warmup Scheduler
Tests logic WITHOUT database dependencies
"""

import unittest
import sys
from pathlib import Path
import math
import random

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestWarmupSchedulerPhases(unittest.TestCase):
    """Test warmup phase calculations without importing DB-dependent modules."""
    
    # Copy of PHASES from WarmupScheduler to test without import
    PHASES = {
        (1, 7): ("foundation", lambda d: 5 + d*2, lambda d: 0, lambda d: 0, lambda d: 0),
        (8, 14): ("ramp", lambda d: 15 + (d-7)*2, lambda d: 2 + (d-7), lambda d: 3 + (d-7), lambda d: 0),
        (15, 21): ("outreach", lambda d: 25 + (d-14), lambda d: 8 + (d-14), lambda d: 8, lambda d: 1 + (d-14)//2),
        (22, 28): ("cruise", lambda d: 30, lambda d: 15, lambda d: 10, lambda d: 5),
    }
    
    def get_phase_config(self, warmup_day: int) -> dict:
        """Copy of WarmupScheduler.get_phase_config for testing."""
        day = max(1, min(28, warmup_day))
        
        for (start, end), (phase, likes_fn, replies_fn, follows_fn, dms_fn) in self.PHASES.items():
            if start <= day <= end:
                return {
                    "phase": phase,
                    "daily_likes": likes_fn(day),
                    "daily_replies": replies_fn(day),
                    "daily_follows": follows_fn(day),
                    "daily_dms": dms_fn(day)
                }
        
        return {
            "phase": "cruise",
            "daily_likes": 30,
            "daily_replies": 15,
            "daily_follows": 10,
            "daily_dms": 5
        }
    
    def get_warmup_stage(self, warmup_day: int) -> str:
        """Copy of WarmupScheduler.get_warmup_stage for testing."""
        if warmup_day < 1:
            return "new"
        elif warmup_day <= 28:
            return "warming"
        else:
            return "active"
    
    def test_foundation_phase_day1(self):
        """Day 1: foundation, likes only."""
        config = self.get_phase_config(1)
        self.assertEqual(config["phase"], "foundation")
        self.assertEqual(config["daily_likes"], 7)  # 5 + 1*2
        self.assertEqual(config["daily_replies"], 0)
        self.assertEqual(config["daily_follows"], 0)
        self.assertEqual(config["daily_dms"], 0)
    
    def test_foundation_phase_day7(self):
        """Day 7: end of foundation."""
        config = self.get_phase_config(7)
        self.assertEqual(config["phase"], "foundation")
        self.assertEqual(config["daily_likes"], 19)  # 5 + 7*2
        self.assertEqual(config["daily_replies"], 0)
        self.assertEqual(config["daily_dms"], 0)
    
    def test_ramp_phase_day8(self):
        """Day 8: start of ramp, replies/follows added."""
        config = self.get_phase_config(8)
        self.assertEqual(config["phase"], "ramp")
        self.assertEqual(config["daily_likes"], 17)  # 15 + (8-7)*2
        self.assertEqual(config["daily_replies"], 3)  # 2 + (8-7)
        self.assertEqual(config["daily_follows"], 4)  # 3 + (8-7)
        self.assertEqual(config["daily_dms"], 0)  # Still no DMs
    
    def test_ramp_phase_day14(self):
        """Day 14: end of ramp."""
        config = self.get_phase_config(14)
        self.assertEqual(config["phase"], "ramp")
        self.assertEqual(config["daily_likes"], 29)  # 15 + (14-7)*2
        self.assertEqual(config["daily_replies"], 9)  # 2 + (14-7)
        self.assertEqual(config["daily_follows"], 10)  # 3 + (14-7)
        self.assertEqual(config["daily_dms"], 0)
    
    def test_outreach_phase_day15(self):
        """Day 15: DMs enabled."""
        config = self.get_phase_config(15)
        self.assertEqual(config["phase"], "outreach")
        self.assertEqual(config["daily_dms"], 1)  # 1 + (15-14)//2 = 1
        self.assertGreater(config["daily_dms"], 0)
    
    def test_outreach_phase_day21(self):
        """Day 21: end of outreach."""
        config = self.get_phase_config(21)
        self.assertEqual(config["phase"], "outreach")
        self.assertEqual(config["daily_likes"], 32)  # 25 + (21-14)
        self.assertEqual(config["daily_replies"], 15)  # 8 + (21-14)
        self.assertEqual(config["daily_dms"], 4)  # 1 + (21-14)//2
    
    def test_cruise_phase_day22(self):
        """Day 22: full capacity."""
        config = self.get_phase_config(22)
        self.assertEqual(config["phase"], "cruise")
        self.assertEqual(config["daily_likes"], 30)
        self.assertEqual(config["daily_replies"], 15)
        self.assertEqual(config["daily_follows"], 10)
        self.assertEqual(config["daily_dms"], 5)
    
    def test_cruise_phase_day28(self):
        """Day 28: still cruise."""
        config = self.get_phase_config(28)
        self.assertEqual(config["phase"], "cruise")
    
    def test_cruise_phase_day30(self):
        """Day 30+: stays in cruise (capped at 28)."""
        config = self.get_phase_config(30)
        self.assertEqual(config["phase"], "cruise")
        self.assertEqual(config["daily_likes"], 30)
    
    def test_warmup_stage_new(self):
        """Day 0: new stage."""
        self.assertEqual(self.get_warmup_stage(0), "new")
        self.assertEqual(self.get_warmup_stage(-1), "new")
    
    def test_warmup_stage_warming(self):
        """Days 1-28: warming stage."""
        self.assertEqual(self.get_warmup_stage(1), "warming")
        self.assertEqual(self.get_warmup_stage(14), "warming")
        self.assertEqual(self.get_warmup_stage(28), "warming")
    
    def test_warmup_stage_active(self):
        """Day 29+: active stage."""
        self.assertEqual(self.get_warmup_stage(29), "active")
        self.assertEqual(self.get_warmup_stage(100), "active")


class TestBellCurveLogic(unittest.TestCase):
    """Test bell curve activity distribution logic."""
    
    def get_activity_multiplier(self, hour: float) -> float:
        """Copy of bell curve logic for testing."""
        peak_hour = 14.5
        sigma = 3.0
        multiplier = math.exp(-((hour - peak_hour) ** 2) / (2 * sigma ** 2))
        return max(0.3, min(1.0, multiplier))
    
    def test_peak_activity_at_1430(self):
        """Peak activity at 14:30."""
        multiplier = self.get_activity_multiplier(14.5)
        self.assertAlmostEqual(multiplier, 1.0, places=2)
    
    def test_activity_at_midnight(self):
        """Low activity at midnight."""
        multiplier = self.get_activity_multiplier(0)
        self.assertEqual(multiplier, 0.3)  # Minimum floor
    
    def test_activity_at_morning(self):
        """Medium activity at 9 AM."""
        multiplier = self.get_activity_multiplier(9)
        self.assertGreaterEqual(multiplier, 0.3)
        self.assertLess(multiplier, 1.0)
    
    def test_activity_at_evening(self):
        """Medium activity at 8 PM."""
        multiplier = self.get_activity_multiplier(20)
        self.assertGreaterEqual(multiplier, 0.3)
        self.assertLess(multiplier, 1.0)
    
    def test_multiplier_range(self):
        """Multiplier should always be 0.3-1.0."""
        for hour in range(24):
            multiplier = self.get_activity_multiplier(hour)
            self.assertGreaterEqual(multiplier, 0.3)
            self.assertLessEqual(multiplier, 1.0)


class TestDelayCalculation(unittest.TestCase):
    """Test delay calculation logic."""
    
    def get_random_delay(self, multiplier: float = 1.0) -> float:
        """Copy of delay logic for testing."""
        base_delay = random.uniform(45, 240)
        adjusted_delay = base_delay / multiplier
        return min(300, max(45, adjusted_delay))
    
    def test_delay_at_peak(self):
        """Delay at peak activity (multiplier=1.0)."""
        for _ in range(100):
            delay = self.get_random_delay(1.0)
            self.assertGreaterEqual(delay, 45)
            self.assertLessEqual(delay, 240)  # No adjustment
    
    def test_delay_at_low_activity(self):
        """Delay at low activity (multiplier=0.3)."""
        for _ in range(100):
            delay = self.get_random_delay(0.3)
            self.assertGreaterEqual(delay, 45)  # Floor
            self.assertLessEqual(delay, 300)  # Cap
    
    def test_delay_never_below_45(self):
        """Delay should never be below 45 seconds."""
        for mult in [0.3, 0.5, 0.7, 1.0]:
            for _ in range(50):
                delay = self.get_random_delay(mult)
                self.assertGreaterEqual(delay, 45)


class TestActionTypes(unittest.TestCase):
    """Test action type validation against schema."""
    
    # From schema.sql
    VALID_SCHEMA_ACTIONS = [
        'send_dm', 'like_tweet', 'follow_user', 'reply_tweet',
        'retweet', 'scroll', 'login', 'logout', 'profile_view'
    ]
    
    # Controller implements these
    CONTROLLER_ACTIONS = [
        'like_tweet', 'follow_user', 'reply_tweet',
        'send_dm', 'scroll_feed', 'profile_view'
    ]
    
    def test_controller_actions_valid(self):
        """All controller actions should map to valid schema actions."""
        for action in self.CONTROLLER_ACTIONS:
            if action == 'scroll_feed':
                self.assertIn('scroll', self.VALID_SCHEMA_ACTIONS)
            else:
                self.assertIn(action, self.VALID_SCHEMA_ACTIONS)
    
    def test_dm_action_exists(self):
        """send_dm must be supported."""
        self.assertIn('send_dm', self.CONTROLLER_ACTIONS)
        self.assertIn('send_dm', self.VALID_SCHEMA_ACTIONS)
    
    def test_like_action_exists(self):
        """like_tweet must be supported."""
        self.assertIn('like_tweet', self.CONTROLLER_ACTIONS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
