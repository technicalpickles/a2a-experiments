# a2a-orchestrator: the cockpit and its orchestrator

**Date:** 2026-08-09 · **Phase:** 6, final bullet (PLAN.md) · **Task:** taskwarrior `9b3c2a04`

A **cockpit for coordinating agent work across repos**: you chat with an orchestrator agent
about a piece of related work, it farms tasks out to per-repo coding agents over A2A, and
you watch the sessions stream side by side, answer their permission gates, and steer — from
one place instead of N terminals. The orchestrator is the agent half of the deliverable;
the cockpit (a React web UI) is the frontend half.

The **multi-repo rig is the development substrate**, not the product: the cockpit is built
and tested against the rig's fake repo agents (deterministic, millisecond turns, zero
inference), and pointed at real `a2acode serve --cwd <worktree>` instances when it's being
*used* rather than *built*. Nothing above the A2A seam can tell the difference — that is
the rig's founding bet, made load-bearing here.

This spec settles the questions Phase 6's bullet opens. Its architectural decisions
graduate into DESIGN-v3 once implemented, per this repo's convention that DESIGN-v3 is the
plan of record. It also settles the open question carried in `.parkinglot`: **the consumer
lives in this repo**, as a new top-level `a2a-orchestrator/` directory, incubating
subtree-style the same way `a2a-rig/` does, extractable later by the same mechanism.

## Use cases

Product-voice: how Josh actually uses the cockpit, with real repo agents behind it. (How
it's built and tested lives in "Development substrate," below.)

1. **Start fresh.** Walk into the cockpit, hit new, describe the thing — a Jira ticket
   just picked up, an idea worth exploring. No repo selection, no setup. The orchestrator
   works out which repos matter (or asks), worktrees get pulled in as needed, and a
   mission forms around the conversation — auto-titled, renamable.
2. **Resume a mission.** Come back hours or days later, see the missions, open one, catch
   up: what finished, what's blocked waiting on an answer, what failed. Pick the
   conversation back up.
3. **Cross-repo change.** Ask for something that spans repos; the orchestrator farms it to
   per-repo agents in parallel; the sessions stream side by side and the work converges.
4. **Talk to one repo directly.** Skip the orchestrator for a quick fix or a poke-around —
   open a session straight with one repo's agent inside the mission.
5. **Gate triage.** Several sessions in flight; permission gates surface in one place as
   they arrive; answer them from the cockpit instead of chasing terminals. Arguably the
   core value: attention routing.
6. **Watch and steer.** Glance at mid-flight work, interject in a session, redirect the
   orchestrator without killing anything.
7. **Inspect the work product.** Browse the mission's worktrees — which repo, which
   branch, where it lives on disk, what changed — with per-worktree diffs, results, and
   costs. See what the mission actually produced and decide what moves forward.

## Domain model

```
Catalog  (what's reachable: the repositories the cockpit knows, and how their agents run)
└── Repository  (a codebase: name, canonical clone, agent endpoint or spawn recipe)

Mission  (emergent grouping of related coordinated work: its chats and its worktrees)
├── Worktree           (a working tree of one repository, owned by the mission; where its agents work)
├── Orchestrator chat  (a conversation with the orchestrator agent; live or replay)
│   └── Repo session   (an A2A conversation with the agent on one of the mission's worktrees)
└── Direct chat        (a conversation held straight with one worktree's agent — it IS a repo session)
```

- **Catalog** — the registry of repositories the cockpit can reach. Configuration points
  at the catalog; everything else is runtime.
- **Repository** — a codebase the cockpit knows: a name, where its canonical clone lives,
  and how agents for it run — resolved through a provider: today the rig's index
  (`GET /`, an agent already running per fake repo); later a spawn recipe that launches
  and supervises a2acode per worktree ("Agent process management," below). Repositories
  are shared across missions and carry no mission state; a mission's view of a repository
  is its worktree.
- **Mission** — the unit of grouping: one piece of related work being coordinated — a
  ticket, an exploration — holding the chats it accumulated and the worktrees it touched.
  **Missions are emergent, not predeclared:** created by starting one, never by editing
  config; repos join a mission by being used in it. Missions carry no priority, urgency,
  or importance — they are coordination contexts, not backlog items (taskwarrior remains
  the backlog). Missions are flat; grouping missions into anything larger is an open
  question deliberately deferred.
- **Chat** — one multi-turn conversation inside a mission, whose **counterparty is either
  the orchestrator agent or one repo agent directly**. An orchestrator chat delegates
  (live: a real Claude Agent SDK session; replay: a recorded trace). A direct chat relays
  turns straight to a repo agent over A2A — no orchestrator, no SDK in the loop.
- **Worktree** — a working tree of one repository, owned by one mission: created when the
  mission first touches that repo (worktrunk's `~/worktrees/{repo}/{branch}` world is the
  obvious mechanism), and the place that repo's agents actually work for this mission.
  Worktree is git's own term — the main clone's working directory is itself the "main
  worktree" — so it covers every case a vaguer word like "checkout" would. **One
  worktree per (mission, repo)** — so "send this to billing-api" is unambiguous inside a
  mission — and the same repo appears in two missions via two different worktrees.
  Addressing stays repo-shaped in conversation; the mission resolves it:
  **(mission, repo) → worktree → endpoint**. A worktree can host several sessions at
  once, but **one writer at a time** — an advisory lease, below.
- **Repo session** — an A2A conversation (a `contextId`, which a2acode serves multi-turn)
  with the agent on one of the mission's worktrees.

**The rig conflates repo and worktree by construction** — fake agents have no filesystem,
so the substrate's resolution chain collapses to repo → endpoint, and two missions talking
to `billing-api` hit the same fake. That's contained: everything above the provider seam
addresses (mission, repo), so worktree semantics slot in at `real-agents` without moving
anything above them (see Risks).

**A2A lives at exactly one seam: cockpit-side ↔ repo agent.** Every repo session is a real
A2A client conversation. Today the counterparty is a fake; structurally it is identical to
a real a2acode on a worktree. Everything above the seam — frontend, API, chats, missions —
cannot tell the difference, which is what makes "swap the rig for the real thing" a
configuration change (point the catalog somewhere else), not a code change.

## Decisions that shaped this

In the order they were made during brainstorming:

- **One system, both halves.** Orchestrator and cockpit designed together — the
  orchestrator's API is the interface between them.
- **The orchestrator is a conversational agent, live-drivable, and live chats record.** A
  real Claude Agent SDK loop decides sequencing when live; replay walks a recorded trace
  with zero inference. The rig's `RecordingBackend` philosophy applied one level up — and
  per the Phase 7 lesson (recordings corrected hand-imagined assumptions twice),
  **recording ships before replay**, so the trace format is grounded in real output.
- **Recording captures the trace, not the transcript.** User turns, visible narration,
  dispatches, gate answers — not the raw SDK session. Replay needs no model mock.
- **The orchestrator core speaks a2acode's event vocabulary.** Narration is `text`, a
  dispatch is tool-call-shaped, a relayed gate is a `permission`, a turn ends in `result`.
  Chat pane and repo panes share one set of renderers; a recorded chat is structurally a
  scenario one level up; and an orchestrator that speaks `BackendEvent` could later be
  mounted as an a2acode backend — a true A2A agent, upstreamable like `playback` — without
  building that now.
- **The cockpit is a web UI** (Vite + React + TS). Rich interactions — live multi-pane
  streams, diff rendering, gate cards — were the priority; a TUI or server-rendered UI
  fights that grain, and Playwright drives a browser first-class.
- **The browser speaks A2A for conversation, REST for management** (this reversed an
  earlier draft's "the frontend never speaks A2A"). Chats map onto A2A almost verbatim —
  a chat is a `contextId`, turns are messages, gates are `input-required` — so a bespoke
  chat API would have shadowed A2A with a homemade protocol, the exact drift this project
  exists to prevent. The browser holds a real A2A client; the service is a
  **contextId-routed pass-through proxy** (one A2A endpoint; each conversation's
  contextId is bound to its counterparty — orchestrator or a repo agent — when the chat
  opens). The proxy relays without translating, and the relay is the observation point:
  session tracking, recording, the gate inbox. What doesn't map — missions, worktrees,
  catalog, traces, pacing — is resource CRUD and stays REST. Side effect worth naming: a
  working browser A2A client UI is a thing the ecosystem visibly lacks (a2a-inspector is
  broken, and we know exactly how).
- **The orchestrator mounts as an a2acode backend from day one.** Its A2A surface —
  card, task state machine, streaming, `input-required` — is a2acode's real server, the
  rig's own can't-drift guarantee applied to our own API. Live and replay are both
  `Backend` implementations, mirroring the rig's swappable-backend pattern one level up.
- **Missions are emergent** (this reversed an earlier draft's predeclared `projects.yaml`):
  the fresh-start use case doesn't survive upfront repo selection, so config shrank to the
  catalog and the grouping became runtime state.
- **Exit is demo + tests.** Working cockpit against the rig, pytest for the service,
  Playwright e2e — automated paths zero-inference.

## Development substrate

The rig is how this gets built without burning inference or waiting on real agents.
Product use cases above assume real repo agents; these are the *builder's* use cases:

- **Deterministic dev loop.** Iterate on a React renderer while the same recorded chat
  replays identically in milliseconds on every reload — deep links
  (`?mission=&trace=&pacing=headless&autostart=1`) make it zero-click under Vite hot
  reload.
- **Automated regression.** pytest and Playwright at `headless` pacing against the rig:
  zero inference, CI-able. Direct chats against `playback` are live *and* deterministic
  at once — free text in, scripted plays out — so even the live path of UC4 is testable.
- **Failure rendering.** A recorded chat that dispatches to `infra-terraform` (whose
  default play fails) pins how failed dispatches and failed turns render.
- **Legible demos.** `demo` pacing replays a recorded chat hands-free with dwells at each
  human action, for showing another person what the cockpit does.
- **Recorded traces as fixtures.** Live chats recorded against the rig become the replay
  library — scrubbed, checked in, covering an allow path, a deny path, and a failure.

## Layout

```
a2a-orchestrator/
  pyproject.toml               # uv project, mirrors a2a-rig conventions
  src/a2a_orchestrator/        # service: orchestrator core, API, recorder, replay
  frontend/                    # the cockpit: Vite + React + TS
    e2e/                       # Playwright (same toolchain as the frontend)
  catalog.yaml                 # where the repo agents are (rig index URL today)
  traces/                      # recorded chats, scrubbed, checked in
  var/                         # runtime state: orchestrator.db (gitignored)
  tests/                       # pytest
  README.md                    # self-contained, like a2a-rig's
```

Two toolchains under one directory (uv + npm), each self-contained at its own root. In
development, Vite's dev server proxies `/api` to the service; for demos, the service serves
the built frontend statically, so one process serves everything.

Default ports: rig at 9200 (existing convention), orchestrator service at 9300.

## The orchestrator agent

`orch-serve` hosts everything: one process, many missions and chats, the way the rig is one
process, many repos. **The orchestrator is served by a2acode's own machinery, mounted
in-process** — its card, task state machine, streaming, and `input-required` handling come
from the real server, and its two brains are `Backend` implementations, the rig's
swappable-backend pattern one level up:

**Live orchestrator chats** each run one Claude Agent SDK session inside the process. Its
tools read the catalog (`list_repos()`) and open or continue repo sessions
(`send_to_repo(repo, prompt, session?)` — dispatch over A2A, block until the task is
terminal). The model decides which repos to involve, what to ask, in what order; parallel
dispatch falls out of the model issuing multiple `send_to_repo` calls in one turn;
multi-turn chat is the SDK session continuing. Repos the chat touches join its mission.
**Gates never route to the model.** When a repo session parks in `input-required`, the
orchestrator parks its own task `input-required` in turn — the gate propagates up the A2A
chain to the browser as protocol, not as a bespoke event — and the human's answer flows
back down the same way. Gate decisions are human decisions, and they are what gets
recorded.

**Replay chats** walk a trace: emit the recorded narration, dispatch each recorded step to
its repo, pause at recorded user turns and gates per the pacing mode. No inference, no SDK,
millisecond turns. A repo task failing marks the turn failed and skips its remaining steps.

**Direct chats** need neither brain, and don't even touch the orchestrator agent: the
browser's A2A client talks to the repo agent through the contextId-routed proxy, which
relays unmodified and observes. Recording a direct chat captures only the user's turns and
gate answers — the repo side is already scripted (rig) or real (production) — so replaying
one is a convenience, not a necessity.

**The recorder** tees live chats into `traces/*.yaml`, scrubbed like Phase 7's recordings:
shape kept, volatile identifiers (session ids, costs) dropped or rounded. Before the
cockpit exists, recording runs through a terminal chat REPL in `orch-record` — type turns,
answer gates y/n.

**Persistence:** SQLite, one file, no server. It tracks missions (title, repos touched),
worktrees (mission, repo, path, branch), chats and their turns, repo sessions
(`contextId`, worktree, status), pending gates, chat event logs, and the agent process
registry (below) — because "resume a mission days later"
is a product use case, and an in-memory-only service can't serve it. The database file lives outside git
(`var/orchestrator.db`, gitignored); traces remain the durable, shareable form of a chat.

**Agent process management:** repo sessions need a running A2A endpoint, and resolving one
is a seam with two providers. The **index provider** points at already-running agents —
the rig's `GET /` index; this is all the substrate needs. The **spawn provider** resolves the full
(mission, repo) → worktree → endpoint chain: on a mission's first touch of a repo it
creates the mission's worktree, launches `a2acode serve --cwd <worktree>`,
tracks the process in the database (worktree, pid, port, health, last activity), reuses it
across sessions, and reaps it when idle. Everything above the seam asks only "give me the
endpoint for this repo, in this mission." The spawn provider is what makes the cockpit a real
daily driver; it lands in the `real-agents` follow-on, not the Phase 6 milestones.

**Agents in a worktree — cardinality and the writer lease.** An a2acode process serves one
`--cwd`, so agent-process ↔ worktree starts 1:1 — but a worktree may host **multiple
concurrent sessions** (a reviewer reading while the builder writes, a Q&A session
answering questions mid-flight), whether multiplexed through one process or spread across
several on the same worktree. The invariant is **one writer per worktree**: the service —
which owns every dispatch — tracks an advisory write lease per worktree; one session holds
it, the rest run read-shaped work concurrently. *Advisory* means it is not enforced by the
filesystem: sessions declare intent at dispatch, the lease gates who may mutate, and
permission gates are the backstop when a "reader" reaches for a write-shaped tool. The
declaration and enforcement mechanism is deliberately TBD (see Open questions).
Parallel *writing* comes from more worktrees, not more writers on one.

**Agent scope.** Git worktrees share `.git` with the main clone, so an agent inherently
sees its *repo* — full history, all branches and refs. That is a feature (diff against
main, read another branch). What it must not touch is other *working trees*: the main
clone's and other missions'. Enforcement there is **policy, not sandbox** — the agent can
technically wander — so the practical controls are permission gates (a `Read` outside the
worktree is visible and deniable) plus whatever sandbox config the spawn provider sets.
Said plainly here so the spec doesn't imply isolation it doesn't have.

### Trace format

The schema below is the target; **the first real recording is the authority**, and
`orchestrator-core` is expected to correct it the way Phase 7's recordings corrected the
scenario format. Tentative shape — turn-structured, events in the orchestrator's
vocabulary:

```yaml
# traces/ship-health-check.yaml — recorded from a live chat
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

## The service's two planes

The cockpit sees two surfaces with a clean split: **conversation is A2A, management is
REST.**

**The conversation plane** is one A2A endpoint at the service (`/a2a`), JSON-RPC + SSE,
driven by a real A2A client in the browser (a2a-js). The service is a **contextId-routed
pass-through proxy**: opening a chat (REST, below) binds a fresh conversation to its
counterparty — the orchestrator agent or one repo agent — and every subsequent A2A call
on that contextId relays to that upstream unmodified. Everything conversational rides the
protocol's own machinery:

- **Turns** are A2A messages; streamed events are the task stream, in a2acode's event
  vocabulary because both counterparties are a2acode servers.
- **Gates** are `input-required` — a repo gate reaches the browser as the task pausing,
  and the answer is an A2A message back on the same task. Nothing bespoke.
- **Repo session panes** are per-session A2A subscriptions (`tasks/resubscribe`) through
  the same endpoint — N streams over HTTP/2, not one hand-rolled aggregate.
- The proxy relays without translating, with **one deliberate exception**: agent cards
  advertise the upstream agent's own origin, so the proxy rewrites card URLs to itself —
  otherwise the browser's client escapes the proxy on its next call (found by prototype;
  see Risks). Beyond that, **the relay is the observation point** — session tracking into
  missions, recording, and the gate inbox all happen by watching traffic, never by
  rewriting it.

**The management plane** is the REST that A2A has no vocabulary for:

| Endpoint | Purpose |
|---|---|
| `GET /api/catalog` | Repositories and their agents (index provider today) |
| `GET /api/missions` | Missions with chats, repos touched, pending gates |
| `POST /api/missions` | Start fresh — creates an empty mission |
| `PATCH /api/missions/{m}` | Rename (auto-title suggested from the first exchange) |
| `GET /api/missions/{m}/worktrees` | The mission's worktrees: repo, branch, path, status |
| `GET /api/traces` | Recorded chats available to replay |
| `POST /api/missions/{m}/chats` | Open a chat: `{agent: orchestrator\|<repo>, mode: live\|replay, trace?, pacing, demo_dwell_ms?}` → binds and returns its `contextId` |
| `GET /api/chats/{id}` | Chat snapshot: turns so far, repo sessions, status |
| `GET /api/gates` | The cross-mission gate inbox |

## Gate and turn semantics, and pacing

The rule: **in replay, only recorded actions are enabled.** That covers both kinds of
human action:

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

The dwell applies at recorded actions only; dispatches proceed as their tasks complete.
And this knob is orchestrator-level pacing only. Event pacing *inside* a repo's stream is
already the rig's job (`delay_ms`, `PLAYBACK_SPEED`); the service does not duplicate it.

## The cockpit

Vite + React + TS, no state library beyond `useReducer`/context until something demands
one. The data layer has the same two planes as the service: an **a2a-js client** for
conversation (chat streams, gate answering, per-session `tasks/resubscribe`
subscriptions), plain `fetch` for management REST. A2A streams feed a single reducer;
components render from that state:

- **Mission list** — the front door: missions with a glanceable status (chats, repos
  touched, gates waiting, last activity). New-mission button is the fresh-start path;
  resume is clicking one.
- **Worktree view** (within a mission) — the mission's worktrees: repo, branch, path,
  what changed, which sessions worked there. The navigation home for "what did this
  mission actually do to disk"; jump here from any repo session pane. (Against the rig
  this view is thin — fake agents have no filesystem — and fills in at `real-agents`.)
- **Chat sidebar** (within a mission) — its chats; open a new one with the orchestrator or
  directly with any catalog repo.
- **Chat pane** — the conversation: user turns, narration, dispatches summarized inline.
  In replay, the input offers the recorded next turn.
- **Repo session panes** — one per repo session, rendering by event type: plan steps with
  status, tool activity, `file_change` as a real diff view, streamed text, result with
  cost. Same renderers as the chat pane — one vocabulary.
- **Gate surface** — gate cards inline where they arise, plus a cockpit-level indicator of
  every gate waiting across missions (UC5's attention routing).
- **Deep links** — `?mission=&chat=&trace=&pacing=&autostart=1` for the substrate dev
  loop.

## Error handling

- **Repo task fails**: the pane shows the failure, the turn is marked failed, its
  remaining dispatches are skipped, the chat stays open. No continue-on-error flag until a
  real trace needs one.
- **Catalog unreachable / repo missing**: the chat or dispatch fails, naming the repo
  lookup that failed.
- **Live inference failure**: the turn is marked failed with the SDK error surfaced;
  never silently retried.
- **Stream drop**: the browser re-subscribes with A2A's own `tasks/resubscribe`, which
  snapshots current task state and resumes — the protocol's recovery machinery, not a
  hand-rolled `Last-Event-ID` scheme.

## Testing

- **pytest** (service): replay engine and API driven against a real `rig-serve`
  subprocess, reusing the rig harness's spawn-and-wait pattern. The trace
  record→YAML→replay round-trip gets a test with a scripted driver standing in for the
  SDK loop — which, per the test-that-supplies-what-it-tests smell (four for four now),
  proves *serialization only*. The evidence traces work is a **real recorded chat checked
  into `traces/`**.
- **Playwright** (e2e): browser → service → rig with zero inference. Start a fresh
  mission and hold a direct chat in free text; replay a chat and assert the chat pane and
  both repo panes stream; replay the deny trace, click its enabled Deny, assert the
  `on_deny` branch renders; a headless-pacing replay completes unattended.
- **Live path**: manual and on-demand, like the rig's `--backend claude` marks. Never CI.

## Milestones

Slugs, not numbers, per convention. Direct sessions lead as the walking skeleton — the
simplest end-to-end slice proves missions, the API envelope, A2A relay, gate machinery,
and renderers before the SDK enters — and recording still precedes replay where traces
exist at all.

- **`direct-sessions`** — catalog (index provider) + SQLite store + the contextId-routed
  A2A proxy + thin management REST (missions, open chat) + thin UI (mission list, chat
  pane, gate card, driven by a2a-js in the browser). Exit: start a fresh mission in the
  browser, chat with a fake repo in free text over genuine A2A, answer a gate via
  `input-required`, zero inference — use cases 1 and 4 working end to end against the
  rig. (The a2a-js-in-browser question is already retired — see Risks.)
- **`orchestrator-core`** — the orchestrator mounted as an a2acode backend: the live loop
  (SDK session per chat, `list_repos`/`send_to_repo` tools), repo sessions joining
  missions, gates propagating up as `input-required`, recorder, terminal chat REPL
  (`orch-record`). Exit: real live chats against the rig recorded, scrubbed,
  and checked in — covering an allow path, a deny path, and a failing dispatch.
- **`replay-engine`** — replay chats (pacing, recorded-actions rule) through the API.
  Exit: pytest green against the rig, zero inference.
- **`web-frontend`** — the full cockpit on the skeleton's bones: mission list with
  glanceable status, chat sidebar, repo session panes, gate surface, deep links. Exit:
  the browser demo — resume a mission, open a replay chat, watch the chat pane and two
  repo panes, advance a recorded turn, answer a gate from the UI.
- **`e2e-suite`** — Playwright green, zero inference; PLAN.md Phase 6 bullet checked;
  this spec's decisions graduate to DESIGN-v3.
- **`real-agents`** *(follow-on, beyond Phase 6's exit)* — the spawn provider: launch and
  supervise a2acode per worktree, process registry in the database, idle reaping;
  worktrunk integration for worktree creation decided here. This is where the cockpit
  stops being a demo and starts being the daily driver.

## Risks

- ~~**a2a-js in the browser is unproven here.**~~ **Retired 2026-08-09**, same day it was
  written, by a throwaway prototype: `@a2a-js/sdk/client` 1.0.1 esbuilds clean for
  `--platform=browser` (56 modules, zero Node builtins, `jose` via its browser
  condition), and in Chrome — through a ~40-line same-origin proxy in front of a live
  `rig-serve` — `createFromUrl` fetched the card and `sendMessageStream` streamed a full
  task lifecycle (submitted → working → artifact → completed) over JSON-RPC + SSE, zero
  inference. One design fact came back: **the pass-through proxy has exactly one
  translation duty** — agent cards advertise the upstream's own origin (in both
  `localhost` and `127.0.0.1` spellings), so the proxy must rewrite card URLs to its own
  origin or the browser's client escapes the proxy on its next call.
- **Trace format is imagined until `orchestrator-core` records one.** Mitigated by
  ordering: the recorder ships first and owns the format; replay conforms to what
  recording produced.
- **Two toolchains in one incubating directory.** Accepted cost; each root is
  self-contained, and extraction-later inherits both cleanly.
- **Live mode depends on Claude Agent SDK behavior** (parallel tool calls, session
  continuation, gate timing). Bounded: live is the recording instrument and the product's
  real mode, but never the test path.
- **The domain model is three layers deep** (mission → chat → repo session) on day one.
  Bounded by the layers being thin: a mission is a metadata record, a chat is a session
  plus an event log, a repo session is a `contextId`. If any layer fails to earn its keep
  in the first two milestones, collapse it before `replay-engine` builds API surface on
  it.
- **Product scope pulls toward daily-driver features** (persistence depth, mission
  lifecycle, multi-machine). The incubation boundary: build what the use cases above
  name, against the rig, and let real usage against real worktrees drive the rest.
- **The substrate can't exercise worktree semantics.** The rig collapses
  (mission, repo) → worktree → endpoint down to repo → endpoint, so worktree creation,
  per-mission isolation, and the worktree view go untested until `real-agents`. Contained
  by the provider seam owning the whole chain — and if it leaks anyway, the rig can grow
  per-mission fake instances then.
- **a2acode event-vocabulary evolution** hits the orchestrator's event stream and cockpit
  renderers the same way it hits the rig's scenarios; the same pinning-and-refresh
  tripwire covers both.

## Open questions (deferred, deliberately)

- **Do missions group into anything larger?** Flat for now; tags or archive can carry
  organization if mission count grows.
- **Auto-titling missions** — suggested from the first exchange; mechanism (model-derived
  vs. heuristic) decided when live chats exist.
- **Worktree management in real use** — who creates/owns the worktrees real repo agents
  sit on (worktrunk integration is the obvious candidate). Deferred to the `real-agents`
  milestone, alongside the spawn provider it belongs to.
- **The writer-lease mechanism** — how a session declares read vs. write intent at
  dispatch, what happens when a reader turns out to need the lease (queue? escalate?
  fail?), and whether enforcement grows past advisory. Model is settled (one writer, N
  readers, per worktree); mechanism decided at `real-agents`.
- **Does a2acode serialize concurrent tasks in one process?** Unknown; determines whether
  multi-session-per-worktree multiplexes through one process or takes a process per
  session. Probe when `real-agents` starts — possibly upstream-shaped, given this repo's
  track record.
- **N worktrees per (mission, repo)** — if a mission ever genuinely wants two parallel
  writing workstreams in one repo. Serialize-by-default until a real mission demands it.
