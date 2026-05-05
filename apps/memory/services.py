"""
Memory service — the brain of the agent's memory system.
Calculates behavioral metrics, extracts notes, manages relevance decay.
"""

import logging
import re
from datetime import date, datetime, timedelta
from typing import Any, Optional

from django.db.models import Count, Q
from django.utils import timezone

from apps.memory.models import BehavioralMetric, MemoryNote, UserPreference
from apps.planner.models import Task
from apps.users.models import User

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Behavioral metrics calculation
# ──────────────────────────────────────────────

def calculate_metrics(user: User, period_days: int = 7) -> dict[str, float]:
    """
    Observe user behavior over the last N days and compute metrics.
    Saves them to BehavioralMetric and returns the values.
    """
    now = timezone.now()
    period_start = (now - timedelta(days=period_days)).date()
    period_end = now.date()

    tasks = Task.objects.filter(user=user, created_at__date__gte=period_start)
    total = tasks.count()

    if total == 0:
        logger.info("No tasks in period for user=%s, skipping metrics", user.username)
        return {}

    # 1. Task completion rate
    done = tasks.filter(status="done").count()
    completion_rate = round(done / total, 2) if total > 0 else 0.0

    # 2. Average postponements
    postponed = tasks.filter(status="postponed").count()
    avg_postponements = round(postponed / max((period_days / 7), 1), 2)

    # 3. Morning vs afternoon productivity
    # Count tasks completed before 13:00 vs after
    morning_done = tasks.filter(
        status="done",
        completed_at__isnull=False,
        completed_at__hour__lt=13,
    ).count()
    afternoon_done = tasks.filter(
        status="done",
        completed_at__isnull=False,
        completed_at__hour__gte=13,
    ).count()

    total_done = morning_done + afternoon_done
    morning_productivity = round(morning_done / total_done, 2) if total_done > 0 else 0.5
    afternoon_productivity = round(afternoon_done / total_done, 2) if total_done > 0 else 0.5

    # 4. Schedule density preference (avg tasks per day)
    active_days = tasks.values("created_at__date").distinct().count()
    schedule_density = round(total / max(active_days, 1), 1)

    # Save all metrics
    metrics = {
        "task_completion_rate": completion_rate,
        "avg_postponements_per_week": avg_postponements,
        "morning_productivity": morning_productivity,
        "afternoon_productivity": afternoon_productivity,
        "schedule_density": schedule_density,
    }

    for name, value in metrics.items():
        BehavioralMetric.objects.update_or_create(
            user=user,
            metric_name=name,
            period_start=period_start,
            defaults={
                "metric_value": value,
                "period_end": period_end,
            },
        )

    logger.info(
        "Metrics calculated for user=%s: completion=%.0f%%, postponed=%.1f/wk, "
        "morning=%.0f%%, density=%.1f tasks/day",
        user.username, completion_rate * 100, avg_postponements,
        morning_productivity * 100, schedule_density,
    )
    return metrics


# ──────────────────────────────────────────────
# Note extraction from user messages
# ──────────────────────────────────────────────

# Patterns to detect facts from messages
EXTRACTION_PATTERNS = [
    # (regex pattern, category, expires_in_days or None)
    (r"exam\s+(?:on\s+)?(\w+)", "goal", 14),
    (r"deadline\s+(?:on\s+|is\s+)?(\w+)", "goal", 7),
    (r"(?:i am|i'm)\s+sick", "constraint", 7),
    (r"(?:i am|i'm)\s+(?:a\s+)?student", "context", None),
    (r"(?:i work|working)\s+part[- ]?time", "context", None),
    (r"(?:i want|i'd like)\s+(?:to\s+)?(.+?)(?:\.|$)", "goal", None),
    (r"(?:i prefer|i like)\s+(?:to\s+)?(.+?)(?:\.|$)", "preference", None),
    (r"(?:preparing|studying)\s+(?:for\s+)?(.+?)(?:\.|$)", "goal", 14),
    (r"(?:vacation|holiday|break)\s+(?:from\s+)?(.+?)(?:\.|$)", "constraint", 14),
    (r"(?:gym|workout|exercise)\s+(\d+)\s*(?:times|days)", "preference", None),
    (r"(?:i need|i must)\s+(.+?)(?:\.|$)", "goal", 7),
    (r"(?:balance|equilibrio)\s+(.+?)(?:\.|$)", "preference", None),
]


def extract_notes_from_message(user: User, message: str) -> list[dict]:
    """
    Parse user message and extract facts to remember.
    Returns list of created notes.
    """
    message_lower = message.lower().strip()
    created_notes = []

    for pattern, category, expires_days in EXTRACTION_PATTERNS:
        match = re.search(pattern, message_lower)
        if match:
            # Build the note content from the full match
            content = match.group(0).strip()
            # Capitalize first letter
            content = content[0].upper() + content[1:]

            # Check for duplicates
            existing = MemoryNote.objects.filter(
                user=user,
                content__icontains=content[:40],  # Partial match
                is_active=True,
            ).exists()

            if existing:
                continue

            # Calculate expiry
            expires_at = None
            if expires_days:
                expires_at = date.today() + timedelta(days=expires_days)

            note = MemoryNote.objects.create(
                user=user,
                content=content,
                category=category,
                relevance_score=1.0,
                expires_at=expires_at,
                source="agent",
            )
            created_notes.append({
                "content": note.content,
                "category": note.category,
                "expires_at": note.expires_at.isoformat() if note.expires_at else None,
            })
            logger.info("Extracted note: [%s] %s", category, content)

    return created_notes


# ──────────────────────────────────────────────
# Relevance decay
# ──────────────────────────────────────────────

def decay_relevance(decay_rate: float = 0.05) -> int:
    """
    Reduce relevance_score of old notes without an expiry date.
    Notes with expiry are handled by cleanup_expired().
    Returns number of notes decayed.
    """
    threshold_date = timezone.now() - timedelta(days=7)

    old_notes = MemoryNote.objects.filter(
        is_active=True,
        expires_at__isnull=True,
        updated_at__lt=threshold_date,
        relevance_score__gt=0.1,
    )

    count = 0
    for note in old_notes:
        note.relevance_score = round(max(note.relevance_score - decay_rate, 0.1), 2)
        note.save(update_fields=["relevance_score", "updated_at"])
        count += 1

    if count:
        logger.info("Decayed relevance for %d notes", count)
    return count


# ──────────────────────────────────────────────
# Cleanup expired notes
# ──────────────────────────────────────────────

def cleanup_expired() -> int:
    """
    Deactivate notes whose expires_at has passed.
    Returns number of notes deactivated.
    """
    today = date.today()
    expired = MemoryNote.objects.filter(
        is_active=True,
        expires_at__isnull=False,
        expires_at__lt=today,
    )
    count = expired.update(is_active=False)

    if count:
        logger.info("Deactivated %d expired notes", count)
    return count


# ──────────────────────────────────────────────
# Get full memory context for the agent
# ──────────────────────────────────────────────

def get_memory_context(user: User) -> dict[str, Any]:
    """
    Gather ALL memory for a user into a single dict.
    This is what the agent reads at Node 4.
    """
    # 1. Structural memory (preferences)
    preferences = {}
    try:
        prefs = UserPreference.objects.get(user=user)
        preferences = {
            "wake_up_time": prefs.wake_up_time,
            "sleep_time": prefs.sleep_time,
            "focus_start": prefs.preferred_focus_start,
            "focus_end": prefs.preferred_focus_end,
            "preferred_gym_days": prefs.preferred_gym_days.split(",") if prefs.preferred_gym_days else [],
            "preferred_gym_time": prefs.preferred_gym_time,
            "gym_duration_minutes": prefs.gym_duration_minutes,
            "max_meetings_per_day": prefs.max_meetings_per_day,
            "break_duration_minutes": prefs.preferred_break_duration,
            "lunch_start": prefs.lunch_start,
            "lunch_duration_minutes": prefs.lunch_duration_minutes,
            "task_block_minutes": prefs.preferred_task_block_minutes,
        }
    except UserPreference.DoesNotExist:
        pass

    # 2. Behavioral memory (metrics)
    recent_metrics = {}
    metrics = BehavioralMetric.objects.filter(user=user).order_by("-period_start")
    for m in metrics:
        # Keep only the most recent value for each metric
        if m.metric_name not in recent_metrics:
            recent_metrics[m.metric_name] = m.metric_value

    # 3. Episodic memory (notes with expiry)
    episodic_notes = list(
        MemoryNote.objects.filter(
            user=user,
            is_active=True,
            expires_at__isnull=False,
        ).values("content", "category", "expires_at", "relevance_score")
    )

    # 4. Semantic memory (notes without expiry)
    semantic_notes = list(
        MemoryNote.objects.filter(
            user=user,
            is_active=True,
            expires_at__isnull=True,
            relevance_score__gte=0.3,  # Filter out low-relevance notes
        ).values("content", "category", "relevance_score")
    )

    context = {
        "preferences": preferences,
        "behavioral_metrics": recent_metrics,
        "episodic_notes": episodic_notes,
        "semantic_notes": semantic_notes,
    }

    logger.info(
        "Memory context for user=%s: %d prefs, %d metrics, %d episodic, %d semantic notes",
        user.username, len(preferences), len(recent_metrics),
        len(episodic_notes), len(semantic_notes),
    )
    return context