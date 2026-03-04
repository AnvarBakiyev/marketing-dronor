"""Queue Manager for Marketing Operations.

Priority queue system for outreach tasks (P0-P4).
FIFO ordering within same priority level.
"""

import heapq
import time
import uuid
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional, List, Dict, Any
from datetime import datetime
import json
from pathlib import Path
import threading


class Priority(IntEnum):
    """Task priority levels (lower = higher priority)."""
    P0_CRITICAL = 0   # Immediate: damage control, urgent responses
    P1_HIGH = 1       # High: time-sensitive opportunities
    P2_MEDIUM = 2     # Medium: scheduled outreach
    P3_LOW = 3        # Low: bulk operations
    P4_BACKGROUND = 4 # Background: cleanup, maintenance


class TaskStatus:
    """Task execution status."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class TaskType:
    """Types of outreach tasks."""
    LIKE = "like"
    REPLY = "reply"
    RETWEET = "retweet"
    QUOTE = "quote"
    FOLLOW = "follow"
    DM = "dm"
    BOOKMARK = "bookmark"
    LIST_ADD = "list_add"


@dataclass(order=True)
class QueueTask:
    """Task in the priority queue."""
    sort_key: tuple = field(compare=True, repr=False)
    task_id: str = field(compare=False)
    priority: Priority = field(compare=False)
    task_type: str = field(compare=False)
    target_id: str = field(compare=False)  # tweet_id, user_id, etc.
    payload: Dict[str, Any] = field(compare=False, default_factory=dict)
    created_at: float = field(compare=False, default_factory=time.time)
    expires_at: Optional[float] = field(compare=False, default=None)
    assigned_account_id: Optional[str] = field(compare=False, default=None)
    status: str = field(compare=False, default=TaskStatus.PENDING)
    attempts: int = field(compare=False, default=0)
    max_attempts: int = field(compare=False, default=3)
    metadata: Dict[str, Any] = field(compare=False, default_factory=dict)
    
    @classmethod
    def create(
        cls,
        priority: Priority,
        task_type: str,
        target_id: str,
        payload: Optional[Dict[str, Any]] = None,
        ttl_seconds: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> 'QueueTask':
        """Factory method to create a task with proper sort key."""
        task_id = str(uuid.uuid4())
        created_at = time.time()
        expires_at = created_at + ttl_seconds if ttl_seconds else None
        
        # Sort key: (priority, created_at) for FIFO within priority
        sort_key = (int(priority), created_at)
        
        return cls(
            sort_key=sort_key,
            task_id=task_id,
            priority=priority,
            task_type=task_type,
            target_id=target_id,
            payload=payload or {},
            created_at=created_at,
            expires_at=expires_at,
            metadata=metadata or {}
        )
    
    def is_expired(self) -> bool:
        """Check if task has expired."""
        if self.expires_at is None:
            return False
        return time.time() > self.expires_at
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "task_id": self.task_id,
            "priority": self.priority.value if isinstance(self.priority, Priority) else self.priority,
            "priority_name": Priority(self.priority).name if isinstance(self.priority, int) else self.priority.name,
            "task_type": self.task_type,
            "target_id": self.target_id,
            "payload": self.payload,
            "created_at": self.created_at,
            "created_at_iso": datetime.fromtimestamp(self.created_at).isoformat(),
            "expires_at": self.expires_at,
            "assigned_account_id": self.assigned_account_id,
            "status": self.status,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "metadata": self.metadata
        }


class QueueManager:
    """Priority queue manager for outreach tasks.
    
    Features:
    - P0-P4 priority levels
    - FIFO within same priority
    - Task expiration
    - Persistence to disk
    - Thread-safe operations
    - Queue statistics
    """
    
    def __init__(self, data_dir: Optional[str] = None):
        """Initialize queue manager.
        
        Args:
            data_dir: Directory for persistence. Defaults to ~/.marketing_dronor/queues/
        """
        self.data_dir = Path(data_dir) if data_dir else Path.home() / ".marketing_dronor" / "queues"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        # Priority queue (min-heap)
        self._queue: List[QueueTask] = []
        
        # Task lookup by ID
        self._tasks: Dict[str, QueueTask] = {}
        
        # Tasks by status
        self._by_status: Dict[str, set] = {
            TaskStatus.PENDING: set(),
            TaskStatus.IN_PROGRESS: set(),
            TaskStatus.COMPLETED: set(),
            TaskStatus.FAILED: set(),
            TaskStatus.CANCELLED: set(),
            TaskStatus.EXPIRED: set()
        }
        
        # Thread lock
        self._lock = threading.RLock()
        
        # Load persisted queue
        self._load_queue()
    
    def enqueue(
        self,
        priority: Priority,
        task_type: str,
        target_id: str,
        payload: Optional[Dict[str, Any]] = None,
        ttl_seconds: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> QueueTask:
        """Add task to queue.
        
        Args:
            priority: Task priority (P0-P4)
            task_type: Type of task (like, reply, etc.)
            target_id: Target identifier
            payload: Additional task data
            ttl_seconds: Time-to-live in seconds
            metadata: Extra metadata
            
        Returns:
            Created task
        """
        with self._lock:
            task = QueueTask.create(
                priority=priority,
                task_type=task_type,
                target_id=target_id,
                payload=payload,
                ttl_seconds=ttl_seconds,
                metadata=metadata
            )
            
            heapq.heappush(self._queue, task)
            self._tasks[task.task_id] = task
            self._by_status[TaskStatus.PENDING].add(task.task_id)
            
            self._save_queue()
            
            return task
    
    def enqueue_batch(
        self,
        tasks: List[Dict[str, Any]]
    ) -> List[QueueTask]:
        """Add multiple tasks to queue.
        
        Args:
            tasks: List of task definitions with keys:
                   priority, task_type, target_id, payload, ttl_seconds, metadata
        
        Returns:
            List of created tasks
        """
        created = []
        with self._lock:
            for task_def in tasks:
                task = QueueTask.create(
                    priority=Priority(task_def.get("priority", Priority.P2_MEDIUM)),
                    task_type=task_def.get("task_type", TaskType.LIKE),
                    target_id=task_def.get("target_id", ""),
                    payload=task_def.get("payload"),
                    ttl_seconds=task_def.get("ttl_seconds"),
                    metadata=task_def.get("metadata")
                )
                heapq.heappush(self._queue, task)
                self._tasks[task.task_id] = task
                self._by_status[TaskStatus.PENDING].add(task.task_id)
                created.append(task)
            
            self._save_queue()
        
        return created
    
    def dequeue(self, count: int = 1) -> List[QueueTask]:
        """Get next tasks from queue.
        
        Marks tasks as IN_PROGRESS.
        Skips expired tasks (marks them as EXPIRED).
        
        Args:
            count: Number of tasks to get
            
        Returns:
            List of tasks ready for processing
        """
        tasks = []
        with self._lock:
            while len(tasks) < count and self._queue:
                task = heapq.heappop(self._queue)
                
                # Check if expired
                if task.is_expired():
                    self._update_status(task.task_id, TaskStatus.EXPIRED)
                    continue
                
                # Check if already processed
                if task.status != TaskStatus.PENDING:
                    continue
                
                self._update_status(task.task_id, TaskStatus.IN_PROGRESS)
                tasks.append(task)
            
            self._save_queue()
        
        return tasks
    
    def peek(self, count: int = 1) -> List[QueueTask]:
        """Preview next tasks without removing.
        
        Args:
            count: Number of tasks to preview
            
        Returns:
            List of next tasks
        """
        with self._lock:
            # Get pending tasks sorted by priority
            pending = [
                t for t in self._queue 
                if t.status == TaskStatus.PENDING and not t.is_expired()
            ]
            return sorted(pending, key=lambda t: t.sort_key)[:count]
    
    def complete_task(self, task_id: str, result: Optional[Dict[str, Any]] = None) -> bool:
        """Mark task as completed.
        
        Args:
            task_id: Task identifier
            result: Optional result data
            
        Returns:
            True if updated successfully
        """
        with self._lock:
            if task_id not in self._tasks:
                return False
            
            task = self._tasks[task_id]
            if result:
                task.metadata["result"] = result
            task.metadata["completed_at"] = time.time()
            
            self._update_status(task_id, TaskStatus.COMPLETED)
            self._save_queue()
            
            return True
    
    def fail_task(
        self,
        task_id: str,
        error: str,
        requeue: bool = True
    ) -> bool:
        """Mark task as failed, optionally requeue.
        
        Args:
            task_id: Task identifier
            error: Error description
            requeue: Whether to requeue if attempts remain
            
        Returns:
            True if updated successfully
        """
        with self._lock:
            if task_id not in self._tasks:
                return False
            
            task = self._tasks[task_id]
            task.attempts += 1
            task.metadata["last_error"] = error
            task.metadata["last_failed_at"] = time.time()
            
            if requeue and task.attempts < task.max_attempts:
                # Requeue with same priority
                task.status = TaskStatus.PENDING
                self._by_status[TaskStatus.IN_PROGRESS].discard(task_id)
                self._by_status[TaskStatus.PENDING].add(task_id)
                # Re-add to heap
                heapq.heappush(self._queue, task)
            else:
                self._update_status(task_id, TaskStatus.FAILED)
            
            self._save_queue()
            return True
    
    def cancel_task(self, task_id: str, reason: str = "") -> bool:
        """Cancel a pending task.
        
        Args:
            task_id: Task identifier
            reason: Cancellation reason
            
        Returns:
            True if cancelled successfully
        """
        with self._lock:
            if task_id not in self._tasks:
                return False
            
            task = self._tasks[task_id]
            if task.status not in [TaskStatus.PENDING, TaskStatus.IN_PROGRESS]:
                return False
            
            task.metadata["cancelled_reason"] = reason
            task.metadata["cancelled_at"] = time.time()
            
            self._update_status(task_id, TaskStatus.CANCELLED)
            self._save_queue()
            
            return True
    
    def get_task(self, task_id: str) -> Optional[QueueTask]:
        """Get task by ID."""
        return self._tasks.get(task_id)
    
    def get_tasks_by_status(self, status: str) -> List[QueueTask]:
        """Get all tasks with given status."""
        with self._lock:
            task_ids = self._by_status.get(status, set())
            return [self._tasks[tid] for tid in task_ids if tid in self._tasks]
    
    def get_tasks_by_type(self, task_type: str) -> List[QueueTask]:
        """Get all pending tasks of given type."""
        with self._lock:
            return [
                t for t in self._tasks.values()
                if t.task_type == task_type and t.status == TaskStatus.PENDING
            ]
    
    def get_tasks_by_priority(self, priority: Priority) -> List[QueueTask]:
        """Get all pending tasks with given priority."""
        with self._lock:
            return [
                t for t in self._tasks.values()
                if t.priority == priority and t.status == TaskStatus.PENDING
            ]
    
    def assign_account(self, task_id: str, account_id: str) -> bool:
        """Assign account to task.
        
        Args:
            task_id: Task identifier
            account_id: Account to assign
            
        Returns:
            True if assigned successfully
        """
        with self._lock:
            if task_id not in self._tasks:
                return False
            
            self._tasks[task_id].assigned_account_id = account_id
            self._save_queue()
            return True
    
    def get_queue_stats(self) -> Dict[str, Any]:
        """Get comprehensive queue statistics."""
        with self._lock:
            # Clean expired first
            self._cleanup_expired()
            
            stats = {
                "total_tasks": len(self._tasks),
                "by_status": {},
                "by_priority": {},
                "by_type": {},
                "queue_depth": len([t for t in self._queue if t.status == TaskStatus.PENDING]),
                "oldest_pending": None,
                "avg_wait_time_seconds": 0.0
            }
            
            # By status
            for status, task_ids in self._by_status.items():
                stats["by_status"][status] = len(task_ids)
            
            # By priority
            priority_counts = {}
            for task in self._tasks.values():
                if task.status == TaskStatus.PENDING:
                    p_name = Priority(task.priority).name
                    priority_counts[p_name] = priority_counts.get(p_name, 0) + 1
            stats["by_priority"] = priority_counts
            
            # By type
            type_counts = {}
            for task in self._tasks.values():
                if task.status == TaskStatus.PENDING:
                    type_counts[task.task_type] = type_counts.get(task.task_type, 0) + 1
            stats["by_type"] = type_counts
            
            # Oldest pending
            pending = [t for t in self._tasks.values() if t.status == TaskStatus.PENDING]
            if pending:
                oldest = min(pending, key=lambda t: t.created_at)
                stats["oldest_pending"] = {
                    "task_id": oldest.task_id,
                    "created_at": datetime.fromtimestamp(oldest.created_at).isoformat(),
                    "wait_seconds": time.time() - oldest.created_at
                }
                
                # Average wait time
                wait_times = [time.time() - t.created_at for t in pending]
                stats["avg_wait_time_seconds"] = sum(wait_times) / len(wait_times)
            
            return stats
    
    def get_next_tasks(
        self,
        count: int = 10,
        task_type: Optional[str] = None,
        max_priority: Optional[Priority] = None
    ) -> List[Dict[str, Any]]:
        """Get next tasks ready for processing.
        
        Args:
            count: Maximum number of tasks
            task_type: Filter by task type
            max_priority: Only tasks with this priority or higher
            
        Returns:
            List of task dictionaries
        """
        with self._lock:
            self._cleanup_expired()
            
            # Filter pending tasks
            candidates = []
            for task in sorted(self._queue, key=lambda t: t.sort_key):
                if task.status != TaskStatus.PENDING:
                    continue
                if task.is_expired():
                    continue
                if task_type and task.task_type != task_type:
                    continue
                if max_priority and task.priority > max_priority:
                    continue
                candidates.append(task.to_dict())
                if len(candidates) >= count:
                    break
            
            return candidates
    
    def clear_completed(self, older_than_hours: int = 24) -> int:
        """Remove completed tasks older than threshold.
        
        Args:
            older_than_hours: Remove tasks completed more than this many hours ago
            
        Returns:
            Number of tasks removed
        """
        cutoff = time.time() - (older_than_hours * 3600)
        removed = 0
        
        with self._lock:
            to_remove = []
            for task_id in self._by_status[TaskStatus.COMPLETED]:
                task = self._tasks.get(task_id)
                if task and task.metadata.get("completed_at", 0) < cutoff:
                    to_remove.append(task_id)
            
            for task_id in to_remove:
                self._remove_task(task_id)
                removed += 1
            
            self._save_queue()
        
        return removed
    
    def prioritize_task(self, task_id: str, new_priority: Priority) -> bool:
        """Change task priority.
        
        Args:
            task_id: Task identifier
            new_priority: New priority level
            
        Returns:
            True if priority changed
        """
        with self._lock:
            if task_id not in self._tasks:
                return False
            
            task = self._tasks[task_id]
            if task.status != TaskStatus.PENDING:
                return False
            
            # Update priority and sort key
            task.priority = new_priority
            task.sort_key = (int(new_priority), task.created_at)
            task.metadata["priority_changed_at"] = time.time()
            task.metadata["original_priority"] = task.metadata.get("original_priority", task.priority)
            
            # Rebuild heap
            heapq.heapify(self._queue)
            self._save_queue()
            
            return True
    
    def _update_status(self, task_id: str, new_status: str):
        """Internal: Update task status."""
        if task_id not in self._tasks:
            return
        
        task = self._tasks[task_id]
        old_status = task.status
        task.status = new_status
        
        # Update status sets
        if old_status in self._by_status:
            self._by_status[old_status].discard(task_id)
        if new_status in self._by_status:
            self._by_status[new_status].add(task_id)
    
    def _remove_task(self, task_id: str):
        """Internal: Remove task completely."""
        if task_id in self._tasks:
            task = self._tasks[task_id]
            if task.status in self._by_status:
                self._by_status[task.status].discard(task_id)
            del self._tasks[task_id]
    
    def _cleanup_expired(self):
        """Internal: Mark expired tasks."""
        for task in list(self._queue):
            if task.status == TaskStatus.PENDING and task.is_expired():
                self._update_status(task.task_id, TaskStatus.EXPIRED)
    
    def _save_queue(self):
        """Persist queue to disk."""
        data = {
            "tasks": [
                {
                    **t.to_dict(),
                    "sort_key": list(t.sort_key)
                } for t in self._tasks.values()
            ],
            "saved_at": time.time()
        }
        
        queue_file = self.data_dir / "task_queue.json"
        with open(queue_file, "w") as f:
            json.dump(data, f, indent=2)
    
    def _load_queue(self):
        """Load queue from disk."""
        queue_file = self.data_dir / "task_queue.json"
        if not queue_file.exists():
            return
        
        try:
            with open(queue_file, "r") as f:
                data = json.load(f)
            
            for task_data in data.get("tasks", []):
                sort_key = tuple(task_data.get("sort_key", [2, time.time()]))
                task = QueueTask(
                    sort_key=sort_key,
                    task_id=task_data["task_id"],
                    priority=Priority(task_data["priority"]),
                    task_type=task_data["task_type"],
                    target_id=task_data["target_id"],
                    payload=task_data.get("payload", {}),
                    created_at=task_data["created_at"],
                    expires_at=task_data.get("expires_at"),
                    assigned_account_id=task_data.get("assigned_account_id"),
                    status=task_data.get("status", TaskStatus.PENDING),
                    attempts=task_data.get("attempts", 0),
                    max_attempts=task_data.get("max_attempts", 3),
                    metadata=task_data.get("metadata", {})
                )
                
                self._tasks[task.task_id] = task
                if task.status in self._by_status:
                    self._by_status[task.status].add(task.task_id)
                
                if task.status == TaskStatus.PENDING:
                    heapq.heappush(self._queue, task)
        
        except Exception as e:
            print(f"Error loading queue: {e}")


# Convenience function for expert usage
def manage_queue(
    action: str = "stats",
    priority: int = 2,
    task_type: str = "",
    target_id: str = "",
    task_id: str = "",
    payload: Optional[Dict[str, Any]] = None,
    count: int = 10,
    data_dir: Optional[str] = None
) -> Dict[str, Any]:
    """Manage task queue.
    
    Args:
        action: Operation - "enqueue", "dequeue", "peek", "complete", "fail", 
                "cancel", "stats", "next_tasks", "get_task"
        priority: Task priority (0-4)
        task_type: Type of task
        target_id: Target identifier
        task_id: Task ID for operations on existing tasks
        payload: Task payload data
        count: Number of tasks for batch operations
        data_dir: Custom data directory
        
    Returns:
        Operation result
    """
    qm = QueueManager(data_dir=data_dir)
    
    if action == "enqueue":
        if not task_type or not target_id:
            return {"error": "task_type and target_id required"}
        task = qm.enqueue(
            priority=Priority(priority),
            task_type=task_type,
            target_id=target_id,
            payload=payload
        )
        return {"status": "success", "task": task.to_dict()}
    
    elif action == "dequeue":
        tasks = qm.dequeue(count=count)
        return {
            "status": "success",
            "tasks": [t.to_dict() for t in tasks],
            "count": len(tasks)
        }
    
    elif action == "peek":
        tasks = qm.peek(count=count)
        return {
            "status": "success",
            "tasks": [t.to_dict() for t in tasks],
            "count": len(tasks)
        }
    
    elif action == "complete":
        if not task_id:
            return {"error": "task_id required"}
        success = qm.complete_task(task_id, result=payload)
        return {"status": "success" if success else "failed", "task_id": task_id}
    
    elif action == "fail":
        if not task_id:
            return {"error": "task_id required"}
        error_msg = payload.get("error", "Unknown error") if payload else "Unknown error"
        success = qm.fail_task(task_id, error=error_msg)
        return {"status": "success" if success else "failed", "task_id": task_id}
    
    elif action == "cancel":
        if not task_id:
            return {"error": "task_id required"}
        reason = payload.get("reason", "") if payload else ""
        success = qm.cancel_task(task_id, reason=reason)
        return {"status": "success" if success else "failed", "task_id": task_id}
    
    elif action == "stats":
        return {"status": "success", "stats": qm.get_queue_stats()}
    
    elif action == "next_tasks":
        task_filter = task_type if task_type else None
        tasks = qm.get_next_tasks(count=count, task_type=task_filter)
        return {"status": "success", "tasks": tasks, "count": len(tasks)}
    
    elif action == "get_task":
        if not task_id:
            return {"error": "task_id required"}
        task = qm.get_task(task_id)
        if task:
            return {"status": "success", "task": task.to_dict()}
        return {"status": "not_found", "task_id": task_id}
    
    else:
        return {"error": f"Unknown action: {action}"}
