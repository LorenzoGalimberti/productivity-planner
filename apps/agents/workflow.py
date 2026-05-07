"""
LangGraph workflow — runs the planning agent.

CHANGES FROM v1:
- Cleans agent-generated events before fresh planning
- Creates calendar events from FINAL_SCHEDULE after agent loop (for fresh plans)
- Passes is_followup flag to agent loop for model routing
"""

from __future__ import annotations

import logging
import time as time_mod
from datetime import date, datetime, timedelta
from typing import Any, Optional

import zoneinfo

from django.conf import settings as django_settings
from apps.agents.agent_loop import run_agent_loop
from apps.memory.services import calculate_metrics
from apps.planner.models import CalendarEvent, PlanningSession
from apps.users.models import User

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Calendar cleanup
# ──────────────────────────────────────────────

def _cleanup_agent_events(user: User, target_date: date) -> int:
    """
    Delete all agent-generated events for a target date.
    Only events with source='agent' and is_fixed=False are removed.
    """
    events = CalendarEvent.objects.filter(
        user=user,
        source="agent",
        is_fixed=False,
        start_time__date=target_date,
    )
    count = events.count()
    if count:
        events.delete()
        logger.info(
            "Cleaned up %d agent events for user=%s on %s",
            count, user.username, target_date,
        )
    return count


# ──────────────────────────────────────────────
# Create events from FINAL_SCHEDULE
# ──────────────────────────────────────────────

# Map block_type to CalendarEvent.EventType
BLOCK_TYPE_MAP = {
    "task": "other",
    "meeting": "meeting",
    "break": "break",
    "gym": "gym",
    "focus": "focus",
    "personal": "personal",
    "errand": "errand",
    "study": "study",
    "other": "other",
}


def _create_events_from_schedule(
    user: User,
    schedule: list[dict],
    target_date: date,
) -> int:
    """
    Create CalendarEvent objects from the FINAL_SCHEDULE JSON.
    Returns the number of events created.
    """
    created = 0

    for block in schedule:
        try:
            title = block.get("task", "Untitled")
            start_str = block.get("start_time", "")
            end_str = block.get("end_time", "")
            block_type = block.get("block_type", "other")

            if not start_str or not end_str:
                logger.warning("Skipping block with missing times: %s", title)
                continue

            # Parse HH:MM times
            start_time = datetime.strptime(start_str, "%H:%M").time()
            end_time = datetime.strptime(end_str, "%H:%M").time()

            # Combine with target date — timezone-aware
            tz = zoneinfo.ZoneInfo(django_settings.TIME_ZONE)
            start_dt = datetime.combine(target_date, start_time, tzinfo=tz)
            end_dt = datetime.combine(target_date, end_time, tzinfo=tz)

            # Handle end time crossing midnight (e.g. 23:00 - 00:30)
            if end_dt <= start_dt:
                end_dt += timedelta(days=1)

            # Map block_type to event_type
            event_type = BLOCK_TYPE_MAP.get(block_type, "other")

            CalendarEvent.objects.create(
                user=user,
                title=title,
                start_time=start_dt,
                end_time=end_dt,
                event_type=event_type,
                is_fixed=False,
                source="agent",
            )
            created += 1

        except (ValueError, TypeError) as e:
            logger.warning("Failed to create event '%s': %s", block.get("task", "?"), e)

    logger.info(
        "Created %d events from schedule for user=%s on %s",
        created, user.username, target_date,
    )
    return created


# ──────────────────────────────────────────────
# Main workflow
# ──────────────────────────────────────────────

def run_planning_agent(
    user: User,
    message: str,
    intent: str = "",
    target_date: Optional[date] = None,
    plan_type: str = "day",
    previous_schedule: dict = None,
) -> dict:
    """
    Execute the planning agent.
    
    Fresh plans:
      1. Clean old agent events
      2. Run agent loop (read-only, produces FINAL_SCHEDULE)
      3. Create events in DB from the schedule
    
    Follow-ups:
      1. Run agent loop (with mutation tools)
      2. Events are created/moved/deleted by the agent directly
    """
    start_time = time_mod.time()

    # Determine if this is a follow-up or fresh plan
    is_followup = previous_schedule is not None

    # Clean up agent events for fresh plans (not follow-ups)
    if not is_followup and target_date:
        _cleanup_agent_events(user, target_date)

    # Run Agent Loop
    logger.info(
        "Starting Agent Loop for user=%s (%s)",
        user.username,
        "follow-up" if is_followup else "fresh plan",
    )
    result = run_agent_loop(
        user=user,
        message=message,
        target_date=target_date,
        previous_schedule=previous_schedule,
        is_followup=is_followup,
    )

    if result:
        # Extract agent metadata
        agent_meta = result.pop("_agent_meta", {})
        duration_ms = agent_meta.get("duration_ms", 0)
        total_tokens = agent_meta.get("total_tokens", 0)

        # ── Validate schedule (guardrails) ──
        from apps.agents.schedule_validator import validate_schedule

        schedule_blocks = result.get("schedule", [])
        if schedule_blocks:
            validation = validate_schedule(schedule_blocks)

            # Replace schedule with validated version
            result["schedule"] = validation["schedule"]

            # Merge validation warnings with any existing warnings
            existing_warnings = result.get("warnings", [])
            result["warnings"] = existing_warnings + validation["warnings"]

            if not validation["is_valid"]:
                logger.warning(
                    "Schedule had %d fixes: %s",
                    validation["fixes_applied"],
                    "; ".join(validation["warnings"][:3]),  # log first 3
                )

        # Create events in DB for fresh plans
        # (Follow-ups already modified the DB via tool calls)
        if not is_followup and target_date:
            validated_blocks = result.get("schedule", [])
            if validated_blocks:
                _create_events_from_schedule(user, validated_blocks, target_date)

        logger.info(
            "Agent Loop completed: %d blocks, %d tokens, %dms",
            len(result.get("schedule", [])),
            total_tokens,
            duration_ms,
        )
    else:
        # Agent loop failed — return error
        logger.error("Agent Loop failed for user=%s", user.username)
        duration_ms = int((time_mod.time() - start_time) * 1000)
        total_tokens = 0
        result = {
            "summary": "Planning failed",
            "schedule": [],
            "warnings": ["Agent loop failed. Please try again."],
            "reasoning": "",
            "unscheduled_tasks": [],
        }

    # Update behavioral metrics
    try:
        calculate_metrics(user)
    except Exception as e:
        logger.exception("Failed to update metrics: %s", e)

    # Save planning session
    plan_type_map = {
        "day": PlanningSession.PlanType.DAY,
        "week": PlanningSession.PlanType.WEEK,
        "replan": PlanningSession.PlanType.REPLAN,
    }
    try:
        session = PlanningSession.objects.create(
            user=user,
            plan_type=plan_type_map.get(plan_type, PlanningSession.PlanType.DAY),
            user_prompt=message,
            result_json=result,
            reasoning=result.get("reasoning", ""),
            warnings=result.get("warnings", []),
            target_date=target_date,
            tokens_used=total_tokens,
            duration_ms=duration_ms,
        )
        result["session_id"] = str(session.id)
    except Exception as e:
        logger.exception("Failed to save planning session: %s", e)

    logger.info("Planning complete in %dms: %s", duration_ms, result.get("summary", ""))
    return result