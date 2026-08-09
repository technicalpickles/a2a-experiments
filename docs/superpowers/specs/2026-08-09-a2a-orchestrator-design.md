# a2a-orchestrator: the consumer the rig exists for

**Date:** 2026-08-09 · **Phase:** 6, final bullet (PLAN.md) · **Task:** taskwarrior `9b3c2a04`

Builds the frontend and orchestrating agent against the multi-repo rig — the deliverable
DESIGN-v3 §1 names, and the reason everything else in this repo exists. The orchestrator is
**an agent you chat with**, which delegates to repo agents over A2A; the frontend is a React
UI where a human holds those chats, watches the delegated repo sessions stream side by side,
and answers permission gates.

This spec settles the questions Phase 6's bullet opens. Its architectural decisions graduate
into DESIGN-v3 once implemented, per this repo's convention that DESIGN-v3 is the plan of
record. It also settles the open question carried in `.parkinglot`: **the consumer lives in
this repo**, as a new top-level `a2a-orchestrator/` directory, incubating subtree-style the
same way `a2a-rig/` does, extractable later by the same mechanism.

## Domain model

```
Project  (a grouping: which repos, and every chat about them)
└── Orchestrator chat  (one conversation with the orchestrator agent; live or replay)
    └── Repo session   (an A2A conversation the orchestrator opens with one repo agent)
```

- **Project** — a named set of repos drawn from the rig's index, plus the chats accumulated
  against them. Defined declaratively in `projects.yaml` (config-on-disk, consistent with
  the rig's directory-is-identity philosophy), not created in the UI:
  `demo-shop: [billing-api, checkout-web, infra-terraform]`.
- **Orchestrator chat** — one conversation with the orchestrator agent, multi-turn. A live
  chat has a real Claude Agent SDK session behind it; a replay chat has a recorded trace.
- **Repo session** — an A2A conversation (a `contextId`, which the rig already serves
  multi-turn) with one repo agent. A chat can hold several, across repos or with the same
  repo repeatedly.

**A2A lives at exactly one seam: orchestrator ↔ repo session.** Each repo session is a real
A2A client conversation with a repo-specific agent. Today those agents are the rig's fakes;
structurally each is identical to a real `a2acode serve --cwd <checkout>` on a per-repo
checkout. Everything above the seam — frontend, API, chats, projects — cannot tell the
difference. That is the rig's founding bet (the fake *is* the real producer minus the
model), made load-bearing in the consumer's architecture: pointing a project at real
a2acode-per-checkout instances is a configuration change, not a code change (UC7).

## Context

Decisions made during brainstorming, in the order they shaped the design:

- **One system, both halves.** Agents and frontend designed together — the orchestrator's
  API is the interface between them, and designing either alone would have meant designing
  that interface twice.
- **The demo is multi-repo orchestration.** The thing M2 specifically enables that a
  single-repo fake couldn't: one chat coordinating work across `billing-api`,
  `checkout-web`, `infra-terraform`, with the delegated streams visible side by side.
- **The orchestrator is a conversational agent, live-drivable, and live chats record.**
  A real Claude Agent SDK loop decides sequencing when recording; replay walks the recorded
  trace with zero inference. This is the rig's own `RecordingBackend` philosophy applied one
  level up — and per the Phase 7 lesson (recordings corrected hand-imagined assumptions
  twice), **recording ships first**, so the trace format is grounded in real output before
  any replay code trusts it.
- **Recording captures the trace, not the transcript.** The user's turns, the orchestrator's
  visible narration, which repo got dispatched with what prompt, which gate answers — not
  the orchestrator's raw SDK session. Replay therefore needs no model mock.
- **The orchestrator core speaks a2acode's event vocabulary.** Its narration is `text`, a
  repo dispatch is tool-call-shaped, a relayed gate is a `permission`, a turn ends in
  `result`. Two payoffs: the chat pane and repo panes share one set of renderers, and an
  orchestrator that speaks `BackendEvent` could later be mounted as an actual a2acode
  backend — a true A2A agent, upstreamable like `playback` — without building that now. A
  recorded chat is then structurally *a scenario one level up*, same format family as the
  rig's plays.
- **The frontend is a web UI** (Vite + React + TS). Rich interactions — live multi-pane
  streams, diff rendering, gate cards — were the stated priority, and a TUI or
  server-rendered UI fights that grain. Testability pushed the same way; Playwright drives
  a browser first-class.
- **The browser speaks only to the orchestrator API.** The orchestrator holds the A2A
  client connections and re-exposes one aggregated SSE stream per chat. The frontend never
  speaks A2A.
- **The human is a full participant.** Opens chats, sends turns, answers gates.
- **Exit is demo + tests.** Working browser demo, pytest suite for the orchestrator,
  Playwright e2e — the automated paths run with zero inference.

## Use cases

The design is checked against these; each names the machinery that serves it.

- **UC1 — Record a chat.** Talk to the live orchestrator about a project (terminal REPL
  first, browser once the frontend exists), answer its repo agents' gates as they surface,
  and a scrubbed trace lands in `traces/<project>/`. *Served by:* `orchestrator-core`.
- **UC2 — Inspect a recorded chat.** Replay it in the browser at `interactive` pacing:
  step through turns and gates by hand, stare at plans/diffs/tool activity at your own
  speed. *Served by:* replay + `interactive` pacing.
- **UC3 — Demo to another human.** `demo` pacing: hands-free, dwells at turns and gates so
  the audience sees each beat; two repos streaming side by side makes the multi-repo point.
  *Served by:* `demo` pacing.
- **UC4 — The frontend dev loop.** Iterate on a React renderer while the same chat replays
  identically in milliseconds on every reload. *Served by:* deep links —
  `?project=X&trace=Y&pacing=headless&autostart=1` re-runs with zero clicks under Vite hot
  reload.
- **UC5 — Automated regression.** pytest and Playwright at `headless` pacing, zero
  inference, CI-able. *Served by:* `e2e-suite`.
- **UC6 — Watch failure handling.** A recorded chat that dispatches to `infra-terraform`
  (whose default play fails) shows how a failed step and failed turn render. *Served by:*
  a deliberately-failing recorded trace in the library.
- **UC7 — Swap the rig for the real thing.** Point a project's repos at live
  `a2acode serve --cwd <checkout>` instances; chats, gates, panes all work unchanged, now
  with real inference behind the repo agents. *Served by:* the A2A seam (Domain model);
  config change only.

## Layout

```
a2a-orchestrator/
  pyproject.toml               # uv project, mirrors a2a-rig conventions
  src/a2a_orchestrator/        # orchestrator: core, API, recorder, replay
  frontend/                    # Vite + React + TS
    e2e/                       # Playwright (same toolchain as the frontend)
  projects.yaml                # project → repos mapping
  traces/<project>/            # recorded chats, scrubbed, checked in
  tests/                       # pytest
  README.md                    # self-contained, like a2a-rig's
```

Two toolchains under one directory (uv + npm), each self-contained at its own root. In
development, Vite's dev server proxies `/api` to the orchestrator; for the demo, the
orchestrator serves the built frontend statically, so one process serves everything.

Default ports: rig at 9200 (existing convention), orchestrator at 9300.

## The orchestrator agent

`orch-serve` hosts it: one process, many chats, the way the rig is one process, many repos.
One chat engine, two brains — the same pattern as the rig's swappable backends:

**Live chats** each run one Claude Agent SDK session inside the process, scoped to the
project through its tools: `list_repos()` returns only the project's repos, and
`send_to_repo(repo, prompt, session?)` opens or continues a repo session (an A2A
`contextId`) and blocks until that task is terminal. The model decides which repos to
involve, what to ask, in what order; parallel dispatch falls out of the model issuing
multiple `send_to_repo` calls in one turn. Multi-turn chat is the SDK session continuing.
**Gates never route to the model.** When a repo session parks in `input-required`, the
orchestrator surfaces the gate to the human, relays the answer over A2A, and the dispatch
resumes. Gate decisions are human decisions, and they are what gets recorded.

**Replay chats** walk a trace: emit the recorded narration, dispatch each recorded step to
its repo, pause at recorded user turns and gates per the pacing mode. No inference, no SDK,
millisecond turns. A repo task failing marks the turn failed and skips its remaining steps.

**The recorder** tees live chats into `traces/<project>/*.yaml`. Scrubbed like Phase 7's
recordings: shape kept, volatile identifiers (session ids, costs) dropped or rounded.
Before the frontend exists, recording runs through a terminal chat REPL in
`orch-record` — type turns, answer gates y/n.

### Trace format

The schema below is the target; **the first real recording is the authority**, and
`orchestrator-core` is expected to correct it the way Phase 7's recordings corrected the
scenario format. Tentative shape — turn-structured, events in the orchestrator's vocabulary:

```yaml
# traces/demo-shop/ship-health-check.yaml — recorded from a live chat
project: demo-shop
turns:
  - user: "Add a health endpoint to billing-api and surface it in checkout-web"
    events:
      - text: "I'll add the endpoint to billing-api first, then wire checkout-web to it.\n"
      - dispatch:
          repo: billing-api
          prompt: "Add a /health endpoint returning service status"
          gates:
            - { tool: Bash, answer: allow }   # tool is informational; matched by order
      - dispatch:
          repo: checkout-web
          prompt: "Show billing service health from the new /health endpoint"
  - user: "Also handle billing being down"
    events:
      - dispatch:
          repo: checkout-web
          session: continue     # same repo session (contextId) as the earlier dispatch
          prompt: "Handle an unreachable billing service in the health display"
```

- Turns are ordered; a turn's events are ordered; concurrency representation waits for a
  real recording that actually dispatched in parallel.
- Gates within a dispatch are matched **by order of arrival**, not by tool name — the
  repo's scenario controls what gates appear; the trace answers them in sequence.
- Traces are linear. They do not branch; the rig's repo scenarios do
  (`on_allow`/`on_deny`). A path not taken during recording is a path replay cannot take
  (see gate and turn semantics).
- To demo or test a deny branch, **record a deny chat** — one answer per gate per
  recording, exactly the rig's own pattern (`20-recorded-planmode.yaml` is the deny
  recording).

## The orchestrator API

The only surface the frontend knows. Small on purpose:

| Endpoint | Purpose |
|---|---|
| `GET /api/projects` | Projects from `projects.yaml`, each with its repos and chats |
| `GET /api/projects/{p}/traces` | Recorded chats available to replay |
| `POST /api/projects/{p}/chats` | Open a chat: `{mode: live\|replay, trace?, pacing, demo_dwell_ms?}` |
| `GET /api/chats/{id}` | Chat snapshot: turns so far, repo sessions, status |
| `POST /api/chats/{id}/messages` | Send a user turn (live: free text; replay: advances the recorded turn) |
| `GET /api/chats/{id}/events` | SSE stream, resumable via `Last-Event-ID` |
| `POST /api/chats/{id}/gates/{gate_id}` | `{answer: allow\|deny}` |

The SSE stream interleaves two tagged sources: orchestrator events (the chat pane) and repo
session events (the repo panes) — `{id, source: orchestrator|repo, repo?, session_id?,
type, payload}` — where `type` is a2acode's event vocabulary in both cases, plus lifecycle
markers (`chat_opened`, `turn_started`, `gate_opened`, `gate_answered`, `turn_finished`,
`chat_failed`).

Chat state and event logs are **in-memory only**. Chats are ephemeral dev-rig artifacts
(the durable form of a chat is its trace); a dropped SSE connection resumes from the
in-memory log, and an orchestrator restart forgets everything. No database until something
needs one.

## Gate and turn semantics, and pacing

The rule, generalized: **in replay, only recorded actions are enabled.** That covers both
kinds of human action:

- **Gates:** the gate card pauses and the human clicks, but only the recorded answer's
  button is enabled; the other is disabled with a "not recorded" hint.
- **User turns:** the message input offers the recorded next message — press send to
  advance — instead of free text.

Replay cannot diverge into a path that was never recorded, which keeps linear traces
coherent by construction rather than by documented caveat. During **live** chats both
actions are free, and both are recorded.

Pacing is a chat-level setting chosen at open:

- **`interactive`** — recorded turns and gates wait indefinitely for the click. UI
  default; manual inspection.
- **`demo`** — recorded turns and gates auto-fire after a dwell (default 1500ms) so a
  watcher sees the pause, the highlighted action, then the resume. Hands-free but legible.
- **`headless`** — recorded actions fire immediately, zero dwell. What pytest and
  Playwright use when not deliberately clicking.

The dwell applies at recorded actions only; dispatches proceed as their tasks complete. And
this knob is orchestrator-level pacing only. Event pacing *inside* a repo's stream is
already the rig's job (`delay_ms`, `PLAYBACK_SPEED`); the orchestrator does not duplicate
it.

## The frontend

Vite + React + TS, no state library beyond `useReducer`/context until something demands
one. The SSE stream feeds a single reducer; components render from that state:

- **Project picker + chat sidebar** — projects from the API; each project lists its chats
  (open one live, or replay a trace).
- **Chat pane** — the conversation with the orchestrator: user turns, its narration, its
  dispatches summarized inline. In replay, the input offers the recorded next turn.
- **Repo session panes** — one per repo session, rendering by event type: plan steps with
  status, tool activity, `file_change` as a real diff view, streamed text, result with
  cost. Same renderers as the chat pane — one vocabulary.
- **Gate cards** — inline where the gate arose: tool, input, Allow/Deny (replay:
  non-recorded answer disabled), the recorded answer highlighted.
- **Deep links** — `?project=&trace=&pacing=&autostart=1` for UC4's zero-click reload
  loop.

## Error handling

- **Repo task fails** (a scenario's scripted `error`): the pane shows the failure, the
  turn is marked failed, its remaining dispatches are skipped, the chat stays open. No
  continue-on-error flag until a real trace needs one.
- **Rig unreachable / repo missing from the index**: the chat fails to open (or the
  dispatch fails), naming the repo lookup that failed.
- **Live inference failure**: the turn is marked failed with the SDK error surfaced; never
  silently retried.
- **SSE drop**: browser auto-reconnects with `Last-Event-ID`; orchestrator replays the
  tail.

## Testing

- **pytest** (orchestrator): replay engine and API driven against a real `rig-serve`
  subprocess, reusing the rig harness's spawn-and-wait pattern. The trace
  record→YAML→replay round-trip gets a test with a scripted driver standing in for the SDK
  loop — which, per the test-that-supplies-what-it-tests smell (four for four now), proves
  *serialization only*. The evidence traces work is a **real recorded chat checked into
  `traces/`**.
- **Playwright** (e2e): browser → orchestrator → rig with zero inference. Replay a chat,
  assert the chat pane and both repo panes stream; replay the deny trace, click its
  enabled Deny, assert the `on_deny` branch renders; a headless-pacing replay completes
  unattended.
- **Live path**: manual and on-demand, like the rig's `--backend claude` marks. Never CI.

## Milestones

Slugs, not numbers, per convention. Recording precedes replay by design.

- **`orchestrator-core`** — conversational live loop (SDK session per chat,
  `list_repos`/`send_to_repo` tools), A2A fan-out with repo sessions, recorder, terminal
  chat REPL (`orch-record`). Exit: real live chats against the rig recorded, scrubbed, and
  checked in — covering an allow path, a deny path, and a failing dispatch (UC6).
- **`replay-engine`** — projects/chats API (SSE, messages, gates, pacing) + replay,
  `orch-serve`. Exit: pytest green against the rig, zero inference.
- **`web-frontend`** — project picker, chat sidebar, chat pane, repo session panes, gate
  cards, deep links. Exit: the browser demo — open a replay chat, watch the chat pane and
  two repo panes, advance a recorded turn, answer a gate from the UI.
- **`e2e-suite`** — Playwright green, zero inference; PLAN.md Phase 6 bullet checked; this
  spec's decisions graduate to DESIGN-v3.

## Risks

- **Trace format is imagined until `orchestrator-core` records one.** Mitigated by
  ordering: the recorder ships first and owns the format; replay conforms to what
  recording produced.
- **Two toolchains in one incubating directory.** Accepted cost; each root is
  self-contained, and extraction-later inherits both cleanly.
- **Live mode depends on Claude Agent SDK behavior** (parallel tool calls, session
  continuation, gate timing). Bounded: live is the recording instrument and a demo mode,
  never the test path.
- **The domain model is three layers deep** (project → chat → repo session) on day one.
  Bounded by the layers being thin: a project is a YAML entry, a chat is a session plus an
  event log, a repo session is a `contextId`. If any layer fails to earn its keep in
  `orchestrator-core`, collapse it before `replay-engine` builds API surface on it.
- **a2acode event-vocabulary evolution** hits the orchestrator's event stream and frontend
  renderers the same way it hits the rig's scenarios; the same pinning-and-refresh
  tripwire covers both.
