"""
Agent tools - defines tools for OpenAI function calling.
Each tool wraps a Django service function.
"""

import json
import logging
from datetime import date, datetime, timedelta
from django.utils import timezone as django_tz

from apps.memory.models import MemoryNote, UserPreference
from apps.memory.services import get_memory_context
from apps.planner.models import CalendarEvent, Task
from apps.tools.calendar_tool import (
    create_calendar_event,
    delete_calendar_event,
    get_calendar_events,
    move_calendar_event,
)
from apps.tools.task_tool import complete_task, create_task, get_tasks
from apps.tools.time_utils import find_free_blocks
from apps.users.models import User

logger = logging.getLogger(__name__)


# -------------------------------------------------
# Tool definitions for OpenAI function calling
# -------------------------------------------------

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "get_calendar_events",
            "description": "Get calendar events for a date range. Call this first to see what is already scheduled.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date_from": {
                        "type": "string",
                        "description": "Start date in YYYY-MM-DD format",
                    },
                    "date_to": {
                        "type": "string",
                        "description": "End date in YYYY-MM-DD format (optional, defaults to date_from)",
                    },
                },
                "required": ["date_from"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_calendar_event",
            "description": "Create a fixed calendar event with a specific start and end time.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Event title"},
                    "start_time": {"type": "string", "description": "Start time in HH:MM format"},
                    "end_time": {"type": "string", "description": "End time in HH:MM format"},
                    "date": {"type": "string", "description": "Date in YYYY-MM-DD format"},
                    "event_type": {
                        "type": "string",
                        "description": "Type: meeting, focus, break, gym, personal, errand, study, other",
                    },
                },
                "required": ["title", "start_time", "end_time", "date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "move_calendar_event",
            "description": "Move an existing calendar event to a new time. Use the event ID from get_calendar_events.",
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {"type": "string", "description": "UUID of the event to move"},
                    "new_start": {"type": "string", "description": "New start time in HH:MM format"},
                    "new_end": {"type": "string", "description": "New end time in HH:MM format"},
                    "date": {"type": "string", "description": "Date in YYYY-MM-DD format"},
                },
                "required": ["event_id", "new_start", "new_end", "date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_calendar_event",
            "description": "Delete a calendar event by ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {"type": "string", "description": "UUID of the event to delete"},
                },
                "required": ["event_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_tasks",
            "description": "Get pending tasks for the user. Returns tasks that are not done or cancelled.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_task",
            "description": "Create a new task for the user.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Task title"},
                    "priority": {
                        "type": "string",
                        "description": "Priority: urgent, high, medium, low, optional",
                    },
                    "estimated_minutes": {
                        "type": "integer",
                        "description": "Estimated duration in minutes",
                    },
                    "due_date": {"type": "string", "description": "Due date in YYYY-MM-DD format"},
                    "category": {"type": "string", "description": "Category: work, study, gym, personal, errand, meeting"},
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "complete_task",
            "description": "Mark a task as completed by ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "UUID of the task to complete"},
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_user_preferences",
            "description": "Get user preferences: wake/sleep time, focus hours, gym days, lunch time, etc.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_memory_notes",
            "description": "Get memory notes about the user: goals, preferences, habits, constraints.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_memory_note",
            "description": "Save an important fact about the user for future planning. Only save meaningful long-term facts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "The fact to remember"},
                    "category": {
                        "type": "string",
                        "description": "Category: preference, goal, constraint, context, habit",
                    },
                    "expires_days": {
                        "type": "integer",
                        "description": "Days until this fact expires. Null for permanent facts.",
                    },
                },
                "required": ["content", "category"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_free_blocks",
            "description": "Find free time blocks in a day, given the existing calendar events.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {"type": "string", "description": "Date in YYYY-MM-DD format"},
                },
                "required": ["date"],
            },
        },
    },
]


# -------------------------------------------------
# Tool executor - runs the actual function
# -------------------------------------------------

def execute_tool(user: User, tool_name: str, arguments: dict) -> str:
    """Execute a tool and return the result as a string."""
    try:
        if tool_name == "get_calendar_events":
            date_from = date.fromisoformat(arguments["date_from"])
            date_to = date.fromisoformat(arguments.get("date_to", arguments["date_from"]))
            result = get_calendar_events(user, date_from, date_to)

        elif tool_name == "create_calendar_event":
            target_date = date.fromisoformat(arguments["date"])
            # Handle both "HH:MM" and "YYYY-MM-DD HH:MM" formats
            start_str = arguments["start_time"]
            end_str = arguments["end_time"]
            if " " in start_str:
                start_str = start_str.split(" ")[-1]
            if " " in end_str:
                end_str = end_str.split(" ")[-1]
            start_dt = django_tz.make_aware(datetime.combine(
                target_date,
                datetime.strptime(start_str, "%H:%M").time(),
            ))
            end_dt = django_tz.make_aware(datetime.combine(
                target_date,
                datetime.strptime(end_str, "%H:%M").time(),
            ))
            result = create_calendar_event(
                user=user,
                title=arguments["title"],
                start_time=start_dt,
                end_time=end_dt,
                event_type=arguments.get("event_type", "other"),
                is_fixed=False,
                source="agent",
            )

        elif tool_name == "move_calendar_event":
            target_date = date.fromisoformat(arguments["date"])
            # Handle both "HH:MM" and "YYYY-MM-DD HH:MM" formats
            new_start_str = arguments["new_start"]
            new_end_str = arguments["new_end"]
            if " " in new_start_str:
                new_start_str = new_start_str.split(" ")[-1]
            if " " in new_end_str:
                new_end_str = new_end_str.split(" ")[-1]
            new_start = django_tz.make_aware(datetime.combine(
                target_date,
                datetime.strptime(new_start_str, "%H:%M").time(),
            ))
            new_end = django_tz.make_aware(datetime.combine(
                target_date,
                datetime.strptime(new_end_str, "%H:%M").time(),
            ))
            result = move_calendar_event(user, arguments["event_id"], new_start, new_end)

        elif tool_name == "delete_calendar_event":
            result = delete_calendar_event(user, arguments["event_id"])

        elif tool_name == "get_tasks":
            result = get_tasks(user)

        elif tool_name == "create_task":
            result = create_task(
                user=user,
                title=arguments["title"],
                priority=arguments.get("priority", "medium"),
                estimated_minutes=arguments.get("estimated_minutes"),
                due_date=date.fromisoformat(arguments["due_date"]) if arguments.get("due_date") else None,
                category=arguments.get("category", ""),
            )

        elif tool_name == "complete_task":
            result = complete_task(user, arguments["task_id"])

        elif tool_name == "get_user_preferences":
            memory = get_memory_context(user)
            prefs = memory.get("preferences", {})
            metrics = memory.get("behavioral_metrics", {})
            clean_prefs = {}
            for k, v in prefs.items():
                if hasattr(v, "strftime"):
                    clean_prefs[k] = v.strftime("%H:%M")
                else:
                    clean_prefs[k] = v
            result = {"preferences": clean_prefs, "behavioral_metrics": metrics}

        elif tool_name == "get_memory_notes":
            memory = get_memory_context(user)
            result = {
                "episodic_notes": memory.get("episodic_notes", []),
                "semantic_notes": memory.get("semantic_notes", []),
            }
            for note_list in [result["episodic_notes"], result["semantic_notes"]]:
                for note in note_list:
                    for k, v in note.items():
                        if hasattr(v, "isoformat"):
                            note[k] = v.isoformat()

        elif tool_name == "save_memory_note":
            content = arguments["content"]
            category = arguments.get("category", "context")
            expires_days = arguments.get("expires_days")

            existing = MemoryNote.objects.filter(
                user=user, content__icontains=content[:30], is_active=True
            ).exists()

            if existing:
                result = {"status": "skipped", "reason": "duplicate"}
            else:
                expires_at = None
                if expires_days:
                    expires_at = date.today() + timedelta(days=int(expires_days))

                note = MemoryNote.objects.create(
                    user=user,
                    content=content,
                    category=category,
                    relevance_score=1.0,
                    expires_at=expires_at,
                    source="agent",
                )
                result = {"status": "saved", "content": content, "category": category}

        elif tool_name == "find_free_blocks":
            target_date = date.fromisoformat(arguments["date"])
            events = get_calendar_events(user, target_date)
            try:
                prefs = UserPreference.objects.get(user=user)
                wake = prefs.wake_up_time
                sleep = prefs.sleep_time
            except UserPreference.DoesNotExist:
                from datetime import time
                wake = time(7, 0)
                sleep = time(23, 0)
            result = find_free_blocks(events, wake, sleep, target_date)

        else:
            result = {"error": f"Unknown tool: {tool_name}"}

        logger.info("Tool '%s' executed successfully", tool_name)
        return json.dumps(result, default=str)

    except Exception as e:
        logger.exception("Tool '%s' failed: %s", tool_name, e)
        return json.dumps({"error": str(e)})