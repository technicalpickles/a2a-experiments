"""The seam, outbound: A2A stream events -> AG-UI events, no server involved."""

from __future__ import annotations

import json

from a2a.types import (
    Artifact,
    Message,
    Part,
    StreamResponse,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)

from a2a_orchestrator.translate import PERMISSION_TOOL, RunTranslator


def task_event(task_id="t1", context_id="c1"):
    return StreamResponse(task=Task(id=task_id, context_id=context_id))


def status_event(state, text="", metadata=None, task_id="t1"):
    message = Message(parts=[Part(text=text)]) if text or metadata else None
    if metadata is not None:
        message.metadata.update(metadata)
    status = TaskStatus(state=state)
    if message is not None:
        status.message.CopyFrom(message)
    return StreamResponse(
        status_update=TaskStatusUpdateEvent(task_id=task_id, status=status)
    )


def artifact_event(text, task_id="t1"):
    return StreamResponse(
        artifact_update=TaskArtifactUpdateEvent(
            task_id=task_id, artifact=Artifact(name="response", parts=[Part(text=text)])
        )
    )


def drain(translator, events):
    out = []
    for event in events:
        out.extend(translator.feed(event))
    out.extend(translator.finish())
    return out


def types_of(events):
    return [e.type.value for e in events]


def test_completed_turn_streams_text_and_finishes():
    translator = RunTranslator("th1", "r1")
    out = drain(
        translator,
        [
            task_event(),
            artifact_event("Ready "),
            artifact_event("when you are"),
            status_event(TaskState.TASK_STATE_COMPLETED),
        ],
    )
    assert types_of(out) == [
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
        "RUN_FINISHED",
    ]
    deltas = [e.delta for e in out if e.type.value == "TEXT_MESSAGE_CONTENT"]
    assert "".join(deltas) == "Ready when you are"
    starts = [e for e in out if e.type.value == "TEXT_MESSAGE_START"]
    ends = [e for e in out if e.type.value == "TEXT_MESSAGE_END"]
    assert starts[0].message_id == ends[0].message_id
    assert translator.task_id == "t1"
    assert translator.parked is None


def test_working_narration_becomes_step_pairs():
    translator = RunTranslator("th1", "r1")
    out = drain(
        translator,
        [
            task_event(),
            status_event(TaskState.TASK_STATE_WORKING, "Using tool: Read"),
            status_event(TaskState.TASK_STATE_COMPLETED),
        ],
    )
    assert types_of(out) == ["STEP_STARTED", "STEP_FINISHED", "RUN_FINISHED"]
    assert out[0].step_name == "Using tool: Read"


def test_permission_parks_as_a_tool_call():
    permission = {"tool": "Bash", "request_id": "req-1", "input": {"command": "pytest"}}
    translator = RunTranslator("th1", "r1")
    out = drain(
        translator,
        [
            task_event(),
            artifact_event("Let me run the tests."),
            status_event(
                TaskState.TASK_STATE_INPUT_REQUIRED,
                text="Bash",
                metadata={"a2acode_permission": permission},
            ),
        ],
    )
    assert types_of(out) == [
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
        "TOOL_CALL_START",
        "TOOL_CALL_ARGS",
        "TOOL_CALL_END",
        "RUN_FINISHED",
    ]
    start = next(e for e in out if e.type.value == "TOOL_CALL_START")
    args = next(e for e in out if e.type.value == "TOOL_CALL_ARGS")
    assert start.tool_call_name == PERMISSION_TOOL
    assert start.tool_call_id == "req-1"
    assert json.loads(args.delta) == permission
    assert translator.parked == permission
    assert translator.task_id == "t1"


def test_failed_turn_becomes_run_error():
    translator = RunTranslator("th1", "r1")
    out = drain(
        translator,
        [
            task_event(),
            status_event(TaskState.TASK_STATE_FAILED, "terraform provider exploded"),
        ],
    )
    assert types_of(out) == ["RUN_ERROR"]
    assert "terraform provider exploded" in out[0].message


def test_canceled_turn_finishes_with_a_note():
    translator = RunTranslator("th1", "r1")
    out = drain(
        translator,
        [task_event(), status_event(TaskState.TASK_STATE_CANCELED)],
    )
    assert types_of(out) == ["CUSTOM", "RUN_FINISHED"]
    assert out[0].name == "canceled"


def test_unknown_payloads_pass_through_as_custom():
    translator = RunTranslator("th1", "r1")
    out = drain(translator, [StreamResponse()])
    assert types_of(out) == ["CUSTOM", "RUN_FINISHED"]
