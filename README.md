# a2a-rig

A pytest harness that drives [a2acode](https://github.com/kanywst/a2acode) over the real A2A
protocol — real agent card, real JSON-RPC + SSE, real task-state machine — using the official
`a2a-sdk` client. No in-process shortcuts: the harness launches `a2acode serve` as a
subprocess and talks to it over HTTP, exactly the way a frontend would.

Planning and design live in the sibling
[a2a-experiments](https://github.com/technicalpickles/a2a-experiments) repo (`docs/PLAN.md`,
`docs/DESIGN-v3.md`). This repo is Phase 3 of that plan.

## Running

```bash
uv sync
uv run pytest
```

Expect ~31 passed, 2 xfailed, in about a second.

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

## Layout

- `src/a2a_rig/server.py` — launches `a2acode serve` on a free port, waits for the card,
  tears down after. Surfaces a2acode's own stderr when it fails to boot, so a broken server is
  never mistaken for a broken test.
- `src/a2a_rig/events.py` — collapses a protobuf event stream into an assertable `Capture`
  (`states`, `artifacts`, `artifact_text()`, `permission`, `completion_metadata`).
- `tests/conftest.py` — server pool, A2A client, card, and the per-backend stimuli above.

Servers are pooled per `(backend, args)` for the session rather than booted per test; that is
the difference between a 20-second suite and a 1-second one. Use the `fresh_server_url` fixture
if a test needs an untouched process.

## Known upstream behavior

`tests/test_lifecycle.py` carries two `xfail(strict=True)` tests: canceling a task parked in
`input-required` reports success but has no effect — the task stays parked, in the response
and on a later `tasks/get`. Marked strict so they fail loudly if a2acode ever fixes it.
Relevant if you are building a UI that offers "cancel" on an approval prompt.
