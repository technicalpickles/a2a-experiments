"""Recording: turn a live ``BackendEvent`` back into a scenario mapping.

The inverse of ``PlaybackBackend._to_backend_event`` in ``backend.py``. That
function reads a scenario's single-key event mapping and produces a
``BackendEvent``; ``to_scenario_event`` here does the reverse, so a real
agent run can be captured and later replayed byte-for-byte through
``playback``. The two live in different files and will rot apart over time —
``tests/test_recording.py``'s round-trip tests are what holds them together.

Only the serializer lives here. The tee that wraps a real backend and calls
it during a live run (``RecordingBackend``) is a later task.
"""

from __future__ import annotations

from typing import Any

from a2acode.backends.base import (
    FileChange, Notice, Plan, Result, TextDelta, Thought, ToolResult, ToolUse,
)


def to_scenario_event(event: Any) -> dict[str, Any]:
    """One BackendEvent as a single-key scenario mapping.

    The inverse of ``PlaybackBackend._to_backend_event``. These two will rot
    apart if nothing holds them together; tests/test_recording.py is that
    something.
    """
    match event:
        case TextDelta():
            return {"text": event.text}
        case Thought():
            return {"thought": event.text}
        case Notice():
            return {"notice": event.text}
        case ToolUse():
            return {"tool_use": {
                "name": event.name,
                "input": dict(event.tool_input),
                "id": event.tool_use_id,
            }}
        case ToolResult():
            body: dict[str, Any] = {"id": event.tool_use_id, "name": event.name}
            # Only when true: `failed: false` on every line is noise a human
            # scrubbing the file has to read past.
            if event.failed:
                body["failed"] = True
            if event.output:
                body["output"] = event.output
            return {"tool_result": body}
        case FileChange():
            return {"file_change": {"path": event.path, "diff": event.diff}}
        case Plan():
            return {"plan": _plan_body(event)}
        case Result():
            return {"result": _result_body(event)}
    raise ValueError(f"cannot record event of type {type(event).__name__}")


def _plan_body(plan: Plan) -> dict[str, Any]:
    """A plan is one of steps, markdown, or uri — never two.

    a2acode's renderer prefers markdown, then uri, and silently drops the rest,
    so writing two would ship a plan nobody authored. Mirrors the rule
    scenario._validate_plan enforces on the way back in.
    """
    if plan.markdown:
        return {"markdown": plan.markdown}
    if plan.uri:
        return {"uri": plan.uri}
    return {"steps": [
        {"content": s.content, "status": s.status, "priority": s.priority}
        for s in plan.steps
    ]}


def _result_body(result: Result) -> dict[str, Any]:
    """`session_id` is deliberately dropped.

    PlaybackBackend falls back to the request's context_id, so replaying a
    recorded session id would pin every replay to a session that no longer
    exists. Dropping it is strictly more correct than recording it.
    """
    body = {
        "cost_usd": result.cost_usd,
        "num_turns": result.num_turns,
        "usage": result.usage,
        "stop_reason": result.stop_reason,
    }
    return {k: v for k, v in body.items() if v is not None}
