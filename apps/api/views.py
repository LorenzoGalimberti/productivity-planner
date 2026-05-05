import logging
from datetime import date, timedelta

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from apps.memory.models import MemoryNote, UserPreference
from apps.memory.serializers import (
    MemoryNoteSerializer,
    UserPreferenceSerializer,
)
from apps.planner.models import CalendarEvent, PlanningSession, Task
from apps.planner.serializers import (
    CalendarEventCreateSerializer,
    CalendarEventSerializer,
    PlanDayRequestSerializer,
    PlanningSessionSerializer,
    PlanWeekRequestSerializer,
    ReplanRequestSerializer,
    TaskCreateSerializer,
    TaskSerializer,
)

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Health
# ──────────────────────────────────────────────

@api_view(["GET"])
@permission_classes([AllowAny])
def health_check(request: Request) -> Response:
    return Response({"status": "ok", "service": "productivity-planner-agent"})


# ──────────────────────────────────────────────
# Tasks
# ──────────────────────────────────────────────

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def task_list(request: Request) -> Response:
    """List all tasks for the authenticated user."""
    tasks = Task.objects.filter(user=request.user)

    # Optional filters
    task_status = request.query_params.get("status")
    if task_status:
        tasks = tasks.filter(status=task_status)

    priority = request.query_params.get("priority")
    if priority:
        tasks = tasks.filter(priority=priority)

    serializer = TaskSerializer(tasks, many=True)
    return Response(serializer.data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def task_create(request: Request) -> Response:
    """Create a new task."""
    serializer = TaskCreateSerializer(data=request.data, context={"request": request})
    if serializer.is_valid():
        task = serializer.save()
        logger.info("Task created: %s (user=%s)", task.title, request.user.username)
        return Response(TaskSerializer(task).data, status=status.HTTP_201_CREATED)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(["PATCH"])
@permission_classes([IsAuthenticated])
def task_update(request: Request, task_id: str) -> Response:
    """Update an existing task."""
    try:
        task = Task.objects.get(id=task_id, user=request.user)
    except Task.DoesNotExist:
        return Response({"error": "Task not found"}, status=status.HTTP_404_NOT_FOUND)

    serializer = TaskSerializer(task, data=request.data, partial=True)
    if serializer.is_valid():
        serializer.save()
        return Response(serializer.data)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def task_delete(request: Request, task_id: str) -> Response:
    """Delete a task."""
    try:
        task = Task.objects.get(id=task_id, user=request.user)
    except Task.DoesNotExist:
        return Response({"error": "Task not found"}, status=status.HTTP_404_NOT_FOUND)

    task.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)


# ──────────────────────────────────────────────
# Calendar Events
# ──────────────────────────────────────────────

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def calendar_event_list(request: Request) -> Response:
    """List calendar events, optionally filtered by date range."""
    events = CalendarEvent.objects.filter(user=request.user)

    date_from = request.query_params.get("from")
    date_to = request.query_params.get("to")
    if date_from:
        events = events.filter(start_time__date__gte=date_from)
    if date_to:
        events = events.filter(start_time__date__lte=date_to)

    serializer = CalendarEventSerializer(events, many=True)
    return Response(serializer.data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def calendar_event_create(request: Request) -> Response:
    """Create a new calendar event."""
    serializer = CalendarEventCreateSerializer(
        data=request.data, context={"request": request}
    )
    if serializer.is_valid():
        event = serializer.save()
        logger.info("Event created: %s (user=%s)", event.title, request.user.username)
        return Response(
            CalendarEventSerializer(event).data, status=status.HTTP_201_CREATED
        )
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# ──────────────────────────────────────────────
# Preferences
# ──────────────────────────────────────────────

@api_view(["GET", "PATCH"])
@permission_classes([IsAuthenticated])
def user_preferences(request: Request) -> Response:
    """Get or update user preferences."""
    prefs, _created = UserPreference.objects.get_or_create(user=request.user)

    if request.method == "GET":
        serializer = UserPreferenceSerializer(prefs)
        return Response(serializer.data)

    # PATCH
    serializer = UserPreferenceSerializer(prefs, data=request.data, partial=True)
    if serializer.is_valid():
        serializer.save()
        return Response(serializer.data)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# ──────────────────────────────────────────────
# Memory Notes
# ──────────────────────────────────────────────

@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def memory_notes(request: Request) -> Response:
    """List or create memory notes."""
    if request.method == "GET":
        notes = MemoryNote.objects.filter(user=request.user, is_active=True)
        serializer = MemoryNoteSerializer(notes, many=True)
        return Response(serializer.data)

    # POST
    serializer = MemoryNoteSerializer(data=request.data)
    if serializer.is_valid():
        serializer.save(user=request.user)
        return Response(serializer.data, status=status.HTTP_201_CREATED)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# ──────────────────────────────────────────────
# Planning (stub — will call agent in Step 5)
# ──────────────────────────────────────────────

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def plan_day(request: Request) -> Response:
    """Generate or modify a day plan using the AI agent."""
    from apps.agents.workflow import run_planning_agent

    serializer = PlanDayRequestSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    target = serializer.validated_data.get("target_date") or (date.today() + timedelta(days=1))
    message = serializer.validated_data["message"]
    session_id = request.data.get("session_id")

    # Load previous schedule if this is a follow-up
    previous_schedule = None
    if session_id:
        try:
            prev_session = PlanningSession.objects.get(id=session_id, user=request.user)
            previous_schedule = prev_session.result_json
            logger.info("Loaded previous session %s for follow-up", session_id)
        except PlanningSession.DoesNotExist:
            logger.warning("Session %s not found, generating fresh plan", session_id)

    result = run_planning_agent(
        user=request.user,
        message=message,
        intent="day_plan",
        target_date=target,
        plan_type="day",
        previous_schedule=previous_schedule,
    )

    logger.info("Plan day completed for user=%s", request.user.username)
    return Response(result, status=status.HTTP_200_OK)

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def plan_week(request: Request) -> Response:
    """Generate a week plan using the AI agent."""
    from apps.agents.workflow import run_planning_agent

    serializer = PlanWeekRequestSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    start = serializer.validated_data.get("start_date") or (
        date.today() + timedelta(days=(7 - date.today().weekday()))
    )
    message = serializer.validated_data["message"]

    result = run_planning_agent(
        user=request.user,
        message=message,
        intent="week_plan",
        target_date=start,
        plan_type="week",
    )

    logger.info("Plan week completed for user=%s", request.user.username)
    return Response(result, status=status.HTTP_200_OK)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def replan(request: Request) -> Response:
    """Replan/reschedule using the AI agent."""
    from apps.agents.workflow import run_planning_agent

    serializer = ReplanRequestSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    message = serializer.validated_data["message"]

    result = run_planning_agent(
        user=request.user,
        message=message,
        intent="replan",
        plan_type="replan",
    )

    logger.info("Replan completed for user=%s", request.user.username)
    return Response(result, status=status.HTTP_200_OK)
# ──────────────────────────────────────────────
# Planning History
# ──────────────────────────────────────────────

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def planning_history(request: Request) -> Response:
    """List past planning sessions."""
    sessions = PlanningSession.objects.filter(user=request.user)[:20]
    serializer = PlanningSessionSerializer(sessions, many=True)
    return Response(serializer.data)