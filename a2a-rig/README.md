# a2a-rig

A pytest harness that drives [a2acode](https://github.com/kanywst/a2acode) over the real A2A
protocol — real agent card, real JSON-RPC + SSE, real task-state machine — using the official
`a2a-sdk` client. No in-process shortcuts: the harness launches `a2acode serve` as a
subprocess and talks to it over HTTP, exactly the way a frontend would.

It also holds `playback`, a scenario-driven a2acode backend: the same server, protocol mapping,
and task-state machine, with a scripted brain instead of a live model. **The stub cannot drift
from the real producer, because it *is* the real producer minus the model.**

Planning and design live in the sibling
[a2a-experiments](https://github.com/technicalpickles/a2a-experiments) repo (`docs/PLAN.md`,
`docs/DESIGN-v3.md`). This repo is Phases 3 and 4 of that plan.

## Running

```bash
uv sync
uv run pytest                      # against a2acode's echo backend
uv run pytest --backend playback   # against a scripted fake repo
```

Expect 108 passed, 4 xfailed, in under seven seconds either way.

### Running the rig

```bash
# every repo, one process, index at /
uv run rig-serve --repos repos/ --port 9200

# one repo at a host root, the way a real deployment would run it
uv run rig-serve --repo repos/billing-api --port 9201
```

Both are real A2A agents — point `a2a-cli`, a browser, or your own client at them. Under
`--repos`, each repo is mounted at `/repos/<name>/` and `GET /` returns an index
(`{"repos": [{"name", "description", "card_url"}]}`) rather than an agent card — the rig is a
directory of agents, not an agent itself.

The harness shells out to a2acode via `uv run --project ~/github.com/kanywst/a2acode`. If your
checkout lives elsewhere:

```bash
A2ACODE_PROJECT=/path/to/a2acode uv run pytest   # relocate the checkout
A2ACODE_CMD="a2acode" uv run pytest              # or override the command entirely
```

## The point: one suite, swappable brains

Every test here asserts *protocol* behavior, which does not depend on which backend produced
the stream. Which backend runs is a fixture:

```bash
uv run pytest --backend echo      # default; no key, no inference
uv run pytest --backend claude    # real inference, real money
```

Per-test override with `@pytest.mark.backend("claude")`.

What genuinely differs per backend is *which prompt provokes a given behavior* — `echo` parks
on a permission request when it sees "sudo", a scripted scenario will park for its own reasons.
Those stimuli are fixtures too (`simple_prompt`, `permission_prompt`, `permission_tool`,
`denied_marker`, `tool_marker` in `tests/conftest.py`). So pointing this suite at the Phase 4
`playback` backend means writing a scenario that answers those prompts and adding a branch to
those fixtures — not editing a single test body.

## Scenarios

A scenario is a list of *plays*; each incoming message selects one by match rule; a play is a
list of events written in a2acode's own `BackendEvent` vocabulary (`text`, `tool_use`,
`tool_result`, `file_change`, `plan`, `thought`, `notice`, `permission`, `result`), plus
`error` for a run that dies partway.

```yaml
plays:
  - match: { contains: "run the tests" }
    events:
      - plan: { steps: [{ content: "Add the endpoint", status: in_progress }] }
      - tool_use: { name: Read, input: { file_path: "src/app.py" }, id: t1 }
      - text: "Adding a /health endpoint.\n"
      - permission:                       # parks the task in input-required
          tool: Bash
          input: { command: "pytest tests/ -q" }
          timeout_ms: 30000                 # optional; omitted means wait forever
          on_allow:   [ { text: "42 tests pass." }, { result: { cost_usd: 0.0173 } } ]
          on_deny:    [ { text: "Skipped the test run." }, { result: { cost_usd: 0.0102 } } ]
          on_timeout: [ { text: "Nobody answered, so I left it." } ]
      - error: "the sandbox ran out of disk"   # fails the task, like a real crash
```

A `permission` with `timeout_ms` takes `on_timeout` (falling back to `on_deny`) when nobody
answers in time — the abandoned-approval path, which live inference cannot be asked to
reproduce on demand. A caller who answers *after* that resumes into the branch that already
ran, rather than the one they asked for, which is the honest rendering of having walked away.
Leaving `timeout_ms` off means wait indefinitely: a gate that quietly expired would turn a
slow reviewer into a denial nobody scripted.

An `error` event raises rather than emits, so the task fails through a2acode's real failure
path. `result: { stop_reason: ... }` reaches the caller as completion metadata, which is how a
truncated answer is told from a finished one.

Match rules, first match wins: `turn: N` (counted per context, so a fresh conversation replays
from the top), `contains:`, `regex:`, and `{}` for a catch-all. Rules within one `match`
combine conjunctively.

**An unmatched message fails the turn.** That is deliberate and is the main thing separating
this from a mock: a mis-scripted test breaks loudly instead of receiving a plausible wrong
answer. Scenario files are also validated at startup, so a typo'd event name fails when the
server boots rather than mid-stream.

Set `PLAYBACK_SPEED` to scale `delay_ms` pacing (unset or `0` means instant, the CI default;
`1.0` is lifelike, `0.1` is a tenth of the scripted pace). `delay_ms` goes on any event, or
on `defaults:` for the whole scenario, and applies to a `permission` too — a gate that arrives
the instant you ask reads as a UI bug rather than a script.

## Layout

- `src/a2a_playback/` — the `playback` backend. `scenario.py` parses and matches, `backend.py`
  emits, `serve.py` is the `rig-serve` wrapper. Written to drop into a2acode's own `backends/`
  directory later (DESIGN-v3 §7, milestone M4), so it imports only a2acode's public backend
  vocabulary and holds no rig-specific assumptions.
- `src/a2a_rig/server.py` — launches a server on a free port, waits for the card, tears down
  after. Surfaces the server's own stderr when it fails to boot, so a broken server is never
  mistaken for a broken test.
- `src/a2a_rig/events.py` — collapses a protobuf event stream into an assertable `Capture`
  (`states`, `artifacts`, `artifact_text()`, `permission`, `completion_metadata`).
- `tests/conftest.py` — server pool, A2A client, card, and the per-backend stimuli above.
- `repos/` — repo library, one directory per fake agent (`repo.yaml` plus `scenarios/*.yaml`).
  `tests/repos/` holds repos used as test instruments rather than demos.

Servers are pooled per `(backend, args)` for the session rather than booted per test; that is
the difference between a 20-second suite and a 1-second one. Use the `fresh_server_url` fixture
if a test needs an untouched process.

## Known upstream behavior

`tests/test_lifecycle.py` carries two `xfail(strict=True)` tests: canceling a task parked in
`input-required` reports success but has no effect — the task stays parked, in the response
and on a later `tasks/get`. Marked strict so they fail loudly if a2acode ever fixes it.
Relevant if you are building a UI that offers "cancel" on an approval prompt.
