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


import asyncio
from pathlib import Path

import yaml
from a2acode.backends.base import PermissionDecision, PermissionRequest
from a2acode.backends.session import BackendSession

from a2a_playback.recording import RecordingBackend
from a2a_playback.scenario import load_scenario
from a2a_playback.scrub import scrub_cwd


class _Scripted:
    """A stand-in inner backend. Runs a caller-supplied coroutine."""

    name = "scripted"

    def __init__(self, body):
        self._body = body

    async def drive(self, session, request):
        await self._body(session, request)


async def _drive_once(backend, prompt, *, answer=None, context_id="c1"):
    """Drive one turn, optionally answering a permission request."""
    session = BackendSession()
    session.start(lambda s: backend.drive(s, RunRequest(prompt=prompt, context_id=context_id)))
    events = [e async for e in session.drain()]
    if events and isinstance(events[-1], PermissionRequest) and answer is not None:
        session.resolve(PermissionDecision(events[-1].request_id, allow=answer))
        events += [e async for e in session.drain()]
    return events


async def test_a_recorded_turn_becomes_one_play(tmp_path):
    async def body(session, request):
        await session.emit(TextDelta(text="hi\n"))
        await session.emit(Result(num_turns=1, stop_reason="end_turn"))

    out = tmp_path / "rec.yaml"
    backend = RecordingBackend(_Scripted(body), out=out, cwd=str(tmp_path))
    await _drive_once(backend, "do the thing")

    doc = backend.document()
    assert len(doc["plays"]) == 1
    assert doc["plays"][0]["events"] == [
        {"text": "hi\n"},
        {"result": {"num_turns": 1, "stop_reason": "end_turn"}},
    ]


async def test_the_match_is_an_anchored_escaped_regex(tmp_path):
    async def body(session, request):
        await session.emit(Result(num_turns=1))

    backend = RecordingBackend(_Scripted(body), out=tmp_path / "r.yaml", cwd=str(tmp_path))
    await _drive_once(backend, "Add a /health endpoint.")

    regex = backend.document()["plays"][0]["match"]["regex"]
    assert regex.startswith("^") and regex.endswith("$")
    import re
    assert re.search(regex, "Add a /health endpoint.")
    assert not re.search(regex, "Please Add a /health endpoint. Now.")


async def test_events_after_a_gate_nest_into_the_branch_taken(tmp_path):
    async def body(session, request):
        await session.emit(TextDelta(text="before\n"))
        decision = await session.request_permission("Bash", {"command": "pytest -q"}, "")
        await session.emit(TextDelta(text=f"after allow={decision.allow}\n"))
        await session.emit(Result(num_turns=2))

    backend = RecordingBackend(_Scripted(body), out=tmp_path / "r.yaml", cwd=str(tmp_path))
    await _drive_once(backend, "run it", answer=True)

    events = backend.document()["plays"][0]["events"]
    assert events[0] == {"text": "before\n"}
    permission = events[1]["permission"]
    assert permission["tool"] == "Bash"
    assert permission["input"] == {"command": "pytest -q"}
    assert "on_deny" not in permission          # never happened; never invented
    assert permission["on_allow"] == [
        {"text": "after allow=True\n"},
        {"result": {"num_turns": 2}},
    ]


async def test_a_denial_records_on_deny_and_nothing_else(tmp_path):
    async def body(session, request):
        decision = await session.request_permission("Bash", {"command": "rm -rf x"}, "")
        await session.emit(TextDelta(text=f"allow={decision.allow}\n"))
        await session.emit(Result(num_turns=1))

    backend = RecordingBackend(_Scripted(body), out=tmp_path / "r.yaml", cwd=str(tmp_path))
    await _drive_once(backend, "delete it", answer=False)

    permission = backend.document()["plays"][0]["events"][0]["permission"]
    assert "on_allow" not in permission
    assert permission["on_deny"][0] == {"text": "allow=False\n"}


async def test_a_raising_turn_records_an_error_and_still_fails(tmp_path):
    async def body(session, request):
        await session.emit(TextDelta(text="partial\n"))
        raise RuntimeError("the model fell over")

    backend = RecordingBackend(_Scripted(body), out=tmp_path / "r.yaml", cwd=str(tmp_path))
    session = BackendSession()
    session.start(lambda s: backend.drive(s, RunRequest(prompt="go", context_id="c1")))
    with pytest.raises(RuntimeError, match="fell over"):
        [e async for e in session.drain()]

    events = backend.document()["plays"][0]["events"]
    assert events == [{"text": "partial\n"}, {"error": "the model fell over"}]


async def test_a_raising_turn_with_an_empty_message_still_writes_a_loadable_file(tmp_path):
    """`str(RuntimeError())`, `str(asyncio.TimeoutError())`, and
    `str(asyncio.CancelledError())` are all `""`, and scenario.py rejects an
    `error` event with an empty message. Falling back to the exception's type
    name is what keeps a plausible real failure (a bare timeout, a dropped
    connection) from writing a recording that cannot load."""
    async def body(session, request):
        await session.emit(TextDelta(text="partial\n"))
        raise RuntimeError()

    out = tmp_path / "r.yaml"
    backend = RecordingBackend(_Scripted(body), out=out, cwd=str(tmp_path))
    session = BackendSession()
    session.start(lambda s: backend.drive(s, RunRequest(prompt="go", context_id="c1")))
    with pytest.raises(RuntimeError):
        [e async for e in session.drain()]

    events = backend.document()["plays"][0]["events"]
    assert events == [{"text": "partial\n"}, {"error": "RuntimeError"}]

    scenario = load_scenario(out)  # must not raise: the file loads
    assert scenario.plays[0].events[-1] == {"error": "RuntimeError"}


async def test_a_cancelled_turn_still_records(tmp_path):
    """`BackendSession.start`'s own runner re-raises `asyncio.CancelledError`
    without relaying it through the session's error queue — a bare
    `except Exception` in `drive` would never see it at all, so
    `RecordingBackend` would silently drop the recording of an in-flight
    turn on uvicorn shutdown, session eviction, or a client disconnect.
    Calling `drive` directly (rather than through `session.start`/`drain`,
    which have their own cancellation semantics) isolates what
    `RecordingBackend` itself is responsible for: recording before it lets
    the exception through unchanged."""
    async def body(session, request):
        await session.emit(TextDelta(text="partial\n"))
        raise asyncio.CancelledError()

    out = tmp_path / "r.yaml"
    backend = RecordingBackend(_Scripted(body), out=out, cwd=str(tmp_path))
    session = BackendSession()
    with pytest.raises(asyncio.CancelledError):
        await backend.drive(session, RunRequest(prompt="go", context_id="c1"))

    events = backend.document()["plays"][0]["events"]
    assert events == [{"text": "partial\n"}, {"error": "CancelledError"}]


async def test_the_file_is_written_after_every_turn(tmp_path):
    """Real money was just spent; a ctrl-C should not cost the recording."""
    async def body(session, request):
        await session.emit(Result(num_turns=1))

    out = tmp_path / "rec.yaml"
    backend = RecordingBackend(_Scripted(body), out=out, cwd=str(tmp_path))

    await _drive_once(backend, "first prompt", context_id="c1")
    assert out.exists()
    assert len(yaml.safe_load(out.read_text())["plays"]) == 1

    await _drive_once(backend, "second prompt", context_id="c2")
    assert len(yaml.safe_load(out.read_text())["plays"]) == 2


async def test_the_written_file_loads_as_a_scenario(tmp_path):
    """The assumption all of M3 rests on: a recording is a scenario file."""
    async def body(session, request):
        await session.emit(TextDelta(text="ok\n"))
        await session.emit(Result(num_turns=1, stop_reason="end_turn"))

    out = tmp_path / "rec.yaml"
    backend = RecordingBackend(_Scripted(body), out=out, cwd=str(tmp_path))
    await _drive_once(backend, "hello")

    scenario = load_scenario(out)
    assert len(scenario.plays) == 1
    assert scenario.recorded["prompts"] == ["hello"]


async def test_cwd_is_scrubbed_out_of_a_recorded_play(tmp_path):
    async def body(session, request):
        await session.emit(ToolUse(
            name="Read", tool_input={"file_path": f"{tmp_path}/src/app.py"}, tool_use_id="t1"
        ))
        await session.emit(Result(num_turns=1))

    backend = RecordingBackend(_Scripted(body), out=tmp_path / "r.yaml", cwd=str(tmp_path))
    await _drive_once(backend, "read it")

    tool_use = backend.document()["plays"][0]["events"][0]["tool_use"]
    assert tool_use["input"]["file_path"] == "./src/app.py"


async def test_an_exception_after_a_gate_lands_inside_the_branch_taken(tmp_path):
    """A model that asks to run a command and then falls over is exactly the
    failure `error` recording exists to capture — and it must land where a
    replay can reach it, not at the root the gate already left behind."""
    async def body(session, request):
        await session.request_permission("Bash", {"command": "pytest -q"}, "")
        raise RuntimeError("blew up after the gate")

    backend = RecordingBackend(_Scripted(body), out=tmp_path / "r.yaml", cwd=str(tmp_path))
    session = BackendSession()
    session.start(lambda s: backend.drive(s, RunRequest(prompt="run it", context_id="c1")))
    events = [e async for e in session.drain()]
    assert isinstance(events[-1], PermissionRequest)
    session.resolve(PermissionDecision(events[-1].request_id, allow=True))
    with pytest.raises(RuntimeError, match="blew up after the gate"):
        [e async for e in session.drain()]

    events = backend.document()["plays"][0]["events"]
    assert events == [{
        "permission": {
            "tool": "Bash",
            "input": {"command": "pytest -q"},
            "on_allow": [{"error": "blew up after the gate"}],
        }
    }]


async def test_a_gate_with_nothing_after_it_still_writes_and_warns(tmp_path, capsys):
    """`scenario.py` refuses a permission whose branches are all empty. The
    file must still land on disk (a paid run already happened), with a loud
    warning telling the operator it needs a hand-edit before it can replay."""
    async def body(session, request):
        await session.request_permission("Bash", {"command": "pytest -q"}, "")

    out = tmp_path / "r.yaml"
    backend = RecordingBackend(_Scripted(body), out=out, cwd=str(tmp_path))
    await _drive_once(backend, "run it", answer=True)

    assert out.exists()
    written = yaml.safe_load(out.read_text())
    permission = written["plays"][0]["events"][0]["permission"]
    assert permission["on_allow"] == []

    captured = capsys.readouterr()
    assert str(out) in captured.err
    assert "branch" in captured.err


async def test_the_prompt_is_scrubbed_in_both_match_and_provenance(tmp_path):
    async def body(session, request):
        await session.emit(Result(num_turns=1))

    backend = RecordingBackend(_Scripted(body), out=tmp_path / "r.yaml", cwd=str(tmp_path))
    prompt = f"fix {tmp_path}/src/app.py"
    await _drive_once(backend, prompt)

    expected = scrub_cwd(prompt, str(tmp_path))
    doc = backend.document()
    assert doc["recorded"]["prompts"] == [expected]
    assert str(tmp_path) not in doc["plays"][0]["match"]["regex"]
    import re
    assert doc["plays"][0]["match"]["regex"] == f"^{re.escape(expected)}$"


async def test_a_repeated_prompt_warns_but_does_not_raise(tmp_path, capsys):
    """Two turns with the same prompt produce identical anchored regexes, so
    the second is unreachable on replay — legitimate to record, but the
    operator should be told, not left to find out by re-recording."""
    async def body(session, request):
        await session.emit(Result(num_turns=1))

    out = tmp_path / "r.yaml"
    backend = RecordingBackend(_Scripted(body), out=out, cwd=str(tmp_path))

    await _drive_once(backend, "do the thing", context_id="c1")
    assert "already has a recorded play" not in capsys.readouterr().err

    await _drive_once(backend, "do the thing", context_id="c2")
    captured = capsys.readouterr()
    assert "already has a recorded play" in captured.err
    assert "do the thing" in captured.err
    assert len(backend.document()["plays"]) == 2  # written anyway, not dropped


async def test_a_second_gate_nests_inside_the_first_branch_taken(tmp_path):
    async def body(session, request):
        d1 = await session.request_permission("Bash", {"command": "one"}, "")
        await session.emit(TextDelta(text=f"first allow={d1.allow}\n"))
        d2 = await session.request_permission("Bash", {"command": "two"}, "")
        await session.emit(TextDelta(text=f"second allow={d2.allow}\n"))
        await session.emit(Result(num_turns=2))

    backend = RecordingBackend(_Scripted(body), out=tmp_path / "r.yaml", cwd=str(tmp_path))
    session = BackendSession()
    session.start(lambda s: backend.drive(s, RunRequest(prompt="run it", context_id="c1")))

    events = [e async for e in session.drain()]
    assert isinstance(events[-1], PermissionRequest)
    session.resolve(PermissionDecision(events[-1].request_id, allow=True))

    events = [e async for e in session.drain()]
    assert isinstance(events[-1], PermissionRequest)
    session.resolve(PermissionDecision(events[-1].request_id, allow=True))

    [e async for e in session.drain()]

    events = backend.document()["plays"][0]["events"]
    assert events == [{
        "permission": {
            "tool": "Bash",
            "input": {"command": "one"},
            "on_allow": [
                {"text": "first allow=True\n"},
                {
                    "permission": {
                        "tool": "Bash",
                        "input": {"command": "two"},
                        "on_allow": [
                            {"text": "second allow=True\n"},
                            {"result": {"num_turns": 2}},
                        ],
                    }
                },
            ],
        }
    }]
