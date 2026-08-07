"""The `playback` backend: echo, but scenario-driven.

Implements a2acode's ``Backend`` protocol, so everything above it — the server,
the protocol mapping, the task-state machine, the input-required round trip —
is a2acode's real code. Only the brain is scripted. That is the whole point:
the fake cannot drift from the real producer, because it *is* the real producer
minus the model.

Deliberately written to drop into a2acode's own ``backends/`` directory later
(DESIGN-v3 §7, milestone M4) — it imports only from a2acode's public backend
vocabulary and holds no rig-specific assumptions.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from a2acode.backends.base import (
    FileChange,
    Notice,
    Plan,
    PlanStep,
    Result,
    RunRequest,
    TextDelta,
    Thought,
    ToolResult,
    ToolUse,
)
from a2acode.backends.session import BackendSession

from .scenario import Scenario, ScenarioError, message_of


class ScriptedError(RuntimeError):
    """A failure the scenario asked for.

    Distinct from ``ScenarioError`` on purpose: that one means the scenario is
    wrong, this one means the scenario is right and the run it describes is a
    failing one. Raising rather than emitting is what makes it a real failure —
    a2acode's session relays the exception to the executor, which fails the
    task through the same path a crashed backend takes.
    """


# Scales every delay_ms. 0 means "no delays at all" — the CI default; 1.0 is
# lifelike. Read per-run rather than cached so a demo can change pacing without
# a restart.
SPEED_ENV = "PLAYBACK_SPEED"


def _speed() -> float:
    raw = os.environ.get(SPEED_ENV)
    if raw is None:
        return 0.0
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 0.0


class PlaybackBackend:
    """Emits scripted events from a scenario file."""

    name = "playback"

    def __init__(self, scenario: Scenario) -> None:
        self.scenario = scenario
        # contextId -> how many turns that conversation has seen. The `turn: N`
        # match rule counts within a context, so a fresh conversation replays
        # the scenario from the top.
        self._turns: dict[str, int] = {}

    def _next_turn(self, context_id: str | None) -> int:
        key = context_id or ""
        self._turns[key] = self._turns.get(key, 0) + 1
        return self._turns[key]

    async def drive(self, session: BackendSession, request: RunRequest) -> None:
        turn = self._next_turn(request.context_id)
        play = self.scenario.select(request.prompt, turn)
        await self._run_events(session, play.events, request)

    async def _run_events(
        self,
        session: BackendSession,
        events: list[dict[str, Any]],
        request: RunRequest,
    ) -> None:
        for event in events:
            (kind, body), = event.items()
            # Before the dispatch below, so a gate can be paced too: an
            # approval that arrives the instant you ask reads as a UI bug.
            await self._delay(body)
            if kind == "permission":
                await self._permission(session, body, request)
                # The branch we took carries its own terminal `result`; nothing
                # after a permission event in the same list would be reachable
                # in a real run either.
                return
            if kind == "error":
                raise ScriptedError(message_of(body))
            await session.emit(self._to_backend_event(kind, body, request))

    async def _permission(
        self, session: BackendSession, body: dict[str, Any], request: RunRequest
    ) -> None:
        asking = session.request_permission(
            body["tool"],
            body.get("input") or {},
            body.get("description") or "",
        )
        timeout_ms = body.get("timeout_ms")
        if timeout_ms is None:
            # No timeout is the default on purpose: a gate that quietly expired
            # would turn a slow reviewer into a denial nobody scripted.
            branch = await self._answered(asking, body)
        else:
            try:
                branch = await self._answered(
                    asyncio.wait_for(asking, float(timeout_ms) / 1000.0), body
                )
            except TimeoutError:
                # Nobody answered. The request stays pending in the session, so
                # a caller who does eventually reply resumes into this branch
                # rather than the one they asked for — which is the honest
                # rendering of having walked away.
                branch = body.get("on_timeout") or body.get("on_deny") or []
        await self._run_events(session, branch, request)

    @staticmethod
    async def _answered(asking, body: dict[str, Any]) -> list[dict[str, Any]]:
        decision = await asking
        return body.get("on_allow" if decision.allow else "on_deny") or []

    async def _delay(self, body: Any) -> None:
        speed = _speed()
        if not speed:
            return
        delay_ms = self.scenario.default_delay_ms
        if isinstance(body, dict) and body.get("delay_ms") is not None:
            delay_ms = float(body["delay_ms"])
        if delay_ms > 0:
            # Plain sleep: cancelling the task cancels the driver, so a cancel
            # mid-delay is honoured without extra machinery.
            await asyncio.sleep(delay_ms / 1000.0 * speed)

    def _to_backend_event(self, kind: str, body: Any, request: RunRequest):
        match kind:
            case "text":
                return TextDelta(text=_text_of(body))
            case "thought":
                return Thought(text=_text_of(body))
            case "notice":
                return Notice(text=_text_of(body))
            case "tool_use":
                return ToolUse(
                    name=body["name"],
                    tool_input=body.get("input") or {},
                    tool_use_id=str(body.get("id") or body["name"]),
                )
            case "tool_result":
                return ToolResult(
                    tool_use_id=str(body.get("id") or ""),
                    name=body.get("name") or "",
                    failed=bool(body.get("failed", False)),
                    output=body.get("output") or "",
                )
            case "file_change":
                return FileChange(path=body["path"], diff=body.get("diff") or "")
            case "plan":
                return Plan(
                    steps=[
                        PlanStep(
                            content=step["content"],
                            status=step.get("status", "pending"),
                            priority=step.get("priority", ""),
                        )
                        for step in (body.get("steps") or [])
                    ],
                    markdown=body.get("markdown") or "",
                    uri=body.get("uri") or "",
                )
            case "result":
                return Result(
                    # Falling back to the context id keeps the executor's
                    # contextId -> session-id mapping behaving like a real
                    # backend's without every scenario having to say so.
                    session_id=body.get("session_id") or request.context_id,
                    cost_usd=body.get("cost_usd"),
                    num_turns=body.get("num_turns"),
                    usage=body.get("usage"),
                    stop_reason=body.get("stop_reason"),
                )
        raise ScenarioError(f"unhandled event kind {kind!r}")


def _text_of(body: Any) -> str:
    """`- text: "hi"` and `- text: {content: "hi"}` should both work."""
    if isinstance(body, str):
        return body
    if isinstance(body, dict):
        return body.get("text") or body.get("content") or ""
    return str(body)
