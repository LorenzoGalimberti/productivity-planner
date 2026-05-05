"""
Django signals — auto-update behavioral metrics when tasks change status.
"""

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.planner.models import Task

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Task)
def on_task_status_change(sender, instance: Task, **kwargs) -> None:
    """Recalculate metrics when a task is completed, postponed, or cancelled."""
    if not kwargs.get("update_fields") and instance.status in ("done", "postponed", "cancelled"):
        try:
            from apps.memory.services import calculate_metrics
            calculate_metrics(instance.user)
            logger.info(
                "Metrics recalculated after task '%s' → %s",
                instance.title, instance.status,
            )
        except Exception as e:
            logger.exception("Failed to recalculate metrics: %s", e)