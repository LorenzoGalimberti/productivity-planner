"""
Agent Loop v4 — ReAct pattern with tracing, model routing, and DB-free planning.

CHANGES FROM v3:
- Fresh plans: model only READS data and produces FINAL_SCHEDULE (no create/move/delete)
- Follow-ups: model still uses move/delete/create tools (needs to modify existing events)
- Filtered TOOL_DEFINITIONS: fresh plans only get read-only tools
- Expected token usage for fresh plans: ~6-7k (down from 19k)
"""

import json
import logging
import time as time_mod
from datetime import date, timedelta

from openai import OpenAI
from django.conf import settings

from apps.agents.agent_tools import TOOL_DEFINITIONS, execute_tool
from apps.agents.tracing import Tracer, _truncate
from apps.users.models import User

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 8
MAX_ITERATIONS_FOLLOWUP = 8
MAX_TOKENS_PER_CALL = 2000

# Tools the model can use for fresh plans (read-only)
READ_ONLY_TOOLS = {
    "get_calendar_events",
    "get_tasks",
    "get_user_preferences",
    "get_memory_notes",
    "find_free_blocks",
    "save_memory_note",
}

# Filter TOOL_DEFINITIONS for read-only mode
TOOL_DEFINITIONS_READONLY = [
    t for t in TOOL_DEFINITIONS
    if t["function"]["name"] in READ_ONLY_TOOLS
]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SYSTEM PROMPT v4 — FRESH PLAN (no event creation)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SYSTEM_PROMPT_PLAN = """You are an autonomous AI productivity planner agent. You help users plan their day optimally.

You have access to tools to READ the user's calendar, tasks, preferences, and memory.

## STEP-BY-STEP WORKFLOW

Follow these steps IN ORDER. Do not skip any step.

### Step 1 — Gather all data
Call ALL of these tools in a SINGLE action (batch them together):
- get_calendar_events (for the target date)
- get_tasks
- get_user_preferences
- get_memory_notes

### Step 2 — Find available time
Call find_free_blocks to see available time slots.

### Step 3 — Build the schedule mentally
Using all the data you gathered, build the optimal schedule in your head.
Place activities in this priority order:
1. FIXED events (keep exactly as they are)
2. Work block (respect the user's work hours)
3. Lunch break INSIDE work hours (not before, not after)
4. High-priority tasks in focus hours
5. Gym (if applicable for the day)
6. Personal activities (girlfriend, friends) in EVENING after work
7. Lower-priority tasks in remaining slots
8. Breaks (15min) between different activities

### Step 4 — Verify
Before responding, mentally check:
- Does any event overlap with another? If yes, fix it.
- Is lunch within work hours? If not, move it.
- Are personal activities after work ends? If not, move them.
- Do all times make sense (end_time > start_time, within wake/sleep bounds)?

### Step 5 — Respond with FINAL_SCHEDULE
Do NOT create calendar events with tools. Just produce the FINAL_SCHEDULE JSON.
The system will automatically create events in the database from your schedule.

## CONFLICT RULES (CRITICAL)

- Two events MUST NEVER have overlapping times
- If event A ends at 11:00, event B can start at 11:00 (adjacent is OK)
- If event A ends at 11:00, event B CANNOT start at 10:45 (overlap is NOT OK)
- Work is a CONTAINER: lunch, focus work, and meetings happen INSIDE work hours, not alongside them
- Do NOT create a separate "Work" block if work is already in the calendar — schedule activities within it

## MEMORY AND PREFERENCES HIERARCHY

When data conflicts between sources, follow this priority:
1. Calendar events (highest priority — what is actually scheduled)
2. Structured preferences (wake_up_time, lunch_start, etc.)
3. Memory notes (learned facts — may be outdated)
4. Defaults (if nothing else is available)

If preferences are empty {}, use memory notes. If both are empty, use reasonable defaults:
- Work: 09:00-18:00
- Lunch: 13:00-14:00
- Wake up: 07:00
- Sleep: 23:00
- Focus hours: 09:00-12:00

## EXAMPLE

User says: "domani lavoro 9-18, palestra, studio AI e cena con la ragazza"
Target date: 2026-05-07 (Wednesday)

Good schedule:
- 09:00-13:00 Work (focus hours + work tasks)
- 13:00-14:00 Lunch Break
- 14:00-18:00 Work
- 18:00-18:15 Break
- 18:15-19:15 Gym
- 19:15-19:30 Break
- 19:30-21:00 Study AI
- 21:00-22:30 See girlfriend + dinner

## RESPONSE FORMAT

Respond with EXACTLY this JSON (no markdown, no text before or after):

FINAL_SCHEDULE:
{
  "summary": "Brief description of the plan",
  "schedule": [
    {"start_time": "09:00", "end_time": "10:30", "task": "Task name", "block_type": "task", "reasoning": "Why here"}
  ],
  "warnings": [],
  "reasoning": "Overall explanation of scheduling decisions",
  "unscheduled_tasks": []
}

block_type must be one of: task, meeting, break, gym, focus, personal, errand, study, other

IMPORTANT: Your final message MUST start with "FINAL_SCHEDULE:" followed by the JSON.
"""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SYSTEM PROMPT v4 — FOLLOW-UP (with mutation tools)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SYSTEM_PROMPT_FOLLOWUP = """You are an autonomous AI productivity planner agent. The user wants to MODIFY their existing schedule.

## CRITICAL RULE FOR MODIFICATIONS

You must do the MINIMUM work needed. Do NOT rebuild the entire schedule.
ONLY move/change the specific event the user asked about. Do NOT touch other events unless they directly overlap with the moved event's NEW time.

### For "move X to Y" requests:
1. Call get_calendar_events to find the event
2. Call move_calendar_event with the event_id and new times (HH:MM format, NEVER YYYY-MM-DD HH:MM)
3. Check: does the new time OVERLAP (not just be adjacent to) another event? If YES, move that event too. If NO, do NOT move anything else.
4. Immediately respond with FINAL_SCHEDULE showing the updated full schedule

"Free time" or "Relax" blocks do NOT need to be moved — they are flexible by nature. Just leave a gap if needed.

### For "add X at Y" requests:
1. Call get_calendar_events to check for conflicts
2. Call create_calendar_event
3. Immediately respond with FINAL_SCHEDULE

### For "remove/cancel X" requests:
1. Call get_calendar_events to find the event
2. Call delete_calendar_event
3. Immediately respond with FINAL_SCHEDULE

## RULES

- Do NOT call get_tasks, get_user_preferences, get_memory_notes — you don't need them for a modification
- Do NOT recreate events that already exist
- Do NOT delete and recreate an event when move_calendar_event works
- If the user expresses a PREFERENCE or HABIT (e.g. "I usually...", "I prefer...", "di solito...", "normally..."), save it using save_memory_note BEFORE producing FINAL_SCHEDULE. This is important for future planning sessions.
- After your tool calls succeed, IMMEDIATELY produce FINAL_SCHEDULE — do not keep calling tools
- Do NOT call get_calendar_events a second time after making changes — you already know the updated state from the move/create/delete results
- The FINAL_SCHEDULE must include ALL events for the day (not just the changed ones) — reconstruct it from the first get_calendar_events call plus your changes
- Maximum 3-4 tool calls for any modification

## TOOL PARAMETER FORMATS

- Dates: YYYY-MM-DD (e.g. "2026-05-07")
- Times: HH:MM (e.g. "09:00", "13:30", "21:00") — NEVER include the date in time fields
- event_id: UUID string from get_calendar_events results

## CONFLICT RULES

- Two events MUST NEVER have overlapping times
- If moving event A causes overlap with event B, move event B as well
- Adjacent times are OK (A ends 11:00, B starts 11:00)

## RESPONSE FORMAT

FINAL_SCHEDULE:
{
  "summary": "Brief description of what changed",
  "schedule": [
    {"start_time": "09:00", "end_time": "10:30", "task": "Task name", "block_type": "task", "reasoning": "Why here"}
  ],
  "warnings": [],
  "reasoning": "What was modified and why",
  "unscheduled_tasks": []
}

block_type must be one of: task, meeting, break, gym, focus, personal, errand, study, other

IMPORTANT: Your final message MUST start with "FINAL_SCHEDULE:" followed by the JSON.
"""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Agent loop
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def run_agent_loop(
    user: User,
    message: str,
    target_date=None,
    previous_schedule=None,
    is_followup: bool = False,
):
    """
    Run the ReAct agent loop with full tracing.
    
    Fresh plans: model reads data + produces FINAL_SCHEDULE (no DB writes).
    Follow-ups: model uses move/delete/create tools to modify existing events.
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

    # ── Model routing ──
    default_model = getattr(settings, "OPENAI_MODEL", "gpt-4o-mini")
    strong_model = getattr(settings, "OPENAI_MODEL_STRONG", default_model)
    model_name = strong_model if is_followup else default_model

    # ── Select prompt, tools, and max iterations ──
    if is_followup:
        system_prompt = SYSTEM_PROMPT_FOLLOWUP
        tools = TOOL_DEFINITIONS          # full tools (including create/move/delete)
        max_iters = MAX_ITERATIONS_FOLLOWUP
    else:
        system_prompt = SYSTEM_PROMPT_PLAN
        tools = TOOL_DEFINITIONS_READONLY  # read-only tools
        max_iters = MAX_ITERATIONS

    # ── Initialize tracer ──
    tracer = Tracer(
        user=user.username,
        target_date=target_date.isoformat(),
        intent="replan" if is_followup else "day_plan",
    )
    tracer.user_message = message

    # Build initial user message
    user_content = (
        f"Today is {today.isoformat()}. "
        f"Target date: {target_date.isoformat()} ({target_date.strftime('%A')}).\n\n"
        f"User request: {message}"
    )

    if previous_schedule and is_followup:
        prev_blocks = previous_schedule.get("schedule", [])
        if prev_blocks:
            prev_text = "\n".join(
                f"- {b.get('time', '?')}: {b.get('task', '?')} ({b.get('type', 'other')})"
                for b in prev_blocks
            )
            user_content += f"\n\nCURRENT SCHEDULE:\n{prev_text}"
            user_content += "\n\nModify ONLY what the user asked. Keep everything else unchanged."

    # Initialize conversation
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    client = OpenAI(api_key=api_key)
    total_tokens = 0
    start_time = time_mod.time()

    for iteration in range(max_iters):
        logger.info(
            "Agent loop iteration %d/%d (model=%s, %s)",
            iteration + 1, max_iters, model_name,
            "follow-up" if is_followup else "plan",
        )

        with tracer.iteration(iteration + 1) as iter_ctx:

            # ── LLM call ──
            with tracer.llm_call(model=model_name) as llm_span:
                try:
                    response = client.chat.completions.create(
                        model=model_name,
                        messages=messages,
                        tools=tools,
                        tool_choice="auto",
                        max_tokens=MAX_TOKENS_PER_CALL,
                        temperature=0.2,
                    )
                except Exception as e:
                    logger.exception("OpenAI API call failed: %s", e)
                    llm_span.record(status="error", error=str(e))
                    tracer.finish(status="error", error=f"OpenAI API failed: {e}")
                    tracer.save()
                    return None

                # Record LLM stats
                if response.usage:
                    llm_span.record(
                        prompt_tokens=response.usage.prompt_tokens,
                        completion_tokens=response.usage.completion_tokens,
                        total_tokens=response.usage.total_tokens,
                    )
                    total_tokens += response.usage.total_tokens

                choice = response.choices[0]
                assistant_message = choice.message

                # Save preview of response for debugging
                if assistant_message.content:
                    llm_span.record(
                        response_preview=_truncate(assistant_message.content, 300)
                    )
                elif assistant_message.tool_calls:
                    tool_names = [tc.function.name for tc in assistant_message.tool_calls]
                    llm_span.record(
                        response_preview=f"[tool_calls: {', '.join(tool_names)}]"
                    )

            # Add assistant message to conversation
            messages.append(assistant_message)

            # ── Tool calls ──
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

                    with tracer.tool_call(tool_name, arguments) as tc_span:
                        result = execute_tool(user, tool_name, arguments)

                        # Check if tool returned an error
                        is_error = False
                        try:
                            result_parsed = json.loads(result)
                            if isinstance(result_parsed, dict) and "error" in result_parsed:
                                is_error = True
                                tc_span.record(
                                    status="error",
                                    error=result_parsed["error"],
                                )
                        except (json.JSONDecodeError, TypeError):
                            pass

                        if not is_error:
                            tc_span.record(
                                status="success",
                                tool_output_preview=_truncate(result, 300),
                            )

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

                # Continue the loop
                continue

            # ── No tool calls — check for final answer ──
            content = assistant_message.content or ""

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
                        "model": model_name,
                        "is_followup": is_followup,
                    }

                    # ── Finalize & save trace ──
                    tracer.finish(status="success", schedule=schedule_data)
                    trace_path = tracer.save()
                    logger.info("Trace: %s", tracer.summary())

                    schedule_data["_trace_file"] = trace_path

                    return schedule_data

                except json.JSONDecodeError as e:
                    logger.error("Failed to parse final schedule JSON: %s", e)
                    tracer.log_event("json_parse_error", str(e), {"raw": _truncate(json_str, 500)})
                    messages.append({
                        "role": "user",
                        "content": "Your JSON was invalid. Please respond again with valid JSON after FINAL_SCHEDULE:",
                    })
                    continue

            # Agent responded without FINAL_SCHEDULE marker
            logger.warning("Agent responded without FINAL_SCHEDULE marker, asking to complete")
            tracer.log_event("missing_marker", "Response without FINAL_SCHEDULE:", {
                "content_preview": _truncate(content, 200),
            })
            messages.append({
                "role": "user",
                "content": "Please complete your response with FINAL_SCHEDULE: followed by the JSON schedule.",
            })

    # ── Max iterations reached ──
    logger.error("Agent loop reached max iterations (%d)", max_iters)
    tracer.finish(status="max_iterations", error=f"Reached {max_iters} iterations without completing")
    tracer.save()
    logger.info("Trace: %s", tracer.summary())

    return None