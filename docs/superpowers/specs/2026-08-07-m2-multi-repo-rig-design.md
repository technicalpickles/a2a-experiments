# M2: the multi-repo rig

**Date:** 2026-08-07 · **Phase:** 6 (PLAN.md) · **Milestone:** M2 (DESIGN-v3 §8)

Turns the single-scenario `playback` server into a *directory of fake repos* a frontend and
agent tests can develop against offline. Phase 6's exit criterion: agent tests run against 3+
fake repos in under 5s total, and the frontend dev loop is offline.

This spec settles the questions M2 opens; the architectural decisions in it (the registry
contract, the mounted-app topology) belong in DESIGN-v3 once implemented, per this repo's
convention that DESIGN-v3 is the plan of record.

## Context

There is no frontend yet. M2 designs the dev environment before its first consumer, so the
governing risk is over-building for an imagined one. Two facts constrain the design:

- **playback never touches the filesystem.** `_playback_command` doesn't pass `--cwd`; a fake
  repo's content is entirely in its scenario YAML.
- **a2acode has no file API.** Its HTTP surface is card routes, JSON-RPC, and REST A2A routes
  (`server.py:140-150`). A frontend cannot read repo files from the producer — not in the rig,
  and not in production either.

The second fact is why real checkouts were considered and rejected as a *frontend* argument:
serving files would mean inventing an endpoint the real producer doesn't have, which is the
drift DESIGN-v3 exists to prevent, made worse by looking official. Whether something else
eventually serves files is deliberately left open (see Layout).

## Layout

```
a2a-rig/
  repos/                          # the fake-repo directory
    billing-api/scenario.yaml     # moved from scenarios/billing-api.yaml
    checkout-web/scenario.yaml    # new
    infra-terraform/scenario.yaml # new
  tests/scenarios/vocabulary.yaml # unchanged
```

**A repo is a directory containing `scenario.yaml`.** Directory-per-repo rather than a flat
`<name>.yaml` because the file-access question is open, not decided: a `files/` subdirectory or
a real checkout can land later without moving anything consumers depend on. That is the only
thing the extra directory level buys, and it is bought deliberately.

`scenarios/billing-api.yaml` moves to `repos/billing-api/scenario.yaml` so there is one answer
to "where do fake repos live." `tests/scenarios/` stays where it is: those are test
instruments, not repos, and must not appear in the registry.

Three repos ship, because the exit criterion says 3+ and because two is not enough to notice a
registry that accidentally serves the same scenario twice.

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
- **`name` and `description` come from the scenario's `card:` block.** No second place to
  declare a repo's identity, and no filename-derived metadata that could disagree with the card
  a client actually fetches.
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

`--repos` and `--scenario` are mutually exclusive; passing both is an argument error rather
than one silently winning. The harness helper `a2a_rig.server.serve()` gains a matching
`repos=` parameter alongside its existing `scenario=`, subject to the same exclusivity.

Mounts one `build_app()` per repo at `/repos/<name>/` in a parent Starlette app. `build()`
already returns a plain Starlette app, so the wrapper is small (DESIGN-v2 §9 pattern 2). One
port, one process, one thing to start and stop, one origin.

The cost is that cards live at `/repos/<name>/.well-known/agent-card.json` rather than at a
host root, so generic off-the-shelf clients need to be handed an explicit card URL instead of
discovering one by probing.

**Retained — one process per repo.** The existing `rig-serve --scenario X --port N` path is
unchanged. This is the topology a real deployment would use, where each repo is a standalone
agent with a root-scoped card that any client discovers unaided.

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
`tests/test_playback.py` (`_scenario_servers`) rather than introducing a third approach.

## Error handling

**A malformed scenario anywhere under `repos/` fails startup, naming the offending file.**
Consistent with how `rig-serve` already treats scenario errors (`serve.py` catches
`ScenarioError`, prints, exits 2). A rig that silently serves 2 of 3 repos is worse than one
that refuses to start: the missing repo would surface later as a confusing 404 in a frontend
rather than as an error at the moment it was introduced.

An empty `repos/` directory is an error for the same reason — a rig serving nothing is a
configuration mistake, not a valid state.

## Testing

- The index lists every repo in the directory, and only those (no test instruments leak in).
- Each advertised `card_url` is reachable and returns a card whose name matches the index.
- Two repos serve genuinely different content for the same prompt. This is the actual claim of
  the milestone — everything else is plumbing.
- A malformed scenario in the directory fails startup with the filename in the message.
- The `repos` fixture serves 3+ repos within the phase's time budget.

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
