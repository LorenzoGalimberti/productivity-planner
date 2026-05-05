"""
Tool registry — central index of all available tools.
The LangGraph agent uses this to discover and call tools.
"""

from apps.tools.calendar_tool import (
    create_calendar_event,
    delete_calendar_event,
    get_calendar_events,
    move_calendar_event,
)
from apps.tools.reminder_tool import (
    clear_reminders,
    get_pending_reminders,
    send_reminder,
)
from apps.tools.task_tool import (
    complete_task,
    create_task,
    get_tasks,
    postpone_task,
)
from apps.tools.time_utils import (
    calculate_total_free_minutes,
    detect_conflicts,
    find_free_blocks,
    suggest_time_for_task,
)

# Master registry: name → (function, description)
TOOL_REGISTRY: dict[str, dict] = {
    # Calendar
    "get_calendar_events": {
        "fn": get_calendar_events,
        "description": "Fetch calendar events for a user in a date range.",
    },
    "create_calendar_event": {
        "fn": create_calendar_event,
        "description": "Create a new calendar event.",
    },
    "move_calendar_event": {
        "fn": move_calendar_event,
        "description": "Move an existing calendar event to a new time.",
    },
    "delete_calendar_event": {
        "fn": delete_calendar_event,
        "description": "Delete a calendar event.",
    },
    # Tasks
    "get_tasks": {
        "fn": get_tasks,
        "description": "Fetch tasks for a user with optional filters.",
    },
    "create_task": {
        "fn": create_task,
        "description": "Create a new task.",
    },
    "complete_task": {
        "fn": complete_task,
        "description": "Mark a task as completed.",
    },
    "postpone_task": {
        "fn": postpone_task,
        "description": "Postpone a task to a new due date.",
    },
    # Reminders
    "send_reminder": {
        "fn": send_reminder,
        "description": "Send or schedule a reminder.",
    },
    "get_pending_reminders": {
        "fn": get_pending_reminders,
        "description": "Get pending reminders for a user.",
    },
    "clear_reminders": {
        "fn": clear_reminders,
        "description": "Clear all reminders for a user.",
    },
    # Time utilities
    "find_free_blocks": {
        "fn": find_free_blocks,
        "description": "Find free time blocks in a day given existing events.",
    },
    "detect_conflicts": {
        "fn": detect_conflicts,
        "description": "Detect overlapping calendar events.",
    },
    "calculate_total_free_minutes": {
        "fn": calculate_total_free_minutes,
        "description": "Calculate total free minutes in a day.",
    },
    "suggest_time_for_task": {
        "fn": suggest_time_for_task,
        "description": "Suggest the best time slot for a task.",
    },
}


def get_tool(name: str):
    """Get a tool function by name."""
    entry = TOOL_REGISTRY.get(name)
    if entry is None:
        raise KeyError(f"Tool '{name}' not found in registry")
    return entry["fn"]


def list_tools() -> list[dict]:
    """List all available tools with descriptions."""
    return [
        {"name": name, "description": entry["description"]}
        for name, entry in TOOL_REGISTRY.items()
    ]