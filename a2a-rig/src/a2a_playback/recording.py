"""Recording: turn a live ``BackendEvent`` back into a scenario mapping.

The inverse of ``PlaybackBackend._to_backend_event`` in ``backend.py``. That
function reads a scenario's single-key event mapping and produces a
``BackendEvent``; ``to_scenario_event`` here does the reverse, so a real
agent run can be captured and later replayed byte-for-byte through
``playback``. The two live in different files and will rot apart over time —
``tests/test_recording.py``'s round-trip tests are what holds them together.

Both halves live here now: the serializer (``to_scenario_event``) and the tee
that wraps a real backend and calls it during a live run
(``RecordingBackend``).

Upstreaming this module to a2acode would go alongside the playback backend,
not on its own: it imports ``parse_scenario``/``ScenarioError`` from
``.scenario`` and ``scrub_cwd`` from ``.scrub``, and what it writes is the
playback format.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any

import yaml
from a2acode.backends.base import (
    FileChange, Notice, Plan, Result, TextDelta, Thought, ToolResult, ToolUse,
)

from .scenario import ScenarioError, parse_scenario
from .scrub import scrub_cwd


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
        except BaseException as exc:
            # A real failure recorded is exactly the coverage recording
            # otherwise cannot manufacture. Keep it, then let a2acode's real
            # failure path run untouched. Appended through the proxy's own
            # cursor, not the root `events` list: if the failure came after a
            # gate was answered, the cursor has already moved into the branch
            # taken, and an error left at the root would be as unreachable on
            # replay as any other post-gate event left there.
            #
            # `BaseException`, not `Exception`: `BackendSession.start`
            # re-raises `asyncio.CancelledError` before its `BaseException`
            # relay, so uvicorn shutdown, session eviction, or a client
            # disconnect must still land the in-flight turn's recording.
            # `str(exc) or type(exc).__name__`: `str(RuntimeError())`,
            # `str(asyncio.TimeoutError())`, and `str(CancelledError())` are
            # all `""`, and scenario.py rejects an `error` event with an
            # empty message — an empty-message failure would write a
            # recording that cannot load.
            proxy._current.append({"error": str(exc) or type(exc).__name__})
            self._finish(request.prompt, events)
            raise
        self._finish(request.prompt, events)

    def _finish(self, prompt: str, events: list[dict]) -> None:
        # Scrubbed once, then reused for both the match and the provenance
        # list: the prompt gets the same redaction its events already get, so
        # neither leaks the recording machine's absolute paths, and the
        # anchored regex still matches when replayed somewhere else.
        prompt = scrub_cwd(prompt, self._cwd)
        if prompt in self._prompts:
            # Not fatal — a repeated prompt is legitimate to record, it is
            # only the second copy's regex that is dead weight. Anchored
            # regexes are identical for identical prompts, so first match
            # wins means the second play this turn writes can never be
            # reached on replay; the operator should know that happened.
            print(
                f"warning: {self._out} already has a recorded play for the "
                f"prompt {prompt!r}; the new one is unreachable on replay "
                f"(first match wins)",
                file=sys.stderr,
            )
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
        """Rewrite the whole file. Every turn, not at shutdown.

        Writes first, unconditionally: a paid run already happened, so it
        must land on disk even if what came out of it cannot replay (a gate
        with nothing after it, say — `scenario.py` refuses those). Only then
        does it round-trip the document through the same parser a replay
        would use, and warn loudly on stderr if that fails. Never raises here
        — a live recording session should not die over a file that is
        already safely written — and never edits the document to make it
        pass: dropping the offending permission would be inventing a run
        that never occurred.

        The write itself is atomic: a sibling `.tmp` path, then `os.replace`
        onto the target. `Path.write_text` is truncate-then-write, so a
        SIGINT or a full disk between those two steps would lose every prior
        turn already on disk — exactly the outcome writing every turn exists
        to prevent.
        """
        self._out.parent.mkdir(parents=True, exist_ok=True)
        document = self.document()
        tmp = self._out.with_name(self._out.name + ".tmp")
        tmp.write_text(
            yaml.safe_dump(document, sort_keys=False, allow_unicode=True)
        )
        os.replace(tmp, self._out)
        try:
            parse_scenario(document, path=self._out)
        except ScenarioError as exc:
            print(
                f"warning: {self._out} was written but will not load as a "
                f"scenario ({exc}); it needs a hand-edit before it can be "
                f"replayed",
                file=sys.stderr,
            )

    async def aclose(self) -> None:
        """Forward to the inner backend if it pools anything (ACPBackend does)."""
        closer = getattr(self._inner, "aclose", None)
        if closer is not None:
            await closer()
