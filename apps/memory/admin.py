from django.contrib import admin

from .models import BehavioralMetric, MemoryNote, UserPreference


@admin.register(UserPreference)
class UserPreferenceAdmin(admin.ModelAdmin):
    list_display = ("user", "wake_up_time", "sleep_time", "max_meetings_per_day")
    readonly_fields = ("updated_at",)


@admin.register(BehavioralMetric)
class BehavioralMetricAdmin(admin.ModelAdmin):
    list_display = ("user", "metric_name", "metric_value", "period_start", "period_end")
    list_filter = ("metric_name",)
    readonly_fields = ("id", "created_at")


@admin.register(MemoryNote)
class MemoryNoteAdmin(admin.ModelAdmin):
    list_display = ("user", "category", "content_short", "relevance_score", "is_active")
    list_filter = ("category", "is_active")
    search_fields = ("content",)
    readonly_fields = ("id", "created_at", "updated_at")

    @admin.display(description="Content")
    def content_short(self, obj: MemoryNote) -> str:
        return obj.content[:80] + "..." if len(obj.content) > 80 else obj.content