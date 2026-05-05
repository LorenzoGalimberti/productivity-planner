"""
Time utilities — find free blocks, detect conflicts, build day timeline.
Used by the agent to schedule tasks intelligently.
"""

import logging
from datetime import date, datetime, time, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


def find_free_blocks(
    events: list[dict],
    day_start: time = time(7, 0),
    day_end: time = time(23, 0),
    target_date: date | None = None,
    min_block_minutes: int = 15,
) -> list[dict]:
    """
    Given a list of events (with start_time/end_time strings),
    find free time blocks during the day.
    """
    if target_date is None:
        target_date = date.today()

    day_start_dt = datetime.combine(target_date, day_start)
    day_end_dt = datetime.combine(target_date, day_end)

    # Parse and sort events by start time
    busy = []
    for e in events:
        try:
            s = datetime.strptime(e["start_time"], "%Y-%m-%d %H:%M")
            t = datetime.strptime(e["end_time"], "%Y-%m-%d %H:%M")
            busy.append((s, t))
        except (ValueError, KeyError):
            continue

    busy.sort(key=lambda x: x[0])

    # Find gaps
    free_blocks = []
    cursor = day_start_dt

    for start, end in busy:
        if start > cursor:
            gap_minutes = int((start - cursor).total_seconds() / 60)
            if gap_minutes >= min_block_minutes:
                free_blocks.append({
                    "start": cursor.strftime("%H:%M"),
                    "end": start.strftime("%H:%M"),
                    "duration_minutes": gap_minutes,
                })
        cursor = max(cursor, end)

    # Final block after last event
    if cursor < day_end_dt:
        gap_minutes = int((day_end_dt - cursor).total_seconds() / 60)
        if gap_minutes >= min_block_minutes:
            free_blocks.append({
                "start": cursor.strftime("%H:%M"),
                "end": day_end_dt.strftime("%H:%M"),
                "duration_minutes": gap_minutes,
            })

    logger.info("Found %d free blocks on %s", len(free_blocks), target_date)
    return free_blocks


def detect_conflicts(events: list[dict]) -> list[dict]:
    """Detect overlapping events."""
    parsed = []
    for e in events:
        try:
            s = datetime.strptime(e["start_time"], "%Y-%m-%d %H:%M")
            t = datetime.strptime(e["end_time"], "%Y-%m-%d %H:%M")
            parsed.append({"event": e, "start": s, "end": t})
        except (ValueError, KeyError):
            continue

    parsed.sort(key=lambda x: x["start"])

    conflicts = []
    for i in range(len(parsed) - 1):
        a = parsed[i]
        b = parsed[i + 1]
        if a["end"] > b["start"]:
            conflicts.append({
                "event_a": a["event"]["title"],
                "event_b": b["event"]["title"],
                "overlap_minutes": int(
                    (a["end"] - b["start"]).total_seconds() / 60
                ),
            })

    if conflicts:
        logger.warning("Detected %d conflicts", len(conflicts))
    return conflicts


def calculate_total_free_minutes(
    events: list[dict],
    day_start: time = time(7, 0),
    day_end: time = time(23, 0),
    target_date: date | None = None,
) -> int:
    """Calculate total free minutes in a day."""
    blocks = find_free_blocks(events, day_start, day_end, target_date)
    return sum(b["duration_minutes"] for b in blocks)


def suggest_time_for_task(
    free_blocks: list[dict],
    duration_minutes: int,
    prefer_morning: bool = True,
) -> Optional[dict]:
    """Find the best free block for a task of given duration."""
    candidates = [b for b in free_blocks if b["duration_minutes"] >= duration_minutes]

    if not candidates:
        return None

    if prefer_morning:
        # Pick earliest block
        return candidates[0]
    else:
        # Pick latest block
        return candidates[-1]