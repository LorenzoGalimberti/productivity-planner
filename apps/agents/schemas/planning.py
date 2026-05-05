"""Pydantic schemas for structured LLM input/output."""

from __future__ import annotations

from datetime import date, time
from typing import Optional

from pydantic import BaseModel, Field


class TaskItem(BaseModel):
    """A single task in the schedule."""
    title: str
    priority: str = Field(description="urgent | high | medium | low | optional")
    estimated_minutes: int = Field(ge=5, le=480)
    category: str = ""
    due_date: Optional[date] = None


class TimeBlock(BaseModel):
    """A scheduled block of time."""
    start_time: str = Field(description="HH:MM format", examples=["09:00"])
    end_time: str = Field(description="HH:MM format", examples=["10:30"])
    task: str = Field(description="What to do in this block")
    block_type: str = Field(
        default="task",
        description="task | meeting | break | gym | focus | personal",
    )
    reasoning: str = Field(default="", description="Why this was scheduled here")


class FinalSchedule(BaseModel):
    """The complete generated schedule returned to the user."""
    summary: str = Field(description="Brief overview of the plan")
    schedule: list[TimeBlock]
    warnings: list[str] = Field(default_factory=list)
    reasoning: str = Field(description="Overall planning rationale")
    unscheduled_tasks: list[str] = Field(
        default_factory=list,
        description="Tasks that could not fit in the schedule",
    )


class PlanRequest(BaseModel):
    """Parsed user request for planning."""
    intent: str = Field(description="day_plan | week_plan | replan | optimize")
    target_date: Optional[date] = None
    target_end_date: Optional[date] = None
    constraints: list[str] = Field(default_factory=list)
    specific_tasks: list[str] = Field(default_factory=list)
    user_message: str = ""


class ReplanRequest(BaseModel):
    """Request to reschedule/replan."""
    reason: str = Field(description="Why replanning is needed")
    keep_fixed: list[str] = Field(
        default_factory=list,
        description="Event IDs that should not be moved",
    )
    new_constraints: list[str] = Field(default_factory=list)