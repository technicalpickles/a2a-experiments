# AG-UI Event Log Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist both directions of the AG-UI seam to SQLite, replay history through the connect handshake on remount, keep pending approvals answerable across service restarts, and close the three hardening gaps in the run loop.

**Architecture:** `agui.py` writes every outbound AG-UI event and each incoming message tail to a new `events` table as the stream flows; a new `/agui/connect` endpoint folds the log into AG-UI messages and answers the `connectAgent()` call CopilotKit already makes on mount (via a tiny `HttpAgent` subclass). Pending-approval state moves from an in-memory dict to three nullable columns on `chats`, and the browser re-arms a pending card via `copilotkit.runTool()`. Spec: `docs/superpowers/specs/2026-08-13-agui-event-log-design.md`.

**Tech Stack:** Python (Starlette, `ag-ui-protocol` 0.1.19, `a2a-sdk`, sqlite3), pytest + pytest-asyncio pinned against the playback rig, React + `@copilotkit/react-core` 1.67.1 (`/v2` subpath) + `@ag-ui/client` 0.0.57.

## Global Constraints

- All work on branch `agui-event-log` (create the worktree via `wt` per the worktrees rule). Code changes branch; only this plan document and the spec live on `main`.
- Python commands run from `a2a-orchestrator/`: tests are `uv run pytest -q` (async tests need no markers; asyncio mode is auto).
- Frontend commands run from `a2a-orchestrator/frontend/`: verify with `npm run build` and `npm run lint`. There is no JS unit-test runner; browser behavior is validated manually in the final tasks.
- The word is **pending**, never "park"/"parked" — in code, comments, tests, and commit messages.
- Do NOT upgrade `@copilotkit/react-core`, `@ag-ui/client`, or `ag-ui-protocol`. The connect handshake, snapshot-merge semantics, and `runTool` re-arm are verified against the versions above (spec: "Risks and deferrals").
- Log only the `/agui/run` seam. `/agui/connect` replays derived data and must never write to the event log.
- Commit messages follow the repo's narrative sentence style (see `git log --oneline`), not conventional-commit prefixes.

---

### Task rename-pending: retire "park" from the vocabulary

Pure rename, no behavior change. The in-memory dict survives this task (it dies in conversations-on-the-store).

**Files:**
- Modify: `a2a-orchestrator/src/a2a_orchestrator/a2a_client.py`
- Modify: `a2a-orchestrator/src/a2a_orchestrator/agui.py`
- Modify: `a2a-orchestrator/src/a2a_orchestrator/translate.py`
- Modify: `a2a-orchestrator/tests/test_a2a_client.py`
- Modify: `a2a-orchestrator/tests/test_translate.py`
- Modify: `a2a-orchestrator/tests/test_agui.py` (test names/comments only)

**Interfaces:**
- Consumes: current names `Conversations.park/clear/parked_task`, `RunTranslator.parked`.
- Produces: `Conversations.set_pending(context_id, task_id)`, `Conversations.clear_pending(context_id)`, `Conversations.pending_task(context_id)`, `RunTranslator.pending` (the permission payload dict or `None`). Later tasks use exactly these names.

- [ ] **Step 1: Rename in source**

In `translate.py`: `self.parked` → `self.pending` (constructor, `_status`, docstrings — the module docstring says "resuming a parked task", make it "resuming a pending task").

In `a2a_client.py`: `park` → `set_pending`, `clear` → `clear_pending`, `parked_task` → `pending_task`, `self._parked` → `self._pending`; update the docstring ("owns which task a chat has parked" → "owns which task a chat has pending", "a service restart loses the park" → "loses the pending state"). Error message in `run_turn`: `f"no pending task for context {chat.context_id!r}"`.

In `agui.py`: call sites become `translator.pending`, `conversations.set_pending(...)`, `conversations.clear_pending(...)`; rewrite the park-related comments and docstring sentences using "pending" ("The pending state is replaced or consumed, never incidentally dropped", "parked permission -> remember the taskId" → "pending permission -> remember the taskId").

- [ ] **Step 2: Rename in tests**

`test_a2a_client.py`: `conversations.park(...)` → `conversations.set_pending(...)`, `conversations.parked_task(...)` → `conversations.pending_task(...)`, `conversations.clear(...)` → `conversations.clear_pending(...)`; rename `test_resume_targets_the_parked_task` → `test_resume_targets_the_pending_task`, `test_resume_with_nothing_parked_refuses` → `test_resume_with_nothing_pending_refuses`. `test_translate.py`: `translator.parked` → `translator.pending` (two assertions), rename `test_permission_parks_as_a_tool_call` → `test_permission_pends_as_a_tool_call`. `test_agui.py`: rename `test_permission_parks_as_a_tool_call_and_allow_resumes` → `test_permission_pends_as_a_tool_call_and_allow_resumes`, `test_fresh_message_while_parked_leaves_the_card_answerable` → `test_fresh_message_while_pending_leaves_the_card_answerable`, `test_resume_with_nothing_parked_is_a_run_error` → `test_resume_with_nothing_pending_is_a_run_error`; local variable `parked` → `pending` where it appears.

Then `grep -rn -i "park" a2a-orchestrator/src a2a-orchestrator/tests` — zero hits.

- [ ] **Step 3: Run the suite**

Run: `uv run pytest -q` (from `a2a-orchestrator/`)
Expected: all pass (51 tests at last count).

- [ ] **Step 4: Commit**

```bash
git add -A a2a-orchestrator
git commit -m "Rename park to pending across the conversation plane"
```

---

### Task store-event-log-and-pending: schema + Store methods

**Files:**
- Modify: `a2a-orchestrator/src/a2a_orchestrator/store.py`
- Test: `a2a-orchestrator/tests/test_store.py`

**Interfaces:**
- Consumes: existing `Store`, `Chat`, `Mission`.
- Produces:
  - `@dataclass Pending: task_id: str; call_id: str; payload: str` (payload = permission JSON verbatim)
  - `Store.append_event(context_id: str, direction: str, payload: str) -> None`
  - `Store.events_for_context(context_id: str) -> list[tuple[int, str, str]]` — `(seq, direction, payload)` ordered by `seq`
  - `Store.set_pending(context_id: str, task_id: str, call_id: str, payload: str) -> None`
  - `Store.clear_pending(context_id: str) -> None`
  - `Store.pending_of(context_id: str) -> Pending | None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_store.py` (it already imports `Store`; add `import sqlite3` and `from a2a_orchestrator.store import Pending`):

```python
def _chat(store):
    mission = store.create_mission()
    return store.create_chat(mission.id, "billing-api", "http://upstream/")


def test_events_append_in_order(tmp_path):
    store = Store(tmp_path / "orch.db")
    chat = _chat(store)
    store.append_event(chat.context_id, "out", '{"type": "RUN_STARTED"}')
    store.append_event(chat.context_id, "in", '{"role": "user"}')
    rows = store.events_for_context(chat.context_id)
    assert [(seq, direction) for seq, direction, _ in rows] == [(1, "out"), (2, "in")]
    assert rows[0][2] == '{"type": "RUN_STARTED"}'


def test_events_are_isolated_per_context(tmp_path):
    store = Store(tmp_path / "orch.db")
    one, two = _chat(store), _chat(store)
    store.append_event(one.context_id, "out", "{}")
    store.append_event(two.context_id, "out", "{}")
    store.append_event(one.context_id, "in", "{}")
    assert [seq for seq, _, _ in store.events_for_context(one.context_id)] == [1, 2]
    assert [seq for seq, _, _ in store.events_for_context(two.context_id)] == [1]


def test_pending_set_read_clear(tmp_path):
    store = Store(tmp_path / "orch.db")
    chat = _chat(store)
    assert store.pending_of(chat.context_id) is None
    store.set_pending(chat.context_id, "t1", "req-1", '{"tool": "Bash"}')
    assert store.pending_of(chat.context_id) == Pending(
        task_id="t1", call_id="req-1", payload='{"tool": "Bash"}'
    )
    store.clear_pending(chat.context_id)
    assert store.pending_of(chat.context_id) is None


def test_events_and_pending_survive_reopen(tmp_path):
    path = tmp_path / "orch.db"
    store = Store(path)
    chat = _chat(store)
    store.append_event(chat.context_id, "out", '{"type": "RUN_STARTED"}')
    store.set_pending(chat.context_id, "t1", "req-1", "{}")

    reopened = Store(path)
    assert [seq for seq, _, _ in reopened.events_for_context(chat.context_id)] == [1]
    assert reopened.pending_of(chat.context_id).task_id == "t1"


def test_migration_adds_pending_columns_to_an_existing_db(tmp_path):
    path = tmp_path / "orch.db"
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE missions (id TEXT PRIMARY KEY, title TEXT NOT NULL,
                               created_at TEXT NOT NULL);
        CREATE TABLE chats (context_id TEXT PRIMARY KEY,
                            mission_id TEXT NOT NULL REFERENCES missions(id),
                            agent TEXT NOT NULL, upstream_url TEXT NOT NULL,
                            created_at TEXT NOT NULL);
        INSERT INTO missions VALUES ('m1', 'title', 'now');
        INSERT INTO chats VALUES ('c1', 'm1', 'billing-api', 'http://up/', 'now');
        """
    )
    db.commit()
    db.close()

    store = Store(path)
    assert store.pending_of("c1") is None
    store.set_pending("c1", "t1", "req-1", "{}")
    assert store.pending_of("c1").task_id == "t1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_store.py -q`
Expected: FAIL — `ImportError: cannot import name 'Pending'` / `AttributeError: append_event`.

- [ ] **Step 3: Implement**

In `store.py`:

Extend `_SCHEMA` — the `chats` CREATE gains the three nullable columns, and the `events` table is added:

```sql
CREATE TABLE IF NOT EXISTS chats (
    context_id TEXT PRIMARY KEY,
    mission_id TEXT NOT NULL REFERENCES missions(id),
    agent TEXT NOT NULL,
    upstream_url TEXT NOT NULL,
    created_at TEXT NOT NULL,
    pending_task_id TEXT,
    pending_call_id TEXT,
    pending_payload TEXT
);
CREATE TABLE IF NOT EXISTS events (
    context_id TEXT NOT NULL REFERENCES chats(context_id),
    seq INTEGER NOT NULL,
    direction TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (context_id, seq)
);
```

`CREATE TABLE IF NOT EXISTS` skips existing tables, so pre-existing dbs need the columns added; `__init__` grows a migration call after `executescript`:

```python
def __init__(self, path: str | Path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    self._db = sqlite3.connect(path, check_same_thread=False)
    self._db.executescript(_SCHEMA)
    self._migrate()

def _migrate(self) -> None:
    """Bring pre-events-log db files up to the current chats shape."""
    for column in ("pending_task_id", "pending_call_id", "pending_payload"):
        try:
            with self._db:
                self._db.execute(f"ALTER TABLE chats ADD COLUMN {column} TEXT")
        except sqlite3.OperationalError:
            pass  # column already exists
```

`create_chat`'s INSERT must name its columns now that `chats` has more of them:

```python
self._db.execute(
    "INSERT INTO chats (context_id, mission_id, agent, upstream_url, created_at) "
    "VALUES (?, ?, ?, ?, ?)",
    (chat.context_id, chat.mission_id, chat.agent,
     chat.upstream_url, chat.created_at),
)
```

Add the dataclass and methods:

```python
@dataclass
class Pending:
    task_id: str
    call_id: str
    payload: str


def append_event(self, context_id: str, direction: str, payload: str) -> None:
    with self._db:
        self._db.execute(
            "INSERT INTO events (context_id, seq, direction, payload, created_at) "
            "SELECT ?, COALESCE(MAX(seq), 0) + 1, ?, ?, ? FROM events "
            "WHERE context_id = ?",
            (context_id, direction, payload, _now(), context_id),
        )


def events_for_context(self, context_id: str) -> list[tuple[int, str, str]]:
    return self._db.execute(
        "SELECT seq, direction, payload FROM events "
        "WHERE context_id = ? ORDER BY seq",
        (context_id,),
    ).fetchall()


def set_pending(self, context_id: str, task_id: str, call_id: str, payload: str) -> None:
    with self._db:
        self._db.execute(
            "UPDATE chats SET pending_task_id = ?, pending_call_id = ?, "
            "pending_payload = ? WHERE context_id = ?",
            (task_id, call_id, payload, context_id),
        )


def clear_pending(self, context_id: str) -> None:
    with self._db:
        self._db.execute(
            "UPDATE chats SET pending_task_id = NULL, pending_call_id = NULL, "
            "pending_payload = NULL WHERE context_id = ?",
            (context_id,),
        )


def pending_of(self, context_id: str) -> Pending | None:
    row = self._db.execute(
        "SELECT pending_task_id, pending_call_id, pending_payload "
        "FROM chats WHERE context_id = ?",
        (context_id,),
    ).fetchone()
    if row is None or row[0] is None:
        return None
    return Pending(task_id=row[0], call_id=row[1], payload=row[2])
```

(The spec names one column, `pending_task_id`; verification and re-arm need the call id and payload persisted beside it — an elaboration the spec's own "Verified resumes" and "Re-arming" sections force, not a contradiction.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_store.py -q` then `uv run pytest -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add -A a2a-orchestrator
git commit -m "Store grows the event log and pending columns, with migration"
```

---

### Task translator-truncation: a cut-off stream is an error, and the call id is captured

**Files:**
- Modify: `a2a-orchestrator/src/a2a_orchestrator/translate.py`
- Test: `a2a-orchestrator/tests/test_translate.py`

**Interfaces:**
- Consumes: `RunTranslator` as renamed (`.pending`).
- Produces: `RunTranslator.call_id: str` (the tool-call id emitted for a pending permission, `""` otherwise); `RunTranslator.truncated: bool` (True when `finish()` found no terminal state); `finish()` emits `RunErrorEvent` on truncation. `agui.py` (next tasks) reads all three.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_translate.py`:

```python
def test_stream_ending_without_terminal_state_is_a_run_error():
    translator = RunTranslator("th1", "r1")
    out = drain(translator, [task_event(), artifact_event("half an ans")])
    assert types_of(out) == ["TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT",
                             "TEXT_MESSAGE_END", "RUN_ERROR"]
    assert "terminal state" in out[-1].message
    assert translator.truncated


def test_terminal_states_are_not_truncation():
    translator = RunTranslator("th1", "r1")
    drain(translator, [task_event(), status_event(TaskState.TASK_STATE_COMPLETED)])
    assert not translator.truncated


def test_pending_permission_captures_the_call_id():
    permission = {"tool": "Bash", "request_id": "req-1", "input": {}}
    translator = RunTranslator("th1", "r1")
    drain(
        translator,
        [task_event(), status_event(TaskState.TASK_STATE_INPUT_REQUIRED,
                                    text="Bash",
                                    metadata={"a2acode_permission": permission})],
    )
    assert translator.call_id == "req-1"
```

Also update the existing `test_unknown_payloads_pass_through_as_custom`: a stream of only unknown payloads never reaches a terminal state, so under the new rule it ends in `RUN_ERROR`:

```python
def test_unknown_payloads_pass_through_as_custom():
    translator = RunTranslator("th1", "r1")
    out = drain(translator, [StreamResponse()])
    assert types_of(out) == ["CUSTOM", "RUN_ERROR"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_translate.py -q`
Expected: FAIL — no `truncated`/`call_id` attributes; unknown-payload test fails on `RUN_FINISHED`.

- [ ] **Step 3: Implement**

In `RunTranslator.__init__` add `self.call_id = ""` and `self.truncated = False`. In `_status`, the pending-permission branch already computes `call_id` — store it: `self.call_id = call_id` (line right after `self.pending = ...`). Replace `finish()`:

```python
def finish(self) -> list[BaseEvent]:
    events = self._close_text()
    if not self._final_state:
        # The upstream stream ended without completing, failing, or asking
        # for input — surface it, don't launder it (spec: Hardening #1).
        self.truncated = True
        events.append(
            RunErrorEvent(message="upstream stream ended without a terminal state")
        )
        return events
    if self._final_state == "failed":
        events.append(RunErrorEvent(message=self._final_text or "task failed"))
        return events
    if self._final_state == "canceled":
        events.append(CustomEvent(name="canceled", value={"text": self._final_text}))
    events.append(RunFinishedEvent(thread_id=self.thread_id, run_id=self.run_id))
    return events
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q`
Expected: all PASS (the agui wire tests still pass — every rig scenario ends in a terminal state).

- [ ] **Step 5: Commit**

```bash
git add -A a2a-orchestrator
git commit -m "Truncated upstream streams close as RUN_ERROR; translator captures the call id"
```

---

### Task incoming-turn-verification-fields: the resume names what it answers

**Files:**
- Modify: `a2a-orchestrator/src/a2a_orchestrator/translate.py`
- Test: `a2a-orchestrator/tests/test_translate.py`

**Interfaces:**
- Consumes: `Turn`, `incoming_turn`, `PERMISSION_TOOL`.
- Produces: `Turn` gains `tool_call_id: str = ""` and `request_id: str = ""`. For a resume, `tool_call_id` is the answered call's id and `request_id` is the `request_id` from that call's args when the history carries the assistant tool call (CopilotKit always sends full history; the folded/re-armed path depends on this). `Conversations.run_turn` (next task) verifies with `turn.request_id or turn.tool_call_id`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_translate.py`:

```python
def test_resume_reads_request_id_from_the_answered_call():
    turn = incoming_turn(
        run_input(
            [
                {"id": "m1", "role": "user", "content": "please run the tests"},
                {
                    "id": "m2",
                    "role": "assistant",
                    "toolCalls": [
                        {
                            "id": "call-9",
                            "type": "function",
                            "function": {
                                "name": "request_permission",
                                "arguments": '{"request_id": "req-7", "tool": "Bash"}',
                            },
                        }
                    ],
                },
                {
                    "id": "m3",
                    "role": "tool",
                    "toolCallId": "call-9",
                    "content": '{"decision": "allow"}',
                },
            ]
        )
    )
    assert turn == Turn(
        kind="resume", text="allow", tool_call_id="call-9", request_id="req-7"
    )


def test_resume_without_history_still_carries_the_tool_call_id():
    turn = incoming_turn(
        run_input(
            [{"id": "m1", "role": "tool", "toolCallId": "req-1", "content": "deny"}]
        )
    )
    assert turn == Turn(kind="resume", text="deny", tool_call_id="req-1")
```

Update the two existing resume expectations, which now carry the id: in `test_trailing_tool_result_is_a_resume` assert `Turn(kind="resume", text="allow", tool_call_id="req-1")`; in `test_bare_string_decision_also_works` assert `Turn(kind="resume", text="deny", tool_call_id="req-1")`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_translate.py -q`
Expected: FAIL — `Turn.__init__() got an unexpected keyword argument 'tool_call_id'`.

- [ ] **Step 3: Implement**

In `translate.py`:

```python
@dataclass
class Turn:
    """What a RunAgentInput asks of the upstream: say this, or answer that."""

    kind: Literal["message", "resume"]
    text: str
    tool_call_id: str = ""
    request_id: str = ""


def incoming_turn(run_input: RunAgentInput) -> Turn:
    if not run_input.messages:
        raise ValueError("run carried no messages")
    last = run_input.messages[-1]
    if isinstance(last, ToolMessage):
        return Turn(
            kind="resume",
            text=_decision(last.content),
            tool_call_id=last.tool_call_id,
            request_id=_request_id(run_input.messages, last.tool_call_id),
        )
    if isinstance(last, UserMessage) and isinstance(last.content, str) and last.content:
        return Turn(kind="message", text=last.content)
    raise ValueError(f"cannot act on a trailing {type(last).__name__}")


def _request_id(messages, tool_call_id: str) -> str:
    """The request_id inside the answered call's args, or '' if unfindable."""
    for message in reversed(messages):
        for call in getattr(message, "tool_calls", None) or []:
            if call.id == tool_call_id and call.function.name == PERMISSION_TOOL:
                try:
                    args = json.loads(call.function.arguments)
                except json.JSONDecodeError:
                    return ""
                return str(args.get("request_id") or "")
    return ""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add -A a2a-orchestrator
git commit -m "A resume turn names the call and request it answers"
```

---

### Task conversations-on-the-store: pending state lands on disk, clients evict, resumes verify

**Files:**
- Modify: `a2a-orchestrator/src/a2a_orchestrator/a2a_client.py`
- Modify: `a2a-orchestrator/src/a2a_orchestrator/app.py:33` (construct `Conversations(http, app.state.store)`)
- Modify: `a2a-orchestrator/src/a2a_orchestrator/agui.py` (the pending block passes call id + payload)
- Test: `a2a-orchestrator/tests/test_a2a_client.py`

**Interfaces:**
- Consumes: `Store.set_pending/clear_pending/pending_of`, `Pending`, `Turn.request_id/tool_call_id`, `RunTranslator.call_id/.pending`.
- Produces: `Conversations(http, store)`; `Conversations.set_pending(context_id, task_id, call_id, payload)`; `clear_pending(context_id)` (also evicts the cached client); `pending_of(context_id) -> Pending | None` (replaces `pending_task`); `run_turn` raises `LookupError` when nothing is pending and `ValueError` when the answer names the wrong call.

- [ ] **Step 1: Write the failing tests**

Rewrite `tests/test_a2a_client.py`'s fixtures/tests to construct `Conversations(http, store)`. Add near the top:

```python
from a2a_orchestrator.store import Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "orch.db")
```

Change every `Conversations(http)` to `Conversations(http, store)` (add `store` to each test's parameters). Update the pending round-trip test and add the new behaviors:

```python
async def test_resume_targets_the_pending_task(billing_chat, http, store):
    conversations = Conversations(http, store)
    pending = await drain(
        conversations.run_turn(
            billing_chat, Turn(kind="message", text="please run the tests")
        )
    )
    task_id = next(e.task.id for e in pending if e.WhichOneof("payload") == "task")
    conversations.set_pending(billing_chat.context_id, task_id, "req-1", "{}")
    assert conversations.pending_of(billing_chat.context_id).task_id == task_id

    resumed = await drain(
        conversations.run_turn(
            billing_chat, Turn(kind="resume", text="allow", tool_call_id="req-1")
        )
    )
    resumed_task_ids = {
        e.status_update.task_id
        for e in resumed
        if e.WhichOneof("payload") == "status_update"
    }
    assert resumed_task_ids == {task_id}
    conversations.clear_pending(billing_chat.context_id)
    assert conversations.pending_of(billing_chat.context_id) is None


async def test_resume_answering_the_wrong_call_refuses(billing_chat, http, store):
    conversations = Conversations(http, store)
    conversations.set_pending(billing_chat.context_id, "t1", "req-1", "{}")
    with pytest.raises(ValueError, match="pending"):
        await drain(
            conversations.run_turn(
                billing_chat, Turn(kind="resume", text="allow", tool_call_id="req-x")
            )
        )
    assert conversations.pending_of(billing_chat.context_id) is not None


async def test_request_id_outranks_the_tool_call_id(billing_chat, http, store):
    """A re-armed card carries a fresh toolCallId; the request_id still matches."""
    conversations = Conversations(http, store)
    pending = await drain(
        conversations.run_turn(
            billing_chat, Turn(kind="message", text="please run the tests")
        )
    )
    task_id = next(e.task.id for e in pending if e.WhichOneof("payload") == "task")
    conversations.set_pending(billing_chat.context_id, task_id, "req-1", "{}")
    resumed = await drain(
        conversations.run_turn(
            billing_chat,
            Turn(kind="resume", text="allow",
                 tool_call_id="freshly-minted", request_id="req-1"),
        )
    )
    assert resumed


async def test_clear_pending_evicts_the_cached_client(billing_chat, http, store):
    conversations = Conversations(http, store)
    await drain(
        conversations.run_turn(billing_chat, Turn(kind="message", text="hello"))
    )
    assert billing_chat.context_id in conversations._clients
    conversations.clear_pending(billing_chat.context_id)
    assert billing_chat.context_id not in conversations._clients
```

Also update `test_resume_with_nothing_pending_refuses` to pass `store` and expect the same `LookupError`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_a2a_client.py -q`
Expected: FAIL — `Conversations.__init__` takes 2 arguments.

- [ ] **Step 3: Implement**

Replace the pending half of `Conversations` (imports gain `from a2a_orchestrator.store import Pending, Store`):

```python
class Conversations:
    def __init__(self, http: httpx.AsyncClient, store: Store):
        self._http = http
        self._store = store
        self._clients: dict[str, object] = {}

    def set_pending(
        self, context_id: str, task_id: str, call_id: str, payload: str
    ) -> None:
        self._store.set_pending(context_id, task_id, call_id, payload)

    def clear_pending(self, context_id: str) -> None:
        self._store.clear_pending(context_id)
        # A wedged connection must not outlive the exchange that wedged it.
        self._clients.pop(context_id, None)

    def pending_of(self, context_id: str) -> Pending | None:
        return self._store.pending_of(context_id)
```

and the top of `run_turn`:

```python
async def run_turn(
    self, chat: ChatLike, turn: Turn
) -> AsyncIterator[StreamResponse]:
    task_id = ""
    if turn.kind == "resume":
        pending = self._store.pending_of(chat.context_id)
        if pending is None:
            raise LookupError(f"no pending task for context {chat.context_id!r}")
        claimed = turn.request_id or turn.tool_call_id
        if claimed != pending.call_id:
            raise ValueError(
                f"resume answers {claimed!r} but the pending approval is "
                f"{pending.call_id!r}"
            )
        task_id = pending.task_id
    ...
```

Update the module docstring: the in-memory deferral note is dead — pending state is now store-owned ("In-memory by design" paragraph goes away; say the store owns pending state and a restart finds it there).

In `app.py`, the lifespan constructs `Conversations(http, app.state.store)`. In `agui.py`, the post-run block becomes (add `import json` if missing):

```python
if translator.pending and translator.task_id:
    conversations.set_pending(
        chat.context_id,
        translator.task_id,
        translator.call_id,
        json.dumps(translator.pending),
    )
elif turn.kind == "resume":
    conversations.clear_pending(chat.context_id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q`
Expected: all PASS (agui wire tests exercise the store-backed path end to end).

- [ ] **Step 5: Commit**

```bash
git add -A a2a-orchestrator
git commit -m "Pending state lives in the store; resumes verify; clearing evicts the client"
```

---

### Task log-the-run-seam: both directions of /agui/run land in the events table

**Files:**
- Modify: `a2a-orchestrator/src/a2a_orchestrator/agui.py`
- Modify: `a2a-orchestrator/tests/conftest.py` (expose the service's db path; extract the spawn helper for the later restart task)
- Test: `a2a-orchestrator/tests/test_agui.py`

**Interfaces:**
- Consumes: `Store.append_event`, `RunTranslator.truncated`.
- Produces: every event `/agui/run` sends is an `out` row (`model_dump_json(by_alias=True, exclude_none=True)`); the incoming tail is an `in` row. Conftest gains `service_workdir` (session), `service_db` (the SQLite path), and module-level `spawn_service(workdir, rig_url, port)`; the restart task builds on `spawn_service`.

- [ ] **Step 1: Restructure conftest**

Replace the `service_url` fixture with a helper plus two fixtures (keep `SERVICE_STARTUP_TIMEOUT_S`):

```python
def spawn_service(workdir: Path, rig_url: str, port: int):
    """Boot orch-serve as a subprocess; returns (proc, url) once it answers."""
    catalog = workdir / "catalog.yaml"
    catalog.write_text(f"provider: index\nurl: {rig_url}\n")
    url = f"http://127.0.0.1:{port}/"
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "a2a_orchestrator.serve",
            "--host", "127.0.0.1",
            "--port", str(port),
            "--db", str(workdir / "orchestrator.db"),
            "--catalog", str(catalog),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    deadline = time.monotonic() + SERVICE_STARTUP_TIMEOUT_S
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"orch-serve exited early:\n{proc.stdout.read()}")
        try:
            if httpx.get(f"{url}api/missions", timeout=2.0).status_code == 200:
                return proc, url
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    proc.terminate()
    raise TimeoutError("orch-serve did not come up in time")


@pytest.fixture(scope="session")
def service_workdir(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("orchestrator")


@pytest.fixture(scope="session")
def service_url(rig_url, service_workdir) -> str:
    """orch-serve as a real subprocess, cataloged against the session rig."""
    proc, url = spawn_service(service_workdir, rig_url, free_port())
    yield url
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture
def service_db(service_workdir) -> Path:
    return service_workdir / "orchestrator.db"
```

Run: `uv run pytest -q` — still all green before touching agui.

- [ ] **Step 2: Write the failing wire test**

Append to `tests/test_agui.py` (add `import sqlite3` at the top):

```python
async def test_seam_traffic_lands_in_the_event_log(
    mission, open_chat, http, service_url, service_db
):
    chat = await open_chat(mission["id"], "billing-api")
    await run(
        http, service_url, chat["context_id"], user_says("hello from the cockpit")
    )
    rows = sqlite3.connect(service_db).execute(
        "SELECT direction, payload FROM events WHERE context_id = ? ORDER BY seq",
        (chat["context_id"],),
    ).fetchall()
    incoming = [json.loads(p) for d, p in rows if d == "in"]
    assert [m["content"] for m in incoming] == ["hello from the cockpit"]
    out_types = [json.loads(p)["type"] for d, p in rows if d == "out"]
    assert out_types[0] == "RUN_STARTED"
    assert out_types[-1] == "RUN_FINISHED"
    assert "TEXT_MESSAGE_CONTENT" in out_types


async def test_mismatched_resume_refuses_and_keeps_pending(
    mission, open_chat, http, service_url
):
    chat = await open_chat(mission["id"], "billing-api")
    pending = await run(
        http, service_url, chat["context_id"], user_says("please run the tests")
    )
    call_id = next(e["toolCallId"] for e in pending if e["type"] == "TOOL_CALL_START")

    wrong = await run(
        http,
        service_url,
        chat["context_id"],
        [
            {
                "id": uuid.uuid4().hex,
                "role": "tool",
                "toolCallId": "not-" + call_id,
                "content": json.dumps({"decision": "allow"}),
            }
        ],
    )
    assert types_of(wrong)[-1] == "RUN_ERROR"

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
```

Run: `uv run pytest tests/test_agui.py -q` — the log test FAILS (`no such table: events` is acceptable at this point only if the service db predates task 2; expected failure is an empty rows list); the mismatch test should already PASS (verification landed last task) — it lives here as the wire-level pin.

- [ ] **Step 3: Implement logging in agui.py**

Replace `run_agent`'s `stream()` so every outbound event routes through one `emit` helper and the inbound tail is logged after the chat resolves (spec: "even a turn that fails upstream shows the user's side of it"). Full new body:

```python
async def run_agent(request: Request) -> StreamingResponse | JSONResponse:
    try:
        run_input = RunAgentInput.model_validate(await request.json())
    except Exception as exc:
        return JSONResponse({"error": f"not a RunAgentInput: {exc}"}, status_code=422)

    store = request.app.state.store
    conversations = request.app.state.conversations
    encoder = EventEncoder()

    async def stream():
        chat = store.chat_for_context(run_input.thread_id)

        def emit(event) -> str:
            # A write failure is a real failure: it raises, and the except
            # arm below turns it into RUN_ERROR — a log with holes is worse
            # than a loud stop.
            if chat is not None:
                store.append_event(
                    chat.context_id,
                    "out",
                    event.model_dump_json(by_alias=True, exclude_none=True),
                )
            return encoder.encode(event)

        yield emit(
            RunStartedEvent(thread_id=run_input.thread_id, run_id=run_input.run_id)
        )
        translator = RunTranslator(run_input.thread_id, run_input.run_id)
        try:
            if chat is None:
                yield encoder.encode(
                    RunErrorEvent(
                        message=f"no chat bound for thread {run_input.thread_id!r}"
                    )
                )
                return
            if run_input.messages:
                store.append_event(
                    chat.context_id,
                    "in",
                    run_input.messages[-1].model_dump_json(
                        by_alias=True, exclude_none=True
                    ),
                )
            turn = incoming_turn(run_input)
            async for event in conversations.run_turn(chat, turn):
                for out in translator.feed(event):
                    yield emit(out)
            for out in translator.finish():
                yield emit(out)
        except Exception as exc:  # every failure must reach the stream as RUN_ERROR
            logger.exception(
                "run %s on thread %s failed", run_input.run_id, run_input.thread_id
            )
            for out in translator.abort():
                yield emit(out)
            yield emit(RunErrorEvent(message=str(exc)))
            return
        if translator.truncated:
            # The upstream never reached a terminal state; whatever was
            # pending before this turn is not this turn's to decide.
            return
        # The pending state is replaced or consumed, never incidentally
        # dropped — a fresh message while an approval waits leaves the card
        # answerable.
        if translator.pending and translator.task_id:
            conversations.set_pending(
                chat.context_id,
                translator.task_id,
                translator.call_id,
                json.dumps(translator.pending),
            )
        elif turn.kind == "resume":
            conversations.clear_pending(chat.context_id)

    return StreamingResponse(stream(), media_type=encoder.get_content_type())
```

(Module gains `import json`. The unbound-thread `RUN_ERROR` deliberately bypasses the log — there is no chat row for it to belong to.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add -A a2a-orchestrator
git commit -m "Every /agui/run turn lands in the event log, both directions"
```

---

### Task fold-messages: events in, AG-UI messages out

**Files:**
- Modify: `a2a-orchestrator/src/a2a_orchestrator/translate.py`
- Test: `a2a-orchestrator/tests/test_translate.py`

**Interfaces:**
- Consumes: `Store.events_for_context` row shape `(seq, direction, payload)`.
- Produces: `fold_messages(rows: list[tuple[int, str, str]]) -> list[Message]` — AG-UI message objects with stable ids: text ids are the translator's `messageId`s; tool-call wrappers are `call-<toolCallId>`; error markers are `error-<seq>`. The connect endpoint (next task) passes the result straight into `MessagesSnapshotEvent`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_translate.py` (imports gain `from ag_ui.core import RunErrorEvent, RunFinishedEvent, RunStartedEvent, TextMessageContentEvent, TextMessageEndEvent, TextMessageStartEvent, ToolCallArgsEvent, ToolCallEndEvent, ToolCallStartEvent` and `from a2a_orchestrator.translate import fold_messages`):

```python
def out_row(seq, event):
    return (seq, "out", event.model_dump_json(by_alias=True, exclude_none=True))


def in_row(seq, message: dict):
    return (seq, "in", json.dumps(message))


def test_fold_concatenates_text_deltas():
    rows = [
        out_row(1, RunStartedEvent(thread_id="th1", run_id="r1")),
        in_row(2, {"id": "u1", "role": "user", "content": "hello"}),
        out_row(3, TextMessageStartEvent(message_id="m1")),
        out_row(4, TextMessageContentEvent(message_id="m1", delta="Ready ")),
        out_row(5, TextMessageContentEvent(message_id="m1", delta="when you are")),
        out_row(6, TextMessageEndEvent(message_id="m1")),
        out_row(7, RunFinishedEvent(thread_id="th1", run_id="r1")),
    ]
    messages = fold_messages(rows)
    assert [(m.role, m.id) for m in messages] == [("user", "u1"), ("assistant", "m1")]
    assert messages[0].content == "hello"
    assert messages[1].content == "Ready when you are"


def test_fold_pairs_tool_calls_with_their_results():
    permission = {"tool": "Bash", "request_id": "req-1", "input": {}}
    rows = [
        in_row(1, {"id": "u1", "role": "user", "content": "please run the tests"}),
        out_row(2, ToolCallStartEvent(tool_call_id="req-1",
                                      tool_call_name="request_permission")),
        out_row(3, ToolCallArgsEvent(tool_call_id="req-1",
                                     delta=json.dumps(permission))),
        out_row(4, ToolCallEndEvent(tool_call_id="req-1")),
        in_row(5, {"id": "t1", "role": "tool", "toolCallId": "req-1",
                   "content": '{"decision": "allow"}'}),
    ]
    messages = fold_messages(rows)
    assert [m.role for m in messages] == ["user", "assistant", "tool"]
    call = messages[1].tool_calls[0]
    assert messages[1].id == "call-req-1"
    assert call.id == "req-1"
    assert call.function.name == "request_permission"
    assert json.loads(call.function.arguments) == permission


def test_fold_marks_failed_runs():
    rows = [
        in_row(1, {"id": "u1", "role": "user", "content": "status check please"}),
        out_row(2, RunErrorEvent(message="terraform provider exploded")),
    ]
    messages = fold_messages(rows)
    assert messages[-1].id == "error-2"
    assert messages[-1].role == "assistant"
    assert "terraform provider exploded" in messages[-1].content


def test_fold_of_nothing_is_empty():
    assert fold_messages([]) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_translate.py -q`
Expected: FAIL — `cannot import name 'fold_messages'`.

- [ ] **Step 3: Implement**

In `translate.py` (imports gain `AssistantMessage`, `FunctionCall`, `Message`, `ToolCall` from `ag_ui.core` and `TypeAdapter` from `pydantic`):

```python
_MESSAGE_ADAPTER: TypeAdapter[Message] = TypeAdapter(Message)


def fold_messages(rows: list[tuple[int, str, str]]) -> list[Message]:
    """The reading of the event log: replayable AG-UI messages, stable ids.

    'in' rows pass through as the messages they already are; 'out' rows
    re-assemble what streamed (deltas concatenate, tool calls pair with
    their args, a RUN_ERROR becomes a visible assistant marker). Lifecycle
    events shape the fold but produce no messages.
    """
    messages: list[Message] = []
    open_text: dict[str, AssistantMessage] = {}
    open_calls: dict[str, ToolCall] = {}
    for seq, direction, payload in rows:
        if direction == "in":
            messages.append(_MESSAGE_ADAPTER.validate_json(payload))
            continue
        event = json.loads(payload)
        kind = event.get("type")
        if kind == "TEXT_MESSAGE_START":
            text = AssistantMessage(
                id=event["messageId"], role="assistant", content=""
            )
            open_text[event["messageId"]] = text
            messages.append(text)
        elif kind == "TEXT_MESSAGE_CONTENT":
            text = open_text.get(event["messageId"])
            if text is not None:
                text.content = (text.content or "") + event["delta"]
        elif kind == "TEXT_MESSAGE_END":
            open_text.pop(event["messageId"], None)
        elif kind == "TOOL_CALL_START":
            call = ToolCall(
                id=event["toolCallId"],
                type="function",
                function=FunctionCall(name=event["toolCallName"], arguments=""),
            )
            open_calls[event["toolCallId"]] = call
            messages.append(
                AssistantMessage(
                    id=f"call-{event['toolCallId']}",
                    role="assistant",
                    tool_calls=[call],
                )
            )
        elif kind == "TOOL_CALL_ARGS":
            call = open_calls.get(event["toolCallId"])
            if call is not None:
                call.function.arguments += event["delta"]
        elif kind == "RUN_ERROR":
            messages.append(
                AssistantMessage(
                    id=f"error-{seq}",
                    role="assistant",
                    content=f"run failed: {event.get('message', '')}",
                )
            )
    return messages
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add -A a2a-orchestrator
git commit -m "fold_messages reads the event log back as AG-UI messages"
```

---

### Task connect-endpoint: /agui/connect answers the handshake

**Files:**
- Modify: `a2a-orchestrator/src/a2a_orchestrator/agui.py`
- Modify: `a2a-orchestrator/src/a2a_orchestrator/app.py:42` (add the route)
- Test: `a2a-orchestrator/tests/test_agui.py`

**Interfaces:**
- Consumes: `fold_messages`, `Store.events_for_context`.
- Produces: `POST /agui/connect` (RunAgentInput body, threadId-routed) streaming `RUN_STARTED → MESSAGES_SNAPSHOT → RUN_FINISHED`; unknown thread → `RUN_STARTED → RUN_ERROR`; empty chat → empty snapshot. The frontend's `ReplayHttpAgent` posts here. Test helper `connect(http, service_url, thread_id)` in `test_agui.py`, reused by the restart task.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_agui.py`:

```python
async def connect(http, service_url, thread_id):
    payload = {
        "threadId": thread_id,
        "runId": uuid.uuid4().hex,
        "state": None,
        "messages": [],
        "tools": [],
        "context": [],
        "forwardedProps": None,
    }
    response = await http.post(f"{service_url}agui/connect", json=payload)
    assert response.status_code == 200, response.text
    return events_of(response.text)


async def test_connect_replays_the_conversation(mission, open_chat, http, service_url):
    chat = await open_chat(mission["id"], "billing-api")
    await run(
        http, service_url, chat["context_id"], user_says("hello from the cockpit")
    )
    await run(http, service_url, chat["context_id"], user_says("please run the tests"))

    events = await connect(http, service_url, chat["context_id"])
    assert types_of(events) == ["RUN_STARTED", "MESSAGES_SNAPSHOT", "RUN_FINISHED"]
    messages = events[1]["messages"]
    users = [m["content"] for m in messages if m["role"] == "user"]
    assert users == ["hello from the cockpit", "please run the tests"]
    assert any(
        "Ready when you are" in (m.get("content") or "")
        for m in messages
        if m["role"] == "assistant"
    )
    calls = [c for m in messages for c in (m.get("toolCalls") or [])]
    assert [c["function"]["name"] for c in calls] == ["request_permission"]


async def test_connect_on_an_unknown_thread_is_a_run_error(http, service_url):
    events = await connect(http, service_url, "deadbeef")
    assert types_of(events) == ["RUN_STARTED", "RUN_ERROR"]


async def test_connect_on_a_fresh_chat_is_an_empty_snapshot(
    mission, open_chat, http, service_url
):
    chat = await open_chat(mission["id"], "billing-api")
    events = await connect(http, service_url, chat["context_id"])
    assert types_of(events) == ["RUN_STARTED", "MESSAGES_SNAPSHOT", "RUN_FINISHED"]
    assert events[1]["messages"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_agui.py -q`
Expected: FAIL — 404 on `/agui/connect`.

- [ ] **Step 3: Implement**

In `agui.py` (imports gain `MessagesSnapshotEvent`, `RunFinishedEvent` from `ag_ui.core` and `fold_messages` from `a2a_orchestrator.translate`):

```python
async def connect_agent(request: Request) -> StreamingResponse | JSONResponse:
    """Replay: answer CopilotKit's mount-time connect with the folded log.

    Derived data only — this endpoint never writes to the event log.
    """
    try:
        run_input = RunAgentInput.model_validate(await request.json())
    except Exception as exc:
        return JSONResponse({"error": f"not a RunAgentInput: {exc}"}, status_code=422)

    store = request.app.state.store
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
        messages = fold_messages(store.events_for_context(chat.context_id))
        yield encoder.encode(MessagesSnapshotEvent(messages=messages))
        yield encoder.encode(
            RunFinishedEvent(thread_id=run_input.thread_id, run_id=run_input.run_id)
        )

    return StreamingResponse(stream(), media_type=encoder.get_content_type())
```

In `app.py` routes, after the `/agui/run` line:

```python
Route("/agui/connect", agui.connect_agent, methods=["POST"]),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add -A a2a-orchestrator
git commit -m "/agui/connect replays the folded event log as a snapshot"
```

---

### Task restart-survival: the whole point, pinned end to end

**Files:**
- Modify: `a2a-orchestrator/tests/conftest.py`
- Create: `a2a-orchestrator/tests/test_restart.py`

**Interfaces:**
- Consumes: `spawn_service` (conftest), `run`/`connect`/`user_says`/`types_of` (test_agui).
- Produces: `restartable_service` fixture — an object with `.url` and `.restart()` that reboots orch-serve on the same db file (fresh port each boot).

- [ ] **Step 1: Add the fixture**

In `conftest.py` (imports gain `from dataclasses import dataclass, field` — or plain class; keep it simple):

```python
class RestartableService:
    def __init__(self, workdir: Path, rig_url: str):
        self.workdir = workdir
        self.rig_url = rig_url
        self.proc, self.url = spawn_service(workdir, rig_url, free_port())

    def restart(self) -> None:
        self.stop()
        self.proc, self.url = spawn_service(self.workdir, self.rig_url, free_port())

    def stop(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()


@pytest.fixture
def restartable_service(rig_url, tmp_path) -> RestartableService:
    service = RestartableService(tmp_path, rig_url)
    yield service
    service.stop()
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_restart.py`:

```python
"""A service restart loses nothing: history replays, the approval resumes."""

from __future__ import annotations

import json
import uuid

from test_agui import connect, run, types_of, user_says


async def open_chat_at(http, url, agent="billing-api"):
    mission = (await http.post(f"{url}api/missions", json={})).json()
    response = await http.post(
        f"{url}api/missions/{mission['id']}/chats", json={"agent": agent}
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_history_and_pending_survive_a_restart(restartable_service, http):
    chat = await open_chat_at(http, restartable_service.url)
    pending = await run(
        http, restartable_service.url, chat["context_id"],
        user_says("please run the tests"),
    )
    call_id = next(
        e["toolCallId"] for e in pending if e["type"] == "TOOL_CALL_START"
    )

    restartable_service.restart()

    replay = await connect(http, restartable_service.url, chat["context_id"])
    snapshot = next(e for e in replay if e["type"] == "MESSAGES_SNAPSHOT")
    calls = [
        c for m in snapshot["messages"] for c in (m.get("toolCalls") or [])
    ]
    assert [c["id"] for c in calls] == [call_id]

    resumed = await run(
        http,
        restartable_service.url,
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
```

- [ ] **Step 3: Run the test**

Run: `uv run pytest tests/test_restart.py -q`
Expected: PASS — every layer it leans on already landed. If it fails, that is a real integration bug in an earlier task; debug there, do not weaken this test.

- [ ] **Step 4: Run the whole suite and commit**

Run: `uv run pytest -q`
Expected: all PASS.

```bash
git add -A a2a-orchestrator
git commit -m "Pin it end to end: history and the pending approval survive a restart"
```

---

### Task replay-agent: the browser answers the handshake it was already making

**Files:**
- Create: `a2a-orchestrator/frontend/src/agent.ts`
- Modify: `a2a-orchestrator/frontend/src/ChatPane.tsx`

**Interfaces:**
- Consumes: `POST /agui/connect`; `HttpAgent` (public `run(input)`, public `url`/`headers`, `protected connect(input)` on `AbstractAgent` — all verified in `@ag-ui/client` 0.0.57's `index.d.ts`).
- Produces: `ReplayHttpAgent` — drop-in `HttpAgent` whose `connect()` posts the run input to `/agui/connect`. ChatPane constructs it instead of `HttpAgent`.

- [ ] **Step 1: Write the subclass**

Create `frontend/src/agent.ts`:

```ts
import { HttpAgent } from '@copilotkit/react-core/v2'
import type { BaseEvent, RunAgentInput } from '@copilotkit/react-core/v2'
import type { Observable } from 'rxjs'

// CopilotChat fires connectAgent() on every mount, expecting
// RUN_STARTED → MESSAGES_SNAPSHOT → RUN_FINISHED from the agent's
// connect(). Plain HttpAgent has no connect(), and the library swallows
// the miss silently — that silent no-op was the empty pane on reload.
// This answers it: same wire shape as run, aimed at /agui/connect.
export class ReplayHttpAgent extends HttpAgent {
  protected connect(input: RunAgentInput): Observable<BaseEvent> {
    const replay = new HttpAgent({ url: '/agui/connect', headers: this.headers })
    return replay.run(input)
  }
}
```

- [ ] **Step 2: Use it in ChatPane**

In `ChatPane.tsx`, add `import { ReplayHttpAgent } from './agent'`, and in the `agents` memo replace `new HttpAgent({ ... })` with `new ReplayHttpAgent({ url: '/agui/run', threadId: chat.context_id })`. The `HttpAgent` import stays only if still referenced (it isn't — drop it from the import list; `useHumanInTheLoop`, `CopilotChat`, `CopilotKitProvider` remain).

- [ ] **Step 3: Verify**

Run (from `a2a-orchestrator/frontend/`): `npm run build && npm run lint`
Expected: both clean. If `tsc` rejects the `protected connect` override signature, diff against `node_modules/@ag-ui/client/dist/index.d.ts` (`AbstractAgent.connect`, ~line 524) and match it exactly.

- [ ] **Step 4: Commit**

```bash
git add -A a2a-orchestrator/frontend
git commit -m "The browser's agent answers connect from /agui/connect"
```

---

### Task pending-endpoint: REST for "does this chat have a pending approval"

**Files:**
- Modify: `a2a-orchestrator/src/a2a_orchestrator/api.py`
- Modify: `a2a-orchestrator/src/a2a_orchestrator/app.py` (route)
- Modify: `a2a-orchestrator/frontend/src/api.ts` (typed fetch helper)
- Test: `a2a-orchestrator/tests/test_missions_api.py`

**Interfaces:**
- Consumes: `Store.pending_of`, `Store.chat_for_context`.
- Produces: `GET /api/chats/{context_id}/pending` → `{"pending": <permission payload object> | null}`, 404 with `{"error": ...}` for unknown chats. Frontend: `fetchPending(contextId: string): Promise<Record<string, unknown> | null>`. The re-arm task consumes both.

- [ ] **Step 1: Write the failing wire tests**

Append to `tests/test_missions_api.py` (check its imports; it uses the same `http`/`service_url`/`mission`/`open_chat` fixtures — add `import json`, `import uuid` if absent, and reuse `test_agui`'s turn helper by importing: `from test_agui import run, user_says`):

```python
async def test_pending_is_null_for_a_quiet_chat(mission, open_chat, http, service_url):
    chat = await open_chat(mission["id"], "billing-api")
    response = await http.get(f"{service_url}api/chats/{chat['context_id']}/pending")
    assert response.status_code == 200
    assert response.json() == {"pending": None}


async def test_pending_carries_the_permission_payload(
    mission, open_chat, http, service_url
):
    chat = await open_chat(mission["id"], "billing-api")
    await run(http, service_url, chat["context_id"], user_says("please run the tests"))
    response = await http.get(f"{service_url}api/chats/{chat['context_id']}/pending")
    payload = response.json()["pending"]
    assert payload["tool"] == "Bash"
    assert payload["request_id"]


async def test_pending_for_an_unknown_chat_is_404(http, service_url):
    response = await http.get(f"{service_url}api/chats/deadbeef/pending")
    assert response.status_code == 404
    assert "error" in response.json()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_missions_api.py -q`
Expected: FAIL — 404s where 200 is expected (route missing), and the unknown-chat test may accidentally pass; keep it anyway.

- [ ] **Step 3: Implement**

In `api.py` (imports gain `import json`):

```python
async def chat_pending(request: Request) -> JSONResponse:
    store = request.app.state.store
    chat = store.chat_for_context(request.path_params["context_id"])
    if chat is None:
        return JSONResponse({"error": "no such chat"}, status_code=404)
    pending = store.pending_of(chat.context_id)
    return JSONResponse(
        {"pending": json.loads(pending.payload) if pending else None}
    )
```

In `app.py` routes, with the other `/api` lines:

```python
Route("/api/chats/{context_id}/pending", api.chat_pending, methods=["GET"]),
```

In `frontend/src/api.ts`:

```ts
export async function fetchPending(
  contextId: string,
): Promise<Record<string, unknown> | null> {
  const data = await json<{ pending: Record<string, unknown> | null }>(
    await fetch(`/api/chats/${contextId}/pending`),
  )
  return data.pending
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q` and (from `frontend/`) `npm run build && npm run lint`
Expected: all clean.

- [ ] **Step 5: Commit**

```bash
git add -A a2a-orchestrator
git commit -m "REST exposes a chat's pending approval"
```

---

### Task rearm-on-mount: the reloaded approval card is answerable again

**Files:**
- Modify: `a2a-orchestrator/frontend/src/ChatPane.tsx`

**Interfaces:**
- Consumes: `fetchPending` (api.ts), `useCopilotKit` (`@copilotkit/react-core/v2`), `copilotkit.runTool({name, agentId, parameters, followUp})` (`@copilotkit/core` — `followUp: 'generate'` triggers the follow-up run that carries the tool result to the service), the `ReplayHttpAgent` instance's public `messages` array.
- Produces: on mount of a chat with a pending approval, a live `request_permission` card. Server-side nothing changes — the re-armed card's fresh toolCallId is reconciled by `request_id` (already implemented).

- [ ] **Step 1: Add the re-arm component**

In `ChatPane.tsx`, add imports `{ useEffect, useRef }` from `react`, `useCopilotKit` from `@copilotkit/react-core/v2`, `fetchPending` from `./api`, and the component:

```tsx
// Replay paints a pending approval's text, but HITL status only goes
// live inside a run — a reloaded card renders inert (verified against
// 1.67.1: status derives from live tool execution, respond() is a no-op
// outside one). runTool() is the one supported re-arm: it fires the tool
// fresh (new toolCallId; the service reconciles by request_id) and
// followUp:'generate' carries the answer upstream as a normal resume.
function PendingRearm({ contextId, agent }: { contextId: string; agent: ReplayHttpAgent }) {
  const { copilotkit } = useCopilotKit()
  const armed = useRef(false)
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      const pending = await fetchPending(contextId)
      if (!pending || cancelled || armed.current) return
      // Wait for the connect snapshot to land first: the snapshot merge
      // drops messages it doesn't know, so arming before it applies would
      // wipe the synthesized call. Pending implies history, so non-empty
      // messages means the snapshot arrived.
      for (let i = 0; i < 100 && agent.messages.length === 0 && !cancelled; i++) {
        await new Promise((resolve) => setTimeout(resolve, 100))
      }
      if (cancelled || armed.current) return
      armed.current = true
      await copilotkit.runTool({
        name: 'request_permission',
        agentId: contextId,
        parameters: pending,
        followUp: 'generate',
      })
    })()
    return () => {
      cancelled = true
    }
  }, [contextId, agent, copilotkit])
  return null
}
```

Render it inside the provider, next to `<PermissionTool />`:

```tsx
<CopilotKitProvider agents__unsafe_dev_only={agents}>
  <PermissionTool />
  <PendingRearm contextId={chat.context_id} agent={agents[chat.context_id]} />
  <CopilotChat ... />
</CopilotKitProvider>
```

(If `useCopilotKit`'s context value doesn't destructure as `{ copilotkit }`, check `CopilotKitContextValue` in `node_modules/@copilotkit/react-core/dist/v2/index.d.mts` and adjust — the core instance on the context is the thing with `.runTool`.)

- [ ] **Step 2: Verify build**

Run (from `frontend/`): `npm run build && npm run lint`
Expected: clean.

- [ ] **Step 3: Manual browser validation against the rig**

Boot the stack (two terminals from `a2a-orchestrator/`): `uv run orch-serve --catalog catalog.yaml --db /tmp/orch-dev.db` and `npm run dev` in `frontend/` (check `serve.py --help` / README for exact flags if these differ). In the browser:

1. Open a chat, send a free-text message, get the reply. Reload the page, reopen the chat — the conversation is there. ✅ replay
2. Send "please run the tests" so the approval card appears. Reload. The history shows AND the card comes back answerable. Click allow — the run resumes and finishes. ✅ re-arm
3. Restart orch-serve (same db) with the approval pending; reload; answer. Same result. ✅ restart
4. Record the pass as a GIF (`~/Downloads/agui_event_log_replay.gif`) per house habit.

Expected: all three behaviors work at rig pacing. Any failure here is a real bug — stop and debug (likely suspects: snapshot/rearm race, `useCopilotKit` shape).

- [ ] **Step 4: Commit**

```bash
git add -A a2a-orchestrator/frontend
git commit -m "Reload re-arms a pending approval card via runTool"
```

---

### Task devlog-and-backlog: close the loop

**Files:**
- Modify: `docs/DEVLOG.md`

**Interfaces:** none — bookkeeping.

- [ ] **Step 1: DEVLOG entry**

Append a dated section to `docs/DEVLOG.md` (2026-08-13 or the actual date) narrating: the event-log milestone shipped per the spec; the connect-handshake discovery (CopilotKit was already calling `connectAgent()` on mount and plain `HttpAgent` silently no-ops — the empty pane was a missing answer, not missing machinery); the `initialMessages` wipe-on-mount trap; the re-arm-via-`runTool` mechanism and why resumes verify by `request_id`; park→pending rename; test counts before/after; a pointer to the browser-validation GIF.

- [ ] **Step 2: Taskwarrior**

```bash
task fc4eb2d8 done   # persistence / event log
task dbcb5569 done   # hardening batch
```

(`13f576dc` rendering and `d798cf14` Playwright stay open — scoped out.)

- [ ] **Step 3: Commit**

```bash
git add docs/DEVLOG.md
git commit -m "DEVLOG: the event log ships — replay, pending survival, hardening"
```

Then hand off to superpowers:finishing-a-development-branch (merge to main, push, remove the worktree).
