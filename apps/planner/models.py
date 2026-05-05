import uuid

from django.conf import settings
from django.db import models


class Task(models.Model):
    """A task the user needs to complete."""

    class Priority(models.TextChoices):
        URGENT = "urgent", "Urgent"
        HIGH = "high", "High"
        MEDIUM = "medium", "Medium"
        LOW = "low", "Low"
        OPTIONAL = "optional", "Optional"

    class Status(models.TextChoices):
        TODO = "todo", "To Do"
        IN_PROGRESS = "in_progress", "In Progress"
        DONE = "done", "Done"
        POSTPONED = "postponed", "Postponed"
        CANCELLED = "cancelled", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="tasks",
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    priority = models.CharField(
        max_length=20, choices=Priority.choices, default=Priority.MEDIUM
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.TODO
    )
    estimated_minutes = models.PositiveIntegerField(
        null=True, blank=True, help_text="Estimated duration in minutes"
    )
    due_date = models.DateField(null=True, blank=True)
    due_time = models.TimeField(null=True, blank=True)
    category = models.CharField(max_length=100, blank=True, default="")
    is_recurring = models.BooleanField(default=False)
    recurrence_rule = models.CharField(
        max_length=100, blank=True, default="",
        help_text="e.g. daily, weekly, MWF",
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tasks"
        ordering = ["due_date", "-priority"]

    def __str__(self) -> str:
        return f"{self.title} ({self.get_priority_display()})"


class CalendarEvent(models.Model):
    """A calendar event (meeting, appointment, block)."""

    class EventType(models.TextChoices):
        MEETING = "meeting", "Meeting"
        FOCUS = "focus", "Focus Block"
        BREAK = "break", "Break"
        GYM = "gym", "Gym"
        PERSONAL = "personal", "Personal"
        ERRAND = "errand", "Errand"
        STUDY = "study", "Study"
        OTHER = "other", "Other"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="calendar_events",
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    event_type = models.CharField(
        max_length=20, choices=EventType.choices, default=EventType.OTHER
    )
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    is_fixed = models.BooleanField(
        default=False, help_text="Cannot be moved by the planner"
    )
    is_all_day = models.BooleanField(default=False)
    location = models.CharField(max_length=255, blank=True, default="")
    source = models.CharField(
        max_length=50, blank=True, default="manual",
        help_text="manual, google_calendar, agent",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "calendar_events"
        ordering = ["start_time"]

    def __str__(self) -> str:
        return f"{self.title} ({self.start_time:%Y-%m-%d %H:%M})"

    @property
    def duration_minutes(self) -> int:
        delta = self.end_time - self.start_time
        return int(delta.total_seconds() / 60)


class PlanningSession(models.Model):
    """Records each planning interaction with the agent."""

    class PlanType(models.TextChoices):
        DAY = "day", "Day Plan"
        WEEK = "week", "Week Plan"
        REPLAN = "replan", "Replan"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="planning_sessions",
    )
    plan_type = models.CharField(
        max_length=20, choices=PlanType.choices, default=PlanType.DAY
    )
    user_prompt = models.TextField(help_text="Original user request")
    result_json = models.JSONField(
        null=True, blank=True, help_text="Generated schedule as JSON"
    )
    reasoning = models.TextField(blank=True, default="")
    warnings = models.JSONField(default=list, blank=True)
    target_date = models.DateField(null=True, blank=True)
    llm_model = models.CharField(max_length=50, blank=True, default="")
    tokens_used = models.PositiveIntegerField(default=0)
    duration_ms = models.PositiveIntegerField(
        default=0, help_text="Agent execution time in milliseconds"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "planning_sessions"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Plan [{self.get_plan_type_display()}] - {self.created_at:%Y-%m-%d %H:%M}"