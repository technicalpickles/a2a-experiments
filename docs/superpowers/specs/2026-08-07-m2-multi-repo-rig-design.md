# M2: the multi-repo rig

**Date:** 2026-08-07 · **Phase:** 6 (PLAN.md) · **Milestone:** M2 (DESIGN-v3 §8)

Turns the single-scenario `playback` server into a *directory of fake repos* a frontend and
agent tests can develop against offline. Phase 6's exit criterion: agent tests run against 3+
fake repos in under 5s total, and the frontend dev loop is offline.

This spec settles the questions M2 opens; the architectural decisions in it (the registry
contract, the mounted-app topology, the repo/scenario split) belong in DESIGN-v3 once
implemented, per this repo's convention that DESIGN-v3 is the plan of record.

**DESIGN-v3 needs a correction as part of this work.** §2 says "a fake repo *is* just a scenario
file" while §4 defines a scenario as "a list of plays" — the same conflation this spec resolves,
and its §2 diagram already wobbles between `repos/` and `scenarios/` in adjacent lines. That is
an architecture change, so it belongs in DESIGN-v3 rather than a DEVLOG note.

## Context

There is no frontend yet. M2 designs the dev environment before its first consumer, so the
governing risk is over-building for an imagined one. Two facts constrain the design:

- **playback never touches the filesystem.** `_playback_command` doesn't pass `--cwd`; a fake
  repo's content is entirely in YAML.
- **a2acode has no file API.** Its HTTP surface is card routes, JSON-RPC, and REST A2A routes
  (`server.py:140-150`). A frontend cannot read repo files from the producer — not in the rig,
  and not in production either.

The second fact is why real checkouts were considered and rejected as a *frontend* argument:
serving files would mean inventing an endpoint the real producer doesn't have, which is the
drift DESIGN-v3 exists to prevent, made worse by looking official. Whether something else
eventually serves files is deliberately left open (see Layout).

## Splitting "repo" from "scenario"

**A repo has scenarios; it is not one.** Today one YAML does both jobs: `name` and `card:`
declare who the fake agent is, while `plays:` is the script it runs. DESIGN-v3 carries that
conflation too — §4 defines a scenario as "a list of plays," and §2 says "a fake repo *is* just
a scenario file." The word came from the deterministic-backend research (pass-4), where the
prior art it surveyed used "scenario" for a scripted transcript. That is the meaning worth
keeping.

M2 is where the conflation starts costing something, and M3 is where it breaks:

- Nesting a `scenario.yaml` inside `repos/<name>/` gives a repo two names — the directory and
  the one in its `card:` block — with nothing keeping them honest.
- Recording (M3) produces *several* scripts per repo: the refactor session, the one that hit a
  permission gate, the one that failed. If identity lives inside the script, every recording
  restates who the repo is, and they drift apart.

So the format splits:

```
a2a-rig/
  repos/
    billing-api/
      repo.yaml                   # identity and defaults
      scenarios/
        refactor.yaml             # plays only
    checkout-web/
      repo.yaml
      scenarios/upgrade.yaml
    infra-terraform/
      repo.yaml
      scenarios/plan-and-apply.yaml
  tests/repos/vocabulary/         # test instruments are repos too
    repo.yaml
    scenarios/probes.yaml
```

**`repo.yaml` — who this agent is.** No `name:` field: the **directory name is the repo id**,
and it is what appears in URLs and in the registry. That removes the two-sources-of-truth
problem rather than resolving it by fiat.

```yaml
card:
  name: billing-api
  description: "Fake billing-api repo (playback)"
defaults:
  delay_ms: 0
```

**`scenarios/*.yaml` — what it does.** Plays, and nothing else. A mapping rather than a bare
list, so M3 recordings can carry provenance (source prompt, date, backend) without another
format change:

```yaml
plays:
  - match: { contains: "run the tests" }
    events: [...]
```

**Combining:** a repo's scenario files are read in filename order and their plays concatenated
into one list, then matched first-match-wins exactly as a single file's plays are today.
Validation runs over the *concatenated* list, not per file — otherwise a catch-all `{}` in
`01-x.yaml` would silently shadow everything in `02-y.yaml`, which is the existing
catch-all-must-be-last rule leaking across a file boundary.

This makes M3 purely additive: a recorded scenario is a new file in `scenarios/`, and nothing
about the repo or the format changes to accept it.

**One format, one concept.** The single-file `--scenario <file>` mode is removed rather than
kept as a convenience; keeping it would mean two file formats and reintroduce the exact
ambiguity this split exists to remove. A repo directory is the unit everywhere, so
`tests/scenarios/vocabulary.yaml` becomes `tests/repos/vocabulary/` — which is honest, since
vocabulary genuinely is a fake repo that happens to be used as a test instrument. Test repos
live under `tests/` and never appear in the shipped registry.

Directory-per-repo also keeps the file-access question open: a `files/` subdirectory or a real
checkout lands later without moving anything consumers depend on.

Three repos ship under `repos/`, because the exit criterion says 3+ and because two is not
enough to notice a registry that accidentally serves the same repo twice.

## The registry contract

`GET /` on the parent app returns the index. **This is the only thing consumers may assume.**

```json
{
  "repos": [
    {
      "name": "billing-api",
      "description": "Fake billing-api repo (playback)",
      "card_url": "http://127.0.0.1:9200/repos/billing-api/.well-known/agent-card.json"
    }
  ]
}
```

- **`card_url` is absolute.** That is what makes the document survive a topology change: the
  same index shape describes N mounted paths or N standalone ports.
- **`name` is the directory name** — the repo id, and the same string that appears in the URL.
- **`description` comes from `repo.yaml`'s `card:` block**, so the index quotes the card a
  client will actually fetch rather than maintaining a second copy of it.
- **No agent card at the root.** The rig is not an agent. A root card would be a small lie of
  exactly the kind this project avoids, and `/.well-known/agent-card.json` at the root
  correctly 404s.

A2A has no standard machine-readable registry format — the community A2A Registry is itself an
agent exposing `POST /a2a/discover`, not a file convention (pass-2 §6). A plain JSON index is
therefore an honest local choice rather than a competing standard. The spec sanctions
registries and direct configuration as discovery alternatives to well-known probing, so a
consumer driven by this index is spec-legal.

**Why the registry is load-bearing.** If a frontend consumes a list of card URLs, "N ports" and
"N paths on one port" are indistinguishable to it. If a frontend instead assumes root-scoped
`/.well-known/` discovery, it is welded to one topology. The registry is the seam that lets the
rig change its mind later, which is the whole reason to have one.

## Topologies

**Default — one process, N mounted apps.**

```
rig-serve --repos repos/ --port 9200
```

`--repos <dir>` serves every repo under a directory; `--repo <dir>` serves exactly one. They are
mutually exclusive, and passing both is an argument error rather than one silently winning. The
old `--scenario <file>` is removed, along with `serve()`'s `scenario=` parameter, which gains
`repo=` and `repos=` in its place.

Mounts one `build_app()` per repo at `/repos/<name>/` in a parent Starlette app. `build()`
already returns a plain Starlette app, so the wrapper is small (DESIGN-v2 §9 pattern 2). One
port, one process, one thing to start and stop, one origin.

The cost is that cards live at `/repos/<name>/.well-known/agent-card.json` rather than at a
host root, so generic off-the-shelf clients need to be handed an explicit card URL instead of
discovering one by probing.

**Retained — one process per repo.** `rig-serve --repo repos/billing-api --port N` serves a
single repo at a host root. This is the topology a real deployment would use, where each repo is
a standalone agent with a root-scoped card that any client discovers unaided.

It is retained for a specific reason, not as a hedge: it is what proves the registry
abstraction is honest. If a consumer built against the index cannot be pointed at three
standalone servers by swapping the index document, then the abstraction was never real and the
rig has invented a shape only the rig can produce.

## Risk to retire first

The default topology assumes two things that are believed true but unverified:

1. Starlette's `Mount` keeps the a2a-sdk's `create_agent_card_routes` path relative to the
   mount, so the card lands at `/repos/<name>/.well-known/agent-card.json`.
2. A card whose `url` field is the absolute mounted URL round-trips through `create_client` —
   that is, the client posts JSON-RPC back to the mounted path rather than to the host root.

Both must be proven before anything else is built on them. If either fails, the fallback is the
retained per-repo-process topology as the default, with the registry contract unchanged — which
is precisely the flexibility the registry exists to provide.

## Fixtures

One rig process per pytest session, shared across tests:

```python
async def test_agent_picks_the_right_repo(repos):
    client = await repos.client("billing-api")
```

The `repos` fixture boots the mounted rig once and exposes:

- `repos.names` — repo names from the index
- `repos.index` — the parsed index document
- `repos.client(name)` — an A2A client pointed at that repo's card

Booting once for all N is what keeps the under-5s criterion reachable as repos accumulate;
paying per-repo startup per test would not scale past a handful.

This follows the existing pooling pattern in `tests/conftest.py` (`_server_pool`) and
`tests/test_playback.py` (`_scenario_servers`) rather than introducing a third approach. The
existing `on_scenario` fixture becomes `on_repo`, pointing at `tests/repos/<name>/`.

## Error handling

**A malformed repo anywhere under `repos/` fails startup, naming the offending file.**
Consistent with how `rig-serve` already treats scenario errors (`serve.py` catches
`ScenarioError`, prints, exits 2). A rig that silently serves 2 of 3 repos is worse than one
that refuses to start: the missing repo would surface later as a confusing 404 in a frontend
rather than as an error at the moment it was introduced.

Startup errors, each naming the path:

- a directory under `repos/` with no `repo.yaml`
- a repo whose `scenarios/` is missing or holds no plays — a repo that can answer nothing is a
  mistake, not a valid state, and would otherwise fail per-turn later
- a malformed scenario file, or a concatenated play list that breaks the catch-all-must-be-last
  rule across file boundaries
- an empty `repos/` directory, for the same reason: a rig serving nothing is a configuration
  mistake

## Testing

- The index lists every repo in the directory, and only those (no test repos leak in).
- Each advertised `card_url` is reachable and returns a card whose name matches the index.
- Two repos serve genuinely different content for the same prompt. This is the actual claim of
  the milestone — everything else is plumbing.
- Plays concatenate across a repo's scenario files in filename order, and a catch-all in an
  earlier file is rejected rather than silently shadowing a later one.
- Each startup error above fails with the offending path in the message.
- The `repos` fixture serves 3+ repos within the phase's time budget.

## Migration

The split touches existing code and files. Nothing consumes the rig yet, so this is the
cheapest it will ever be — which is the reason to do it inside M2 rather than after.

- `scenarios/billing-api.yaml` → `repos/billing-api/{repo.yaml, scenarios/refactor.yaml}`
- `tests/scenarios/vocabulary.yaml` → `tests/repos/vocabulary/{repo.yaml, scenarios/probes.yaml}`
- `scenario.py` keeps its name and its job — parsing and validating a plays document — and
  loses `name`, `card`, and `defaults`, which move to a new repo loader. `Scenario` stays the
  parsed plays document; a new `Repo` carries identity, defaults, and the combined play list.
- `PlaybackBackend` takes a `Repo` rather than a `Scenario`.
- `serve.py` swaps `--scenario` for `--repo`/`--repos`.
- The validation tests in `test_playback.py` that call `parse({...})` directly split along the
  same seam: play-level rules stay with the scenario parser, and the "needs a name" style rules
  move to repo loading.

The 4 xfailed cancel tests and the backend-agnostic suite should come through untouched. If the
backend-agnostic suite needs edits, that is a signal the split leaked into the wrong layer.

## Out of scope

- Per-repo hot reload and dynamic add/remove. Restarting the rig is fast; watching the
  filesystem is a feature nobody has asked for yet.
- Auth, TLS, non-loopback binding. This is a local dev rig.
- Native project selection via `tenant`, a declared extension, or context-bound projects
  (DESIGN-v2 §9). That is the upstream-PR design conversation, not a rig milestone — and it
  changes a2acode rather than the rig.
- Real checkouts or a file-serving surface. Deliberately deferred; the layout keeps it additive.
- **The frontend and agents themselves.** Phase 6's third bullet is "start building the
  frontend and agents against this." This spec covers only the environment they build against.
  Whatever consumes the rig is its own project with its own design pass — the deliverable here
  is a dev environment that is ready for one, and the honest test of it is that a consumer can
  be written without changing the rig.
