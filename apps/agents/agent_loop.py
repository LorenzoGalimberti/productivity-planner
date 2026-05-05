"""
Agent Loop — ReAct pattern implementation.
The LLM reasons, calls tools, observes results, and repeats until done.
"""

import json
import logging
import time as time_mod
from datetime import date, timedelta

from openai import OpenAI
from django.conf import settings

from apps.agents.agent_tools import TOOL_DEFINITIONS, execute_tool
from apps.users.models import User

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 15
MAX_TOKENS_PER_CALL = 2000

SYSTEM_PROMPT = """You are an autonomous AI productivity planner agent. You help users plan their day and week.

You have access to tools to read and modify the user's calendar, tasks, preferences, and memory.

## YOUR WORKFLOW

1. FIRST: Read existing calendar events for the target date
2. THEN: Read pending tasks
3. THEN: Read user preferences and memory notes
4. THEN: Find free time blocks
5. ANALYZE all the information and create an optimal schedule
6. If the user asks to MODIFY an existing plan, use move/delete/create tools to adjust
7. Save any important facts about the user to memory (preferences, goals, habits)
8. FINALLY: Respond with the complete schedule as JSON

## RULES

- NEVER overlap events
- Respect fixed calendar events (is_fixed=true)
- Place lunch break inside workday according to preferences
- Personal activities (girlfriend, friends) go in EVENING after work
- Study/deep work goes in focus hours
- Add 15min breaks between tasks
- If user says "move X to Y", find the event and use move_calendar_event
- If user says "add X at Y", use create_calendar_event
- If user says "remove/cancel X", use delete_calendar_event
- Only save MEANINGFUL memory notes (not temporary scheduling details)

## RESPONSE FORMAT

When you have gathered all info and built the schedule, respond with EXACTLY this JSON format (no markdown, no explanation before or after):

FINAL_SCHEDULE:
{
  "summary": "Brief description of the plan",
  "schedule": [
    {"start_time": "09:00", "end_time": "10:30", "task": "Task name", "block_type": "task", "reasoning": "Why here"}
  ],
  "warnings": [],
  "reasoning": "Overall explanation",
  "unscheduled_tasks": []
}

block_type must be one of: task, meeting, break, gym, focus, personal, errand, study, other

IMPORTANT: Your final message MUST start with "FINAL_SCHEDULE:" followed by the JSON. Do not add any text before "FINAL_SCHEDULE:".
"""


def run_agent_loop(
    user: User,
    message: str,
    target_date=None,
    previous_schedule=None,
):
    """
    Run the ReAct agent loop.
    The LLM decides which tools to call and in what order.
    Returns the final schedule dict.
    """
    api_key = settings.OPENAI_API_KEY
    if not api_key:
        logger.error("OPENAI_API_KEY not set")
        return None

    today = date.today()
    tomorrow = today + timedelta(days=1)

    if target_date is None:
        if "today" in message.lower():
            target_date = today
        elif "tomorrow" in message.lower():
            target_date = tomorrow
        else:
            target_date = tomorrow

    # Build initial user message
    user_content = f"Today is {today.isoformat()}. Target date: {target_date.isoformat()} ({target_date.strftime('%A')}).\n\n"
    user_content += f"User request: {message}"

    if previous_schedule:
        prev_blocks = previous_schedule.get("schedule", [])
        if prev_blocks:
            prev_text = "\n".join(
                f"- {b.get('time', '?')}: {b.get('task', '?')} ({b.get('type', 'other')})"
                for b in prev_blocks
            )
            user_content += f"\n\nPREVIOUS SCHEDULE (user wants to MODIFY this):\n{prev_text}"
            user_content += "\n\nThis is a follow-up request. Modify the existing plan, don't start from scratch."

    # Initialize conversation
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    client = OpenAI(api_key=api_key)
    total_tokens = 0
    start_time = time_mod.time()

    for iteration in range(MAX_ITERATIONS):
        logger.info("Agent loop iteration %d/%d", iteration + 1, MAX_ITERATIONS)

        try:
            response = client.chat.completions.create(
                model=settings.OPENAI_MODEL,
                messages=messages,
                tools=TOOL_DEFINITIONS,
                tool_choice="auto",
                max_tokens=MAX_TOKENS_PER_CALL,
                temperature=0.2,
            )
        except Exception as e:
            logger.exception("OpenAI API call failed: %s", e)
            return None

        # Track tokens
        if response.usage:
            total_tokens += response.usage.total_tokens

        choice = response.choices[0]
        assistant_message = choice.message

        # Add assistant message to conversation
        messages.append(assistant_message)

        # Check if agent wants to call tools
        if assistant_message.tool_calls:
            for tool_call in assistant_message.tool_calls:
                tool_name = tool_call.function.name
                try:
                    arguments = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError:
                    arguments = {}

                logger.info(
                    "Agent calls tool: %s(%s)",
                    tool_name,
                    json.dumps(arguments, default=str)[:200],
                )

                # Execute the tool
                result = execute_tool(user, tool_name, arguments)

                logger.info(
                    "Tool result: %s",
                    result[:300] if len(result) > 300 else result,
                )

                # Add tool result to conversation
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result,
                })

            # Continue the loop — agent will process tool results
            continue

        # No tool calls — agent is responding with final answer
        content = assistant_message.content or ""

        # Check for FINAL_SCHEDULE marker
        if "FINAL_SCHEDULE:" in content:
            json_str = content.split("FINAL_SCHEDULE:", 1)[1].strip()

            # Clean markdown fences
            if json_str.startswith("```"):
                lines = json_str.split("\n")
                lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                json_str = "\n".join(lines).strip()

            try:
                schedule_data = json.loads(json_str)
                duration_ms = int((time_mod.time() - start_time) * 1000)

                logger.info(
                    "Agent completed in %d iterations, %dms, %d tokens",
                    iteration + 1, duration_ms, total_tokens,
                )

                schedule_data["_agent_meta"] = {
                    "iterations": iteration + 1,
                    "total_tokens": total_tokens,
                    "duration_ms": duration_ms,
                }

                return schedule_data

            except json.JSONDecodeError as e:
                logger.error("Failed to parse final schedule JSON: %s", e)
                # Ask agent to fix it
                messages.append({
                    "role": "user",
                    "content": "Your JSON was invalid. Please respond again with valid JSON after FINAL_SCHEDULE:",
                })
                continue

        # Agent responded without FINAL_SCHEDULE marker
        logger.warning("Agent responded without FINAL_SCHEDULE marker, asking to complete")
        messages.append({
            "role": "user",
            "content": "Please complete your response with FINAL_SCHEDULE: followed by the JSON schedule.",
        })

    # Max iterations reached
    logger.error("Agent loop reached max iterations (%d)", MAX_ITERATIONS)
    return None