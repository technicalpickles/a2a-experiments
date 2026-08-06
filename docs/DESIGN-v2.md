# Design Doc v2 — Claude Code over A2A, built on `a2acode`

*Status: draft · 2026-08-05 · Supersedes DESIGN.md (v1). v1's concept-mapping sections remain
a useful reference — a2acode independently converged on essentially the same mapping and
implements it. Research passes: `docs/pass-{1..4}-*.md`. Code assessment of a2acode: cloned at
v0.6.2 (2026-08-03), test suite verified — 163/163 pass on Python 3.13.*

## 1. What changed since v1

v1 proposed building an A2A server around the Claude Agent SDK from scratch.
[kanywst/a2acode](https://github.com/kanywst/a2acode) (the successor to the a2claude project
our research had found) already **is** that server, and then some: A2A 1.0 on `a2a-sdk ≥1.1`,
the full SDK↔A2A mapping (streamed artifacts, tool-call status updates, plan artifacts, file
diffs, cost metadata, session↔contextId resume, `canUseTool` ↔ `input-required` permission
round-trips, cancel), plus things v1 deferred: push notifications, task persistence, signed
agent cards, bearer auth, OTel tracing, attachments — and an ACP abstraction that makes the
coding agent swappable (Claude Code, Codex, Gemini CLI, any ACP agent). Apache-2.0, active,
CI-tested, ~3.2k source + ~2.8k test lines.

**v2 decision: adopt a2acode as the server. Do not rebuild it.** Our work shifts to the two
legs it does not have — which happen to be the two legs Josh's original idea emphasized:

1. **A deterministic full-stack test rig** — the real `claude` binary + real Agent SDK +
   a2acode, driven over A2A, backed by a fake Anthropic Messages API. Zero inference.
2. **A repeatable way to drive A2A interactions** — a scripted pytest driver using the
   official A2A client, TCK conformance gating, and inspector-based debugging.

Plus a third, opportunistic leg: **upstream contributions** where our rig finds gaps.

**Non-goals:** forking a2acode (we depend on a pinned version; patches go upstream),
production deployment, sandboxing, multi-tenancy.

## 2. a2acode in one page (what we're leveraging)

Architecture (their layering, which we preserve):

```
A2A caller ──▶ a2a-sdk routes ──▶ ClaudeCodeExecutor (executor.py: protocol mapping only)
                                        │  normalized BackendEvents:
                                        │  TextDelta · Thought · ToolUse · ToolResult
                                        │  FileChange · Plan · Notice · PermissionRequest · Result
                                        ▼
                              Backend (never imports a2a.*):
                                ├─ echo   — offline mirror, no deps
                                ├─ claude — ClaudeSDKClient direct (extra: claude)
                                └─ acp    — any ACP agent subprocess (default; presets:
                                            claude → @zed-industries/claude-agent-acp,
                                            codex, gemini, or --agent-command …)
```

Facts that matter for our rig:

- **CLI:** `a2acode serve --backend {acp|claude|echo} [--agent …] [--cwd DIR] [--permission-mode …]
  [--max-budget-usd …] [--auth-token-file …] [--task-db DSN] [--sign-key …]`, default
  `127.0.0.1:9100`; `a2acode call TEXT [--context ID] [--task ID]`; `a2acode card`.
- **Env inheritance is our injection point.** The `claude` backend builds
  `ClaudeAgentOptions` *without* an `env` override, so the SDK inherits the server process
  environment; the `acp` backend likewise passes the environment to the adapter subprocess.
  Therefore `ANTHROPIC_BASE_URL` set on `a2acode serve` reaches the `claude` binary on **both**
  Claude paths, with no a2acode changes.
- **Permission flow:** backend's `can_use_tool` → `session.request_permission()` parks on a
  future → executor emits `input-required` → follow-up A2A message on the same task resolves
  it (`allow`/`yes`/`approve`/`ok` = allow, else deny). Session stays alive across the pause.
- **Sessions:** `contextId` → backend session; Claude `session_id` chained via
  `options.resume`. `setting_sources` defaults to `[]` (no host settings leak — good for
  reproducibility).
- **Their test layers:** unit tests against fabricated SDK messages
  (`events_from_message` is pure), a **fake ACP agent** (`tests/fake_agent.py`) exercising the
  subprocess/handshake/permission plumbing, and live tests requiring credentials.

## 3. The gap we fill: the missing rungs of the substitution ladder

Every deterministic-testing strategy substitutes reality at some layer. a2acode covers the top
of the ladder; nobody covers the bottom:

| Rung | What's fake | What's real | Exists? |
|---|---|---|---|
| L0 | everything below the executor (`echo` backend) | A2A protocol surface | ✅ a2acode |
| L1 | the coding agent (fake ACP agent) | ACP plumbing, subprocess lifecycle | ✅ a2acode tests |
| L2 | **the Anthropic API only** | `claude` binary, Agent SDK, a2acode, A2A | ❌ **ours to build** |
| L3 | nothing (live inference) | everything | manual, costs money |

L2 is the valuable rung: it is the only inference-free configuration that exercises the real
agent loop — the SDK's message taxonomy, tool execution against a real workspace, permission
callbacks, session resume across turns — i.e. the exact seams where an A2A wrapper can be
subtly wrong. It's also the rung that catches breakage when the `claude` binary or SDK
updates, before any token is spent. Pass-4 established the seam is official and clean:

```bash
ANTHROPIC_BASE_URL=http://localhost:4010
ANTHROPIC_AUTH_TOKEN=test-token             # bearer path — no interactive approval
CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1
CLAUDE_CODE_ATTRIBUTION_HEADER=0            # stable bodies → deterministic matching
```

## 4. System under test and rig layout

```
pytest driver (a2a-sdk Client) ──A2A/JSON-RPC+SSE──▶ a2acode serve --backend claude --cwd <tmp workspace>
                                                            │ env: ANTHROPIC_BASE_URL=… (above)
                                                            ▼
                                                      claude binary ──SSE──▶ clockwork (fake Anthropic API :4010)
                                                                              scenario selected per test
```

Repo layout (ours; a2acode is a dependency, not vendored):

```
claude-a2a-rig/
├── DESIGN-v2.md
├── docs/pass-{1..4}-*.md
├── clockwork/                  # fake Anthropic Messages API (see §5)
│   ├── server.py               # FastAPI SSE, implements pass-4 §2 contract
│   ├── scenarios/*.json        # ordered turn scripts, one per E2E test
│   └── recorder/               # Plan C: mitmproxy capture → scenario converter (later)
├── tests/
│   ├── conftest.py             # fixtures: workspace tmpdir, clockwork, a2acode server, a2a client
│   ├── test_contract.py        # clockwork vs claude binary: request-shape assertions
│   └── e2e/test_{hello,tool_use,permission,multi_turn,cancel,errors,attachments}.py
├── tck/                        # a2a-tck runner config + baseline expectations
└── Makefile                    # serve / clockwork / tck / inspector / e2e targets
```

Primary SUT configuration is **`--backend claude`** (fewest moving parts between our fake and
the SDK). A secondary matrix axis runs the same scenarios through **`--backend acp --agent
claude`** — same fake API underneath, now also exercising the ACP adapter — which doubles as
a regression check on a2acode's default path. `echo` stays useful for driver-only tests.

## 5. `clockwork` — the deterministic Anthropic API

v1's Plan A was aimock, Plan B bespoke. **v2 flips to bespoke-first**, for three reasons
discovered since: our E2E assertions need *request-side* hooks (verify turn 2 carried turn 1's
transcript to prove resume; verify `x-claude-code-agent-id` for subagent attribution) that a
generic mock doesn't give us; the wire contract is small and precisely documented
(pass-4 §2), so the build is ~300–400 lines; and owning it lets scenario files double as
readable test specs. aimock remains the fallback if clockwork fidelity turns into a tar pit.

Contract (from pass-4): streaming SSE on `POST /v1/messages?beta=true`
(`message_start → content_block_start/delta/stop → message_delta → message_stop`, `ping`s,
`input_json_delta` for tool args); tolerate `HEAD /`, unknown `anthropic-beta` headers,
`thinking: {"type":"adaptive"}`, the attribution system block; optional `count_tokens`;
errors only in the exact envelope `{"type":"error","error":{…}}`.

Scenario format — an ordered turn script, selected per test via a header
(`X-Clockwork-Scenario: tool_use`, set through `ANTHROPIC_CUSTOM_HEADERS` on the a2acode
process — one scenario per server instance, which the per-test fixture gives us anyway):

```jsonc
// scenarios/tool_use.json — "add a health endpoint" with one Write + one approval-free Read
{ "turns": [
  { "expect": { "turn": 1, "last_message_contains": "health endpoint" },
    "respond": { "blocks": [
      { "text": "Adding the endpoint now." },
      { "tool_use": { "name": "Write",
                      "input": { "file_path": "app.py", "content": "..." } } } ],
      "stop_reason": "tool_use" } },
  { "expect": { "turn": 2, "has_tool_result": true },
    "respond": { "blocks": [ { "text": "Done — added /health." } ],
      "stop_reason": "end_turn" } }
] }
```

`expect` clauses are assertions, not just matchers: a mismatch fails the turn loudly (HTTP 500
with a diagnostic the pytest driver surfaces) instead of returning the wrong canned answer.
Matching keys only on stable signals — turn index, last-message content, tool_result presence,
session/agent headers — never full-body equality (pass-4 §4's brittleness lesson).

Each request/response pair is also journaled to disk, so every E2E failure comes with the
exact wire history. That journal is the seed for **Plan C (later): record real `@live`
sessions via mitmproxy/llm-interceptor and convert them into scenario files.**

## 6. The A2A test driver

Same layered scheme as v1 §6, now aimed at a2acode:

1. **Contract tests (`test_contract.py`):** clockwork + bare `claude` binary (no a2acode) —
   pin down that our fake satisfies the client before involving A2A at all.
2. **Conformance:** `a2a-tck --sut-host http://localhost:9100 --level must` against
   `a2acode serve --backend echo`, in CI. a2acode does not run the TCK itself; a baseline
   file records the current pass set so regressions in *their* releases surface in *our* CI.
   (Any MUST failures we find are upstream issue material — see §8.)
3. **E2E scenarios** (pytest, `a2a-sdk` `ClientFactory` driver; each test = one clockwork
   scenario + one a2acode instance on a tmp workspace):
   - `hello` — text round trip; `response` artifact chunks arrive with `append`/`last_chunk`.
   - `tool_use` — Write flows through: WORKING status updates for ToolUse/ToolResult, file
     diff artifact, file actually on disk in the workspace.
   - `permission` — Bash triggers `input-required` (state, `status.message` payload);
     approve path resumes and completes; deny path completes with the denial visible;
     timeout path documented behavior.
   - `multi_turn` — second task on the same `contextId`; **clockwork asserts turn 1's
     transcript is present in the follow-up request** — resume proven without inference.
   - `cancel` — mid-stream `CancelTask` → CANCELED; clockwork sees the connection drop.
   - `errors` — budget/turn exhaustion and API-error scenarios → task FAILED with metadata.
   - `attachments` — text + image parts reach the prompt (clockwork asserts on request
     content); truncation marked.
4. **Interactive:** Makefile targets for a2a-inspector and `a2acode call` against clockwork —
   a full agentic demo loop that costs nothing and needs no credentials.
5. **Cross-framework (stretch):** ADK `RemoteA2aAgent` / CrewAI `A2AClientConfig` pointed at
   the rig — third-party client interop on scripted scenarios.
6. **`@live` smoke (last, manual):** the same E2E tests, real API, `--max-budget-usd` capped.

## 7. What we deliberately reuse instead of building

| v1 planned to build | v2 disposition |
|---|---|
| AgentExecutor / SDK↔A2A transcoding | a2acode `executor.py` + backends |
| Session registry, workspace mgmt | a2acode (`--cwd`, per-context backend sessions) |
| `input-required` permission bridge | a2acode (`session.request_permission`) |
| File-change artifacts | a2acode (`FileChange` events + diff artifacts) |
| Agent card + skills | a2acode `card.py` (generation/refactor/debug/review/test/explain) |
| Push notifications, persistence, auth, signing | a2acode extras |
| Deterministic backend | **ours: clockwork** (their `echo`/fake-ACP cover different rungs) |
| A2A driver + TCK gating | **ours** |

## 8. Upstream contribution track

Run the rig, file issues/PRs where it hits friction. Already-visible candidates, smallest
first:

1. **Token-level streaming in the `claude` backend** — it iterates `receive_response()`
   without `include_partial_messages`, so `TextDelta` granularity is a whole `TextBlock` per
   assistant message, while the ACP path streams finer chunks. Wiring
   `include_partial_messages=True` + `stream_event` handling into `events_from_message` would
   equalize them.
2. **`stop_reason` on the Claude path** — `Result` has the field; the claude backend doesn't
   populate it from the SDK's result subtypes (`error_max_turns`, budget, etc.).
3. **Explicit env control for the `claude` backend** — today the fake-API seam works only via
   process-env inheritance; a `--backend-env KEY=VAL` (or config) flag would make hermetic
   setups first-class and document the pattern.
4. **TCK in their CI** — contribute the conformance job once our baseline is clean.
5. Whatever the E2E matrix shakes out — event-ordering, artifact chunking, or 1.0-spec edge
   cases the TCK flags.
6. **Lifecycle hygiene** (motivated by §10): an idle-TTL reaper for the ACP agent pool, a
   timeout on parked permission waits, and configurable `_MAX_AGENTS`/`_MAX_LIVE`.
7. **Per-context project binding** (motivated by §9): plumb cwd from `RunRequest` into
   backend/session construction so a workspace can be chosen at context creation.

## 9. Workflows: multi-repo deployments

The scenario: a directory of repos, and a caller starts a session in a specific one.

**Today, a2acode binds one working directory per server process.** `--cwd` is fixed at serve
time; `build_app()` wires one backend → one executor → one card; nothing in the request path
selects a project. Two patterns work without code changes:

1. **Agent-per-repo, process-per-repo.** A supervisor scans the repos directory and runs
   `a2acode serve --cwd ~/repos/<name> --port 91xx` per repo. This is the most A2A-native
   shape: each repo *is* an agent with its own card, and "start a session in project X"
   becomes "pick agent X" — the discovery/delegation decision A2A clients (ADK, CrewAI) are
   built to make. The "directory of repos" is literally an agent registry (a static list of
   card URLs suffices). Cost: N processes/ports; generic (non-repo-specific) skills.
2. **One process, N mounted apps** — the recommended starting point. `build_app()` is a plain
   function returning a Starlette app, so a ~30-line wrapper builds one app per repo and
   mounts them at `/repos/<name>/` in a parent app: one process, one port, N cards. The
   `/.well-known/agent-card.json` convention is root-scoped, but the spec sanctions
   registries/direct configuration as alternate discovery, so a root index endpoint listing
   the per-repo card URLs is legitimate. Note each mounted app still owns its own backend and
   process pool — this collapses N uvicorns into one, not the agent processes (§10).

Sessions compose with either: a context is implicitly bound to its repo because it is bound to
its server (or mounted app); `--context <id>` continues it there.

**Design space for native project selection** (in descending A2A-nativeness):

- **Project bound at context creation** — the flagship future shape and the natural upstream
  PR (§8 item 7). The first message of a new context carries `{"project": "<name>"}` in a
  `data` part or URI-keyed metadata; the server resolves it against an **allowlisted scan**
  of the repos directory (callers pick from a menu, never supply paths) and binds the
  workspace to that `contextId` immutably. a2acode is close: `BackendSession` is already
  per-context; the change is moving cwd from backend construction to session construction.
- **The `tenant` field.** A2A 1.0's native multi-tenancy: `AgentInterface.tenant` is an
  opaque routing string the client must echo per request, and a2a-sdk already plumbs it into
  `RequestContext`. One card, multiple interfaces at the same URL with `tenant = repo` is a
  spec-legal one-server-many-projects reading, and `ListTasks` scopes by tenant for free.
  Caveats: stretches intent (tenants ≈ customers, not workspaces) and tenant echoing is the
  newest, least-exercised client behavior in the ecosystem.
- **A declared extension** — `urn:a2acode:project-selection` in `capabilities.extensions`,
  with `params` listing available projects so the card itself advertises the repo directory;
  the choice travels in extension-keyed metadata. Most ceremony, best self-description.
- **Skills-per-repo is the tempting wrong answer:** A2A skills are descriptive, not
  invocable — there is no standard way for a caller to select one.

Orthogonal but same plumbing: **isolation within a repo.** Two contexts working one repo
concurrently will trample each other; the fix is a git worktree per context
(claude-code-agent's per-session-workspace pattern). Any design that binds cwd per-context
gets worktrees nearly free.

## 10. Process lifecycle (what actually runs, when)

Verified against v0.6.2 source (`executor.py`, `backends/acp.py`, `backends/claude.py`).
The two backends have opposite lifecycles, and **neither has an inactivity timer**.

**`claude` backend: process-per-turn, nothing resident.** Each turn opens
`ClaudeSDKClient` inside `drive()` — a fresh `claude` process spawns for the turn and exits
with it. Cross-turn continuity is not a live process; it's `options.resume=<session_id>`
reloading the transcript from disk on the next spawn. Exception: a turn parked on a
permission stays parked inside `can_use_tool`, so that claude process lives across the whole
`input-required` pause — indefinitely (no timeout) — until answered, cancelled, or evicted.
An idle server holds zero claude processes.

**`acp` backend (default): persistent pool, LRU, no idle reaping.** Adapter subprocesses
(e.g. `@zed-industries/claude-agent-acp`) are pooled per `contextId`, capped at
`_MAX_AGENTS = 32`. First turn on a context spawns + ACP-handshakes; follow-up turns reuse
the live process (no relaunch/handshake/session reload — the latency win). A pooled agent
closes only on: LRU eviction of an *unclaimed* agent at capacity (a fully claimed pool
overshoots with a warning), an exception during its turn (`broken` → retired), a dead
process found on reacquire (respawned), or server shutdown. **No TTL** — an idle adapter
for a repo touched once sits resident until pressure or restart. Messages without a
`contextId` get a one-shot process.

**Executor-level caps (both backends):** `_MAX_LIVE = 256` in-flight/parked turns per
server — eviction prefers abandoned permission prompts, then the oldest running task, which
is *failed* rather than completed with partial output; `_MAX_CONTEXTS = 4096` LRU map of
contextId → claude session id, past which old contexts silently lose resume.

**Multi-repo consequences.** With `acp`, N repo servers can accumulate up to N×32 resident
Node processes with no reaper — fine for a few hot repos, bad hygiene for fifty mostly-idle
ones. With `claude`, idle repos cost only the server process, paying per-turn spawn +
transcript reload instead. Pragmatic split: **`--backend claude` for the long tail of
mostly-idle repos; `acp` for actively-worked ones.** The mounted-apps pattern (§9) doesn't
change this math — each mounted app owns its own pool.

Rig note: the E2E suite should assert lifecycle behavior too — process counts before/after a
turn on each backend, and pool reuse across turns of one context (the fake ACP agent's
`SESSIONS` trick shows how) — so upstream lifecycle changes (§8 item 6) land with tests.

## 11. Milestones

| # | Deliverable | Exit criteria |
|---|---|---|
| M0 | Rig skeleton: pinned a2acode dep; Makefile; TCK MUST baseline vs `echo`; pytest driver runs `hello` against `echo` | TCK baseline recorded; driver green on echo |
| M1 | clockwork v0: contract tests pass against the bare `claude` binary (hello + tool_use scenarios) | `test_contract.py` green, zero inference; wire journal on failure |
| M2 | E2E through a2acode `--backend claude`: hello, tool_use, multi_turn | resume proven via clockwork request assertion |
| M3 | permission (allow/deny/timeout), cancel, errors, attachments; `acp:claude` matrix axis | full E2E matrix green on both backends |
| M4 | Inspector demo loop; cross-framework client check; first `@live` smoke; upstream issues/PRs filed from findings | demo runbook; ≥2 upstream contributions opened |

## 12. Risks and open questions

- **Fidelity chase:** the `claude` binary's request shape drifts across releases; clockwork
  must tolerate unknowns by default and assert narrowly. Pin the binary/SDK version in the
  rig; treat version bumps as explicit events with the contract tests as the tripwire.
- **Upstream velocity:** a2acode is one maintainer, pre-1.0 (v0.6.x); APIs may move. Pinning
  + a thin dependency surface (CLI + wire protocol only, no imports of its internals) keeps
  us insulated; the wire protocol is the stable thing.
- **Scenario/prompt coupling:** scripted `tool_use` turns assume the model *would* call the
  tool — fine for testing plumbing, meaningless for testing prompts. Keep clear that the rig
  validates the pipeline, not agent quality; `@live` and evals cover the latter.
- **One scenario per server instance** (header via `ANTHROPIC_CUSTOM_HEADERS`): acceptable at
  per-test fixture granularity; revisit if server startup cost (~seconds) dominates the suite.
- **Open:** do we want the rig to eventually publish clockwork as a standalone package
  ("deterministic Anthropic API for agent testing")? Pass-4 found this niche genuinely
  unfilled — it may be the most broadly useful artifact of the whole exercise.
