"""
LangGraph workflow — runs the planning agent.
Uses Agent Loop (ReAct) as primary, falls back to pipeline if it fails.
"""

from __future__ import annotations

import logging
import time as time_mod
from datetime import date, timedelta
from typing import Any, Optional

from apps.agents.agent_loop import run_agent_loop
from apps.memory.services import calculate_metrics
from apps.planner.models import PlanningSession
from apps.users.models import User

logger = logging.getLogger(__name__)


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
    Uses ReAct Agent Loop as primary method.
    """
    start_time = time_mod.time()

    # Run Agent Loop
    logger.info("Starting Agent Loop for user=%s", user.username)
    result = run_agent_loop(
        user=user,
        message=message,
        target_date=target_date,
        previous_schedule=previous_schedule,
    )

    if result:
        # Extract agent metadata
        agent_meta = result.pop("_agent_meta", {})
        duration_ms = agent_meta.get("duration_ms", 0)
        total_tokens = agent_meta.get("total_tokens", 0)

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