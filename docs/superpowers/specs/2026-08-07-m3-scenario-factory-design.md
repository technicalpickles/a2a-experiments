# M3: the scenario factory

**Date:** 2026-08-07 · **Phase:** 7 (PLAN.md) · **Milestone:** M3 (DESIGN-v3 §8)

Adds a `RecordingBackend` decorator and a `rig-record` command that capture a real agent run as
a scenario file, so the library's backbone stops being what we *imagine* Claude runs look like
and becomes what one actually did.

This spec settles the questions M3 opens; its architectural decisions (the recording seam, the
recorded-file format, branch fidelity, and how recorded and hand-written scenarios compose)
belong in DESIGN-v3 once implemented, per this repo's convention that DESIGN-v3 is the plan of
record.

**Two documents need correcting as part of this work**, both flagged in place below:

- **DESIGN-v3 §6** says the recorder tees "every normalized event (plus timing)". Recording
  per-event timing destroys the refresh loop the same section defines. The aside goes.
- **PLAN.md Phase 7** says to check in recorded scenarios "replacing hand-written ones."
  Recordings structurally cannot capture what the hand-written ones exist for. They compose.

## Context

M1 built the event vocabulary by hand, from the shape of a real run captured in
`docs/captures/phase2-claude-run.jsonl`. `repos/billing-api/repo.yaml` says so in a comment:
*"Hand-written from the shape of the real Claude run … M3 replaces hand-written repos like this
one with recordings."* That comment is half right, and this spec is where the other half gets
settled.

Four facts constrain the design:

- **`BackendSession` is the only seam.** a2acode's `Backend` protocol is one method,
  `drive(session, request)`. Everything a backend produces goes through `session.emit` or
  `session.request_permission`. That is the whole interception surface, and it is above the
  vendor — so `acp` and `claude` record identically.
- **`make_backend()` is public.** The rig can construct a real backend with no fork, the same
  property that let `playback` exist out of tree (DESIGN-v3 §7).
- **`--backend claude` cannot emit plans at all.** It keys on a `TodoWrite` tool the current
  Claude Code no longer offers (`docs/UPSTREAM.md`, taskwarrior `70dc7c04`). Real recording has
  to go through `--backend acp --agent claude`.
- **`ACPBackend` has no `max_budget_usd`.** Only `ClaudeBackend` takes one. The acp connection
  tracks `cost_usd` internally, so a ceiling *could* exist upstream; it does not. Phase 7's
  "budget-capped" is therefore procedural on the path we need.

## Decisions

### The recording seam: a proxy session inside a decorator

`RecordingBackend(inner)` implements `Backend`. Its `drive(session, request)` hands the inner
backend a `_RecordingSession` that delegates to the real session through `__getattr__` and
overrides the two methods that carry meaning:

- `emit(event)` — append the serialized event to the current list, then forward unchanged
- `request_permission(tool, input, description)` — record the request, await the real decision,
  open the branch the caller actually chose, then return the decision untouched

The recorder observes. It never changes what the caller sees, and it never changes what the
inner backend receives.

**One `drive()` call is one turn is one play.** That falls out of the protocol rather than from a
convention imposed on top of it.

Two alternatives were considered and rejected:

- **Wire-level recording.** `dump_stream.py` already captures A2A streams, and everything in
  `docs/captures/` came from it. But the wire is downstream of the protocol mapping: `plan` and
  `thought` arrive as separate artifacts, `file_change` as diffs, `response` in append/last_chunk
  chunks. Reconstructing the original `BackendEvent` stream from that is lossy guesswork.
  DESIGN-v3 §6 already settled this — recording at the BackendEvent level is what makes `acp` and
  `claude` recordings interchangeable.
- **Patching a2acode to add a tee hook.** Cleanest implementation, and where M4 should land. As a
  *starting* point it inverts the strategy that has worked for three milestones: build out of
  tree against the real thing, prove it, then offer it upstream. Starting with a patch means the
  rig cannot run until a PR lands.

### Branch fidelity: record what happened, fail loudly on the rest

A real run takes exactly one branch. Record a run where the Bash call was approved and you have
`on_allow` and nothing else — and the other branch cannot be invented without reintroducing
exactly the imagination M3 exists to remove.

So a recording writes **only the branch taken**, and reaching an unrecorded branch at replay
raises rather than doing nothing.

This is a change to existing code. `PlaybackBackend._answered` does
`body.get("on_allow" if decision.allow else "on_deny") or []`, so an unscripted branch currently
produces a turn that emits nothing and ends with no `result` — silently. It reads to a frontend
as a frontend bug. The new behavior extends the rig's existing rule (`repo.select` already
refuses to guess an unmatched turn, in those words) from turn matching to branch selection.

**One rule, not two:** a branch that is absent *or* empty raises when reached. Distinguishing
`on_deny:` (which YAML parses to `None`) from `on_deny: []` is a subtlety nobody will use
deliberately, and both produce the same dead end.

The timeout path folds in consistently: `_permission` falls back
`on_timeout` → `on_deny` → raise.

`load_scenario` also gains a load-time check that a `permission` has **at least one** branch. One
with none can never do anything, and that is knowable at startup rather than mid-turn.

### Recorded and hand-written scenarios compose

Recordings own the happy paths — real tool shapes, real diff formats, real plan structure, real
`result` metadata. Hand-written scenarios stay for what a live run cannot be made to produce on
demand: the deny branch, the abandoned-approval timeout, `error` events, budget exhaustion,
refusals. DESIGN-v3 §4 calls those "the states a frontend is usually worst at handling and can
never reproduce on demand against a live backend," and M1 shipped them for that reason.

Both live in the same repo's `scenarios/` and concatenate. Phase 7's exit criterion is corrected:
*the backbone is recorded* is right, *replacing hand-written* is not — that would trade real
coverage for provenance.

### Match rules: escaped, anchored regex

A recorded play matches on `regex:` built from `re.escape(prompt)` anchored at both ends.

It is the only derivation that is automatic, exact, and collision-free: two different prompts can
never both match, so recordings compose in one repo without shadowing each other. `contains:` is
substring and case-insensitive, so a later prompt quoting an earlier one silently hits the wrong
play — the multi-turn case recording produces most often. `turn: N` collides immediately, since
every recording emits a `turn: 1` play and the first file wins with no error.

Start strict, loosen deliberately. A human scrubbing the file can relax a regex to a `contains:`
slug; nothing can tighten a `contains:` that has already shadowed something.

`re.escape` escapes spaces (they live in its special-characters map, for verbose-mode safety), so
a prompt serializes as `^Add\ a\ /health\ endpoint\ to\ the\ API\.$`. `yaml.safe_dump` writes that
as a plain scalar, where backslashes are literal — no double-escaping, and it round-trips through
`yaml.safe_load` unchanged.

### No per-event timing

DESIGN-v3 §6 wants two things that fight. It says the recorder tees every event "plus timing",
and it says the refresh loop works by re-recording source prompts and **diffing normalized
streams** so that "changed recordings tell you exactly what the frontend must newly handle."

Wall-clock timing differs on every run. Per-event `delay_ms` means every re-recording diffs on
every line and the refresh-loop signal drowns in noise. Since the refresh loop is the entire
payoff Phase 7 buys, timing loses. **DESIGN-v3 gets corrected rather than the code bent to match
it.**

Pacing is unaffected: `repo.yaml`'s `defaults.delay_ms` and `PLAYBACK_SPEED` set it per repo,
which is the knob demos actually want.

### Scrubbing: narrow and mechanical, then a human read

The recorder itself does two things, both universal and both mechanical:

- rewrite the `--cwd` prefix to a relative path everywhere it appears — tool inputs, tool output,
  diff bodies, `file_change.path`
- drop `session_id`, since `PlaybackBackend` already falls back to `request.context_id`, making
  the drop strictly more correct than recording a dead UUID

`cost_usd`, `num_turns`, and `usage` are **kept**. Realistic numbers are the point.

Everything else is a human read-through, named as a step in the runbook rather than left to hope.
No configurable scrub-rule surface: YAGNI for a handful of recordings against a throwaway Flask
app.

## Components

Three new modules in `a2a-rig/src/a2a_playback/`, mirroring the existing `backend.py`
(mechanism) / `serve.py` (CLI) split.

**`recording.py` — the mechanism.** `RecordingBackend`, the `_RecordingSession` proxy, and
`_to_scenario_event()`. Written to be upstreamable on its own, the way `backend.py` is; DESIGN-v3
§7 wants `playback` and `--record` as two PRs.

**`scrub.py` — the mechanical redaction.** One job, no backend or server needed to test it, and
the piece most likely to grow a rule later.

**`record.py` — the `rig-record` CLI.** Builds a real backend via `make_backend()`, wraps it,
serves one agent at the host root, writes YAML to `--out`.

**`--out` is a staging path and the CLI enforces it**: it refuses to write into any directory
named `scenarios/`, exiting 2 with the reason. A raw recording carries unscrubbed paths and
possibly a shadowing match, and landing it live means the next `rig-serve` boot fails on a file
nobody has read yet. Making the scrub a deliberate `mv` is the whole point of staging; a flag that
*could* skip it is a flag that will.

`rig-serve` is left alone. It serves scripted agents from a repo directory; its flags
(`--repo`/`--repos`) are disjoint from what a live backend needs (`--cwd`, `--agent`,
`--max-budget-usd`), and M4 upstreams `--record` onto a2acode's own `serve` anyway, so the
out-of-tree command does not need to mirror that spelling.

**`rig-record` serves rather than one-shots.** Recording taps inside the server, so something
must drive it from outside — and it has to be a real A2A client, because the permission round
trip *is* an `input-required` exchange with a caller. One-shot would mean either
`--permission-mode acceptEdits` (which never records a gate at all) or inventing a console
prompt. Serving means the recorded run went through the same path Phase 2 and Phase 5 did.

### Drift risk

`_to_backend_event` (in `backend.py`) and `_to_scenario_event` (in `recording.py`) are inverses
living in two files, and they will rot apart. The round-trip test pins them. That is the main
reason to build it first rather than last.

## Data flow through one turn

The recorder keeps a **stack** of event lists. A new turn starts with `stack = [[]]`.

| Call | Effect |
|---|---|
| `emit(e)` | append `_to_scenario_event(e)` to `stack[-1]`, forward unchanged |
| `request_permission(...)` | append a `permission` node to `stack[-1]`, await the real decision, create `on_allow`/`on_deny` per the decision, push it, return the decision |
| anything else | `__getattr__` straight through |

Nesting everything after a gate *inside* the branch is required, not stylistic:
`PlaybackBackend._run_events` returns after handling a permission, so a post-gate event left at
the top level would never fire on replay. A second gate inside a branch simply pushes again.

When `drive()` returns, the stack pops, the play is built with its `regex:` match, scrubbed,
appended to the document, and **the whole file is rewritten** — every turn, not at shutdown. Real
money was just spent; a `ctrl-C` should not cost the recording.

If `drive()` raises, an `- error: "<message>"` event is appended, the play is written, and the
exception is **re-raised** so a2acode's real failure path runs and the caller sees the failed task
it would have seen anyway. A real failure that happens for free is exactly the coverage recording
otherwise cannot manufacture.

## The recorded file format

No format change. A scenario document already accepts `plays` and, optionally, `recorded` — the
allowance added in M2's fix wave for precisely this.

```yaml
# rig-record: acp:claude against ~/scratch/demo-app, 2026-08-07.
# Scrubbed: cwd rewritten, session_id dropped. Cost and usage are real.
recorded:
  at: "2026-08-07T21:14:33Z"
  backend: "acp:claude"
  a2acode: "0.6.2"
  prompts:
    - "Add a /health endpoint to the API."
    - "Now write a test for it."

plays:
  # "Add a /health endpoint to the API."
  - match: { regex: ^Add\ a\ /health\ endpoint\ to\ the\ API\.$ }
    events:
      - plan: { steps: [...] }
      - tool_use: { name: Read, input: { file_path: "src/app.py" }, id: toolu_01... }
      - tool_result: { id: toolu_01..., name: Read }
      - file_change: { path: "src/app.py", diff: "..." }
      - permission:
          tool: Bash
          input: { command: "pytest tests/ -q" }
          on_allow: # the branch actually taken; there is no on_deny
            - tool_result: { id: toolu_02..., name: Bash, output: "42 passed" }
            - text: "Added /health; tests pass."
            - result: { cost_usd: 0.0173, num_turns: 4, stop_reason: end_turn }
```

**`recorded.prompts` is machine-readable because the refresh loop consumes it.** "Re-record the
library's source prompts" needs the prompts back. That is the same test the rejected
timing-in-provenance option failed: this one has an actual consumer. It is a *source list for
re-recording*, not an index into `plays`, so pruning a play during scrub does not corrupt it.

**The raw prompt also goes in a YAML comment above each play.** An `re.escape`d regex is
unreadable, and the human doing the scrub is the one deciding whether to loosen it.

## Promotion: scenario files get numeric prefixes

Every shipped repo's scenario file currently ends with a `match: {}` catch-all:

```
repos/billing-api/scenarios/refactor.yaml            → ends `match: {}`
repos/checkout-web/scenarios/upgrade.yaml            → ends `match: {}`
repos/infra-terraform/scenarios/plan-and-apply.yaml  → ends `match: {}`
```

Files load in sorted filename order and their plays concatenate, so dropping a second file into
`billing-api/scenarios/` that sorts *after* `refactor.yaml` makes every recorded play unreachable
and `_reject_shadowed_plays` fails the repo at boot. Loud, and correct — but it means promoting a
recording is not a `mv`. (`recorded-*.yaml` happens to sort before `refactor.yaml` because `c` <
`f`. That is luck, not design.)

Fix: rename to `10-refactor.yaml`, `10-upgrade.yaml`, `10-plan-and-apply.yaml`, and lift each
trailing catch-all into its own `99-default.yaml`. After that, promoting a recording is a `mv`
into `20-<slug>.yaml`, forever, with the catch-all last by construction.

## Testing

The keystone is a **round-trip through playback itself**: `RecordingBackend(PlaybackBackend(repo))`
served for real and driven for real, where the recorded YAML — reloaded as a repo and replayed —
produces an identical event stream. It pins the two inverse serializers together and costs zero
inference.

Around it:

- round-trip **with a gate**, allowed and denied in separate runs, asserting the recording holds
  only the branch taken and nests it correctly
- the recorder's output **loads through `load_scenario`** — the assumption all of M3 rests on,
  verified first
- **scrub units**: cwd prefix rewritten in tool input, tool output, diff body, and
  `file_change.path`; `session_id` dropped; `cost_usd`/`usage` preserved
- **fail-loud**: replaying a recording and taking the unrecorded branch raises, naming the branch
- **error turn**: a raising backend yields an `error:` play *and* still fails the task
- **crash safety**: after turn 1 and before turn 2, the file on disk is already valid
- **CLI arg validation**, which also closes taskwarrior `002fdfc5`'s ask for argparse-branch
  coverage

**The backend-agnostic suite must need zero edits.** It has for three consecutive milestones,
which is the standing signal that changes are landing in the right layer. If the fail-loud change
forces an edit there, stop and reassess rather than editing it.

## The recording run

```
rig-record --backend acp --agent claude --cwd ~/scratch/demo-app --out <staging>/health.yaml
```

driven with `a2a-cli`. Three-plus prompts against `demo-app` (the Phase 2 Flask app, clean at
`6890fd7`), at least one hitting a real permission gate so a recorded `on_allow` exists. Then read
the file, scrub what the recorder could not, and promote into
`repos/billing-api/scenarios/20-*.yaml`.

`checkout-web` and `infra-terraform` keep their hand-written scenarios. The format is additive, so
recording them later costs nothing now, and the factory is proven by one repo.

**Cost:** procedural, since the acp path has no ceiling. Few prompts, watch `cost_usd` in each
`result`, stop. Phase 2's comparable run was ~$0.54 total.

## Documents this changes

Per this repo's conventions — DESIGN-v3 changes when architecture changes, PLAN.md gets inline
notes where outcomes differ from plan, DEVLOG.md carries the why.

- **DESIGN-v3 §6** — drop "plus timing"; state that a recording captures only the branch taken;
  record the recorded-file format and the seam
- **PLAN.md Phase 7** — "replacing hand-written ones" becomes "composing with them"; note that
  the acp path has no budget cap
- **UPSTREAM.md** — new candidate: `ACPBackend` tracks `cost_usd` but exposes no ceiling while
  `ClaudeBackend` has `max_budget_usd`. Possibly deliberate; a nit, not a blocker
- **DEVLOG.md** — dated section for the session
- **`repos/billing-api/repo.yaml`** — its comment predicts recordings *replace* hand-written
  repos. Correct it to the composition model

### Scope: the refresh loop is documented as mechanics, not as a runbook

Phase 7's third bullet asks to document "upstream bump → re-record → diff normalized streams →
update scenarios/frontend." M3 builds every piece that makes it possible — `recorded.prompts` is
machine-readable precisely so re-recording has an input, and dropping per-event timing is what
keeps the diff readable.

But the loop has never been run against a real upstream bump. Writing the full runbook now would
be describing a procedure from imagination, which is the exact failure mode M3 exists to end.

**Decision:** M3 documents the mechanics that exist and demonstrably work (how to re-record from
`recorded.prompts`, what a clean diff looks like), and stops short of prescribing what to do when
a diff shows a change. The first real a2acode bump writes that part, in DEVLOG, from evidence.
Phase 7's bullet gets an inline note saying so rather than being silently narrowed.

## Open questions

- **Is `docs/captures/` still needed once recordings exist?** Carried over from the M2 handoff and
  still open. The captures may become purely historical. No action either way in M3 — worth
  deciding once at least one recording is checked in and the overlap is concrete.
