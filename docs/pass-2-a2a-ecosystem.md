# Pass 2 — The A2A Ecosystem: Testing, Tooling, and Prior Art

*Research snapshot: 2026-08-05. Focus: how to exercise, test, and validate a new A2A server
during development — plus existing projects that already wrap coding agents (including Claude
Code) in A2A.*

## 1. a2a-inspector — the interactive debugger

Repo: https://github.com/a2aproject/a2a-inspector (official).

A web UI you point at any A2A server base URL. It fetches and displays the agent card, runs
spec-compliance validation on the card and on live responses, gives you a live chat interface
to the agent, and — most usefully — a **debug console showing raw JSON-RPC traffic in both
directions**. This is the standard first-line tool for SSE/wire-level visibility. Recent CI
runs show it now targets **spec v1.0 with backward compat for v0.3 agents**.

Run locally (port 5001; needs Python 3.10+, `uv`, Node):

```bash
git clone https://github.com/a2aproject/a2a-inspector.git && cd a2a-inspector
uv sync && (cd frontend && npm install)
bash scripts/run.sh
```

Or Docker (port 8080): `docker build -t a2a-inspector . && docker run -d -p 8080:8080 a2a-inspector`

Community alternative: https://github.com/hybroai/a2a-agent-inspector.

## 2. a2a-tck — the official compliance suite

Repo: https://github.com/a2aproject/a2a-tck.

Pytest-based compatibility kit validating A2A servers across all three transports (JSON-RPC,
gRPC, HTTP+JSON). It discovers the SUT via `{sut-host}/.well-known/agent-card.json` and picks
transports from the declared capabilities. TCK pass rate is the de-facto compliance badge in
the ecosystem (community SDKs advertise scores against it).

```bash
git clone https://github.com/a2aproject/a2a-tck.git && cd a2a-tck
uv venv && source .venv/bin/activate && uv pip install -e .
./run_tck.py --sut-host http://localhost:9999
# options: --transport grpc|jsonrpc|http_json, --level must|should|may, -v
```

RFC-2119 tiers: MUST = hard fail, SHOULD = xfail, MAY = skip-if-unsupported. Reports in JSON,
HTML, and JUnit XML. SDK validation guide:
https://github.com/a2aproject/a2a-tck/blob/main/docs/SDK_VALIDATION_GUIDE.md

## 3. CLI clients and curl-level testing

- **a2a-cli** (https://github.com/ericabouaf/a2a-cli): `npm install -g a2a-cli`. Commands:
  `a2a-cli chat` (interactive; `/new` resets session), `a2a-cli send "msg"` (`--wait` streams
  to task completion), `a2a-cli get <task-id>`, `a2a-cli cancel <id>`, with
  `--server http://localhost:8000`. Auto-fetches the agent card; supports stdin piping.
  Notably by the same author as claude-a2a (§7) — built to test exactly this kind of server.
- **Official samples CLI host** (`samples/python/hosts/cli` in a2a-samples): demonstrates SSE
  streaming iteration, `input-required` multi-turn re-prompting, and a
  `push_notification_listener.py` webhook receiver. Best reference for how a client should
  consume your server.
- **curl** (JSON-RPC POST to the server root; 0.3-style shown — adjust method/state names for
  1.0 servers):

```bash
curl -X POST http://localhost:9999/ -H "Content-Type: application/json" -d '{
  "jsonrpc":"2.0","id":1,"method":"message/send",
  "params":{"message":{"role":"user","messageId":"<uuid>",
    "parts":[{"kind":"text","text":"tell me a joke"}]},"metadata":{}}}'
```

Streaming: same body with the streaming method + `-H "Accept: text/event-stream"` and `-N`.
Multi-turn: echo back the returned `contextId` (and `taskId` when answering `input-required`).

## 4. a2a-samples

Repo: https://github.com/a2aproject/a2a-samples — Python, Go, Java, C#/.NET, JS under
`samples/<lang>/`, plus notebooks, a web demo UI, an `extensions/` dir, and an `itk/`
integration-testing toolkit + dashboard.

Highlights: **helloworld** (with `test_client.py`, a minimal client harness), the **LangGraph
currency agent** (the canonical streaming + multi-turn `input-required` demo), CrewAI,
Semantic Kernel, ADK, LlamaIndex, and AG2 agents; hosts include the CLI host, a multi-agent
orchestrator host, and the demo web UI.

## 5. Frameworks that can act as A2A clients (ready-made "real client" harnesses)

| Framework | Client support | How |
|---|---|---|
| **Google ADK** | First-class | `pip install google-adk[a2a]`; `RemoteA2aAgent(name=…, agent_card="http://host/.well-known/agent-card.json")` as a sub-agent. Auto-detects 0.3 vs 1.x. Docs: https://adk.dev/a2a/quickstart-consuming |
| **Microsoft Agent Framework** (SK + AutoGen successor) | First-class | Python: `pip install agent-framework-a2a --pre`, `A2AAgent(name=…, url=…)`; .NET: `Microsoft.Agents.AI.A2A`. Streaming, background tasks w/ continuation tokens. Docs: https://learn.microsoft.com/en-us/agent-framework/agents/providers/agent-to-agent |
| **CrewAI** | Native (~v1.x+) | `pip install 'crewai[a2a]'`; `Agent(..., a2a=A2AClientConfig(endpoint=".../.well-known/agent-card.json", max_turns=10))`; supports multi-turn + streaming/polling/push. Docs: https://docs.crewai.com/en/learn/a2a-agent-delegation |
| **LangGraph / LangSmith Agent Server** | Server-side native; client via plain HTTP | Exposes `/a2a/{assistant_id}`; maps contextId ↔ LangSmith thread_id. Docs: https://docs.langchain.com/langsmith/server-a2a |

Practical implication: ADK's `RemoteA2aAgent`, Agent Framework's `A2AAgent`, and CrewAI's
`A2AClientConfig` are three off-the-shelf integration-test drivers for a new server.

## 6. Discovery and registries

- Convention: `GET {base}/.well-known/agent-card.json`. Older deployments vary
  (`/.well-known/agent.json`, extension-less `agent-card`) — be lenient when probing prior art.
- **A2A Registry** (community directory of live agents): https://www.a2a-registry.org/ ·
  https://github.com/prassanna-ravishankar/a2a-registry — itself exposes an agent card and a
  `POST /a2a/discover` natural-language routing API.
- Gemini Enterprise offers managed A2A agent registration.
- Field caveat: "[Most Published Agent Cards Are Not Actually A2A](https://apievangelist.com/2026/07/29/most-published-agent-cards-are-not-actually-a2a/)" —
  many published cards fail validation, which is exactly why the inspector + TCK matter.
- Main ecosystem catalog: https://github.com/ai-boost/awesome-a2a

## 7. Prior art: coding agents (and Claude Code specifically) behind A2A

This space is already populated — four direct precedents:

1. **ericabouaf/claude-a2a** (https://github.com/ericabouaf/claude-a2a) — TypeScript A2A
   server wrapping the Claude Code SDK. `npm install -g claude-a2a`, set `ANTHROPIC_API_KEY`,
   run in a working dir; port 3008; card at `/.well-known/agent-card`. Streaming, contextual
   sessions, **artifact publishing of created/modified files**, hooks to intercept tool usage.
   Self-described "not production ready."
2. **kanywst/a2claude** (https://github.com/kanywst/a2claude; writeup:
   https://dev.to/kanywst/a2claude-turn-claude-code-into-a-server-other-ai-agents-can-call-1mf6)
   — Python. The most design-thoughtful of the bunch; ideas worth stealing:
   - preserves structured metadata (tool runs, file diffs, cost, permission requests) instead
     of flattening everything to text;
   - maps Claude session IDs ↔ A2A contextIds for multi-turn;
   - uses the **`input-required` state for permission-approval workflows**;
   - ships an **echo backend for offline protocol testing** (deterministic, no inference);
   - agent card exposes discrete skills (codegen, debugging, testing) rather than one generic
     chat skill.
3. **dwmkerr/claude-code-agent** (https://github.com/dwmkerr/claude-code-agent) —
   containerized/isolated Claude Code A2A agent, port 2222. Per-request isolated session
   workspaces, SSE delta streaming, `/.well-known/agent-card.json`, `/health`, MCP/plugin
   configuration, `.init-session.sh` per-session setup, `make dev-safe` to avoid inheriting
   host credentials.
4. **caomyer/claude-code-a2a-multiagent** — terminal multi-agent system using A2A with the
   Claude Code CLI as the execution toolkit.

Adjacent: Gemini CLI has an open RFC for an A2A extension
(https://github.com/google-gemini/gemini-cli/discussions/7822) — discussed, not shipped. No
notable aider wrapper surfaced. Other production-grade servers worth reading: LangSmith Agent
Server's A2A endpoint, Inkeep's A2A endpoint, Quarkus a2a-java 1.0.0.Alpha1.

**Recurring design decisions across all of these:** session ↔ contextId mapping,
`input-required` for permissions, artifact publication of file changes, streaming deltas, and
workspace isolation. a2claude's offline echo backend is the standout testing idea.

## 8. Observability and debugging

- a2a-inspector's raw JSON-RPC debug console; `curl -N` + `Accept: text/event-stream` for raw SSE.
- **Kong AI A2A Proxy plugin** and **Kong A2A Traffic Gateway** — the main commercial
  gateway-level proxy/logging pattern (https://developer.konghq.com/plugins/ai-a2a-proxy/).
- The spec's official observability answer is deliberately boring: standard HTTP
  infrastructure — **OpenTelemetry** tracing with `traceparent` propagation, gateway
  auth/rate-limiting (https://a2a-protocol.org/latest/topics/enterprise-ready/).
- LangSmith auto-maps A2A `contextId` → `thread_id` for cross-agent trace grouping.
- Good conceptual reads: Heiko Hotz, "A2A Deep Dive: Getting Real-Time Updates"
  (https://medium.com/google-cloud/a2a-deep-dive-getting-real-time-updates-from-ai-agents-a28d60317332);
  streaming/async topic (https://a2a-protocol.org/latest/topics/streaming-and-async/);
  cross-language dev/test workflow
  (https://medium.com/google-cloud/cross-language-a2a-agent-development-and-testing-ad5a14d4614c).

## Takeaways for our dev loop

The trio to build around: **a2a-inspector** (interactive + card validation) →
**a2a-tck** (`./run_tck.py --sut-host …`, gate on the MUST tier) → **a2a-cli / samples CLI
host** (scripted send, streaming, multi-turn, cancel). The spec is now v1.0 with three
transports; both TCK and inspector handle v0.3 ↔ 1.0, so decide explicitly which version(s) to
target. For the Claude Code wrapper itself, claude-a2a / a2claude / claude-code-agent are
direct prior art — none appears to be both current (spec 1.0) and production-grade, which is
the gap this spike can explore.
