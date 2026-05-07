"""
Agent Tracing — local observability for the Productivity Planner.

Captures the full execution tree of an agent run:
  - LLM calls (prompt, response, tokens, latency)
  - Tool calls (name, input, output, status, latency)
  - Iterations, retries, errors
  - Final output and metadata

Usage:
    from apps.agents.tracing import Tracer

    tracer = Tracer(user="lorenzo", target_date="2026-05-06")

    with tracer.iteration(1):
        with tracer.llm_call(model="gpt-4.1") as llm:
            # ... make OpenAI call ...
            llm.record(prompt_tokens=890, completion_tokens=45)

        with tracer.tool_call("get_calendar_events", {"date_from": "2026-05-06"}) as tc:
            # ... execute tool ...
            tc.record(output=result, status="success")

    tracer.finish(status="success", schedule=schedule_data)
    tracer.save()  # writes to logs/traces/trace_<id>.json
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Span: a single tracked operation
# ──────────────────────────────────────────────

class Span:
    """A timed operation within a trace (LLM call, tool call, etc.)."""

    def __init__(self, span_type: str, name: str, metadata: dict = None):
        self.span_id = str(uuid.uuid4())[:8]
        self.span_type = span_type  # "llm", "tool", "iteration", "custom"
        self.name = name
        self.metadata = metadata or {}
        self.start_time: float = 0
        self.end_time: float = 0
        self.status: str = "running"
        self.error: Optional[str] = None
        self.children: list[dict] = []

        # LLM-specific
        self.prompt_tokens: int = 0
        self.completion_tokens: int = 0
        self.total_tokens: int = 0
        self.model: str = ""
        self.prompt_preview: str = ""
        self.response_preview: str = ""

        # Tool-specific
        self.tool_input: dict = {}
        self.tool_output_preview: str = ""

    def record(self, **kwargs):
        """Record data into this span."""
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
            else:
                self.metadata[key] = value

    def to_dict(self) -> dict:
        """Serialize span to dict."""
        duration_ms = int((self.end_time - self.start_time) * 1000) if self.end_time else 0

        result = {
            "span_id": self.span_id,
            "type": self.span_type,
            "name": self.name,
            "status": self.status,
            "duration_ms": duration_ms,
        }

        if self.error:
            result["error"] = self.error

        if self.span_type == "llm":
            result["model"] = self.model
            result["prompt_tokens"] = self.prompt_tokens
            result["completion_tokens"] = self.completion_tokens
            result["total_tokens"] = self.total_tokens
            if self.prompt_preview:
                result["prompt_preview"] = self.prompt_preview
            if self.response_preview:
                result["response_preview"] = self.response_preview

        elif self.span_type == "tool":
            result["input"] = self.tool_input
            if self.tool_output_preview:
                result["output_preview"] = self.tool_output_preview

        if self.metadata:
            result["metadata"] = self.metadata

        if self.children:
            result["children"] = self.children

        return result


# ──────────────────────────────────────────────
# Tracer: the main tracing class
# ──────────────────────────────────────────────

class Tracer:
    """
    Captures the full execution tree of an agent run.

    Each trace has:
      - A unique trace_id
      - Metadata (user, date, intent)
      - A list of iterations, each with LLM calls and tool calls
      - Aggregated stats (total tokens, latency, tool errors, etc.)
    """

    def __init__(
        self,
        user: str = "",
        target_date: str = "",
        intent: str = "",
        trace_dir: str = "logs/traces",
    ):
        self.trace_id = str(uuid.uuid4())
        self.user = user
        self.target_date = target_date
        self.intent = intent
        self.trace_dir = trace_dir
        self.start_time = time.time()
        self.end_time: float = 0

        # Execution tree
        self.iterations: list[dict] = []
        self._current_iteration: Optional[dict] = None

        # Aggregated stats
        self.total_llm_calls: int = 0
        self.total_tool_calls: int = 0
        self.total_tokens: int = 0
        self.total_prompt_tokens: int = 0
        self.total_completion_tokens: int = 0
        self.tool_errors: int = 0
        self.retries: int = 0

        # Final result
        self.status: str = "running"
        self.final_output: dict = {}
        self.error: Optional[str] = None

        # User message (for debugging)
        self.user_message: str = ""

    # ── Context managers ──

    @contextmanager
    def iteration(self, number: int):
        """Track an agent loop iteration."""
        iteration_data = {
            "iteration": number,
            "start_time_ms": int((time.time() - self.start_time) * 1000),
            "llm_calls": [],
            "tool_calls": [],
            "events": [],
        }
        self._current_iteration = iteration_data
        try:
            yield iteration_data
        finally:
            iteration_data["end_time_ms"] = int((time.time() - self.start_time) * 1000)
            iteration_data["duration_ms"] = (
                iteration_data["end_time_ms"] - iteration_data["start_time_ms"]
            )
            self.iterations.append(iteration_data)
            self._current_iteration = None

    @contextmanager
    def llm_call(self, model: str = ""):
        """Track an LLM API call within the current iteration."""
        span = Span("llm", "openai_chat_completion")
        span.model = model
        span.start_time = time.time()
        self.total_llm_calls += 1

        try:
            yield span
            span.status = "success"
        except Exception as e:
            span.status = "error"
            span.error = str(e)
            raise
        finally:
            span.end_time = time.time()
            # Accumulate tokens
            self.total_tokens += span.total_tokens
            self.total_prompt_tokens += span.prompt_tokens
            self.total_completion_tokens += span.completion_tokens

            if self._current_iteration is not None:
                self._current_iteration["llm_calls"].append(span.to_dict())

    @contextmanager
    def tool_call(self, tool_name: str, arguments: dict = None):
        """Track a tool execution within the current iteration."""
        span = Span("tool", tool_name)
        span.tool_input = _safe_serialize(arguments or {})
        span.start_time = time.time()
        self.total_tool_calls += 1

        try:
            yield span
            if span.status == "running":
                span.status = "success"
        except Exception as e:
            span.status = "error"
            span.error = str(e)
            self.tool_errors += 1
            raise
        finally:
            span.end_time = time.time()
            if span.status == "error":
                self.tool_errors += 1

            if self._current_iteration is not None:
                self._current_iteration["tool_calls"].append(span.to_dict())

    # ── Event logging ──

    def log_event(self, event_type: str, message: str, data: dict = None):
        """Log a custom event (retry, warning, json parse error, etc.)."""
        event = {
            "type": event_type,
            "message": message,
            "time_ms": int((time.time() - self.start_time) * 1000),
        }
        if data:
            event["data"] = _safe_serialize(data)

        if event_type == "retry":
            self.retries += 1

        if self._current_iteration is not None:
            self._current_iteration["events"].append(event)

    # ── Finalization ──

    def finish(self, status: str = "success", schedule: dict = None, error: str = None):
        """Mark the trace as complete."""
        self.end_time = time.time()
        self.status = status
        if schedule:
            self.final_output = _safe_serialize(schedule)
        if error:
            self.error = error

    def to_dict(self) -> dict:
        """Serialize the full trace to a dict."""
        duration_ms = int((self.end_time - self.start_time) * 1000) if self.end_time else 0

        return {
            # Identity
            "trace_id": self.trace_id,
            "timestamp": datetime.now().isoformat(),
            "user": self.user,
            "target_date": self.target_date,
            "intent": self.intent,
            "user_message": self.user_message,

            # Result
            "status": self.status,
            "error": self.error,

            # Aggregated stats
            "stats": {
                "total_duration_ms": duration_ms,
                "total_iterations": len(self.iterations),
                "total_llm_calls": self.total_llm_calls,
                "total_tool_calls": self.total_tool_calls,
                "total_tokens": self.total_tokens,
                "prompt_tokens": self.total_prompt_tokens,
                "completion_tokens": self.total_completion_tokens,
                "tool_errors": self.tool_errors,
                "retries": self.retries,
            },

            # Execution tree
            "iterations": self.iterations,

            # Final output (schedule)
            "final_output": self.final_output,
        }

    def save(self) -> str:
        """Save trace to a JSON file. Returns the file path."""
        os.makedirs(self.trace_dir, exist_ok=True)

        # Filename: trace_<date>_<short_id>.json
        date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        short_id = self.trace_id[:8]
        filename = f"trace_{date_str}_{short_id}.json"
        filepath = os.path.join(self.trace_dir, filename)

        trace_data = self.to_dict()

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(trace_data, f, indent=2, ensure_ascii=False, default=str)

        logger.info("Trace saved: %s (%d iterations, %d tokens, %s)",
                     filename, len(self.iterations), self.total_tokens, self.status)

        return filepath

    # ── Quick summary for logging ──

    def summary(self) -> str:
        """One-line summary for log output."""
        duration_ms = int((self.end_time - self.start_time) * 1000) if self.end_time else 0
        return (
            f"Trace {self.trace_id[:8]} | {self.status} | "
            f"{len(self.iterations)} iters | {self.total_llm_calls} LLM calls | "
            f"{self.total_tool_calls} tool calls ({self.tool_errors} errors) | "
            f"{self.total_tokens} tokens | {duration_ms}ms"
        )


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def _safe_serialize(obj: Any, max_length: int = 500) -> Any:
    """Make an object JSON-safe and truncate long strings."""
    if isinstance(obj, dict):
        return {k: _safe_serialize(v, max_length) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [_safe_serialize(item, max_length) for item in obj]
    elif isinstance(obj, str):
        if len(obj) > max_length:
            return obj[:max_length] + f"... [truncated, {len(obj)} chars]"
        return obj
    elif isinstance(obj, (int, float, bool, type(None))):
        return obj
    elif isinstance(obj, (date, datetime)):
        return obj.isoformat()
    elif hasattr(obj, "strftime"):
        return obj.strftime("%H:%M")
    else:
        return str(obj)


def _truncate(text: str, max_len: int = 300) -> str:
    """Truncate text for preview fields."""
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    return text[:max_len] + f"... [{len(text)} chars]"