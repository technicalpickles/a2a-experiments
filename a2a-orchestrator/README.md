# a2a-orchestrator

The cockpit: coordinate agent work across repos over A2A — chat with repo
agents, watch sessions stream, answer approvals from one place. The browser
speaks AG-UI (CopilotKit over `POST /agui/run` + SSE); the service is the
A2A client, translating between the two vocabularies at one tested seam
(`translate.py`), against [a2a-rig](../a2a-rig/README.md)'s deterministic
fakes or a live a2acode interchangeably. The design of record is
[the AG-UI spec](../docs/superpowers/specs/2026-08-12-agui-native-design.md),
which revises one decision of
[the original spec](../docs/superpowers/specs/2026-08-09-a2a-orchestrator-design.md).

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
2. Open a chat with `billing-api` and say hello — the reply streams into
   CopilotKit's chat as one AG-UI run; behind the seam it's genuine A2A
   (JSON-RPC + SSE) spoken by the service.
3. Say `please run the tests` — the task parks in `input-required`, which
   arrives as a `request_permission` tool call, and the approval card
   renders in the flow of the chat naming the tool and its input.
4. **Allow** — the decision rides back as the tool's result and the run
   resumes to completion. (Deny works too; the scenario answers "Skipped
   the test run.")
5. Open a chat with `infra-terraform` and say anything — its default play
   fails, and a `run failed` banner appears.

## Tests

    uv run pytest

pytest drives real subprocesses: a `playback` rig serving `../a2a-rig/repos`
and `orch-serve` in front of it — the AG-UI endpoint's full turn/approval/
failure matrix, both directions of the translation seam (unit-tested
besides), the service-side A2A client, and the missions API, zero inference.
