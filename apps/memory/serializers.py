from rest_framework import serializers

from .models import BehavioralMetric, MemoryNote, UserPreference


class UserPreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserPreference
        fields = [
            "wake_up_time", "sleep_time",
            "preferred_focus_start", "preferred_focus_end",
            "preferred_gym_days", "preferred_gym_time", "gym_duration_minutes",
            "max_meetings_per_day", "preferred_break_duration",
            "lunch_start", "lunch_duration_minutes",
            "preferred_task_block_minutes", "updated_at",
        ]
        read_only_fields = ["updated_at"]


class MemoryNoteSerializer(serializers.ModelSerializer):
    class Meta:
        model = MemoryNote
        fields = [
            "id", "content", "category", "relevance_score",
            "expires_at", "is_active", "source", "created_at",
        ]
        read_only_fields = ["id", "created_at"]


class BehavioralMetricSerializer(serializers.ModelSerializer):
    class Meta:
        model = BehavioralMetric
        fields = [
            "id", "metric_name", "metric_value",
            "period_start", "period_end", "created_at",
        ]
        read_only_fields = ["id", "created_at"]