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

## 2026-08-08 — Phase 7 (M3): the scenario factory

Seven tasks, each reviewed, plus a whole-branch review and one consolidated fix wave. What
shipped: `to_scenario_event()` (the inverse of `PlaybackBackend._to_backend_event`), the
`RecordingBackend` decorator, `scrub_cwd()`, the `rig-record` CLI, and a round-trip test that
records `playback` through a real server, promotes the file, replays it, and compares.

The recording *run* is still outstanding. Everything below is what building the machinery
turned up.

### The seam is `BackendSession`, not the `Backend` protocol

The obvious place to tee events is the backend — wrap `Backend`, intercept what it returns.
That does not work, because a2acode's backends don't return events; they're handed a session
and call `session.emit(...)` and `session.request_permission(...)` against it. The events never
pass through a value the decorator can see.

So `RecordingBackend` wraps the backend only to get invoked, and the actual recording happens in
`_RecordingSession`, a proxy substituted for the real session inside `drive()`. It overrides
exactly two methods, records, and forwards the *original* objects unchanged — same event
instance, same args, bare re-raise. A recorder that normalizes on the way through would be
changing what the caller sees, which is the one thing a tee must never do.

The proxy has a documented hole worth knowing about: attribute *assignment* doesn't reach the
real session. Safe today because a2acode's only session assignment (`session.evicted = True`,
executor.py:538) happens on the real session outside `drive()`, but it's a property of the
current upstream rather than of the design.

### Two design contradictions, found by trying to implement them

**DESIGN-v3 §6 wanted "every normalized event (plus timing)" *and* a refresh loop that diffs
normalized streams.** Those fight. Wall-clock timing differs on every run, so recording it
would make every re-record diff every line, and the diff is the entire point of the refresh
loop — it's supposed to tell you what upstream changed. Timing lost. Pacing already had a home
in `repo.yaml`'s `defaults.delay_ms` and `PLAYBACK_SPEED`, which is a better one anyway: it's
authored intent rather than an accident of how busy the machine was.

**PLAN.md Phase 7 said recordings would replace hand-written scenarios.** They can't. A real
run answers a permission gate *once*, so a recorded `permission` carries `on_allow` or
`on_deny` and never both. The deny branch, the abandoned-approval timeout, and scripted
mid-stream failures are precisely the things a live run cannot be asked to produce — they're
why the hand-written scenarios exist. Recordings own the happy paths and the two compose.
"Replacing" would have traded real coverage for provenance.

Both documents are corrected. Neither error was visible by reading; both surfaced as "wait,
what do I write here?"

### The silent branch, and why it had to be fixed first

Recording only-the-taken-branch makes the unscripted branch a *common* state rather than a
scripting mistake — so the first task was making it loud. It wasn't: reaching a permission
branch a play didn't script emitted nothing and ended the turn with no `result`. A frontend
would see a task that just... stopped. That's exactly the plausible-wrong-answer this rig's
design refuses everywhere else, sitting in the code the whole time. Now it raises at runtime,
and a gate with no branches at all is refused at load.

### The ordering trap, three times in one milestone

`Match.matches` only checks the fields that are *set*. So a play with a bare `turn: 1` and no
`contains`/`regex` matches **any** first-turn prompt — it's a catch-all wearing a constraint.
And `_reject_shadowed_plays` only polices a literal `match: {}`.

That bit three times:

1. `billing-api`'s greeting play (`match: { turn: 1 }`) lived in the file that sorted first. A
   promoted `20-recorded-*.yaml` sorts after it, so a recorded first-turn prompt — the most
   common shape a capture takes — would never reach its own play. It would answer "Ready when
   you are." No error. That would have surfaced *after* the recording run.
2. Then the same class one layer over: recorded plays sorted *behind* `billing-api`'s
   hand-written `contains: "run the tests"` and `contains: "explain"` plays. The spec's own
   runbook asks for a prompt that hits a permission gate, and the round-trip test's phrasing is
   "add a /health endpoint and run the tests" — a direct collision.
3. And the guard that would have caught it can't be written the obvious way. "A play with no
   `contains`/`regex` must be last" would reject `90-greeting.yaml`, which is legitimately
   broad and legitimately not last.

The resolution was a naming ladder rather than a validator: `20-*` recorded, `30-*`
hand-written specifics, `90-*` broad fallbacks, `99-default.yaml` catch-all. Recordings sort
**first**, which is the substantive call — an imagined play shadowing a real recording of the
same prompt is backwards. What makes that safe in both directions is that each side already has
a detector: a new `recorded.prompts` self-check re-selects every recorded prompt at load and
catches a recording being shadowed, and `conftest.py`'s `permission_prompt`/`denied_marker`
fixtures depend on `billing-api`'s hand-written gate, so a recording that ate that prompt turns
the existing suite red.

The general validator is still unwritten and is an M4 design question: what does "over-broad"
mean, when a broad play sorting early is sometimes exactly right?

A related discovery, unfixed: `conftest.py`'s `reply_marker` asserts "Ready when you are",
which *both* the greeting play and the catch-all contain. That's why renaming and splitting the
scenario files tripped no failure — the fixture can't tell which play answered it. A fixture
that passes against two different plays cannot detect a play-ordering regression.

### What the round-trip test turned up

Mostly: no serializer disagreement. `to_scenario_event` and `_to_backend_event` agree across
the full gated-play vocabulary, including `plan`, `file_change`, and nested branch events, and
the strengthened assertions passed against the code as built rather than forcing changes.

The interesting failures were in the *test*, and they're the kind that pass while proving
nothing:

- The replay-side `serve()` calls omitted `backend="playback"`, and `serve()` defaults to echo.
  The keystone test would have been green while replaying the promoted repo through a2acode's
  real echo backend.
- `assert "on_allow" in permission` passes for an *empty* branch — exactly the unreplayable
  recording that `write()` warns about.
- Asserting on the failure *message* proves nothing, because executor.py collapses every
  backend exception to the same "Claude Code run failed; see server logs." The useful assertion
  is that the replay parks in `input_required` first.

### Durability, and what "don't lose the run" actually means

`write()` rewrites the whole file after **every turn**, not at shutdown, so a ctrl-C keeps what
already happened. Three rulings fell out of that:

- **Write first, validate after, warn on stderr.** Not raise — that would kill a live session
  over a file already safely on disk. Not drop the offending node to force a clean load — the
  gate happened, and deleting it fabricates a run that didn't occur. A recording that can't
  replay is a worse outcome than a clean file only if you'd rather have nothing.
- **`except BaseException`, not `except Exception`**, so a cancel doesn't drop the in-flight
  turn. This one had a trap the reviewer caught: fixing it *alone* makes things worse, because
  `str(CancelledError())` is `""` and `scenario.py` rejects an empty error message — turning a
  lost turn into a corrupt file. The empty-message guard had to land first.
- **Atomic replace**, since a non-atomic whole-file rewrite can truncate and lose every prior
  turn, which is the exact outcome writing-every-turn exists to prevent.

Still open, deliberately: restarting `rig-record` against an existing `--out` starts from an
empty play list and silently rewrites the file. Use a fresh `--out` per run. It's documented in
the README rather than guarded in code.

### The run doesn't cost money

I framed the recording run as "the paid run" throughout this milestone and was wrong about it.
The `claude` and `acp` backends spawn the `claude` CLI and inherit its subscription login;
`Result.cost_usd` *reports* a figure (0.30 and 0.24 on the Phase 2 turns) without billing it.
What the run costs is time and a rate-limit slice.

The related upstream nit is real, though, and is now in `docs/UPSTREAM.md`: `ACPBackend` takes
no `max_budget_usd` at all, while `ClaudeBackend` does. Combined with the `TodoWrite` finding,
the only backend that can record a `plan` is the one with no cost ceiling. `rig-record` rejects
`--max-budget-usd` on the acp path rather than accepting a flag it can't honor.

### Verification

```
uv run pytest --backend playback   →  165 passed, 4 xfailed, 7.86s
uv run pytest --backend echo       →  165 passed, 4 xfailed, 8.66s
```

(Baseline going into Phase 7 was 108 passed, 4 xfailed.) The backend-agnostic suite needed zero
edits across all seven tasks — fourth consecutive milestone. That constraint is the standing
signal that changes are landing in the right layer; the day it needs edits is the day to stop
and reassess rather than edit it.

### Outcome

M3's machinery is done and merged. Phase 7 is not closed: the recording run itself is still
outstanding, and it has to be `--backend acp` (the claude backend can't emit plans), driven by
hand with a client in a second terminal, hitting at least one real permission gate and
approving it so a recorded `on_allow` exists. Avoid prompts containing "run the tests" or
"explain" — those belong to `billing-api`'s hand-written plays.

## 2026-08-08 — the recording run

Three runs, not one. The first produced a usable recording with no permission gate in it, the
second failed to authenticate, and the third is what got promoted:
`a2a-rig/repos/billing-api/scenarios/20-recorded-health.yaml`, one play, a real gate, answered.

Prompt was `add a /health endpoint to app.py` against `~/scratch/demo-app` at `6890fd7`, via
`rig-record --backend acp --agent claude`. Reported cost across all runs was about $0.24, which
as established isn't billed.

### `permissions.defaultMode` is why the first run had no gate

The first recording ran clean and captured nothing to approve: Claude edited `app.py` without
asking. The plumbing was never the problem — a2acode parks correctly on
`session/request_permission` (`acp.py:362`) — the agent simply never called it. The cause was
`permissions.defaultMode: "acceptEdits"` in this machine's `~/.claude/settings.json`, which the
ACP adapter reads and hands to the Agent SDK (`acp-agent.js:935` →
`resolvePermissionMode`).

This is the sharpest operator lesson of the milestone, because nothing about it is visible in
the recording. A gateless recording looks exactly like a run that had nothing to approve. The
rig's whole premise is that a consumer develops against the real `input-required` machinery,
and the recording that was supposed to demonstrate it had been silently flattened by a personal
config setting three directories away from anything in this repo.

### The `CLAUDE_CONFIG_DIR` dead end, and what it accidentally proved

First attempt at a fix was `CLAUDE_CONFIG_DIR` pointed at a clean-room config directory
(`acp-agent.js:11` reads it; `settings.js:74` resolves user settings under it). Credentials on
this machine live in the macOS Keychain rather than `~/.claude/.credentials.json`, which looked
like it made the swap safe. It didn't: Claude Code also needs auth state inside the config
directory, and the run died with `acp.exceptions.RequestError: Authentication required`.

The accident is the useful part. `RecordingBackend` caught the failure and wrote it:

```yaml
plays:
- match: { regex: ^add\ a\ /health\ endpoint\ to\ app\.py$ }
  events:
  - error: Authentication required
```

Write-every-turn and `except BaseException` were both designed against imagined failures — a
ctrl-C, a crash mid-write. This is the first time a real unplanned failure hit that path, and
the turn landed on disk instead of evaporating. Kept as `scratch/rec-health-2.yaml`.

The fix that worked is narrower and leaves auth alone entirely: a **project-level**
`.claude/settings.json` in the agent's `--cwd` with `permissions.defaultMode: "default"`.
Precedence is user → project → local → enterprise, last writer wins on `defaultMode`
(`settings.js:124-129`), so the project file overrides the user's `acceptEdits` without
relocating anything. `~/scratch/demo-app/.claude/settings.json` now carries it, and re-recording
depends on it staying there.

### What the recording proves that the round-trip test couldn't

The keystone test records `playback` through `playback`, which pins the serializer but says
nothing about a live agent. This run pins three things it couldn't:

- **`scrub_cwd` fires on a real permission payload.** The live gate carried
  `file_path: /Users/…/scratch/demo-app/app.py`; the recorded and replayed gate carries
  `./app.py`. The Important-3 fix from the recording-backend task, confirmed against real
  traffic rather than a fixture.
- **Only the taken branch exists.** The recorded `permission` has `on_allow` and no `on_deny`.
  Replaying the same prompt and answering `deny` produces `failed`, not an invented answer.
  That is the "compose, don't replace" rule holding on real output.
- **The gate replays as a gate.** Serving the promoted repo and sending the recorded prompt
  parks in `input_required` with the tool name and args on the status metadata, then `allow`
  resumes to `completed` with the recorded diff and response text.

### What it did not capture

- **No `plan` event.** The acp backend was chosen precisely because `--backend claude` can't
  emit one (`70dc7c04`). The task turned out too small for Claude to build a plan at all, so
  the plan path is still unexercised by a recording. A bigger prompt would be needed, and it
  is not obvious a prompt can be written that *reliably* provokes a plan — which is the same
  "whether a real model does X is a judgment call" problem that motivates hand-written plays.
- **Tool arguments are dropped.** Every recorded `tool_use` has `input: {}` and every
  `tool_result` has `name: ''`, and tool names arrive as UI labels (`Read File`, `ToolSearch`)
  rather than tool ids. The permission payload carries full args, so the loss is specific to
  the tool-call events. Filed in `docs/UPSTREAM.md`.
- **Two `file_change` events for one `Edit`**, one before the gate and one inside `on_allow`,
  with different diffs — ACP streams a preview and then the applied change. Left in, because
  it is what the real backend emits and a consumer has to tolerate it.

One thing was scrubbed by hand beyond what `scrub_cwd` does: a `<system-reminder>` block that
Claude Code embeds in `Read` output rode along inside a `tool_result`. It is harness-internal
text, and a consumer replaying the recording should see the file, not the agent's instructions
to itself.

### Verification

```
uv run pytest --backend playback   →  165 passed, 4 xfailed, 7.37s
uv run pytest --backend echo       →  165 passed, 4 xfailed, 8.33s
```

Plus an out-of-suite replay against a live `rig-serve`: parks → `allow` → `completed` with the
recorded diff and text intact, and `deny` → `failed`. Play order after promotion is the
documented ladder — recorded `20-*` first, then `30-refactor.yaml`'s two `contains` plays, then
`90-greeting.yaml`'s `turn: 1`, then `99-default.yaml`. The suite needed zero edits again.

### Outcome

The library's backbone now has one real recording in it, and the record → scrub → promote →
replay loop has been run end to end by a human-driven client against live inference. PLAN's
Phase 7 bullet asks for **three or more** recorded scenarios, so it stays unchecked: one
recording is a proven pipeline, not a backbone. What is settled is that the pipeline works and
what it costs to run — the remaining recordings are repetition, not risk.

## 2026-08-08 — two more recordings, and the two things they corrected

Closed Phase 7's `>=3 recorded scenarios` bullet. The previous entry called the remaining
recordings "repetition, not risk." That was wrong twice over, and both corrections are worth
more than the checkbox.

### The recordings

`20-recorded-crud.yaml` — "add a DELETE route and a POST route that 400s on a missing name,
plus pytest coverage." Three permission gates, all answered `allow`, five `file_change` events,
and **three `plan` events**.

`20-recorded-planmode.yaml` — "add pagination to /items with limit and offset, and document it
in README.md", recorded with `permissions.defaultMode: "plan"` in the agent's `--cwd`. One gate,
answered `deny`. It is the only recording in the library carrying an `on_deny` branch.

Both replay clean against a live `rig-serve` (recorded branch reproduces, unrecorded branch
raises), suite unchanged at **165 passed / 4 xfailed** on both backends. Sixth consecutive
milestone needing zero suite edits.

### Correction 1: plan events are a function of task size, not permission mode

The open question was whether a prompt could be written that *reliably* provokes a `plan`. The
guess going in was that Claude Code's plan mode would be the lever. It is not, and the reason
is that "plan" names two unrelated things:

- **a2acode's `plan` event comes from `TodoWrite`.** `acp-agent.js:1438-43` maps a `TodoWrite`
  call to ACP's `sessionUpdate: "plan"`, and a2acode's `acp.py:147` maps that to `Plan`. The
  phase5 capture's checkbox list with `[>]`/`[x]` markers is a todo list wearing the word plan.
- **Plan mode produces an `ExitPlanMode` permission request** (`acp-agent.js:707`), which lands
  as a `PermissionRequest`. It never produces a `Plan` at all.

So the health recording had no plan because a one-step task never bothers with a todo list, not
because of any mode setting. The three-part CRUD prompt produced three, and they show the plan
being *revised* mid-turn — steps merging, statuses walking `pending → in_progress → completed`.
That is the by-replacement semantics DESIGN-v3 §6 describes, now with a recording behind it
rather than a hand-written play asserting it.

The knob is task size. Ask for three things.

### Correction 2: a denied gate does not always fail the task

This is the one that would have bitten a consumer. `acp.py:371` runs every decision through
`select_option`, which prefers one-shot over sticky. ExitPlanMode offers three options, so:

- `allow` → `allow_once` → optionId `default` ("yes, and manually approve edits")
- `deny` → `reject_once` → optionId `plan` ("**no, keep planning**")

Denying this gate does not stop the agent. It ends the turn `completed`, with the agent asking
"What would you like to change about the plan?" — the revision happens on the *next* turn.
Every hand-written deny in this repo ends `failed`, and the Phase 7 note asserting that
hand-written plays own the deny branch had quietly assumed that was the only shape. A frontend
built against the hand-written plays alone would treat deny as terminal and be wrong.

Worth noting what got flattened on the way: a2acode's `PermissionDecision` is `allow: bool`
(`base.py:114-120`), so ACP's three options collapse to two before they ever reach A2A. The
caller cannot express "yes, and auto-accept the rest." For an edit gate that is a fair
simplification; for ExitPlanMode the option ids are distinct *modes*, not styling. Logged in
UPSTREAM.md.

### Two operator hazards the runs surfaced

**The agent inherits the recording harness's environment.** The first CRUD attempt was driven
from a sandboxed shell, and the ACP-spawned Claude Code inherited the sandbox: `EPERM` on every
`Edit`, `EPERM` on `mkdir '~/.claude/session-env/...'`, and a turn that ended with the agent
asking the operator to debug their own `~/.claude` permissions. Nothing was written to the
fixture. The recorder captured all of it faithfully, which is the second time write-every-turn
has paid for an unplanned failure. Re-run outside the sandbox, it worked first try.

The milder form survives into the good run: the agent's `pytest` resolved to
`a2a-rig/.venv/bin/python`, because `rig-record` is launched from `a2a-rig/` and the spawned
agent inherits `VIRTUAL_ENV`. Harmless to the scenario, but it puts the harness's own checkout
path into recorded tool output.

**`scrub_cwd` only covers the agent's `--cwd`.** Both recordings leaked absolute paths from
*outside* it, in different shapes: the harness virtualenv above, and — in plan mode — the plan
file Claude Code writes to `~/.claude/plans/<random-slug>.md`, which appears in `planFilePath`,
in a `file_change.path`, and inside a diff header. Neither is reachable by the existing
scrubber's rule. Handled by a generalized `scrub_promote.py` that applies caller-supplied
redactions to every string in the document and then **refuses to write if any `/Users/` path
survived** — a check worth having permanently, since both leaks were found by looking rather
than by any error.

### Outcome

Three recordings, covering three shapes the hand-written plays could not assert on their own: a
gated edit run, a multi-gate run with an evolving plan, and a deny that completes. The
"recordings own the happy paths, hand-written plays own deny" split from the last entry is no
longer accurate — recordings own whichever branch the live run took, and a live run can be
*steered* to the interesting one by choosing the mode before recording.

## 2026-08-08 — the first upstream issue

Nine findings had been sitting in `docs/UPSTREAM.md` with zero filed. Filed one:
[kanywst/a2acode#37](https://github.com/kanywst/a2acode/issues/37), the dead `plan` events in
the claude backend (`70dc7c04`).

It went first for the reason UPSTREAM's filing order already argued: it's the only finding in
the pile that's a plain feature-is-broken report instead of a design conversation. Everything
else either waits on the a2a-sdk cancel answer or opens with "is this deliberate?", and a first
contact that starts an argument about intent is a worse opening than one that starts with a
tool-list dump.

Every claim in the body was re-verified against source before posting rather than trusted from
the notes: `_PLAN_TOOL = "TodoWrite"` still at `backends/claude.py:60` on v0.6.2, the synthetic
`ToolUseBlock(name="TodoWrite")` still at `tests/test_claude_backend.py:104`, 29 tools in
`phase5-session-tools.json` with no `TodoWrite`, 0 plan events in the probe against 3 in the ACP
run. The notes held up, but the point of writing the issue from the code is that a month-old
note is a claim, not evidence.

Two judgment calls worth recording:

- **The offer to send a PR got cut.** The draft ended with one. The fix isn't a drive-by:
  `TodoWrite` carried the whole list per call while `TaskCreate`/`TaskUpdate` mutate one task at
  a time, and `Plan` is emitted by replacement, so a real fix has to hold list state across
  calls. Offering the patch commits to that work before knowing whether the maintainer even
  wants the event shaped that way. It can be offered after they respond.
- **The version got noted, not chased.** The capture ran on Claude Code 2.1.224 and this machine
  is on 2.1.226. Re-running the dump to make the number current would have been cheap, but the
  report is about a rename that already happened, so the honest move was stating what was tested
  and what wasn't rather than implying a fresher run than actually happened.

Body kept at `scratch/issue-70dc7c04.md` so the next one has a shape to copy.

## 2026-08-08 — the cancel pair, filed with a repro in someone else's suite

Filed [a2a-python#1170](https://github.com/a2aproject/a2a-python/issues/1170) covering both
cancel findings, with [#1171](https://github.com/a2aproject/a2a-python/pull/1171) adding two
`xfail(strict=True)` scenarios to a2a-python's own `tests/integration/test_scenarios.py`.

The repro living upstream was Josh's call, and it changed the report's whole footing. Our
repros are strict xfails in the rig, which a maintainer can't run without checking out the rig,
installing a2acode, and trusting a playback backend they've never seen. Rewritten against a
stub `AgentExecutor` in their suite, the same bugs reproduce in 0.22s with no a2acode, no rig,
no API key. It also removes the "sounds like a2acode's bug" deflection before anyone can reach
for it. Their suite already uses `xfail(strict=True, reason=<issue url>)` (see #869), so this
is their idiom rather than ours imposed.

**One issue, not two.** That question rode six handoffs. The case for two was that the parked
finding opens a design debate while the stranding one doesn't. It dissolved once V1 turned out
to guard *both* paths: both became plain regressions under one headline. The parked test then
asserts only "the state must not come back unchanged", so cancelling *or* raising
`TaskNotCancelableError` both pass and the design question never has to be litigated.

**Three things the notes had wrong or missing.** Writing the issue from source rather than from
`UPSTREAM.md` is what caught them:

- **The stale-read mechanism was wrong.** The notes said `ActiveTask.cancel` returns the task it
  read before cancelling. It doesn't: it re-reads at `active_task.py:753` after
  `await self._is_finished.wait()`. The return is fresh, and it says `working` because the store
  was never written. Right observed behavior, wrong cause, and it would have been a public
  correction if it had shipped.
- **A parametrized V1-vs-V2 test does not work.** The tempting move was one test on their
  existing `use_legacy` fixture, passing on legacy and failing on v2, so the regression *is* the
  test. With an empty `cancel()` legacy doesn't pass, it hangs: `on_cancel_task` waits in
  `consume_all` for an event that never arrives. Both handlers are broken, differently. Shipped
  V2-only.
- **Their own test papers over it, with a maintainer's TODO attached.**
  `test_scenario_cancel_working_task_empty_cancel` passes only because its executor
  hand-enqueues the `CANCELED` event, under a literal
  `# TODO: this should be done automatically by the framework ?`. Same shape as the `TodoWrite`
  finding one entry up: a test that supplies the thing it is testing. Two for two on that
  pattern now, which is starting to look like the most reliable smell in this whole exercise.

Also confirmed both bugs alive on `main` (`cff6727`), not just the v1.1.2 pin: `active_task.py`
and `default_request_handler.py` are byte-identical to the tag.

Mechanics worth remembering: the first attempt put the tests in a new file importing
`tests.integration.test_scenarios`, which collided with pytest's own collection of that module
and produced 111 errors across the suite. Appending to `test_scenarios.py` (where a PR would
want them anyway) fixed it. `ruff format` also reformatted the addition, so check
`ruff format --check` against the baseline before assuming a diff is yours.

The `Claude-Session:` commit trailer got stripped from the upstream commit at Josh's call. It
points at a private session URL, which is fine in this repo and not fine in someone else's.

### Cleanup pass after the two filings

Went looking for what the filings invalidated. Five things:

- **`test_playback.py`'s comment repeated the wrong mechanism.** The stale-read claim wasn't
  only in UPSTREAM.md, it was in a checked-in code comment at the xfail. Corrected in place,
  with a note saying what it used to say, since that comment is the first thing anyone reads
  when the xfail flips.
- **Both rig xfail `reason=` strings now cite `a2a-python#1170`** instead of describing the bug
  in isolation. When one of these flips in CI, whoever sees it gets a link to the conversation
  rather than a sentence they have to go re-derive.
- **UPSTREAM.md pointed at `scratch/` for three issue bodies.** `scratch` is gitignored (via
  `~/.gitignore`), so a checked-in, pushed doc was citing paths that exist on exactly one
  machine and vanish on cleanup. Replaced with the GitHub URLs, which are the durable copy
  anyway. Rule added to CLAUDE.md.
- **CLAUDE.md's UPSTREAM bullet said the notes exist so an issue "can be written later without
  re-deriving it."** That is exactly backwards and this session is the evidence: re-deriving is
  what caught the bad mechanism *and* found the framing that made both reports land. Rewritten
  to say an entry is a lead, not a verified claim, plus what to record after filing.
- **`a2a-python` is a new sibling checkout** and CLAUDE.md's "where the rest of the code lives"
  didn't know about it. Added, with the two things that cost time: their CI enforces
  `ruff format`, and the fork/branch/PR arrangement.

Rig suite still 165 passed / 4 xfailed, so none of the comment edits moved behavior.

### The habit that came out of it

Promoted "look for the test that supplies the thing it's testing" to a standing habit in
UPSTREAM.md, alongside lead-with-the-diff. Two for two: a2acode's plan test hand-builds
`ToolUseBlock(name="TodoWrite")` and a2a-python's cancel test hand-enqueues the `CANCELED`
event. Both bugs survived large green suites *because* of their tests.

The practical version: when a finding seems too obvious to have gone unnoticed, go read the
test that should have caught it. That's usually where the report gets good, because it turns
"you have a bug" into "you have a blind spot", which is the more useful thing to hand a
maintainer, and it's the half neither issue would have had otherwise.

Also fixed a line in UPSTREAM.md's preamble that survived the last pass and contradicted the
new one: it promised writing an issue "shouldn't mean re-deriving any of it a month later."
An entry saves you the hunt, not the verification.

Filed `3bbf57b5` for the other half of Josh's point: contributing the check upstream rather
than only filing the bugs it finds. Two shapes, an integration test in a2acode asserting the
session offers whatever tool a backend keys on (already suggested inside a2acode#37), and
proposing it as testing guidance in the repos' own contributor docs.

## 2026-08-08 — the third filing, and a premise that had gone stale

Filed `5dcde5fb` as [a2acode#38](https://github.com/kanywst/a2acode/issues/38). Third of nine
out. Started by answering a scoping question from Josh, and the answer changed the issue.

### The question: are #38 and #1170 dependencies?

No, and they aren't halves of one story either, which is how the handoff had them framed
("a2acode's half of the same cancel story"). They're independent fixes for the same symptom
and each one alone is sufficient:

- a2acode's `AgentExecutor.cancel` (`executor.py:471`) **already** calls `updater.cancel()`.
  The terminal state isn't missing from the code, it just arrives after `ActiveTask.cancel`
  has killed the producer and the producer's `finally` has closed the queue. Fix the SDK
  ordering (#1170) and a2acode needs no change.
- Conversely, emitting from inside `_pump`'s CancelledError branch gets the event into the
  queue *before* that close, and `close(immediate=False)` is graceful, draining what's already
  enqueued (`event_queue.py:194-196`). Fix a2acode and the SDK ordering stops mattering.

So the honest reason to file it was never "it completes #1170." It's that a2acode is broken
today and can fix itself without waiting on another repo's maintainers.

### The re-derivation earned its keep again, third time running

The entry said the branch "was written for a *disconnected client*, where there is genuinely
nobody left to tell — a reasonable call." Under `a2a-sdk` 1.1.x's V2 handler that's false, and
so is the "timed out" half of the code comment:

- The producer is a detached `asyncio.create_task` (`active_task.py:490`), so an HTTP client
  disconnecting doesn't cancel it. Subscriber teardown runs `_maybe_cleanup`, which no-ops
  unless `_is_finished` is set (`:815-819`); mid-run it isn't, so the run finishes normally.
- No timeout mechanism exists at all. Zero hits for `wait_for`/`timeout` in `active_task.py`.
- Only two things cancel the producer: `ActiveTask.cancel` (`:733`) and `aclose()` (`:790`,
  shutdown, queues already closed `immediate=True`).

Confirmed a2acode is on that path (`DefaultRequestHandler = DefaultRequestHandlerV2`).

That inverts the framing from "a case they hadn't hit" to "the comment names a case that
stopped reaching this branch, and the only case that does reach it is handled wrong." Stronger,
and it's a blind-spot story rather than a you-have-a-bug story.

Worth noting what kind of error that was. The stale-read claim last time was a wrong mechanism
to avoid shipping. This one was wrong *and* sitting on top of the better framing. Re-deriving
isn't damage control, it's where the report gets good. Preamble updated to say so.

### The blind-spot smell generalized

Three for three, but in a new shape: not a test that supplies the thing it's testing, no test
at all. `tests/test_executor.py` has zero hits for `.cancel(` or `CancelledError`, so
`AgentExecutor.cancel` and this branch are uncovered. The cancel tests that exist
(`test_smoke.py`, `test_acp.py`) all sit at the `BackendSession`/ACP layer. Cancel is well
covered as "does the backend stop," never as "does the task close out."

So the habit is really two questions: what does the test fake, and what layer does coverage
stop at? Either one explains how a finding survived a green suite.

### Josh's catch: don't hand them the out

The first draft closed with "if #1170 lands, a2acode is fixed without any change here." True,
disclosed in good faith, and an argument for closing the issue as someone else's problem. We
have no read on the SDK maintainers' turnaround, so leaning on their fix is betting on an
unknown.

Rewritten to cross-reference #1170 for the full picture, say plainly that the timeline is
unknown, keep the double-emit disclosure (redundant, not conflicting), and end on the direct
assertion: *this is a2acode's terminal state to write, and right now nothing writes it.*

Generalizes past this issue. When a finding overlaps someone else's bug, disclose the overlap
and don't editorialize it into a reason to defer.

### Filed with one claim untested, and said so

The suggested fix (`await updater.cancel()` before re-raising) is reasoned from the graceful
close semantics, never run. The issue hedges it explicitly rather than asserting it, and offers
a PR plus a protocol-level cancel test if the maintainer likes the shape. Josh's call to file
as-is rather than verify first; the alternative was a rig run to flip the existing strict-xfail
cancel test, which is still the obvious follow-up if #38 gets traction.

Nothing in the rig changed this session. Suite untouched at 165 passed / 4 xfailed.

## 2026-08-08 — the fourth filing, and a pair that split

Filed `f010f63e` as [a2acode#39](https://github.com/kanywst/a2acode/issues/39). Four of nine
out. Went in to file it *with* `438d9c1c` as a pair of small nits, per the filing order, and
re-deriving both broke the pairing.

### `f010f63e` came back a bug, not a nit

The entry had it as "the caller's deny text is dropped, small, a nit rather than a bug." The
dropping is real, but the note missed why it matters: the feature is built end to end and one
line makes it unreachable.

- `PermissionDecision.message` is a real field (`base.py:120`)
- `claude.py:197` sends `PermissionResultDeny(message=decision.message or "Denied by A2A caller")`
- `acp.py:444` raises `auth_required({"reason": decision.message or "terminal denied by the A2A caller"})`

Three sites written to pass caller text through when it's there. And `executor.py:506` hardcodes
`"Denied by A2A caller"`, which is character-for-character `claude.py:197`'s own fallback. The
executor supplies the default the backend would have supplied anyway, so the `or` branch is dead
code and the caller's text can never arrive.

That one observation carries the entire issue. It converts "would be nice" into "this was
clearly meant to work," which is a much easier yes for a maintainer.

Also caught a trap in the fix: `executor.py:501` does `.strip().lower()` for the allow-word
match, so the naive `message=text` patch hands the agent casing-flattened guidance. The fix
needs the raw input.

### `438d9c1c` went the other way, and its fix shape was wrong

The entry said `ClaudeBackend` "takes `max_budget_usd` and enforces it," and that the fix was to
plumb it through `ACPBackend` the same way. Neither holds. `ClaudeBackend` sets
`options.max_budget_usd` (`claude.py:184-185`) and lets the Claude Agent SDK enforce it. a2acode
enforces nothing itself.

ACP is a generic protocol fronting arbitrary agents and has no equivalent knob, so there is
nothing to plumb. A ceiling there means a2acode enforcing one itself: watch `cost_usd` (tracked
at `acp.py:316`/`:358`, so the data exists), abort mid-turn when it crosses, decide what task
state that lands in. Feature with design questions, not a nit.

The old "possibly deliberate" hedge was right for a better reason than the note knew. Not that
ACP costs philosophically aren't a2acode's to enforce, but that ACP hands a2acode no mechanism
to enforce them the way Claude does.

### The split, and the test that generalizes it

The filing order paired these as "same repo, same size, same *is this deliberate?* shape."
Re-deriving falsified all three. Bundling a one-line bug with a clear answer into an
open-ended feature request buries the one that can be said yes to.

That's the same call UPSTREAM already made to keep `e653db90` (dropped tool-call arguments) out
of the nit bundle, so it's now written down as a standing test: **does bundling bury the item
that has evidence attached?** If yes, split. Right twice, and both times the instinct arrived
before the reasoning.

### What re-derivation actually does

Worth stating plainly, because three sessions of filings have made it concrete: re-derivation
changes *severity and scope*, not just facts. This session it upgraded one finding and
downgraded another in the same pass. Sizing either from the note alone would have been wrong,
in opposite directions, and the ordering decision that followed from the sizes would have been
wrong too.

### Blind-spot smell, four for four

Purest instance yet. `tests/test_acp.py:293-300`'s `_FakeSession` takes a `PermissionDecision`
in its *constructor* and hands it back, so every permission test bypasses `Executor._decision`.
The deny path is thoroughly covered below the executor and never through it.

Framing note that's now in UPSTREAM, because it's why these land: say the tests are good tests
of the thing they actually target, and that they just construct the input themselves. Same
finding, no implied sloppiness. "You have a blind spot" keeps outperforming "you have a bug."

Rig untouched again. Suite still 165 passed / 4 xfailed. Taskwarrior at 16 pending.

## 2026-08-08 — the fifth filing, and the one that needed a wire trace

Filed `e653db90` as [a2acode#40](https://github.com/kanywst/a2acode/issues/40). Five of nine
out. This is the entry where re-derivation first said *don't file*, and then a capture said
*now you can*.

### The note was wrong three ways at once

The symptom was real (`tool_use` events carry `input: {}`), but everything explaining it was
wrong:

- **The proposed fix was already implemented.** The note said to carry `raw_input` into the
  `ToolUse` event. `acp.py:134` already does `tool_input=_as_dict(update.raw_input)`.
- **The evidence was an invalid inference.** "The permission event from the same turn carries
  the full `Edit` payload, so the data is available at that layer." That payload arrives via
  `request_permission(tool_call: s.ToolCallUpdate)`, a *different protocol message* from the
  `session/update` notification the mapper handles. One carrying args says nothing about the
  other.
- **The third claim wasn't a bug at all.** "Tool names should be ids (`Read`) not labels
  (`Read File`)" is asking ACP's `title` field to stop being what it is. `ToolCall` has no
  underlying tool-name field to prefer.

So the honest verdict mid-session was: not fileable, and two-thirds of it may not even be
a2acode's. What was missing was evidence about what the *agent* sends, and no existing capture
had it, because every recording we own is post-mapping. They show a2acode's output, not its
input.

### The capture, and what it settled

Thirty lines: `scratch/acp-tee.sh` puts a `tee` on both directions of the agent's stdio,
`scratch/acp_trace.py` drives one turn through `ACPBackend` directly against
`~/scratch/demo-app`. One file read produced this:

| # | `sessionUpdate` | `title` | `status` | `rawInput` |
|---|---|---|---|---|
| 1 | `tool_call` | `Read File` | `pending` | `{}` |
| 2 | `tool_call_update` | `Read app.py` | absent | `{"file_path": "/.../app.py"}` |
| 3 | `tool_call_update` | absent | absent | absent |
| 4 | `tool_call_update` | absent | `completed` | absent |

ACP streams one tool call across several messages, each refining the last. The agent announces
before it has parsed arguments, fills them in on message 2, completes on message 4. Absent
means *unchanged* (`ToolCallUpdate`'s fields are all `Optional = None`, documented "Update the
raw input", "Update the human-readable title").

a2acode reads `raw_input` only on message 1, where it is genuinely `{}`, and its
`ToolCallProgress` branch (`acp.py:139-143`) never reads `raw_input` at all. **The arguments
arrive and get dropped on the floor.** Same cause for `name=''`: `_tool_results` reads
`update.title` on message 4, absent because unchanged.

So it *is* a2acode's bug, definitively, and both symptoms are one root cause. Two gotchas the
run itself taught: the tee needs **absolute** paths (the agent subprocess runs with `--cwd` as
its working directory, so relative ones silently write nowhere and you get an empty trace with
no error), and the `BackendEvent` dataclasses use `slots=True`, so `vars()` raises and you want
`getattr`.

### Two things the capture found that no note had

- **The two symptoms are not the same size.** `executor.py:349`/`:358` stash tool names by id
  and fall back to them, so the empty `name` never reaches an A2A client. The arguments have no
  such fallback. Treating them as equal halves was wrong.
- **The best detail in the issue.** `_describe_tool` (`executor.py:207-217`) reads
  `tool_input`, so a `Bash` call over ACP renders as literally `$ ` with no command. And its
  comment says "ACP names a tool call with a human title that often already says the path
  (`Write calc.py`)" — which describes *message 2's* refined title, the one a2acode drops in
  favour of message 1's `Read File`. Someone wrote a fallback for exactly this case and the
  mapper never gives it the chance. That is evidence the loss already tripped someone, which
  beats any amount of me asserting it matters.

### Josh's question: what do we mean by "purity"?

The draft leaned on the word as though it explained itself. It doesn't, and the property that
matters here is narrower: **the function has no memory of the previous message.** That is what
makes it unable to attach message 2's arguments to a call emitted from message 1.

Asking also caught a real imprecision. The draft had offered "pass the state in as an argument
to keep it pure," which keeps it *testable* but not pure, since mutating a caller's dict is a
side effect. The rewritten suggestion leaves `events_from_update` alone entirely and merges
*before* calling it, in `session_update`, which already owns per-connection state. The mapper
is untouched and its tests don't move. Better fit for their design, and a better thing to hand
a maintainer than "relax your invariant."

"Pure" now appears exactly once in the issue, inside their own quoted docstring.

### The habit this adds

When a claim is about what someone *else* sent you, only the wire can answer it. Reading your
own source can prove what you did with the data; it cannot prove what arrived. `e653db90` sat
in the notes for a day looking confidently filed-ready and was unfileable, and thirty lines of
`tee` turned it into the best-evidenced of the five.

Rig untouched again. Suite still 165 passed / 4 xfailed. Taskwarrior at 15 pending.

### Edited #40 after filing: an eliminative header and a shipped tell

Josh read the filed issue and said the "Why I don't think this is a careless bug" section
missed. He was right on two counts, and a rescan found a third.

**The header was eliminative.** It defines the section by what the bug isn't, and it plants
"careless" in the maintainer's head purely so the next sentence can reassure them about it.
Nobody had raised carelessness until we did. That's squarely against the standing "direct
assertion over elimination" rule, and it's the one place in three issues where the framing
turned on a verdict about someone's competence rather than about their code.

**It opened with a compliment sandwich.** "That's a good property and it's why the mapper is
pleasant to test," immediately before explaining what's broken. The generous framing that
actually worked in #37 and #39 was plain accurate description ("those are good tests of the
thing they actually target"). It lands because it's true, not because it's kind, and it says
nothing about the author. Announcing charity reads as technique.

**And `Worth noting` shipped, a filler transition on the skill's own list.** Worse than the tell
is how it got through: the earlier scan grepped `it's worth noting`, the form shown in the
skill's summary, and reported all three drafts clean. The bare form is only in
`references/anti-ai-tells.md`, which never got opened. A self-check built from the same
incomplete memory as the draft cannot catch what the memory is missing.

Rewrote the section as "The mapper has no memory, by design", cut the compliment and the filler,
and pushed it with `gh issue edit 40`. Verified against the live body. Full rescan of the
revised draft against the complete reference is clean.

Two habits out of it, both now in UPSTREAM.md: **invoke `writing-voice` per issue, not once per
session** (#38 invoked it, #39 and #40 coasted on context, #40 is where it showed), and
**actually read the anti-tells reference** rather than the summary list.

## 2026-08-08 — re-derived a2a-cli, then decided not to file it

Picked up `cc7feef9` expecting the easy one: the migration was written 2026-08-06, verified
then, and just needed a PR opened. Re-derived it anyway. Good thing.

**The branch itself held up perfectly**, the first entry in this whole exercise where the notes
overstated nothing. Upstream hasn't moved since the branch was cut, so it still applies cleanly.
`tsc --noEmit` clean. Ran it end to end against a live a2acode echo server (card discovery,
blocking send, streaming through to `completed [FINAL]`, `get`, `cancel` failing gracefully) and
`input-required` against the playback repo. "Verified end-to-end" was accurate.

**The premise underneath it was incomplete, though.** The commit message says `^0.3.4` "can't
fetch an agent card built on the new supportedInterfaces shape". True, but that is the *second*
failure and you never reach it normally. Reproducing on a worktree of `upstream/main`:

1. `GET /` → **405**, because `A2AClient.fromCardUrl(serverUrl)` gets the base URL and a2acode's
   root is POST-only JSON-RPC. Not version skew at all.
2. Feed it the real card URL and you get the actual one: *"Provided Agent Card does not contain
   a valid 'url' for the service endpoint."* That is the protocol change.
3. Bonus, found by accident: upstream doesn't typecheck on a clean checkout. `^0.3.4` floats to
   0.3.14 and the API moved inside the 0.3 line, so `npm install && npx tsc` gives 11 errors.
   `tsc` emits anyway, which is why the CLI still runs and nobody noticed.

A report claiming only the shape problem would have had a maintainer pass the base URL, see a
405, and reasonably conclude we were wrong.

**Josh's question sharpened it further:** is this just a version thing? Yes, but across three
axes that are easy to conflate (A2A protocol 0.x→1.0, `@a2a-js/sdk` 0.3.14→1.0.1, `a2a-sdk`
0.3.x→1.1.2, plus a2acode's own unrelated 0.6.2). Only the protocol axis is the real story.
Confirmed it is protocol-level rather than an a2acode quirk: `@a2a-js/sdk` 1.0.1's own
`AgentCard` declares `supportedInterfaces` as **required**, so the JS and Python 1.x SDKs agree
across languages, and a2a-inspector hits the identical wall from its own 0.3.10 pin. Two
unrelated clients, same break.

That also showed the draft was structured wrong: it led with the 405 because that's the order
you encounter things, which buries the finding a skimming maintainer needs.

**Then we didn't file it.** `ericabouaf/a2a-cli` was created and abandoned inside a 35-minute
window on 2025-11-08, and nine months later has 1 star, 1 fork (ours), zero issues and zero PRs
ever. A cold 200-line PR into that is a lot of ceremony for something unlikely to be read, and
the fork already works locally so nothing is blocked. Josh called it, and it's the right call.

Everything is preserved in UPSTREAM.md's a2a-cli entry rather than left in `scratch/`: all three
failures, the version table, the framing note about leading with #2, and the reproduction recipe.
The draft issue stays in `scratch/` and the task is annotated rather than closed.

**Worth naming as a pattern:** re-deriving was worth it even though we filed nothing. It caught
that the premise was incomplete, produced a version breakdown neither of us had straight, and
turned "just open the PR" into a documented decision. The output of a re-derivation isn't always
an issue; sometimes it's a better-informed no.

Cleanup: killed a stray `rig-serve` on 9310 left running from a prior session, plus an echo
server on 9317 that survived a first `kill` and needed `-9`, and removed the temp worktree.

### Convention change: no agent trailers on outbound commits

The 2026-08-08 call was "strip `Claude-Session` from commits to someone else's repo,
`Co-Authored-By` stays." Reviewing the a2a-cli migration commit, which had neither, Josh
reversed it: **commits going into someone else's repo carry no agent trailers at all.** His own
repos keep both. Already-pushed commits that predate this stay as they are; not force-pushing a
branch over a trailer. Saved as its own memory since it applies well beyond this project.

### What five filings and four re-derivations actually taught

Worth collecting, because it's spread across the last four entries and the pattern is the
transferable part:

- **Re-derivation changes severity and scope, not just facts.** `f010f63e` went nit → bug,
  `438d9c1c` went nit → feature request, in the same pass, in opposite directions. Ordering
  built on the old sizes was wrong too.
- **A wrong claim is often sitting on top of the right framing.** `5dcde5fb`'s dead "disconnected
  client" premise hid the much stronger "the only case that reaches this branch is the one it
  handles wrong." `e653db90`'s invalid inference hid a whole streaming-protocol misread.
- **When the claim is about what someone else sent you, only the wire answers it.** Reading your
  own source proves what you did with data, never what arrived. `e653db90` was unfileable until
  thirty lines of `tee`.
- **Bundling test: does it bury the item with evidence?** Split `f010f63e` from `438d9c1c` on
  that, same as `e653db90` earlier.
- **Sometimes the output is a better-informed no.** a2a-cli, this entry.
- **The generous framing that lands is accurate description, not announced charity.** #40 shipped
  a section headed "Why I don't think this is a careless bug" and had to be edited after filing.

Rig untouched. Suite still 165 passed / 4 xfailed. Taskwarrior at 15 pending. Five issues out,
five docs commits unpushed.

## 2026-08-08 — sixth filing, and the one asymmetry that decided it

Resumed from the handoff: pushed the six unpushed docs commits first, then picked up
`777656ed` (binary `PermissionDecision` flattens ACP's `ExitPlanMode` options) — the entry
explicitly flagged as patterning like `438d9c1c`, which got parked as a design question rather
than filed.

Re-derived against current a2acode source (still pinned v0.6.2) before doing anything else.
Every claim held with no drift: `PermissionDecision.allow: bool` at `backends/base.py:114-120`,
`select_option`'s one-shot-over-sticky preference at `acp.py:155-174`, the `371` call site, and
the "completed not failed" consequence, checked directly against `executor.py`'s control flow
(the only paths to `updater.failed()` are an unhandled exception or session eviction — a denied
`ExitPlanMode` drains normally and falls through to `updater.complete()`). The three-option
table's source turned out to live one layer further down than the entry implied: ACP's option
*kinds* are generic (`acp/schema.py`'s `PermissionOptionKind` literal), but the specific mapping
of Claude Code's `acceptEdits`/`default`/`plan` modes onto them is `claude_agent_sdk`'s, not
a2acode's — confirmed by reading `claude_agent_sdk/types.py` directly rather than trusting the
note's paraphrase.

That re-derivation is what decided the file-or-park question the handoff left open. `438d9c1c`
was parked because there was no mechanism to enforce a cost ceiling over ACP at all — filing it
would have meant asking a2acode to invent one. `777656ed` is different in exactly the way that
matters: the agent is *already sending* three option kinds over the wire, and a2acode's binary
`PermissionDecision` throws one of them away before it ever reaches a caller. Nothing to invent,
just something already arriving that gets discarded. Filed as
[a2acode#41](https://github.com/kanywst/a2acode/issues/41), diff-first per the standing habit:
opens with what a2acode does today (deny ends the task `completed`), not a case for why the
protocol *should* carry three options.

One filing mechanic worth keeping: drafted the issue as a single scratch file with `# Title` /
`# Body` sections for readability, then piped the whole file into `gh issue create --body-file`
without splitting it first. The posted issue carried the `# Title` heading and a duplicate title
line inside the body. Caught on the review pass immediately after filing (`gh issue view --json
body`), fixed with `gh issue edit --body-file` pointed at just the body half. Splitting the
scratch file before the `gh` call would have skipped the round trip entirely — the two-section
format is fine for drafting, it just can't go straight into `--body-file`.

Also fixed a small case of doc rot the parked handoff had flagged: `docs/UPSTREAM.md` still said
"No task yet" under this finding, though `777656ed` had been created in taskwarrior before the
session was parked. Descriptions and reality drift even inside a single session's boundary.

Rig untouched. Suite still 165 passed / 4 xfailed. Taskwarrior task `777656ed` closed; 14
pending. Six issues out.

## 2026-08-09 → 10 — The consumer gets its spec: the cockpit, missions, and A2A turtles

Brainstormed Phase 6's last bullet (taskwarrior `9b3c2a04`) into a spec:
`docs/superpowers/specs/2026-08-09-a2a-orchestrator-design.md`, eighteen commits of
iteration, three crit review rounds (16 comments, all addressed). The deliverable is a
product now, not a demo: a **cockpit** for coordinating agent work across repos —
missions (emergent groupings of chats + worktrees), an orchestrator agent you chat with
that delegates to repo agents, approvals routed to one place.

The decisions that mattered, in the order the spec reversed itself into them:

- **Missions are emergent.** The predeclared `projects.yaml` died the moment the
  fresh-start use case was written down: walk in, describe the work, no repo selection.
  Config shrank to a catalog.
- **The browser speaks A2A.** The bespoke REST+SSE chat API was shadowing A2A with a
  homemade protocol — a chat is a `contextId`, turns are messages, approvals are
  `input-required`. The service became a contextId-routed pass-through proxy; REST
  remains for what A2A has no vocabulary for (missions, worktrees, catalog).
- **Proven, not assumed:** `@a2a-js/sdk/client` 1.0.1 works in a real browser — esbuild
  clean for the browser platform, and a live Chrome run through a ~40-line Starlette
  proxy against `rig-serve` streamed a full task lifecycle. Found the proxy's one
  translation duty: cards advertise the upstream origin in both `localhost` and
  `127.0.0.1` spellings and must be rewritten or the client escapes the proxy.
- **Recording reuses the rig.** The orchestrator is a `Backend`, so `RecordingBackend`
  wraps it unchanged and a recorded chat is structurally a scenario one level up. The
  two real gaps: dispatches must be recognizable, and replay must execute them.
- **Milestones resliced for visible progress** (crit round three's aftermath): six rungs,
  each ending in a demo — direct-sessions, orchestrator-live, recorder, replay, cockpit,
  e2e-suite — with `real-agents` (spawn provider, worktree lease mechanics, worktrunk
  integration) as the follow-on where it becomes a daily driver.

Vocabulary settled by review: **approval** (not gate), **recording** (not trace),
**worktree** (not checkout), **mission** (the grouping). A verified-facts appendix went
into the spec so implementers inherit the session's discoveries instead of re-earning
them.

Rig untouched. Next session: `writing-plans` for `direct-sessions`.

## 2026-08-10 — direct-sessions ships: the cockpit's walking skeleton, subagent-driven

Executed the `direct-sessions` plan (written this morning from the parked handoff,
`docs/superpowers/plans/2026-08-10-direct-sessions.md`) via subagent-driven development:
eight tasks, each with a fresh implementer and a task-scoped review, then a whole-branch
review and the 👀 demo in a real Chrome. `a2a-orchestrator/` now exists: SQLite store,
catalog index provider, management REST, the contextId-routed pass-through A2A proxy, an
`orch-serve` CLI, and a Vite+React cockpit (mission list, chat pane, approval card over a
genuine a2a-js client). 32 pytest tests, all driving real subprocesses — a playback rig
serving `a2a-rig/repos/` with the service in front of it.

Both open questions the handoff carried were settled by verification before the plan was
written, and the wire confirmed both:

- **The service mints contextIds.** a2a-sdk 1.1.2 (the version installed in a2acode's
  venv) adopts a client-supplied `message.context_id` rather than replacing it
  (`_check_or_generate_context_id`), so chat-open mints the id, the browser sends it on
  turn one, and the upstream converges. `test_upstream_adopts_the_service_minted_context`
  pins it on the wire.
- **Cold resubscribe dissolved rather than solved.** The chat's proxied base is
  `/a2a/chats/{contextId}/`, so the contextId rides the path and routing any call — a
  `tasks/resubscribe` after a reload included — is a store lookup, never an inference
  from observed traffic.

The plan's verbatim-code approach mostly held (six of eight tasks were byte-for-byte
transcriptions that passed review), and where it didn't, the review loop caught defects
*in the plan's own listings*: the card-rewrite branch dropped headers in both directions
(fixed: filtered passthrough, plus a `content-encoding` strip since httpx has already
decoded the body being rewritten), the shell's error banner never cleared on success, and
ChatPane never stopped consuming the stream on unmount (fixed: alive-flag ref; breaking
a `for await` calls the generator's `.return()`). One task-1 surprise: the implementer
quietly fixed a real a2a-rig bug — `load_repos()` choking on a `.claude/` harness
artifact inside `repos/` — and the review made it its own commit with a regression test
(`test_load_repos_skips_dot_prefixed_directories`; rig suite now 166 passed / 4 xfailed).

The demo ran on the one-process origin (built frontend served statically at :9300):
fresh mission → billing-api free text streamed (plan, tool events, diff rendered as
text) → "please run the tests" parked the task and the approval card rendered from
`a2acode_permission` metadata naming Bash and `pytest tests/ -q` → Allow resumed to
completion → an infra-terraform chat rendered its default-play failure as a failed turn.
Recorded to `~/Downloads/direct-sessions-demo.gif`. The approval answer round-trip also
retired the one runtime question the reviews had deferred (whether a2a-js's
empty-string-vs-absent `taskId` distinction matters on the wire: it doesn't).

Final whole-branch review: ready to merge, no Critical/Important findings; next-touch
notes recorded here so the workspace ledger can be deleted: wrap the proxy relay's
upstream-connect failure in a 502 naming the upstream (it currently 500s opaquely),
delete the frontend scaffold leftovers (`frontend/README.md`, `public/icons.svg`), add
the `__init__.py` future import, and guard the `catch`-path `append` in ChatPane with
the same alive check as the loop. Merged to main; PLAN.md Phase 6's consumer bullet
stays unchecked until `e2e-suite`, per the milestone ladder.

One process note: the worktree convention (`wt`) collided with the command sandbox —
`~/worktrees` isn't writable in-sandbox, and a session of subagent file-writes there
would have meant constant permission prompts — so the branch was worked in the primary
checkout instead. Fine this time (the checkout was idle), worth remembering as the
tradeoff it is.

## 2026-08-12 — first live run: a real Claude behind the cockpit, and the question nobody could answer

Josh drove the rig demo himself this time (one-process mode at :9300) — every beat from the
2026-08-10 GIF reproduced by hand: streamed text, tool events, the plan checklist, the diff,
the approval round trip both ways, infra-terraform's scripted failure. That closed Part 1 of
the handoff; Part 2 was pointing the same cockpit at a live agent.

The blocker was discovery: the catalog only spoke rig-index (`GET /`), and a lone a2acode
serves a card but no index. Went with the static provider over an index shim (less moving
parts; the provider seam existed for exactly this): `provider: static` inlines the same
entry shape in catalog.yaml, `repos()`/`resolve()` work identically with no HTTP. TDD'd on
branch `static-catalog` (`7321bf8`, 34 tests green), plus `catalog-live.yaml` pointing at
a2acode's default port — which turned out to be 9100, accidentally completing the port
ladder (a2acode 9100, rig 9200, orch 9300).

The live run itself: `a2acode serve --backend claude --cwd ~/scratch/demo-app`, no
`--permission-mode`, subscription auth. Prompts, streaming, and plain tool approvals all
worked through the proxy unchanged — the card rewrite plus contextId routing was genuinely
all the translation a real agent needed.

Then the find of the day. "Add a /health endpoint and run the tests" ran into demo-app
having no test suite (only stale `.pyc`s from a deleted `test_app.py` — the live Claude
disassembled a pyc with `marshal`+`dis` to reconstruct what the tests used to cover, which
is more archaeology than $0.30 usually buys). So it did the right thing: asked, via
`AskUserQuestion`. The cockpit showed the generic approval card — tool name, raw input
JSON, Allow/Deny — and allowing produced the tool result "The user did not answer the
questions." The question had reached the browser (it's right there in the
`a2acode_permission` input blob); there was just no way to answer it. Root cause is
a2acode's boolean permission pipe: `_decision` collapses the resume text to allow/deny and
the claude backend returns a bare `PermissionResultAllow()`, never using the SDK's
`updated_input` — which is exactly where answers must ride
(`{"questions": <passthrough>, "answers": {question: label}}`, per the Agent SDK
user-input docs, re-derived same day). Full lead in UPSTREAM.md, filed alongside its ACP
sibling (the binary-`PermissionDecision` entry): three permission findings now trace to
the same boolean pipe.

The split that matters for what's next: rendering the question as a real question card is
cockpit work; carrying the chosen option back is a2acode work, and without the upstream
channel the prettiest card in the world still can't answer. Live-rendering polish notes
from the handoff (choppy artifact chunking) didn't materialize as complaints; parking
lot's other carried questions stay carried.

## 2026-08-12 — agui-native ships: the reversal, the research that paid for itself, and a table that survived reality

The afternoon took the morning's cockpit and swapped its conversation plane out from
under it. The 2026-08-12 spec reversed one decision of the 2026-08-09 spec — the browser
stops speaking A2A and speaks AG-UI (CopilotKit), the service becomes the A2A client —
and by end of day it was merged (`59950f6`), demoed in Chrome, and reference-run against
a live Claude. 51 tests, was 34 this morning.

The spec grew three amendments during review before any code: the seam is two-way
(`RunAgentInput` → new-message-or-resume is the trickier half and got its own tested
function), the parked task lives in an in-memory dict (not a store column — same deferral
class as reload replay), and a domain audit that ended with "no schema change." The audit's
sleeper: AG-UI's client-sends-full-history design assumes a stateless agent, ours isn't —
a2acode holds the conversation via `contextId` — so the service reads only the tail and
stores nothing. The message gap (reload loses the render log) is acknowledged and tracked
(taskwarrior `fc4eb2d8`).

Research before implementation corrected three hand-imagined assumptions at zero cost:
CopilotKit "v2" is the `/v2` subpath of the 1.x package (react-ui is v1 and unneeded);
HITL is `useHumanInTheLoop`, not v1's `renderAndWaitForResponse`; and — decisive for the
design — registry agents are per-key singletons, so the spec's "one logical agent name"
would have made panes clobber each other's thread. Each chat now registers its own
`HttpAgent` under its `context_id`. The AG-UI side was verified against the extracted
0.1.19 wheel rather than docs; the encoder is four lines and the event constructors are
exactly what they claim.

Subagent-driven execution, five review gates, three fix rounds worth recording:
multimodal `UserMessage.content` would have slipped a list into `Turn.text` (now refused
loudly); the endpoint's failure boundary didn't structurally cover the store lookup and
could strand an open text frame (now `RunTranslator.abort()` + a widened try — the
invariant "every failure lands inside the run as RUN_ERROR" is structural, not aspirational);
and the final review caught that a fresh message while an approval was parked would clear
the park — a guard the old `ChatPane` had (disabled input) that didn't survive the swap.
The park is now replaced or consumed, never incidentally dropped, and the stale card
stays answerable.

Browser validation (delegated to a subagent driving Chrome) passed hello/allow/deny/
second-chat on the first try and found the one thing pytest couldn't: **a failed run
rendered as literally nothing** — CopilotChat consumes RUN_ERROR into an `onError`
callback and paints no default UI. Indistinguishable from thinking, short of devtools.
One `onError` → red banner fix later, infra-terraform's scripted failure reads
`run failed: …` like it should. Also observed: plan/diff artifacts concatenate into a
wall of text (taskwarrior `13f576dc`), and STEP_*/CUSTOM events are consumed-but-invisible
in CopilotKit's default rendering — narration is on the wire, unrendered for now.

The reference run was the day's quiet triumph: four live turns against `a2acode serve
--backend claude` through the new plane — streaming, narration-as-steps, a real Bash
write gate parking as a `request_permission` tool call with a2acode's `request_id` as the
`toolCallId`, the allow decision riding back as a `ToolMessage`, and the file actually
landing on disk. Zero CUSTOM passthroughs across all four captures: the hand-imagined
translation table needed **no corrections**. After Phase 7's recordings-corrected-us-twice
lesson, that's the first table to survive contact with a real producer unchanged — credit
to it being derived from a2a.ts's shipped distillation rather than imagination.

Deletions landed last, per the strangler bullet: `proxy.py`, the card rewrite and its
load-bearing trailing slash, `a2a.ts`, `ChatRef.a2a_url`, `@a2a-js/sdk`. The working
browser A2A client exists only in git history now, as the spec said it would. One
operational gotcha for next time: the morning's orch-serve was still holding :9300 and
answered `/agui/run` with a 405 from its static mount — check `lsof` before trusting a
demo stack.

Followups in taskwarrior: message persistence as AG-UI event log (`fc4eb2d8`), plan/diff
rich rendering (`13f576dc`), Playwright through the new plane (`d798cf14`), and a
hardening batch (truncated-stream-as-success, toolCallId verification on resume, client
cache eviction) that resolves with the event-log work.

## 2026-08-12 — addendum: plural, live, in the browser

The question that closed the day: "have we pointed it at real a2acodes yet?" The honest
answer at merge time was one-at-a-time and only via curl on the new plane. So: a second
scratch repo (`~/scratch/demo-notes`), a second `a2acode serve` on 9101, a second entry
in `catalog-live.yaml`, and a browser-driven mission spanning both. demo-app answered a
question about itself (~10s to first text); demo-notes hit a Bash write gate, the
approval card rendered with the real command, Allow completed the run, and the note
landed on disk saying "two live agents, one mission." Thread isolation held on remount —
the demo-app pane came back empty (no replay yet, per the message gap) but with zero
bleed from its sibling. Console clean. GIF: `multi_live_mission.gif`.

The multi-live topology needed nothing new — the static provider's one-entry-per-process
shape just pluralized, which is what DESIGN-v3's one-process-many-repos ↔
one-process-per-repo interchangeability promised. The empty-on-remount pane is now the
most visible argument for the event-log followup (`fc4eb2d8`).

## 2026-08-13 — the event log ships: replay, pending survival, hardening

The deferral 2026-08-12 named twice — "history is advisory," and the message gap it
called the sharpest one — got paid off today. Spec
`docs/superpowers/specs/2026-08-13-agui-event-log-design.md`, one branch of commits
(`8f75ece..973c090`), merged to `main`. Every message crossing the AG-UI seam, both
directions, now lands in a SQLite `events` table as it happens; a remounted pane
replays the whole prior conversation through the protocol's own connect handshake; a
pending approval survives a service restart and comes back answerable; and the
hardening batch scoped alongside it (`dbcb5569`) closed three trust gaps in the run
loop. 51 tests this morning, 78 now. Frontend got build+lint only — there's still no JS
test runner in this repo.

The load-bearing discovery of the day was that the empty-pane-on-reload bug was never
a missing-machinery problem. CopilotKit's `CopilotChat` calls `connectAgent()` on
every mount, expecting `RUN_STARTED → MESSAGES_SNAPSHOT → RUN_FINISHED` back — and the
plain `HttpAgent` we'd been running has no `connect()` at all. The library doesn't
error on the miss, it swallows it silently, so the pane just renders nothing and looks
like a rendering gap. Verified by reading `@ag-ui/client` 0.0.57 and
`@copilotkit/react-core` 1.67.1 shipped source via sourcemaps, not docs. The fix
(`12a0855`) is a real endpoint, `/agui/connect`, that folds the stored event log back
into AG-UI messages (`d2337ed`) and answers with exactly the snapshot sequence the
client was already asking for. Once that was understood, "add persistence" and "fix
the empty pane" turned out to be the same task, which is why the spec could promise
both in one sentence.

A second trap surfaced next to it and got written down so nobody rediscovers it the
hard way: `AgentConfig.initialMessages` exists and looks like the obvious seed point
for replayed history, but `CopilotChat`'s mount effect wipes agent messages in both of
its `threadId` branches — seeded history paints once, on the first render, then
vanishes on the very next effect pass. The connect-handshake path sidesteps it
entirely by answering the library's own expected request instead of fighting its
mount lifecycle.

Re-arming a pending approval after reload rides the same handshake: the HITL card's
status derives from live run state only, and the one supported way to re-arm it is
`copilotkit.runTool()` with a fresh `toolCallId` (`744c4d4`). Because the tool-call id
changes on every re-arm, resume verification can't key on it — it keys on the
permission payload's `request_id` instead, which is stable across the restart that
orphaned the original `toolCallId`. `39f7b9f` exposes a chat's pending approval over
REST so the browser has something to re-arm against after remount, and `6019016` pins
the whole path — restart, replay, re-arm, resolve — end to end against the same
database in `tests/test_restart.py`, which passed on the first run.

Housekeeping that touched every layer: "park" is now "pending" everywhere —
`pending_task_id`, `pending_call_id`, `pending_payload` on `chats` — cleaning up the
naming the 2026-08-12 spec left in place as a same-deferral-class shortcut (`8f75ece`).
The hardening batch landed alongside the persistence work rather than after it, per
the spec's framing: a truncated upstream stream now closes as `RUN_ERROR` instead of
silently reading as success (`c1c941d`), resumes are verified against `request_id`
before being applied (`3cf8552`, `3843d97`), and clearing a pending approval evicts the
cached A2A client instead of leaving it stale (`3843d97`).

Review caught two plan-authored bugs before merge, both structural-invariant misses of
the same shape 2026-08-12 already flagged once (every failure should land inside the
run as `RUN_ERROR`). First: `run_agent`'s chat lookup had moved outside the `try`, so a
db fault on that lookup would break the transport with no `RUN_ERROR` at all — fixed by
wrapping the lookup and making the except-arm's emits best-effort (`5860b04`). Second:
`PendingRearm`'s poll timeout armed the card anyway, and a failed poll vanished as an
unhandled promise rejection with nothing on screen — fixed by gating arming on an
actual snapshot and routing failures through the existing `runError` banner
(`ac4b713`).

The final whole-branch review caught a third, subtler than the first two: a resume
answered through a re-armed card logged its tool result under the runTool-minted
toolCallId, while the log's only `TOOL_CALL_START` carried the original request_id —
so every later replay of that chat would fold an unanswered permission call plus an
answer to a call that doesn't exist. The service already reconciled the *resume* by
request_id; the *log* kept the unreconciled id. Fixed at write time: a resume that
verifies against the pending state logs its answer under the call id it verified
against, while a mismatched resume still logs verbatim, because the wrong answer is
the truth of what happened (`973c090`).

Browser validation (Chrome, four scenarios) confirmed all of it live: reload replay
showed the full prior conversation; a pending card re-armed after reload and Allow
completed the run; restart survival held against the same database, pending
approval intact; and reloading *after* answering a re-armed card rendered the
exchange as cleanly answered — no orphan card, no error banner. Console clean
throughout. GIF: `~/Downloads/agui_event_log_replay.gif`.

Taskwarrior: `fc4eb2d8` (event log / persistence) and `dbcb5569` (hardening batch) both
close with this entry. `13f576dc` (rich rendering of plan/diff artifacts) and
`d798cf14` (Playwright through the new plane) stay open — scoped out of this spec
explicitly, unchanged by today's work.

## 2026-08-14 — cockpit's "Phosphor" redesign ships

A design handoff bundle landed in `~/Downloads/design_handoff_cockpit_phosphor/` — README,
HTML mocks (current vs. redesign), `tokens.css`, an implementation plan — the product of an
external design session working from `docs/superpowers/specs/2026-08-14-cockpit-ui-redesign-brief.md`.
"Phosphor": dark-first, all-monospace, phosphor-green accent, 2px flat radius everywhere, text
glyphs instead of icons (`▸ ▾ ▴ ✕ ⏎ ↑↓ ↻ >`). The README was named design authority — where a
mock and the README disagreed, the README won.

Implementation followed `docs/superpowers/plans/2026-08-14-cockpit-phosphor.md` via
subagent-driven development: six slices (`tokens-theme-wiring`, `app-shell`, `repo-picker`,
`chat-stream-reskin`, `approval-card`, `error-surfaces`), each implemented by a subagent,
reviewed, and fixed before the next started. Three fix rounds total — composer pointer-events /
caret ownership / message gap on `chat-stream-reskin`, a mobile margin bug on `approval-card`,
an `onArmed` memoization bug on `error-surfaces` — all caught in review, none surfaced later.
`docs-and-upstream` (this entry) is the seventh and last slice; `visual-verification` is a
separate orchestrator-driven pass against the mocks, not part of this doc slice.

**Tech choices.** Tailwind v4 (`@tailwindcss/vite`) plus `class-variance-authority` for the
button/card variants — Josh's call, and it matched the brief's own stated preference over hand-
rolled CSS modules. `cmdk`/Radix were pulled in as dependencies but ultimately skipped for the
repo picker: the design's trigger-as-input combobox (the filter text lives *in* the trigger,
not behind a separate panel) doesn't fit cmdk's panel-input pattern, so the listbox is hand-
rolled — filter, keyboard nav, match count, all local `useState`.

**Theming CopilotKit.** The chat pane reskins through two mechanisms. First, a token override:
CopilotKit v2 ships its own shadcn-style custom properties scoped to `[data-copilotkit]`
(verified by reading the installed package, `@copilotkit/react-core` 1.67.1), so redeclaring
the Phosphor palette at that same selector beats `:root` on specificity with zero component
overrides — as long as `main.tsx` imports CopilotKit's `styles.css` *before* `index.css`, so
the cascade order matches the specificity order. Second, the structural reskin (prefix-style
messages, block caret, custom composer) rides CopilotKit v2's slot system:
`messageView.assistantMessage` / `userMessage` / `cursor`, plus a top-level `input` slot on
`<CopilotChat>`. `CopilotChatToolCallsView` stayed nested inside the custom assistant message
rather than getting pulled out, because it's the only path in the v2 API that actually invokes
HITL `render()` callbacks — pull it out and the approval card stops rendering.

**Findings that shaped the implementation, all verified against 1.67.1 shipped source:**

- The `input` slot renders inside a `pointer-events-none` overlay (`CopilotChatView`'s own
  input wrapper carries `cpk:pointer-events-auto` to punch back through it); a custom composer
  that doesn't set the same class is mouse-dead — clicks and the send button do nothing, only
  Tab-focus works. First shipped broken, caught in `chat-stream-reskin`'s review.
- `MemoizedAssistantMessage`'s comparator never re-renders a message once it stops being the
  last one in the thread, so an inline `isRunning && isLast` streaming caret can go stale and
  stick on permanently once a later message arrives. The design called for the caret sitting at
  the end of the streaming text; the fix moves caret ownership entirely to the `cursor` slot
  (which `CopilotChatMessageView` already re-renders correctly), a deviation from the mock that
  Josh approved during review rather than something discovered and shipped silently.
- `useHumanInTheLoop`'s `render` callback is frozen at first tool-call registration — the
  effect's dependency array excludes `render` itself, so a closure captured at mount time is
  what actually runs on every subsequent call, not whatever the latest render passed. Anything
  the render callback closes over (`repo`, `onPendingChange`) has to be stable across the
  component's lifetime, not just correct at the moment it's read.

**Known, deliberate gaps** — not bugs, just data the backend doesn't expose yet: `RepoEntry`
has no reachability field, so the "unreachable repo, dimmed and unselectable" state from the
mocks is skipped entirely, every repo row renders selectable; `ChatRef` has no status field, so
nested chat rows never show the `·paused`/`·running` suffix; and the block-style typing caret
in *editable* inputs (repo filter, composer) uses native `caret-color: var(--primary)` instead
of a simulated block caret, because a real `<input>` can't render a fake block caret without
also hiding the real one — only the *streaming* caret in assistant messages is the actual 7×14
block.

Two CopilotKit gaps the design ran into are written up as upstream leads in `docs/UPSTREAM.md`:
HITL `render()` losing the resolved decision once a call reaches `status === 'complete'`
(the approval card's post-reload receipt has to fall back to a neutral "ANSWERED" rather than
the true "ALLOWED"/"DENIED"), and `RUN_ERROR` having no supported hook to clear a sticky error
banner short of remounting the whole chat pane.
