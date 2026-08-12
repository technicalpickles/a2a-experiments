# AG-UI native: the cockpit's conversation plane, reoriented

**Date:** 2026-08-12 · **Provenance:** revisits one decision of
`2026-08-09-a2a-orchestrator-design.md` ("The browser speaks A2A for conversation, REST
for management") in light of the AG-UI protocol and its ecosystem position. Everything
else in that spec — missions, catalog, worktrees, the rig substrate, the recording plan —
stands.

In one sentence: the browser stops speaking A2A and speaks AG-UI instead; the service
stops being a byte relay and becomes the A2A client itself, translating between the two
vocabularies at a single seam; the cockpit's chat surface is CopilotKit React rather than
hand-rolled panes.

## Background: AG-UI, enough to work with

AG-UI (Agent-User Interaction Protocol, `docs.ag-ui.com`, `github.com/ag-ui-protocol`) is
an open, event-based protocol standardizing how agents talk to user-facing applications —
the third leg of the stool this project already stands on two legs of: **MCP gives agents
tools, A2A connects agents to agents, AG-UI connects agents to frontends.** It came out
of CopilotKit (who remain its first party), has first-party integrations across the major
agent frameworks including the Claude Agent SDK, and an official bridge to A2A.

The mechanics, as much as this design needs:

- **A run is the unit of interaction.** The client POSTs a `RunAgentInput` — carrying
  `threadId` (the conversation), `runId` (this exchange), the message history, the
  frontend's registered tools, and optional state — and receives an SSE stream of typed
  events until the run ends. One user turn = one run.
- **Events are a small, flat vocabulary.** Lifecycle: `RUN_STARTED`, `RUN_FINISHED`,
  `RUN_ERROR`, `STEP_STARTED`/`STEP_FINISHED`. Assistant text streams as
  `TEXT_MESSAGE_START` / `TEXT_MESSAGE_CONTENT` (deltas) / `TEXT_MESSAGE_END`. Tool
  invocations stream as `TOOL_CALL_START` / `TOOL_CALL_ARGS` / `TOOL_CALL_END`, with
  results returning as messages on a later run. Shared state moves via `STATE_SNAPSHOT`
  (whole object) and `STATE_DELTA` (RFC 6902 JSON Patch). `CUSTOM` is the typed escape
  hatch. That's essentially the whole protocol.
- **Frontend tools are how agents reach into the UI** — and, run in reverse, how
  humans-in-the-loop work. The client declares tools in `RunAgentInput`; the agent emits
  a tool call for one of them; the run ends; the human (or browser code) produces the
  result; the next run carries it back. "Ask the user for permission" is just a tool
  whose renderer is an approval card and whose result is the decision.
- **Transport is deliberately boring:** HTTP POST + SSE is the reference shape
  (`HttpAgent` in the `@ag-ui/client` npm package speaks it; the `ag-ui-protocol` PyPI
  package provides the event types and SSE encoder for the server side). Middleware
  exists for other transports and for bridging — including `@ag-ui/a2a`, which wraps an
  A2A endpoint and re-emits its stream as AG-UI events.
- **CopilotKit consumes AG-UI natively.** Its React provider binds an agent (by name) and
  a `threadId`; its chat components render the event stream — streaming text, markdown,
  tool-call renderers, loading states — and its `renderAndWaitForResponse` action idiom
  implements the human-in-the-loop tool round-trip with no bespoke choreography.

The resonance with this repo's A2A vocabulary is strong and non-accidental: a thread is a
`contextId`, a run is roughly a task, tool-call-then-wait is `input-required`. The
protocols are siblings designed to meet at exactly the seam this design places them.

## The target state

```
Browser — CopilotKit React
  one provider per chat pane: agent="repo-chat", threadId=<contextId>
  chat component renders the stream; ApprovalCard renders request_permission
   │
   │  AG-UI: POST /agui/run (RunAgentInput) → SSE of AG-UI events
   ▼
orch-serve — the service, one process
  agui.py: threadId → chat lookup (the store), one run per turn
  a2a_client.py: holds the A2A conversation per chat — contextId, taskId, parked state
  translate.py: A2A events ↔ AG-UI vocabulary, both directions tested
   │
   │  A2A: JSON-RPC message/stream over SSE
   ▼
repo agents — a2acode on a worktree, or the playback rig, indistinguishably
```

**A2A lives at exactly one seam — service ↔ repo agent.** The original spec's sentence,
with "cockpit-side" now meaning the service rather than the browser tab. No A2A client,
SDK, or card exists in the browser.

**A turn, end to end.** The user sends a message in a pane. CopilotKit POSTs a
`RunAgentInput` to `/agui/run`; the service resolves `threadId` to the chat and its
upstream, emits `RUN_STARTED`, and sends the text as an A2A message on the chat's
`contextId`. As the upstream streams back, `translate.py` maps each event: narration
becomes steps, artifact text becomes streaming assistant text, completion becomes
`RUN_FINISHED`, failure becomes `RUN_ERROR` (pinning the `infra-terraform` rendering).
The mapping in full:

| A2A (as a2acode emits it)                            | AG-UI                                                        |
| ---------------------------------------------------- | ------------------------------------------------------------ |
| `task` (id, contextId)                               | `RUN_STARTED` (threadId=contextId; runId minted; taskId held server-side) |
| `statusUpdate` state=working, message parts           | `STEP_STARTED`/`STEP_FINISHED` or `CUSTOM` activity — narration becomes structure, not log lines |
| `statusUpdate` state=input_required + `a2acode_permission` | `TOOL_CALL_START`/`ARGS`/`END` (tool `request_permission`), then `RUN_FINISHED` |
| `artifactUpdate` text parts                          | `TEXT_MESSAGE_START`/`CONTENT`/`END` — real incremental streaming instead of one append per artifact |
| `statusUpdate` state=completed                       | `RUN_FINISHED`                                               |
| `statusUpdate` state=failed / stream error           | `RUN_ERROR` (message from the status text)                   |
| `statusUpdate` state=canceled                        | `RUN_FINISHED` + `CUSTOM` cancellation note (revisit if AG-UI grows first-class cancel) |

Unknown or future a2acode event shapes pass through as `CUSTOM` rather than being
dropped — the cockpit's rich rendering keys on a2acode's shapes by choice (per the
original spec), and `CUSTOM` keeps any other A2A agent a valid counterparty with generic
rendering.

**The seam is two-way, and the reverse direction is the trickier half.**
`RunAgentInput` carries the full message history every run, so the service must decide
what is *new*: fresh user text becomes a new A2A message on the chat's `contextId`; a
`request_permission` tool result becomes the resume message on the parked `taskId`.
That includes mapping the tool result's structured payload back to the resume text
a2acode's boolean pipe expects (today's `ChatPane.answer()` sends the literal strings
`allow`/`deny`). This inbound mapping gets the same treatment as the outbound one: its
own tested function (`translate.py` houses both directions), not logic smeared through
the endpoint — it is exactly where a hand-imagined assumption would bite.

**An approval, end to end.** The upstream parks in `input_required` with
`a2acode_permission` metadata (`{tool, request_id, input}`, observed in Phases 0–4). The
translator emits a `request_permission` tool call carrying that payload verbatim and
finishes the run; the service remembers the parked `taskId` for the chat. CopilotKit's
`renderAndWaitForResponse` renders `ApprovalCard` in the flow of the chat; the user's
allow/deny arrives as the tool result on the next run; the service turns it into today's
resume semantics — a new A2A message on the same `taskId`. Deny works identically (the
scenario answers "Skipped the test run"). Note the state that moved: the parked taskId
was `ChatPane`'s problem; it is now the service's, which is also what makes
reconnect-after-reload tractable later.

**Identifiers.** `threadId` = the chat's `contextId`, minted at chat-open exactly as
today. `runId` is minted per turn. A2A's `taskId` never reaches the browser as a thing it
must track — it appears only inside the permission payload, and the *service* owns
which task is parked.

**Where the parked task lives:** an in-memory dict on `a2a_client.py`, keyed by
`contextId` — not a store column. A service restart loses the park, which is the same
deferral class as reload/reconnect (see Risks): both resolve together when the event
log lands with the recording milestone. Named here so the store schema isn't grown
speculatively.

## What changes from today

- **`frontend/src/a2a.ts` is deleted, not ported.** The hand-rolled a2a-js client, its
  `ChatEvent` distillation, and its stream-draining generator all move server-side (the
  distillation *is* `translate.py`'s starting point — it already identifies task, status,
  permission, and artifact events; the translation table above is that function's spec).
- **`ChatPane.tsx` becomes thin:** a CopilotKit provider + chat component per pane,
  replacing the log-item list, the busy flag, and the draft form. `ApprovalCard.tsx`
  survives nearly unchanged as `request_permission`'s renderer.
- **`proxy.py` is deleted, along with `test_proxy.py`.** The card rewriting (both
  `localhost` and `127.0.0.1` spellings), the hop-by-hop filtering, and the load-bearing
  trailing slash all existed because a browser-resident A2A client escapes the proxy
  when a card advertises upstream's origin. Its only consumer was that client, and this
  design deletes the client — a kept proxy would be dead code with live tests. Debugging
  needs are covered better without it: curl the upstream directly (no rewrite in the
  way), or read the translator's typed event log. Git history preserves the working
  browser-A2A-client existence proof; the 2026-08-09 spec preserves the survey note.
- **The service grows the conversation plane:** `agui.py` (the endpoint), `translate.py`
  (the mapping), `a2a_client.py` (the service-side A2A conversation, grown from the
  pytest harness's existing client machinery). The REST plane (`/api/*`), the store, and
  the catalog are untouched; `ChatRef` drops `a2a_url` — the frontend needs only
  `context_id` and the shared endpoint.
- **The observation point improves.** The spec made the proxy the observation point for
  session tracking, recording, and the approval inbox — but a byte relay observes bytes.
  The translator observes typed events: it already knows which event is a permission,
  which is narration, which is an artifact. The recorder and approval-inbox milestones
  hook the translator instead of re-parsing relayed NDJSON. Recording format is
  unaffected — it captures the conversation's shape, which the translator sees more
  legibly than the proxy ever did.

## Domain model: borrowed where possible, owned where necessary

The store's schema does not change — missions and chats, two tables, exactly as
shipped. The audit of what's ours versus the protocols':

- **Ours, and staying:** `Mission` (no protocol has a coordination unit spanning agent
  conversations) and `Chat` (not a thread — the join entity binding mission + agent +
  upstream_url to a conversation). The leverage is in the identifier: `context_id` is
  one id playing three roles — our primary key, A2A's `contextId`, AG-UI's `threadId`.
  The catalog entry (`{name, description, card_url}`) also stays: A2A standardizes the
  card, not multi-agent discovery, so a minimal index pointer is a necessary invention.
- **Ours, and dying:** `ChatEvent` (a2a.ts's private event vocabulary — AG-UI is the
  standardized version of exactly that distillation) and `LogItem` (ChatPane's
  who-said-it triple, replaced by AG-UI message roles and CopilotKit rendering).
- **Borrowed wholesale:** runs (`runId`, per turn, never persisted), tasks (`taskId`,
  parked in memory), and the permission payload (a2acode's shape, verbatim).
- **The one contract we mint:** AG-UI leaves frontend tool names and result shapes to
  the app, so `request_permission` is our only new wire vocabulary. Name:
  `request_permission`. Args: the `a2acode_permission` payload verbatim. Result:
  `{decision: "allow" | "deny"}` — the inbound translate function maps it to the
  resume text upstream expects. Nothing else about it is ours.

**History is advisory; the tail is the message.** AG-UI's client-sends-full-history
design assumes a stateless agent backend. Ours is stateful — A2A's `contextId` means
a2acode holds the real conversation (the Claude session). So the service reads
`RunAgentInput.messages` only for the new tail, stores no message history, and the
schema stays two tables. When persistence arrives (recording milestone, reload
replay), the log format is AG-UI events — the standard shape the translator already
emits — not a third vocabulary.

## Decisions

- **The browser speaks AG-UI; the reversal, accounted for.** The 2026-08-09 spec chose
  browser-side A2A deliberately: "a bespoke chat API would have shadowed A2A with a
  homemade protocol, the exact drift this project exists to prevent." That argument was
  against inventing a private protocol; AG-UI is not that — it is the ecosystem's
  standard for this exact seam, with A2A as its designed sibling. The drift risk is now
  handled by a **typed translation layer exercised against the rig**: playback emits the
  same A2A events a real a2acode run would, `translate.py` maps them identically every
  time, and pytest pins the resulting AG-UI stream. The fake still can't drift from the
  real producer, and the translation can't drift either, because it's one tested
  function of the same events. Two side effects of the old decision, honestly
  surrendered: the card-rewrite proxy is deleted outright (its only consumer was the
  browser client), and "a working browser A2A client UI" — which the ecosystem visibly
  lacked, and still does — stops being a side deliverable. `a2a.ts` and the proxy remain
  in git history as the existence proof; the cockpit's mission is coordination UX, not
  filling that gap.
- **Native translation, not the `@ag-ui/a2a` bridge.** The official bridge (browser-side
  `A2AAgent` wrapping an A2A client) was considered and rejected: it is experimental, it
  would keep the card-rewrite proxy alive, and — decisive — the approval flow rides
  a2acode's custom `a2acode_permission` metadata, which a generic bridge surfaces as an
  opaque status. We would write custom middleware to recover what ten lines of our own
  translator do with full knowledge of the payload. The translation is ours,
  server-side, in Python. (The bridge's conversion helpers are worth reading as prior
  art for `translate.py`.)
- **One endpoint, threadId-routed.** CopilotKit registers agents by *name* against *one*
  URL, so per-chat URLs would fight the frontend's grain. `RunAgentInput` carries
  `threadId` anyway, so the contextId-routed proxy's one good idea — routing is a store
  lookup, never a guess — survives with the key moved from the URL path into the
  protocol's own field. One logical agent name (`repo-chat`) covers every repo session,
  because identity lives in the threadId, not the registration.
- **The frontend is CopilotKit React.** The chat surface — message list, streaming
  rendering, markdown, tool-call renderers, input handling, loading states — is bought,
  not built, from the protocol's first party. The escape hatch stays real and is a
  ladder: CopilotKit's headless hooks with our own panes if the components fight the
  cockpit's multi-pane grain, then bare `@ag-ui/client` subscriptions against the same
  endpoint. The service is identical at every rung.
- **Approvals are the `renderAndWaitForResponse` idiom.** The `input_required` +
  metadata pattern maps onto AG-UI's HITL tool round-trip with no invention:
  `request_permission` is a frontend action, `ApprovalCard` is its renderer, `respond()`
  is the decision, and CopilotKit does the park-and-resume choreography. (AG-UI's newer
  Interrupts concept is the alternative; tool calls are the boring, widely-rendered path
  and the one CopilotKit's HITL docs teach.)
- **Runtime topology: direct connection first, Node runtime when it earns it.**
  CopilotKit's production shape puts a small Node `CopilotRuntime` between browser and
  endpoint (the frontend discovers agents from the runtime's `/info` and proxies runs
  through it). CopilotKit v2 also supports connecting the provider straight to an AG-UI
  agent via `HttpAgent` — flagged dev-only upstream, but the guard is about secrets and
  multi-tenancy in browsers, and the cockpit is a localhost/tailnet tool with neither.
  The walking skeleton uses the direct connection: zero new processes, `orch-serve`
  stays the only service. The Node runtime slots in later behind a one-prop seam if
  auth, CopilotKit Cloud features, or its observability ever earn the third process.
- **Missions and worktrees are future shared state, not this milestone.** AG-UI's
  `STATE_SNAPSHOT`/`STATE_DELTA` is the natural home for use case 7 ("inspect the work
  product"): the mission's worktrees, diffs, and costs as a state object the UI binds to
  and the service patches as sessions progress. Named so the endpoint shape doesn't
  preclude it; deliberately out of scope now.

## Layout

```
a2a-orchestrator/
  src/a2a_orchestrator/
    agui.py            # the conversation plane: POST /agui/run, threadId-routed, SSE out
    translate.py       # both directions: A2A events -> AG-UI events, and RunAgentInput -> new-message-or-resume; heavily tested
    a2a_client.py      # service-side A2A conversation per chat (grown from harness code)
    api.py, store.py, catalog.py, app.py, serve.py   # unchanged planes
    (proxy.py deleted, with test_proxy.py)
  frontend/src/
    ChatPane.tsx       # thin: CopilotKit provider (threadId=contextId) + chat component
    ApprovalCard.tsx   # unchanged UI, now request_permission's renderAndWaitForResponse
    (a2a.ts deleted)
```

## Development substrate and tests

The rig's founding bet is load-bearing here, unchanged: `playback` emits
protocol-correct A2A, so the translator is developed and pinned against it with zero
inference, sub-second.

- **pytest:** drive `orch-serve` in front of a `playback` rig (existing subprocess
  pattern), POST a `RunAgentInput`, assert on the AG-UI SSE stream — event order, the
  permission tool call's args, resume-on-tool-result, the failure path. `translate.py`
  additionally gets direct unit tests both ways: recorded A2A event sequences in,
  expected AG-UI sequences out; and `RunAgentInput` fixtures in (fresh text, tool
  result, mixed history), expected new-message-vs-resume decisions out.
- **Playwright:** unchanged in role — allow path, deny path, failure path against the
  rig at `headless` pacing, now through the AG-UI plane and CopilotKit's rendering.
- **Reference run:** one real `a2acode serve` conversation through the new plane before
  declaring the mapping done, per the Phase 7 lesson (recordings corrected hand-imagined
  assumptions twice). The translation table above is hand-imagined until then.
- **Week-one spike:** provider-instance-per-pane with a shared agent name and distinct
  threadIds — the multi-pane assumption most worth verifying first, since CopilotKit's
  docs center single-copilot apps. Run it against a hand-rolled ~30-line SSE echo
  endpoint, no translator yet — it also verifies the Python SSE framing CopilotKit
  will actually accept, killing both top risks before any real code exists to throw
  away. The fallback ladder (headless hooks, then bare client) is named in Decisions;
  the service is identical at every rung.
- **Strangler ordering, deletions last:** spike → service plane (`translate.py`,
  `a2a_client.py`, `agui.py`, TDD'd against the rig) → frontend swap and Playwright →
  reference run → only then delete `a2a.ts`, `proxy.py`, `test_proxy.py`, and
  `ChatRef.a2a_url`. The old plane keeps working until the new one has demoed both
  paths; a failed spike costs a day, not the cockpit.

## Risks and open questions

- **Reload/reconnect.** Today a browser reload could theoretically `tasks/resubscribe`
  through the proxy; AG-UI has no standard resubscribe. Mitigation: the service now
  holds the A2A stream and the parked-task state, so reconnect is an AG-UI-side concern
  only — simplest is replaying the chat's event log from the store on reconnect, which
  the recording milestone wants anyway. Deferred until it bites.
- **The message gap, acknowledged.** No message history is persisted anywhere in this
  milestone: the browser's render log lives in CopilotKit state and a reload loses it,
  even though the upstream conversation survives via `contextId`. Accepted for now —
  the fix is the AG-UI event log named under "History is advisory," and it is a
  tracked followup (taskwarrior `fc4eb2d8`, project `a2a-experiments`), not a
  someday note.
- **Python AG-UI SDK maturity.** The `ag-ui-protocol` package (event types + encoder) is
  young. Exposure is small — we emit events over SSE and need none of its agent
  abstractions — and the event vocabulary is the protocol's stable core. Pin exact
  versions at implementation time; verify the encoder's SSE framing against CopilotKit's
  consumption early (one integration test).
- **Framework buy-in.** CopilotKit brings its styling, its chat semantics, and its
  release cadence — and its v1→v2 API surface is still settling. Accepted for what it
  buys (the entire chat surface, HITL choreography, later shared-state hooks for
  missions) and bounded by the wire being standard AG-UI and the fallback ladder.
- **Losing the browser A2A client.** Named in Decisions; accepted, and total — no debug
  route keeps a vestige alive. If a browser A2A client is ever wanted again, git history
  has a working one and the reasons it worked (the card-rewrite lore included).
- **AskUserQuestion stays unanswerable — by upstream's hand, not ours.** The 2026-08-12
  live run showed questions reaching the browser inside the `a2acode_permission` blob
  with no way to carry an answer back: a2acode's boolean permission pipe drops
  `updated_input` (filed upstream, see UPSTREAM.md). The AG-UI shape sets up nicely for
  a future `ask_user_question` frontend tool with its own card renderer, but the resume
  channel is boolean until a2acode moves — this milestone renders questions through the
  generic approval card exactly as today, and promises nothing more.

## Appendix: verified facts for implementers

Surveyed 2026-08-12:

- `@ag-ui/client` (npm): `HttpAgent` — POSTs `RunAgentInput`, consumes SSE, exposes a
  typed event subscription. The reference transport this design targets.
- `ag-ui-protocol` (PyPI): Python event types and SSE encoder; the AG-UI monorepo
  (`github.com/ag-ui-protocol/ag-ui`) documents the event vocabulary summarized in
  Background.
- CopilotKit v2 (`@copilotkit/react-core/v2` + react-ui): connects directly to an AG-UI
  agent by passing `HttpAgent` instances to the provider (`agents__unsafe_dev_only`,
  documented as dev/prototyping); production shape is a Node `CopilotRuntime`
  (`createCopilotEndpoint`) registering agents by name, discovered by the frontend via
  `/info`. HITL via `renderAndWaitForResponse`. Verify at implementation time:
  provider-per-pane threading behavior and exact v2 API names.
- `@ag-ui/a2a` (npm): the A2A↔AG-UI bridge; experimental. Rejected as the mechanism
  (see Decisions) but prior art for `translate.py`'s conversions.
- a2acode `v0.6.2` behavior (this repo's own Phases 0–4): `a2acode_permission` metadata
  `{tool, request_id, input}` on the `input_required` status message; the stream ends on
  terminal states and `input_required` alike; resume is a new message on the same
  `taskId`. The translator is built against exactly these observed facts, via the rig.
