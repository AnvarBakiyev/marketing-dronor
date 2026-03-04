"""Assignment Engine for Marketing Operations.

Assigns outreach tasks to accounts based on:
- Account state (only Active accounts get tasks)
- Current load and rate limits
- Account specialization/history
- Load balancing across fleet
"""

import time
import random
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime
import json
from pathlib import Path
import threading
from enum import Enum


class AccountState(Enum):
    """Account lifecycle states."""
    WARMING = "warming"
    ACTIVE = "active"
    COOLING = "cooling"
    SUSPENDED = "suspended"


class AssignmentStrategy(Enum):
    """Task assignment strategies."""
    ROUND_ROBIN = "round_robin"        # Distribute evenly
    LEAST_LOADED = "least_loaded"      # Assign to account with lowest current load
    RANDOM = "random"                  # Random selection from available
    SPECIALIZED = "specialized"        # Match account expertise to task type
    WEIGHTED = "weighted"              # Consider account reputation/success rate


@dataclass
class AccountLoad:
    """Track account's current workload."""
    account_id: str
    state: AccountState = AccountState.ACTIVE
    current_tasks: int = 0
    tasks_today: int = 0
    tasks_this_hour: int = 0
    last_task_at: Optional[float] = None
    success_rate: float = 1.0
    specializations: List[str] = field(default_factory=list)
    daily_limit: int = 100
    hourly_limit: int = 20
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def can_accept_task(self, task_type: str = "") -> Tuple[bool, str]:
        """Check if account can accept a new task.
        
        Returns:
            (can_accept, reason)
        """
        # State check
        if self.state == AccountState.SUSPENDED:
            return False, "Account suspended"
        if self.state == AccountState.COOLING:
            return False, "Account in cooling period"
        if self.state == AccountState.WARMING:
            # Warming accounts have reduced limits
            if self.tasks_today >= self.daily_limit // 4:
                return False, "Warming account daily limit reached"
            if self.tasks_this_hour >= self.hourly_limit // 4:
                return False, "Warming account hourly limit reached"
        
        # Daily limit
        if self.tasks_today >= self.daily_limit:
            return False, "Daily limit reached"
        
        # Hourly limit
        if self.tasks_this_hour >= self.hourly_limit:
            return False, "Hourly limit reached"
        
        # Minimum delay between tasks (at least 30 seconds)
        if self.last_task_at:
            elapsed = time.time() - self.last_task_at
            if elapsed < 30:
                return False, f"Too soon since last task ({elapsed:.0f}s < 30s)"
        
        return True, "OK"
    
    def calculate_load_score(self) -> float:
        """Calculate load score (lower = less loaded).
        
        Score considers:
        - Current tasks in progress (40%)
        - Hourly utilization (30%)
        - Daily utilization (30%)
        """
        # Normalize each metric to 0-1
        current_score = min(self.current_tasks / 10, 1.0)  # Assume max 10 concurrent
        hourly_score = self.tasks_this_hour / self.hourly_limit if self.hourly_limit > 0 else 0
        daily_score = self.tasks_today / self.daily_limit if self.daily_limit > 0 else 0
        
        # Weighted average
        return (current_score * 0.4 + hourly_score * 0.3 + daily_score * 0.3)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "account_id": self.account_id,
            "state": self.state.value,
            "current_tasks": self.current_tasks,
            "tasks_today": self.tasks_today,
            "tasks_this_hour": self.tasks_this_hour,
            "last_task_at": self.last_task_at,
            "last_task_at_iso": datetime.fromtimestamp(self.last_task_at).isoformat() if self.last_task_at else None,
            "success_rate": self.success_rate,
            "specializations": self.specializations,
            "daily_limit": self.daily_limit,
            "hourly_limit": self.hourly_limit,
            "load_score": self.calculate_load_score(),
            "metadata": self.metadata
        }


class AssignmentEngine:
    """Engine for assigning tasks to accounts.
    
    Features:
    - Multiple assignment strategies
    - Load balancing across account fleet
    - Respect account states and limits
    - Track assignment history
    - Automatic load updates
    """
    
    def __init__(self, data_dir: Optional[str] = None):
        """Initialize assignment engine.
        
        Args:
            data_dir: Directory for persistence. Defaults to ~/.marketing_dronor/assignments/
        """
        self.data_dir = Path(data_dir) if data_dir else Path.home() / ".marketing_dronor" / "assignments"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        # Account loads
        self._accounts: Dict[str, AccountLoad] = {}
        
        # Assignment history (for round-robin)
        self._last_assigned_idx: int = 0
        
        # Thread lock
        self._lock = threading.RLock()
        
        # Load persisted state
        self._load_state()
    
    def register_account(
        self,
        account_id: str,
        state: AccountState = AccountState.ACTIVE,
        daily_limit: int = 100,
        hourly_limit: int = 20,
        specializations: Optional[List[str]] = None
    ) -> AccountLoad:
        """Register or update an account.
        
        Args:
            account_id: Unique account identifier
            state: Current account state
            daily_limit: Maximum tasks per day
            hourly_limit: Maximum tasks per hour
            specializations: Task types this account is good at
            
        Returns:
            Account load info
        """
        with self._lock:
            if account_id in self._accounts:
                # Update existing
                account = self._accounts[account_id]
                account.state = state
                account.daily_limit = daily_limit
                account.hourly_limit = hourly_limit
                if specializations:
                    account.specializations = specializations
            else:
                # Create new
                account = AccountLoad(
                    account_id=account_id,
                    state=state,
                    daily_limit=daily_limit,
                    hourly_limit=hourly_limit,
                    specializations=specializations or []
                )
                self._accounts[account_id] = account
            
            self._save_state()
            return account
    
    def update_account_state(self, account_id: str, state: AccountState) -> bool:
        """Update account state.
        
        Args:
            account_id: Account identifier
            state: New state
            
        Returns:
            True if updated
        """
        with self._lock:
            if account_id not in self._accounts:
                return False
            
            self._accounts[account_id].state = state
            self._save_state()
            return True
    
    def assign_task(
        self,
        task_type: str,
        strategy: AssignmentStrategy = AssignmentStrategy.LEAST_LOADED,
        preferred_account: Optional[str] = None,
        exclude_accounts: Optional[List[str]] = None
    ) -> Optional[Dict[str, Any]]:
        """Assign a task to an account.
        
        Args:
            task_type: Type of task (like, reply, etc.)
            strategy: Assignment strategy to use
            preferred_account: Try this account first if available
            exclude_accounts: Accounts to skip
            
        Returns:
            Assignment info or None if no account available
        """
        with self._lock:
            # Reset hourly counters if needed
            self._reset_hourly_counters()
            
            exclude = set(exclude_accounts or [])
            
            # Try preferred account first
            if preferred_account and preferred_account in self._accounts:
                account = self._accounts[preferred_account]
                can_accept, reason = account.can_accept_task(task_type)
                if can_accept:
                    return self._make_assignment(account, task_type)
            
            # Get available accounts
            available = []
            for account in self._accounts.values():
                if account.account_id in exclude:
                    continue
                can_accept, reason = account.can_accept_task(task_type)
                if can_accept:
                    available.append(account)
            
            if not available:
                return None
            
            # Select account based on strategy
            selected = self._select_account(available, task_type, strategy)
            if selected:
                return self._make_assignment(selected, task_type)
            
            return None
    
    def _select_account(
        self,
        candidates: List[AccountLoad],
        task_type: str,
        strategy: AssignmentStrategy
    ) -> Optional[AccountLoad]:
        """Select account based on strategy."""
        if not candidates:
            return None
        
        if strategy == AssignmentStrategy.RANDOM:
            return random.choice(candidates)
        
        elif strategy == AssignmentStrategy.ROUND_ROBIN:
            # Sort by ID for consistent ordering
            sorted_candidates = sorted(candidates, key=lambda a: a.account_id)
            self._last_assigned_idx = (self._last_assigned_idx + 1) % len(sorted_candidates)
            return sorted_candidates[self._last_assigned_idx]
        
        elif strategy == AssignmentStrategy.LEAST_LOADED:
            # Select account with lowest load score
            return min(candidates, key=lambda a: a.calculate_load_score())
        
        elif strategy == AssignmentStrategy.SPECIALIZED:
            # Prefer accounts specialized in this task type
            specialized = [a for a in candidates if task_type in a.specializations]
            if specialized:
                # Among specialized, pick least loaded
                return min(specialized, key=lambda a: a.calculate_load_score())
            # Fallback to least loaded
            return min(candidates, key=lambda a: a.calculate_load_score())
        
        elif strategy == AssignmentStrategy.WEIGHTED:
            # Weight by success rate and inverse load
            def weight(account: AccountLoad) -> float:
                load_factor = 1 - account.calculate_load_score()
                return account.success_rate * load_factor
            
            # Weighted random selection
            weights = [weight(a) for a in candidates]
            total = sum(weights)
            if total == 0:
                return random.choice(candidates)
            
            # Roulette wheel selection
            pick = random.uniform(0, total)
            current = 0
            for account, w in zip(candidates, weights):
                current += w
                if current >= pick:
                    return account
            return candidates[-1]
        
        # Default: first available
        return candidates[0]
    
    def _make_assignment(
        self,
        account: AccountLoad,
        task_type: str
    ) -> Dict[str, Any]:
        """Record assignment and update counters."""
        account.current_tasks += 1
        account.tasks_today += 1
        account.tasks_this_hour += 1
        account.last_task_at = time.time()
        
        self._save_state()
        
        return {
            "assigned_account_id": account.account_id,
            "task_type": task_type,
            "assigned_at": time.time(),
            "assigned_at_iso": datetime.now().isoformat(),
            "load_after_assignment": account.calculate_load_score(),
            "account_state": account.state.value,
            "tasks_today": account.tasks_today,
            "tasks_this_hour": account.tasks_this_hour
        }
    
    def complete_task(self, account_id: str, success: bool = True) -> bool:
        """Mark task as completed.
        
        Args:
            account_id: Account that completed task
            success: Whether task succeeded
            
        Returns:
            True if updated
        """
        with self._lock:
            if account_id not in self._accounts:
                return False
            
            account = self._accounts[account_id]
            account.current_tasks = max(0, account.current_tasks - 1)
            
            # Update success rate (exponential moving average)
            alpha = 0.1  # Smoothing factor
            account.success_rate = alpha * (1.0 if success else 0.0) + (1 - alpha) * account.success_rate
            
            self._save_state()
            return True
    
    def get_account_load(self, account_id: str) -> Optional[Dict[str, Any]]:
        """Get account load info."""
        with self._lock:
            if account_id not in self._accounts:
                return None
            return self._accounts[account_id].to_dict()
    
    def get_all_accounts(self) -> List[Dict[str, Any]]:
        """Get all registered accounts."""
        with self._lock:
            return [a.to_dict() for a in self._accounts.values()]
    
    def get_available_accounts(self, task_type: str = "") -> List[Dict[str, Any]]:
        """Get accounts that can accept tasks."""
        with self._lock:
            self._reset_hourly_counters()
            
            available = []
            for account in self._accounts.values():
                can_accept, reason = account.can_accept_task(task_type)
                info = account.to_dict()
                info["can_accept"] = can_accept
                info["reject_reason"] = reason if not can_accept else None
                available.append(info)
            
            return available
    
    def get_fleet_summary(self) -> Dict[str, Any]:
        """Get summary of entire account fleet."""
        with self._lock:
            self._reset_hourly_counters()
            
            summary = {
                "total_accounts": len(self._accounts),
                "by_state": {},
                "available_capacity": 0,
                "total_tasks_today": 0,
                "total_current_tasks": 0,
                "avg_load_score": 0.0,
                "accounts_at_limit": 0,
                "top_performers": [],
                "needs_attention": []
            }
            
            if not self._accounts:
                return summary
            
            # Count by state
            state_counts = {}
            load_scores = []
            
            for account in self._accounts.values():
                state_name = account.state.value
                state_counts[state_name] = state_counts.get(state_name, 0) + 1
                
                can_accept, _ = account.can_accept_task()
                if can_accept:
                    summary["available_capacity"] += account.daily_limit - account.tasks_today
                
                summary["total_tasks_today"] += account.tasks_today
                summary["total_current_tasks"] += account.current_tasks
                load_scores.append(account.calculate_load_score())
                
                if account.tasks_today >= account.daily_limit:
                    summary["accounts_at_limit"] += 1
                
                # Track top performers and problem accounts
                if account.success_rate >= 0.95:
                    summary["top_performers"].append(account.account_id)
                elif account.success_rate < 0.7:
                    summary["needs_attention"].append({
                        "account_id": account.account_id,
                        "success_rate": account.success_rate,
                        "state": account.state.value
                    })
            
            summary["by_state"] = state_counts
            summary["avg_load_score"] = sum(load_scores) / len(load_scores)
            summary["top_performers"] = summary["top_performers"][:5]  # Top 5
            
            return summary
    
    def reset_daily_counters(self) -> int:
        """Reset daily task counters for all accounts.
        
        Returns:
            Number of accounts reset
        """
        with self._lock:
            count = 0
            for account in self._accounts.values():
                account.tasks_today = 0
                count += 1
            self._save_state()
            return count
    
    def _reset_hourly_counters(self):
        """Reset hourly counters if hour changed."""
        current_hour = datetime.now().hour
        saved_hour = getattr(self, '_last_hour', None)
        
        if saved_hour is None or saved_hour != current_hour:
            for account in self._accounts.values():
                account.tasks_this_hour = 0
            self._last_hour = current_hour
    
    def _save_state(self):
        """Persist state to disk."""
        data = {
            "accounts": {
                aid: {
                    **a.to_dict(),
                    "state": a.state.value  # Ensure state is string
                } for aid, a in self._accounts.items()
            },
            "last_assigned_idx": self._last_assigned_idx,
            "saved_at": time.time()
        }
        
        state_file = self.data_dir / "assignment_state.json"
        with open(state_file, "w") as f:
            json.dump(data, f, indent=2)
    
    def _load_state(self):
        """Load state from disk."""
        state_file = self.data_dir / "assignment_state.json"
        if not state_file.exists():
            return
        
        try:
            with open(state_file, "r") as f:
                data = json.load(f)
            
            self._last_assigned_idx = data.get("last_assigned_idx", 0)
            
            for aid, adata in data.get("accounts", {}).items():
                self._accounts[aid] = AccountLoad(
                    account_id=aid,
                    state=AccountState(adata.get("state", "active")),
                    current_tasks=adata.get("current_tasks", 0),
                    tasks_today=adata.get("tasks_today", 0),
                    tasks_this_hour=adata.get("tasks_this_hour", 0),
                    last_task_at=adata.get("last_task_at"),
                    success_rate=adata.get("success_rate", 1.0),
                    specializations=adata.get("specializations", []),
                    daily_limit=adata.get("daily_limit", 100),
                    hourly_limit=adata.get("hourly_limit", 20),
                    metadata=adata.get("metadata", {})
                )
        
        except Exception as e:
            print(f"Error loading assignment state: {e}")


# Convenience function for expert usage
def manage_assignments(
    action: str = "summary",
    account_id: str = "",
    state: str = "active",
    task_type: str = "",
    strategy: str = "least_loaded",
    daily_limit: int = 100,
    hourly_limit: int = 20,
    specializations: str = "",  # Comma-separated
    success: bool = True,
    data_dir: Optional[str] = None
) -> Dict[str, Any]:
    """Manage task assignments.
    
    Args:
        action: Operation - "register", "assign", "complete", "get_account", 
                "available", "summary", "update_state", "reset_daily"
        account_id: Account identifier
        state: Account state (warming, active, cooling, suspended)
        task_type: Type of task for assignment
        strategy: Assignment strategy (round_robin, least_loaded, random, specialized, weighted)
        daily_limit: Daily task limit for account
        hourly_limit: Hourly task limit for account
        specializations: Comma-separated task types account is good at
        success: Whether task completed successfully (for complete action)
        data_dir: Custom data directory
        
    Returns:
        Operation result
    """
    engine = AssignmentEngine(data_dir=data_dir)
    
    if action == "register":
        if not account_id:
            return {"error": "account_id required"}
        specs = [s.strip() for s in specializations.split(",") if s.strip()] if specializations else None
        try:
            account_state = AccountState(state)
        except ValueError:
            return {"error": f"Invalid state: {state}"}
        account = engine.register_account(
            account_id=account_id,
            state=account_state,
            daily_limit=daily_limit,
            hourly_limit=hourly_limit,
            specializations=specs
        )
        return {"status": "success", "account": account.to_dict()}
    
    elif action == "update_state":
        if not account_id:
            return {"error": "account_id required"}
        try:
            account_state = AccountState(state)
        except ValueError:
            return {"error": f"Invalid state: {state}"}
        success = engine.update_account_state(account_id, account_state)
        return {"status": "success" if success else "not_found", "account_id": account_id}
    
    elif action == "assign":
        try:
            strat = AssignmentStrategy(strategy)
        except ValueError:
            strat = AssignmentStrategy.LEAST_LOADED
        result = engine.assign_task(task_type=task_type, strategy=strat)
        if result:
            return {"status": "success", "assignment": result}
        return {"status": "no_available_accounts"}
    
    elif action == "complete":
        if not account_id:
            return {"error": "account_id required"}
        updated = engine.complete_task(account_id, success=success)
        return {"status": "success" if updated else "not_found", "account_id": account_id}
    
    elif action == "get_account":
        if not account_id:
            return {"error": "account_id required"}
        load = engine.get_account_load(account_id)
        if load:
            return {"status": "success", "account": load}
        return {"status": "not_found", "account_id": account_id}
    
    elif action == "available":
        accounts = engine.get_available_accounts(task_type=task_type)
        available_count = sum(1 for a in accounts if a["can_accept"])
        return {
            "status": "success",
            "accounts": accounts,
            "total": len(accounts),
            "available": available_count
        }
    
    elif action == "summary":
        return {"status": "success", "summary": engine.get_fleet_summary()}
    
    elif action == "reset_daily":
        count = engine.reset_daily_counters()
        return {"status": "success", "accounts_reset": count}
    
    elif action == "all_accounts":
        accounts = engine.get_all_accounts()
        return {"status": "success", "accounts": accounts, "total": len(accounts)}
    
    else:
        return {"error": f"Unknown action: {action}"}
