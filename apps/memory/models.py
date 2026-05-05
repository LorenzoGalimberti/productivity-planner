import uuid

from django.conf import settings
from django.db import models


class UserPreference(models.Model):
    """Persistent user preferences the agent reads before planning."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="preferences",
    )

    # Schedule boundaries
    wake_up_time = models.TimeField(
        default="07:00", help_text="Usual wake-up time"
    )
    sleep_time = models.TimeField(
        default="23:00", help_text="Usual bedtime"
    )

    # Focus & productivity
    preferred_focus_start = models.TimeField(
        default="09:00", help_text="Start of best focus window"
    )
    preferred_focus_end = models.TimeField(
        default="12:00", help_text="End of best focus window"
    )

    # Lifestyle
    preferred_gym_days = models.CharField(
        max_length=50, blank=True, default="mon,wed,fri",
        help_text="Comma-separated: mon,tue,wed,thu,fri,sat,sun",
    )
    preferred_gym_time = models.TimeField(
        null=True, blank=True, default="17:00",
    )
    gym_duration_minutes = models.PositiveIntegerField(default=60)

    # Meetings & breaks
    max_meetings_per_day = models.PositiveIntegerField(default=4)
    preferred_break_duration = models.PositiveIntegerField(
        default=15, help_text="Break duration in minutes"
    )
    lunch_start = models.TimeField(default="12:30")
    lunch_duration_minutes = models.PositiveIntegerField(default=60)

    # Work style
    preferred_task_block_minutes = models.PositiveIntegerField(
        default=90, help_text="Ideal task block length"
    )

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "user_preferences"

    def __str__(self) -> str:
        return f"Preferences for {self.user.username}"


class BehavioralMetric(models.Model):
    """Tracks user behavior over time so the agent can learn patterns."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="behavioral_metrics",
    )
    metric_name = models.CharField(
        max_length=100,
        help_text="e.g. task_completion_rate, avg_postponements, focus_adherence",
    )
    metric_value = models.FloatField()
    period_start = models.DateField()
    period_end = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "behavioral_metrics"
        unique_together = ["user", "metric_name", "period_start"]
        ordering = ["-period_start"]

    def __str__(self) -> str:
        return f"{self.user.username} | {self.metric_name}: {self.metric_value}"


class MemoryNote(models.Model):
    """Semantic notes about the user (facts the agent should remember)."""

    class NoteCategory(models.TextChoices):
        GOAL = "goal", "Goal"
        CONSTRAINT = "constraint", "Constraint"
        PREFERENCE = "preference", "Preference"
        CONTEXT = "context", "Context"
        HABIT = "habit", "Habit"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="memory_notes",
    )
    content = models.TextField(
        help_text="e.g. 'Preparing for AI exam next week'"
    )
    category = models.CharField(
        max_length=20, choices=NoteCategory.choices, default=NoteCategory.CONTEXT
    )
    relevance_score = models.FloatField(
        default=1.0, help_text="0.0-1.0, decays over time"
    )
    expires_at = models.DateField(
        null=True, blank=True,
        help_text="Note becomes irrelevant after this date",
    )
    is_active = models.BooleanField(default=True)
    source = models.CharField(
        max_length=50, default="agent",
        help_text="agent, user, system",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "memory_notes"
        ordering = ["-relevance_score", "-created_at"]

    def __str__(self) -> str:
        return f"[{self.get_category_display()}] {self.content[:60]}"