# AG-UI Native Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The browser stops speaking A2A and speaks AG-UI; the service becomes the A2A
client, translating at one tested seam; the cockpit's chat surface is CopilotKit React.

**Architecture:** Per `docs/superpowers/specs/2026-08-12-agui-native-design.md`. New
service plane: `agui.py` (POST `/agui/run`, threadId-routed, SSE out), `translate.py`
(both directions of the seam, heavily unit-tested), `a2a_client.py` (service-side A2A
conversations, parked-task registry). Frontend: CopilotKit provider-per-chat over
`@ag-ui/client`'s `HttpAgent`, direct connection (no Node runtime). Strangler ordering:
the old proxy plane keeps working until the new plane demos; deletions are the last
code task.

**Tech Stack:** Python 3.13 / Starlette / uvicorn / `a2a-sdk==1.1.2` (protobuf types) /
`ag-ui-protocol==0.1.19` (pinned; verified 2026-08-12 on PyPI). React 19 / Vite 8 /
`@ag-ui/client` 0.0.57 / `@copilotkit/react-core` + `@copilotkit/react-ui` 1.67.1
(npm `latest` as of 2026-08-12 — note "CopilotKit v2" is an API surface inside the 1.6x
line, not a 2.x semver).

## Global Constraints

- Work happens in the `agui-native` worktree (`~/worktrees/a2a-experiments/agui-native`),
  branch `agui-native`. Code commits go to that branch; this plan and other docs-only
  commits go straight to `main` per repo convention.
- Ports: rig 9200, orch-serve 9300, Vite dev 5173, live a2acode 9100.
- The rig substrate is the test bed: `playback` scenarios in `a2a-rig/repos/` answer
  the exact prompts used below ("hello from the cockpit" → completes with "Ready when
  you are"; "please run the tests" → parks on a `Bash` permission; deny → "Skipped the
  test run"; infra-terraform any prompt → fails). Mirror `tests/test_proxy.py`'s
  bodies — they are the behavioral spec, reassigned to the new plane.
- AG-UI wire facts (verified locally against `ag-ui-protocol==0.1.19`): Python
  constructors take snake_case (`thread_id`), the wire is camelCase (`threadId`);
  `EventEncoder().encode(event)` returns an SSE `data: {...}\n\n` string;
  `EventEncoder().get_content_type()` is the response media type. `RunAgentInput`
  fields: `thread_id, run_id, parent_run_id, state, messages, tools, context,
  forwarded_props, resume`.
- a2acode permission metadata rides `a2acode_permission` = `{tool, request_id, input}`
  on the `input_required` status message; resume is a new message with the parked
  `taskId`; the A2A stream ends on terminal states and `input_required` alike.
- All service tests run from `a2a-orchestrator/`: `uv run pytest -q`. Baseline before
  this plan: 34 passed. Frontend build check: `cd frontend && npm run build`.
- No em-dashes in outbound-as-Josh content (commit messages are fine).

---

### Task: agui-deps

**Files:**
- Modify: `a2a-orchestrator/pyproject.toml`
- Modify: `a2a-orchestrator/frontend/package.json` (via npm)
- Modify: `a2a-orchestrator/frontend/vite.config.ts`

**Interfaces:**
- Produces: importable `ag_ui.core` / `ag_ui.encoder` in the service venv;
  `@ag-ui/client`, `@copilotkit/react-core`, `@copilotkit/react-ui` in the frontend;
  `/agui` proxied to :9300 in dev.

- [ ] **Step 1: Move `a2a-sdk` to runtime deps, add `ag-ui-protocol`**

In `a2a-orchestrator/pyproject.toml`, `a2a-sdk==1.1.2` moves from
`[dependency-groups].dev` to `[project].dependencies` (the service itself is now the
A2A client), and `ag-ui-protocol==0.1.19` joins it:

```toml
dependencies = [
    "a2a-sdk==1.1.2",
    "ag-ui-protocol==0.1.19",
    "httpx>=0.28",
    "pyyaml>=6.0",
    "starlette>=0.47",
    "uvicorn>=0.30",
]
```

(dev group keeps `a2a-rig`, `pytest`, `pytest-asyncio`.) Run `uv sync --dev`.

- [ ] **Step 2: Verify imports**

Run: `uv run python -c "from ag_ui.core import RunAgentInput, RunStartedEvent; from ag_ui.encoder import EventEncoder; from a2a.types import StreamResponse; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Frontend deps**

```bash
cd frontend && npm install @ag-ui/client@0.0.57 @copilotkit/react-core@1.67.1 @copilotkit/react-ui@1.67.1
```

- [ ] **Step 4: Vite proxies `/agui`**

In `frontend/vite.config.ts`, add to `server.proxy`:

```ts
      '/agui': 'http://127.0.0.1:9300',
```

(`/a2a` stays until the delete-legacy task.)

- [ ] **Step 5: Verify baseline still green**

Run: `uv run pytest -q` (34 passed) and `cd frontend && npm run build` (builds).

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock frontend/package.json frontend/package-lock.json frontend/vite.config.ts
git commit -m "Add the AG-UI dependencies on both sides of the seam"
```

---

### Task: spike-copilotkit-direct

The week-one spike from the spec, run first: CopilotKit direct-connected to a
hand-rolled SSE endpoint, two chats with distinct threadIds. Kills the two top risks
(CopilotKit-accepts-our-SSE, provider-per-chat threading) before any real translator
code exists. The endpoint skeleton survives into the agui-endpoint task; the spike
frontend page is throwaway.

**Files:**
- Create: `a2a-orchestrator/src/a2a_orchestrator/agui.py` (echo version, replaced later)
- Modify: `a2a-orchestrator/src/a2a_orchestrator/app.py` (add route)
- Create: `a2a-orchestrator/frontend/src/SpikePane.tsx` (throwaway, deleted this task)
- Modify: `a2a-orchestrator/frontend/src/App.tsx` (temporarily render SpikePane, reverted this task)

**Interfaces:**
- Produces: `POST /agui/run` route registered in `app.py`; confidence (or a fallback
  decision) on the CopilotKit direct-connection idiom.

- [ ] **Step 1: Echo endpoint**

`src/a2a_orchestrator/agui.py`:

```python
"""The conversation plane: POST /agui/run, threadId-routed, SSE out.

Spike version: echoes the last user message back as one streamed assistant
message. Replaced by the translated pipeline in the agui-endpoint task.
"""

from __future__ import annotations

import uuid

from ag_ui.core import (
    RunAgentInput,
    RunErrorEvent,
    RunFinishedEvent,
    RunStartedEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
)
from ag_ui.encoder import EventEncoder
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse


async def run_agent(request: Request) -> StreamingResponse | JSONResponse:
    try:
        run_input = RunAgentInput.model_validate(await request.json())
    except Exception as exc:
        return JSONResponse({"error": f"not a RunAgentInput: {exc}"}, status_code=422)
    encoder = EventEncoder()

    async def stream():
        yield encoder.encode(
            RunStartedEvent(thread_id=run_input.thread_id, run_id=run_input.run_id)
        )
        text = run_input.messages[-1].content if run_input.messages else ""
        message_id = uuid.uuid4().hex
        yield encoder.encode(TextMessageStartEvent(message_id=message_id))
        yield encoder.encode(
            TextMessageContentEvent(
                message_id=message_id,
                delta=f"[{run_input.thread_id[:8]}] you said: {text}",
            )
        )
        yield encoder.encode(TextMessageEndEvent(message_id=message_id))
        yield encoder.encode(
            RunFinishedEvent(thread_id=run_input.thread_id, run_id=run_input.run_id)
        )

    return StreamingResponse(stream(), media_type=encoder.get_content_type())
```

- [ ] **Step 2: Route it**

In `app.py`, import `agui` and add to `routes` (before the static mount):

```python
        Route("/agui/run", agui.run_agent, methods=["POST"]),
```

- [ ] **Step 3: Curl the endpoint**

Start `uv run orch-serve`, then:

```bash
curl -sN http://127.0.0.1:9300/agui/run -H 'content-type: application/json' -d '{
  "threadId": "spike-thread", "runId": "r1", "state": null, "context": [],
  "forwardedProps": null, "tools": [],
  "messages": [{"id": "m1", "role": "user", "content": "hello spike"}]}'
```

Expected: four `data:` lines — RUN_STARTED, TEXT_MESSAGE_START/CONTENT/END, RUN_FINISHED,
with `[spike-th] you said: hello spike` in the CONTENT delta.

- [ ] **Step 4: Spike pane** — CopilotKit direct connection, exact idiom per the
  frontend research notes (see frontend-swap task for the settled imports/props; this
  step is where they get proven). Two `SpikePane` instances side by side with distinct
  hard-coded threadIds, each its own provider bound to one `HttpAgent({url: "/agui/run",
  threadId})`. Render both in `App.tsx` temporarily.

- [ ] **Step 5: Browser check (the spike's whole point)**

With `orch-serve` and `npm run dev` running, drive http://localhost:5173 with the
claude-in-chrome tools (delegate the page-reading to a subagent to keep dumps out of
the main context): send a message in each pane; each must echo with its own threadId
prefix and render as a streamed assistant message. Record what worked and what fought
back (these notes feed the DEVLOG and the frontend-swap task). If CopilotKit's
components fight the two-pane shape, drop one rung on the spec's fallback ladder
(headless hooks) and note it.

- [ ] **Step 6: Revert the spike frontend, keep the endpoint**

Delete `SpikePane.tsx`, revert `App.tsx`. Keep `agui.py` + the route. Commit:

```bash
git add src/a2a_orchestrator/agui.py src/a2a_orchestrator/app.py
git commit -m "Spike: AG-UI echo endpoint proves the CopilotKit direct connection"
```

---

### Task: translate-outbound

The forward half of the seam: A2A stream events in, AG-UI events out. Pure unit
tests — fixtures are protobuf `StreamResponse` objects built directly (verified
constructors below), no server.

**Files:**
- Create: `a2a-orchestrator/src/a2a_orchestrator/translate.py`
- Create: `a2a-orchestrator/tests/test_translate.py`

**Interfaces:**
- Produces: `RunTranslator(thread_id, run_id)` with `.feed(event) -> list[BaseEvent]`,
  `.finish() -> list[BaseEvent]`, `.parked: dict | None`, `.task_id: str`;
  module constants `PERMISSION_KEY = "a2acode_permission"`,
  `PERMISSION_TOOL = "request_permission"`.
- Consumes: nothing from other tasks.

- [ ] **Step 1: Write the failing tests**

`tests/test_translate.py`:

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_translate.py -q`
Expected: FAIL — `ModuleNotFoundError: a2a_orchestrator.translate`.

- [ ] **Step 3: Implement `translate.py` (outbound half)**

```python
"""Both directions of the AG-UI <-> A2A seam, and nothing else.

Outbound: RunTranslator turns one A2A turn's stream events into AG-UI events.
Inbound (added in the translate-inbound task): incoming_turn decides whether a
RunAgentInput is a fresh user message or a permission decision resuming a
parked task.

Stateful across one run on purpose: artifact chunks stream as one assistant
message (START once, a CONTENT delta per chunk, END at close), and terminal
A2A states surface only at finish() so the caller controls run lifecycle.
The endpoint emits RUN_STARTED itself, before upstream is contacted, so
pre-upstream failures can still land RUN_ERROR inside a well-formed run.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from a2a.types import StreamResponse, TaskState
from ag_ui.core import (
    BaseEvent,
    CustomEvent,
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
        self.parked: dict[str, Any] | None = None
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
            self.parked = metadata[PERMISSION_KEY]
            self.task_id = update.task_id or self.task_id
            self._final_state = "input_required"
            call_id = self.parked.get("request_id") or uuid.uuid4().hex
            return self._close_text() + [
                ToolCallStartEvent(tool_call_id=call_id, tool_call_name=PERMISSION_TOOL),
                ToolCallArgsEvent(tool_call_id=call_id, delta=json.dumps(self.parked)),
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
```

Note the input_required-without-permission case falls through to the CUSTOM
passthrough (state 5) rather than parking — a park without a payload is not a
permission, and the AskUserQuestion risk in the spec keeps us honest here.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_translate.py -q`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/a2a_orchestrator/translate.py tests/test_translate.py
git commit -m "Translate outbound: one A2A turn becomes one AG-UI run"
```

---

### Task: translate-inbound

The reverse half: `RunAgentInput` in, a `Turn` decision out — fresh text or a
permission decision. History is advisory; the tail is the message (spec, Domain model).

**Files:**
- Modify: `a2a-orchestrator/src/a2a_orchestrator/translate.py`
- Modify: `a2a-orchestrator/tests/test_translate.py`

**Interfaces:**
- Produces: `Turn` dataclass (`kind: Literal["message", "resume"]`, `text: str`) and
  `incoming_turn(run_input: RunAgentInput) -> Turn`. Raises `ValueError` on anything
  it cannot act on (endpoint maps that to RUN_ERROR).

- [ ] **Step 1: Write the failing tests** (append to `tests/test_translate.py`)

```python
import pytest
from ag_ui.core import RunAgentInput

from a2a_orchestrator.translate import Turn, incoming_turn


def run_input(messages):
    return RunAgentInput.model_validate(
        {
            "threadId": "th1",
            "runId": "r1",
            "state": None,
            "messages": messages,
            "tools": [],
            "context": [],
            "forwardedProps": None,
        }
    )


def test_trailing_user_message_is_a_fresh_turn():
    turn = incoming_turn(
        run_input(
            [
                {"id": "m1", "role": "user", "content": "hello"},
                {"id": "m2", "role": "assistant", "content": "hi"},
                {"id": "m3", "role": "user", "content": "please run the tests"},
            ]
        )
    )
    assert turn == Turn(kind="message", text="please run the tests")


def test_trailing_tool_result_is_a_resume():
    turn = incoming_turn(
        run_input(
            [
                {"id": "m1", "role": "user", "content": "please run the tests"},
                {
                    "id": "m2",
                    "role": "tool",
                    "toolCallId": "req-1",
                    "content": '{"decision": "allow"}',
                },
            ]
        )
    )
    assert turn == Turn(kind="resume", text="allow")


def test_bare_string_decision_also_works():
    turn = incoming_turn(
        run_input(
            [{"id": "m1", "role": "tool", "toolCallId": "req-1", "content": "deny"}]
        )
    )
    assert turn == Turn(kind="resume", text="deny")


def test_unknown_decision_refuses_loudly():
    with pytest.raises(ValueError, match="decision"):
        incoming_turn(
            run_input(
                [{"id": "m1", "role": "tool", "toolCallId": "req-1", "content": "maybe"}]
            )
        )


def test_empty_run_refuses_loudly():
    with pytest.raises(ValueError, match="no messages"):
        incoming_turn(run_input([]))
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_translate.py -q`
Expected: the new tests fail with `ImportError: cannot import name 'Turn'`.

- [ ] **Step 3: Implement** (append to `translate.py`; add
  `from dataclasses import dataclass` and `from typing import Literal` and the
  `RunAgentInput, ToolMessage, UserMessage` imports from `ag_ui.core`)

```python
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
    if isinstance(last, UserMessage) and last.content:
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
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_translate.py -q`
Expected: 11 passed.

- [ ] **Step 5: Commit**

```bash
git add src/a2a_orchestrator/translate.py tests/test_translate.py
git commit -m "Translate inbound: the RunAgentInput tail becomes a message or a resume"
```

---

### Task: a2a-conversations

The service-side A2A conversation per chat, grown from `a2a_rig.events`'s client
machinery (`a2a-rig/src/a2a_rig/events.py` — read it first; `user_message` and the
`send` loop are the prior art). Holds the client cache and the parked-task registry
the spec assigns to the service.

**Files:**
- Create: `a2a-orchestrator/src/a2a_orchestrator/a2a_client.py`
- Create: `a2a-orchestrator/tests/test_a2a_client.py`

**Interfaces:**
- Consumes: `Turn` from `translate.py`; `Chat` from `store.py` (only `.context_id`
  and `.upstream_url`); the shared `httpx.AsyncClient`.
- Produces:

```python
class Conversations:
    def __init__(self, http: httpx.AsyncClient): ...
    async def run_turn(self, chat, turn: Turn) -> AsyncIterator[StreamResponse]: ...
    def park(self, context_id: str, task_id: str) -> None: ...
    def clear(self, context_id: str) -> None: ...
    def parked_task(self, context_id: str) -> str | None: ...
```

`run_turn` raises `LookupError` if `turn.kind == "resume"` with nothing parked.
Park/clear is the *endpoint's* call after the translator has seen the whole turn —
`Conversations` only stores it.

- [ ] **Step 1: Write the failing tests**

`tests/test_a2a_client.py` (the `rig_url`/`http` fixtures already exist in
`tests/conftest.py`; base URLs resolve through the rig index the way a real consumer
would):

```python
"""Service-side A2A conversations, driven straight against the rig (no service)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import pytest

from a2a_orchestrator.a2a_client import Conversations
from a2a_orchestrator.translate import Turn


@dataclass
class FakeChat:
    context_id: str
    upstream_url: str


@pytest.fixture
async def billing_chat(rig_url, http):
    index = (await http.get(rig_url)).json()
    entry = next(e for e in index["repos"] if e["name"] == "billing-api")
    base = entry["card_url"].removesuffix(".well-known/agent-card.json")
    return FakeChat(context_id=uuid.uuid4().hex, upstream_url=base)


async def drain(events):
    return [event async for event in events]


async def test_free_text_round_trips(billing_chat, http):
    conversations = Conversations(http)
    events = await drain(
        conversations.run_turn(billing_chat, Turn(kind="message", text="hello from the cockpit"))
    )
    kinds = [e.WhichOneof("payload") for e in events]
    assert "task" in kinds
    assert "artifact_update" in kinds


async def test_upstream_adopts_the_minted_context(billing_chat, http):
    conversations = Conversations(http)
    events = await drain(
        conversations.run_turn(billing_chat, Turn(kind="message", text="hello"))
    )
    task = next(e.task for e in events if e.WhichOneof("payload") == "task")
    assert task.context_id == billing_chat.context_id


async def test_resume_targets_the_parked_task(billing_chat, http):
    conversations = Conversations(http)
    parked = await drain(
        conversations.run_turn(billing_chat, Turn(kind="message", text="please run the tests"))
    )
    task_id = next(e.task.id for e in parked if e.WhichOneof("payload") == "task")
    conversations.park(billing_chat.context_id, task_id)
    assert conversations.parked_task(billing_chat.context_id) == task_id

    resumed = await drain(
        conversations.run_turn(billing_chat, Turn(kind="resume", text="allow"))
    )
    resumed_task_ids = {
        e.status_update.task_id
        for e in resumed
        if e.WhichOneof("payload") == "status_update"
    }
    assert resumed_task_ids == {task_id}
    conversations.clear(billing_chat.context_id)
    assert conversations.parked_task(billing_chat.context_id) is None


async def test_resume_with_nothing_parked_refuses(billing_chat, http):
    conversations = Conversations(http)
    with pytest.raises(LookupError, match=billing_chat.context_id):
        await drain(conversations.run_turn(billing_chat, Turn(kind="resume", text="allow")))
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_a2a_client.py -q`
Expected: FAIL — `ModuleNotFoundError: a2a_orchestrator.a2a_client`.

- [ ] **Step 3: Implement `a2a_client.py`**

```python
"""Service-side A2A conversations, one per chat.

Grown from a2a-rig's harness client (a2a_rig/events.py): same create_client +
send_message loop, reshaped as a long-lived registry. The service — not the
browser — owns which task a chat has parked; agui.py records it here after
the translator has seen the whole turn. In-memory by design (spec: Domain
model / Identifiers): a service restart loses the park, same deferral class
as reload replay, both resolved by the future event log.
"""

from __future__ import annotations

import uuid
from typing import AsyncIterator, Protocol

import httpx
from a2a.client import create_client
from a2a.client.client import ClientConfig
from a2a.types import Message, Part, Role, SendMessageRequest, StreamResponse

from a2a_orchestrator.translate import Turn


class ChatLike(Protocol):
    context_id: str
    upstream_url: str


class Conversations:
    def __init__(self, http: httpx.AsyncClient):
        self._http = http
        self._clients: dict[str, object] = {}
        self._parked: dict[str, str] = {}

    def park(self, context_id: str, task_id: str) -> None:
        self._parked[context_id] = task_id

    def clear(self, context_id: str) -> None:
        self._parked.pop(context_id, None)

    def parked_task(self, context_id: str) -> str | None:
        return self._parked.get(context_id)

    async def _client(self, chat: ChatLike):
        if chat.context_id not in self._clients:
            self._clients[chat.context_id] = await create_client(
                chat.upstream_url,
                ClientConfig(streaming=True, httpx_client=self._http),
            )
        return self._clients[chat.context_id]

    async def run_turn(
        self, chat: ChatLike, turn: Turn
    ) -> AsyncIterator[StreamResponse]:
        task_id = ""
        if turn.kind == "resume":
            task_id = self._parked.get(chat.context_id, "")
            if not task_id:
                raise LookupError(f"no parked task for context {chat.context_id!r}")
        client = await self._client(chat)
        message = Message(
            message_id=uuid.uuid4().hex,
            role=Role.ROLE_USER,
            parts=[Part(text=turn.text)],
        )
        message.context_id = chat.context_id
        if task_id:
            message.task_id = task_id
        async for event in client.send_message(SendMessageRequest(message=message)):
            yield event
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_a2a_client.py -q`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/a2a_orchestrator/a2a_client.py tests/test_a2a_client.py
git commit -m "Conversations: the service is the A2A client, and owns the park"
```

---

### Task: agui-endpoint

Replace the spike echo in `agui.py` with the real pipeline: inbound translate → A2A
turn → outbound translate → SSE. Integration tests mirror `test_proxy.py`'s bodies
through the new plane.

**Files:**
- Modify: `a2a-orchestrator/src/a2a_orchestrator/agui.py` (replace echo)
- Modify: `a2a-orchestrator/src/a2a_orchestrator/app.py` (lifespan grows `conversations`)
- Create: `a2a-orchestrator/tests/test_agui.py`

**Interfaces:**
- Consumes: `RunTranslator`, `incoming_turn`, `Turn` (translate.py);
  `Conversations` (a2a_client.py); `store.chat_for_context`.
- Produces: `POST /agui/run` — SSE stream of AG-UI events; `app.state.conversations`.

- [ ] **Step 1: Write the failing tests**

`tests/test_agui.py` (uses the existing `service_url`, `http`, `mission`, `open_chat`
fixtures):

```python
"""The conversation plane, end to end: RunAgentInput in, AG-UI SSE out, rig behind."""

from __future__ import annotations

import json
import uuid


def events_of(body: str) -> list[dict]:
    return [
        json.loads(line[len("data: "):])
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


async def run(http, service_url, thread_id, messages):
    payload = {
        "threadId": thread_id,
        "runId": uuid.uuid4().hex,
        "state": None,
        "messages": messages,
        "tools": [
            {
                "name": "request_permission",
                "description": "Ask the user to allow or deny a tool use",
                "parameters": {"type": "object", "properties": {}},
            }
        ],
        "context": [],
        "forwardedProps": None,
    }
    response = await http.post(f"{service_url}agui/run", json=payload)
    assert response.status_code == 200, response.text
    return events_of(response.text)


def user_says(text):
    return [{"id": uuid.uuid4().hex, "role": "user", "content": text}]


def types_of(events):
    return [e["type"] for e in events]


def text_of(events):
    return "".join(
        e["delta"] for e in events if e["type"] == "TEXT_MESSAGE_CONTENT"
    )


async def test_free_text_round_trips(mission, open_chat, http, service_url):
    chat = await open_chat(mission["id"], "billing-api")
    events = await run(
        http, service_url, chat["context_id"], user_says("hello from the cockpit")
    )
    assert types_of(events)[0] == "RUN_STARTED"
    assert types_of(events)[-1] == "RUN_FINISHED"
    assert "Ready when you are" in text_of(events)


async def test_permission_parks_as_a_tool_call_and_allow_resumes(
    mission, open_chat, http, service_url
):
    chat = await open_chat(mission["id"], "billing-api")
    parked = await run(
        http, service_url, chat["context_id"], user_says("please run the tests")
    )
    starts = [e for e in parked if e["type"] == "TOOL_CALL_START"]
    assert [e["toolCallName"] for e in starts] == ["request_permission"]
    call_id = starts[0]["toolCallId"]
    args = json.loads(
        "".join(e["delta"] for e in parked if e["type"] == "TOOL_CALL_ARGS")
    )
    assert args["tool"] == "Bash"
    assert types_of(parked)[-1] == "RUN_FINISHED"

    resumed = await run(
        http,
        service_url,
        chat["context_id"],
        [
            {
                "id": uuid.uuid4().hex,
                "role": "tool",
                "toolCallId": call_id,
                "content": json.dumps({"decision": "allow"}),
            }
        ],
    )
    assert types_of(resumed)[-1] == "RUN_FINISHED"
    assert not [e for e in resumed if e["type"] == "RUN_ERROR"]


async def test_deny_reads_as_the_skipped_ending(mission, open_chat, http, service_url):
    chat = await open_chat(mission["id"], "billing-api")
    parked = await run(
        http, service_url, chat["context_id"], user_says("please run the tests")
    )
    call_id = next(e["toolCallId"] for e in parked if e["type"] == "TOOL_CALL_START")
    resumed = await run(
        http,
        service_url,
        chat["context_id"],
        [
            {
                "id": uuid.uuid4().hex,
                "role": "tool",
                "toolCallId": call_id,
                "content": json.dumps({"decision": "deny"}),
            }
        ],
    )
    assert "Skipped the test run" in text_of(resumed)


async def test_upstream_failure_is_a_run_error(mission, open_chat, http, service_url):
    chat = await open_chat(mission["id"], "infra-terraform")
    events = await run(
        http, service_url, chat["context_id"], user_says("status check please")
    )
    assert types_of(events)[0] == "RUN_STARTED"
    assert types_of(events)[-1] == "RUN_ERROR"
    assert events[-1]["message"]


async def test_unbound_thread_is_a_run_error(http, service_url):
    events = await run(http, service_url, "deadbeef", user_says("hello"))
    assert types_of(events) == ["RUN_STARTED", "RUN_ERROR"]
    assert "deadbeef" in events[-1]["message"]


async def test_resume_with_nothing_parked_is_a_run_error(
    mission, open_chat, http, service_url
):
    chat = await open_chat(mission["id"], "billing-api")
    events = await run(
        http,
        service_url,
        chat["context_id"],
        [
            {
                "id": uuid.uuid4().hex,
                "role": "tool",
                "toolCallId": "req-x",
                "content": json.dumps({"decision": "allow"}),
            }
        ],
    )
    assert types_of(events)[-1] == "RUN_ERROR"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_agui.py -q`
Expected: FAIL — the spike echo answers everything with an echo, so the first
assertion on "Ready when you are" fails (and there is no `app.state.conversations`).

- [ ] **Step 3: Replace the echo with the pipeline**

`agui.py` becomes:

```python
"""The conversation plane: POST /agui/run, threadId-routed, SSE out.

One run per turn. RUN_STARTED is emitted before upstream is contacted so
every failure after it — unknown thread, refused turn, upstream fault —
lands inside the run as RUN_ERROR rather than as a broken transport. The
park/clear decision happens here, after the translator has seen the whole
turn: parked permission -> remember the taskId, anything else -> forget it.
"""

from __future__ import annotations

from ag_ui.core import RunAgentInput, RunErrorEvent, RunStartedEvent
from ag_ui.encoder import EventEncoder
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse

from a2a_orchestrator.translate import RunTranslator, incoming_turn


async def run_agent(request: Request) -> StreamingResponse | JSONResponse:
    try:
        run_input = RunAgentInput.model_validate(await request.json())
    except Exception as exc:
        return JSONResponse({"error": f"not a RunAgentInput: {exc}"}, status_code=422)

    store = request.app.state.store
    conversations = request.app.state.conversations
    encoder = EventEncoder()

    async def stream():
        yield encoder.encode(
            RunStartedEvent(thread_id=run_input.thread_id, run_id=run_input.run_id)
        )
        chat = store.chat_for_context(run_input.thread_id)
        if chat is None:
            yield encoder.encode(
                RunErrorEvent(
                    message=f"no chat bound for thread {run_input.thread_id!r}"
                )
            )
            return
        translator = RunTranslator(run_input.thread_id, run_input.run_id)
        try:
            turn = incoming_turn(run_input)
            async for event in conversations.run_turn(chat, turn):
                for out in translator.feed(event):
                    yield encoder.encode(out)
            for out in translator.finish():
                yield encoder.encode(out)
        except Exception as exc:  # every failure must reach the stream as RUN_ERROR
            yield encoder.encode(RunErrorEvent(message=str(exc)))
            return
        if translator.parked and translator.task_id:
            conversations.park(chat.context_id, translator.task_id)
        else:
            conversations.clear(chat.context_id)

    return StreamingResponse(stream(), media_type=encoder.get_content_type())
```

In `app.py`'s lifespan, after `app.state.http = http`:

```python
            app.state.conversations = Conversations(http)
```

with `from a2a_orchestrator.a2a_client import Conversations` at the top.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_agui.py -q` — 6 passed.
Then the whole suite: `uv run pytest -q` — everything green (proxy tests still pass;
both planes coexist until delete-legacy).

- [ ] **Step 5: Commit**

```bash
git add src/a2a_orchestrator/agui.py src/a2a_orchestrator/app.py tests/test_agui.py
git commit -m "The AG-UI endpoint: translated turns over the service's own A2A client"
```

---

### Task: frontend-swap

`ChatPane` becomes a CopilotKit provider + chat component bound to the chat's
`context_id` as threadId; `ApprovalCard` becomes `request_permission`'s renderer.
Exact imports/props verified in the spike task — the code below is the settled shape.

**Files:**
- Modify: `a2a-orchestrator/frontend/src/ChatPane.tsx` (rewritten thin)
- Modify: `a2a-orchestrator/frontend/src/ApprovalCard.tsx` (self-contained types)
- Modify: `a2a-orchestrator/frontend/src/main.tsx` or `index.css` (CopilotKit styles import)

**Interfaces:**
- Consumes: `ChatRef` from `api.ts` (`context_id`, `agent`); `/agui/run` endpoint.
- Produces: the new conversation UI; `a2a.ts` has no remaining importers.

- [ ] **Step 1: ApprovalCard owns its Permission type** (drop the `./a2a` import)

```tsx
export interface Permission {
  tool: string
  request_id: string
  input: Record<string, unknown>
}

export function ApprovalCard({
  permission,
  onAnswer,
}: {
  permission: Permission
  onAnswer: (decision: 'allow' | 'deny') => void
}) {
  return (
    <aside className="approval">
      <p>
        <b>Approval requested:</b> {permission.tool}
      </p>
      <pre>{JSON.stringify(permission.input, null, 2)}</pre>
      <button onClick={() => onAnswer('allow')}>Allow</button>
      <button onClick={() => onAnswer('deny')}>Deny</button>
    </aside>
  )
}
```

- [ ] **Step 2: Rewrite `ChatPane.tsx`** (idiom as proven in the spike; adjust names
  only if the spike found the documented ones moved)

```tsx
import { useMemo } from 'react'
import { HttpAgent } from '@ag-ui/client'
import { CopilotKit, useCopilotAction } from '@copilotkit/react-core'
import { CopilotChat } from '@copilotkit/react-ui'
import '@copilotkit/react-ui/styles.css'
import type { ChatRef } from './api'
import { ApprovalCard, type Permission } from './ApprovalCard'

// request_permission is the one wire contract the cockpit mints (spec: Domain
// model): args are a2acode's permission payload verbatim, the result is
// {decision}. CopilotKit does the park-and-resume choreography.
function PermissionAction() {
  useCopilotAction({
    name: 'request_permission',
    available: 'remote',
    renderAndWaitForResponse: ({ args, respond, status }) => {
      if (status === 'complete') return <></>
      const permission = args as unknown as Permission
      return (
        <ApprovalCard
          permission={permission}
          onAnswer={(decision) => respond?.(JSON.stringify({ decision }))}
        />
      )
    },
  })
  return null
}

export function ChatPane({ chat }: { chat: ChatRef }) {
  const agent = useMemo(
    () => new HttpAgent({ url: '/agui/run', threadId: chat.context_id }),
    [chat.context_id],
  )
  return (
    <section>
      <h2>{chat.agent}</h2>
      <CopilotKit agent__unsafe_dev_only={agent} threadId={chat.context_id}>
        <PermissionAction />
        <CopilotChat labels={{ placeholder: `Message ${chat.agent}` }} />
      </CopilotKit>
    </section>
  )
}
```

- [ ] **Step 3: Type-check and build**

Run: `cd frontend && npm run build`
Expected: clean build. (`npm run lint` too.)

- [ ] **Step 4: Hands-on check against the rig**

Three processes (`rig-serve`, `orch-serve`, `npm run dev`), then walk the README demo
flow manually in the browser: hello → streamed reply; "please run the tests" →
ApprovalCard renders in-flow; Allow → completion; Deny (fresh chat) → "Skipped the
test run"; infra-terraform → visible error rendering. Fix what fights back before
committing.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/ChatPane.tsx frontend/src/ApprovalCard.tsx
git commit -m "ChatPane is a CopilotKit provider; ApprovalCard renders request_permission"
```

---

### Task: delete-legacy

The strangler's last move: the old plane goes, nothing keeps it alive.

**Files:**
- Delete: `a2a-orchestrator/frontend/src/a2a.ts`, `a2a-orchestrator/src/a2a_orchestrator/proxy.py`, `a2a-orchestrator/tests/test_proxy.py`
- Modify: `a2a-orchestrator/src/a2a_orchestrator/app.py` (drop proxy route + import)
- Modify: `a2a-orchestrator/src/a2a_orchestrator/store.py` (drop the `a2a_url` property)
- Modify: `a2a-orchestrator/src/a2a_orchestrator/api.py` (drop `a2a_url` from `_chat_json`)
- Modify: `a2a-orchestrator/frontend/src/api.ts` (drop `a2a_url` from `ChatRef`)
- Modify: `a2a-orchestrator/frontend/vite.config.ts` (drop `/a2a` proxy entry + stale comment)
- Modify: `a2a-orchestrator/frontend/package.json` (npm uninstall `@a2a-js/sdk`)

- [ ] **Step 1: Delete the files**

```bash
git rm frontend/src/a2a.ts src/a2a_orchestrator/proxy.py tests/test_proxy.py
```

- [ ] **Step 2: Remove the references**

- `app.py`: delete the `/a2a/chats/...` Route and the `proxy` import; update the
  module docstring (the proxy clause is stale).
- `store.py`: delete the `a2a_url` property on `Chat`.
- `api.py`: delete the `"a2a_url"` line in `_chat_json`.
- `api.ts`: delete `a2a_url` from `ChatRef`.
- `vite.config.ts`: delete the `'/a2a'` proxy line and rewrite the comment (it
  narrates the card rewrite, which no longer exists).
- `cd frontend && npm uninstall @a2a-js/sdk`

- [ ] **Step 3: Full verification**

Run: `uv run pytest -q` (proxy tests gone, everything else green) and
`cd frontend && npm run build && npm run lint`.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "Delete the browser A2A plane: proxy, card rewrite, a2a.ts"
```

---

### Task: browser-validation

The goal's exit criterion: see it working in a real browser, through the new plane,
at rig pacing. Use the claude-in-chrome tools; delegate context-heavy page reading to
a subagent and keep only conclusions.

- [ ] **Step 1: Start the stack** (from `a2a-orchestrator/` in the worktree)

```bash
uv run rig-serve --repos ../a2a-rig/repos --port 9200   # terminal 1
uv run orch-serve                                        # terminal 2
cd frontend && npm run dev                               # terminal 3
```

- [ ] **Step 2: Walk the README demo through the AG-UI plane**

At http://localhost:5173: new mission → chat with `billing-api` → hello (streamed
reply) → "please run the tests" (ApprovalCard) → Allow (completes) → fresh chat →
deny path ("Skipped the test run") → `infra-terraform` chat → failure rendering.
Two chats open in sequence must not bleed threads (distinct contextIds).

- [ ] **Step 3: Capture evidence**

Record a GIF of the allow path (`gif_creator`), and note anything that renders
generically (STEP events, CUSTOM passthroughs) for the DEVLOG.

---

### Task: reference-run

The Phase 7 lesson, honored: one real a2acode conversation through the new plane
before the mapping is declared done. (Recordings corrected hand-imagined assumptions
twice; the translation table is hand-imagined until this passes.)

- [ ] **Step 1: Live upstream**

```bash
uv run --project ~/github.com/kanywst/a2acode a2acode serve \
  --backend claude --cwd ~/scratch/demo-app   # port 9100, subscription auth, no --permission-mode
```

- [ ] **Step 2: Point the service at it**

`uv run orch-serve --catalog catalog-live.yaml` (the static-provider catalog from the
2026-08-12 live run points at :9100).

- [ ] **Step 3: One conversation with one approval**

Through the cockpit: a small prompt that provokes a tool approval (the live-run
precedent: "Add a /health endpoint and run the tests" against `~/scratch/demo-app`).
Verify: streaming text renders, the approval card carries the real payload, allow
resumes, completion lands. Note any event shape the translator passed through as
CUSTOM — each one is a mapping decision to make deliberately, in the spec's table.

- [ ] **Step 4: Correct the table if reality disagrees**

Any discrepancy: fix `translate.py` + tests first, then amend the spec's translation
table and note the correction in DEVLOG (that's the documented update path).

---

### Task: docs-and-merge

- [ ] **Step 1: DEVLOG entry** — dated section: the reversal shipped, what the spike
  found, what the reference run corrected, the deleted-proxy moment, test counts.
- [ ] **Step 2: README refresh** — `a2a-orchestrator/README.md`: the demo narrative
  now goes through AG-UI/CopilotKit; the spec pointer moves to the 2026-08-12 spec;
  the "Run it" section is unchanged except any new note.
- [ ] **Step 3: Check the followup ledger** — taskwarrior `fc4eb2d8` (message
  persistence) stays open; add any new followups found during implementation. Add one
  now: the spec's "Playwright: unchanged in role" line describes a suite that does not
  exist yet in this repo — capture "Playwright suite for the cockpit through the AG-UI
  plane (allow/deny/failure at rig pacing)" as a taskwarrior task rather than growing
  this milestone.
- [ ] **Step 4: Merge** — `git checkout main && git merge --no-ff agui-native` with a
  merge message in the repo's voice ("Merge agui-native: ..."), push, then
  `wt remove agui-native -y`.
