from rest_framework import serializers

from .models import CalendarEvent, PlanningSession, Task


class TaskSerializer(serializers.ModelSerializer):
    class Meta:
        model = Task
        fields = [
            "id", "title", "description", "priority", "status",
            "estimated_minutes", "due_date", "due_time", "category",
            "is_recurring", "recurrence_rule", "completed_at",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class TaskCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Task
        fields = [
            "title", "description", "priority", "estimated_minutes",
            "due_date", "due_time", "category", "is_recurring",
            "recurrence_rule",
        ]

    def create(self, validated_data: dict) -> Task:
        validated_data["user"] = self.context["request"].user
        return super().create(validated_data)


class CalendarEventSerializer(serializers.ModelSerializer):
    duration_minutes = serializers.ReadOnlyField()

    class Meta:
        model = CalendarEvent
        fields = [
            "id", "title", "description", "event_type",
            "start_time", "end_time", "duration_minutes",
            "is_fixed", "is_all_day", "location", "source",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class CalendarEventCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = CalendarEvent
        fields = [
            "title", "description", "event_type",
            "start_time", "end_time", "is_fixed",
            "is_all_day", "location",
        ]

    def validate(self, data: dict) -> dict:
        if data.get("start_time") and data.get("end_time"):
            if data["start_time"] >= data["end_time"]:
                raise serializers.ValidationError(
                    "end_time must be after start_time."
                )
        return data

    def create(self, validated_data: dict) -> CalendarEvent:
        validated_data["user"] = self.context["request"].user
        return super().create(validated_data)


class PlanningSessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = PlanningSession
        fields = [
            "id", "plan_type", "user_prompt", "result_json",
            "reasoning", "warnings", "target_date",
            "llm_model", "tokens_used", "duration_ms", "created_at",
        ]
        read_only_fields = fields


# --- Request serializers for plan endpoints ---

class PlanDayRequestSerializer(serializers.Serializer):
    message = serializers.CharField(
        help_text="e.g. 'Plan my day tomorrow' or 'I have gym, study and meetings'"
    )
    target_date = serializers.DateField(
        required=False,
        help_text="Date to plan for (defaults to tomorrow)",
    )


class PlanWeekRequestSerializer(serializers.Serializer):
    message = serializers.CharField(
        help_text="e.g. 'Organize my week for maximum productivity'"
    )
    start_date = serializers.DateField(
        required=False,
        help_text="Week start date (defaults to next Monday)",
    )


class ReplanRequestSerializer(serializers.Serializer):
    message = serializers.CharField(
        help_text="e.g. 'Reschedule everything, I am sick'"
    )
    reason = serializers.CharField(
        required=False, default="",
        help_text="Reason for replanning",
    )
    keep_fixed_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False, default=list,
        help_text="Event IDs that should not be moved",
    )