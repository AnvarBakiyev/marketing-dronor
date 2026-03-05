"""M3 Account Manager - Multi-account management for marketing operations."""

from .queue_manager import manage_queue
from .assignment_engine import manage_assignments

__all__ = ["manage_queue", "manage_assignments"]
__version__ = "0.1.0"
