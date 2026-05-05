"""
Agent state — the data structure that flows through the LangGraph workflow.
Each node reads from and writes to this state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time
from typing import Any, Optional


@dataclass
class AgentState:
    """Mutable state passed between LangGraph nodes."""

    # --- Input ---
    user_id: int = 0
    user_message: str = ""
    intent: str = ""  # day_plan | week_plan | replan
    target_date: Optional[date] = None
    target_end_date: Optional[date] = None

    # --- Fetched data ---
    calendar_events: list[dict] = field(default_factory=list)
    tasks: list[dict] = field(default_factory=list)
    memory_notes: list[dict] = field(default_factory=list)

    # --- User preferences ---
    wake_up_time: time = time(7, 0)
    sleep_time: time = time(23, 0)
    focus_start: time = time(9, 0)
    focus_end: time = time(12, 0)
    preferred_gym_days: list[str] = field(default_factory=lambda: ["mon", "wed", "fri"])
    preferred_gym_time: time = time(17, 0)
    gym_duration_minutes: int = 60
    max_meetings_per_day: int = 4
    break_duration_minutes: int = 15
    lunch_start: time = time(12, 30)
    lunch_duration_minutes: int = 60
    task_block_minutes: int = 90

    # --- Analysis ---
    free_blocks: list[dict] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)
    prioritized_tasks: list[dict] = field(default_factory=list)
    total_free_minutes: int = 0
    constraints: list[str] = field(default_factory=list)

    # --- Output ---
    schedule: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    reasoning: str = ""
    summary: str = ""
    unscheduled_tasks: list[str] = field(default_factory=list)

    # --- Control ---
    replan_count: int = 0
    max_replans: int = 3
    needs_replan: bool = False
    error: str = ""