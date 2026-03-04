"""Health Monitor — MKT-19

Monitor Twitter account health and detect issues.

Health Indicators:
    - Shadowban detection (engagement drop)
    - Rate limit violations
    - Error patterns (API failures)
    - Account restrictions

Usage:
    from health_monitor import HealthMonitor
    
    monitor = HealthMonitor(db_connection_string)
    monitor.init_schema()  # Create tables if not exist
    
    # Record health event
    monitor.record_event(account_id, "shadowban_suspected", severity="warning")
    
    # Get health status
    health = monitor.get_health_status(account_id)
"""

import psycopg2
import psycopg2.extras
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from enum import Enum


class HealthEventType(Enum):
    # Warnings
    ENGAGEMENT_DROP = "engagement_drop"
    RATE_LIMIT_WARNING = "rate_limit_warning"
    API_ERROR = "api_error"
    SLOW_RESPONSE = "slow_response"
    
    # Critical
    SHADOWBAN_SUSPECTED = "shadowban_suspected"
    SHADOWBAN_CONFIRMED = "shadowban_confirmed"
    RATE_LIMIT_EXCEEDED = "rate_limit_exceeded"
    ACCOUNT_LOCKED = "account_locked"
    ACCOUNT_SUSPENDED = "account_suspended"
    
    # Info
    HEALTH_CHECK_OK = "health_check_ok"
    RECOVERED = "recovered"
    WARMING_MILESTONE = "warming_milestone"


class Severity(Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


# Event severity mapping
EVENT_SEVERITY = {
    HealthEventType.ENGAGEMENT_DROP: Severity.WARNING,
    HealthEventType.RATE_LIMIT_WARNING: Severity.WARNING,
    HealthEventType.API_ERROR: Severity.WARNING,
    HealthEventType.SLOW_RESPONSE: Severity.INFO,
    HealthEventType.SHADOWBAN_SUSPECTED: Severity.WARNING,
    HealthEventType.SHADOWBAN_CONFIRMED: Severity.CRITICAL,
    HealthEventType.RATE_LIMIT_EXCEEDED: Severity.CRITICAL,
    HealthEventType.ACCOUNT_LOCKED: Severity.CRITICAL,
    HealthEventType.ACCOUNT_SUSPENDED: Severity.CRITICAL,
    HealthEventType.HEALTH_CHECK_OK: Severity.INFO,
    HealthEventType.RECOVERED: Severity.INFO,
    HealthEventType.WARMING_MILESTONE: Severity.INFO,
}

# Thresholds for automatic actions
THRESHOLDS = {
    "warnings_to_cooling": 3,  # N warnings in 24h → trigger cooling
    "criticals_to_suspend": 1,  # N critical events → consider suspension
    "engagement_drop_percent": 50,  # % drop to trigger warning
    "recovery_hours": 48,  # Hours without issues to mark recovered
}


class HealthMonitor:
    """Monitors Twitter account health and tracks issues."""
    
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
                CREATE TABLE IF NOT EXISTS health_events (
                    id SERIAL PRIMARY KEY,
                    account_id INTEGER NOT NULL,
                    event_type VARCHAR(50) NOT NULL,
                    severity VARCHAR(20) NOT NULL,
                    message TEXT,
                    metadata JSONB DEFAULT '{}',
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    resolved_at TIMESTAMP,
                    resolved_by VARCHAR(100)
                );
                
                CREATE INDEX IF NOT EXISTS idx_health_events_account 
                    ON health_events(account_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_health_events_severity 
                    ON health_events(severity, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_health_events_unresolved 
                    ON health_events(account_id) WHERE resolved_at IS NULL;
            """)
            
            # Health summary table for quick lookups
            cur.execute("""
                CREATE TABLE IF NOT EXISTS account_health_summary (
                    account_id INTEGER PRIMARY KEY,
                    health_score INTEGER DEFAULT 100,
                    last_check_at TIMESTAMP,
                    warnings_24h INTEGER DEFAULT 0,
                    criticals_24h INTEGER DEFAULT 0,
                    status VARCHAR(20) DEFAULT 'healthy',
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
                );
            """)
            
            self.conn.commit()
        
        return {"status": "success", "message": "Health monitor schema initialized"}
    
    def record_event(
        self,
        account_id: int,
        event_type: str,
        severity: str = None,
        message: str = "",
        metadata: dict = None
    ) -> Dict[str, Any]:
        """Record a health event for an account."""
        # Validate event type
        try:
            event = HealthEventType(event_type)
        except ValueError:
            return {
                "status": "error",
                "message": f"Invalid event type: {event_type}",
                "valid_events": [e.value for e in HealthEventType]
            }
        
        # Determine severity
        if severity:
            try:
                sev = Severity(severity)
            except ValueError:
                return {
                    "status": "error",
                    "message": f"Invalid severity: {severity}",
                    "valid_severities": [s.value for s in Severity]
                }
        else:
            sev = EVENT_SEVERITY.get(event, Severity.INFO)
        
        with self.conn.cursor() as cur:
            cur.execute("""
                INSERT INTO health_events 
                    (account_id, event_type, severity, message, metadata)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id, created_at
            """, (
                account_id, 
                event_type, 
                sev.value, 
                message, 
                psycopg2.extras.Json(metadata or {})
            ))
            
            result = cur.fetchone()
            
            # Update health summary
            self._update_health_summary(account_id, cur)
            
            self.conn.commit()
            
            return {
                "status": "success",
                "event_id": result[0],
                "account_id": account_id,
                "event_type": event_type,
                "severity": sev.value,
                "recorded_at": result[1].isoformat(),
                "action_triggered": self._check_thresholds(account_id)
            }
    
    def _update_health_summary(self, account_id: int, cur) -> None:
        """Update the health summary for an account."""
        now = datetime.utcnow()
        day_ago = now - timedelta(hours=24)
        
        # Count warnings and criticals in last 24h
        cur.execute("""
            SELECT 
                COUNT(*) FILTER (WHERE severity = 'warning') as warnings,
                COUNT(*) FILTER (WHERE severity = 'critical') as criticals
            FROM health_events
            WHERE account_id = %s AND created_at >= %s
        """, (account_id, day_ago))
        
        counts = cur.fetchone()
        warnings_24h = counts[0] or 0
        criticals_24h = counts[1] or 0
        
        # Calculate health score (0-100)
        health_score = 100 - (warnings_24h * 10) - (criticals_24h * 30)
        health_score = max(0, min(100, health_score))
        
        # Determine status
        if criticals_24h > 0:
            status = "critical"
        elif warnings_24h >= THRESHOLDS["warnings_to_cooling"]:
            status = "degraded"
        elif warnings_24h > 0:
            status = "warning"
        else:
            status = "healthy"
        
        # Upsert summary
        cur.execute("""
            INSERT INTO account_health_summary 
                (account_id, health_score, last_check_at, warnings_24h, criticals_24h, status, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (account_id) DO UPDATE SET
                health_score = EXCLUDED.health_score,
                last_check_at = EXCLUDED.last_check_at,
                warnings_24h = EXCLUDED.warnings_24h,
                criticals_24h = EXCLUDED.criticals_24h,
                status = EXCLUDED.status,
                updated_at = EXCLUDED.updated_at
        """, (account_id, health_score, now, warnings_24h, criticals_24h, status, now))
    
    def _check_thresholds(self, account_id: int) -> Optional[str]:
        """Check if any threshold is exceeded and return recommended action."""
        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT warnings_24h, criticals_24h 
                FROM account_health_summary 
                WHERE account_id = %s
            """, (account_id,))
            
            row = cur.fetchone()
            if not row:
                return None
            
            warnings, criticals = row
            
            if criticals >= THRESHOLDS["criticals_to_suspend"]:
                return "consider_suspension"
            elif warnings >= THRESHOLDS["warnings_to_cooling"]:
                return "trigger_cooling"
            
            return None
    
    def get_health_status(
        self,
        account_id: int
    ) -> Dict[str, Any]:
        """Get current health status for an account."""
        with self.conn.cursor() as cur:
            # Get summary
            cur.execute("""
                SELECT health_score, last_check_at, warnings_24h, 
                       criticals_24h, status, updated_at
                FROM account_health_summary
                WHERE account_id = %s
            """, (account_id,))
            
            row = cur.fetchone()
            
            if not row:
                return {
                    "account_id": account_id,
                    "status": "unknown",
                    "message": "No health data available"
                }
            
            # Get recent events
            cur.execute("""
                SELECT event_type, severity, message, created_at
                FROM health_events
                WHERE account_id = %s
                ORDER BY created_at DESC
                LIMIT 10
            """, (account_id,))
            
            recent_events = [
                {
                    "event_type": r[0],
                    "severity": r[1],
                    "message": r[2],
                    "created_at": r[3].isoformat() if r[3] else None
                }
                for r in cur.fetchall()
            ]
            
            return {
                "account_id": account_id,
                "health_score": row[0],
                "status": row[4],
                "last_check_at": row[1].isoformat() if row[1] else None,
                "warnings_24h": row[2],
                "criticals_24h": row[3],
                "updated_at": row[5].isoformat() if row[5] else None,
                "recent_events": recent_events,
                "recommended_action": self._check_thresholds(account_id)
            }
    
    def get_unhealthy_accounts(
        self,
        min_severity: str = "warning"
    ) -> List[Dict[str, Any]]:
        """Get all accounts with health issues."""
        status_filter = ["warning", "degraded", "critical"]
        if min_severity == "critical":
            status_filter = ["critical"]
        elif min_severity == "degraded":
            status_filter = ["degraded", "critical"]
        
        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT ahs.account_id, ahs.health_score, ahs.status,
                       ahs.warnings_24h, ahs.criticals_24h,
                       ta.twitter_handle, ta.state
                FROM account_health_summary ahs
                LEFT JOIN twitter_accounts ta ON ta.id = ahs.account_id
                WHERE ahs.status = ANY(%s)
                ORDER BY ahs.health_score ASC
            """, (status_filter,))
            
            return [
                {
                    "account_id": r[0],
                    "health_score": r[1],
                    "status": r[2],
                    "warnings_24h": r[3],
                    "criticals_24h": r[4],
                    "twitter_handle": r[5],
                    "account_state": r[6]
                }
                for r in cur.fetchall()
            ]
    
    def resolve_event(
        self,
        event_id: int,
        resolved_by: str = "system"
    ) -> Dict[str, Any]:
        """Mark a health event as resolved."""
        with self.conn.cursor() as cur:
            cur.execute("""
                UPDATE health_events
                SET resolved_at = NOW(), resolved_by = %s
                WHERE id = %s AND resolved_at IS NULL
                RETURNING account_id
            """, (resolved_by, event_id))
            
            result = cur.fetchone()
            if not result:
                return {
                    "status": "error",
                    "message": f"Event {event_id} not found or already resolved"
                }
            
            # Update summary
            self._update_health_summary(result[0], cur)
            self.conn.commit()
            
            return {
                "status": "success",
                "event_id": event_id,
                "resolved_by": resolved_by
            }
    
    def run_health_check(
        self,
        account_id: int,
        engagement_data: dict = None
    ) -> Dict[str, Any]:
        """Run health check and record result."""
        issues = []
        
        # Check engagement drop if data provided
        if engagement_data:
            current = engagement_data.get("current_engagement", 0)
            baseline = engagement_data.get("baseline_engagement", 0)
            
            if baseline > 0:
                drop_percent = ((baseline - current) / baseline) * 100
                if drop_percent >= THRESHOLDS["engagement_drop_percent"]:
                    issues.append({
                        "type": HealthEventType.ENGAGEMENT_DROP.value,
                        "message": f"Engagement dropped by {drop_percent:.1f}%",
                        "metadata": {"drop_percent": drop_percent}
                    })
        
        # Record issues or OK status
        if issues:
            for issue in issues:
                self.record_event(
                    account_id=account_id,
                    event_type=issue["type"],
                    message=issue["message"],
                    metadata=issue.get("metadata")
                )
            return {
                "status": "issues_found",
                "account_id": account_id,
                "issues": issues
            }
        else:
            self.record_event(
                account_id=account_id,
                event_type=HealthEventType.HEALTH_CHECK_OK.value,
                message="Routine health check passed"
            )
            return {
                "status": "healthy",
                "account_id": account_id,
                "message": "Health check passed"
            }
    
    def get_fleet_health_summary(self) -> Dict[str, Any]:
        """Get aggregated health summary for all accounts."""
        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT 
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE status = 'healthy') as healthy,
                    COUNT(*) FILTER (WHERE status = 'warning') as warning,
                    COUNT(*) FILTER (WHERE status = 'degraded') as degraded,
                    COUNT(*) FILTER (WHERE status = 'critical') as critical,
                    AVG(health_score) as avg_score
                FROM account_health_summary
            """)
            
            row = cur.fetchone()
            
            return {
                "total_accounts": row[0] or 0,
                "healthy": row[1] or 0,
                "warning": row[2] or 0,
                "degraded": row[3] or 0,
                "critical": row[4] or 0,
                "average_health_score": round(row[5] or 0, 1),
                "fleet_status": (
                    "critical" if (row[4] or 0) > 0 else
                    "degraded" if (row[3] or 0) > 0 else
                    "warning" if (row[2] or 0) > 0 else
                    "healthy"
                )
            }
    
    def close(self):
        """Close database connection."""
        if self._conn and not self._conn.closed:
            self._conn.close()
