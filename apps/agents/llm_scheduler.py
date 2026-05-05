"""
LLM-powered schedule generator.
Takes events, tasks, preferences and memory, sends to GPT,
receives an optimized schedule back.
"""

import json
import logging
from datetime import date, time
from typing import Any

from openai import OpenAI
from django.conf import settings

logger = logging.getLogger(__name__)

SCHEDULE_PROMPT = """You are an expert productivity planner. Generate an optimized daily schedule.

TARGET DATE: {target_date} ({day_name})

FIXED CALENDAR EVENTS (cannot be moved):
{events_text}

TASKS TO SCHEDULE (find the best time for each):
{tasks_text}

USER PREFERENCES:
- Wake up: {wake_up}
- Sleep: {sleep_time}
- Focus hours: {focus_start}-{focus_end} (best for demanding work)
- Lunch: {lunch_start}, duration {lunch_dur}min
- Preferred break: {break_dur}min between tasks
- Gym days: {gym_days}, preferred time: {gym_time}, duration {gym_dur}min

USER MEMORY (facts about this person):
{memory_text}

BEHAVIORAL DATA:
{metrics_text}

CONSTRAINTS FROM MESSAGE:
{constraints_text}

RULES:
1. NEVER overlap with fixed calendar events
2. Lunch break goes INSIDE the workday as a pause, not as a separate conflicting block
3. Personal activities (girlfriend, friends, family) go in the EVENING after work
4. Study/deep work goes in focus hours when possible
5. Add breaks between tasks
6. If today is a gym day, include gym
7. If completion rate is low (<50%), limit to 4-5 tasks max
8. If user is sick, make a light schedule
9. Explain your reasoning for each block

Respond ONLY with valid JSON, no markdown, no backticks:
[
  {{"start_time": "09:00", "end_time": "10:30", "task": "Task name", "block_type": "task", "reasoning": "Why here"}}
]

block_type must be one of: task, meeting, break, gym, focus, personal, errand, study, other
"""


def generate_schedule_with_llm(
    target_date,
    calendar_events,
    tasks,
    preferences,
    memory_notes,
    metrics,
    constraints,
    previous_schedule=None,
):
    """
    Send all planning context to LLM and get optimized schedule back.
    If previous_schedule is provided, modify it instead of creating from scratch.
    """
    api_key = settings.OPENAI_API_KEY
    if not api_key:
        logger.warning("OPENAI_API_KEY not set, cannot use LLM scheduler")
        return None

    # Format events
    if calendar_events:
        events_text = "\n".join(
            f"- {e['title']}: {e['start_time']} to {e['end_time']} (type: {e.get('event_type', 'other')}, fixed: {e.get('is_fixed', False)})"
            for e in calendar_events
        )
    else:
        events_text = "No fixed events."

    # Format tasks
    if tasks:
        tasks_text = "\n".join(
            f"- {t['title']} (priority: {t.get('priority', 'medium')}, estimated: {t.get('estimated_minutes', 60)}min, category: {t.get('category', '')})"
            for t in tasks
        )
    else:
        tasks_text = "No tasks to schedule."

    # Format memory
    if memory_notes:
        memory_text = "\n".join(
            f"- [{n.get('category', 'note')}] {n['content']}"
            for n in memory_notes
        )
    else:
        memory_text = "No memory notes."

    # Format metrics
    if metrics:
        metrics_text = "\n".join(
            f"- {k}: {v}" for k, v in metrics.items()
        )
    else:
        metrics_text = "No behavioral data yet."

    # Format constraints
    constraints_text = "\n".join(f"- {c}" for c in constraints) if constraints else "None."

    # Get preferences with defaults
    wake_up = preferences.get("wake_up_time", time(7, 0))
    sleep_t = preferences.get("sleep_time", time(23, 0))
    focus_s = preferences.get("focus_start", time(9, 0))
    focus_e = preferences.get("focus_end", time(12, 0))
    lunch_s = preferences.get("lunch_start", time(12, 30))
    lunch_d = preferences.get("lunch_duration_minutes", 60)
    break_d = preferences.get("break_duration_minutes", 15)
    gym_d_list = preferences.get("preferred_gym_days", [])
    gym_t = preferences.get("preferred_gym_time", time(17, 0))
    gym_dur = preferences.get("gym_duration_minutes", 60)

    def fmt_time(t):
        if isinstance(t, time):
            return t.strftime("%H:%M")
        return str(t)

    day_name = target_date.strftime("%A")

    # Build the prompt
    prompt = SCHEDULE_PROMPT.format(
        target_date=target_date.isoformat(),
        day_name=day_name,
        events_text=events_text,
        tasks_text=tasks_text,
        wake_up=fmt_time(wake_up),
        sleep_time=fmt_time(sleep_t),
        focus_start=fmt_time(focus_s),
        focus_end=fmt_time(focus_e),
        lunch_start=fmt_time(lunch_s),
        lunch_dur=lunch_d,
        break_dur=break_d,
        gym_days=", ".join(gym_d_list) if gym_d_list else "none",
        gym_time=fmt_time(gym_t),
        gym_dur=gym_dur,
        memory_text=memory_text,
        metrics_text=metrics_text,
        constraints_text=constraints_text,
    )

    # If follow-up, add previous schedule context
    if previous_schedule:
        prev_blocks = previous_schedule.get("schedule", [])
        if prev_blocks:
            prev_text = "\n".join(
                f"- {b.get('time', '?')}: {b.get('task', '?')} ({b.get('type', 'other')})"
                for b in prev_blocks
            )
            prompt += f"\n\nPREVIOUS SCHEDULE (user wants to MODIFY this, not start from scratch):\n{prev_text}"
            prompt += "\n\nThe user's new message is a MODIFICATION request. Keep unchanged blocks and only adjust what the user asked to change."

    try:
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            temperature=0.2,
            messages=[
                {"role": "system", "content": "You are a productivity scheduling expert. Return ONLY valid JSON arrays."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=2000,
        )

        raw = response.choices[0].message.content.strip()

        # Clean markdown fences
        if raw.startswith("```"):
            lines = raw.split("\n")
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            raw = "\n".join(lines).strip()

        schedule = json.loads(raw)

        usage = response.usage
        if usage:
            logger.info(
                "LLM scheduler: %d input tokens, %d output tokens",
                usage.prompt_tokens, usage.completion_tokens,
            )

        is_followup = "follow-up" if previous_schedule else "fresh"
        logger.info("LLM generated %s schedule with %d blocks", is_followup, len(schedule))
        return schedule

    except json.JSONDecodeError as e:
        logger.error("LLM scheduler returned invalid JSON: %s", e)
        return None
    except Exception as e:
        logger.exception("LLM scheduler failed: %s", e)
        return None