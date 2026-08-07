# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

This repo is currently **docs-only** — no application code lives here yet. It's the planning
and research record for building a deterministic A2A (Agent2Agent protocol) dev rig on top of
[a2acode](https://github.com/kanywst/a2acode), a Claude-Code-over-A2A server. The actual rig
code (a `playback` backend, scenario files, pytest harness) does not exist yet; see
`docs/PLAN.md` for what's built so far.

## How work here is organized

Three documents, three different jobs — don't blend them:

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
- **`docs/pass-{1..4}-*.md`** are dated research snapshots (A2A protocol/SDKs, ecosystem
  tooling, Claude Agent SDK, deterministic-backend approaches) that fed the designs. Treat them
  as historical inputs, not living docs — if something in a pass doc turns out stale, the fix
  belongs in DESIGN-v3 or a DEVLOG entry, not an edit to the pass doc itself.

When picking up work in this repo: read `docs/PLAN.md` first for current status, `docs/DEVLOG.md`
for recent context/decisions, and `docs/DESIGN-v3.md` for the target architecture.

## Where the actual code lives (not in this repo)

The rig is built *around* a2acode rather than inside this repo. Sibling checkouts, referenced
throughout the docs:

- `~/github.com/kanywst/a2acode` — the producer being faked. Pinned at v0.6.2. Standard
  Python/`uv` project: `uv sync --dev`, `uv run pytest -q` (163 tests), `uv run a2acode serve
  --backend echo|claude|acp`, `uv run a2acode call`/`card`.
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

Future phases add an out-of-tree `playback` backend package and a pytest harness
(`a2a-sdk` + pytest-asyncio) that imports a2acode rather than forking it — per DESIGN-v3 §7,
no fork of a2acode itself is needed since backends are constructor-injected.

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
