# Devlog

Working notes captured alongside PLAN.md execution — what actually happened, what broke, and
why decisions went the way they did. PLAN.md tracks status; this is the narrative behind it.

## 2026-08-06 — Phase 0 and Phase 1

### Phase 0: install

Cloned `a2acode` to `~/github.com/kanywst/a2acode` — already sitting at the `v0.6.2` tag at
tip, no checkout needed. `uv sync --dev` clean, `uv run pytest -q` → 163 passed, matching the
plan's expectation exactly.

Cloned `a2a-inspector` to `~/github.com/a2aproject/a2a-inspector`. `uv sync` and
`cd frontend && npm install` (which runs `tsc` via the `prepare` script) both succeeded.

Installed `a2a-cli` globally via `npm install -g a2a-cli`. This is the point where things got
more interesting than expected — see below.

### Phase 1: echo backend, card discovery, `a2acode call`

Started `a2acode serve --backend echo` on port 9100. Card discovery worked cleanly both via
`a2acode card` and raw `curl .../.well-known/agent-card.json`: `streaming: true`,
`pushNotifications: true`, 6 skills.

`a2acode call "hello world"` produced task id, context id, streamed echo, `[completed]` —
exactly per the plan. The `input-required` pause/resume round trip worked both ways: a
`sudo`-containing prompt parks the task, `allow` resumes it to completion, `deny` reports
`permission denied; nothing run`. Multi-turn context continuity (two calls sharing
`--context`, distinct task ids under the same context id) also confirmed.

### Finding: a2a-cli and a2a-inspector can't talk to a2acode at all

Both `a2a-cli send`/`chat` and `a2a-inspector`'s connect step failed card validation against
a2acode. Root cause: a2acode runs `a2a-sdk` 1.1.2, which builds the card with the newer
multi-transport `supportedInterfaces` array. Both client tools are pinned well behind that —
`a2a-cli` bundles `@a2a-js/sdk` 0.3.14, `a2a-inspector`'s `uv.lock` pins `a2a-sdk` 0.3.10 — and
both still expect the legacy single `url` field on the card, so both fail identically
(`AgentCard does not contain a valid 'url'` / a pydantic "field required" error).

Confirmed this is a genuine breaking API change between the 0.3.x and 1.x SDK lines, not a
stale lockfile: bumping a2a-inspector's `a2a-sdk` pin to 1.1.2 breaks its own code
(`ImportError: cannot import name 'ClientEvent'`), and swapping a2a-cli's bundled SDK to 1.0.1
breaks its own code (`A2AClient.fromCardUrl` no longer exists on the new export shape). Both
reverted cleanly — no lasting changes from that probe.

Decided **not** to chase a2a-inspector's fix: its client code is built on 1.1.2's
protobuf-generated message types internally (not plain pydantic), so a working port is a real
rewrite of `backend/app.py`'s message/task construction, not a like-for-like API swap. Left
untouched, not blocking — Phase 1's exit criterion only needs *one* independent client
(a2a-cli or inspector).

### Fixing a2a-cli

`a2a-cli`'s client code is much smaller and plain TypeScript (no protobuf), so migrating it to
`@a2a-js/sdk` 1.0.1 was tractable. Changes needed, worked out by reading the new SDK's `.d.ts`
files and iterating against the live echo server:

- `A2AClient.fromCardUrl(url)` → `new ClientFactory().createFromUrl(url)`.
- Message parts: old `{kind: "text", text}` → new `{content: {$case: "text", value}}` union
  (also `"raw"`, `"url"`, `"data"` cases).
- `role: "user"` string → `Role.ROLE_USER` numeric enum.
- `TaskStatusUpdateEvent.final` was removed entirely — terminality is now derived from
  `TaskState` (`TASK_STATE_COMPLETED`/`FAILED`/`CANCELED`/`REJECTED` are terminal;
  `INPUT_REQUIRED` is not).
- `sendMessage`/`getTask`/`cancelTask` no longer return a JSON-RPC envelope
  (`{result, error}` with `client.isErrorResponse()`) — they return the value directly and
  throw on error.

First patched this directly in mise's globally-installed copy of `a2a-cli` to prove it out —
correctly called out as sketchy, since that's shared, unversioned state that `npm install -g`
or a mise upgrade would silently blow away. Reverted that.

**Where the real source lives:** the published npm package has no `repository` field and the
maintainer's npm handle (`neyric`) doesn't match their GitHub handle. The actual repo is
[`ericabouaf/a2a-cli`](https://github.com/ericabouaf/a2a-cli) — found via `gh api
repos/ericabouaf/a2a-cli`, confirmed by diffing its `package.json` against the published
tarball (exact match). Forked to `github.com/technicalpickles/a2a-cli`, migration applied as
branch `a2a-sdk-1.0-migration` (commit `452cda7`) on top of real upstream history. The global
`a2a-cli` binary is now `npm link`ed from `~/github.com/technicalpickles/a2a-cli` instead of a
standalone install, so it stays a normal, diffable git checkout.

Verified end-to-end against the live a2acode echo server: card discovery, blocking send,
`--wait` streaming (full task lifecycle — status updates, artifact chunks, `[FINAL]`
completion), `input-required` pause → `allow` resume with context continuity (via `chat`,
piping paced stdin to work around a readline/async-handler race with piped input), `get`, and
`cancel` (including a correct graceful error canceling an already-completed task).

Upstreaming this as a PR to `ericabouaf/a2a-cli` is tracked as taskwarrior task 341 (project
`a2a-experiments`) rather than done inline, since opening a PR against someone else's repo is
a separate, more consequential step than fixing our own dev tooling.

### Outcome

Phase 1's checkpoint — "an independent client has driven card discovery, streaming,
`input-required` round trip, and multi-turn against a2acode" — is met via the patched
`a2a-cli`. a2a-inspector remains broken and is deferred, not blocking.

## 2026-08-07 — Phase 2: real Claude through the A2A surface

### Setup, and the `uv run` gotcha

Scratch repo at `~/scratch/demo-app`: a small Flask app (`/items`, `/items/<id>`), committed
clean at `6890fd7`. First attempt to start the server failed with `Failed to spawn: a2acode` —
`uv run` resolves against whatever project you're standing in, and we were in this repo, not
a2acode's checkout. Either `cd` there first or pass `--project`. Written up in CLAUDE.md since
it will happen again.

### Auth: no API key needed

The plan budgeted an `ANTHROPIC_API_KEY` and `--max-budget-usd 1`. Neither turned out to be
required. The `claude` backend spawns the `claude` CLI through the Claude Agent SDK and
inherits whatever that CLI is logged in with; a2acode does no key validation of its own.
a2acode's README flags that Anthropic doesn't permit subscription credentials for *third-party
serving* — a local single-user rig isn't that.

Worth correcting an assumption made earlier in the session: we guessed `cost_usd` would come
back empty under subscription auth. It doesn't. Both turns reported real figures ($0.30 and
$0.24, $0.54 total) alongside full `usage` token counts and a `claude_session_id`.

### How it was driven

`a2a-cli chat` confirmed card discovery and a `ping`/`pong` round trip against the live claude
backend. For the actual capture we wanted wire shapes rather than a human-readable summary, so
`docs/captures/dump_stream.py` uses the same client path as `a2acode call` but serializes each
protobuf event with `MessageToDict`. Output is one JSON object per line —
`docs/captures/phase2-claude-run.jsonl`, 66 events across both turns.

### What the run produced

`"add a /health endpoint returning ok"` → 16 `working` status updates carrying tool activity as
text (`$ ls -la`, `✓ Bash`, `Read …`), then a `file_change` artifact, then `input-required`.
Approve → the edit lands. Second permission (a Flask test-client command) approved; third (build
a venv and pip-install flask) denied, at which point the run completed gracefully and reported
honestly that it hadn't been able to execute the code.

Multi-turn `"now add a test for it"` on the same `--context` produced a **new task id under the
same context id**, and went straight to writing `test_app.py` without re-reading `app.py` — the
same `claude_session_id` on both completions confirms the Claude session genuinely resumed
rather than starting cold.

`git -C ~/scratch/demo-app diff` matches the artifact. Left uncommitted on purpose as a Phase 4
comparison point.

### Findings for scenario authoring

- **Permission shape.** The `input-required` status message carries
  `metadata.a2acode_permission` = `{tool, input, request_id}`, with `input` being the raw tool
  arguments (for `Edit`: `file_path`, `old_string`, `new_string`, `replace_all`). The visible
  text part is just `"Permission requested for Edit: Edit"` — the tool name is doubled and
  the description is empty. Phase 5's `permission` events need to reproduce the metadata block,
  not the text.
- **Deny discards your text.** `executor.py:502-506` matches the answer against
  `{allow, yes, y, approve, ok, accept, grant}` (or any string starting with `allow`);
  everything else is a deny carrying a fixed `"Denied by A2A caller"`. There's no way to pass
  guidance back to the agent through a denial, which is why wrapping up the second turn took
  three denials in a row. Possible upstream nit.
- **Diff artifacts are synthesized, not real diffs.** The `file_change` artifact for `app.py`
  had hunk header `@@ -1 +1,6 @@` and paths like `a//Users/technicalpickles/…` (doubled
  slash) — a2acode builds it from the `Edit` tool's `old_string`/`new_string`, so line numbers
  are fabricated and the header is cosmetic. Fine for UI rendering, but don't build anything
  that trusts those line numbers.
- **No `plan` event.** a2acode derives plans from `TodoWrite` calls, and Claude didn't use
  TodoWrite for a task this small. Exercising the `plan` path needs a deliberately bigger
  prompt — worth doing before Phase 5 scripts plan events against nothing but the code path.

### Outcome

Phase 2's exit criterion is met: one end-to-end real run observed through independent clients,
artifacts verified on disk, transcript saved. Real inference is optional from here.

The `--backend acp` variant is still unrun (optional in the plan). Next up is Phase 3, which
needs no key — and `dump_stream.py` is most of a first draft of its event-collection helper.

## 2026-08-07 — Phase 3: the pytest harness

New repo at `~/github.com/technicalpickles/a2a-rig`. PLAN pointed at "DESIGN-v3 §7 layout",
but §7 only covers upstream strategy — the only concrete layout in the docs is DESIGN-v2's,
which is superseded (it is built around `clockwork/`, the fake-Anthropic-API approach v3
dropped). So the layout is just `src/a2a_rig/` + `tests/`.

### Shape of it

Three pieces. `server.py` launches `a2acode serve` on a free port and waits for the card;
`events.py` collapses the protobuf event stream into an assertable `Capture`; `conftest.py`
wires up the fixtures. Deliberately **not** importing a2acode — it shells out via
`uv run --project`, which keeps the dependency trees independent (a2acode is on Python 3.14,
the rig on 3.13) and means the harness drives a2acode the way a real client does, over the
wire, with no in-process shortcuts. `A2ACODE_PROJECT` / `A2ACODE_CMD` relocate it.

`server.py` surfaces a2acode's own stderr when the process dies before binding. That paid for
itself immediately: running with a bogus `--backend` produced a2acode's real
`ValueError: unknown backend` instead of an opaque connection timeout, which also proved the
backend flag threads all the way through.

The plan said `ClientFactory.create_from_url()`; the 1.1.2 SDK actually exposes
`create_client(url, ClientConfig(...))`, which is what a2acode's own CLI uses. Same for
`GetTaskRequest`/`CancelTaskRequest` — they take `id`, not the `name="tasks/{id}"` resource
form that the guess assumed.

### Backend parameterization

The point of the phase. Test bodies assert protocol behavior only, so they are
backend-independent; `--backend` (or `@pytest.mark.backend`) picks which one runs. What
genuinely varies is *which prompt provokes a given behavior* — echo parks on a permission
request when it sees "sudo", a playback scenario will park for its own scripted reasons — so
those stimuli are fixtures too (`simple_prompt`, `permission_prompt`, `permission_tool`,
`denied_marker`, `tool_marker`). Phase 4 should be: write a scenario, add a fixture branch,
change no test.

### Finding: cancel does not apply to a parked task

Canceling a task sitting in `input-required` returns successfully and does nothing. The
returned task is still `input_required`, and so is a later `tasks/get`. Traced through all
three layers rather than left as a guess:

**The protocol is fine.** `input-required` is an interrupted, non-terminal state, and
`TaskNotCancelableError` exists specifically to distinguish terminal ones. The spec expects
cancel to work here; it is the contract both layers below fail to honor.

**a2acode parks by returning.** `executor.py` ends the `execute()` call on a permission
pause (`if not session.done: return`, commented "keep the stream for the follow-up"), holding
the live `BackendSession` out of band in `self._live`. That is deliberate and is exactly what
lets one round trip span two separate `execute()` calls — but it means the producer looks
finished to the layer above.

**a2a-sdk V2 is where it actually breaks.** Note `DefaultRequestHandler` is aliased to
`DefaultRequestHandlerV2` (`request_handlers/__init__.py`), so the V1 file is dead code — worth
knowing before reading that source. Two problems:

1. `ActiveTask.cancel` (`agent_execution/active_task.py`) only does anything
   `if not self._is_finished.is_set() and self._producer_task`; otherwise it logs
   "Task already finished … not cancelling" and returns the task untouched. It treats
   *cancelable* as "has a running producer" rather than "is not terminal". `_is_finished` is
   set "EXACTLY ONCE when the consumer loop exits", which a parked a2acode task has done.
2. V2's `on_cancel_task` dropped the guard V1 ended with —
   `if result.status.state != TASK_STATE_CANCELED: raise TaskNotCancelableError(...)`. So
   instead of a wrong-but-loud error, the caller gets a success carrying a non-canceled task.

Under V1 this would have been a `TaskNotCancelableError`. The silent success is a V2
regression; a2acode's design choice is what walks into it. Two different upstreams, two
different bugs — tracked separately in taskwarrior.

Also worth noting a2acode only tests cancel at the `BackendSession` level
(`tests/test_smoke.py`), never end to end over the protocol, which is why this went unnoticed
there. Recorded as two `xfail(strict=True)` tests plus one passing test documenting today's
behavior, so the contrast is explicit and the xfails flip loudly if either side fixes it.
Matters for any UI that wants a "cancel" button on an approval prompt — right now that button
would lie.

### Speed

First green run took 19.7s for 33 tests, because every test booted its own a2acode. Pooling
servers per `(backend, args)` at session scope dropped it to ~1.3s. `fresh_server_url` is
there for anything that genuinely needs an untouched process.

### Outcome

31 passed, 2 xfailed, ~1.3s against echo. Exit criterion met. Phase 4 (playback M0) is next
and needs no key either — and the Phase 2 capture in `docs/captures/` is the shape reference
the first scenario gets written against.

## 2026-08-07 — Phase 4: playback M0

The centerpiece. `playback` lives in the a2a-rig repo as its own package
(`src/a2a_playback/`), deliberately separate from the harness: DESIGN-v3 §7's endgame is
offering it upstream, so keeping it importing nothing but a2acode's public backend vocabulary
makes that a file move rather than an untangling.

### No fork needed, as advertised

`build_app(backend, *, url, card_name, card_description, ...)` takes the backend by
constructor injection, exactly as the design predicted. `rig-serve` is ~50 lines: load a
scenario, construct `PlaybackBackend`, hand it to `build_app`, run uvicorn. Nothing about
a2acode is patched or subclassed.

The Phase 3 worry about dependency trees turned out to be a non-issue: a2acode declares
`requires-python = ">=3.13"`. Its venv happening to be on 3.14 was incidental. Added as a
pinned git dependency at `v0.6.2`.

### More than M0 asked for

M0's list was `text`, `tool_use`/`tool_result`, `result` and `turn`/`contains`/default
matching. The full event set (`thought`, `plan`, `file_change`, `notice`) and `regex` matching
came along anyway, because each is a one-line map onto a `BackendEvent` dataclass and writing
the dispatch for three of nine would have been the odd choice. Permission branching
(`on_allow`/`on_deny`) also landed, since a scenario without a permission gate would not have
exercised the interesting path. `timeout_ms` and `PLAYBACK_SPEED`-driven pacing are wired but
untested, so they stay M1 work.

### The Phase 3 bet paid off

`pytest --backend playback` runs the same 33 backend-agnostic tests as echo with **zero test
bodies changed**. Only the fixtures gained a playback branch.

One fixture had to be added rather than branched: tests asserted `simple_prompt in
artifact_text()`, which is an echo-ism — echo parrots its input, a scripted agent answers with
whatever the scenario says. Split into `simple_prompt` (the stimulus) and `reply_marker` (what
the reply should contain). That is the kind of coupling worth catching now; it would have been
much more annoying to discover with a dozen scenarios in the library.

### Failing loudly

The anti-mock guarantee is the point of the whole design, so it got direct tests: an unmatched
message fails the turn rather than answering plausibly (`ScenarioError` from the backend
propagates through the session runner to `updater.failed`, landing the task in `failed`).
Scenario files are validated at load, so a typo'd event name fails when the server boots
instead of mid-stream in front of a frontend. And a catch-all play that is not last is
rejected outright, since every play after it would silently never run — an easy mistake to
make and a miserable one to debug.

### Verified the Phase 1 way

`a2a-cli send "add a /health endpoint and run the tests"` against `rig-serve` produced the
scenario's own card (`billing-api`, "Fake billing-api repo (playback)"), a plan artifact
rendered as a checklist, tool activity as working-status text, a diff artifact, a response
artifact, and an `input-required` permission pause. `a2acode call "allow"` resumed it to
`[completed] $0.0173 · 4.0 turns` — scripted cost metadata rendering exactly like a real
run's. Instant, no key, no inference.

### Outcome

50 passed, 2 xfailed against both backends, under 2s each. Frontend development can start
against this now. Phase 5 (M1) is the remaining vocabulary depth: permission `timeout_ms`,
`delay_ms`/`PLAYBACK_SPEED` under test, error and `stop_reason` variants, cancel honored
mid-delay.

## 2026-08-07 — Public, and incubating a2a-rig here

Pushed this repo to GitHub public (`technicalpickles/a2a-experiments`) so coworkers can follow
along. Scanned every tracked file for secrets first — only doc mentions of env var names
(`ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`) and a placeholder `test-token`, nothing real.

Then folded the standalone `a2a-rig` repo (`~/github.com/technicalpickles/a2a-rig`, Phases 3–4,
3 commits) into this one under `a2a-rig/`, via `git subtree add --prefix=a2a-rig` against a
temporary local remote pointing at that checkout — not a squash merge, so the three original
commits (harness, cancel-root-cause note, playback backend) replay intact in this repo's
history:

```
git remote add a2a-rig-local ~/github.com/technicalpickles/a2a-rig
git fetch a2a-rig-local
git subtree add --prefix=a2a-rig a2a-rig-local main -m "Incubate a2a-rig here as a subtree"
git remote remove a2a-rig-local
```

`git subtree add` needed the sandbox disabled twice along the way — once because adding the
temporary remote writes `.git/config`, once because the merge itself locks `.git/index` — same
class of restriction as the earlier `git push origin main` over SSH, not a real failure.

Reasoning: `docs/PLAN.md` and the original `CLAUDE.md` both described `a2a-rig` as a permanent
separate repo, which was the plan when Phase 3 started. That's no longer the intent — showing
early, incomplete work to coworkers is easier with one repo to point at than two, and splitting
too early was pure overhead for a project this size. `git subtree` was chosen specifically
because the split is meant to be temporary: `git subtree split --prefix=a2a-rig` can hand the
full, unsquashed history back to its own repo later without any rewriting. Updated `CLAUDE.md`
and `docs/PLAN.md` to record this as the current state, not just a one-off note here.

Also added a top-level `README.md` — the repo had none before going public, only `CLAUDE.md`
(agent-facing, not a landing page). It points at `docs/` for the plan/design/log and `a2a-rig/`
for the code, and states the incubating status up front.

Finally, marked the standalone `~/github.com/technicalpickles/a2a-rig` checkout itself stale:
added a note at the top of *its* `README.md` pointing back at the `a2a-rig/` subtree here as
the active copy, and committed it there (that repo's `origin` remote has no URL configured, so
the commit is local-only, not pushed anywhere). It stays on disk deliberately — it's the source
`git subtree split` would replay from if `a2a-rig/` ever gets extracted back out — but is no
longer where anyone should make changes.

## 2026-08-07 — Phase 5 (M1): the vocabulary a frontend can't provoke on demand

Picked up Phase 5 deliberately narrowed: everything except `plan`, which stays blocked on
taskwarrior `fb20c22b` (no real `TodoWrite`-derived plan event has ever been observed, so
scripting one would be writing against a code path nobody has watched run). Doing the rest
first cost nothing and unblocked four behaviors.

The framing that made this phase worth doing: these are all states a UI has to render and
*cannot reliably provoke from live inference*. You cannot ask a real model to run out of disk
partway through, or to hit a token ceiling on cue, or to sit on an approval prompt for exactly
200ms. Scripted, each is a two-line play.

### What landed

**`error` events.** A new event kind that *raises* (`ScriptedError`) rather than emitting.
That matters: raising is what makes it a real failure — a2acode's `BackendSession` relays the
exception to the executor, which fails the task through the same path a crashed backend takes.
Kept deliberately distinct from `ScenarioError`: that one means the scenario is wrong, this
one means the scenario is right and the run it describes is a failing one.

**`stop_reason` variants.** Already plumbed end to end (backend → `Result` → a2acode's
`_result_metadata` → completion-message metadata), just never asserted. Now pinned, because
telling a truncated answer from a finished one is the whole reason a frontend reads it.

**`permission` `timeout_ms`, with `on_timeout`.** The abandoned-approval path. Implemented as
`asyncio.wait_for` around `session.request_permission`. Two design calls worth recording:

- **`on_timeout` is its own branch, falling back to `on_deny`.** A frontend renders "you
  declined" and "nobody was there" differently, and collapsing them would have made the
  feature untestable at the level that matters.
- **No `timeout_ms` means wait indefinitely**, not some default. A gate that quietly expired
  would turn a slow reviewer into a denial nobody scripted — a mock-shaped failure, exactly
  what this rig exists to avoid.

The nicest part fell out of a2acode's own machinery rather than being designed: `wait_for`
cancels the *await*, but the request stays in the session's `_pending` map, so `is_parked`
remains true and `resolve` no-ops on the already-cancelled future. A caller who finally
answers therefore resumes into the branch that already ran — they say "allow" and get told
production is untouched. That is the honest rendering of having walked away, and it works
without a line of special-casing.

**`delay_ms` / `PLAYBACK_SPEED` under test**, which turned up a real bug: `delay_ms` on a
`permission` event was silently ignored, because `_run_events` dispatched permissions before
reaching the `_delay` call. So "think for two seconds, then ask" was unscriptable. One-line
fix (hoist the delay above the dispatch). Worth noting the shape of it: the code was written,
it just had never been *run* in that combination — which is the argument for the phase.

`serve()` grew an `env` parameter to make this testable over the wire, overlaid on
`os.environ` rather than replacing it so the child still resolves `uv` and `PATH`.

### Cancel mid-run: worse than the parked case, and not ours

The last checklist item — "cancel honored mid-delay" — does not work, and the failure is more
interesting than the feature would have been.

The existing Phase 3 finding was that cancelling a task parked on `input-required` silently
no-ops. The natural hypothesis was that a task genuinely mid-run would cancel fine, since the
a2a-sdk guard that swallows the parked case (`if not self._is_finished.is_set() and
self._producer_task`) is satisfied there. It doesn't. It's worse:

```
STREAM states:        ['working', 'working']    # stream just stops
cancel_task returned: working
later get_task:       working                   # forever
```

No terminal state is ever written. Traced through both layers:

- **a2a-sdk** (`ActiveTask.cancel`, `active_task.py` ~L733): cancels `self._producer_task`
  *first*, then awaits `self._agent_executor.cancel(...)`. The producer is the task running
  `execute`.
- **a2acode** (`executor.py`): `_pump`'s `except asyncio.CancelledError` branch is the one
  path that deliberately emits no status — it drops the session and re-raises. That branch
  was written for a *disconnected client*, where there is nobody left to tell. A deliberate
  cancel takes the same branch.
- The `updater.cancel()` inside a2acode's own `cancel()` does enqueue a canceled status, but
  by then it doesn't reach the task store; and `ActiveTask.cancel` returns the task it read
  *before* cancelling, which still says `working`. That's what the caller gets back.

So the ordering is the bug: the only component that owns the task's terminal state is killed
before it can write one. Fixable on either side — the SDK awaiting the executor's cancel
before killing the producer, or a2acode distinguishing "client vanished" from "cancelled on
purpose". Filed as taskwarrior `167506a4` (a2a-sdk, primary) and `5dcde5fb` (a2acode).

Captured as two strict xfails plus a passing test that documents today's behavior, matching
how the parked-cancel finding was recorded in Phase 3. Strict, so they flip loudly if either
side fixes it.

**This is the rig paying for itself.** The bug needs a turn slow enough to interrupt, at a
moment you can identify precisely. Against live inference that's a flaky test with an API bill
attached. Scripted, it's a 3s `delay_ms` behind a `tool_use` that fires first as the "the
driver is really running" signal — deterministic, free, ~200ms of wall clock, reproducible on
every run.

### Where it stands

70 passed, 4 xfailed against both `echo` and `playback`, under 5s each (was 50/2). All new
behavior was driven test-first; the two characterization tests for already-wired code
(`stop_reason`, the `PLAYBACK_SPEED` scaling) are marked as such in their docstrings rather
than dressed up as new.

Test structure held: the backend-agnostic suite needed zero edits again, and everything new
lives in `tests/test_playback.py`, which is playback-pinned by module marker. Two additions
worth knowing about — an `on_scenario` fixture that pools servers per (scenario, env), and a
small in-process section that drives `BackendSession` directly, because permission timeouts
and scripted failures are decided at that seam rather than on the wire.

## 2026-08-07 — Phase 5 finished: the plan capture found the plan path broken

Picked up the one thing Phase 5 left open: `plan` events, deferred on taskwarrior `fb20c22b`
because no real `TodoWrite`-derived plan had ever been observed and scripting against an
unwatched code path is how mocks start drifting. The plan was to spend ~$0.30 of inference,
watch one, and script against it.

### What the capture actually found

There is no `TodoWrite` tool to watch. a2acode's claude backend derives plans from exactly one
tool name (`_PLAN_TOOL = "TodoWrite"`, `backends/claude.py:60`), and current Claude Code
(2.1.224) doesn't put that tool in the session. It offers `TaskCreate` / `TaskUpdate` /
`TaskList` / `TaskGet` instead.

Watched it happen first, then confirmed it properly rather than trusting the model's prose. The
run itself said "No TodoWrite tool here" in its response text and went off to use `TaskCreate`;
it then completed all three requested steps over 25 turns with zero plan artifacts
(`docs/captures/phase5-plan-probe.jsonl`). The confirmation is `docs/captures/phase5-session-tools.json`
— a2acode drops the SDK's init `SystemMessage` on the floor (`events_from_message` maps only
Assistant/User/Result), so getting an authoritative tool list meant opening a session directly
with the same `ClaudeAgentOptions` a2acode builds. 29 tools, no `TodoWrite`.

So `--backend claude` cannot emit a `plan` event under any prompt. Filed as taskwarrior
`70dc7c04` with the full framing in `docs/UPSTREAM.md`.

**Why a2acode's 163 tests don't catch it:** `test_todowrite_yields_a_plan_alongside_the_tool_use`
hand-builds a `ToolUseBlock(name="TodoWrite")`. A unit test that supplies the very constant it
is testing cannot notice that constant going stale — it'll keep passing through the next rename
too. That testing-gap observation is the more useful half of the report; the rename alone is a
one-line fix.

### Getting the shape anyway, via ACP

Same prompt, same repo, `--backend acp --agent claude`: three plan updates, cleanly
(`docs/captures/phase5-acp-plan-run.jsonl`, $0.17). ACP models plans as first-class session
updates, so `acp.py`'s `_plan_content` maps them onto the same `Plan` dataclass the claude
backend was supposed to produce. That isolates the break to the claude backend — `_render_plan`,
the artifact machinery, and the executor are all healthy.

The observed wire contract, now pinned by test:

- artifact `name: "plan"`, one part, `media_type: "text/markdown"`
- **one `artifact_id` across all three updates**, `append` unset, `last_chunk: true` — reported
  by replacement, so a consumer that appended would render the same steps three times
- marks are `- [ ]` / `- [>]` / `- [x]`, matching `_PLAN_MARKS = {"completed": "x", "in_progress": ">"}`

`priority` never appeared in the real run, so it's scripted rather than assumed, and flagged as
such in the test.

### Then the scripted side needed almost nothing

The six end-to-end plan tests passed the first time they ran. playback's `plan` support (written
in Phase 4 against a2acode's dataclass) already matched the shape the wire produces — which is
the DESIGN-v3 §3 bet paying out. Emitting into a2acode's real executor means the rig gets the
artifact contract for free instead of reimplementing it and drifting.

What did need writing was validation, and it's the same class of bug as Phase 5's `delay_ms`
find:

- a `plan` with both `steps` and `markdown` was accepted, and a2acode's renderer prefers
  markdown and drops the steps **without a word**. A scenario author would ship a plan they
  never wrote.
- a step missing `content` would `KeyError` mid-turn, in front of a frontend already watching
  the stream, rather than at server start.

An empty plan stays legal — it's how an agent says it abandoned the checklist, and a2acode
replaces the artifact with nothing so a stale plan doesn't linger on screen. There's a test for
that path too, since "the plan disappeared" is a thing a frontend has to render correctly.

### Two side findings

- **`dump_stream.py` is not what corrupted the Phase 2 capture.** `phase2-claude-run.jsonl` has
  4 lines that don't parse (raw newlines inside diff strings). Both captures written this
  session parse clean at 84 and 44 events, so the mangling happened to that file after the
  fact, not in the dumper.
- **The ACP run never hit a permission gate**, while the claude-backend run gated seven times
  (needing an auto-allow loop to get through). Not chased down; noted because it means the two
  backends do not present the same approval surface to a caller, which matters if the rig ever
  claims to model "a2acode" rather than "a2acode with backend X".

### Where it stands

79 passed, 4 xfailed against both `echo` and `playback`, under 5s each (was 70/4). Test-body
separation held for the third time running: the backend-agnostic suite needed zero edits, and
everything new lives in `tests/test_playback.py`.

**Phase 5 (M1) is done.** The odd part is where it landed: a frontend can now develop plan
views against the rig that the real producer cannot currently generate. That's not the rig
drifting from reality — it's the rig holding the contract a2acode intends and upstream having
broken its own half. Which is a decent argument for the whole approach.

## 2026-08-07 — Phase 6 (M2): a directory of repos

### The repo/scenario split

Six tasks landed M2, and the first real decision wasn't in the plan: what a "fake repo" *is*.
One YAML had been carrying both an agent's identity and its script — `name:`, `card:`,
`defaults:` sitting next to `plays:` in the same file. That conflation wasn't introduced by
M2; it was inherited from DESIGN-v3 itself. §2 said "a fake repo is just a scenario file,"
while §4 defined a scenario as a list of plays. Both were true of the same document, which is
how a conflation like this survives a design review — nothing in it is wrong on its own. The
word "scenario" traces back further still, to the surveyed prior art in
`docs/pass-4-deterministic-backend.md`, where it meant a scripted transcript; a2acode's own
event vocabulary and DESIGN-v3's usage both inherited that word without separating "the
transcript" from "the thing running it."

M3 is what would have broken it, not tidiness. Recording (the next milestone) produces several
scripts per repo over time. With identity living inside the script, every recording would
restate the repo's name and description, and they would eventually disagree with each other.
Under the split, a repo's identity lives once in `repo.yaml`, and a recording is just a new
file dropped into `scenarios/` — no format change needed to accept it, and nothing to keep in
sync. `repo.yaml` now holds identity and defaults; `scenarios/*.yaml` hold `plays:` and
nothing else; the directory name is the repo id, so there is exactly one source of identity
(there is no `name:` field anywhere).

### The lifespan finding

The plan's flagged central risk was retired first, before anything else got built on top of
it: Starlette does not run a mounted sub-app's lifespan, only the lifespan of the app actually
being served. The original reasoning here was wrong and was caught in final review: a2acode's
task and push-notification stores are constructed eagerly in `build_app()`, not inside the
lifespan, so mounting N repos the naive way does not leave them answering against
uninitialized stores. What an un-run child lifespan actually costs is shutdown — its `finally`
block is what closes each backend and its push-notification client, and a lifespan that never
runs never runs that either. `src/a2a_playback/mounting.py` runs every child app's lifespan by
hand via an `AsyncExitStack`, so one process serving many repos gets the same startup-and-
shutdown symmetry a single `--repo` process gets for free.

A nice corroboration showed up sideways: the decision that the rig serves **no agent card at
the root** (`/` is the index, not a card — the rig is a directory of agents, not an agent)
broke the test harness's own server-readiness probe, which had been polling the root card path
waiting for it to come up. That the probe broke is independent evidence the 404 is real and not
just a documented intention — nobody had to assert it, the harness proved it by falling over.

### The index as the topology seam

`GET /` returns `{"repos": [{"name", "description", "card_url"}]}`, with `card_url` always
absolute. That's deliberate: a consumer reading card URLs out of the index cannot tell N repos
mounted under one process from N repos each on their own port — both look like a list of full
URLs to fetch. That's the seam that keeps the two topologies interchangeable.

The single-repo-per-process path (`--repo`) was kept rather than deleted once `--repos`
existed, and for a specific reason: it's what proves the index abstraction is honest rather
than a shape only the rig itself can serve. If `--repo` didn't exist, or produced a
differently-shaped card, "consumers read the index, not the topology" would be an assertion
about one code path instead of a property that held across two.

### A plan ordering flaw, caught during implementation

The plan sequenced repo-format and backend-wiring changes ahead of actually shipping the three
repos under `repos/`. But the task that pointed the backend and server at repos also set
`DEFAULT_REPO = repos/billing-api` in `src/a2a_rig/server.py`, and `conftest.py`'s server pool
calls `serve()` with no explicit repo argument — so `DEFAULT_REPO` gets hit on every pytest run
regardless of `--backend`, because `test_playback.py`'s module marker pins it to playback
regardless of the CLI flag. That meant the suite could not go green without a real
`repos/billing-api` existing several tasks before "ship three fake repos" was scheduled to
create it. Not a plan mistake anyone caught by reading ahead — it surfaced as a failing test
run during implementation, and got resolved by migrating billing-api early (byte-identical
`plays:` content, verified by diff) rather than reordering the whole plan.

### Verification

```
uv run pytest --backend playback   →  102 passed, 4 xfailed, 4.44s
uv run pytest --backend echo       →  102 passed, 4 xfailed, 4.82s
```

(Baseline going into Phase 6 was 79 passed, 4 xfailed.) The backend-agnostic suite
(`test_card.py`, `test_lifecycle.py`, `test_multiturn.py`, `test_permission.py`,
`test_stream.py`) needed zero edits across all six tasks —
`git diff --stat main..HEAD -- a2a-rig/tests/test_card.py a2a-rig/tests/test_lifecycle.py
a2a-rig/tests/test_multiturn.py a2a-rig/tests/test_permission.py a2a-rig/tests/test_stream.py`
comes back empty. That constraint has now held for three consecutive milestones (Phase 4, 5,
and 6), which is the evidence that the repo/scenario split landed in the right layer: protocol
behavior never had to change to accommodate how repos are organized on disk.

### Outcome

**Phase 6 (M2) is done.** 3+ fake repos, served through one process behind an index, well
inside the 5s test budget. The remaining Phase 6 bullet — building a frontend/agents against
the rig — is its own project from here, not more rig work.
