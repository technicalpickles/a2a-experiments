# Upstream issues to file

Findings from this project that belong in someone else's repo, and what to say when filing
them. Nothing here has been reported yet.

**This doc is the "why", taskwarrior is the "when".** Each entry carries the UUID of its
taskwarrior task (project `a2a-experiments`, tag `a2a`) — that's the actionable backlog and
where status lives. What's here is the context a report needs: the trace, the repro, the
framing that will land, and the parts we're not sure about. Writing an issue shouldn't mean
re-deriving any of it a month later.

Two habits worth keeping:

- **Lead with the diff, not the philosophy.** Several of these could be argued as intended
  behavior. An issue that opens with "V1 did X, V2 does Y" starts a conversation about a
  regression; one that opens with "cancel *should* mean..." starts an argument about design.
- **Run the final text through the `writing-voice` skill.** These drafts are notes to
  ourselves; an issue body is outbound prose with my name on it.

**Versions in play:** a2acode v0.6.2, `a2a-sdk` 1.1.2 (a2acode pins `>=1.1,<2`),
`a2a-inspector` pinning `a2a-sdk` 0.3.10.

## Why these are worth filing at all

The rig is what makes them reportable. Every cancel finding below needs a turn that is in a
specific state at a specific moment — parked on an approval, or slow enough to interrupt.
Against live inference that's a flaky reproduction with an API bill attached. Against
`playback` it's a scenario file: deterministic, free, no API key, ~200ms of wall clock,
identical on every run.

So each issue can ship with a repro that a maintainer can actually run. That's the difference
between "we think cancel is broken" and a failing test.

---

## a2a-sdk (`a2aproject/a2a-python`)

### Cancel of a task whose producer already returned succeeds silently

**Task:** `79297b49` · **Repro:** `a2a-rig/tests/test_lifecycle.py::test_cancel_a_parked_task`
(strict xfail)

`DefaultRequestHandlerV2.on_cancel_task` dropped the guard V1 had:

```python
if result.status.state != TASK_STATE_CANCELED:
    raise TaskNotCancelableError
```

Underneath, `ActiveTask.cancel` only acts `if not self._is_finished.is_set() and
self._producer_task` — it models "cancelable" as *has a running producer* rather than *is not
terminal*. So cancelling a task parked in `input-required` returns a successful response with
the task still `input_required`, in both the response and a later `tasks/get`.

The protocol side seems clear: `input-required` is an interrupted, non-terminal state, and
`TaskNotCancelableError` exists precisely to distinguish terminal ones. With V1's guard the
mismatch at least surfaced as an error; without it, it's a silent success.

**Why it matters:** any UI offering "cancel" on an approval prompt is lying to the user today.

**How to lead:** the V1/V2 guard diff. That's a concrete regression with a git blame behind it.
Save the "what should cancelable mean" discussion for after they've agreed something changed.

**Open question — this may flip to a2acode.** If the maintainers consider "the producer
returned" to be the intended definition of finished, then the bug is a2acode parking by
returning from `execute()` (see below), and this issue becomes a docs/naming problem. Worth
asking directly rather than assuming.

### A mid-run cancel strands the task in `working`, permanently

**Task:** `167506a4` · **Repro:**
`a2a-rig/tests/test_playback.py::test_a_cancel_lands_while_the_run_is_still_going` (strict
xfail), plus `test_a_cancelled_run_is_stranded_in_working` documenting today's behavior

Worse than the one above, and found while trying to confirm it *didn't* apply here. A task
genuinely mid-run satisfies the `_producer_task` guard, so the natural expectation is that
cancel works. It doesn't:

```
STREAM states:        ['working', 'working']    # stream just stops
cancel_task returned: working
later get_task:       working                   # forever
```

`ActiveTask.cancel` (`active_task.py`, ~L733) cancels `self._producer_task` **first**, then
awaits `self._agent_executor.cancel(...)`. But the producer is the task running `execute` —
the only component that writes the task's terminal state. Killing it first means:

1. The executor unwinds through its `CancelledError` path, which emits no status.
2. The `updater.cancel()` inside the executor's own `cancel()` does enqueue a canceled status,
   but by then it doesn't reach the task store.
3. `ActiveTask.cancel` returns the task it read *before* cancelling — still `working`.

So the caller gets `working` back, and the task never reaches a terminal state at all. A
client that cancels has no way to know it's done polling.

**Fix shape:** await the executor's cancel *before* killing the producer, so the component
that owns terminal state gets a chance to write one.

**How to lead:** the three-line observed output above. It's unambiguous and needs no argument
about intent — a task with no terminal state is broken under anyone's definition.

**File after `79297b49`, or together.** They're the same root cause seen from two states, and
filing the stranding one first risks it being triaged as a duplicate of a bug nobody's read yet.

---

## a2acode (`kanywst/a2acode`)

### `_pump`'s CancelledError branch emits no terminal state

**Task:** `5dcde5fb` · **Pairs with:** `167506a4`

`executor.py`'s `_pump` handles `asyncio.CancelledError` by dropping the session and
re-raising, deliberately without emitting a status. That branch was written for a
*disconnected client*, where there is genuinely nobody left to tell — a reasonable call. But a
deliberate cancel arrives through the same branch, and there the caller is very much still
listening.

**Suggestion:** distinguish "client vanished" from "cancelled on purpose" and close the task
out in the second case. That would make a2acode robust regardless of what the SDK does about
the ordering, which is the more useful place to fix it if the SDK conversation stalls.

**Framing note:** this is upstream's code doing something defensible in the case it was
written for. Present it as a case they hadn't hit, not as a mistake.

### A task parked in `input-required` cannot be cancelled

**Task:** `34c83f8c`

a2acode parks by *returning* from `execute()` (the "Paused on a permission request; keep the
stream for the follow-up" path), keeping the `BackendSession` alive out of band. That's
deliberate and is what lets one permission round trip span two `execute()` calls — but it
means the producer looks finished to the SDK, which is what walks into `79297b49`.

Also worth mentioning in the same issue: **cancel is only tested at the `BackendSession`
level, never end to end over the protocol.** That's why this survived 163 tests. Offering that
observation alongside the bug is more useful than the bug alone.

### The claude backend can no longer emit a `plan` event at all

**Task:** `70dc7c04` · **Evidence:** `docs/captures/phase5-session-tools.json`,
`docs/captures/phase5-plan-probe.jsonl`

`backends/claude.py` derives the agent's plan from one tool name:

```python
_PLAN_TOOL = "TodoWrite"   # L60
```

That tool is not in the session any more. Claude Code 2.1.224, driven through the Claude Agent
SDK with a2acode's own options (`setting_sources=[]`, no `allowed_tools`), hands the session 29
tools, and the todo-list ones are `TaskCreate` / `TaskUpdate` / `TaskList` / `TaskGet`. No
`TodoWrite`. So `_plan_from_todos` never fires and `--backend claude` produces no `plan`
artifact under any prompt.

Watched live, not inferred: a three-step feature request against a Flask app ran to
`completed` over 25 turns with zero plan artifacts, and Claude said so in its own response
text — "No TodoWrite tool here" — before settling on `TaskCreate`/`TaskUpdate` instead. The
same prompt against `--backend acp --agent claude` produced three plan updates, so this is the
claude backend specifically, not the plan pipeline: `_render_plan`, the artifact replacement,
and the executor's handling are all fine.

**Why 163 tests didn't catch it:** `test_todowrite_yields_a_plan_alongside_the_tool_use` builds
a synthetic `ToolUseBlock(name="TodoWrite")` by hand. A unit test that supplies the constant it
is testing can't notice the constant went stale — the test will keep passing after the tool is
renamed again.

**Fix shape:** recognize the current task tools alongside `TodoWrite` (keep the old name for
older CLIs — this is a moving target, so a set beats a constant). The mapping is not
one-for-one: `TodoWrite` carries the whole list in one call, while `TaskCreate`/`TaskUpdate`
mutate one task at a time, so the backend would have to hold list state across calls to emit a
`Plan` by replacement the way the dataclass expects.

**Framing note:** lead with the diff — "v0.6.2 emitted plans against Claude Code 2.0.x; against
2.1.224 it emits none" — and offer the tool-list dump as the evidence. The interesting half of
the report is the testing gap, not the rename; a follow-up worth suggesting is an integration
check that asserts the session actually offers whatever tools the backend keys on, since that
class of break will happen again.

### Permission deny discards the caller's text

**Task:** `f010f63e` · Small; a nit rather than a bug

`executor.py` (~L502-506) builds the decision from the caller's message, but on a denial always
sends `"Denied by A2A caller"` — whatever the caller actually wrote is dropped. So there's no
way to deny *with guidance* ("not that command, try `pytest -x`"), which is the useful half of
a denial.

**Why we care specifically:** it caps how rich a scripted deny branch can plausibly be. A
scenario can't model "denied with a redirect" because the real producer can't express it.

**Fix shape:** pass the caller's text through as `PermissionDecision.message`, which already
exists and is already the obvious home for it.

### M4: offer `playback` and `--record` upstream

**Not a bug — the planned contribution.** DESIGN-v3 §7-8. `a2a_playback` is written to drop
into a2acode's `backends/` directory: it imports only the public backend vocabulary and holds
no rig-specific assumptions, so this should be a file move rather than an untangling.

Not ready to propose yet. Worth having the cancel conversations first anyway — they establish
that we've been using a2acode seriously, which is a better opening than a cold feature PR.

---

## a2a-cli (`ericabouaf/a2a-cli`)

### Migrate the client to `@a2a-js/sdk` 1.0.1

**Task:** `cc7feef9` · **Work is done, sitting in a fork**

Patched on `a2a-sdk-1.0-migration` in `~/github.com/technicalpickles/a2a-cli`, `npm link`ed
locally. Upstream's npm metadata has no `repository` field, which is why this went fork-first
rather than issue-first — there was no obvious repo to file against from the package alone.

Just needs the PR opened.

---

## a2a-inspector (`a2aproject/a2a-inspector`)

### Broken against current a2acode — probably not worth filing as-is

**No task.** See DEVLOG 2026-08-06.

Its `a2a-sdk` pin (0.3.10) predates the `supportedInterfaces` card shape that a2acode's 1.1.2
emits, and its client code is built on 1.1.2's protobuf-generated types internally. So this is
a real migration, not a patch.

Deferred and not blocking anything. Filing "your pin is old" without a fix attached is low
value — if it gets reported at all it should be after someone's actually done the migration.
Noted here so the decision not to file is a decision rather than an oversight.

---

## Filing order

1. **`79297b49` + `167506a4` to a2a-sdk, together.** Same root cause, two symptoms; the
   stranding one is the more clearly-broken of the pair and the parked one has the cleaner
   regression story. Ask about the "producer finished" definition in the same breath, since
   the answer decides whether `34c83f8c` is a real bug or expected.
2. **`5dcde5fb` and `34c83f8c` to a2acode**, once the SDK answer is in hand — and file
   `5dcde5fb` regardless of that answer, since it's worth fixing either way.
3. **`f010f63e` to a2acode** any time. Independent of everything else, small, easy yes.
4. **`cc7feef9` to a2a-cli** any time. The work already exists.

**`70dc7c04` jumps the queue.** It's the only one here that's a plain feature-is-broken report
rather than a design conversation: reproducible, evidenced by a tool-list dump, and provably
not the reporter's setup since the ACP backend does the same job fine. Nothing about it waits
on the SDK cancel answer. File it first, or alongside the a2a-sdk pair.
