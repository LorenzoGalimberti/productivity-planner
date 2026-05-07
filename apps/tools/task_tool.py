"""
Task tool — reads/writes Task from DB.
Designed to be swapped with Todoist / Notion API later.
"""

import logging
from datetime import date, datetime, timedelta
from typing import Optional

from django.db.models import Q
from django.utils import timezone

from apps.planner.models import Task
from apps.users.models import User

logger = logging.getLogger(__name__)


def get_tasks(
    user: User,
    status_filter: Optional[str] = None,
    priority_filter: Optional[str] = None,
    due_date: Optional[date] = None,
) -> list[dict]:
    """Fetch tasks for a user with optional filters."""
    qs = Task.objects.filter(user=user)

    if status_filter:
        qs = qs.filter(status=status_filter)
    else:
        # By default, exclude done and cancelled
        qs = qs.exclude(status__in=["done", "cancelled"])

    if priority_filter:
        qs = qs.filter(priority=priority_filter)

    if due_date:
        # Tasks for this specific date + open tasks (no due date)
        qs = qs.filter(Q(due_date=due_date) | Q(due_date__isnull=True))

    result = []
    for t in qs:
        result.append({
            "id": str(t.id),
            "title": t.title,
            "description": t.description,
            "priority": t.priority,
            "status": t.status,
            "estimated_minutes": t.estimated_minutes,
            "due_date": t.due_date.isoformat() if t.due_date else None,
            "category": t.category,
        })

    logger.info("Fetched %d tasks for user=%s", len(result), user.username)
    return result


def create_task(
    user: User,
    title: str,
    priority: str = "medium",
    estimated_minutes: Optional[int] = None,
    due_date: Optional[date] = None,
    category: str = "",
    description: str = "",
) -> dict:
    """Create a new task."""
    task = Task.objects.create(
        user=user,
        title=title,
        priority=priority,
        estimated_minutes=estimated_minutes,
        due_date=due_date,
        category=category,
        description=description,
    )
    logger.info("Created task '%s' for user=%s", title, user.username)
    return {
        "id": str(task.id),
        "title": task.title,
        "priority": task.priority,
        "status": task.status,
    }


def complete_task(user: User, task_id: str) -> dict:
    """Mark a task as completed."""
    try:
        task = Task.objects.get(id=task_id, user=user)
    except Task.DoesNotExist:
        return {"error": f"Task {task_id} not found"}

    task.status = Task.Status.DONE
    task.completed_at = timezone.now()
    task.save()

    logger.info("Completed task '%s' for user=%s", task.title, user.username)
    return {"id": str(task.id), "title": task.title, "status": "done"}


def postpone_task(user: User, task_id: str, new_due_date: date) -> dict:
    """Postpone a task to a new due date."""
    try:
        task = Task.objects.get(id=task_id, user=user)
    except Task.DoesNotExist:
        return {"error": f"Task {task_id} not found"}

    old_date = task.due_date
    task.due_date = new_due_date
    task.status = Task.Status.POSTPONED
    task.save()

    logger.info(
        "Postponed task '%s' from %s to %s",
        task.title, old_date, new_due_date,
    )
    return {
        "id": str(task.id),
        "title": task.title,
        "old_due_date": old_date.isoformat() if old_date else None,
        "new_due_date": new_due_date.isoformat(),
    }


def expire_old_tasks(days_threshold: int = 3) -> int:
    """
    Auto-expire tasks whose due_date has passed by more than X days.
    Marks them as cancelled so they don't pollute future plans.
    """
    cutoff = date.today() - timedelta(days=days_threshold)
    old_tasks = Task.objects.filter(
        status__in=["todo", "in_progress"],
        due_date__lt=cutoff,
    )
    count = old_tasks.update(status="cancelled")
    if count:
        logger.info("Auto-expired %d old tasks (due before %s)", count, cutoff)
    return count