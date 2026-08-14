# Upstream issues to file

Findings from this project that belong in someone else's repo, and what to say when filing
them. Seven filed so far: `70dc7c04` → [a2acode#37](https://github.com/kanywst/a2acode/issues/37),
`79297b49`+`167506a4` → [a2a-python#1170](https://github.com/a2aproject/a2a-python/issues/1170)
with repro PR [#1171](https://github.com/a2aproject/a2a-python/pull/1171), `5dcde5fb` →
[a2acode#38](https://github.com/kanywst/a2acode/issues/38), `f010f63e` →
[a2acode#39](https://github.com/kanywst/a2acode/issues/39), `e653db90` →
[a2acode#40](https://github.com/kanywst/a2acode/issues/40), `777656ed` →
[a2acode#41](https://github.com/kanywst/a2acode/issues/41), and `b0cefb1a` →
[a2acode#49](https://github.com/kanywst/a2acode/issues/49). The rest are drafts.

> **Refreshed 2026-08-12 (second pass).** All five originally-filed a2acode issues are
> **CLOSED, fixed and merged** (PRs #42-#46, all merged 2026-08-09 — see each section below for
> which PR closed which issue). A sixth, `34c83f8c` (input-required cancel), turned out to
> already be resolved by the same wave (`#43`'s second commit) and was never separately filed;
> closed as resolved-by-upstream instead. A seventh, `b0cefb1a` (`AskUserQuestion`
> unanswerable), was re-derived and filed fresh as
> [a2acode#49](https://github.com/kanywst/a2acode/issues/49). None of the merged fixes have
> shipped in a release: a2acode's latest tag is still `v0.6.2` (2026-08-02), and `origin/main`
> is 40 commits ahead of it as of this check. a2a-rig still pins `v0.6.2`, so none of these
> fixes are in the version we actually run against yet. `a2a-python#1170` is still **open** —
> see the a2a-sdk section below, a different PR turned out to be the one that matters.

**Writing the issue is what verifies the note.** All five filings turned up claims here that
were wrong or incomplete, and one (the stale-read mechanism, below) would have been a public
correction if it had shipped. Re-derive from source before filing, always. Re-deriving isn't
damage control, it's where the report gets good:

- `5dcde5fb`'s wrong claim ("written for a disconnected client") was hiding the *better* framing
  underneath it.
- `f010f63e` was filed as a nit and came back a bug, because re-deriving found three downstream
  sites already built to consume the thing the executor throws away.
- The same pass **downgraded** `438d9c1c` and falsified its stated fix shape, which is why it
  got split out and dropped down the queue instead of filed.
- `e653db90` is the extreme case: the symptom was real, and the mechanism, the evidence, *and*
  the proposed fix were each independently wrong. The fix it asked for was already implemented.

So re-derivation changes *severity and scope*, not just facts. Sizing a finding from the note
alone would have gotten those wrong in both directions.

**And when the note can't be settled from source alone, go get the evidence.** `e653db90`
needed the raw ACP wire, which no existing capture held, because the recordings are
*post*-mapping and therefore show a2acode's output rather than the agent's input. Thirty lines
(`scratch/acp-tee.sh` teeing the agent's stdio, `scratch/acp_trace.py` driving one turn) turned
a finding that was **not fileable** into the best-evidenced issue of the five. Budget for that
too: when a claim is about what someone *else* sent you, only the wire can answer it.

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
- **Look for the test that supplies the thing it's testing.** Four for four, and it is the
  most reliable smell in this whole exercise. a2acode's plan test hand-builds
  `ToolUseBlock(name="TodoWrite")`, so it cannot notice the tool was renamed out from under it.
  a2a-python's cancel test hand-enqueues the `CANCELED` event, so it cannot notice the framework
  never writes one. Both bugs survived large green suites *because* of their tests, not despite
  them. When a finding seems too obvious to have gone unnoticed, go read the test that should
  have caught it — the answer is usually there, and it makes the report much better than the bug
  alone. It also reframes the issue from "you have a bug" to "you have a blind spot", which is
  the more useful thing to hand a maintainer.
  **The third case ([#38](https://github.com/kanywst/a2acode/issues/38)) generalized the
  habit:** sometimes the answer is that there's no test at all. `AgentExecutor.cancel` and
  `_pump`'s CancelledError branch have zero coverage, because a2acode's cancel tests all sit a
  layer below the protocol. So the question isn't only "what does the test fake?" but "what
  layer does the coverage stop at?" Same payoff either way.
  **The fourth ([#39](https://github.com/kanywst/a2acode/issues/39)) is the purest instance
  yet:** `_FakeSession` takes a `PermissionDecision` in its *constructor*, so the deny path is
  thoroughly covered below the executor and never through it. Note the framing used there, since
  it's why these land: say the tests are good tests of the thing they actually target, and that
  they just construct the input themselves. Same finding, no implied sloppiness.
- **Run the final text through the `writing-voice` skill.** These drafts are notes to
  ourselves; an issue body is outbound prose with my name on it. **Per issue, not once per
  session** — #38 invoked it, #39 and #40 coasted on it still being in context, and #40 shipped
  a tell as a result. **And actually open `references/anti-ai-tells.md`.** Working from the
  summary list in the skill body missed a bare `Worth noting` (the summary shows the
  `It's worth noting` form), which then survived a grep built from the same incomplete memory.
  A self-check written from the thing you're checking is not a check.
- **Watch for eliminative headers and compliment sandwiches specifically.** #40 shipped a
  section headed *"Why I don't think this is a careless bug"*, which nobody had accused them of
  until we raised it, and which defines the section by what it isn't — against Josh's standing
  "direct assertion over elimination" rule. It opened with *"that's a good property"* too;
  praising someone's design immediately before criticizing it reads as technique, not warmth.
  Fixed in place to *"The mapper has no memory, by design"*. **The generous framing that works
  is accurate description, not announced charity** — "those are good tests of the thing they
  actually target" (#39) lands because it's simply true, and says nothing about anyone's
  character.

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
>
> **Refreshed 2026-08-12: still open, and the fix that's coming isn't ours.**
> [#1171](https://github.com/a2aproject/a2a-python/pull/1171) is still exactly what it was filed
> as — two `xfail(strict=True)` repros, no fix attached, unmerged. A different PR,
> [#1172](https://github.com/a2aproject/a2a-python/pull/1172) ("owner-scope cancel/subscribe and
> write terminal state on cancel"), fixes both `#1170` and a second issue (`#1159`) together and
> is also still open. A third party (`astrogilda`) ran our two repro scenarios against #1172's
> branch on 2026-08-10 and confirmed both flip from `XFAIL` to `XPASS(strict)` — i.e. #1172
> actually closes this out, once it merges. They also suggested folding our two scenarios into
> #1172 directly and dropping the strict markers, since #1172 covers the same ground at the
> handler layer. Worth watching #1172 rather than #1171 for the fix landing.
>
> One more thing surfaced on #1170 worth a flag, not action: a comment posted 2026-08-10 by
> `impartshadow` reads as an automated triage bot — cites an "Advisory SFA-2026-C7F2E65D71",
> links an external "echo-site" write-up, and asks readers to react 👍/👎 to steer what it
> investigates next. The technical content isn't wrong on a skim, but the link is unvisited and
> the ask-for-reactions pattern is exactly the shape of something angling for engagement rather
> than genuinely helping. Not acted on here; flagging so nobody treats it as an official
> maintainer response.

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

**FILED 2026-08-08:** [kanywst/a2acode#38](https://github.com/kanywst/a2acode/issues/38), which
is the body of record.

> **MERGED 2026-08-09:** [#43](https://github.com/kanywst/a2acode/pull/43) ("write a terminal
> state when a run is cancelled"), plus a second-half fix the maintainer found while in there —
> cancelling a task already parked on a permission also left it stuck in `input-required`, same
> root cause. Both measured against a real server per the maintainer's close comment. Unreleased
> (still ahead of `v0.6.2`).

**Task:** `5dcde5fb` (done) · **Pairs with:** `167506a4`

`executor.py`'s `_pump` handles `asyncio.CancelledError` by dropping the session and
re-raising, deliberately without emitting a status. ~~That branch was written for a
*disconnected client*, where there is genuinely nobody left to tell — a reasonable call. But a
deliberate cancel arrives through the same branch, and there the caller is very much still
listening.~~ **Half wrong, corrected 2026-08-08 while writing the issue.** The second sentence
holds. The first does not: under `a2a-sdk` 1.1.x's V2 handler, a disconnected client *cannot
reach this branch*, and neither can a timeout.

- The producer is a detached `asyncio.create_task` (`active_task.py:490`), so an HTTP client
  going away doesn't cancel it. Subscriber teardown runs `_maybe_cleanup`, which no-ops unless
  `_is_finished` is already set (`active_task.py:815-819`) — mid-run it isn't, so the run
  continues to completion and writes its terminal state normally.
- There is no timeout mechanism: zero hits for `wait_for`/`timeout` in all of `active_task.py`.
- The only live callers of `_producer_task.cancel()` are `ActiveTask.cancel` (`:733`, a
  deliberate cancel, caller still listening) and `aclose()` (`:790`, server shutdown, where the
  queues are already closed `immediate=True` so emitting reaches nothing anyway).

Confirmed a2acode is on that path: `DefaultRequestHandler = DefaultRequestHandlerV2`
(`request_handlers/__init__.py:46`).

**This inverted the framing, and it's what made the issue good.** Not "a case they hadn't hit"
but "the comment names a case that stopped reaching this branch, and the only case that does
reach it is the one handled wrong."

**Suggestion as filed:** `await updater.cancel()` in the branch before re-raising. It lands
where `AgentExecutor.cancel`'s existing `updater.cancel()` doesn't, because the producer's
`finally` closes the queue with `close(immediate=False)` — a graceful close that still drains
what's already enqueued (`event_queue.py:194-196`). **Reasoned from source, never tested.**
Flagged as such in the issue body rather than asserted.

**The blind-spot smell went three for three, in a new shape.** Not a test that supplies the
thing it's testing — *no test at all*. `tests/test_executor.py` has zero hits for `.cancel(`
or `CancelledError`, so `AgentExecutor.cancel` and this branch are uncovered. The cancel tests
that exist (`test_smoke.py`, `test_acp.py`) all sit at the `BackendSession`/ACP layer. Cancel
is well covered as "does the backend stop," never as "does the task close out."

**Don't hand them the out.** The first draft said "if #1170 lands, a2acode is fixed without any
change here." True, and an argument for closing the issue. Josh caught it: we have no read on
the SDK maintainers' turnaround, so leaning on their fix is betting on an unknown. Rewritten to
cross-reference #1170 for the full picture, state plainly that the timeline is unknown, keep
the double-emit disclosure (redundant, not conflicting), and end on the direct assertion:
*this is a2acode's terminal state to write, and right now nothing writes it.*

### A task parked in `input-required` cannot be cancelled

**Task:** `34c83f8c` (closed, resolved by upstream — see below)

a2acode parks by *returning* from `execute()` (the "Paused on a permission request; keep the
stream for the follow-up" path), keeping the `BackendSession` alive out of band. That's
deliberate and is what lets one permission round trip span two `execute()` calls — but it
means the producer looks finished to the SDK, which is what walks into `79297b49`.

Also worth mentioning in the same issue: **cancel is only tested at the `BackendSession`
level, never end to end over the protocol.** That's why this survived 163 tests. Offering that
observation alongside the bug is more useful than the bug alone.

> **Resolved 2026-08-12, re-derived against current `main` (`b36a645`).** The maintainer's
> close comment on [#38](https://github.com/kanywst/a2acode/issues/38) was right: PR
> [#43](https://github.com/kanywst/a2acode/pull/43), specifically commit `1d1a0c6` ("close out
> a task cancelled while paused too"), is exactly this fix. `executor.py`'s `cancel()` now
> checks `session is None or session.is_parked` and, when true, writes the terminal state
> itself before closing the session, since a parked session has no `_pump` left to do it. Two
> tests cover it directly, `test_cancelling_a_paused_task_writes_its_terminal_state` and
> `test_cancelling_a_running_task_leaves_the_state_to_its_pump`, both pass against current
> `main`. Nothing left to file on the a2acode side. Confirmed not just by reading the diff but
> by running `uv run pytest tests/test_executor.py -k cancel` against a synced `origin/main`
> checkout. The `79297b49`/`167506a4` question ("what does 'the producer returned' mean
> upstream") is still open on a2a-python's side regardless — see the refreshed a2a-sdk section
> above.

### The claude backend can no longer emit a `plan` event at all

**FILED 2026-08-08:** [kanywst/a2acode#37](https://github.com/kanywst/a2acode/issues/37), which
is the body of record. The offer to send a PR was cut before posting — the fix needs cross-call
list state, so it's not a drive-by, and it can be offered later if the maintainer likes the
shape.

> **MERGED 2026-08-09:** [#46](https://github.com/kanywst/a2acode/pull/46) ("rebuild the plan
> from the tools that carry it now"). Per the maintainer's close comment, not a rename after
> all: `TaskCreate`/`TaskUpdate` change one entry at a time and the created task's id only comes
> back in the tool's result, so the plan is now rebuilt from per-context list state rather than
> a single tool-name constant — the harder version of the fix shape suggested in the issue.
> Several follow-up commits after #46 (`0ee484f`..`a22288f` on `main`) keep hardening the same
> state (bounding the task-id map, refreshing context position on resume). Unreleased.

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

**FILED 2026-08-08:** [kanywst/a2acode#39](https://github.com/kanywst/a2acode/issues/39), which
is the body of record.

> **MERGED 2026-08-09:** [#42](https://github.com/kanywst/a2acode/pull/42) ("hand the agent the
> words a caller denied with"). Per the maintainer's close comment they also hardened the
> allow-word matcher while in there: the old `startswith("allow")` read a prose denial like
> "allowing that would drop the database, so no" as consent, once prose denials became the
> documented way to answer. Unreleased.

**Task:** `f010f63e` (done) · ~~Small; a nit rather than a bug~~ **A bug, and not a nit.**

`executor.py:497-507` builds the decision from the caller's message, but on a denial always
sends `"Denied by A2A caller"` — whatever the caller actually wrote is dropped. So there's no
way to deny *with guidance* ("not that command, try `pytest -x`"), which is the useful half of
a denial.

**Why we care specifically:** it caps how rich a scripted deny branch can plausibly be. A
scenario can't model "denied with a redirect" because the real producer can't express it.

**Re-deriving upgraded this from nit to bug.** The feature is built end to end and one line
makes it unreachable. Three sites already read `decision.message` on the deny path, each with
its own fallback: `base.py:120` defines the field, `claude.py:197` sends
`PermissionResultDeny(message=decision.message or "Denied by A2A caller")`, and `acp.py:444`
raises `auth_required({"reason": decision.message or "terminal denied by the A2A caller"})`.
**The string hardcoded at `executor.py:506` is character-for-character `claude.py:197`'s own
fallback**, so the executor supplies the default the backend would have supplied anyway, making
that `or` branch dead code. That single observation is the whole argument, and it's what turned
a "would be nice" into "this was meant to work."

**Fix shape:** pass the caller's text through as `PermissionDecision.message`. Watch the
lowercase trap: `executor.py:501` does `.strip().lower()` for the allow-word match, so the fix
needs the *raw* input or the agent gets casing-flattened guidance.

**Blind-spot smell, four for four.** `tests/test_acp.py:293-300`'s `_FakeSession` takes a
`PermissionDecision` in its constructor and returns it, so every permission test bypasses
`Executor._decision` entirely. The deny path is well covered *below* the executor and not at
all *through* it. Framed generously in the issue (those are good tests of the ACP bridge, they
just construct the decision themselves), which is the framing that keeps making these land.

### `ACPBackend` exposes no cost ceiling

**Task:** `438d9c1c` · **Not filed, and deliberately dropped down the queue** (see the split
note below)

~~`ClaudeBackend` takes `max_budget_usd` and enforces it;~~ **Wrong, corrected 2026-08-08 while
re-deriving.** `ClaudeBackend` takes `max_budget_usd` (`claude.py:155`) and *delegates*
enforcement: it sets `options.max_budget_usd` (`claude.py:184-185`) and lets the Claude Agent
SDK do the enforcing. a2acode enforces nothing itself.

`ACPBackend.__init__` (`acp.py:550-558`) takes no such argument. The connection does track cost
(`self.cost_usd` at `acp.py:316`, set from `update.cost.amount` at `:358`, reported at `:640`),
so the *data* is there.

**Why we care specifically:** combined with the `TodoWrite` finding above, the only backend
that can record a `plan` event is the one with no cost ceiling. `rig-record` therefore rejects
`--max-budget-usd` on `--backend acp` and says so at startup, rather than accepting a flag it
cannot honor.

~~**Fix shape:** plumb `max_budget_usd` through `ACPBackend` the way `ClaudeBackend` already
does.~~ **There is nothing to plumb.** ClaudeBackend's "way" is handing a knob to the Anthropic
SDK, and ACP is a generic protocol fronting arbitrary agents with no equivalent knob. Giving
ACP a ceiling means a2acode enforcing one *itself*: watch `cost_usd` on session updates, abort
mid-turn when it crosses, decide what task state that lands in. That's a feature with real
design questions, not a nit.

**So the "possibly deliberate" hedge was right, for a better reason than the note knew.** It
isn't that ACP costs philosophically aren't a2acode's to enforce; it's that ACP gives a2acode
no mechanism to enforce them the way Claude does. If this ever gets filed, it's a question or a
feature request, not a bug report.

**Split from `f010f63e` on 2026-08-08.** The filing order had paired them as "same repo, same
size, same *is this deliberate?* shape." Re-deriving falsified all three: `f010f63e` turned out
to be a one-line bug with an obvious right answer, this one an open-ended feature request.
Bundling them would bury the one with a clear answer under the one needing a conversation —
the same reasoning already applied to keep `e653db90` separate.

### The acp backend drops tool-call arguments

**FILED 2026-08-08:** [kanywst/a2acode#40](https://github.com/kanywst/a2acode/issues/40), which
is the body of record.

> **MERGED 2026-08-09:** [#44](https://github.com/kanywst/a2acode/pull/44) ("keep the arguments
> a tool call reports after it opens"), in the shape suggested — the merge happens in
> `session_update`, mapper untouched. Per the maintainer's close comment, on the sequencing
> question the issue left open, `ToolUse` now waits rather than emitting twice. Several
> follow-up commits after #44 (`ed5c1b3`..`5162834` on `main`) keep refining the same
> merge-before-map state (snapshotting calls the flush walks, dropping calls on unbind,
> flushing on paths that don't return normally). Unreleased.

**Task:** `e653db90` (done) · **The symptom was real; the mechanism, the evidence, and the fix
in these notes were all wrong.** Settled by capturing the raw ACP wire (below).

Every `tool_use` event out of `ACPBackend` carries an empty `input`, every `tool_result` carries
an empty `name`, and the tool names are UI labels (`Read File`, `ToolSearch`) rather than tool
ids (`Read`, `Grep`). Evidence is the promoted recording,
`a2a-rig/repos/billing-api/scenarios/20-recorded-health.yaml`: sixteen events, and not one
`tool_use` says *what* it read or edited.

~~The arguments are demonstrably available at that layer — the `permission` event recorded from
the same turn carries the full `Edit` payload (`file_path`, `old_string`, `new_string`,
`replace_all`). So this is a mapping gap in `events_from_update`, not missing upstream data.~~
**Invalid inference.** The permission payload arrives via
`request_permission(tool_call: s.ToolCallUpdate)`, a *different protocol message* from the
`session/update` notification `events_from_update` maps. That one carries args proves nothing
about the other.

~~**Fix shape:** carry `tool_call.raw_input` (and the tool id) into the `ToolUse` event the way
`request_permission` already does at `acp.py:363`.~~ **Already implemented.** `acp.py:134`
does exactly `tool_input=_as_dict(update.raw_input)` on `ToolCallStart`. The note asked for a
line that was already there.

**What is actually happening, from the wire** (`scratch/acp-tee.sh` + `scratch/acp_trace.py`,
one turn against `~/scratch/demo-app`). ACP streams one tool call across several messages, each
refining the last:

| # | `sessionUpdate` | `title` | `status` | `rawInput` |
|---|---|---|---|---|
| 1 | `tool_call` | `Read File` | `pending` | `{}` |
| 2 | `tool_call_update` | `Read app.py` | absent | `{"file_path": "/.../app.py"}` |
| 3 | `tool_call_update` | absent | absent | absent |
| 4 | `tool_call_update` | absent | `completed` | absent |

Absent means *unchanged*, not empty: `ToolCallUpdate`'s fields are all `Optional = None`,
documented as "Update the raw input", "Update the human-readable title". a2acode reads
`raw_input` only on message 1 (where it is genuinely `{}`), and its `ToolCallProgress` branch
(`acp.py:139-143`) never reads `raw_input` at all. **The arguments arrive on message 2 and are
dropped on the floor.** Same root cause for `name=''`: `_tool_results` (`acp.py:269`) reads
`update.title` on message 4, where it is absent because unchanged.

**Root cause, and the framing that made the issue:** `events_from_update` has no memory of the
previous message, deliberately and by documented design ("Pure and side-effect free so the
translation can be unit tested without a live agent subprocess"). Correlating a refinement to
the call it refines requires exactly that memory. A reasonable design choice meets a protocol
it cannot express. **Note the vocabulary lesson:** the first draft leaned on the word "purity"
as if it explained itself, and Josh asked what it meant. It doesn't explain itself, and the
property that matters is narrower anyway ("no memory between calls"). Rewritten to say that;
"pure" survives only inside their quoted docstring.

**Fix shape (corrected):** merge before mapping. `session_update` already owns per-connection
state, so it can keep a `toolCallId`→tool-call dict, fold each update into the remembered call,
and hand `events_from_update` a complete view. The mapper is untouched and its tests don't
move. Offered as a suggestion, since putting the state inside the mapper is also defensible.

**Two things the capture found that the notes had backwards:**

- **`tool_result.name` is masked, `tool_input` is not.** `executor.py:349`/`:358` stash tool
  names by id and fall back to them, so the empty name never reaches an A2A client. The
  arguments have no such fallback. Sizing the two symptoms equally was wrong.
- **It is user-visible over A2A, worse than "a consumer rendering a timeline."**
  `_describe_tool` (`executor.py:207-217`) reads `tool_input`, so a `Bash` call renders as
  literally `$ ` with no command. And its comment ("ACP names a tool call with a human title
  that often already says the path") is describing *message 2's* refined title, which a2acode
  drops in favour of message 1's `Read File`. Someone wrote a fallback for exactly this and the
  mapper never delivers it. That detail is the best evidence in the issue and no note had it.

**Third claim dropped entirely.** "Tool names should be ids like `Read`, not labels like
`Read File`" is not a bug: `title` is ACP's human-readable title by design, and `ToolCall`
carries no underlying tool-name field for a2acode to prefer.

### A binary `PermissionDecision` flattens ACP's multi-option gates

**FILED:** [a2acode#41](https://github.com/kanywst/a2acode/issues/41)

> **MERGED 2026-08-09:** [#45](https://github.com/kanywst/a2acode/pull/45) ("let a caller
> answer with the option the agent offered"). One deviation from the suggested fix shape per the
> maintainer's close comment: naming an option takes an explicit `option:<id>` prefix, because
> the agent chooses both an option's name and its kind and could label an `allow_always` choice
> "Deny." They also settled the `completed`-vs-`failed` question this issue raised: a recorded
> run confirmed `completed` is the honest state for a denied gate, not a bug to fix. Several
> follow-up commits after #45 (`8aefa1b`..`b36a645` on `main`) keep hardening the same option-id
> matching (sanitizing the rendered kind, folding headers onto one line, binding an answer to
> the prompt it answers). Unreleased.

**Task:** `777656ed` · Design question more than a bug, evidenced by a real recording

**Re-derived 2026-08-08, holds exactly.** Every line number, the `select_option` preference
order, and the "completed not failed" consequence all still match current source
(`backends/base.py:114-120`, `acp.py:155-174,371`, `executor.py`'s single path to
`updater.failed()` being exception-or-eviction only). Unlike `438d9c1c`, ACP already carries the
distinction here — the agent is already sending three option kinds — so this isn't "invent a new
mechanism," it's "a2acode is throwing away a distinction the wire already hands it." That's the
one asymmetry that argues for filing where `438d9c1c` didn't.

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

### The claude backend cannot answer `AskUserQuestion` — allow arrives with no answers

**FILED 2026-08-12:** [kanywst/a2acode#49](https://github.com/kanywst/a2acode/issues/49), which
is the body of record.

**Task:** `b0cefb1a` (done) · **Cockpit half:** `d6465f5e`

**Re-derived 2026-08-12 against current `main` (`b36a645`) before filing, per the doc's own
discipline.** Everything below held, with one addition: `PermissionDecision` now also carries
`option_id` (new since the ACP multi-option fix, [#41](https://github.com/kanywst/a2acode/issues/41)/PR
[#45](https://github.com/kanywst/a2acode/pull/45), landed after this finding was first
written). It doesn't help here — `claude.py`'s `can_use_tool` never reads it, only
`decision.allow` — but it's evidence the fix shape asked for below (a richer
`PermissionDecision`) is exactly the kind of change the maintainer has already been willing to
make. Also confirmed the blind spot directly: `tests/test_claude_backend.py:362` is the only
test that reaches `drive()`, and it passes `can_use_tool=lambda *a: None`, so the real closure
that builds `PermissionResultAllow()` has zero coverage.

**Found 2026-08-12**, first live cockpit run against `--backend claude` (no `--permission-mode`,
the instructive path). Prompted with "add a /health endpoint and run the tests" against a repo
whose test suite turned out not to exist, Claude did the right thing: called `AskUserQuestion`
to ask how to proceed. The question *reached* the A2A caller intact — `AskUserQuestion` routes
through `can_use_tool` like any other tool, so the task parked `input-required` and the
`a2acode_permission` metadata carried the full `questions` array (question text, headers,
options, `multiSelect`). Then the round trip dead-ends:

- The caller's answer runs through `_decision` (`executor.py:501-506`), which lowercases the
  resume text and checks it against allow-words. Everything collapses to
  `PermissionDecision(allow: bool)`.
- `claude.py:194-195` turns an allow into a bare `PermissionResultAllow()` — no
  `updated_input`.
- The CLI synthesizes the tool result **"The user did not answer the questions."** and the
  agent continues unanswered. In our run it left the change as-is and said so; the turn's
  question was simply wasted.

**The SDK contract** (re-derived 2026-08-12 from
[the Agent SDK user-input docs](https://code.claude.com/docs/en/agent-sdk/user-input.md);
`PermissionResultAllow.updated_input` confirmed in `types.py:235-240` of the vendored SDK):
answering requires

```python
PermissionResultAllow(updated_input={
    "questions": tool_input["questions"],          # pass-through
    "answers": {"<question text>": "<chosen label>"},  # list or comma-join for multiSelect
})
```

with an optional `"response"` free-text field when the caller dismisses the structured
questions. See also anthropics/claude-agent-sdk-python#327 and anthropics/claude-code#20275
for the documentation history.

**Why this one bites harder than the other permission gaps:** it's not an exotic tool. An
un-steered Claude reaches for `AskUserQuestion` whenever the task is ambiguous, and the
instructive path (no `--permission-mode`) guarantees the gate. Today every clarifying question
a claude-backend agent asks over A2A is unanswerable — allow and deny both leave it unanswered;
they only vary the flavor of shrug.

**Fix shape:** `PermissionDecision` needs a payload channel, not just a bool — e.g. the resume
message's metadata (mirroring `a2acode_permission` inbound) carrying an object the claude
backend forwards as `updated_input`. This is the same structural gap as "A binary
`PermissionDecision` flattens ACP's multi-option gates" above and "Permission deny discards the
caller's text" — three findings, one boolean pipe. A single richer decision type (option id /
updated-input / message) closes all three.

### M4: offer `playback` and `--record` upstream

**Not a bug — the planned contribution.** DESIGN-v3 §7-8. `a2a_playback` is written to drop
into a2acode's `backends/` directory: it imports only the public backend vocabulary and holds
no rig-specific assumptions, so this should be a file move rather than an untangling.

Not ready to propose yet. Worth having the cancel conversations first anyway — they establish
that we've been using a2acode seriously, which is a better opening than a cold feature PR.

---

## a2a-cli (`ericabouaf/a2a-cli`)

### Migrate the client to `@a2a-js/sdk` 1.0.1

**Task:** `cc7feef9` · **Work is done, sitting in a fork.** **Decision 2026-08-08: not filing,
for now.** Re-derived in full first, so if we change our minds everything needed is below and
nothing has to be rediscovered.

Patched on `a2a-sdk-1.0-migration` in `~/github.com/technicalpickles/a2a-cli`, `npm link`ed
locally. Upstream's npm metadata has no `repository` field, which is why this went fork-first
rather than issue-first — there was no obvious repo to file against from the package alone.
(Their own history has `1d3fc44 Remove repo ?`, so the absence was deliberate, not an oversight.)

**Why we stopped short of filing.** `ericabouaf/a2a-cli` was created and abandoned inside a
35-minute window on 2025-11-08 (`createdAt` and `pushedAt` are both that day), and nine months
on it has 1 star, 1 fork (ours), **zero issues and zero PRs, ever**. A cold 200-line PR into
that is a lot of ceremony for something unlikely to be read. Josh's call, and the fork already
works locally so nothing is blocked. A draft issue is at `scratch/issue-a2a-cli.md` if it's
ever wanted.

**Re-derivation (2026-08-08) verified the branch and corrected the story.** Everything the
commit message claimed is true; it was also incomplete in a way that would have made a filed
report look wrong.

Branch state: one commit on top of `upstream/main` (`c3bfa17`), zero behind — upstream has not
moved since 2026-08-06, so it still applies cleanly. `tsc --noEmit` clean. Verified end to end
against a live a2acode echo server (card discovery, blocking send, streaming `-w` through to
`completed [FINAL]`, `get`, `cancel` failing gracefully with `TASK_NOT_CANCELABLE`) and
`input-required` against the playback repo via `30-refactor.yaml`'s gate.

**There are three distinct failures, not one, and they sit on three different version axes:**

| axis | old | current | what changed |
|---|---|---|---|
| A2A protocol | 0.x | 1.0 | endpoint moved from a top-level `url` to `supportedInterfaces[]` |
| `@a2a-js/sdk` (JS, client) | 0.3.14 | 1.0.1 | client API restructured; a2a-cli pins `^0.3.4` |
| `a2a-sdk` (Python, server) | 0.3.x | 1.1.2 | what a2acode pins |

(a2acode's own `v0.6.2` is a fourth, unrelated number. Easy to conflate all four.)

1. **`GET /` → 405 Method Not Allowed**, which is what you actually hit first.
   `initializeClient` calls `A2AClient.fromCardUrl(serverUrl)` with the *base* URL, so 0.3.x
   fetches `/` and looks for a card there; a2acode's `/` is JSON-RPC and is POST-only. **This is
   not version skew** — it is independent of protocol version, and 1.x only papers over it
   because `ClientFactory.createFromUrl` does well-known discovery. Whether it ever worked
   against their default server is unknown, so don't call it a bug in any report.
2. **The card shape, which is the real interop break.** Given the actual card URL, 0.3.14 fails
   with `Provided Agent Card does not contain a valid 'url' for the service endpoint.` **This is
   protocol-level, confirmed**: `@a2a-js/sdk` 1.0.1's own `AgentCard` type declares
   `supportedInterfaces: AgentInterface[]` as *required*, so the JS and Python 1.x SDKs agree on
   the shape across languages. a2a-inspector hits the identical wall from its Python 0.3.10 pin
   — two unrelated clients, same break. a2a-cli is simply a pre-1.0 protocol client.
3. **Upstream doesn't typecheck on a clean checkout.** `^0.3.4` floats to 0.3.14 today and the
   client API moved *within* the 0.3 line: `npm install && npx tsc` gives 11 errors across
   `send.ts`, `get.ts`, `cancel.ts`. `tsc` still emits, so the CLI runs, which is why nobody
   noticed. Pure library skew, nothing to do with the protocol.

**Framing note if this is ever written up:** lead with #2. Findings #1 and #3 are what you
*encounter*, but a maintainer skimming a report that opens on the 405 takes away "I'm calling
the wrong URL" rather than "the protocol moved under me." The draft in `scratch/` currently
leads with #1 and would need restructuring.

**Reproduction method, since it took some setup:** `git worktree add <tmp> upstream/main`,
`npm install` (gets 0.3.14), `npx tsc` for finding #3, then run `node dist/cli.js -s <url>`
against `a2acode serve --backend echo` for #1 and again with the explicit
`/.well-known/agent-card.json` URL for #2.

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

## @copilotkit/react-core (`CopilotKit/CopilotKit`)

Two gaps found 2026-08-14 building cockpit's "Phosphor" redesign
(`docs/superpowers/plans/2026-08-14-cockpit-phosphor.md`), both against
`@copilotkit/react-core@1.67.1`, both drafts — neither has been re-derived against the
package's current shipped source since, per this doc's own rule that a lead is not a verified
claim until that pass happens.

### HITL `render()` remembers no decision once `status` is `'complete'`

**Task:** `e340d022` (open)

`useHumanInTheLoop`'s render callback receives `{ args, status, respond }` — nothing else.
Once a tool call transitions to `status === 'complete'`, there is no field on the callback's
arguments carrying which way it resolved. Confirmed by reading the installed 1.67.1 types:
`status` derives from live tool-call execution state, not from the resolved result or the
arguments `respond()` was called with.

That's invisible inside a single continuous session — the component can hold local state
memorizing what it called `respond()` with, and use that for the terminal render. It breaks on
reload. Cockpit's chat pane replays prior history through CopilotKit's own connect handshake
(see the 2026-08-13 DEVLOG entry on the AG-UI event log), which rehydrates the message list and
lands the HITL card back at `status === 'complete'` with no local state to fall back on — the
component genuinely cannot tell, from anything CopilotKit gives it, whether the request was
allowed or denied.

Cockpit's workaround is honest but weaker than the real answer: `ApprovalCard.tsx:69-71` falls
back to a neutral "ANSWERED" receipt when local state is empty, rather than asserting a false
"ALLOWED"/"DENIED". The framework already has the real answer — it's sitting in the tool
call's resolved result — it just doesn't hand it to `render()`.

**Fix shape:** pass the resolved arguments/result into `render()` once `status === 'complete'`,
even just echoing back whatever `respond()` was invoked with, so a receipt UI can recover the
true decision after a reload instead of only while the component happens to still be mounted.

**How to lead:** the callback's own type is the evidence — `args`/`status`/`respond`, no
result — paired with the reload repro (correct answer available in-session, unrecoverable after
remount). No design argument needed, it's a clean before/after.

**Cite:** `a2a-orchestrator/frontend/src/ChatPane.tsx:57-62` (comment above `PendingRearm`
documenting the same status-derives-from-live-execution finding), `.../src/ApprovalCard.tsx:69-71`
(the fallback and its comment).

### `RUN_ERROR` has no supported way to clear a sticky error banner on the next run

**Task:** `13d538c4` (open)

`CopilotChat` swallows AG-UI's `RUN_ERROR` event into a console log by default, with no
in-flow UI trace — cockpit's `onError` prop is what surfaces it at all
(`ChatPane.tsx:139-143`, `216-222`). Once an app-level error banner is set from that callback,
there's no supported hook to clear it when the next run starts or succeeds. Verified against
1.67.1: there's no `onRunStart`-equivalent callback, and `CopilotChat` neither clears its own
internal error state on a fresh run nor exposes a way for a consumer to null out state derived
from a prior `onError` call.

Cockpit's workaround is a manual "↻ remount" control inside the error banner
(the `error-surfaces` slice) — the only way to dismiss it short of a real recovered run is to
tear down and recreate the whole `CopilotKitProvider`/`CopilotChat` subtree, keyed by a nonce
bumped from outside. That's a heavier reset than the actual problem (one stale string in
component state) should require.

**Fix shape:** an `onRunStart` callback, or a `clearError`/reset function returned alongside
the error state, so an app-level banner can be cleared the moment a new run begins without
forcing a full remount.

**Cite:** `a2a-orchestrator/frontend/src/ChatPane.tsx:139-143` (the comment describing the gap
directly); the remount-based workaround this gap forced landed in the `error-surfaces` slice,
commits `472cd6f`/`40ca1f3`.

---

## Filing order

1. ~~**`79297b49` + `167506a4` to a2a-sdk, together.**~~ **DONE** — one issue,
   [#1170](https://github.com/a2aproject/a2a-python/issues/1170), plus repro PR
   [#1171](https://github.com/a2aproject/a2a-python/pull/1171).
   The "producer finished" question was *not* asked outright. Once V1 turned out to guard both
   paths, the finding stood on its own as a regression, and the parked test was written to pass
   under either answer. So `34c83f8c`'s fate now hangs on how they respond rather than on a
   question we posed.
2. ~~**`5dcde5fb` and `34c83f8c` to a2acode**~~ **`5dcde5fb` DONE** —
   [#38](https://github.com/kanywst/a2acode/issues/38). Note the premise this entry was filed
   under was wrong: `5dcde5fb` is *not* "the same story from a2acode's side." Re-deriving showed
   the two fixes are independent and each individually sufficient, so it neither depends on
   `#1170` nor completes it. It was filed on its own merits (a2acode can fix itself now, without
   waiting on another repo). `34c83f8c` still wants the SDK response first.
3. ~~**`f010f63e` to a2acode** any time. Independent of everything else, small, easy yes. The
   `ACPBackend` cost-ceiling nit rides along here — same repo, same size, same "is this
   deliberate?" shape, and it reads better as a pair than as a lone quibble.~~
   **`f010f63e` DONE and filed ALONE** — [#39](https://github.com/kanywst/a2acode/issues/39).
   The pairing didn't survive re-derivation: `f010f63e` is a one-line bug with a right answer,
   `438d9c1c` is a feature request needing a2acode to build enforcement it doesn't have. All
   three claimed similarities were false. `438d9c1c` is now unfiled and low priority.
   **The dropped tool-call arguments file separately**, despite being the same repo: it is a
   plain data-loss bug with a checked-in recording as the repro, not a design question, and
   bundling it with a "is this deliberate?" nit would bury the one item that has evidence
   attached. **That instinct was right twice now** — it's the same call that split `f010f63e`
   from `438d9c1c`, so treat "does bundling bury the one with evidence?" as the standing test.
4. ~~**`cc7feef9` to a2a-cli** any time. The work already exists.~~ **Not filing, 2026-08-08.**
   The work does exist and was re-derived and re-verified in full, but `ericabouaf/a2a-cli` has
   been untouched for nine months with zero issues and zero PRs ever. Findings preserved in the
   entry above; a draft issue sits in `scratch/`. Revisit if the repo shows a pulse.

**`70dc7c04` jumped the queue and is filed** ([a2acode#37](https://github.com/kanywst/a2acode/issues/37),
2026-08-08). It was the only plain feature-is-broken report rather than a design conversation:
reproducible, evidenced by a tool-list dump, and provably not the reporter's setup since the ACP
backend does the same job fine. Nothing about it waited on the SDK cancel answer.

**`777656ed` also jumped the queue and is filed** ([a2acode#41](https://github.com/kanywst/a2acode/issues/41)),
not listed here when this section was first written. Same shape as `70dc7c04`: a concrete,
evidenced finding independent of the still-open cancel question.

So the queue now starts at step 1 above — the a2a-sdk cancel pair, still the only unresolved
item of the six filed.

**Refreshed 2026-08-12 (second pass): six of seven a2acode findings are resolved.** The five
originally-filed issues (`#37`-`#41`) are closed, fixed, merged 2026-08-09 — none released yet
(`v0.6.2` still latest tag, `origin/main` 40 commits ahead). See the "MERGED" callout in each
section above for the closing PR. `34c83f8c` (input-required cancel) turned out to already be
fixed by the same wave (`#43`'s second commit, `1d1a0c6`) and needed no separate filing — closed
as resolved-by-upstream after re-deriving against current `main` and confirming both of its
tests pass. `b0cefb1a` (`AskUserQuestion` unanswerable) was re-derived fresh and filed as
[a2acode#49](https://github.com/kanywst/a2acode/issues/49). `a2a-python#1170` is the one still
open, and the PR that will actually close it is
[#1172](https://github.com/a2aproject/a2a-python/pull/1172), not our
[#1171](https://github.com/a2aproject/a2a-python/pull/1171) — see the refreshed a2a-sdk section
above. Remaining unfiled: `438d9c1c` (ACP cost ceiling, deliberately low priority) and
`cc7feef9` (a2a-cli fork, not filing per the 2026-08-08 decision). `d6465f5e` (the cockpit half
of the `AskUserQuestion` finding) is separate rig work, not an upstream filing, and stays open.
