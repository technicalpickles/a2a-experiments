"""Both directions of the AG-UI <-> A2A seam, and nothing else.

Outbound: RunTranslator turns one A2A turn's stream events into AG-UI events.
Inbound (added in the translate-inbound task): incoming_turn decides whether a
RunAgentInput is a fresh user message or a permission decision resuming a
pending task.

Stateful across one run on purpose: artifact chunks stream as one assistant
message (START once, a CONTENT delta per chunk, END at close), and terminal
A2A states surface only at finish() so the caller controls run lifecycle.
The endpoint emits RUN_STARTED itself, before upstream is contacted, so
pre-upstream failures can still land RUN_ERROR inside a well-formed run.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from a2a.types import StreamResponse, TaskState
from ag_ui.core import (
    BaseEvent,
    CustomEvent,
    RunAgentInput,
    RunErrorEvent,
    RunFinishedEvent,
    StepFinishedEvent,
    StepStartedEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
    ToolMessage,
    UserMessage,
)
from google.protobuf.json_format import MessageToDict

PERMISSION_KEY = "a2acode_permission"
PERMISSION_TOOL = "request_permission"

_TERMINAL = {
    TaskState.TASK_STATE_COMPLETED: "completed",
    TaskState.TASK_STATE_FAILED: "failed",
    TaskState.TASK_STATE_CANCELED: "canceled",
    TaskState.TASK_STATE_REJECTED: "rejected",
}


def _parts_text(parts) -> str:
    return "".join(p.text for p in parts if p.text)


class RunTranslator:
    """One A2A turn in, one AG-UI run out. Feed every stream event, then finish."""

    def __init__(self, thread_id: str, run_id: str):
        self.thread_id = thread_id
        self.run_id = run_id
        self.task_id = ""
        self.pending: dict[str, Any] | None = None
        self._message_id = ""
        self._final_state = ""
        self._final_text = ""

    def feed(self, event: StreamResponse) -> list[BaseEvent]:
        which = event.WhichOneof("payload")
        if which == "task":
            self.task_id = event.task.id
            return []
        if which == "status_update":
            return self._status(event.status_update)
        if which == "artifact_update":
            text = _parts_text(event.artifact_update.artifact.parts)
            return self._text_delta(text) if text else []
        return [CustomEvent(name="a2a", value={"payload": which})]

    def abort(self) -> list[BaseEvent]:
        """Close any open text frame so a caller can end the run mid-stream."""
        return self._close_text()

    def finish(self) -> list[BaseEvent]:
        events = self._close_text()
        if self._final_state == "failed":
            events.append(RunErrorEvent(message=self._final_text or "task failed"))
            return events
        if self._final_state == "canceled":
            events.append(CustomEvent(name="canceled", value={"text": self._final_text}))
        events.append(
            RunFinishedEvent(thread_id=self.thread_id, run_id=self.run_id)
        )
        return events

    def _status(self, update) -> list[BaseEvent]:
        status = update.status
        text = ""
        metadata: dict[str, Any] = {}
        if status.HasField("message"):
            text = _parts_text(status.message.parts)
            metadata = MessageToDict(
                status.message.metadata, preserving_proto_field_name=True
            )
        if (
            status.state == TaskState.TASK_STATE_INPUT_REQUIRED
            and PERMISSION_KEY in metadata
        ):
            self.pending = metadata[PERMISSION_KEY]
            self.task_id = update.task_id or self.task_id
            self._final_state = "input_required"
            call_id = self.pending.get("request_id") or uuid.uuid4().hex
            return self._close_text() + [
                ToolCallStartEvent(tool_call_id=call_id, tool_call_name=PERMISSION_TOOL),
                ToolCallArgsEvent(tool_call_id=call_id, delta=json.dumps(self.pending)),
                ToolCallEndEvent(tool_call_id=call_id),
            ]
        if status.state == TaskState.TASK_STATE_WORKING:
            if not text:
                return []
            return [StepStartedEvent(step_name=text), StepFinishedEvent(step_name=text)]
        if status.state in _TERMINAL:
            self._final_state = _TERMINAL[status.state]
            self._final_text = text
            return []
        return [
            CustomEvent(
                name="a2a_status",
                value={"state": int(status.state), "text": text},
            )
        ]

    def _text_delta(self, text: str) -> list[BaseEvent]:
        events: list[BaseEvent] = []
        if not self._message_id:
            self._message_id = uuid.uuid4().hex
            events.append(TextMessageStartEvent(message_id=self._message_id))
        events.append(TextMessageContentEvent(message_id=self._message_id, delta=text))
        return events

    def _close_text(self) -> list[BaseEvent]:
        if not self._message_id:
            return []
        message_id, self._message_id = self._message_id, ""
        return [TextMessageEndEvent(message_id=message_id)]


@dataclass
class Turn:
    """What a RunAgentInput asks of the upstream: say this, or answer that."""

    kind: Literal["message", "resume"]
    text: str


def incoming_turn(run_input: RunAgentInput) -> Turn:
    if not run_input.messages:
        raise ValueError("run carried no messages")
    last = run_input.messages[-1]
    if isinstance(last, ToolMessage):
        return Turn(kind="resume", text=_decision(last.content))
    if isinstance(last, UserMessage) and isinstance(last.content, str) and last.content:
        return Turn(kind="message", text=last.content)
    raise ValueError(f"cannot act on a trailing {type(last).__name__}")


def _decision(content: str | None) -> str:
    parsed: Any = content
    try:
        parsed = json.loads(content or "")
    except json.JSONDecodeError:
        pass
    if isinstance(parsed, dict):
        parsed = parsed.get("decision")
    if parsed not in ("allow", "deny"):
        raise ValueError(f"tool result carried no decision: {content!r}")
    return parsed
