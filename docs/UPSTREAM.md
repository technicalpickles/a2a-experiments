# Upstream issues to file

Findings from this project that belong in someone else's repo, and what to say when filing
them. Two filed so far: `70dc7c04` → [a2acode#37](https://github.com/kanywst/a2acode/issues/37),
and `79297b49`+`167506a4` → [a2a-python#1170](https://github.com/a2aproject/a2a-python/issues/1170)
with repro PR [#1171](https://github.com/a2aproject/a2a-python/pull/1171). The rest are drafts.

**Writing the issue is what verifies the note.** Both filings turned up claims here that were
wrong or incomplete, and one of them (the stale-read mechanism, below) would have been a public
correction if it had shipped. Re-derive from source before filing, always.

**This doc is the "why", taskwarrior is the "when".** Each entry carries the UUID of its
taskwarrior task (project `a2a-experiments`, tag `a2a`) — that's the actionable backlog and
where status lives. What's here is the context a report needs: the trace, the repro, the
framing that will land, and the parts we're not sure about. Writing an issue shouldn't mean
*rediscovering* any of it a month later — but it does mean re-deriving it, per the note above.
An entry here saves you the hunt, not the verification.

Three habits worth keeping:

- **Lead with the diff, not the philosophy.** Several of these could be argued as intended
  behavior. An issue that opens with "V1 did X, V2 does Y" starts a conversation about a
  regression; one that opens with "cancel *should* mean..." starts an argument about design.
- **Look for the test that supplies the thing it's testing.** Two for two so far, and it is the
  most reliable smell in this whole exercise. a2acode's plan test hand-builds
  `ToolUseBlock(name="TodoWrite")`, so it cannot notice the tool was renamed out from under it.
  a2a-python's cancel test hand-enqueues the `CANCELED` event, so it cannot notice the framework
  never writes one. Both bugs survived large green suites *because* of their tests, not despite
  them. When a finding seems too obvious to have gone unnoticed, go read the test that should
  have caught it — the answer is usually there, and it makes the report much better than the bug
  alone. It also reframes the issue from "you have a bug" to "you have a blind spot", which is
  the more useful thing to hand a maintainer.
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

> **FILED 2026-08-08 as ONE issue, not two:**
> [a2a-python#1170](https://github.com/a2aproject/a2a-python/issues/1170), with repro PR
> [#1171](https://github.com/a2aproject/a2a-python/pull/1171). Those links are the bodies of
> record — read them for the shape the next filing should copy.
>
> **That answers the "one issue or two?" question that rode six handoffs: one.** The argument
> for two was that the parked finding opens a design debate ("what should cancelable mean")
> while the stranding one doesn't, and bundling them buries the clean one. What dissolved that:
> V1 guarded *both* paths, so both are plain regressions under one headline, and the parked
> test asserts only "the state must not come back unchanged" — which sidesteps the design
> question entirely, since cancelling *or* raising both pass.

### Cancel of a task whose producer already returned succeeds silently

**Task:** `79297b49` (done) · **Repro:** `a2a-rig/tests/test_lifecycle.py::test_cancel_a_parked_task`
(strict xfail), and upstream as `test_scenario_19_cancel_of_parked_task_does_not_silently_succeed`

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

**Task:** `167506a4` (done) · **Repro:**
`a2a-rig/tests/test_playback.py::test_a_cancel_lands_while_the_run_is_still_going` (strict
xfail), plus `test_a_cancelled_run_is_stranded_in_working` documenting today's behavior; and
upstream as `test_scenario_19_mid_run_cancel_reaches_a_terminal_state`

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
3. ~~`ActiveTask.cancel` returns the task it read *before* cancelling — still `working`.~~
   **Wrong, corrected 2026-08-08 while writing the issue.** `cancel` re-reads via `get_task()`
   at `active_task.py:753`, after `await self._is_finished.wait()`, so the returned task is
   fresh. It says `working` because the *store* was never updated, not because the read was
   stale. Same observed output, different cause. This claim did not ship in the issue.

So the caller gets `working` back, and the task never reaches a terminal state at all. A
client that cancels has no way to know it's done polling.

**Fix shape:** await the executor's cancel *before* killing the producer, so the component
that owns terminal state gets a chance to write one.

**How to lead:** the three-line observed output above. It's unambiguous and needs no argument
about intent — a task with no terminal state is broken under anyone's definition.

**Filed together** (see the note at the top of this section).

**Two things found while writing the repro that weren't in these notes, and both strengthened
the report:**

- **V1 does it right in two separate ways.** It has the post-check guard
  (`default_request_handler.py:233`) *and* it awaits the executor's cancel before killing the
  producer (L213 vs L224). The correct implementation is sitting in the same codebase, in the
  file V2 replaced. That turns both findings into one regression story instead of two
  arguments about intent.
- **Their own test papers over it, and a maintainer already suspected as much.**
  `test_scenario_cancel_working_task_empty_cancel` passes only because its executor
  hand-enqueues the `CANCELED` event, directly under a
  `# TODO: this should be done automatically by the framework ?` comment. Same shape as the
  `TodoWrite` finding: a test that supplies the thing it's testing.

**Both bugs are alive on `main` (`cff6727`), not just v1.1.2** — `active_task.py` and
`default_request_handler.py` are byte-identical to the tag.

**The legacy handler is not a working reference here.** The tempting move was one test
parametrized on a2a-python's existing `use_legacy` fixture, so it would pass on V1 and fail on
V2. It doesn't work: with an executor whose `cancel()` is empty, legacy *hangs* rather than
passing, because `on_cancel_task` waits in `consume_all` for an event that never arrives. Both
handlers are broken, differently. The shipped tests are V2-only for that reason.

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

**FILED 2026-08-08:** [kanywst/a2acode#37](https://github.com/kanywst/a2acode/issues/37), which
is the body of record. The offer to send a PR was cut before posting — the fix needs cross-call
list state, so it's not a drive-by, and it can be offered later if the maintainer likes the
shape.

**Task:** `70dc7c04` (done) · **Evidence:** `docs/captures/phase5-session-tools.json`,
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

### `ACPBackend` exposes no cost ceiling

**No task yet** · Small; a nit rather than a bug

`ClaudeBackend` takes `max_budget_usd` and enforces it; `ACPBackend` takes no such argument,
though the ACP connection tracks `cost_usd` internally — so a ceiling is implementable there,
it just isn't exposed.

**Why we care specifically:** combined with the `TodoWrite` finding above, the only backend
that can record a `plan` event is the one with no cost ceiling. `rig-record` therefore rejects
`--max-budget-usd` on `--backend acp` and says so at startup, rather than accepting a flag it
cannot honor.

**Fix shape:** plumb `max_budget_usd` through `ACPBackend` the way `ClaudeBackend` already
does. Possibly deliberate — ACP fronts agents whose cost accounting isn't a2acode's to enforce
— so lead with the question rather than the patch.

### The acp backend drops tool-call arguments

**No task yet** · Small, and evidenced by a real recording

Every `tool_use` event out of `ACPBackend` carries an empty `input`, every `tool_result` carries
an empty `name`, and the tool names are UI labels (`Read File`, `ToolSearch`) rather than tool
ids (`Read`, `Grep`). Evidence is the promoted recording,
`a2a-rig/repos/billing-api/scenarios/20-recorded-health.yaml`: sixteen events, and not one
`tool_use` says *what* it read or edited.

The arguments are demonstrably available at that layer — the `permission` event recorded from
the same turn carries the full `Edit` payload (`file_path`, `old_string`, `new_string`,
`replace_all`). So this is a mapping gap in `events_from_update`, not missing upstream data.
Compare `ClaudeBackend`, whose Phase 2 capture (`docs/captures/phase2-claude-run.jsonl`) does
carry tool detail.

**Why we care specifically:** a consumer rendering a tool timeline gets "Edit" with no file
name. It also degrades recordings permanently — a recording is only as good as the events it
saw, so every scenario captured through the acp path is missing this and re-recording after a
fix is the only way to get it back.

**Fix shape:** carry `tool_call.raw_input` (and the tool id) into the `ToolUse` event the way
`request_permission` already does at `acp.py:363`.

### A binary `PermissionDecision` flattens ACP's multi-option gates

**No task yet** · Design question more than a bug, evidenced by a real recording

`PermissionDecision` is `allow: bool` (`backends/base.py:114-120`), and `acp.py:371` resolves
it against ACP's option list with `select_option`, preferring one-shot over sticky. For an edit
approval — allow once, reject once — that is a fair simplification. For Claude Code's
`ExitPlanMode` it is not, because there the three options are distinct *modes*, not styling:

| ACP option | `kind` | what it means |
|---|---|---|
| `acceptEdits` | `allow_always` | yes, and auto-accept the edits that follow |
| `default` | `allow_once` | yes, and keep gating each edit |
| `plan` | `reject_once` | no, keep planning |

An A2A caller can reach only the middle and last rows. "Yes, and stop asking me" — the option a
frontend most wants to offer on a plan approval — is unreachable, and nothing reports that it
was dropped.

The `reject_once` row is the sharper half: because it means "keep planning" rather than "stop",
denying this gate ends the task **`completed`**, not `failed`. Evidence is the promoted
recording `a2a-rig/repos/billing-api/scenarios/20-recorded-planmode.yaml`, where the denied
turn ends with the agent asking what to change. Any consumer that maps deny → failure is wrong
for this tool, and a2acode's own vocabulary gives it no way to tell the two denials apart.

**Why we care specifically:** the rig exists to let a frontend develop against real protocol
shapes. This is a shape the protocol *can* carry (ACP has the options) that a2acode discards
before it reaches the wire, so no amount of recording will surface it.

**Fix shape:** carry the offered options onto `PermissionRequest` and let `PermissionDecision`
name an option id, falling back to the current allow/deny resolution when it doesn't. Pairs
naturally with the dropped-tool-arguments finding above — both are `events_from_update` /
`request_permission` losing detail that ACP already handed over.

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

1. ~~**`79297b49` + `167506a4` to a2a-sdk, together.**~~ **DONE** — one issue,
   [#1170](https://github.com/a2aproject/a2a-python/issues/1170), plus repro PR
   [#1171](https://github.com/a2aproject/a2a-python/pull/1171).
   The "producer finished" question was *not* asked outright. Once V1 turned out to guard both
   paths, the finding stood on its own as a regression, and the parked test was written to pass
   under either answer. So `34c83f8c`'s fate now hangs on how they respond rather than on a
   question we posed.
2. **`5dcde5fb` and `34c83f8c` to a2acode** — `5dcde5fb` is now unblocked and worth filing
   regardless, since it's the same "cancel writes no terminal state" story from a2acode's side
   and `#1170` gives it something to point at. `34c83f8c` still wants the SDK response first.
3. **`f010f63e` to a2acode** any time. Independent of everything else, small, easy yes. The
   `ACPBackend` cost-ceiling nit rides along here — same repo, same size, same "is this
   deliberate?" shape, and it reads better as a pair than as a lone quibble.
   **The dropped tool-call arguments file separately**, despite being the same repo: it is a
   plain data-loss bug with a checked-in recording as the repro, not a design question, and
   bundling it with two "is this deliberate?" nits would bury the one item that has evidence
   attached.
4. **`cc7feef9` to a2a-cli** any time. The work already exists.

**`70dc7c04` jumped the queue and is filed** ([a2acode#37](https://github.com/kanywst/a2acode/issues/37),
2026-08-08). It was the only plain feature-is-broken report rather than a design conversation:
reproducible, evidenced by a tool-list dump, and provably not the reporter's setup since the ACP
backend does the same job fine. Nothing about it waited on the SDK cancel answer.

So the queue now starts at step 1 above — the a2a-sdk cancel pair.
