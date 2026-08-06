# Design Doc — `claude-a2a`: Exposing Claude Code as an A2A Server

*Status: draft for spike · 2026-08-05 · Companion research: `docs/pass-1` (A2A protocol),
`docs/pass-2` (ecosystem/testing), `docs/pass-3` (Claude Agent SDK), `docs/pass-4`
(deterministic backend). Terminology below is A2A spec **v1.0**.*

## 1. Summary

We wrap the Claude Agent SDK — the programmatic form of Claude Code's agent loop — in an A2A
v1.0 server, so any A2A client (Google ADK, CrewAI, Microsoft Agent Framework, another A2A
agent) can delegate coding work to Claude Code as a remote agent. The spike has three legs:

1. **The server:** an `AgentExecutor` that transcodes the Agent SDK's message stream into A2A
   tasks, status updates, and artifacts, with multi-turn conversations and human/agent-in-the-
   loop permission approval.
2. **A test driver:** a layered harness — a2a-tck for conformance, a2a-inspector for
   interactive debugging, and a scripted pytest driver using the official A2A client — so we
   can drive A2A interactions repeatably.
3. **A deterministic backend:** Claude Code pointed at a fake Anthropic Messages API via
   `ANTHROPIC_BASE_URL`, so the whole stack runs scripted, inference-free scenarios until we
   trust the implementation.

**Non-goals for the spike:** production auth, multi-tenancy, push notifications, gRPC/REST
bindings (JSON-RPC only), persistence beyond process lifetime, sandboxing beyond a scratch
workspace directory.

## 2. Prior art and what we take from it

Four projects already put Claude Code behind A2A (see pass-2 §7): **claude-a2a** (TS, streaming
+ file artifacts), **a2claude** (Python — structured metadata, session↔contextId mapping,
`input-required` for permissions, an offline echo backend), **claude-code-agent** (container
isolation), and **claude-code-a2a-multiagent**. None appears to be both current (spec 1.0 —
they predate the 0.3→1.0 breaking changes) and production-shaped. We adopt their recurring
decisions (session mapping, input-required permissions, file-change artifacts, streaming
deltas, workspace isolation) and add what they lack: spec-1.0 targeting, TCK conformance as a
gate, and a deterministic full-stack test rig.

## 3. Stack decision

**Recommendation: Python** for the server and test driver.

- `a2a-sdk` (1.1.2) is the reference A2A implementation with the richest server surface:
  `AgentExecutor`/`RequestContext`/`EventQueue`, the `TaskUpdater` convenience wrapper,
  `DefaultRequestHandlerV2`, pluggable `TaskStore` (in-memory now, SQLite/Postgres later), and
  — critically — a framework contract that **re-invokes `execute()` when input arrives after
  `input-required`**, which is exactly the shape our permission flow needs.
- The test ecosystem is Python-first: a2a-tck is pytest, a2a-inspector is Python+uv, the
  canonical samples are Python.
- Python's `ClaudeSDKClient` is connection-oriented (multi-call sessions, `interrupt()`,
  `receive_response()`), a good fit for one-executor-turn-per-A2A-message.

TypeScript is a legitimate alternative (`@a2a-js/sdk` 1.0.1 is solid; single runtime with the
`claude` binary; the best fake backend, aimock, is TS; claude-a2a is TS prior art) — if we
later want to upstream into a Node codebase, the design maps 1:1 (`ExecutionEventBus` ≙
`EventQueue`, etc.). A polyglot rig is fine meanwhile: Python server + TS aimock coexist
happily since they only meet over HTTP.

## 4. Concept mapping (the heart of the design)

### 4.1 Identity and conversation

| A2A concept | Claude Code / Agent SDK concept | Notes |
|---|---|---|
| `contextId` | Claude **session lineage** (chain of `session_id`s) | Each `resume` mints a *new* session_id, so contextId maps to a chain. Server keeps `SessionRegistry: contextId → {latest_session_id, workspace_dir}`. |
| `Task` | **One turn of the agent loop** = one `query()` run (or one held run spanning permission pauses) | New message with `contextId` but no `taskId` → new Task, executed with `resume=latest_session_id`. |
| Message (ROLE_USER) | The prompt (or follow-up input) fed to `query()` / the pending permission decision | |
| Message (ROLE_AGENT) | Claude's intermediate commentary | Carried inside `TaskStatus.message` on status updates — best-effort context, never the deliverable. |
| `Artifact` | Deliverables: final response text, files created/modified, `structured_output` | Spec §3.7: outputs are Artifacts, not Messages. |
| `CancelTask` | `client.interrupt()` | → `TASK_STATE_CANCELED`. |
| Claude **subagents** | *Not* separate A2A Tasks | They aren't client-addressable units; surface as metadata (`agent_id`, subagent name) on status updates. |
| Claude **background tasks** | Future work: related tasks via `referenceTaskIds` | Out of scope for spike; metadata-only. |

**On "responses as Messages, tasks as Tasks":** the original intuition was to return most
Claude replies as bare A2A `Message`s and reserve `Task` for agentic work. Two facts push the
other way. First, an A2A Message response has no lifecycle — no streaming progress, no
artifacts, no cancel — and we can't know a turn will be trivial *before* running it, because
even "explain this repo" usually reads files (tool use). Second, the spec's own guidance is
Message-for-chit-chat, Task-for-trackable-work, and everything Claude Code does is trackable
work. **Spike decision: always respond with a Task.** There is a known optimization if we
ever want it: the executor may *defer* its first event until either the first tool use /
timeout (→ emit Task) or a one-turn pure-text completion (→ emit Message) — the first stream
event decides which shape the response takes. Not worth the complexity now.

### 4.2 SDK message stream → A2A event stream

The executor consumes `query()`'s async iterator and transcodes:

| SDK message | A2A emission |
|---|---|
| System `init` | `TaskUpdater.start_work()`; metadata: `session_id`, model, tool list |
| Assistant `TextBlock` | Status update (WORKING) whose `status.message` carries a text Part — progress commentary |
| Assistant `ToolUseBlock` | Status update with a `data` Part `{tool, input, tool_use_id}` under a URI-keyed metadata namespace (a2claude's structured-metadata pattern) |
| User `ToolResultBlock` | Status update with truncated result summary in a `data` Part (full results stay server-side) |
| `stream_event` text deltas (`include_partial_messages=True`) | `TaskArtifactUpdateEvent` with `append=true` chunks on a well-known `response` artifact — A2A's only append/chunk mechanism, ideal for live text |
| Result `success` | Final artifacts (see 4.3), then `TaskUpdater.complete()`; metadata: `total_cost_usd`, `usage`, `num_turns`, final `session_id` |
| Result `error_max_turns` / `error_max_budget_usd` / `error_during_execution` | `TaskUpdater.failed()` with the subtype + detail in metadata |
| Result `error_max_structured_output_retries` | `failed()`; note absence of structured output |

Event-ordering rules we must respect (pass-1 §5): first stream event is the `Task`; ordering
preserved; stream closes at terminal states and pauses at interrupted ones; `SubscribeToTask`
re-emits the current Task snapshot first (the SDK's `DefaultRequestHandlerV2` + `QueueManager`
handle this — a reason to not hand-roll the handler).

### 4.3 Artifacts

Three artifact families per task:

- **`response`** — the final assistant text (and the live-streamed chunks above), `mediaType:
  text/markdown`.
- **`file:<relpath>`** — one artifact per file created/modified during the task. Tracked by a
  `PostToolUse` hook on Write/Edit/NotebookEdit (belt) plus a workspace `git status` diff at
  task end (suspenders — catches Bash-side writes). Small text files inline as `text` parts;
  binaries as `raw` (base64) with `mediaType`; oversized files as a manifest `data` part in
  the spike (an HTTP file endpoint is future work).
- **`structured_output`** — a `data` Part when the client requested structured output
  (mapped from an A2A extension/metadata field → SDK `output_format`).

### 4.4 Permissions: `canUseTool` ↔ `input-required`

The signature move of this design. Flow:

1. Executor runs the SDK with `permission_mode="default"` and a `can_use_tool` callback.
2. Callback fires for an unapproved tool → it parks a `PendingDecision` (an `asyncio.Future`)
   in a per-task registry, and the executor emits
   `TaskUpdater.requires_input()` with a `status.message` containing a `data` Part:
   `{kind: "permission_request", tool, input, suggestions}`.
3. The blocking `SendMessage` returns to the client with the task in
   `TASK_STATE_INPUT_REQUIRED`. **The SDK query stays alive, paused inside the callback.**
4. Client sends a follow-up Message on the same `taskId` with a `data` Part
   `{kind: "permission_decision", behavior: "allow"|"deny", updatedInput?, message?}` (or
   plain text — we interpret affirmative/negative text as a fallback).
5. The framework re-invokes `execute()`; the executor sees a pending decision for this task,
   resolves the Future (`PermissionResultAllow`/`Deny`), and resumes streaming from the
   still-alive query.

The same mechanism serves Claude's `AskUserQuestion` (it surfaces as a tool call): question →
`input-required` with the options in a `data` Part; answer → resolved as the tool result.

Constraints and mitigations: the paused query lives in process memory, so `input-required`
tasks don't survive a server restart (spike-acceptable; note in card docs). A decision
timeout (default 10 min) resolves to deny-and-fail so tasks can't wedge forever. Clients that
never want interactivity can request `permission_mode: "dontAsk"` or an allow-list via message
metadata, mapped straight onto SDK options.

### 4.5 Agent card

Served at `/.well-known/agent-card.json`:

- `supportedInterfaces: [{url, protocolBinding: "JSONRPC", protocolVersion: "1.0"}]`
- `capabilities: {streaming: true, pushNotifications: false}` (push is post-spike; the SDK's
  `BasePushNotificationSender` makes it cheap later)
- `skills`: discrete, tagged skills rather than one generic chat skill (a2claude pattern) —
  `implement-feature`, `fix-bug`, `explain-code`, `code-review`, each with `examples`;
  `defaultInputModes: ["text/plain"]`, `defaultOutputModes: ["text/markdown",
  "application/json"]`
- `securitySchemes`: none for localhost spike; single static bearer
  (`HTTPAuthSecurityScheme{scheme: "bearer"}`) as the first hardening step. This server
  executes code — it must never listen beyond localhost without auth.

## 5. Architecture

```
                    ┌──────────────────────────── claude-a2a server (Python) ───────────────────────────┐
 A2A client         │  FastAPI + a2a-sdk routes (agent card / JSON-RPC / SSE)                            │
 (inspector, tck,   │        │                                                                           │
 a2a-cli, pytest,  ─┼─▶ DefaultRequestHandlerV2 ── TaskStore (InMemory → SQLite)                         │
 ADK/CrewAI)        │        │                                                                           │
                    │   ClaudeAgentExecutor                                                              │
                    │     ├─ SessionRegistry   contextId → {session_id, workspace}                       │
                    │     ├─ PendingDecisions  taskId → asyncio.Future (paused canUseTool)               │
                    │     ├─ ArtifactCollector PostToolUse hook + git diff                               │
                    │     └─ ClaudeSDKClient  (claude-agent-sdk, one per active task)                    │
                    │             │  env: ANTHROPIC_BASE_URL, ANTHROPIC_AUTH_TOKEN, hermetic flags       │
                    └─────────────┼──────────────────────────────────────────────────────────────────────┘
                                  ▼
                     claude binary ──HTTP/SSE──▶  real api.anthropic.com   (production)
                                            └──▶  fake backend :4010       (dev/test — aimock or bespoke)
```

Key implementation choices:

- **Lean on `a2a-sdk`'s handler, don't hand-roll.** `DefaultRequestHandlerV2` +
  `InMemoryTaskStore` + `InMemoryQueueManager` give us JSON-RPC + SSE + resubscribe-snapshot +
  task persistence semantics for free; we only write the executor.
- **One executor turn per A2A message.** `execute()` either (a) starts a fresh query
  (new task), (b) resolves a pending permission decision (follow-up on `input-required`), or
  (c) rejects (message to terminal task — the handler already errors for us).
- **Concurrency:** tasks in different contexts run concurrently; tasks within one `contextId`
  are serialized with a per-context lock (a Claude session chain can't be resumed in parallel).
- **Workspace isolation:** `workspaces/<contextId>/` created on first use; `cwd` pinned there
  (which also keys the SDK's transcript storage). Optionally seeded from a configured repo.
  `settingSources` pinned to `[]` or `["project"]` for reproducibility.
- **SDK options per task:** `permission_mode` (default `"default"`), `max_turns`,
  `max_budget` where supported, `include_partial_messages=True`, `env` built from a hermetic
  base (never inherit the host's `ANTHROPIC_*` implicitly — claude-code-agent's `dev-safe`
  lesson).

## 6. Testing: driving A2A interactions

Layered, cheapest-first:

1. **Unit (no Claude, no network):** `ClaudeAgentExecutor` against a `FakeAgentSDK` that
   yields scripted SDK message sequences; assert the exact A2A event stream (event types,
   ordering, states, artifact chunking). This pins the transcoding logic, which is most of
   the novel code.
2. **Protocol conformance:** `a2a-tck` against the running server with the echo/fake
   executor: `./run_tck.py --sut-host http://localhost:9999 --level must` gates CI; SHOULD
   tier tracked as xfail.
3. **Interactive:** `a2a-inspector` (card validation + raw JSON-RPC console) for manual
   debugging; `a2a-cli chat` for quick smoke.
4. **Scripted E2E driver (the "way to drive A2A interactions"):** a pytest suite using
   `a2a-sdk`'s own `ClientFactory` as the driver, running the full stack (our server + real
   `claude` binary + fake Anthropic backend). One test per scenario:
   - `hello` — single turn, pure text → Task completes, `response` artifact.
   - `tool_use` — scripted tool_use → Write → file artifact + status updates in order.
   - `permission` — tool call not allow-listed → `input-required` → approve → complete;
     plus the deny path and the timeout path.
   - `multi_turn` — two tasks sharing a `contextId`; assert session resume happened (the fake
     backend sees the prior conversation in the second request).
   - `cancel` — long scripted run, `CancelTask` mid-stream → CANCELED, interrupt reached the
     binary.
   - `errors` — max_turns exhaustion → FAILED with subtype metadata.
5. **Real-inference smoke (last):** the same pytest scenarios tagged `@live`, run manually
   against the real API once everything above is green.
6. **Cross-framework check (stretch):** point Google ADK's `RemoteA2aAgent` or CrewAI's
   `A2AClientConfig` at the server — proves real-world clients interoperate, not just our
   driver.

## 7. Deterministic backend (the bonus points)

The seam is official and clean (pass-4): the SDK passes env through, so we run the stack with

```bash
ANTHROPIC_BASE_URL=http://localhost:4010
ANTHROPIC_AUTH_TOKEN=test-token            # bearer path: no interactive approval
CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 # hermetic
CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1   # stable request bodies
CLAUDE_CODE_ATTRIBUTION_HEADER=0           # stable system prompt (matching determinism)
```

The fake must serve streaming SSE on `POST /v1/messages?beta=true`, tolerate `HEAD /`,
unknown `anthropic-beta` headers, and `thinking: {"type":"adaptive"}`, and emit errors in
Anthropic's exact envelope (Claude Code string-matches error wording). Optional:
`count_tokens`. Full contract in pass-4 §2.

**Plan A — aimock** (`@copilotkit/aimock`): the only mature mock with Anthropic format + SSE +
tool_use + *turn-sequenced* fixtures (`turnIndex`, `hasToolResult`, `toolResultContains`,
`X-AIMock-Context` per-test scoping). Scenarios like "call 1 → tool_use(Write …), call 2 →
end_turn text" are its native vocabulary. First task: a fidelity check that it accepts Claude
Code's actual request shape.

**Plan B — bespoke scenario server** (~300 lines FastAPI): implements the pass-4 §2 contract
exactly; scenarios are ordered JSON turn scripts (`[{match: {turn: 1}, respond:
{tool_use: …}}, …]`); selected per-test via a header, mirroring aimock's context header.
Cheap insurance, and it doubles as an assertion point (e.g. verifying
`x-claude-code-agent-id` subagent attribution, or that turn 2 contains turn 1's transcript —
how we *prove* session resume works without inference).

**Plan C — record/replay (later):** once `@live` tests run, capture real sessions with
mitmproxy/llm-interceptor and replay through Plan B. Cassette matching must key on stable
signals (turn index, last-message content, session/agent headers) — never full-body equality;
Claude Code's request bodies drift across releases.

Whichever plan, scenario fixtures live in the repo next to the E2E tests that use them.

## 8. Milestones

| # | Deliverable | Exit criteria |
|---|---|---|
| M0 | Skeleton: agent card + echo `AgentExecutor` on a2a-sdk routes | Inspector validates card; TCK MUST tier passes |
| M1 | Fake backend running `hello` scenario; Agent SDK wired single-shot; Task lifecycle + `response` artifact | `hello` E2E green, zero inference |
| M2 | Streaming (status updates, artifact append chunks); file artifacts via hooks + git diff | `tool_use` E2E green; inspector shows live stream |
| M3 | `contextId` ↔ session resume; `input-required` permission flow (allow/deny/timeout) | `multi_turn` + `permission` E2E green |
| M4 | Cancel, `ListTasks`, error mapping; full TCK run; cross-framework client check | `cancel`/`errors` green; TCK MUST 100%; then first `@live` smoke test |

## 9. Risks and open questions

- **Paused-query durability:** `input-required` state held in process memory dies with the
  process. Fine for a spike; a real service needs either deny-on-restart semantics or a
  redesign (persist the decision request; restart the turn with the decision pre-applied via
  allow-rules on resume).
- **a2a-sdk 1.x churn:** the 0.x → 1.x rewrite was recent (routes-based wiring, V2 handler);
  pin versions, expect minor API movement.
- **Spec-version reach:** we target 1.0 only. Some ecosystem clients still speak 0.3; the
  compat layer exists in both SDKs if interop demands it, but it doubles the test matrix —
  decide only if a concrete client needs it.
- **aimock fidelity:** unproven against Claude Code's exact request shape; Plan B bounds this
  risk at ~a day of work.
- **Security:** this is remote code execution by design. Spike stays on localhost; anything
  more needs bearer auth + container isolation (claude-code-agent shows the shape).
- **Cost controls for `@live`:** always set `max_turns` and budget caps; surface
  `total_cost_usd` in task metadata so callers see spend.
- **Open:** should a `data` Part on the incoming message let clients set SDK options
  (model, permission_mode, allow-list) per task, or is that an extension? Leaning: yes via a
  URI-keyed metadata namespace, documented in the card's skill descriptions.

## 10. Repo layout (proposed)

```
claude-a2a/
├── DESIGN.md                  # this doc
├── docs/pass-{1..4}-*.md      # research passes
├── src/claude_a2a/
│   ├── server.py              # FastAPI app + a2a-sdk route wiring + agent card
│   ├── executor.py            # ClaudeAgentExecutor (SDK↔A2A transcoding)
│   ├── sessions.py            # SessionRegistry, per-context locks, workspaces
│   ├── permissions.py         # PendingDecisions, canUseTool bridge
│   └── artifacts.py           # ArtifactCollector (hooks + git diff)
├── fake_backend/              # Plan B scenario server + scenario JSON fixtures
├── tests/
│   ├── unit/                  # executor vs FakeAgentSDK
│   └── e2e/                   # pytest driver: hello, tool_use, permission, …
└── Makefile                   # run server / fake / tck / inspector targets
```
