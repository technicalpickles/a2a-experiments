# a2a-orchestrator

The cockpit: coordinate agent work across repos over A2A — chat with repo
agents, watch sessions stream, answer approvals from one place. This is the
`direct-sessions` walking skeleton: direct chats with repo agents through a
contextId-routed pass-through proxy, against
[a2a-rig](../a2a-rig/README.md)'s deterministic fakes. The design of record
is [the spec](../docs/superpowers/specs/2026-08-09-a2a-orchestrator-design.md).

## Layout

Two toolchains, each self-contained at its own root: `src/` + `tests/` are a
uv project (the service), `frontend/` is an npm project (the cockpit UI).
`catalog.yaml` names the index the service discovers repos from. Runtime
state lives in `var/` (gitignored).

## Run it

Three terminals, from this directory:

    uv run rig-serve --repos ../a2a-rig/repos --port 9200
    uv run orch-serve
    cd frontend && npm install && npm run dev

Open http://localhost:5173. (`rig-serve` resolves here because a2a-rig is an
editable dev dependency.)

One-process demo mode — build the frontend and the service serves it
statically at http://127.0.0.1:9300:

    (cd frontend && npm run build)
    uv run orch-serve

## The demo

1. **New mission** — describe nothing, configure nothing; a mission is
   created by starting one.
2. Open a chat with `billing-api` and say hello — the reply streams over
   genuine A2A (JSON-RPC + SSE) through the service's proxy.
3. Say `please run the tests` — the task parks in `input-required` and an
   approval card appears naming the tool and its input.
4. **Allow** — the run resumes to completion. (Deny works too; the scenario
   answers "Skipped the test run.")
5. Open a chat with `infra-terraform` and say anything — its default play
   fails, and the turn renders as failed.

## Tests

    uv run pytest

pytest drives real subprocesses: a `playback` rig serving `../a2a-rig/repos`
and `orch-serve` in front of it — proxy routing, the agent-card rewrite,
and the missions API, zero inference.
