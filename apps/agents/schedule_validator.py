"""
Schedule Validator — guardrails for FINAL_SCHEDULE output.

Validates the schedule JSON produced by the agent and fixes common issues:
- Overlapping events
- Events out of time order
- end_time <= start_time
- Events outside wake/sleep bounds
- Duplicate events

Returns a validated schedule with warnings for anything that was fixed.
"""

import logging
from datetime import datetime, time
from typing import Any

logger = logging.getLogger(__name__)


def validate_schedule(
    schedule: list[dict],
    wake_time: str = "07:00",
    sleep_time: str = "23:00",
) -> dict:
    """
    Validate and fix a schedule.
    
    Returns:
        {
            "schedule": [...],       # cleaned schedule
            "warnings": [...],       # list of warning strings
            "fixes_applied": int,    # number of fixes made
            "is_valid": bool,        # True if no fixes were needed
        }
    """
    warnings = []
    fixes = 0

    if not schedule:
        return {
            "schedule": [],
            "warnings": ["Empty schedule"],
            "fixes_applied": 0,
            "is_valid": True,
        }

    # ── Step 1: Parse and validate individual blocks ──
    parsed_blocks = []
    for i, block in enumerate(schedule):
        start_str = block.get("start_time", "")
        end_str = block.get("end_time", "")
        task = block.get("task", "Untitled")

        # Parse times
        try:
            start = datetime.strptime(start_str, "%H:%M").time()
        except (ValueError, TypeError):
            warnings.append(f"Invalid start_time '{start_str}' for '{task}' — skipping block")
            fixes += 1
            continue

        try:
            end = datetime.strptime(end_str, "%H:%M").time()
        except (ValueError, TypeError):
            warnings.append(f"Invalid end_time '{end_str}' for '{task}' — skipping block")
            fixes += 1
            continue

        # Check end > start (allow midnight crossing for last block)
        if end <= start and end != time(0, 0):
            warnings.append(
                f"'{task}' has end_time ({end_str}) <= start_time ({start_str}) — skipping block"
            )
            fixes += 1
            continue

        parsed_blocks.append({
            **block,
            "_start": start,
            "_end": end if end != time(0, 0) else time(23, 59),
        })

    # ── Step 2: Remove duplicates ──
    seen = set()
    deduped = []
    for block in parsed_blocks:
        key = (block["start_time"], block["end_time"], block.get("task", ""))
        if key in seen:
            warnings.append(
                f"Duplicate block '{block.get('task', '')}' {block['start_time']}-{block['end_time']} — removed"
            )
            fixes += 1
            continue
        seen.add(key)
        deduped.append(block)
    parsed_blocks = deduped

    # ── Step 3: Sort by start time ──
    parsed_blocks.sort(key=lambda b: b["_start"])

    # ── Step 4: Detect and fix overlaps ──
    cleaned = []
    for i, block in enumerate(parsed_blocks):
        if not cleaned:
            cleaned.append(block)
            continue

        prev = cleaned[-1]
        prev_end = prev["_end"]
        curr_start = block["_start"]

        if curr_start < prev_end:
            # Overlap detected
            overlap_minutes = _time_diff_minutes(curr_start, prev_end)

            # Strategy: truncate the previous block's end to current block's start
            # (prefer keeping the later block intact)
            warnings.append(
                f"Overlap: '{prev.get('task', '')}' (ends {prev['end_time']}) "
                f"overlaps with '{block.get('task', '')}' (starts {block['start_time']}) "
                f"by {overlap_minutes}min — truncated '{prev.get('task', '')}' to end at {block['start_time']}"
            )
            prev["end_time"] = block["start_time"]
            prev["_end"] = curr_start
            fixes += 1

            # If truncation made the previous block zero-length, remove it
            if prev["_start"] >= prev["_end"]:
                warnings.append(
                    f"'{prev.get('task', '')}' became zero-length after fix — removed"
                )
                cleaned.pop()
                fixes += 1

        cleaned.append(block)

    # ── Step 5: Check wake/sleep bounds ──
    try:
        wake = datetime.strptime(wake_time, "%H:%M").time()
        sleep = datetime.strptime(sleep_time, "%H:%M").time()
    except ValueError:
        wake = time(7, 0)
        sleep = time(23, 0)

    for block in cleaned:
        if block["_start"] < wake:
            warnings.append(
                f"'{block.get('task', '')}' starts at {block['start_time']} "
                f"before wake time ({wake_time})"
            )
        if block["_end"] > sleep:
            warnings.append(
                f"'{block.get('task', '')}' ends at {block['end_time']} "
                f"after sleep time ({sleep_time})"
            )

    # ── Step 6: Clean up internal fields and build final schedule ──
    final_schedule = []
    for block in cleaned:
        clean_block = {k: v for k, v in block.items() if not k.startswith("_")}
        final_schedule.append(clean_block)

    is_valid = fixes == 0

    if fixes:
        logger.warning(
            "Schedule validation: %d fixes applied, %d warnings",
            fixes, len(warnings),
        )
    else:
        logger.info("Schedule validation: passed, no fixes needed")

    return {
        "schedule": final_schedule,
        "warnings": warnings,
        "fixes_applied": fixes,
        "is_valid": is_valid,
    }


def _time_diff_minutes(t1: time, t2: time) -> int:
    """Calculate minutes between two times (t2 - t1). Assumes same day."""
    d1 = t1.hour * 60 + t1.minute
    d2 = t2.hour * 60 + t2.minute
    return abs(d2 - d1)