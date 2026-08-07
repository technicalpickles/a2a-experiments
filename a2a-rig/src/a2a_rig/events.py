"""Turn a streamed A2A response into something assertable.

The SDK hands back protobuf oneof events; tests want "what states did this go
through", "what did the artifacts say", "what tool is it asking permission for".
This module is that translation layer, and nothing else — it holds no opinion
about which backend produced the stream, which is what lets the same test body
run against echo, playback, and claude.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from a2a.types import Message, Part, Role, SendMessageRequest, TaskState

# a2acode stashes the pending tool-permission payload here on the
# input-required status message. Confirmed against a real claude run — see
# a2a-experiments docs/captures/phase2-claude-run.jsonl.
PERMISSION_KEY = "a2acode_permission"

TERMINAL_STATES = {
    TaskState.TASK_STATE_COMPLETED,
    TaskState.TASK_STATE_FAILED,
    TaskState.TASK_STATE_CANCELED,
    TaskState.TASK_STATE_REJECTED,
}


def state_name(state) -> str:
    """``TASK_STATE_INPUT_REQUIRED`` -> ``input_required``."""
    return TaskState.Name(state).removeprefix("TASK_STATE_").lower()


def user_message(text: str, *, task_id: str = "", context_id: str = "") -> Message:
    message = Message(
        message_id=uuid4().hex, role=Role.ROLE_USER, parts=[Part(text=text)]
    )
    if context_id:
        message.context_id = context_id
    if task_id:
        message.task_id = task_id
    return message


def parts_text(parts) -> str:
    return "".join(p.text for p in parts if p.text)


@dataclass
class Capture:
    """Everything one ``send_message`` stream produced."""

    events: list[Any] = field(default_factory=list)
    task_id: str = ""
    context_id: str = ""

    @property
    def kinds(self) -> list[str]:
        """Event oneof names in arrival order, e.g. ``['task', 'status_update', ...]``."""
        return [e.WhichOneof("payload") for e in self.events]

    @property
    def states(self) -> list[str]:
        return [
            state_name(e.status_update.status.state)
            for e in self.events
            if e.WhichOneof("payload") == "status_update"
        ]

    @property
    def final_state(self) -> str | None:
        return self.states[-1] if self.states else None

    @property
    def artifacts(self) -> list[Any]:
        return [
            e.artifact_update.artifact
            for e in self.events
            if e.WhichOneof("payload") == "artifact_update"
        ]

    def artifact_text(self, name: str | None = None) -> str:
        """Concatenated text of all artifact parts, optionally filtered by name.

        Artifacts arrive chunked, so joining is the point — a single logical
        artifact can span several ``artifact_update`` events.
        """
        return "".join(
            parts_text(a.parts) for a in self.artifacts if name is None or a.name == name
        )

    @property
    def status_texts(self) -> list[str]:
        out = []
        for e in self.events:
            if e.WhichOneof("payload") != "status_update":
                continue
            status = e.status_update.status
            if status.HasField("message"):
                text = parts_text(status.message.parts)
                if text:
                    out.append(text)
        return out

    @property
    def permission(self) -> dict[str, Any] | None:
        """The pending permission request, if the stream parked on one."""
        for e in reversed(self.events):
            if e.WhichOneof("payload") != "status_update":
                continue
            status = e.status_update.status
            if status.state != TaskState.TASK_STATE_INPUT_REQUIRED:
                continue
            if not status.HasField("message"):
                continue
            metadata = _struct_to_dict(status.message.metadata)
            if PERMISSION_KEY in metadata:
                return metadata[PERMISSION_KEY]
        return None

    @property
    def completion_metadata(self) -> dict[str, Any]:
        """Metadata on the terminal status message (cost, usage, session id)."""
        for e in reversed(self.events):
            if e.WhichOneof("payload") != "status_update":
                continue
            status = e.status_update.status
            if status.state in TERMINAL_STATES and status.HasField("message"):
                return _struct_to_dict(status.message.metadata)
        return {}


def _struct_to_dict(metadata) -> dict[str, Any]:
    from google.protobuf.json_format import MessageToDict

    if metadata is None:
        return {}
    return MessageToDict(metadata, preserving_proto_field_name=True)


async def send(
    client,
    text: str,
    *,
    task_id: str = "",
    context_id: str = "",
    on_event=None,
) -> Capture:
    """Send one message and drain the whole event stream into a Capture.

    ``on_event(event, capture)`` is awaited after each event, for the tests
    that have to act *during* a turn rather than after it — cancelling a run
    in flight is the one that needs it.
    """
    message = user_message(text, task_id=task_id, context_id=context_id)
    capture = Capture(task_id=task_id, context_id=context_id)
    async for event in client.send_message(SendMessageRequest(message=message)):
        capture.events.append(event)
        which = event.WhichOneof("payload")
        if which == "task":
            capture.task_id = event.task.id
            capture.context_id = event.task.context_id
        elif which in ("status_update", "artifact_update"):
            update = getattr(event, which)
            capture.task_id = capture.task_id or update.task_id
            capture.context_id = capture.context_id or update.context_id
        if on_event is not None:
            await on_event(event, capture)
    return capture
