"""
LLM-powered memory extractor.
Analyzes user messages and extracts facts to remember long-term.
"""

import json
import logging
from datetime import date, timedelta

from openai import OpenAI
from django.conf import settings

logger = logging.getLogger(__name__)

MEMORY_PROMPT = """You are a memory extraction system for a productivity planner AI agent.

Analyze the user message and extract FACTS worth remembering for future planning sessions.

Today's date is {today}.

CATEGORIES:
- "preference": how the user likes things (e.g. "prefers studying in the morning", "hates meetings after lunch")
- "goal": something the user is working toward (e.g. "preparing thesis on AI", "training for marathon")
- "constraint": a limitation (e.g. "works part-time on Tuesdays", "has a baby at home")
- "context": background info (e.g. "is a university student", "lives in Milan")
- "habit": behavioral patterns (e.g. "tends to skip gym on Fridays", "always postpones evening tasks")

RULES:
- Only extract MEANINGFUL facts that would help plan future days
- Do NOT extract temporary things like "wants to plan tomorrow" or "is asking for a schedule"
- Do NOT extract things already obvious from the schedule itself
- If there is nothing meaningful to extract, return an empty array
- Each fact should be a short, clear sentence
- expires_days: null for permanent facts, or number of days for temporary ones (exam in 2 weeks = 14)

Respond ONLY with valid JSON, no markdown:
[
  {{"content": "Prefers studying in the morning", "category": "preference", "expires_days": null}},
  {{"content": "Has AI exam in two weeks", "category": "goal", "expires_days": 14}}
]

If nothing meaningful to extract, respond with: []
"""


def extract_memory_with_llm(message, existing_notes=None):
    """
    Send user message to LLM and extract facts to remember.
    Returns list of dicts or None if LLM fails.
    """
    api_key = settings.OPENAI_API_KEY
    if not api_key:
        logger.warning("OPENAI_API_KEY not set, cannot use LLM memory")
        return None

    today = date.today()
    system = MEMORY_PROMPT.format(today=today.isoformat())

    # Add existing notes context so LLM doesn't duplicate
    user_content = f"User message: {message}"
    if existing_notes:
        notes_text = "\n".join(f"- {n}" for n in existing_notes[:10])
        user_content += f"\n\nAlready known facts (do NOT duplicate these):\n{notes_text}"

    try:
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            temperature=0.1,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
            max_tokens=500,
        )

        raw = response.choices[0].message.content.strip()

        # Clean markdown fences
        if raw.startswith("```"):
            lines = raw.split("\n")
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            raw = "\n".join(lines).strip()

        notes = json.loads(raw)

        usage = response.usage
        if usage:
            logger.info(
                "LLM memory: %d input tokens, %d output tokens",
                usage.prompt_tokens, usage.completion_tokens,
            )

        logger.info("LLM extracted %d memory notes", len(notes))
        return notes

    except json.JSONDecodeError as e:
        logger.error("LLM memory returned invalid JSON: %s", e)
        return None
    except Exception as e:
        logger.exception("LLM memory failed: %s", e)
        return None