"""M3 Account Manager - Multi-account management for marketing operations.

This module provides:
- AccountPool: Credential management and account lifecycle
- QueueManager: Priority queue with rate limiting and deduplication  
- AssignmentEngine: Task assignment with load balancing strategies
"""

from .account_pool import AccountPool, AccountInfo, AccountStatus
from .queue_manager import QueueManager, Task, TaskPriority, TaskStatus
from .assignment_engine import AssignmentEngine, AccountLoad, AccountState, AssignmentStrategy

__all__ = [
    # Account Pool
    "AccountPool",
    "AccountInfo",
    "AccountStatus",
    # Queue Manager
    "QueueManager",
    "Task",
    "TaskPriority",
    "TaskStatus",
    # Assignment Engine
    "AssignmentEngine",
    "AccountLoad",
    "AccountState",
    "AssignmentStrategy",
]

__version__ = "0.1.0"
