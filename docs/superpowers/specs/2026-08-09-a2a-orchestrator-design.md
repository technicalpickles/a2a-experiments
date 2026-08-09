# a2a-orchestrator: the consumer the rig exists for

**Date:** 2026-08-09 · **Phase:** 6, final bullet (PLAN.md) · **Task:** taskwarrior `9b3c2a04`

Builds the frontend and orchestrating agent against the multi-repo rig — the deliverable
DESIGN-v3 §1 names, and the reason everything else in this repo exists. One system, two
separated halves: a Python **orchestrator** (an agent that dispatches A2A tasks across the
rig's fake repos, live or from a recorded trace) and a React **frontend** (a browser UI where
a human starts runs, watches per-repo streams side by side, and answers permission gates).

This spec settles the questions Phase 6's bullet opens. Its architectural decisions graduate
into DESIGN-v3 once implemented, per this repo's convention that DESIGN-v3 is the plan of
record. It also settles the open question carried in `.parkinglot`: **the consumer lives in
this repo**, as a new top-level `a2a-orchestrator/` directory, incubating subtree-style the
same way `a2a-rig/` does, extractable later by the same mechanism.

## Context

Decisions made during brainstorming, in the order they shaped the design:

- **One system, both halves.** Agents and frontend are designed together — the orchestrator's
  API is the interface between them, and designing either alone would have meant designing
  that interface twice.
- **The demo is multi-repo orchestration.** The thing M2 specifically enables that a
  single-repo fake couldn't: an agent coordinating work across `billing-api`,
  `checkout-web`, `infra-terraform`, with the streams visible side by side.
- **The orchestrator is live-drivable, and live runs record.** A real Claude Agent SDK loop
  decides sequencing when recording; replay walks the recorded trace with zero inference.
  This is the rig's own `RecordingBackend` philosophy applied one level up — and per the
  Phase 7 lesson (recordings corrected hand-imagined assumptions twice), **recording ships
  first**, so the trace format is grounded in real output before any replay code trusts it.
- **Recording captures the trace, not the transcript.** Which repo, what prompt, what order,
  which gate answers — not the orchestrator's raw SDK session. Replay therefore needs no
  model mock; it walks steps.
- **The frontend is a web UI** (Vite + React + TS). Rich interactions — live multi-pane
  streams, diff rendering, gate modals — were the stated priority, and a TUI or
  server-rendered UI fights that grain. Testability concerns with driving a TUI also pushed
  here; Playwright drives a browser first-class.
- **The browser speaks only to the orchestrator.** The orchestrator holds the N A2A client
  connections and re-exposes one aggregated SSE stream. The frontend never speaks A2A.
- **The human is a full participant.** Starts runs, watches, answers gates from the UI.
- **Exit is demo + tests.** Working browser demo, pytest suite for the orchestrator,
  Playwright e2e — the automated paths run with zero inference.

## Layout

```
a2a-orchestrator/
  pyproject.toml               # uv project, mirrors a2a-rig conventions
  src/a2a_orchestrator/        # orchestrator: core, API, recorder, replay
  frontend/                    # Vite + React + TS
    e2e/                       # Playwright (same toolchain as the frontend)
  traces/                      # recorded orchestration traces, scrubbed, checked in
  tests/                       # pytest
  README.md                    # self-contained, like a2a-rig's
```

Two toolchains under one directory (uv + npm), each self-contained at its own root. In
development, Vite's dev server proxies `/api` to the orchestrator; for the demo, the
orchestrator serves the built frontend statically, so one process serves everything.

Default ports: rig at 9200 (existing convention), orchestrator at 9300.

## The orchestrator

One run engine, two brains — the same pattern as the rig's swappable backends:

**Live mode** is a Claude Agent SDK loop with two tools: `list_repos()` (the rig's `GET /`
index) and `run_task(repo, prompt)` (dispatch an A2A task, block until terminal). The model
decides which repos to involve, what to ask, and in what order; parallel dispatch falls out
of the model issuing multiple `run_task` calls in one turn. **Gates never route to the
model.** When a repo parks in `input-required`, the orchestrator surfaces the gate to the
human (browser, or terminal y/n prompt before the frontend exists), relays the answer over
A2A, and `run_task` resumes. Gate decisions are human decisions, and they are what gets
recorded.

**Replay mode** walks a trace: dispatch each step's prompt to its repo, pause at gates per
the pacing mode, mark the run failed and skip remaining steps if a repo task fails. No
inference, no SDK, millisecond turns.

**The recorder** tees live runs into `traces/*.yaml`. Scrubbed like Phase 7's recordings:
shape kept, volatile identifiers (session ids, costs) dropped or rounded.

### Trace format

The schema below is the target; **the first real recording is the authority**, and the
recorder milestone is expected to correct it the way Phase 7's recordings corrected the
scenario format. Tentative shape:

```yaml
# traces/ship-health-check.yaml — recorded from a live run
goal: "Add a health endpoint to billing-api and surface it in checkout-web"
steps:
  - repo: billing-api
    prompt: "Add a /health endpoint returning service status"
    gates:
      - tool: Bash            # informational; matching is by order, not name
        answer: allow
  - repo: checkout-web
    prompt: "Show billing service health from the new /health endpoint"
```

- Steps are ordered and sequential; concurrency representation waits for a real recording
  that actually dispatched in parallel.
- Gates within a step are matched **by order of arrival**, not by tool name — the repo's
  scenario controls what gates appear; the trace just answers them in sequence.
- Traces are linear. They do not branch; the rig's repo scenarios do (`on_allow`/`on_deny`).
  A path not taken during recording is a path replay cannot take (see gate semantics).
- To demo or test a deny branch, **record a deny run** — one answer per gate per recording,
  exactly the rig's own pattern (`20-recorded-planmode.yaml` is the deny recording).

## The orchestrator API

The only surface the frontend knows. Small on purpose:

| Endpoint | Purpose |
|---|---|
| `GET /api/repos` | Proxied rig index |
| `GET /api/traces` | Available recorded traces |
| `POST /api/runs` | Start a run: `{mode: replay\|live, trace?, goal?, pacing, demo_dwell_ms?}` |
| `GET /api/runs/{id}` | Run status snapshot |
| `GET /api/runs/{id}/events` | SSE stream, resumable via `Last-Event-ID` |
| `POST /api/runs/{id}/gates/{gate_id}` | `{answer: allow\|deny}` |

The SSE stream carries every repo event tagged with its origin —
`{id, repo, task_id, type, payload}` — where `type` is a2acode's own event vocabulary
(`text`, `plan`, `tool_use`, `tool_result`, `file_change`, `notice`, `result`, …) plus
orchestrator-level lifecycle events (`run_started`, `step_started`, `gate_opened`,
`gate_answered`, `step_finished`, `run_finished`, `run_failed`).

Run state and event logs are **in-memory only**. Runs are ephemeral dev-rig artifacts; a
dropped SSE connection resumes from the in-memory log, and an orchestrator restart forgets
everything. No database until something needs one.

## Gate semantics and pacing

During **live** runs, gates pause for the human's answer, and the answer is recorded.

During **replay**, the gate pauses and the human clicks — but **only the recorded answer's
button is enabled**; the other is disabled with a "not recorded" hint. Replay cannot diverge
into an orchestration path that was never recorded, which is what keeps linear traces
coherent by construction rather than by documented caveat.

Pacing is a run-level setting chosen at start:

- **`interactive`** — gates wait indefinitely for the click. UI default; manual inspection.
- **`demo`** — gates auto-apply the recorded answer after a dwell (default 1500ms) so a
  watcher sees the pause, the highlighted answer, then the resume. Hands-free but legible.
- **`headless`** — recorded answers applied immediately, zero dwell. What pytest and
  Playwright use when not deliberately clicking.

The dwell applies at gates only; step transitions proceed as their tasks complete. And this
knob is orchestrator-level pacing only. Event pacing *inside* a repo's stream is
already the rig's job (`delay_ms`, `PLAYBACK_SPEED`); the orchestrator does not duplicate it.

## The frontend

Vite + React + TS, no state library beyond `useReducer`/context until something demands one.
The SSE stream feeds a single reducer; components render from that state:

- **Run launcher** — pick a trace (or enter a live goal), pick pacing, start.
- **Repo panes** — one per active repo task, rendering by event type: plan steps with
  status, tool activity, `file_change` as a real diff view, streamed text, result with cost.
- **Gate cards** — inline in the pane: tool, input, Allow/Deny buttons (replay: non-recorded
  answer disabled), the recorded answer highlighted.
- **Run timeline** — orchestrator-level lifecycle: which steps ran, what's in flight,
  terminal status.

## Error handling

- **Repo task fails** (a scenario's scripted `error`): the pane shows the failure, the run
  is marked failed, remaining steps are skipped. No continue-on-error flag until a real
  scenario needs one.
- **Rig unreachable / repo missing from the index**: the run fails to start, naming the
  repo lookup that failed.
- **Live inference failure**: run marked failed with the SDK error surfaced; never silently
  retried.
- **SSE drop**: browser auto-reconnects with `Last-Event-ID`; orchestrator replays the tail.

## Testing

- **pytest** (orchestrator): replay engine and API driven against a real `rig-serve`
  subprocess, reusing the rig harness's spawn-and-wait pattern. The trace record→YAML→replay
  round-trip gets a test with a scripted driver standing in for the SDK loop — which, per
  the test-that-supplies-what-it-tests smell (four for four now), proves *serialization
  only*. The evidence traces work is a **real recorded run checked into `traces/`**.
- **Playwright** (e2e): browser → orchestrator → rig with zero inference. Start a replay,
  assert both panes stream; replay the deny trace, click its enabled Deny, assert the
  `on_deny` branch renders; a headless-pacing run completes unattended.
- **Live path**: manual and on-demand, like the rig's `--backend claude` marks. Never CI.

## Milestones

Slugs, not numbers, per convention. Recording precedes replay by design.

- **`orchestrator-core`** — live loop (SDK + `list_repos`/`run_task` tools), A2A fan-out,
  recorder, terminal y/n gate prompt, `orch-record` CLI. Exit: one real live orchestration
  run against the rig, first traces (allow and deny runs) scrubbed and checked in.
- **`replay-engine`** — replay + the API (runs, SSE, gates, pacing), `orch-serve`. Exit:
  pytest green against the rig, zero inference.
- **`web-frontend`** — launcher, panes, gate cards, timeline. Exit: the browser demo — start
  a 2-repo replay, watch both streams, answer a gate from the UI.
- **`e2e-suite`** — Playwright green, zero inference; PLAN.md Phase 6 bullet checked; this
  spec's decisions graduate to DESIGN-v3.

## Risks

- **Trace format is imagined until `orchestrator-core` records one.** Mitigated by ordering:
  the recorder ships first and owns the format; replay conforms to what recording produced.
- **Two toolchains in one incubating directory.** Accepted cost of Approach A; each root is
  self-contained, and extraction-later inherits both cleanly.
- **Live mode depends on Claude Agent SDK behavior** (parallel tool calls, gate timing).
  Bounded: live is the recording instrument and a demo mode, never the test path.
- **a2acode event-vocabulary evolution** hits the SSE envelope and frontend renderers the
  same way it hits the rig's scenarios; the same pinning-and-refresh tripwire covers both.
