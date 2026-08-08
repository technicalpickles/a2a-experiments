# M3: the scenario factory — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture a real agent run as a scenario file, so the rig's scenario library has a recorded backbone instead of a hand-imagined one.

**Architecture:** A `RecordingBackend` decorator wraps any a2acode `Backend` and hands the inner backend a proxy `BackendSession` that tees `emit()` and `request_permission()` into a scenario document, forwarding both unchanged. A `rig-record` CLI builds a real backend via a2acode's public `make_backend()`, wraps it, and serves it so a real A2A client can drive the run — including the `input-required` permission round trip. Recording `playback` itself is a supported mode, which is what makes the whole thing testable at zero inference cost.

**Tech Stack:** Python 3.13+, `a2a-sdk==1.1.2`, `a2acode` pinned at `v0.6.2`, PyYAML, pytest + pytest-asyncio, `uv`.

**Spec:** `docs/superpowers/specs/2026-08-07-m3-scenario-factory-design.md`

## Global Constraints

- All rig code lives under `a2a-rig/`. Run everything from that directory; `uv run` resolves against the project you stand in.
- Verification command, both backends, after every task: `uv run pytest --backend playback` and `uv run pytest --backend echo`. Baseline before this plan: **108 passed, 4 xfailed** on each, ~5.5s.
- The 4 xfails are upstream cancel bugs and are **meant to stay failing**. Never "fix" them.
- **The backend-agnostic suite must need zero edits.** It has for three consecutive milestones. If a change here forces an edit to an existing test body, stop and reassess rather than editing it.
- `a2a_playback` may import a2acode (it implements its protocol). The pytest harness in `a2a_rig` must **not** import a2acode to take shortcuts around the network — it drives servers over the wire.
- Scenario documents accept exactly two top-level keys: `plays` and optional `recorded`. Do not add a third.
- No em-dashes are required or forbidden in code or docs here; match surrounding prose style.
- Commit after each task. Write commit messages to `scratch/` (globally gitignored) and use `git commit -F scratch/<name>.txt`.

---

### permission-branch-fail-loud

Reaching an unscripted permission branch currently emits nothing and ends the turn with no `result`. Recording only the taken branch makes that path common, so it has to become loud first.

**Files:**
- Modify: `a2a-rig/src/a2a_playback/backend.py` (`_run_events`, `_permission`, `_answered`)
- Modify: `a2a-rig/src/a2a_playback/scenario.py` (`_validate_event`, permission arm)
- Test: `a2a-rig/tests/test_playback.py`, `a2a-rig/tests/test_repo.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `PlaybackBackend._run_events(session, events, request, play)` — note the new fourth positional parameter `play: Play`, threaded so error messages can name the play. Later tasks do not call it directly.

- [ ] **Step 1: Write the failing tests**

Add to `a2a-rig/tests/test_repo.py` (load-time rule):

```python
def test_a_permission_with_no_branches_is_rejected_at_load(tmp_path):
    """A gate that can do nothing on any answer is a mistake, not a valid state."""
    home = _repo(
        tmp_path,
        "gateless",
        scenarios={
            "only.yaml": """
plays:
  - match: {}
    events:
      - permission: { tool: Bash, input: { command: "ls" } }
"""
        },
    )
    with pytest.raises(ScenarioError, match="at least one branch"):
        load_repo(home)
```

Add to `a2a-rig/tests/test_playback.py` (runtime rule). Use the file's **existing** helpers — `_gate(**body)` builds a one-play repo whose only event is a `Bash` permission, `_driven(raw)` starts a session on it, `_events(session)` drains one stretch with a timeout. Do not add new helpers:

```python
async def test_an_unscripted_permission_branch_fails_loudly():
    """A recording holds only the branch that was taken. Taking the other one
    must say so, not end the turn in silence with no result."""
    session = _driven(_gate(
        input={"command": "pytest -q"},
        on_allow=[{"result": {"num_turns": 1, "stop_reason": "end_turn"}}],
    ))

    events = await _events(session)
    assert type(events[-1]).__name__ == "PermissionRequest"

    session.resolve(PermissionDecision(request_id=events[-1].request_id, allow=False))
    with pytest.raises(ScenarioError, match="on_deny"):
        await _events(session)


async def test_a_recorded_allow_branch_still_replays():
    """The companion case: the branch that *was* recorded works untouched."""
    session = _driven(_gate(
        input={"command": "pytest -q"},
        on_allow=[{"result": {"num_turns": 1, "stop_reason": "end_turn"}}],
    ))

    events = await _events(session)
    session.resolve(PermissionDecision(request_id=events[-1].request_id, allow=True))
    resumed = await _events(session)

    assert type(resumed[-1]).__name__ == "Result"
```

**Add no imports.** `PermissionDecision` is already imported; the file deliberately identifies event types by `type(x).__name__` rather than importing `PermissionRequest`/`Result`, and these tests follow that. `pytest` and `ScenarioError` are already imported too.

- [ ] **Step 2: Run them to verify they fail**

```bash
cd a2a-rig
uv run pytest tests/test_repo.py::test_a_permission_with_no_branches_is_rejected_at_load -v
uv run pytest tests/test_playback.py::test_an_unscripted_permission_branch_fails_loudly -v
```

Expected: the load-time test fails (no error raised); the runtime test fails because `drain()` yields nothing and raises nothing.

- [ ] **Step 3: Add the load-time check**

In `scenario.py`, inside `_validate_event`'s `if kind == "permission":` arm, after the existing `tool` check:

```python
        if not any(body.get(b) for b in ("on_allow", "on_deny", "on_timeout")):
            raise ScenarioError(
                f"{where}: play #{play_index} `permission` has at least one branch "
                f"missing — it needs at least one of `on_allow`, `on_deny`, or "
                f"`on_timeout`. A gate that does nothing on every answer cannot be "
                f"what was meant"
            )
```

Word the message so it contains the literal text `at least one branch`, which the test matches on.

- [ ] **Step 4: Make the runtime branch loud**

In `backend.py`, thread `play` through and raise on a missing branch. Replace `_run_events`, `_permission`, and `_answered`:

```python
    async def drive(self, session: BackendSession, request: RunRequest) -> None:
        turn = self._next_turn(request.context_id)
        play = self.repo.select(request.prompt, turn)
        await self._run_events(session, play.events, request, play)

    async def _run_events(self, session, events, request, play) -> None:
        for event in events:
            (kind, body), = event.items()
            await self._delay(body)
            if kind == "permission":
                await self._permission(session, body, request, play)
                return
            if kind == "error":
                raise ScriptedError(message_of(body))
            await session.emit(self._to_backend_event(kind, body, request))

    async def _permission(self, session, body, request, play) -> None:
        asking = session.request_permission(
            body["tool"], body.get("input") or {}, body.get("description") or ""
        )
        timeout_ms = body.get("timeout_ms")
        if timeout_ms is None:
            branch = await self._answered(asking, body, play)
        else:
            try:
                branch = await self._answered(
                    asyncio.wait_for(asking, float(timeout_ms) / 1000.0), body, play
                )
            except TimeoutError:
                branch = self._branch(body, "on_timeout", play, fallback="on_deny")
        await self._run_events(session, branch, request, play)

    async def _answered(self, asking, body, play):
        decision = await asking
        return self._branch(body, "on_allow" if decision.allow else "on_deny", play)

    def _branch(self, body, name, play, *, fallback=None):
        """The branch an answer selects, or a loud failure.

        Absent and empty are treated alike: both are a dead end that would end
        the turn with no `result`, which reads to a frontend as its own bug.
        A recording carries only the branch that was actually taken, so this is
        the common way to meet an unrecorded path.
        """
        events = body.get(name) or (body.get(fallback) if fallback else None)
        if not events:
            wanted = name if not fallback else f"{name}` or `{fallback}"
            raise ScenarioError(
                f"repo {self.repo.repo_id!r}: {play.describe()} reached `{wanted}` "
                f"on tool {body.get('tool')!r}, which is not scripted. A recording "
                f"holds only the branch that was taken — script it, or record a run "
                f"that takes it. Refusing to guess."
            )
        return events
```

- [ ] **Step 5: Run the new tests, then the whole suite on both backends**

```bash
cd a2a-rig
uv run pytest tests/test_repo.py::test_a_permission_with_no_branches_is_rejected_at_load tests/test_playback.py::test_an_unscripted_permission_branch_fails_loudly -v
uv run pytest --backend playback
uv run pytest --backend echo
```

Expected: new tests PASS; both suites **111 passed, 4 xfailed** (baseline 108 plus the three tests added here: one in `test_repo.py`, two in `test_playback.py`). If any pre-existing test body needs editing to pass, **stop** — that violates a global constraint and means the change landed in the wrong layer.

- [ ] **Step 6: Commit**

```bash
git add a2a-rig/src/a2a_playback/backend.py a2a-rig/src/a2a_playback/scenario.py \
        a2a-rig/tests/test_playback.py a2a-rig/tests/test_repo.py
git commit -F scratch/commit-msg-fail-loud.txt
```

---

### scenario-event-serializer

The inverse of `backend.py`'s `_to_backend_event`. Pure function, no server, no async.

**Files:**
- Create: `a2a-rig/src/a2a_playback/recording.py`
- Test: `a2a-rig/tests/test_recording.py`

**Interfaces:**
- Consumes: a2acode's `BackendEvent` dataclasses from `a2acode.backends.base`.
- Produces: `to_scenario_event(event: BackendEvent) -> dict[str, Any]` — a single-key mapping like `{"text": "hi"}`, exactly the shape `scenario._validate_event` accepts. Raises `ValueError` on an unhandled event type.

- [ ] **Step 1: Write the failing round-trip test**

Create `a2a-rig/tests/test_recording.py`:

```python
"""Recording: turning a live run back into a scenario document.

The load-bearing property is that `to_scenario_event` and
`PlaybackBackend._to_backend_event` are inverses. They live in different files
and will rot apart; these tests are what pin them together.
"""

from __future__ import annotations

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
    Result(cost_usd=0.017, num_turns=4, usage={"input_tokens": 10}, stop_reason="end_turn"),
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


def test_session_id_is_dropped_from_a_recorded_result():
    """PlaybackBackend falls back to context_id, so a recorded dead UUID is
    strictly worse than no session_id at all."""
    serialized = to_scenario_event(Result(session_id="abc123", num_turns=1))
    assert "session_id" not in serialized["result"]


def test_an_unknown_event_type_is_refused():
    with pytest.raises(ValueError, match="cannot record"):
        to_scenario_event(object())
```

Note: `Result` round-trips only because the test omits `session_id`; the `Result` case in `ROUND_TRIP_CASES` has none, and `_to_backend_event` fills `session_id` from `request.context_id`. Set the `Result` case's expectation accordingly — if the assert fails on `session_id`, compare with `dataclasses.replace(event, session_id="c1")` for that one case rather than weakening the others.

- [ ] **Step 2: Run it to verify it fails**

```bash
cd a2a-rig && uv run pytest tests/test_recording.py -v
```

Expected: `ModuleNotFoundError: No module named 'a2a_playback.recording'`.

- [ ] **Step 3: Write `recording.py` with just the serializer**

```python
"""Recording: tee a real backend's events into a scenario document.

Wraps any a2acode ``Backend`` and hands the inner backend a proxy
``BackendSession``. Everything a backend produces goes through ``emit`` or
``request_permission``, so those two methods are the whole interception
surface — and it sits above the vendor, which is what makes ``acp`` and
``claude`` recordings interchangeable (DESIGN-v3 §6).

The recorder observes. It never changes what the inner backend receives, and
never changes what the caller sees.

Written to be upstreamable to a2acode on its own, the way ``backend.py`` is.
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
```

- [ ] **Step 4: Run the tests**

```bash
cd a2a-rig && uv run pytest tests/test_recording.py -v
```

Expected: PASS. If the `Plan(steps=...)` case fails on `priority`, check that `_to_backend_event` defaults `priority` to `""` — it does, so an empty priority round-trips.

- [ ] **Step 5: Commit**

```bash
git add a2a-rig/src/a2a_playback/recording.py a2a-rig/tests/test_recording.py
git commit -F scratch/commit-msg-serializer.txt
```

---

### scrub-module

Mechanical redaction only: the `--cwd` prefix, everywhere it appears. `session_id` is already handled by the serializer.

**Files:**
- Create: `a2a-rig/src/a2a_playback/scrub.py`
- Test: `a2a-rig/tests/test_scrub.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `scrub_cwd(value: Any, cwd: str) -> Any` — walks nested dicts/lists/strings and replaces occurrences of the absolute `cwd` (and its `~`-expanded, resolved form) with `.`. Returns a new structure; does not mutate.

- [ ] **Step 1: Write the failing tests**

Create `a2a-rig/tests/test_scrub.py`:

```python
"""Scrubbing: the mechanical half of making a recording checkable-in.

Narrow on purpose. Absolute paths are universal and mechanical; everything
else is a human read-through, named as a step in the runbook.
"""

from __future__ import annotations

from a2a_playback.scrub import scrub_cwd

CWD = "/Users/someone/scratch/demo-app"


def test_a_tool_input_path_is_made_relative():
    play = {"tool_use": {"name": "Read", "input": {"file_path": f"{CWD}/src/app.py"}}}
    assert scrub_cwd(play, CWD) == {
        "tool_use": {"name": "Read", "input": {"file_path": "./src/app.py"}}
    }


def test_tool_output_is_scrubbed_too():
    """pytest output quotes absolute paths, and it is a plain string field."""
    play = {"tool_result": {"id": "t1", "output": f"{CWD}/tests/test_app.py .. [100%]"}}
    assert scrub_cwd(play, CWD)["tool_result"]["output"] == "./tests/test_app.py .. [100%]"


def test_a_diff_body_is_scrubbed():
    play = {"file_change": {"path": f"{CWD}/src/app.py", "diff": f"--- a{CWD}/src/app.py\n"}}
    scrubbed = scrub_cwd(play, CWD)
    assert scrubbed["file_change"]["path"] == "./src/app.py"
    assert CWD not in scrubbed["file_change"]["diff"]


def test_nested_permission_branches_are_scrubbed():
    play = {"permission": {"tool": "Bash", "on_allow": [
        {"tool_result": {"id": "t2", "output": f"ran in {CWD}"}}
    ]}}
    assert CWD not in scrub_cwd(play, CWD)["permission"]["on_allow"][0]["tool_result"]["output"]


def test_cost_and_usage_survive():
    """Realistic numbers are the point; they are not secrets."""
    play = {"result": {"cost_usd": 0.017, "usage": {"input_tokens": 10}}}
    assert scrub_cwd(play, CWD) == play


def test_the_input_is_not_mutated():
    play = {"tool_use": {"input": {"file_path": f"{CWD}/a.py"}}}
    scrub_cwd(play, CWD)
    assert play["tool_use"]["input"]["file_path"] == f"{CWD}/a.py"
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd a2a-rig && uv run pytest tests/test_scrub.py -v
```

Expected: `ModuleNotFoundError: No module named 'a2a_playback.scrub'`.

- [ ] **Step 3: Write `scrub.py`**

```python
"""Making a recording fit to check in.

Narrow and mechanical on purpose: absolute paths leak the machine that made
the recording and are the one redaction that is universal, unambiguous, and
safe to do without asking. Everything else is a human read-through, named as
a step in the runbook rather than left to hope.

No configurable rule surface. For a handful of recordings against a throwaway
app, that would be a config format to design, document, and test for no
benefit.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def _forms(cwd: str) -> list[str]:
    """Every spelling of the working directory a backend might emit.

    An agent may report the path it was given, or the symlink-resolved one.
    Longest first, so replacing the resolved form never leaves a fragment of
    a longer match behind.
    """
    raw = os.path.expanduser(cwd)
    candidates = {raw.rstrip("/"), str(Path(raw).resolve()).rstrip("/")}
    return sorted((c for c in candidates if c), key=len, reverse=True)


def scrub_cwd(value: Any, cwd: str) -> Any:
    """Replace the working-directory prefix with `.` throughout a structure.

    Walks dicts, lists, and strings; leaves numbers, booleans, and None alone,
    so cost_usd/usage/num_turns come through untouched. Returns new objects
    rather than mutating, because the caller still holds the live events.
    """
    forms = _forms(cwd)

    def walk(node: Any) -> Any:
        if isinstance(node, str):
            for form in forms:
                node = node.replace(form, ".")
            return node
        if isinstance(node, dict):
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(value)
```

- [ ] **Step 4: Run the tests**

```bash
cd a2a-rig && uv run pytest tests/test_scrub.py -v
```

Expected: PASS, 6 tests.

- [ ] **Step 5: Commit**

```bash
git add a2a-rig/src/a2a_playback/scrub.py a2a-rig/tests/test_scrub.py
git commit -F scratch/commit-msg-scrub.txt
```

---

### recording-backend

The decorator and its proxy session. Tested against a hand-rolled inner backend — no server, no network.

**Files:**
- Modify: `a2a-rig/src/a2a_playback/recording.py`
- Test: `a2a-rig/tests/test_recording.py`

**Interfaces:**
- Consumes: `to_scenario_event` and `scrub_cwd` from the two previous tasks.
- Produces:
  - `RecordingBackend(inner: Backend, *, out: Path, cwd: str = ".", provenance: dict[str, Any] | None = None)` with attribute `name = "recording"` and `async def drive(session, request) -> None`.
  - `RecordingBackend.document() -> dict[str, Any]` — the scenario document built so far, `{"recorded": {...}, "plays": [...]}`.
  - `RecordingBackend.write() -> None` — dumps `document()` to `out` as YAML.

- [ ] **Step 1: Write the failing tests**

Append to `a2a-rig/tests/test_recording.py`:

```python
import asyncio
from pathlib import Path

import yaml
from a2acode.backends.base import PermissionDecision, PermissionRequest
from a2acode.backends.session import BackendSession

from a2a_playback.recording import RecordingBackend
from a2a_playback.scenario import load_scenario


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
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd a2a-rig && uv run pytest tests/test_recording.py -v
```

Expected: `ImportError: cannot import name 'RecordingBackend'`.

- [ ] **Step 3: Implement the decorator and proxy**

Append to `a2a-rig/src/a2a_playback/recording.py`. Add exactly these to the imports at the top — nothing else, or the module carries unused names: `import re`, `from pathlib import Path`, `import yaml`, and `from .scrub import scrub_cwd`. (Timestamps are stamped by `record.py`, not here, so no `datetime` import belongs in this file.)

```python
class _RecordingSession:
    """A BackendSession that tees what passes through it.

    Delegates everything it does not override, so a backend reaching for
    `set_canceller`, `is_parked`, or anything a2acode adds later still gets the
    real session. Only `emit` and `request_permission` carry meaning worth
    recording, and both forward unchanged — the recorder observes, it does not
    intervene.
    """

    def __init__(self, inner, sink: list[dict]) -> None:
        # Bypass __setattr__-free delegation deliberately: these two names are
        # ours, everything else falls through to the real session.
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "_stack", [sink])

    def __getattr__(self, name: str):
        return getattr(object.__getattribute__(self, "_inner"), name)

    @property
    def _current(self) -> list[dict]:
        return object.__getattribute__(self, "_stack")[-1]

    async def emit(self, event) -> None:
        self._current.append(to_scenario_event(event))
        await object.__getattribute__(self, "_inner").emit(event)

    async def request_permission(self, tool_name, tool_input, description=""):
        node: dict[str, Any] = {"tool": tool_name, "input": dict(tool_input)}
        if description:
            node["description"] = description
        self._current.append({"permission": node})

        decision = await object.__getattribute__(self, "_inner").request_permission(
            tool_name, tool_input, description
        )

        # Everything from here belongs inside the branch that was actually
        # taken. Required, not stylistic: PlaybackBackend._run_events returns
        # after handling a permission, so a post-gate event left at the top
        # level would never fire on replay.
        branch: list[dict] = []
        node["on_allow" if decision.allow else "on_deny"] = branch
        object.__getattribute__(self, "_stack").append(branch)
        return decision


class RecordingBackend:
    """Wraps any Backend and writes what it does as a scenario document."""

    name = "recording"

    def __init__(self, inner, *, out, cwd: str = ".", provenance=None) -> None:
        self._inner = inner
        self._out = Path(out)
        self._cwd = cwd
        self._prompts: list[str] = []
        self._plays: list[dict] = []
        self._provenance = dict(provenance or {})

    async def drive(self, session, request) -> None:
        events: list[dict] = []
        proxy = _RecordingSession(session, events)
        try:
            await self._inner.drive(proxy, request)
        except Exception as exc:
            # A real failure recorded is exactly the coverage recording
            # otherwise cannot manufacture. Keep it, then let a2acode's real
            # failure path run untouched.
            events.append({"error": str(exc)})
            self._finish(request.prompt, events)
            raise
        self._finish(request.prompt, events)

    def _finish(self, prompt: str, events: list[dict]) -> None:
        self._prompts.append(prompt)
        self._plays.append({
            "match": {"regex": f"^{re.escape(prompt)}$"},
            "events": scrub_cwd(events, self._cwd),
        })
        self.write()

    def document(self) -> dict[str, Any]:
        return {
            "recorded": {
                **self._provenance,
                # Machine-readable because the refresh loop consumes it:
                # "re-record the library's source prompts" needs the prompts
                # back. A source list for re-recording, not an index into
                # `plays` — pruning a play during scrub does not corrupt it.
                "prompts": list(self._prompts),
            },
            "plays": list(self._plays),
        }

    def write(self) -> None:
        """Rewrite the whole file. Every turn, not at shutdown."""
        self._out.parent.mkdir(parents=True, exist_ok=True)
        self._out.write_text(
            yaml.safe_dump(self.document(), sort_keys=False, allow_unicode=True)
        )

    async def aclose(self) -> None:
        """Forward to the inner backend if it pools anything (ACPBackend does)."""
        closer = getattr(self._inner, "aclose", None)
        if closer is not None:
            await closer()
```

- [ ] **Step 4: Run the tests**

```bash
cd a2a-rig && uv run pytest tests/test_recording.py -v
```

Expected: PASS, all cases. If `test_the_written_file_loads_as_a_scenario` fails on the `recorded` key, check that `document()` emits `recorded` even when `provenance` is empty — `prompts` alone must be enough to make it a mapping.

- [ ] **Step 5: Run both suites**

```bash
cd a2a-rig && uv run pytest --backend playback && uv run pytest --backend echo
```

Expected: both green, counts up by the new tests, still 4 xfailed.

- [ ] **Step 6: Commit**

```bash
git add a2a-rig/src/a2a_playback/recording.py a2a-rig/tests/test_recording.py
git commit -F scratch/commit-msg-recording-backend.txt
```

---

### record-cli

`rig-record` serves a real backend, wrapped. Recording `playback` is a first-class mode, which is what makes the next task's round-trip test possible without test-only scaffolding.

**Files:**
- Create: `a2a-rig/src/a2a_playback/record.py`
- Modify: `a2a-rig/pyproject.toml` (add the console script)
- Modify: `a2a-rig/src/a2a_rig/server.py` (launch `rig-record` from tests)
- Test: `a2a-rig/tests/test_record_cli.py`

**Interfaces:**
- Consumes: `RecordingBackend` from the previous task; `load_repo` from `repo.py`; `PlaybackBackend` from `backend.py`.
- Produces:
  - `a2a_playback.record.main(argv: list[str] | None = None) -> int`
  - `a2a_playback.record.build_recording_backend(args) -> RecordingBackend`
  - `a2a_rig.server.serve(..., record_out: str | Path | None = None)` — when set, launches `python -m a2a_playback.record` instead of `rig-serve`, passing `--out record_out`.

- [ ] **Step 1: Write the failing CLI tests**

Create `a2a-rig/tests/test_record_cli.py`:

```python
"""rig-record's argument handling.

Config problems are user errors, not crashes: say what is wrong and exit 2,
the same contract rig-serve already honors.
"""

from __future__ import annotations

import pytest

from a2a_playback.record import main


def test_out_is_required(capsys):
    assert main(["--backend", "echo"]) == 2
    assert "--out" in capsys.readouterr().err


def test_writing_into_a_scenarios_directory_is_refused(tmp_path, capsys):
    """Staging is the point. A flag that could skip the scrub is one that will."""
    target = tmp_path / "repos" / "billing-api" / "scenarios" / "rec.yaml"
    assert main(["--backend", "echo", "--out", str(target)]) == 2
    assert "scenarios" in capsys.readouterr().err


def test_playback_mode_requires_a_repo(tmp_path, capsys):
    out = tmp_path / "rec.yaml"
    assert main(["--backend", "playback", "--out", str(out)]) == 2
    assert "--repo" in capsys.readouterr().err


def test_a_missing_repo_is_a_user_error_not_a_traceback(tmp_path, capsys):
    out = tmp_path / "rec.yaml"
    code = main(["--backend", "playback", "--repo", str(tmp_path / "nope"), "--out", str(out)])
    assert code == 2
    assert "not a repo" in capsys.readouterr().err


def test_an_unknown_backend_is_a_user_error(tmp_path, capsys):
    out = tmp_path / "rec.yaml"
    assert main(["--backend", "nonsense", "--out", str(out)]) == 2
```

These must not start a server. Structure `main` so every validation happens before `uvicorn.run`.

- [ ] **Step 2: Run to verify they fail**

```bash
cd a2a-rig && uv run pytest tests/test_record_cli.py -v
```

Expected: `ModuleNotFoundError: No module named 'a2a_playback.record'`.

- [ ] **Step 3: Write `record.py`**

```python
"""`rig-record` — a real agent, served, with everything it does written down.

Recording taps at the BackendEvent level *inside* the server, so something has
to drive it from outside. That something has to be a real A2A client, because
the permission round trip *is* an `input-required` exchange with a caller.
Running one-shot would mean either --permission-mode acceptEdits (which never
records a gate at all) or inventing a console prompt. Serving means a recorded
run went through the same path Phase 2 and Phase 5 did.

`--backend playback` is supported on purpose: recording the scripted backend is
how the recorder is tested end to end without spending a cent on inference, and
making that a real mode beats bolting test-only scaffolding onto the side.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from a2acode.server import build_app

from .backend import PlaybackBackend
from .recording import RecordingBackend
from .repo import RepoError, load_repo
from .scenario import ScenarioError

SCENARIOS_DIR = "scenarios"


def build_recording_backend(args) -> RecordingBackend:
    """The backend under test, wrapped. Raises RepoError/ScenarioError/ValueError."""
    if args.backend == "playback":
        inner = PlaybackBackend(load_repo(args.repo))
        label = "playback"
    else:
        from a2acode.backends import make_backend

        if args.backend == "acp":
            inner = make_backend("acp", agent=args.agent, cwd=args.cwd)
            label = f"acp:{args.agent}"
        elif args.backend == "claude":
            inner = make_backend(
                "claude", cwd=args.cwd, max_budget_usd=args.max_budget_usd
            )
            label = "claude"
        else:
            inner = make_backend(args.backend)
            label = args.backend

    return RecordingBackend(
        inner,
        out=args.out,
        cwd=args.cwd,
        provenance={
            "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "backend": label,
            # Which producer's event vocabulary this recording describes. The
            # refresh loop's whole premise is re-recording after an upstream
            # bump and diffing, which needs to know what it is diffing against.
            "a2acode": _a2acode_version(),
        },
    )


def _a2acode_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("a2acode")
    except PackageNotFoundError:  # a bare checkout on sys.path
        return "unknown"


def _check_out(path: str, parser) -> None:
    """`--out` is a staging path, and that is enforced.

    A raw recording carries unscrubbed absolute paths and possibly a shadowing
    match, so landing it live means the next rig-serve boot fails on a file
    nobody has read yet. Promotion is a deliberate `mv` after the scrub; a flag
    that could skip it is a flag that will.
    """
    if SCENARIOS_DIR in Path(path).parts:
        parser.error(
            f"--out {path} is inside a {SCENARIOS_DIR}/ directory. Record to a "
            f"staging path, read the file, then move it in — an unscrubbed "
            f"recording landing live can fail the repo at boot"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="rig-record", description="Serve a real agent and record it as a scenario."
    )
    parser.add_argument("--backend", default="acp", help="acp, claude, echo, or playback.")
    parser.add_argument("--agent", default="claude", help="ACP agent the acp backend fronts.")
    parser.add_argument("--cwd", default=".", help="Project directory the agent works in.")
    parser.add_argument("--repo", help="Repo directory, required when --backend playback.")
    parser.add_argument("--out", help="Where to write the scenario file (a staging path).")
    parser.add_argument("--max-budget-usd", type=float, default=None,
                        help="Cost ceiling per run. Honored by --backend claude only; "
                             "ACPBackend takes no ceiling.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9300)
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args(argv)

    if not args.out:
        parser.error("--out is required: recording with nowhere to write is a no-op")
    _check_out(args.out, parser)
    if args.backend == "playback" and not args.repo:
        parser.error("--backend playback needs a --repo to play")

    url = f"http://{args.host}:{args.port}/"
    try:
        backend = build_recording_backend(args)
    except (RepoError, ScenarioError, ValueError) as exc:
        print(f"rig-record: {exc}", file=sys.stderr)
        return 2

    app = build_app(backend, url=url)
    print(f"rig-record: backend={args.backend} out={args.out} card={url}", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Note: `parser.error` raises `SystemExit(2)`, which the tests expect as a return of 2. Wrap the `parser.parse_args`/`parser.error` calls so `main` returns 2 instead of propagating `SystemExit` — add at the top of `main`:

```python
    class _Parser(argparse.ArgumentParser):
        def error(self, message):  # exit 2 without a traceback, like rig-serve
            print(f"rig-record: {message}", file=sys.stderr)
            raise _BadArgs()
```

with a module-level `class _BadArgs(Exception): pass`, and wrap the body of `main` in `try: ... except _BadArgs: return 2`. Use `_Parser` in place of `argparse.ArgumentParser`.

- [ ] **Step 4: Register the console script**

In `a2a-rig/pyproject.toml`:

```toml
[project.scripts]
rig-serve = "a2a_playback.serve:main"
rig-record = "a2a_playback.record:main"
```

- [ ] **Step 5: Teach the test harness to launch it**

In `a2a-rig/src/a2a_rig/server.py`, add a `record_out` parameter to `serve()` and a command builder beside `_playback_command`:

```python
def _record_command(port: int, repo: str | Path | None, out: str | Path) -> list[str]:
    """rig-record, launched the same way rig-serve is: module, not console script,
    so the harness works from a bare checkout without an install step."""
    return [
        sys.executable, "-m", "a2a_playback.record",
        "--backend", "playback",
        "--repo", str(repo or DEFAULT_REPO),
        "--out", str(out),
        "--host", "127.0.0.1",
        "--port", str(port),
    ]
```

In `serve()`, add `record_out: str | Path | None = None` to the signature and branch before the `playback` check:

```python
    if record_out is not None:
        cmd = _record_command(port, repo, record_out)
    elif backend == "playback":
        cmd = _playback_command(port, repo, repos)
    else:
        ...
```

- [ ] **Step 6: Run the CLI tests and both suites**

```bash
cd a2a-rig
uv run pytest tests/test_record_cli.py -v
uv run pytest --backend playback && uv run pytest --backend echo
```

Expected: CLI tests PASS; both suites green with 4 xfailed.

- [ ] **Step 7: Commit**

```bash
git add a2a-rig/src/a2a_playback/record.py a2a-rig/src/a2a_rig/server.py \
        a2a-rig/pyproject.toml a2a-rig/tests/test_record_cli.py
git commit -F scratch/commit-msg-record-cli.txt
```

---

### recording-round-trip

The keystone. A recording of `playback`, replayed, must produce what the original produced. Costs zero inference and is what pins the two inverse serializers together for good.

**Files:**
- Test: `a2a-rig/tests/test_record_round_trip.py`

**Interfaces:**
- Consumes: `serve(..., record_out=...)` from `record-cli`; the `repos/billing-api` fixture; `http_client` from `conftest.py`.
- Produces: nothing.

- [ ] **Step 1: Write the failing test**

Create `a2a-rig/tests/test_record_round_trip.py`:

```python
"""The property M3 rests on: a recording replays as what it recorded.

`to_scenario_event` and `PlaybackBackend._to_backend_event` are inverses living
in different files. Unit tests pin them pairwise; this pins them end to end,
through a real server, a real client, and a real file on disk — for free,
because the thing being recorded is the scripted backend.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from a2a.client import create_client
from a2a.client.client import ClientConfig

from a2a_playback.repo import load_repo
from a2a_playback.scenario import load_scenario
from a2a_rig.events import send
from a2a_rig.server import serve

REPO = Path(__file__).resolve().parents[1] / "repos" / "billing-api"


def _promote(recording: Path, into: Path) -> Path:
    """Stand the recording up as a repo of its own, the way a `mv` would."""
    (into / "scenarios").mkdir(parents=True)
    (into / "repo.yaml").write_text('card:\n  description: "replay"\n')
    (into / "scenarios" / "10-recorded.yaml").write_text(recording.read_text())
    load_repo(into)  # must load clean, or promotion was never going to work
    return into


async def _client(url, http_client):
    return await create_client(
        url, ClientConfig(streaming=True, httpx_client=http_client)
    )


async def test_a_recorded_playback_run_replays_as_itself(tmp_path, http_client):
    """Record billing-api answering a prompt, then serve the recording and ask
    the same question. The two streams must agree."""
    out = tmp_path / "recorded.yaml"
    prompt = "explain the tax module"

    with serve(repo=REPO, record_out=out) as url:
        original = await send(await _client(url, http_client), prompt)

    scenario = load_scenario(out)
    assert scenario.recorded["prompts"] == [prompt]
    assert len(scenario.plays) == 1

    with serve(repo=_promote(out, tmp_path / "replayed")) as url:
        replayed = await send(await _client(url, http_client), prompt)

    assert replayed.artifact_text() == original.artifact_text()
    assert replayed.final_state == original.final_state
    assert replayed.status_texts == original.status_texts


async def test_a_recorded_gate_replays_and_the_other_branch_is_loud(tmp_path, http_client):
    """Recording the allow path gives on_allow and nothing else.

    The gate spans two `execute` calls but one `drive()` — the session parks
    inside `request_permission` — so it is still exactly one recorded play.
    """
    out = tmp_path / "gated.yaml"
    prompt = "add a /health endpoint and run the tests"

    with serve(repo=REPO, record_out=out) as url:
        client = await _client(url, http_client)
        parked = await send(client, prompt)
        assert parked.final_state == "input_required"
        resumed = await send(
            client, "allow", task_id=parked.task_id, context_id=parked.context_id
        )
        assert resumed.final_state == "completed"

    plays = load_scenario(out).plays
    assert len(plays) == 1, "a gated turn is one drive, so one play"
    permission = next(e["permission"] for e in plays[0].events if "permission" in e)
    assert "on_allow" in permission
    assert "on_deny" not in permission, "a run that was allowed never saw a denial"


async def test_replaying_a_recorded_gate_and_denying_is_loud(tmp_path, http_client):
    """The other half: the unrecorded branch fails the task rather than
    completing it empty."""
    out = tmp_path / "gated.yaml"
    prompt = "add a /health endpoint and run the tests"

    with serve(repo=REPO, record_out=out) as url:
        client = await _client(url, http_client)
        parked = await send(client, prompt)
        await send(client, "allow", task_id=parked.task_id, context_id=parked.context_id)

    with serve(repo=_promote(out, tmp_path / "replayed")) as url:
        client = await _client(url, http_client)
        parked = await send(client, prompt)
        denied = await send(
            client, "deny", task_id=parked.task_id, context_id=parked.context_id
        )

    assert denied.final_state == "failed"
```

**Reuse note:** `send()` from `a2a_rig.events` is the suite's one way to read an A2A stream — it returns a `Capture` with `.artifact_text()`, `.final_state`, `.states`, `.status_texts`, `.permission`, `.task_id`, and `.context_id`. `tests/test_permission.py` already uses the `send` → `send(..., task_id=, context_id=)` pattern for answering a parked task. Do not add a second way to read a stream.

- [ ] **Step 2: Run to verify it fails**

```bash
cd a2a-rig && uv run pytest tests/test_record_round_trip.py -v
```

Expected: FAIL — `serve()` does not accept `record_out` until `record-cli` is done, or a genuine stream mismatch if it is.

- [ ] **Step 3: Run until green**

```bash
cd a2a-rig && uv run pytest tests/test_record_round_trip.py -v
```

Expected: PASS. **A mismatch here is a real finding, not a test bug** — it means the two serializers disagree. Fix the serializer, not the assertion, and note what disagreed for the DEVLOG.

- [ ] **Step 4: Run both suites**

```bash
cd a2a-rig && uv run pytest --backend playback && uv run pytest --backend echo
```

- [ ] **Step 5: Commit**

```bash
git add a2a-rig/tests/test_record_round_trip.py
git commit -F scratch/commit-msg-round-trip.txt
```

This task adds a test file and nothing else. If you found yourself changing `conftest.py` or any source file to make it pass, that is a finding worth reporting, not a fix to slip in — the round trip is supposed to work on the code as built.

---

### scenario-file-prefixes

Make promotion a `mv`. Every shipped repo's scenario file ends with a `match: {}` catch-all, so any second file sorting after it makes the recorded plays unreachable and fails the repo at boot.

**Files:**
- Rename: `a2a-rig/repos/billing-api/scenarios/refactor.yaml` → `10-refactor.yaml`
- Rename: `a2a-rig/repos/checkout-web/scenarios/upgrade.yaml` → `10-upgrade.yaml`
- Rename: `a2a-rig/repos/infra-terraform/scenarios/plan-and-apply.yaml` → `10-plan-and-apply.yaml`
- Create: `99-default.yaml` in each of the three `scenarios/` directories
- Test: `a2a-rig/tests/test_repo.py`

**Interfaces:**
- Consumes: nothing.
- Produces: the convention `NN-<slug>.yaml`, catch-all alone in `99-default.yaml`.

- [ ] **Step 1: Write the failing test**

Add to `a2a-rig/tests/test_repo.py`:

```python
@pytest.mark.parametrize("repo_name", ["billing-api", "checkout-web", "infra-terraform"])
def test_a_shipped_repo_accepts_a_new_scenario_file_without_reordering(repo_name):
    """Promotion must be a `mv`. A catch-all living in the same file as real
    plays means any file sorting after it shadows everything it contains."""
    from pathlib import Path

    home = Path(__file__).parents[1] / "repos" / repo_name
    scenarios = sorted(p.name for p in (home / "scenarios").glob("*.yaml"))
    assert scenarios[-1] == "99-default.yaml", (
        f"the catch-all must sort last; got {scenarios}"
    )
    for name in scenarios[:-1]:
        assert not name.startswith("99-"), f"{name} would compete with the catch-all"
    load_repo(home)
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd a2a-rig && uv run pytest tests/test_repo.py::test_a_shipped_repo_accepts_a_new_scenario_file_without_reordering -v
```

Expected: FAIL for all three repos — `scenarios[-1]` is the single hand-written file, not `99-default.yaml`. Locate the shipped repos with `Path(__file__).parents[1] / "repos"`, the same idiom `test_shipped_repos_load` already uses; do not import a path constant from `a2a_rig.server`.

- [ ] **Step 3: Rename and split, one repo at a time**

```bash
cd a2a-rig/repos
git mv billing-api/scenarios/refactor.yaml billing-api/scenarios/10-refactor.yaml
git mv checkout-web/scenarios/upgrade.yaml checkout-web/scenarios/10-upgrade.yaml
git mv infra-terraform/scenarios/plan-and-apply.yaml infra-terraform/scenarios/10-plan-and-apply.yaml
```

Then, for each of the three, cut the trailing `- match: {}` play out of the `10-*.yaml` file and put it in a sibling `99-default.yaml`. For `billing-api`, the new file is:

```yaml
# The catch-all lives alone so a new scenario file can be dropped in without
# reordering anything. Files load in filename order and their plays
# concatenate, so a catch-all sharing a file with real plays shadows every
# file that sorts after it.
plays:
  - match: {}
    events:
      - text: "Ready when you are"
      - result: { num_turns: 1, stop_reason: end_turn }
```

Copy the *existing* catch-all events verbatim out of each repo's file rather than retyping them — `conftest.py`'s `reply_marker` fixture expects billing-api's to contain `Ready when you are`, and the other two have their own text.

- [ ] **Step 4: Prove promotion is now a `mv`**

Add the test that states the actual goal, rather than only its precondition:

```python
def test_a_new_scenario_file_drops_in_without_shadowing(tmp_path):
    """The point of the prefixes: a recorded file lands between the
    hand-written plays and the catch-all, and everything stays reachable."""
    import shutil
    from pathlib import Path

    home = tmp_path / "billing-api"
    shutil.copytree(Path(__file__).parents[1] / "repos" / "billing-api", home)
    (home / "scenarios" / "20-recorded.yaml").write_text(
        'plays:\n'
        '  - match: { regex: "^a recorded prompt$" }\n'
        '    events:\n'
        '      - text: "from a recording"\n'
        '      - result: { num_turns: 1 }\n'
    )

    repo = load_repo(home)  # must not raise: the catch-all is still last
    play = repo.select("a recorded prompt", turn=1)
    assert play.events[0] == {"text": "from a recording"}, "the catch-all shadowed it"
```

- [ ] **Step 5: Run everything**

```bash
cd a2a-rig && uv run pytest --backend playback && uv run pytest --backend echo
```

Expected: both green. If `reply_marker` or `denied_marker` assertions break, the catch-all text was changed rather than moved — restore it verbatim.

- [ ] **Step 6: Commit**

```bash
git add -A a2a-rig/repos a2a-rig/tests/test_repo.py
git commit -F scratch/commit-msg-scenario-prefixes.txt
```

---

### the-recording-run

The one task that costs money and cannot be done by an agent unattended. **Stop and hand this to Josh.**

**Files:**
- Create: `a2a-rig/repos/billing-api/scenarios/20-<slug>.yaml`
- Create: staging file outside the repo (e.g. `scratch/recording-health.yaml`)

**Interfaces:**
- Consumes: `rig-record` from `record-cli`; the prefix convention from `scenario-file-prefixes`.
- Produces: at least one recorded scenario file checked in.

- [ ] **Step 1: Confirm the scratch repo is clean**

```bash
git -C ~/scratch/demo-app status --short && git -C ~/scratch/demo-app log --oneline -1
```

Expected: clean, at `6890fd7`. If not, stop and ask — a dirty repo makes the recording's diffs unreproducible.

- [ ] **Step 2: Start the recorder**

```bash
cd a2a-rig
uv run rig-record --backend acp --agent claude \
  --cwd ~/scratch/demo-app \
  --out ../scratch/recording-health.yaml \
  --port 9300
```

`--backend acp` is not optional: `--backend claude` cannot emit `plan` events at all (`docs/UPSTREAM.md`, taskwarrior `70dc7c04`).

**There is no enforceable cost ceiling on this path** — `ACPBackend` takes no `max_budget_usd`. Watch `cost_usd` in each `result` and stop. Phase 2's comparable run was ~$0.54.

- [ ] **Step 3: Drive three prompts with a real client**

In a second terminal, using `a2a-cli` (npm-linked, on PATH):

1. A plain question that produces text and a plan, no gate.
2. A change that edits a file, so a `file_change` with a real diff gets recorded.
3. A change that wants to run the tests, so a **real permission gate** parks the task — **approve it**, so a recorded `on_allow` exists.

- [ ] **Step 4: Stop the recorder and read the file**

`ctrl-C` the server. Then read `scratch/recording-health.yaml` end to end. The recorder handled the cwd prefix and `session_id`; you are looking for what it could not:

- any remaining absolute path or username
- anything from the environment that should not be public
- a `match:` regex worth loosening to a `contains:` slug
- plays not worth keeping (delete them; `recorded.prompts` stays as the re-record source)

- [ ] **Step 5: Promote it**

```bash
mv scratch/recording-health.yaml a2a-rig/repos/billing-api/scenarios/20-recorded-health.yaml
cd a2a-rig && uv run pytest --backend playback && uv run pytest --backend echo
```

Expected: both green. A boot failure here means a match collision with `10-refactor.yaml` — adjust the recorded regex, do not touch the hand-written file.

- [ ] **Step 6: Commit**

```bash
git add a2a-rig/repos/billing-api/scenarios/20-recorded-health.yaml
git commit -F scratch/commit-msg-first-recording.txt
```

---

### docs-corrections

Graduate M3's decisions into the plan of record, and correct the two documents this design contradicts.

**Files:**
- Modify: `docs/DESIGN-v3.md` (§4, §6)
- Modify: `docs/PLAN.md` (Phase 7)
- Modify: `docs/UPSTREAM.md`
- Modify: `docs/DEVLOG.md`
- Modify: `a2a-rig/repos/billing-api/repo.yaml`
- Modify: `a2a-rig/README.md`

**Interfaces:**
- Consumes: everything above.
- Produces: nothing.

- [ ] **Step 1: Correct DESIGN-v3 §6**

Remove "(plus timing)" from the `RecordingBackend` sentence. Add, as a bullet in the same section:

> - **A recording captures only the branch that was taken.** A real run answers a permission
>   gate once, so a recorded `permission` carries `on_allow` or `on_deny`, never both, and the
>   other branch is a loud failure on replay rather than an invented one. This is why recorded
>   and hand-written scenarios compose rather than one replacing the other: the deny path, the
>   abandoned-approval timeout, and scripted failures are exactly what a live run cannot be made
>   to produce on demand.
> - **No per-event timing.** The refresh loop works by diffing normalized streams across
>   re-recordings, and wall-clock timing differs every run — recording it would make every
>   re-record diff every line. Pacing stays where it already lives: `repo.yaml`'s
>   `defaults.delay_ms` and `PLAYBACK_SPEED`.

- [ ] **Step 2: Document the recorded-file shape in DESIGN-v3 §4**

Add after the existing scenario-format block: the `recorded:` mapping carries `at`, `backend`, and `prompts`; `prompts` is machine-readable because the refresh loop re-records from it, and it is a source list rather than an index into `plays`. Recorded plays match on an anchored, `re.escape`d regex so two recordings in one repo cannot shadow each other. Scenario files use an `NN-<slug>.yaml` naming convention with the catch-all alone in `99-default.yaml`.

- [ ] **Step 3: Correct PLAN.md Phase 7**

Check off the first two bullets. Rewrite the second to say recorded scenarios **compose with** hand-written ones, with an inline note in the repo's established style:

> Recordings own the happy paths; the hand-written scenarios stay for the deny branch, the
> abandoned-approval timeout, and scripted failures, which a live run cannot be made to produce
> on demand. "Replacing hand-written ones" would have traded real coverage for provenance.

Add an inline note to the third bullet that the refresh loop is documented as mechanics only, and the first real upstream bump writes the rest from evidence. Note that the acp path has no budget ceiling.

- [ ] **Step 4: Add the UPSTREAM.md candidate**

New entry: `ACPBackend` accepts no `max_budget_usd` while `ClaudeBackend` does, though the ACP connection tracks `cost_usd` internally — so a ceiling is implementable, it just isn't exposed. Consequence: the only backend that can record `plan` events is the one with no cost ceiling. Possibly deliberate; a nit, not a blocker. Slot it into the filing order alongside `f010f63e`.

- [ ] **Step 5: Write the DEVLOG entry**

New dated section covering: the seam and why it is `BackendSession` rather than the `Backend` protocol; the two design contradictions found in DESIGN-v3 §6 and PLAN.md Phase 7 and how they were resolved; the silent no-result branch found in `_answered`; the catch-all ordering trap and why `recorded-*.yaml` sorting before `refactor.yaml` was luck; whatever the round-trip test turned up; and what the live recording actually cost.

- [ ] **Step 6: Fix the repo.yaml comment and the README**

`repos/billing-api/repo.yaml` currently says *"M3 replaces hand-written repos like this one with recordings."* Correct it to the composition model. Update `a2a-rig/README.md` with a `rig-record` section (how to record, that `--out` must be a staging path, why `--backend acp`, that there is no cost ceiling on that path) and refresh any test counts.

- [ ] **Step 7: Verify counts are real before writing them down**

```bash
cd a2a-rig && uv run pytest --backend playback && uv run pytest --backend echo
```

Copy the actual numbers into the README and DEVLOG. Do not write counts from memory.

- [ ] **Step 8: Commit**

```bash
git add docs/ a2a-rig/README.md a2a-rig/repos/billing-api/repo.yaml
git commit -F scratch/commit-msg-m3-docs.txt
```

---

## Task Dependency Order

```
permission-branch-fail-loud
        ↓
scenario-event-serializer ──→ scrub-module
        ↓                          ↓
        └──────→ recording-backend ←┘
                        ↓
                   record-cli
                        ↓
              recording-round-trip
                        ↓
              scenario-file-prefixes
                        ↓
               the-recording-run   ← STOP: hand to Josh, costs money
                        ↓
                docs-corrections
```

`scenario-event-serializer` and `scrub-module` are independent of each other and can be done in either order or in parallel.
