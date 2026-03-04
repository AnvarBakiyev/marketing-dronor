"""Account State Manager — MKT-18

Manages Twitter account lifecycle states with validation and audit logging.

State Machine:
    NEW → WARMING (28 days warmup)
    WARMING → ACTIVE (warmup complete)
    ACTIVE → COOLING (shadowban detected / rate limits exceeded)
    COOLING → ACTIVE (after 48h rest)
    ACTIVE/COOLING → SUSPENDED (critical violations)

Usage:
    from account_state_manager import AccountStateManager
    
    manager = AccountStateManager(db_connection_string)
    manager.init_schema()  # Create tables if not exist
    
    # Transition account state
    result = manager.transition(account_id, "WARMING", reason="Starting warmup")
    
    # Check if warming complete
    is_ready = manager.check_warming_complete(account_id)
"""

import psycopg2
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any
from enum import Enum


class AccountState(Enum):
    NEW = "NEW"
    WARMING = "WARMING"
    ACTIVE = "ACTIVE"
    COOLING = "COOLING"
    SUSPENDED = "SUSPENDED"


# Valid state transitions
ALLOWED_TRANSITIONS = {
    AccountState.NEW: [AccountState.WARMING],
    AccountState.WARMING: [AccountState.ACTIVE, AccountState.SUSPENDED],
    AccountState.ACTIVE: [AccountState.COOLING, AccountState.SUSPENDED],
    AccountState.COOLING: [AccountState.ACTIVE, AccountState.SUSPENDED],
    AccountState.SUSPENDED: [],  # Terminal state
}

# Time periods
WARMING_PERIOD_DAYS = 28
COOLING_PERIOD_HOURS = 48


class AccountStateManager:
    """Manages Twitter account states with PostgreSQL persistence."""
    
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
            # Main accounts table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS twitter_accounts (
                    id SERIAL PRIMARY KEY,
                    twitter_handle VARCHAR(255) UNIQUE NOT NULL,
                    state VARCHAR(20) NOT NULL DEFAULT 'NEW',
                    state_changed_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    warming_started_at TIMESTAMP,
                    cooling_started_at TIMESTAMP,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    metadata JSONB DEFAULT '{}'
                );
                
                CREATE INDEX IF NOT EXISTS idx_twitter_accounts_state 
                    ON twitter_accounts(state);
                CREATE INDEX IF NOT EXISTS idx_twitter_accounts_handle 
                    ON twitter_accounts(twitter_handle);
            """)
            
            # Audit log table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS account_state_log (
                    id SERIAL PRIMARY KEY,
                    account_id INTEGER NOT NULL REFERENCES twitter_accounts(id),
                    from_state VARCHAR(20),
                    to_state VARCHAR(20) NOT NULL,
                    reason TEXT,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW()
                );
                
                CREATE INDEX IF NOT EXISTS idx_account_state_log_account 
                    ON account_state_log(account_id);
            """)
            
            self.conn.commit()
        
        return {"status": "success", "message": "Schema initialized"}
    
    def get_state(self, account_id: int) -> Optional[Dict[str, Any]]:
        """Get current state of an account."""
        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT id, twitter_handle, state, state_changed_at, 
                       warming_started_at, cooling_started_at, metadata
                FROM twitter_accounts 
                WHERE id = %s
            """, (account_id,))
            
            row = cur.fetchone()
            if not row:
                return None
            
            return {
                "id": row[0],
                "twitter_handle": row[1],
                "state": row[2],
                "state_changed_at": row[3].isoformat() if row[3] else None,
                "warming_started_at": row[4].isoformat() if row[4] else None,
                "cooling_started_at": row[5].isoformat() if row[5] else None,
                "metadata": row[6]
            }
    
    def transition(
        self, 
        account_id: int, 
        new_state: str, 
        reason: str = ""
    ) -> Dict[str, Any]:
        """Transition account to new state with validation."""
        # Validate new_state
        try:
            target_state = AccountState(new_state)
        except ValueError:
            return {
                "status": "error",
                "message": f"Invalid state: {new_state}",
                "valid_states": [s.value for s in AccountState]
            }
        
        # Get current state
        current = self.get_state(account_id)
        if not current:
            return {"status": "error", "message": f"Account {account_id} not found"}
        
        current_state = AccountState(current["state"])
        
        # Validate transition
        if target_state not in ALLOWED_TRANSITIONS[current_state]:
            allowed = [s.value for s in ALLOWED_TRANSITIONS[current_state]]
            return {
                "status": "error",
                "message": f"Cannot transition from {current_state.value} to {target_state.value}",
                "allowed_transitions": allowed
            }
        
        # Check warming period if transitioning WARMING → ACTIVE
        if current_state == AccountState.WARMING and target_state == AccountState.ACTIVE:
            if not self.check_warming_complete(account_id):
                return {
                    "status": "error",
                    "message": f"Warming period not complete. Required: {WARMING_PERIOD_DAYS} days"
                }
        
        # Check cooling period if transitioning COOLING → ACTIVE
        if current_state == AccountState.COOLING and target_state == AccountState.ACTIVE:
            if not self.check_cooling_complete(account_id):
                return {
                    "status": "error",
                    "message": f"Cooling period not complete. Required: {COOLING_PERIOD_HOURS} hours"
                }
        
        # Perform transition
        now = datetime.utcnow()
        with self.conn.cursor() as cur:
            # Update account state
            update_fields = {
                "state": target_state.value,
                "state_changed_at": now,
                "updated_at": now
            }
            
            # Track warming/cooling start times
            if target_state == AccountState.WARMING:
                update_fields["warming_started_at"] = now
            elif target_state == AccountState.COOLING:
                update_fields["cooling_started_at"] = now
            
            set_clause = ", ".join(f"{k} = %s" for k in update_fields.keys())
            values = list(update_fields.values()) + [account_id]
            
            cur.execute(f"""
                UPDATE twitter_accounts 
                SET {set_clause}
                WHERE id = %s
            """, values)
            
            # Log the transition
            cur.execute("""
                INSERT INTO account_state_log 
                    (account_id, from_state, to_state, reason)
                VALUES (%s, %s, %s, %s)
            """, (account_id, current_state.value, target_state.value, reason))
            
            self.conn.commit()
        
        return {
            "status": "success",
            "account_id": account_id,
            "from_state": current_state.value,
            "to_state": target_state.value,
            "reason": reason,
            "transitioned_at": now.isoformat()
        }
    
    def check_warming_complete(self, account_id: int) -> bool:
        """Check if account has completed 28-day warming period."""
        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT warming_started_at 
                FROM twitter_accounts 
                WHERE id = %s AND state = 'WARMING'
            """, (account_id,))
            
            row = cur.fetchone()
            if not row or not row[0]:
                return False
            
            warming_started = row[0]
            required_end = warming_started + timedelta(days=WARMING_PERIOD_DAYS)
            
            return datetime.utcnow() >= required_end
    
    def check_cooling_complete(self, account_id: int) -> bool:
        """Check if account has completed 48-hour cooling period."""
        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT cooling_started_at 
                FROM twitter_accounts 
                WHERE id = %s AND state = 'COOLING'
            """, (account_id,))
            
            row = cur.fetchone()
            if not row or not row[0]:
                return False
            
            cooling_started = row[0]
            required_end = cooling_started + timedelta(hours=COOLING_PERIOD_HOURS)
            
            return datetime.utcnow() >= required_end
    
    def get_accounts_by_state(self, state: str) -> List[Dict[str, Any]]:
        """Get all accounts in a specific state."""
        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT id, twitter_handle, state, state_changed_at, metadata
                FROM twitter_accounts 
                WHERE state = %s
                ORDER BY state_changed_at DESC
            """, (state,))
            
            return [
                {
                    "id": row[0],
                    "twitter_handle": row[1],
                    "state": row[2],
                    "state_changed_at": row[3].isoformat() if row[3] else None,
                    "metadata": row[4]
                }
                for row in cur.fetchall()
            ]
    
    def create_account(self, twitter_handle: str, metadata: dict = None) -> Dict[str, Any]:
        """Create a new account in NEW state."""
        with self.conn.cursor() as cur:
            try:
                cur.execute("""
                    INSERT INTO twitter_accounts (twitter_handle, metadata)
                    VALUES (%s, %s)
                    RETURNING id
                """, (twitter_handle, psycopg2.extras.Json(metadata or {})))
                
                account_id = cur.fetchone()[0]
                
                # Log creation
                cur.execute("""
                    INSERT INTO account_state_log 
                        (account_id, from_state, to_state, reason)
                    VALUES (%s, NULL, 'NEW', 'Account created')
                """, (account_id,))
                
                self.conn.commit()
                
                return {
                    "status": "success",
                    "account_id": account_id,
                    "twitter_handle": twitter_handle,
                    "state": "NEW"
                }
            except psycopg2.IntegrityError:
                self.conn.rollback()
                return {
                    "status": "error",
                    "message": f"Account {twitter_handle} already exists"
                }
    
    def get_state_history(self, account_id: int) -> List[Dict[str, Any]]:
        """Get full state transition history for an account."""
        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT from_state, to_state, reason, created_at
                FROM account_state_log
                WHERE account_id = %s
                ORDER BY created_at DESC
            """, (account_id,))
            
            return [
                {
                    "from_state": row[0],
                    "to_state": row[1],
                    "reason": row[2],
                    "created_at": row[3].isoformat() if row[3] else None
                }
                for row in cur.fetchall()
            ]
    
    def close(self):
        """Close database connection."""
        if self._conn and not self._conn.closed:
            self._conn.close()


# For direct import fix
import psycopg2.extras
