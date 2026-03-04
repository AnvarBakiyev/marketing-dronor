"""Rate Limiter — MKT-19

Track and enforce rate limits for Twitter account actions.

Limits per account state:
    WARMING: 5 likes/hr, 3 replies/hr, 2 follows/hr
    ACTIVE: 20 likes/hr, 10 replies/hr, 5 follows/hr
    COOLING: 2 likes/hr, 1 reply/hr, 0 follows/hr

Usage:
    from rate_limiter import RateLimiter
    
    limiter = RateLimiter(db_connection_string)
    limiter.init_schema()  # Create tables if not exist
    
    # Check if action is allowed
    result = limiter.can_act(account_id, "like")
    if result["can_act"]:
        # perform action
        limiter.record_action(account_id, "like")
"""

import psycopg2
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
from enum import Enum


class ActionType(Enum):
    LIKE = "like"
    REPLY = "reply"
    FOLLOW = "follow"
    RETWEET = "retweet"
    DM = "dm"
    QUOTE = "quote"


# Rate limits per hour by account state
RATE_LIMITS = {
    "WARMING": {
        ActionType.LIKE: {"per_hour": 5, "per_day": 50},
        ActionType.REPLY: {"per_hour": 3, "per_day": 30},
        ActionType.FOLLOW: {"per_hour": 2, "per_day": 20},
        ActionType.RETWEET: {"per_hour": 3, "per_day": 25},
        ActionType.DM: {"per_hour": 2, "per_day": 15},
        ActionType.QUOTE: {"per_hour": 2, "per_day": 15},
    },
    "ACTIVE": {
        ActionType.LIKE: {"per_hour": 20, "per_day": 200},
        ActionType.REPLY: {"per_hour": 10, "per_day": 100},
        ActionType.FOLLOW: {"per_hour": 5, "per_day": 50},
        ActionType.RETWEET: {"per_hour": 10, "per_day": 80},
        ActionType.DM: {"per_hour": 8, "per_day": 60},
        ActionType.QUOTE: {"per_hour": 5, "per_day": 40},
    },
    "COOLING": {
        ActionType.LIKE: {"per_hour": 2, "per_day": 10},
        ActionType.REPLY: {"per_hour": 1, "per_day": 5},
        ActionType.FOLLOW: {"per_hour": 0, "per_day": 0},
        ActionType.RETWEET: {"per_hour": 1, "per_day": 5},
        ActionType.DM: {"per_hour": 1, "per_day": 5},
        ActionType.QUOTE: {"per_hour": 0, "per_day": 0},
    },
    "NEW": {
        ActionType.LIKE: {"per_hour": 0, "per_day": 0},
        ActionType.REPLY: {"per_hour": 0, "per_day": 0},
        ActionType.FOLLOW: {"per_hour": 0, "per_day": 0},
        ActionType.RETWEET: {"per_hour": 0, "per_day": 0},
        ActionType.DM: {"per_hour": 0, "per_day": 0},
        ActionType.QUOTE: {"per_hour": 0, "per_day": 0},
    },
    "SUSPENDED": {
        ActionType.LIKE: {"per_hour": 0, "per_day": 0},
        ActionType.REPLY: {"per_hour": 0, "per_day": 0},
        ActionType.FOLLOW: {"per_hour": 0, "per_day": 0},
        ActionType.RETWEET: {"per_hour": 0, "per_day": 0},
        ActionType.DM: {"per_hour": 0, "per_day": 0},
        ActionType.QUOTE: {"per_hour": 0, "per_day": 0},
    },
}


class RateLimiter:
    """Manages rate limiting for Twitter account actions."""
    
    def __init__(self, connection_string: str):
        """Initialize with PostgreSQL connection string."""
        self.connection_string = connection_string
        self._conn = None
    
    @property
    def conn(self):
        """Lazy connection initialization."""
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(self.connection_string)
        return self._conn
    
    def init_schema(self) -> Dict[str, Any]:
        """Create database tables if not exist."""
        with self.conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS account_actions (
                    id SERIAL PRIMARY KEY,
                    account_id INTEGER NOT NULL,
                    action_type VARCHAR(20) NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    metadata JSONB DEFAULT '{}'
                );
                
                CREATE INDEX IF NOT EXISTS idx_account_actions_account_time 
                    ON account_actions(account_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_account_actions_type 
                    ON account_actions(action_type);
            """)
            
            self.conn.commit()
        
        return {"status": "success", "message": "Rate limiter schema initialized"}
    
    def _get_account_state(self, account_id: int) -> Optional[str]:
        """Get current state of an account."""
        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT state FROM twitter_accounts WHERE id = %s
            """, (account_id,))
            
            row = cur.fetchone()
            return row[0] if row else None
    
    def _count_actions(self, account_id: int, action_type: str, since: datetime) -> int:
        """Count actions since given time."""
        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) FROM account_actions
                WHERE account_id = %s AND action_type = %s AND created_at >= %s
            """, (account_id, action_type, since))
            
            return cur.fetchone()[0]
    
    def can_act(
        self, 
        account_id: int, 
        action_type: str
    ) -> Dict[str, Any]:
        """Check if account can perform action within rate limits."""
        # Validate action type
        try:
            action = ActionType(action_type)
        except ValueError:
            return {
                "can_act": False,
                "reason": f"Invalid action type: {action_type}",
                "valid_actions": [a.value for a in ActionType]
            }
        
        # Get account state
        state = self._get_account_state(account_id)
        if not state:
            return {
                "can_act": False,
                "reason": f"Account {account_id} not found"
            }
        
        # Get limits for this state
        if state not in RATE_LIMITS:
            return {
                "can_act": False,
                "reason": f"Unknown state: {state}"
            }
        
        limits = RATE_LIMITS[state].get(action)
        if not limits:
            return {
                "can_act": False,
                "reason": f"No limits defined for {action_type} in state {state}"
            }
        
        # Check hourly limit
        now = datetime.utcnow()
        hour_ago = now - timedelta(hours=1)
        hourly_count = self._count_actions(account_id, action_type, hour_ago)
        
        if hourly_count >= limits["per_hour"]:
            return {
                "can_act": False,
                "reason": "Hourly limit reached",
                "current_hourly": hourly_count,
                "limit_hourly": limits["per_hour"],
                "reset_in_minutes": 60,
                "state": state
            }
        
        # Check daily limit
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        daily_count = self._count_actions(account_id, action_type, day_start)
        
        if daily_count >= limits["per_day"]:
            next_day = day_start + timedelta(days=1)
            reset_in = (next_day - now).total_seconds() / 60
            return {
                "can_act": False,
                "reason": "Daily limit reached",
                "current_daily": daily_count,
                "limit_daily": limits["per_day"],
                "reset_in_minutes": int(reset_in),
                "state": state
            }
        
        # Action is allowed
        return {
            "can_act": True,
            "account_id": account_id,
            "action_type": action_type,
            "state": state,
            "hourly": {
                "current": hourly_count,
                "limit": limits["per_hour"],
                "remaining": limits["per_hour"] - hourly_count
            },
            "daily": {
                "current": daily_count,
                "limit": limits["per_day"],
                "remaining": limits["per_day"] - daily_count
            }
        }
    
    def record_action(
        self, 
        account_id: int, 
        action_type: str,
        metadata: dict = None
    ) -> Dict[str, Any]:
        """Record an action for rate limiting tracking."""
        # Validate action type
        try:
            ActionType(action_type)
        except ValueError:
            return {
                "status": "error",
                "message": f"Invalid action type: {action_type}"
            }
        
        with self.conn.cursor() as cur:
            import psycopg2.extras
            cur.execute("""
                INSERT INTO account_actions (account_id, action_type, metadata)
                VALUES (%s, %s, %s)
                RETURNING id, created_at
            """, (account_id, action_type, psycopg2.extras.Json(metadata or {})))
            
            result = cur.fetchone()
            self.conn.commit()
            
            return {
                "status": "success",
                "action_id": result[0],
                "account_id": account_id,
                "action_type": action_type,
                "recorded_at": result[1].isoformat()
            }
    
    def get_account_stats(
        self, 
        account_id: int
    ) -> Dict[str, Any]:
        """Get current rate limit stats for an account."""
        state = self._get_account_state(account_id)
        if not state:
            return {"status": "error", "message": f"Account {account_id} not found"}
        
        now = datetime.utcnow()
        hour_ago = now - timedelta(hours=1)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        
        stats = {
            "account_id": account_id,
            "state": state,
            "actions": {}
        }
        
        limits = RATE_LIMITS.get(state, {})
        
        for action in ActionType:
            action_limits = limits.get(action, {"per_hour": 0, "per_day": 0})
            hourly_count = self._count_actions(account_id, action.value, hour_ago)
            daily_count = self._count_actions(account_id, action.value, day_start)
            
            stats["actions"][action.value] = {
                "hourly": {
                    "used": hourly_count,
                    "limit": action_limits["per_hour"],
                    "remaining": max(0, action_limits["per_hour"] - hourly_count)
                },
                "daily": {
                    "used": daily_count,
                    "limit": action_limits["per_day"],
                    "remaining": max(0, action_limits["per_day"] - daily_count)
                }
            }
        
        return stats
    
    def get_available_actions(
        self, 
        account_id: int
    ) -> Dict[str, Any]:
        """Get list of actions the account can currently perform."""
        available = []
        blocked = []
        
        for action in ActionType:
            result = self.can_act(account_id, action.value)
            if result.get("can_act"):
                available.append({
                    "action": action.value,
                    "remaining_hourly": result["hourly"]["remaining"],
                    "remaining_daily": result["daily"]["remaining"]
                })
            else:
                blocked.append({
                    "action": action.value,
                    "reason": result.get("reason", "Unknown")
                })
        
        return {
            "account_id": account_id,
            "available_actions": available,
            "blocked_actions": blocked
        }
    
    def close(self):
        """Close database connection."""
        if self._conn and not self._conn.closed:
            self._conn.close()


# For direct import fix
import psycopg2.extras
