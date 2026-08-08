"""Recording: turning a live run back into a scenario document.

The load-bearing property is that `to_scenario_event` and
`PlaybackBackend._to_backend_event` are inverses. They live in different files
and will rot apart; these tests are what pin them together.
"""

from __future__ import annotations

import dataclasses

import pytest
from a2acode.backends.base import (
    FileChange, Notice, Plan, PlanStep, Result, RunRequest,
    TextDelta, Thought, ToolResult, ToolUse,
)

from a2a_playback.backend import PlaybackBackend
from a2a_playback.recording import to_scenario_event
from a2a_playback.scenario import _validate_event

ROUND_TRIP_CASES = [
    TextDelta(text="hello\n"),
    Thought(text="thinking"),
    Notice(text="heads up"),
    ToolUse(name="Read", tool_input={"file_path": "src/app.py"}, tool_use_id="t1"),
    ToolResult(tool_use_id="t1", name="Read", failed=False, output="ok"),
    ToolResult(tool_use_id="t2", name="Bash", failed=True, output="boom"),
    FileChange(path="src/app.py", diff="@@ -1 +1 @@\n-a\n+b\n"),
    Plan(steps=[PlanStep(content="Do it", status="in_progress", priority="high")]),
    Plan(markdown="# plan"),
    Plan(uri="file:///plan.md"),
]


@pytest.mark.parametrize("event", ROUND_TRIP_CASES, ids=lambda e: type(e).__name__)
def test_a_serialized_event_replays_as_itself(event):
    """Serialize an event, feed it back through playback's parser, get it back."""
    serialized = to_scenario_event(event)
    _validate_event(serialized, 1, "<test>")  # the scenario loader must accept it

    (kind, body), = serialized.items()
    backend = PlaybackBackend.__new__(PlaybackBackend)  # no repo needed for mapping
    replayed = backend._to_backend_event(kind, body, RunRequest(prompt="p", context_id="c1"))

    assert replayed == event


def test_a_result_round_trips_except_for_the_session_id():
    """`session_id` is intentionally asymmetric: recording drops it, and
    replay refills it from the request's context_id. Everything else must
    survive unchanged."""
    event = Result(cost_usd=0.017, num_turns=4, usage={"input_tokens": 10},
                   stop_reason="end_turn")
    serialized = to_scenario_event(event)
    _validate_event(serialized, 1, "<test>")

    backend = PlaybackBackend.__new__(PlaybackBackend)
    replayed = backend._to_backend_event(
        "result", serialized["result"], RunRequest(prompt="p", context_id="c1")
    )

    assert replayed == dataclasses.replace(event, session_id="c1")


def test_session_id_is_dropped_from_a_recorded_result():
    """PlaybackBackend falls back to context_id, so a recorded dead UUID is
    strictly worse than no session_id at all."""
    serialized = to_scenario_event(Result(session_id="abc123", num_turns=1))
    assert "session_id" not in serialized["result"]


def test_an_unknown_event_type_is_refused():
    with pytest.raises(ValueError, match="cannot record"):
        to_scenario_event(object())
