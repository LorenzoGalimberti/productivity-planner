from django.contrib import admin

from .models import CalendarEvent, PlanningSession, Task


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "priority", "status", "due_date", "estimated_minutes")
    list_filter = ("priority", "status", "category")
    search_fields = ("title", "description")
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(CalendarEvent)
class CalendarEventAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "event_type", "start_time", "end_time", "is_fixed")
    list_filter = ("event_type", "is_fixed", "source")
    search_fields = ("title",)
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(PlanningSession)
class PlanningSessionAdmin(admin.ModelAdmin):
    list_display = ("user", "plan_type", "target_date", "tokens_used", "duration_ms", "created_at")
    list_filter = ("plan_type",)
    readonly_fields = ("id", "created_at")