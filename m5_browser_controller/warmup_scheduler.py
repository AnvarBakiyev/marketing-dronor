"""
Account Warmup Scheduler for Marketing Dronor
MKT-32 | Sprint 4: Real Infrastructure

4-Phase Warmup Cycle:
- foundation (days 1-7): Likes only
- ramp (days 8-14): Add replies/follows  
- outreach (days 15-21): Add DMs
- cruise (days 22-28): Full capacity
"""

import psycopg2
import psycopg2.extras
from datetime import date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from infra.config import DB_CONFIG


class WarmupScheduler:
    """Account warmup scheduler with 4-phase progression."""
    
    # Phase definitions: (phase_name, likes_fn, replies_fn, follows_fn, dms_fn)
    PHASES = {
        (1, 7): ("foundation", lambda d: 5 + d*2, lambda d: 0, lambda d: 0, lambda d: 0),
        (8, 14): ("ramp", lambda d: 15 + (d-7)*2, lambda d: 2 + (d-7), lambda d: 3 + (d-7), lambda d: 0),
        (15, 21): ("outreach", lambda d: 25 + (d-14), lambda d: 8 + (d-14), lambda d: 8, lambda d: 1 + (d-14)//2),
        (22, 28): ("cruise", lambda d: 30, lambda d: 15, lambda d: 10, lambda d: 5),
    }
    
    def __init__(self):
        self.db_config = DB_CONFIG
    
    def get_db_connection(self):
        return psycopg2.connect(**self.db_config, cursor_factory=psycopg2.extras.RealDictCursor)
    
    def get_phase_config(self, warmup_day: int) -> dict:
        """Get phase name and daily limits for given warmup day."""
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
        
        # Day > 28 = cruise mode
        return {
            "phase": "cruise",
            "daily_likes": 30,
            "daily_replies": 15,
            "daily_follows": 10,
            "daily_dms": 5
        }
    
    def get_warmup_stage(self, warmup_day: int) -> str:
        """Map warmup_day to warmup_stage."""
        if warmup_day < 1:
            return "new"
        elif warmup_day <= 28:
            return "warming"
        else:
            return "active"
    
    def generate_today_schedules(self) -> dict:
        """Generate warmup_schedule for all profiles without today's schedule."""
        conn = self.get_db_connection()
        created = 0
        
        try:
            with conn.cursor() as cur:
                # Get all profiles without today's schedule
                cur.execute("""
                    SELECT ap.id, ap.warmup_day, ap.warmup_stage
                    FROM adspower_profiles ap
                    LEFT JOIN warmup_schedule ws 
                        ON ws.adspower_profile_id = ap.id 
                        AND ws.schedule_date = CURRENT_DATE
                    WHERE ws.id IS NULL
                        AND ap.warmup_stage NOT IN ('restricted', 'banned')
                """)
                profiles = cur.fetchall()
                
                for profile in profiles:
                    profile_id = profile["id"]
                    warmup_day = profile["warmup_day"] + 1
                    
                    config = self.get_phase_config(warmup_day)
                    new_stage = self.get_warmup_stage(warmup_day)
                    
                    # Insert schedule
                    cur.execute("""
                        INSERT INTO warmup_schedule 
                        (adspower_profile_id, schedule_date, warmup_day, phase,
                         daily_likes, daily_replies, daily_follows, daily_dms)
                        VALUES (%s, CURRENT_DATE, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (adspower_profile_id, schedule_date) DO NOTHING
                    """, (profile_id, warmup_day, config["phase"],
                          config["daily_likes"], config["daily_replies"],
                          config["daily_follows"], config["daily_dms"]))
                    
                    # Update profile warmup_day and stage
                    cur.execute("""
                        UPDATE adspower_profiles
                        SET warmup_day = %s, warmup_stage = %s
                        WHERE id = %s
                    """, (warmup_day, new_stage, profile_id))
                    
                    created += 1
                
                conn.commit()
                
        finally:
            conn.close()
        
        return {
            "status": "success",
            "schedules_created": created,
            "date": str(date.today())
        }
    
    def get_profile_schedule(self, profile_id: int) -> dict:
        """Get today's warmup schedule for a profile."""
        conn = self.get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT * FROM warmup_schedule
                    WHERE adspower_profile_id = %s AND schedule_date = CURRENT_DATE
                """, (profile_id,))
                schedule = cur.fetchone()
                
                if not schedule:
                    return {"status": "error", "message": "No schedule for today"}
                
                return {
                    "status": "success",
                    "schedule": dict(schedule),
                    "remaining": {
                        "likes": schedule["daily_likes"] - schedule["likes_done"],
                        "replies": schedule["daily_replies"] - schedule["replies_done"],
                        "follows": schedule["daily_follows"] - schedule["follows_done"],
                        "dms": schedule["daily_dms"] - schedule["dms_done"]
                    }
                }
        finally:
            conn.close()
    
    def get_all_today_schedules(self) -> list:
        """Get today's schedules for all profiles."""
        conn = self.get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT 
                        ws.*,
                        ap.serial_number,
                        ta.username
                    FROM warmup_schedule ws
                    JOIN adspower_profiles ap ON ap.id = ws.adspower_profile_id
                    LEFT JOIN twitter_accounts ta ON ta.id = ap.account_id
                    WHERE ws.schedule_date = CURRENT_DATE
                    ORDER BY ws.warmup_day ASC
                """)
                return [dict(row) for row in cur.fetchall()]
        finally:
            conn.close()
    
    def check_phase(self, profile_id: int) -> dict:
        """Check current phase and limits for a profile."""
        conn = self.get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT warmup_day, warmup_stage FROM adspower_profiles WHERE id = %s
                """, (profile_id,))
                row = cur.fetchone()
                
                if not row:
                    return {"status": "error", "message": "Profile not found"}
                
                config = self.get_phase_config(row["warmup_day"])
                
                return {
                    "status": "success",
                    "profile_id": profile_id,
                    "warmup_day": row["warmup_day"],
                    "warmup_stage": row["warmup_stage"],
                    "phase": config["phase"],
                    "daily_limits": config
                }
        finally:
            conn.close()
    
    def mark_schedule_completed(self, profile_id: int) -> bool:
        """Mark today's schedule as completed."""
        conn = self.get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE warmup_schedule
                    SET completed = TRUE
                    WHERE adspower_profile_id = %s AND schedule_date = CURRENT_DATE
                """, (profile_id,))
                conn.commit()
                return cur.rowcount > 0
        finally:
            conn.close()


if __name__ == "__main__":
    scheduler = WarmupScheduler()
    result = scheduler.generate_today_schedules()
    print(f"Generated schedules: {result}")
