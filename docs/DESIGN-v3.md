# Design Doc — A Fast, Deterministic Claude-Code A2A Fake for Building Agents & Frontends

*Status: draft (v3, plan of record) · 2026-08-05 · Companion references: DESIGN-v2 (producer
internals, multi-repo patterns, wire contracts), `docs/pass-{1..4}-*.md` (research). History
and prior-design rationale: §10.*

## 1. Goal

**Build A2A agents and a frontend against something predictable and fast, with zero
inference.** The agents and frontend are the deliverable; the Claude-Code-over-A2A producer
([a2acode](https://github.com/kanywst/a2acode)) is the thing being faked.

The dev loop this has to support: millisecond turns, hundreds of runs a minute, and
scriptable variety — long refactors, permission prompts, failures, ugly diffs — reproducible
on demand. That fixes the layer to fake at: the **A2A surface itself**, the layer the
consumer code actually touches, while staying honest about what real traffic looks like.

**Non-goals:** production hardening of the producer, TCK completeness, drift-detection
infrastructure (see §10 for where those live).

## 2. System picture

```
YOUR CODE (the deliverable)                 THE FAKE (dev/test double)
┌───────────────────────────┐    A2A 1.0    ┌─────────────────────────────────────────┐
│ frontend                  │  JSON-RPC+SSE │ a2acode serve --backend playback         │
│ orchestrating agents      │──────────────▶│   --repos repos/                         │
│ pytest suites for both    │               │ (real card · real streams · fake brain)  │
└───────────────────────────┘               └─────────────────────────────────────────┘
                                                repos/               ◀── scenario factory:
                                                  billing-api/           record real runs
                                                    repo.yaml             (--record, §6)
                                                    scenarios/
                                                  frontend-app/
                                                  flaky-legacy/
```

A **fake repo is a directory**: `repo.yaml` declares who the agent is, and
`scenarios/*.yaml` hold the scripts it can play. A repo *has* scenarios; it is
not one. The directory name is the repo id, so identity has one source. A
multi-repo frontend is therefore cheap to develop: a directory of repo
directories, served either from one process with each repo mounted at
`/repos/<name>/` behind a JSON index, or one process per repo (DESIGN-v2 §9).
No git checkouts, no workspaces, no claude installs anywhere.

Consumers read the **index**, not the filesystem and not root-scoped
well-known discovery: `GET /` returns `{"repos": [{"name", "description",
"card_url"}]}` with absolute card URLs. That is the seam that keeps the two
topologies interchangeable — a consumer built on the index cannot tell them
apart.

## 3. The core component: a `playback` backend for a2acode

a2acode's architecture makes the right fake almost unfairly cheap. Its backends emit a
normalized event vocabulary — `TextDelta`, `Thought`, `ToolUse`, `ToolResult`, `FileChange`,
`Plan`, `Notice`, permission requests (via `session.request_permission`), `Result` — and
never touch protocol code; the executor maps events onto A2A. Its `echo` backend proves the
slot exists but is a wiring check: one hardcoded tool call, a permission prompt only on the
keyword "sudo", the prompt parroted back. Not enough to build a UI against.

**`playback` is echo, scenario-driven**: ~150–250 lines that read a scenario file and emit
its scripted events. What that buys, structurally:

> The frontend develops against the **real a2acode server** — real agent card, real
> JSON-RPC + SSE, real task-state machine, real `input-required` machinery, real artifact
> chunking — with a scripted brain. **The stub cannot drift from the real producer, because
> it *is* the real producer minus the model.**

That is the contract-testing guarantee obtained structurally instead of through verification
tooling. Every protocol behavior the consumers must handle (stream ordering, status-update
shapes, permission pauses, resubscribe snapshots, push notifications, task persistence)
comes from a2acode's own code paths, not from an imitation of them.

Speed: no subprocess, no filesystem, no network beyond localhost HTTP — turns complete in
milliseconds unless a scenario asks for realistic pacing.

## 4. Repo and scenario format

Identity and pacing live in `repo.yaml`; a scenario file holds `plays:` and
nothing else. A repo's scenario files are read in filename order and their
plays concatenated, then matched first-match-wins — which is what makes M3
additive, since a recording is just a new file in `scenarios/`.

YAML, written in a2acode's own event vocabulary so recordings (§6) and hand-written
scenarios are the same format. A scenario is a list of **plays**; each incoming message
selects a play by match rules; a play is a list of events.

```yaml
# repos/billing-api/repo.yaml — identity and pacing; no plays
card:
  description: "Fake billing-api repo (playback)"
defaults:
  delay_ms: 0            # instant by default; PLAYBACK_SPEED env scales all delays
```

```yaml
# repos/billing-api/scenarios/30-refactor.yaml — a repo that "does" a refactor with a permission gate
plays:
  - match: { turn: 1 }                    # first message in any context
    events:
      - plan:
          steps:
            - { content: "Read the tax module", status: in_progress }
            - { content: "Extract rate table",  status: pending }
      - tool_use:    { name: Read, input: { file_path: "src/tax.py" }, id: t1 }
      - tool_result: { id: t1 }
      - text: "The rate logic is duplicated in three places. Extracting it now.\n"
      - permission:                        # parks the task in input-required
          tool: Bash
          input: { command: "pytest tests/tax -q" }
          on_allow:
            - tool_result: { id: t2, output: "42 passed" }
            - file_change: { path: "src/tax.py", diff: "@@ -10,7 +10,3 @@ ..." }
            - text: "Extracted RATES to rates.py; all tests pass."
            - result: { cost_usd: 0.0173, num_turns: 4, stop_reason: end_turn }
          on_deny:
            - notice: "Skipping the test run; edits left uncommitted."
            - result: { cost_usd: 0.0102, num_turns: 3, stop_reason: end_turn }

  - match: { contains: "explain" }         # keyword-routed alternate behavior
    events:
      - thought: "The caller wants a summary, not changes."
      - text: "This module computes VAT using a hardcoded table..."
      - result: { cost_usd: 0.004, num_turns: 1 }

  - match: {}                              # default/fallback play
    events:
      - text: "Done."
      - result: { cost_usd: 0.001, num_turns: 1 }
```

A recorded file is the same format plus provenance. `rig-record` writes a `recorded:` mapping
alongside `plays:`, carrying `at`, `backend` (the label, e.g. `acp:claude`), `a2acode` (the
producer version this describes), and `prompts`:

```yaml
# a scrubbed rig-record capture, promoted to scenarios/20-recorded-health.yaml
recorded:
  at: "2026-08-08T17:04:11Z"
  backend: "acp:claude"
  a2acode: "0.6.2"
  prompts:
    - "add a /health endpoint to ./src/app.py"
plays:
  - match: { regex: "^add a /health endpoint to \\./src/app\\.py$" }
    events: [ ... ]
```

`prompts` is machine-readable because the refresh loop consumes it — "re-record the library's
source prompts" needs the prompts back. It is a *source list for re-recording*, not an index
into `plays`, so pruning a play during scrub does not corrupt it. Recorded plays match on an
anchored, `re.escape`d regex, which is what stops two recordings in one repo from shadowing
each other.

Scenario files are named `NN-<slug>.yaml`, and because files load in filename order the prefix
is the promotion ladder: `20-*` recorded, `30-*` hand-written specifics, `90-*` broad
fallbacks, and the catch-all alone in `99-default.yaml`. Recordings sort *ahead* of
hand-written plays — an imagined play shadowing a real recording of the same prompt is
backwards.

Semantics worth pinning:

- **Match rules** (first match wins): `turn: N` (nth task in the context — exercises
  multi-turn UIs), `contains: str` / `regex:`, and `{}` default. A scenario with no match
  hit fails the turn loudly — a mis-scripted test should never get a plausible wrong answer.
- **Events** map 1:1 onto `BackendEvent` types; `permission` maps onto
  `session.request_permission` with branching — this is what lets a frontend build and test
  the whole approve/deny UX offline, including the abandoned-prompt path (add `timeout_ms`).
- **Timing:** `delay_ms` per event for realistic pacing (streaming text renders, spinners,
  race conditions); a `PLAYBACK_SPEED` multiplier makes the same scenario instant in CI and
  lifelike in manual demos.
- **Failure modeling:** `result` takes `stop_reason` and an `error:` variant so scenarios
  can script budget exhaustion, refusals, and mid-stream crashes — the states a frontend is
  usually worst at handling and can never reproduce on demand against a live backend.
- **Sessions:** playback honors the executor's contextId → session-id mapping (emit
  `session_id` in `result`), so continuity behaves exactly as with a real backend.

## 5. What this exercises

Everything the frontend/agents must handle arrives through real a2acode plumbing: initial
`Task` event then ordered status updates; `response` artifact chunks with
`append`/`last_chunk`; separate thinking and plan artifacts; file-diff artifacts;
`input-required` with resume-on-answer; cancel (`CancelTask` against a playback turn
mid-delay); `tasks/get` and resubscribe snapshots; push-notification webhooks; task
persistence (`--task-db`) across fake restarts; bearer auth and signed cards if the frontend
should handle them. None of that is simulated by this project — it's the real server.

## 6. The scenario factory: keeping canned interactions honest

Hand-written scenarios rot toward what we *imagine* Claude runs look like. The fix is a
**recording mode**: a `RecordingBackend` decorator that wraps any real backend (`acp` or
`claude`), tees every normalized event to a file, and emits it unchanged. One
flag: `a2acode serve --backend claude --record captures/refactor.yaml`. Then: run a real
prompt once, scrub, check the file in as a scenario.

- **A recording captures only the branch that was taken.** A real run answers a permission
  gate once, so a recorded `permission` carries `on_allow` or `on_deny`, never both, and the
  other branch is a loud failure on replay rather than an invented one. This is why recorded
  and hand-written scenarios compose rather than one replacing the other: the deny path, the
  abandoned-approval timeout, and scripted failures are exactly what a live run cannot be made
  to produce on demand.
- **No per-event timing.** The refresh loop works by diffing normalized streams across
  re-recordings, and wall-clock timing differs every run — recording it would make every
  re-record diff every line. Pacing stays where it already lives: `repo.yaml`'s
  `defaults.delay_ms` and `PLAYBACK_SPEED`.
- **Determinism of the recording source is optional.** For scenario capture, a live run is
  fine — recording captures *shape*, it doesn't assert. Note that the cost ceiling only exists
  on one path: `--max-budget-usd` is honored by the `claude` backend and `ACPBackend` takes no
  ceiling at all. Since `--backend claude` can no longer emit a `plan` at all (`docs/UPSTREAM.md`),
  the only backend that records the full vocabulary is the one that cannot be capped. For
  reproducible re-recording, a fake Anthropic API under the real `claude` binary works
  (contract and plan in DESIGN-v2 §5).
- **Refresh loop = provider verification.** When upstream moves (new SDK event types,
  changed diff shapes), re-record the library's source prompts and diff normalized streams;
  changed recordings tell you exactly what the frontend must newly handle. This is the
  consumer-driven-contract story landing where it belongs: the scenario library *is* the
  contract between the consumers and the producer.
- Recording at the **BackendEvent level** (not wire level) is deliberate: it's the stable,
  vendor-neutral vocabulary — recordings made through the `acp` backend and the `claude`
  backend are interchangeable as scenarios.

## 7. Upstream strategy

`playback` + `--record` are ideal a2acode PRs: they extend the pattern `echo` establishes,
touch no protocol code, and the README's own claim ("adding a backend never touches the
server or the protocol mapping") is the review argument. The maintainer already values
offline test doubles (`tests/fake_agent.py`). Sequence: build out-of-tree first (a thin
`serve` wrapper passing our Backend into `build_app()` — no fork needed since backends are
constructor-injected), prove it against our own frontend, then offer both upstream. If
upstream stalls, the out-of-tree package stands alone at ~300 lines with a2acode as a pinned
dependency.

## 8. Milestones

| # | Deliverable | Exit criteria |
|---|---|---|
| M0 | Out-of-tree `playback` backend + `billing-api.yaml`; serve wrapper | Frontend/curl gets card, streamed task, artifacts from a fake repo — day one |
| M1 | Full event vocabulary: permission branches (allow/deny/timeout), plans, diffs, errors, delays, `PLAYBACK_SPEED` | A UI can build chat, plan, diff, and approval views entirely offline |
| M2 | Multi-repo dev rig: scenario directory → N fake repos (per-port or mounted); pytest fixtures for the agents | Agent tests run against 3+ fake repos in <5s total |
| M3 | `--record` decorator + first recorded scenarios from one budget-capped live run; scrub tooling | Library contains ≥3 recorded (not hand-written) scenarios |
| M4 | Upstream PRs (`playback`, `--record`); refresh-loop runbook | PRs opened; re-record + diff procedure documented |

## 9. Risks

- **Hand-written scenario realism** — the known failure mode of canned mocks; bounded by M3
  making recordings the library's backbone and hand-authoring the exception.
- **Match-rule brittleness** — keyword routing is crude; acceptable because a mis-match
  fails loudly rather than answering plausibly. Revisit only if scenarios multiply.
- **a2acode event-vocabulary evolution** — new `BackendEvent` types (pre-1.0 project) change
  the scenario schema; pinning + the M3 refresh loop is the tripwire, and upstreaming
  playback makes the vocabulary *their* compatibility surface too.
- **Fidelity ceiling** — playback inherits a2acode's protocol behavior but scripts its
  content; if a frontend bug depends on *content* realism (giant diffs, pathological
  streams), record those cases rather than simulating them.

## 10. Background: how this design got here

Three designs, each reshaped by a discovery:

- **v1 (DESIGN.md)** planned to build the Claude-Code-over-A2A server itself: an
  `AgentExecutor` transcoding the Claude Agent SDK's message stream into A2A tasks, status
  updates, and artifacts. Its concept-mapping work remains valid reference.
- **v2 (DESIGN-v2.md)** followed the discovery that a2acode already *is* that server —
  A2A 1.0, the full mapping, permissions, push, persistence, a swappable ACP backend — and
  pivoted to what it lacked: a deterministic full-stack test rig ("clockwork", a fake
  Anthropic Messages API under the real `claude` binary), TCK conformance gating, and
  drift-detection tripwires. v2 remains the reference for a2acode internals and process
  lifecycle (§2, §10), multi-repo deployment patterns (§9), the fake-API wire contract (§5),
  and the contract-testing/drift ideas.
- **v3 (this doc)** followed the clarification of the actual goal: the deliverable is the
  *consumer* side — agents and a frontend — needing a fast, predictable producer to develop
  against. That moved the fake up to the A2A layer (playback), where it is both faster and
  structurally drift-proof, and demoted v2's centerpieces to supporting roles: clockwork is
  now an optional scenario-factory tool (§6), the TCK a one-time sanity check rather than a
  CI gate, and drift proxies/canaries parked until something load-bearing depends on live
  operation. Producer-side lifecycle and multi-repo production concerns return when real
  repos replace scenario files.
