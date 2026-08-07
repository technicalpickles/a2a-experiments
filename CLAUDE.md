# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

This is the planning, research, and (for now) code record for building a deterministic A2A
(Agent2Agent protocol) dev rig on top of [a2acode](https://github.com/kanywst/a2acode), a
Claude-Code-over-A2A server. `docs/` holds the design/plan/log; `a2a-rig/` holds the actual rig
code (a `playback` backend, scenario files, pytest harness) — see `docs/PLAN.md` for what's
built so far.

`a2a-rig/` is **incubating here, not permanently living here.** It started as its own repo
(`~/github.com/technicalpickles/a2a-rig`) and was folded in via `git subtree` on 2026-08-07 so
early work stays visible alongside the docs that motivate it. The plan is to extract it back
out to its own repo once it's further along — `git subtree split --prefix=a2a-rig` replays its
commits cleanly when that happens, since the merge preserved full history rather than
squashing it. Treat `a2a-rig/` as a self-contained project (own `pyproject.toml`, own
`README.md`, own dependency on `a2acode`) that happens to be checked out under this repo, not
as something to intermix with the docs-only conventions below.

## How work here is organized

Each document has one job — don't blend them:

- **`docs/DESIGN-v3.md`** is the plan of record (current architecture and rationale). It
  supersedes `docs/DESIGN.md` (v1) and `docs/DESIGN-v2.md` (v2), which are kept as reference —
  v1 for its still-valid A2A concept-mapping work, v2 for a2acode internals/lifecycle,
  multi-repo deployment patterns, and the fake-Anthropic-API wire contract. §10 of v3 explains
  why each earlier design was superseded. Update DESIGN-v3 only when the actual architecture
  changes, not for status/progress.
- **`docs/PLAN.md`** is the execution checklist: phases with checkboxes and an exit criterion
  each. Check boxes off as work completes; add a short inline note when a step's outcome
  differs from what was planned (a workaround, a deferral, a version pin) rather than silently
  editing the original bullet.
- **`docs/DEVLOG.md`** is the narrative log: what actually happened, in the order it happened,
  under dated headings. This is where findings, dead ends, and the reasoning behind
  non-obvious decisions belong — PLAN.md stays terse, DEVLOG.md carries the "why." Append a
  new dated section per work session rather than editing old ones.
- **`docs/UPSTREAM.md`** tracks findings that belong in someone else's repo (a2a-sdk,
  a2acode, a2a-cli, a2a-inspector) — the trace, the repro, and the framing each report
  needs. Taskwarrior holds status and is the actionable backlog; this doc holds the "why"
  so an issue can be written later without re-deriving it. Add an entry when a DEVLOG
  finding turns out to be upstream's problem rather than ours.
- **`docs/captures/`** holds recorded wire traffic, not prose — one JSON event per line,
  protobuf-serialized via `MessageToDict`, captured with `dump_stream.py` alongside them.
  These are the shape reference scenario YAML gets written against in Phases 4–5, until
  `--record` replaces them in Phase 7. `phase2-claude-run.jsonl` is the Phase 2 real-Claude
  run (note: 4 of its lines don't parse, mangled after the fact — the dumper is fine).
  `phase5-acp-plan-run.jsonl` is the ACP-backed run that pins the `plan` artifact contract.
  `phase5-plan-probe.jsonl` plus `phase5-session-tools.json` are the evidence that
  `--backend claude` can't emit plans at all (see `docs/UPSTREAM.md`).
- **`docs/pass-{1..4}-*.md`** are dated research snapshots (A2A protocol/SDKs, ecosystem
  tooling, Claude Agent SDK, deterministic-backend approaches) that fed the designs. Treat them
  as historical inputs, not living docs — if something in a pass doc turns out stale, the fix
  belongs in DESIGN-v3 or a DEVLOG entry, not an edit to the pass doc itself.

When picking up work in this repo: read `docs/PLAN.md` first for current status, `docs/DEVLOG.md`
for recent context/decisions, and `docs/DESIGN-v3.md` for the target architecture.

## Where the rest of the code lives (not in this repo)

`a2a-rig/` (this repo) is built *around* a2acode rather than forking it. Sibling checkouts,
referenced throughout the docs:

- `~/github.com/kanywst/a2acode` — the producer being faked. Pinned at v0.6.2. Standard
  Python/`uv` project: `uv sync --dev`, `uv run pytest -q` (163 tests), `uv run a2acode serve
  --backend echo|claude|acp`, `uv run a2acode call`/`card`. See "Running a2acode" below.
- `~/github.com/a2aproject/a2a-inspector` — reference debugging UI. Currently **broken**
  against a2acode (see DEVLOG 2026-08-06): its `a2a-sdk` pin (0.3.10) predates the
  `supportedInterfaces` card shape a2acode's `a2a-sdk` 1.1.2 emits, and its client code is
  built on 1.1.2's protobuf-generated types internally, so fixing it is a real rewrite, not a
  patch. Deferred, not blocking.
- `~/github.com/technicalpickles/a2a-cli` — reference CLI client, forked from
  `ericabouaf/a2a-cli` (upstream has no `repository` field in its npm metadata, hence the
  fork-first approach) on branch `a2a-sdk-1.0-migration`, patched for the same SDK-shape issue.
  `npm link`ed globally rather than installed from npm, so `a2a-cli` on `PATH` resolves here.
  Upstreaming the fix is tracked outside this repo (taskwarrior, project `a2a-experiments`).

`a2a-rig/` is the out-of-tree `playback` backend package and pytest harness (`a2a-sdk` +
pytest-asyncio) that imports a2acode rather than forking it — per DESIGN-v3 §7, no fork of
a2acode itself is needed since backends are constructor-injected. See `a2a-rig/README.md` for
how to run it.

## Running a2acode

`uv run` resolves against the project you're standing in, so `uv run a2acode …` from this repo
fails with `Failed to spawn: a2acode`. Either `cd ~/github.com/kanywst/a2acode` first, or pass
the project explicitly from anywhere:

```bash
uv run --project ~/github.com/kanywst/a2acode a2acode serve \
  --backend claude --cwd ~/scratch/demo-app
```

Two directories are in play and they are easy to conflate: where you *launch* (must resolve
a2acode's venv) versus a2acode's `--cwd` flag, which is the project the coding agent edits.

**Auth:** the `claude` backend spawns the `claude` CLI via the Claude Agent SDK and inherits
whatever that CLI is logged in with — `ANTHROPIC_API_KEY` is **not** required, and a2acode
does no key validation of its own. a2acode's README notes Anthropic doesn't permit
subscription credentials when serving *third parties*; a local single-user rig isn't that.
Subscription auth still reports real cost: `Result.cost_usd` (from the SDK's
`total_cost_usd`) came back as `0.30` and `0.24` on the two Phase 2 turns, alongside full
`usage` token counts and `claude_session_id`.

Omit `--permission-mode` to keep tool approvals routing back to the caller as `input-required`
(the instructive path); `acceptEdits` skips those pauses.

The Phase 2 scratch repo is `~/scratch/demo-app` — a small Flask app (`/items`,
`/items/<id>`), committed clean at `6890fd7`.

## Target architecture (DESIGN-v3)

The centerpiece is a `playback` backend for a2acode: it reads a **scenario** (YAML, in
a2acode's own `BackendEvent` vocabulary — `text`, `tool_use`/`tool_result`, `plan`,
`file_change`, `permission`, `result`, etc.) and emits scripted events through a2acode's real
server, protocol mapping, and task-state machine. The frontend/agents being built therefore
develop against the **real A2A surface** (real card, real JSON-RPC+SSE, real `input-required`
machinery, real artifact chunking) with a scripted brain instead of live inference — so the
stub can't drift from the real producer, because it *is* the real producer minus the model.

Scenario matching is first-match-wins over `turn: N`, `contains`/`regex`, or a `{}` default; an
unmatched turn fails loudly rather than answering plausibly. A later `RecordingBackend`
decorator tees real backend runs (via `--record`) into scenario files at the same event level,
so the scenario library becomes recorded rather than hand-imagined over time (DESIGN-v3 §6).

Milestones (DESIGN-v3 §8): M0 single fake repo + serve wrapper → M1 full event vocabulary
(permissions, plans, diffs, errors, timing) → M2 multi-repo rig → M3 recording/scenario
factory → M4 upstream `playback`/`--record` PRs to a2acode.
