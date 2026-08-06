# Pass 4 — A Deterministic Anthropic API Substitute

*Research snapshot: 2026-08-05. Goal: develop and test the A2A wrapper without doing real
inference, by pointing Claude Code / the Agent SDK at a fake Anthropic Messages API.*

## 1. Pointing Claude Code at an alternate endpoint (officially supported)

Primary sources: the Claude Code gateway docs —
[llm-gateway-connect](https://code.claude.com/docs/en/llm-gateway-connect),
[llm-gateway-protocol](https://code.claude.com/docs/en/llm-gateway-protocol),
[llm-gateway](https://code.claude.com/docs/en/llm-gateway). The protocol page is effectively
**the spec for building a Claude Code-compatible fake**.

Key environment variables:

- `ANTHROPIC_BASE_URL` — points Claude Code at any Anthropic-Messages-format endpoint. It
  sends everything it would send to api.anthropic.com (beta headers, body fields), minus a few
  direct-connection-only defaults.
- `ANTHROPIC_AUTH_TOKEN` — sent as `Authorization: Bearer …`; takes effect immediately, **no
  interactive approval**. Use this for automation.
- `ANTHROPIC_API_KEY` — sent as `x-api-key`, but requires a one-time interactive approval in
  interactive mode. Avoid for a fake.
- `ANTHROPIC_CUSTOM_HEADERS` — extra `Name: Value` pairs on all API requests.
- Hermetic-run helpers: `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` (kills version checks,
  telemetry, error reports — traffic that bypasses the gateway),
  `CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1` (suppresses pre-release body fields),
  `CLAUDE_CODE_ATTRIBUTION_HEADER=0` (drops the system-prompt attribution block, which
  pre-v2.1.181 contained a per-request token — relevant to request-matching determinism).
- Agent SDK: no gateway options of its own — it passes env to the agent process. TS
  `options.env` **replaces** env (spread `process.env`); Python `env=` **merges**.
- Verify via `/status` ("Anthropic base URL", "Auth token" lines).

Proxy usage is proven and common: LiteLLM has official Claude Code tutorials, and a family of
community proxies exists specifically to sit under `ANTHROPIC_BASE_URL`
([maxnowack/anthropic-proxy](https://github.com/maxnowack/anthropic-proxy),
[anthropic-proxy-rs](https://github.com/m0n0x41d/anthropic-proxy-rs), the
[anthropic-proxy topic](https://github.com/topics/anthropic-proxy)). Both official `anthropic`
SDKs accept `base_url` and respect `ANTHROPIC_BASE_URL`.

## 2. What the fake must implement (wire contract)

**Endpoints Claude Code calls:**

| Endpoint | Required? | Notes |
|---|---|---|
| `POST /v1/messages?beta=true` | Yes | Match on path, not full URL. **Responses must stream SSE** — Claude Code consumes as it arrives; a buffering server stalls the client. |
| `POST /v1/messages/count_tokens` | Optional | If absent, Claude Code falls back to local estimation. Response `{"input_tokens": N}`. |
| `GET /v1/models?limit=1000` | Optional | Only with `CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1` (off by default). |
| `HEAD /` | Tolerate | Best-effort startup probe; may be rejected harmlessly. |

Traffic that does **not** hit the base URL: fast-mode availability and WebFetch domain-safety
checks go directly to api.anthropic.com; telemetry/version checks go elsewhere. Disable with
`CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` and `skipWebFetchPreflight: true`.

**Headers to expect:** `anthropic-version: 2023-06-01`; `anthropic-beta` (comma-separated,
changes per release — don't allowlist); `x-claude-code-session-id`;
`x-claude-code-agent-id` / `x-claude-code-parent-agent-id` (subagent attribution — very useful
for request routing/assertions in a fake); credential in `Authorization` and/or `x-api-key`.

**Body fields to tolerate:** `thinking: {"type":"adaptive"}` (sent for Claude 4.6+ **and for
unrecognized model names** — a fake with custom model ids will receive it; rejecting it with
the wrong error wording matters because Claude Code's retry-and-disable path string-matches
Anthropic's error text — always emit errors in the exact envelope
`{"type":"error","error":{"type":…,"message":…}}`), `context_management`, tool-schema fields
(`strict`, `defer_loading`), `output_config`, `system` as an array whose first block is the
attribution block, `tools`, `tool_choice`, `metadata`.

**SSE sequence** (per the [streaming docs](https://platform.claude.com/docs/en/build-with-claude/streaming)):
`message_start` (embeds a Message with `usage`) → per content block: `content_block_start`
(`text` | `tool_use` | `thinking`) → `content_block_delta` (`text_delta` |
`input_json_delta` — tool args as partial JSON strings | `thinking_delta`) →
`content_block_stop` → `message_delta` (`stop_reason`: `end_turn`/`tool_use`, cumulative
usage) → `message_stop`, with interleaved `ping` events and in-stream `error` events.

The machine-readable OpenAPI spec is public: URL in
[`.stats.yml`](https://raw.githubusercontent.com/anthropics/anthropic-sdk-python/main/.stats.yml)
of anthropic-sdk-python (a Stainless-hosted `anthropic-<hash>.yml`).

## 3. Existing mock/fake servers, evaluated

**Best in class: `aimock`** — [CopilotKit/llmock](https://github.com/CopilotKit/llmock), npm
`@copilotkit/aimock`, site https://aimock.copilotkit.dev/. TypeScript, zero-dep, ~664 stars,
Docker/Helm. A "deterministic mock LLM server for testing across processes." Emulates the
Anthropic Messages format (plus OpenAI/Gemini/Bedrock/Vertex/Ollama and MCP/A2A/AG-UI).
**Streaming: yes**, with timing physics (TTFT, tokens/sec, jitter, recorded per-frame
timestamps replayable at speed multipliers). **Tool calls: yes**, including tool-first /
interleaved block ordering. Fixtures are JSON with matching predicates — `turnIndex`,
`hasToolResult`, `toolCallId`, `toolResultContains`, `sequenceIndex`, `systemMessage`, custom
predicates — and an `X-AIMock-Context` header scopes fixtures per test. This is the closest
existing thing to a scenario-driven deterministic Claude Code backend, and it expresses
exactly "call 1 → tool_use, call 2 → end_turn."

Others:

- **mokksy/ai-mocks** (`ai-mocks-anthropic`, Maven Central; https://mokksy.dev/docs/ai-mocks/anthropic/)
  — Kotlin/Ktor, active. Dedicated `/v1/messages` mock with SSE streaming and a fluent
  matching DSL; tool_use not documented for Anthropic; JVM-only DSL.
- **StacklokLabs/mockllm** (PyPI `mockllm`) — Python; OpenAI + Anthropic endpoints; YAML
  prompt→response map; char-by-char text streaming only; **no tool use**; stale (v0.0.8,
  Feb 2025). Too shallow for agentic use.
- **evalops/mocktopus** — Python; YAML scenario rules, streaming + tool calls, record/replay —
  but **OpenAI-format only**.
- **paultyng/testagent** — Go, very new. Different layer: fakes the **`claude` CLI binary
  itself** (PTY, `--print`, stream-json, hooks, MCP handshakes). Relevant when the system
  under test *drives* Claude Code rather than being Claude Code pointed at a fake API.
- WireMock Cloud / MockGPT and mockserver advertise LLM mocking but are OpenAI-centric — no
  first-class Anthropic template found.

## 4. Record/replay (VCR-style)

- Generic HTTP VCR works: **vcrpy** / **pytest-recording** record httpx traffic from the
  Anthropic Python SDK; practitioners document doing this for LLM tests. Caveats: SSE bodies
  are stored as one blob (replay loses timing — fine for correctness), and cassette matching
  on full request bodies is **brittle against Claude Code's evolving/nondeterministic bodies**
  (pre-v2.1.181 attribution token; shifting `anthropic-beta` sets). Match on stable signals
  instead: session/agent-id headers, last-message content, turn index.
- LLM-specific: **llm-rewind** (PyPI, alpha) — mitmproxy-based HTTPS proxy recording
  OpenAI/Anthropic/Gemini **with SSE preservation**, language-agnostic (works with the Claude
  Code binary); closest to true Claude Code session record/replay.
  **langchain-replay** has an interesting hybrid pattern: replay recorded agent decisions
  while executing tools for real.
- Claude Code-specific capture (no replay): **chouzz/llm-interceptor** — mitmproxy MITM
  purpose-built for Claude Code/Cursor/Codex traffic; per-session `raw.jsonl`/`merged.jsonl`
  with key masking; configured via `HTTP(S)_PROXY` + `NODE_EXTRA_CA_CERTS`. Pairing capture
  with a replay server is an **open gap** — nobody ships turnkey record/replay of Claude Code
  HTTP sessions.
- **LiteLLM as replay:** proxy `mock_response` (static canned responses in Anthropic format)
  and response caching (exact-match → effectively replay after a warm run); both coarse — no
  scenario sequencing, same request-nondeterminism issues in cache keying.

## 5. Anthropic-official test utilities

No published official fake for consumers. The official SDKs' own CI runs against a
schema-driven mock server — formerly Stoplight Prism, now **steady**
([dgellow/steady](https://github.com/dgellow/steady), `@stdy/cli`), launched by
[`scripts/mock`](https://github.com/anthropics/anthropic-sdk-python/blob/main/scripts/mock)
against the public Stainless OpenAPI spec. Reusable by anyone: gives schema-valid example
responses on `/v1/messages` — good for contract validation, weak on scenario/tool-use
sequencing. Separately, the Claude apps gateway (self-hosted, runs from the `claude` binary)
serves a machine-readable contract at `GET /protocol`.

## 6. Recommendations

1. **The seam:** `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN` +
   `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` (+ `CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1`,
   `CLAUDE_CODE_ATTRIBUTION_HEADER=0` for matching stability) gives a fully hermetic Claude
   Code run against a local fake.
2. **First choice: aimock** — the only mature project with Anthropic format + SSE + tool_use +
   turn-sequenced deterministic fixtures. Validate early that it tolerates Claude Code's
   request shape (`thinking: adaptive`, beta headers, attribution block).
3. **Fallback: a small bespoke scenario server** (~a few hundred lines of FastAPI/Express SSE)
   implementing §2 exactly, with scenarios as ordered turn scripts. The wire contract is
   small and fully documented, so this is cheap insurance and gives us assertion hooks aimock
   may lack (e.g. asserting on `x-claude-code-agent-id`).
4. **Later: record/replay** — capture real sessions with llm-interceptor or mitmproxy once
   we're doing real inference, replay through the scenario server. Key cassettes on stable
   signals (turn index, last-message content, session/agent headers), never full-body equality.
5. **Alternative layer to remember:** faking the `claude` CLI itself (testagent-style) — not
   applicable to our design (we sit *above* the SDK), but useful if we ever test orchestrators
   that drive our server end-to-end.

## Sources

[llm-gateway-protocol](https://code.claude.com/docs/en/llm-gateway-protocol) ·
[llm-gateway-connect](https://code.claude.com/docs/en/llm-gateway-connect) ·
[streaming](https://platform.claude.com/docs/en/build-with-claude/streaming) ·
[CopilotKit/llmock (aimock)](https://github.com/CopilotKit/llmock) ·
[mokksy/ai-mocks](https://github.com/mokksy/ai-mocks) ·
[StacklokLabs/mockllm](https://github.com/StacklokLabs/mockllm) ·
[evalops/mocktopus](https://github.com/evalops/mocktopus) ·
[paultyng/testagent](https://github.com/paultyng/testagent) ·
[LiteLLM Claude Code tutorial](https://docs.litellm.ai/docs/tutorials/claude_non_anthropic_models) ·
[LiteLLM mock_response](https://docs.litellm.ai/docs/completion/mock_requests) ·
[vcrpy](https://vcrpy.readthedocs.io/en/latest/usage.html) ·
[pytest-recording](https://github.com/kiwicom/pytest-recording) ·
[llm-rewind](https://pypi.org/project/llm-rewind/) ·
[llm-interceptor](https://github.com/chouzz/llm-interceptor) ·
[langchain-replay](https://github.com/sixty-north/langchain-replay) ·
[dgellow/steady](https://github.com/dgellow/steady) ·
[anthropic-sdk-python scripts/mock](https://github.com/anthropics/anthropic-sdk-python/blob/main/scripts/mock)
