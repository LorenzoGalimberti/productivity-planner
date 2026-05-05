"""
LLM-powered message parser.
Sends the user message to GPT and receives structured JSON back.
"""

import json
import logging
from datetime import date, timedelta

from openai import OpenAI
from django.conf import settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT_TEMPLATE = """You are a scheduling assistant parser. Your job is to extract structured data from a user message about their day/week planning.

Today's date is {today}. Tomorrow is {tomorrow}.

Extract the following from the user message:

1. calendar_events: Activities with SPECIFIC times mentioned (e.g. "work from 9 to 13", "dinner at 20:00", "meeting at 15"). These become fixed calendar blocks.
2. tasks: Activities WITHOUT specific times (e.g. "go to gym", "see my girlfriend", "study"). These will be scheduled by the planner.
3. constraints: Any limitations or special conditions (e.g. "I'm sick", "only have 3 free hours", "exam next week").
4. intent: One of: "day_plan", "week_plan", "replan"
5. target_date: The date being planned for (YYYY-MM-DD format)

RULES:
- If user says "from 9 to 13" or "9-13" or "at 9" -> that is a calendar_event with fixed times
- If user just mentions an activity without times -> that is a task
- "lunch" and "dinner" with specific times -> calendar_event. Without times -> ignore them
- For tasks, estimate a reasonable duration_minutes
- preferred_time can be: "morning", "afternoon", "evening", "any"
- "see girlfriend/boyfriend/partner/friends" -> preferred_time "evening"
- "gym/workout" -> preferred_time "afternoon"
- "study/work/deep work" -> preferred_time "morning"

Respond ONLY with valid JSON. No markdown. No explanation. Example format:

[EXAMPLE_START]
target_date: "2026-05-05"
intent: "day_plan"
calendar_events: array of objects with title, start_time (HH:MM), end_time (HH:MM)
tasks: array of objects with title, duration_minutes, priority, category, preferred_time
constraints: array of strings
[EXAMPLE_END]
"""


def parse_message_with_llm(message, target_date=None):
    """
    Send user message to LLM and get structured planning data back.
    Returns parsed dict or None if LLM fails.
    """
    api_key = settings.OPENAI_API_KEY
    if not api_key:
        logger.warning("OPENAI_API_KEY not set, cannot use LLM parser")
        return None

    today = date.today()
    tomorrow = today + timedelta(days=1)

    system = SYSTEM_PROMPT_TEMPLATE.format(
        today=today.isoformat(),
        tomorrow=tomorrow.isoformat(),
    )

    try:
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            temperature=0.1,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": message},
            ],
            max_tokens=1000,
        )

        raw = response.choices[0].message.content.strip()

        # Clean markdown fences if present
        if raw.startswith("```"):
            lines = raw.split("\n")
            lines = lines[1:]  # remove first line with ```json
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            raw = "\n".join(lines).strip()

        parsed = json.loads(raw)

        # Log token usage
        usage = response.usage
        if usage:
            logger.info(
                "LLM parse: %d input tokens, %d output tokens",
                usage.prompt_tokens, usage.completion_tokens,
            )

        logger.info(
            "LLM extracted: %d events, %d tasks, %d constraints",
            len(parsed.get("calendar_events", [])),
            len(parsed.get("tasks", [])),
            len(parsed.get("constraints", [])),
        )

        return parsed

    except json.JSONDecodeError as e:
        logger.error("LLM returned invalid JSON: %s", e)
        return None
    except Exception as e:
        logger.exception("LLM parser failed: %s", e)
        return None