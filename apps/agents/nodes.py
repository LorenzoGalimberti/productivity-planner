"""
LangGraph node functions.
Each function takes AgentState, does work, and returns updated fields.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, time, timedelta
from typing import Any

from django.conf import settings

from apps.memory.models import MemoryNote, UserPreference
from apps.memory.services import (
    calculate_metrics,
    cleanup_expired,
    decay_relevance,
    extract_notes_from_message,
    get_memory_context,
)
import re

from apps.tools.calendar_tool import get_calendar_events
from apps.tools.task_tool import create_task, get_tasks
from apps.tools.time_utils import detect_conflicts, find_free_blocks
from apps.users.models import User

logger = logging.getLogger(__name__)

PRIORITY_ORDER = {"urgent": 0, "high": 1, "medium": 2, "low": 3, "optional": 4}

# ── Known activity patterns for auto-extraction ──
# (pattern, default_title, category, estimated_minutes, priority)
ACTIVITY_PATTERNS = [
    (r"\b(?:go to |do )?gym\b", "Gym session", "gym", 60, "medium"),
    (r"\b(?:go to |do )?workout\b", "Workout", "gym", 60, "medium"),
    (r"\bstudy(?:ing)?\b", "Study session", "study", 90, "high"),
    (r"\bexam\b", "Exam preparation", "study", 120, "urgent"),
    (r"\b(?:see|meet|visit)\s+(?:my\s+)?(?:girlfriend|boyfriend|partner|gf|bf)\b", "See partner", "personal", 120, "medium"),
    (r"\b(?:see|meet|visit)\s+(?:my\s+)?(?:friends?|mates?|buddy|buddies)\b", "See friends", "personal", 120, "low"),
    (r"\b(?:see|meet|visit)\s+(?:my\s+)?(?:family|parents?|mom|dad|mum|mother|father)\b", "Visit family", "personal", 120, "medium"),
    (r"\b(?:go )?shopping\b", "Shopping", "errand", 60, "low"),
    (r"\berrands?\b", "Errands", "errand", 60, "medium"),
    (r"\bcook(?:ing)?\b", "Cooking", "personal", 60, "low"),
    (r"\bclean(?:ing)?\b", "Cleaning", "personal", 60, "low"),
    (r"\blaundry\b", "Laundry", "personal", 45, "low"),
    (r"\b(?:doctor|dentist|appointment)\b", "Medical appointment", "personal", 60, "high"),
    (r"\bmeeting(?:s)?\b", "Meeting", "meeting", 60, "high"),
    (r"\b(?:call|phone)\s+(?:\w+\s+)?(?:\w+)?\b", "Phone call", "meeting", 30, "medium"),
    (r"\bread(?:ing)?\b", "Reading", "study", 60, "low"),
    (r"\bwork(?:ing)?\s+(?:on\s+)?(?:project|report|presentation|code|coding)\b", "Deep work", "work", 120, "high"),
    (r"\b(?:i )?work\b", "Work", "work", 120, "high"),
    (r"\brelax(?:ing|ation)?\b", "Relaxation", "personal", 60, "optional"),
    (r"\bmeditat(?:e|ion|ing)\b", "Meditation", "personal", 20, "medium"),
    (r"\bwalk(?:ing)?\b", "Walk", "personal", 30, "low"),
    (r"\brun(?:ning)?\b", "Running", "gym", 45, "medium"),
    (r"\byoga\b", "Yoga", "gym", 60, "medium"),
    (r"\bgroceries\b", "Buy groceries", "errand", 45, "medium"),
    (r"\bpresentation\b", "Prepare presentation", "work", 90, "high"),
    (r"\bemail(?:s)?\b", "Process emails", "work", 30, "medium"),
    (r"\blunch\b", "Lunch", "personal", 60, "medium"),
    (r"\bdinner\b", "Dinner", "personal", 60, "medium"),
]

# Words that are part of filler phrases, not real activities
NOISE_PHRASES = [
    "plan my day", "plan my week", "organize my", "optimize my",
    "i have", "i need to", "i want to", "i should",
    "tomorrow", "today", "next week", "this week",
    "please", "help me", "can you", "could you",
]


def _extract_activities_from_message(message: str) -> list[dict]:
    """
    Extract activities/tasks mentioned in the user message.
    Returns list of dicts with title, category, estimated_minutes, priority.
    """
    msg_lower = message.lower().strip()
    found = []
    matched_spans = []

    for pattern, title, category, minutes, priority in ACTIVITY_PATTERNS:
        match = re.search(pattern, msg_lower)
        if match:
            # Avoid overlapping matches
            span = match.span()
            overlaps = any(
                not (span[1] <= ms[0] or span[0] >= ms[1])
                for ms in matched_spans
            )
            if overlaps:
                continue

            matched_spans.append(span)
            found.append({
                "title": title,
                "category": category,
                "estimated_minutes": minutes,
                "priority": priority,
                "matched_text": match.group(0),
            })

    return found


# ──────────────────────────────────────────────
# Node 1: Parse user request (LLM-powered)
# ──────────────────────────────────────────────

def parse_user_request(state: dict) -> dict:
    """
    Parse user message using LLM to extract:
    - Calendar events with fixed times -> created as CalendarEvent
    - Tasks without times -> created as Task
    - Constraints and intent
    Falls back to regex if LLM fails.
    """
    from apps.agents.llm_parser import parse_message_with_llm
    from apps.planner.models import Task
    from apps.tools.calendar_tool import create_calendar_event

    message = state.get("user_message", "")
    user = User.objects.get(id=state["user_id"])
    today = date.today()
    tomorrow = today + timedelta(days=1)

    # ── Try LLM parsing first ──
    llm_result = parse_message_with_llm(message, state.get("target_date"))

    if llm_result:
        logger.info("Using LLM parser result")

        # Intent
        intent = llm_result.get("intent", "day_plan")

        # Target date
        target_date_str = llm_result.get("target_date")
        if target_date_str:
            try:
                target_date = date.fromisoformat(target_date_str)
            except (ValueError, TypeError):
                target_date = state.get("target_date") or tomorrow
        else:
            target_date = state.get("target_date") or tomorrow

        # Constraints
        constraints = llm_result.get("constraints", [])

        # ── Create CalendarEvents from LLM output ──
        for event in llm_result.get("calendar_events", []):
            title = event.get("title", "Event")
            start_str = event.get("start_time", "")
            end_str = event.get("end_time", "")

            if not start_str or not end_str:
                continue

            try:
                start_dt = datetime.combine(
                    target_date,
                    datetime.strptime(start_str, "%H:%M").time(),
                )
                end_dt = datetime.combine(
                    target_date,
                    datetime.strptime(end_str, "%H:%M").time(),
                )

                # Check if similar event already exists
                from apps.planner.models import CalendarEvent as CE
                existing = CE.objects.filter(
                    user=user,
                    title__icontains=title[:20],
                    start_time__date=target_date,
                ).exists()

                if not existing:
                    create_calendar_event(
                        user=user,
                        title=title,
                        start_time=start_dt,
                        end_time=end_dt,
                        event_type="other",
                        is_fixed=True,
                        source="agent",
                    )
                    logger.info(
                        "LLM created event: '%s' %s-%s",
                        title, start_str, end_str,
                    )
            except (ValueError, TypeError) as e:
                logger.warning("Failed to create event '%s': %s", title, e)

        # ── Create Tasks from LLM output ──
        for task in llm_result.get("tasks", []):
            title = task.get("title", "Task")
            duration = task.get("duration_minutes", 60)
            priority = task.get("priority", "medium")
            category = task.get("category", "")

            # Validate priority
            if priority not in ("urgent", "high", "medium", "low", "optional"):
                priority = "medium"

            existing = Task.objects.filter(
                user=user,
                title__icontains=title[:20],
                due_date=target_date,
            ).exclude(status__in=["done", "cancelled"]).exists()

            if not existing:
                create_task(
                    user=user,
                    title=title,
                    priority=priority,
                    estimated_minutes=duration,
                    due_date=target_date,
                    category=category,
                )
                logger.info(
                    "LLM created task: '%s' (%s, %dmin)",
                    title, priority, duration,
                )

        target_end_date = state.get("target_end_date")
        if target_end_date is None and intent == "week_plan":
            target_end_date = target_date + timedelta(days=6)

        return {
            "intent": intent,
            "target_date": target_date,
            "target_end_date": target_end_date,
            "constraints": constraints,
        }

    # ── Fallback to regex if LLM fails ──
    logger.warning("LLM parser failed, falling back to regex")
    message_lower = message.lower()

    intent = state.get("intent", "")
    if not intent:
        if any(w in message_lower for w in ["replan", "reschedul", "sick", "cancel", "change"]):
            intent = "replan"
        elif any(w in message_lower for w in ["week", "weekly", "7 days", "next week"]):
            intent = "week_plan"
        else:
            intent = "day_plan"

    target_date = state.get("target_date")
    if target_date is None:
        if "today" in message_lower:
            target_date = today
        elif "tomorrow" in message_lower:
            target_date = tomorrow
        else:
            target_date = tomorrow

    target_end_date = state.get("target_end_date")
    if target_end_date is None and intent == "week_plan":
        target_end_date = target_date + timedelta(days=6)

    constraints = []
    if "sick" in message_lower:
        constraints.append("User is sick - light schedule only")
    if "exam" in message_lower:
        constraints.append("Exam preparation is a priority")

    # Regex fallback: extract activities
    activities = _extract_activities_from_message(message)
    for activity in activities:
        existing = Task.objects.filter(
            user=user,
            title__icontains=activity["title"][:20],
            due_date=target_date,
        ).exclude(status__in=["done", "cancelled"]).exists()

        if not existing:
            create_task(
                user=user,
                title=activity["title"],
                priority=activity["priority"],
                estimated_minutes=activity["estimated_minutes"],
                due_date=target_date,
                category=activity["category"],
            )

    logger.info("Fallback parsed intent=%s, target=%s", intent, target_date)

    return {
        "intent": intent,
        "target_date": target_date,
        "target_end_date": target_end_date,
        "constraints": constraints,
    }
# Node 2: Fetch calendar events
# ──────────────────────────────────────────────

def fetch_calendar(state: dict) -> dict:
    """Load existing calendar events for the target date range."""
    user = User.objects.get(id=state["user_id"])
    target_date = state.get("target_date") or date.today() + timedelta(days=1)
    target_end = state.get("target_end_date") or target_date

    events = get_calendar_events(user, target_date, target_end)
    logger.info("Fetched %d calendar events", len(events))
    return {"calendar_events": events}


# ──────────────────────────────────────────────
# Node 3: Fetch tasks
# ──────────────────────────────────────────────

def fetch_tasks(state: dict) -> dict:
    """Load pending tasks for the user."""
    user = User.objects.get(id=state["user_id"])
    tasks = get_tasks(user)
    logger.info("Fetched %d tasks", len(tasks))
    return {"tasks": tasks}


# ──────────────────────────────────────────────
# Node 4: Fetch user preferences & FULL memory
# ──────────────────────────────────────────────

def fetch_user_preferences(state: dict) -> dict:
    """Load all 4 types of memory: structural, behavioral, episodic, semantic."""
    user = User.objects.get(id=state["user_id"])

    # Clean up expired notes first
    cleanup_expired()
    decay_relevance()

    # Get full memory context
    memory = get_memory_context(user)

    # Flatten preferences into state
    prefs = memory.get("preferences", {})
    result = {**prefs}

    # Add behavioral metrics to state
    result["behavioral_metrics"] = memory.get("behavioral_metrics", {})

    # Add memory notes to state
    result["memory_notes"] = (
        memory.get("episodic_notes", []) + memory.get("semantic_notes", [])
    )

    # Log what memory we loaded
    metrics = result["behavioral_metrics"]
    if metrics:
        logger.info(
            "Behavioral memory: completion=%.0f%%, morning_prod=%.0f%%, density=%.1f",
            metrics.get("task_completion_rate", 0) * 100,
            metrics.get("morning_productivity", 0.5) * 100,
            metrics.get("schedule_density", 5),
        )

    notes_count = len(result["memory_notes"])
    if notes_count:
        logger.info("Loaded %d memory notes", notes_count)

    return result


# ──────────────────────────────────────────────
# Node 5: Analyze constraints (free blocks, conflicts)
# ──────────────────────────────────────────────

def analyze_constraints(state: dict) -> dict:
    """Find free time blocks and detect conflicts."""
    events = state.get("calendar_events", [])
    target_date = state.get("target_date") or date.today() + timedelta(days=1)
    wake = state.get("wake_up_time", time(7, 0))
    sleep = state.get("sleep_time", time(23, 0))

    free_blocks = find_free_blocks(events, wake, sleep, target_date)
    conflicts = detect_conflicts(events)
    total_free = sum(b["duration_minutes"] for b in free_blocks)

    warnings = []
    if conflicts:
        for c in conflicts:
            warnings.append(
                f"Conflict: '{c['event_a']}' overlaps with '{c['event_b']}' "
                f"by {c['overlap_minutes']} min"
            )
    if total_free < 60:
        warnings.append("Very little free time available today")

    logger.info(
        "Analysis: %d free blocks, %d min free, %d conflicts",
        len(free_blocks), total_free, len(conflicts),
    )
    return {
        "free_blocks": free_blocks,
        "conflicts": conflicts,
        "total_free_minutes": total_free,
        "warnings": state.get("warnings", []) + warnings,
    }


# ──────────────────────────────────────────────
# Node 6: Prioritize tasks (now memory-aware)
# ──────────────────────────────────────────────

def prioritize_tasks(state: dict) -> dict:
    """Sort tasks using priority, deadline, constraints, AND behavioral memory."""
    tasks = state.get("tasks", [])
    constraints = state.get("constraints", [])
    target_date = state.get("target_date") or date.today() + timedelta(days=1)
    memory_notes = state.get("memory_notes", [])

    # Extract goals from memory notes
    active_goals = [
        n["content"] for n in memory_notes
        if n.get("category") in ("goal", "constraint")
    ]

    scored = []
    for t in tasks:
        score = PRIORITY_ORDER.get(t.get("priority", "medium"), 2)

        # Boost tasks due today or overdue
        due = t.get("due_date")
        if due:
            due_date = date.fromisoformat(due) if isinstance(due, str) else due
            days_until = (due_date - target_date).days
            if days_until <= 0:
                score -= 3
            elif days_until <= 2:
                score -= 1

        # Boost if constraint mentions exam and task is study-related
        if "Exam preparation" in str(constraints) or any("exam" in g.lower() for g in active_goals):
            cat = t.get("category", "").lower()
            title = t.get("title", "").lower()
            if any(w in cat or w in title for w in ["study", "exam", "review", "learn"]):
                score -= 2

        # Boost based on active goals from memory
        for goal in active_goals:
            goal_lower = goal.lower()
            title_lower = t.get("title", "").lower()
            if any(word in title_lower for word in goal_lower.split() if len(word) > 3):
                score -= 1
                break

        scored.append({**t, "_score": score})

    scored.sort(key=lambda x: x["_score"])
    prioritized = [{k: v for k, v in t.items() if k != "_score"} for t in scored]

    logger.info("Prioritized %d tasks (with %d active goals)", len(prioritized), len(active_goals))
    return {"prioritized_tasks": prioritized}


# -------------------------------------------------
# Node 7: Generate schedule (LLM-powered)
# -------------------------------------------------

def generate_schedule(state: dict) -> dict:
    """
    Build the schedule using LLM.
    Sends all context (events, tasks, preferences, memory) to GPT
    and receives an optimized schedule.
    Falls back to basic algorithm if LLM fails.
    """
    from apps.agents.llm_scheduler import generate_schedule_with_llm

    tasks = list(state.get("prioritized_tasks", []))
    target_date = state.get("target_date") or date.today() + timedelta(days=1)
    constraints = state.get("constraints", [])
    metrics = state.get("behavioral_metrics", {})
    memory_notes = state.get("memory_notes", [])
    calendar_events = state.get("calendar_events", [])

    # Build preferences dict for the LLM
    preferences = {
        "wake_up_time": state.get("wake_up_time", time(7, 0)),
        "sleep_time": state.get("sleep_time", time(23, 0)),
        "focus_start": state.get("focus_start", time(9, 0)),
        "focus_end": state.get("focus_end", time(12, 0)),
        "lunch_start": state.get("lunch_start", time(12, 30)),
        "lunch_duration_minutes": state.get("lunch_duration_minutes", 60),
        "break_duration_minutes": state.get("break_duration_minutes", 15),
        "preferred_gym_days": state.get("preferred_gym_days", []),
        "preferred_gym_time": state.get("preferred_gym_time", time(17, 0)),
        "gym_duration_minutes": state.get("gym_duration_minutes", 60),
    }

    # -- Try LLM scheduling --
    llm_schedule = generate_schedule_with_llm(
        target_date=target_date,
        calendar_events=calendar_events,
        tasks=tasks,
        preferences=preferences,
        memory_notes=memory_notes,
        metrics=metrics,
        constraints=constraints,
        previous_schedule=state.get("previous_schedule"),
    )

    if llm_schedule:
        logger.info("Using LLM-generated schedule (%d blocks)", len(llm_schedule))

        # Build summary
        is_sick = any("sick" in c.lower() for c in constraints)
        if is_sick:
            summary = f"Light schedule for {target_date} (recovery day)"
        elif state.get("intent") == "week_plan":
            summary = f"Weekly plan starting {target_date}"
        else:
            summary = f"Optimized day plan for {target_date}"

        # Collect reasoning from LLM blocks
        reasoning_parts = []
        for block in llm_schedule:
            if block.get("reasoning"):
                reasoning_parts.append(block["reasoning"])

        # Add memory goals
        goals = [n["content"] for n in memory_notes if n.get("category") == "goal"]
        if goals:
            reasoning_parts.append(f"Active goals considered: {', '.join(goals[:3])}")

        if metrics.get("task_completion_rate", 1) < 0.7:
            rate = metrics["task_completion_rate"]
            reasoning_parts.append(f"Completion rate is {rate*100:.0f}% - schedule adjusted")

        reasoning = ". ".join(reasoning_parts[:5]) if reasoning_parts else "Schedule optimized by AI"

        return {
            "schedule": llm_schedule,
            "unscheduled_tasks": [],
            "summary": summary,
            "reasoning": reasoning,
        }

    # -- Fallback: basic algorithm if LLM fails --
    logger.warning("LLM scheduler failed, using basic algorithm")

    free_blocks = list(state.get("free_blocks", []))
    task_block = state.get("task_block_minutes", 90)
    focus_start = state.get("focus_start", time(9, 0))
    focus_end = state.get("focus_end", time(12, 0))
    lunch_start = state.get("lunch_start", time(12, 30))
    lunch_dur = state.get("lunch_duration_minutes", 60)
    break_dur = state.get("break_duration_minutes", 15)

    schedule = []
    unscheduled = []

    def time_in_range(t_str, start, end):
        t = datetime.strptime(t_str, "%H:%M").time()
        return start <= t < end

    # Add lunch
    lunch_end_m = lunch_start.minute + lunch_dur
    lunch_end = time(lunch_start.hour + lunch_end_m // 60, lunch_end_m % 60)
    schedule.append({
        "start_time": lunch_start.strftime("%H:%M"),
        "end_time": lunch_end.strftime("%H:%M"),
        "task": "Lunch break",
        "block_type": "break",
        "reasoning": "Daily lunch break",
    })

    # Add existing events
    for ev in calendar_events:
        ev_start = ev["start_time"].split(" ")[1] if " " in ev["start_time"] else ev["start_time"]
        ev_end = ev["end_time"].split(" ")[1] if " " in ev["end_time"] else ev["end_time"]
        schedule.append({
            "start_time": ev_start,
            "end_time": ev_end,
            "task": ev["title"],
            "block_type": ev.get("event_type", "other"),
            "reasoning": "Pre-existing calendar event",
        })

    # Fit tasks
    schedule.sort(key=lambda x: x["start_time"])
    for task in tasks:
        est = task.get("estimated_minutes") or task_block
        placed = False
        for block in free_blocks:
            if block["duration_minutes"] >= est:
                block_start = block["start"]
                start_dt = datetime.strptime(block_start, "%H:%M")
                end_dt = start_dt + timedelta(minutes=est)
                end_str = end_dt.strftime("%H:%M")

                overlap = any(
                    not (end_str <= s["start_time"] or block_start >= s["end_time"])
                    for s in schedule
                )
                if not overlap:
                    schedule.append({
                        "start_time": block_start,
                        "end_time": end_str,
                        "task": task["title"],
                        "block_type": "task",
                        "reasoning": "Scheduled in available slot",
                    })
                    break_end = end_dt + timedelta(minutes=break_dur)
                    block["start"] = break_end.strftime("%H:%M")
                    block["duration_minutes"] -= (est + break_dur)
                    placed = True
                    break
        if not placed:
            unscheduled.append(task["title"])

    schedule.sort(key=lambda x: x["start_time"])
    summary = f"Optimized day plan for {target_date}"
    reasoning = "Schedule generated with basic algorithm (LLM unavailable)"

    logger.info("Fallback schedule: %d blocks, %d unscheduled", len(schedule), len(unscheduled))

    return {
        "schedule": schedule,
        "unscheduled_tasks": unscheduled,
        "summary": summary,
        "reasoning": reasoning,
    }

# ──────────────────────────────────────────────
# Node 8: Detect conflicts in generated schedule
# ──────────────────────────────────────────────

def detect_schedule_conflicts(state: dict) -> dict:
    """Check the generated schedule for overlaps and decide if replan is needed."""
    schedule = state.get("schedule", [])
    target_date = state.get("target_date") or date.today()
    replan_count = state.get("replan_count", 0)
    max_replans = state.get("max_replans", 3)

    events_for_check = []
    for s in schedule:
        events_for_check.append({
            "title": s["task"],
            "start_time": f"{target_date} {s['start_time']}",
            "end_time": f"{target_date} {s['end_time']}",
        })

    conflicts = detect_conflicts(events_for_check)
    needs_replan = len(conflicts) > 0 and replan_count < max_replans

    warnings = state.get("warnings", [])
    if conflicts:
        for c in conflicts:
            warnings.append(
                f"Schedule conflict: '{c['event_a']}' overlaps '{c['event_b']}'"
            )

    logger.info(
        "Conflict check: %d conflicts, needs_replan=%s (attempt %d/%d)",
        len(conflicts), needs_replan, replan_count, max_replans,
    )
    return {
        "conflicts": conflicts,
        "needs_replan": needs_replan,
        "warnings": warnings,
    }


# ──────────────────────────────────────────────
# Node 9: Replan (remove conflicts)
# ──────────────────────────────────────────────

def replanning_node(state: dict) -> dict:
    """Attempt to fix conflicts by removing lowest-priority overlapping items."""
    schedule = list(state.get("schedule", []))
    conflicts = state.get("conflicts", [])
    replan_count = state.get("replan_count", 0)
    unscheduled = list(state.get("unscheduled_tasks", []))

    titles_to_remove = set()
    for c in conflicts:
        titles_to_remove.add(c["event_b"])

    new_schedule = [s for s in schedule if s["task"] not in titles_to_remove]
    unscheduled.extend(titles_to_remove)

    logger.info("Replan: removed %d conflicting items", len(titles_to_remove))

    return {
        "schedule": new_schedule,
        "unscheduled_tasks": unscheduled,
        "replan_count": replan_count + 1,
        "needs_replan": False,
    }


# -------------------------------------------------
# Node 10: Save memory (LLM-powered)
# -------------------------------------------------

def save_memory(state: dict) -> dict:
    """
    Save relevant facts to memory using LLM:
    1. LLM extracts meaningful facts from user message
    2. Save constraints as notes
    3. Update behavioral metrics
    Falls back to regex if LLM fails.
    """
    from apps.agents.llm_memory import extract_memory_with_llm

    user = User.objects.get(id=state["user_id"])
    message = state.get("user_message", "")
    constraints = state.get("constraints", [])

    # Get existing notes to avoid duplicates
    existing_contents = list(
        MemoryNote.objects.filter(user=user, is_active=True)
        .values_list("content", flat=True)
    )

    # 1. Try LLM extraction
    llm_notes = extract_memory_with_llm(message, existing_notes=existing_contents)

    if llm_notes is not None:
        for note in llm_notes:
            content = note.get("content", "").strip()
            category = note.get("category", "context")
            expires_days = note.get("expires_days")

            if not content:
                continue

            # Validate category
            if category not in ("preference", "goal", "constraint", "context", "habit"):
                category = "context"

            # Check for duplicates (fuzzy match)
            is_duplicate = any(
                content.lower()[:30] in existing.lower()
                or existing.lower()[:30] in content.lower()
                for existing in existing_contents
            )

            if is_duplicate:
                logger.info("Skipping duplicate note: %s", content[:50])
                continue

            # Calculate expiry
            expires_at = None
            if expires_days:
                expires_at = date.today() + timedelta(days=int(expires_days))

            MemoryNote.objects.create(
                user=user,
                content=content,
                category=category,
                relevance_score=1.0,
                expires_at=expires_at,
                source="agent",
            )
            existing_contents.append(content)
            logger.info("LLM saved memory: [%s] %s", category, content[:60])
    else:
        # Fallback to regex extraction
        logger.warning("LLM memory failed, using regex fallback")
        extracted = extract_notes_from_message(user, message)
        if extracted:
            logger.info("Regex extracted %d notes", len(extracted))

    # 2. Save constraints as notes
    for constraint in constraints:
        exists = MemoryNote.objects.filter(
            user=user, content=constraint, is_active=True
        ).exists()
        if not exists:
            MemoryNote.objects.create(
                user=user,
                content=constraint,
                category="constraint",
                source="agent",
                expires_at=date.today() + timedelta(days=7),
            )

    # 3. Update behavioral metrics
    calculate_metrics(user)

    return {}


# ──────────────────────────────────────────────
# Node 11: Build final response
# ──────────────────────────────────────────────

def final_response(state: dict) -> dict:
    """Package the final response."""
    schedule = state.get("schedule", [])

    clean_schedule = []
    for s in schedule:
        clean_schedule.append({
            "time": f"{s['start_time']}-{s['end_time']}",
            "task": s["task"],
            "type": s.get("block_type", "task"),
            "reasoning": s.get("reasoning", ""),
        })

    return {
        "schedule": clean_schedule,
        "summary": state.get("summary", ""),
        "reasoning": state.get("reasoning", ""),
        "warnings": state.get("warnings", []),
        "unscheduled_tasks": state.get("unscheduled_tasks", []),
    }