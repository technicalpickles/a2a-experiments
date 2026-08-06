# Pass 3 — The Claude Agent SDK

*Research snapshot: 2026-08-05, from the official docs at
https://code.claude.com/docs/en/agent-sdk/overview.md and the SDK references. Focus: everything
a wrapping service needs in order to expose the SDK over another protocol.*

## 1. What it is; relationship to Claude Code

The Agent SDK packages the complete Claude Code agent loop (evaluate → request tools → execute
→ repeat) as a programmatic library you run in your own process. It does **not** require the
Claude Code CLI to be installed — it bundles its own native binary — but it shares the same
engine and the same feature set: built-in tools (Read, Write, Edit, Bash, Glob, Grep,
WebSearch, WebFetch), hooks, permissions, MCP servers, skills, sessions, subagents, and
CLAUDE.md loading.

Packages: TypeScript **`@anthropic-ai/claude-agent-sdk`**, Python **`claude-agent-sdk`**.
Changelogs:
https://github.com/anthropics/claude-agent-sdk-typescript/blob/main/CHANGELOG.md ·
https://github.com/anthropics/claude-agent-sdk-python/blob/main/CHANGELOG.md

## 2. Core API: `query()` and the message stream

```typescript
// TypeScript
query({ prompt: string | AsyncIterable<SDKUserMessage>, options?: Options }): Query  // async iterable
```

```python
# Python
query(prompt: str | AsyncIterable[...], options: ClaudeAgentOptions) -> AsyncIterator[Message]
```

Two input modes: **single-shot** (string prompt, iterate until the result message) and
**streaming input** (pass an async iterable, which lets you send follow-up user messages
mid-run and enables interrupts). Python additionally offers **`ClaudeSDKClient`**, a
connection-oriented wrapper that tracks the session across multiple `query()` calls
(`await client.query(...)` then `async for msg in client.receive_response()`).

### Message types emitted

| Type | Subtype | Fired when | Key fields |
|---|---|---|---|
| System | `init` | session start | `session_id`, `cwd`, model, tools |
| System | `compact_boundary` | context compaction | — |
| System | `informational` | status banners | `message` |
| Assistant | — | each Claude turn | `content[]` (TextBlock, ToolUseBlock), message id, `usage` |
| User | — | after each tool run | `content[]` (ToolResultBlock) |
| StreamEvent / `stream_event` | — | only with partial messages on | `event` = raw API SSE event |
| Result | `success` | done | `result` (final text), `session_id`, `total_cost_usd`, `usage`, `num_turns`, `structured_output` |
| Result | `error_max_turns` \| `error_max_budget_usd` \| `error_during_execution` \| `error_max_structured_output_retries` | failure modes | `session_id`, cost/usage where available |

Type discrimination: TS checks `message.type === "assistant"` etc. (Assistant/User wrap the API
message under `.message`); Python uses `isinstance()` against `AssistantMessage`,
`ResultMessage`, `SystemMessage`, `UserMessage`, `StreamEvent` (content is accessed directly,
no wrapper).

Docs: agent-loop.md, streaming-output.md, python.md#message-types, typescript.md#sdkmessage
(all under https://code.claude.com/docs/en/agent-sdk/).

## 3. Sessions

- Each `query()` creates or continues one session. Transcripts persist to
  `~/.claude/projects/<encoded-cwd>/<session-id>.jsonl` (or under `$CLAUDE_CONFIG_DIR`), where
  `<encoded-cwd>` is the absolute cwd with non-alphanumerics replaced by `-`. Local-only.
- Get the `session_id` from the `init` system message (Python: `msg.data["session_id"]`;
  TS: `message.session_id`) or from the result message.
- **Resume:** `options.resume = session_id` (Python `ClaudeAgentOptions(resume=…)`). **Continue
  latest in cwd:** `continue: true` / `continue_conversation=True`. **Fork:**
  `resume` + `forkSession: true` / `fork_session=True` → new session id, original untouched.
- Transcript enumeration helpers: `listSessions` / `getSessionMessages` (TS),
  `list_sessions` / `get_session_messages` (Python).
- Docs: https://code.claude.com/docs/en/agent-sdk/sessions.md

## 4. Permissions: modes, callback, hooks

**Permission modes** (`permissionMode` / `permission_mode`): `default` (unmatched tools go to
the `canUseTool` callback), `acceptEdits` (file ops + filesystem commands auto-approve),
`plan` (read-only exploration), `dontAsk` (anything not allow-listed is denied, callback never
invoked), `auto` (LLM classifier decides; v2.1.199+), `bypassPermissions` (everything runs
except explicit deny/ask rules — hooks still fire).

**Evaluation order:** PreToolUse hooks → deny rules → ask rules → permission mode →
allow rules → `canUseTool` callback (the fallback decision).
Ref: permissions.md#how-permissions-are-evaluated.

**`canUseTool`** — the interactive-approval surface, and the key hook for a wrapper:

```python
async def can_use_tool(tool_name: str, input_data: dict, context: ToolPermissionContext
                      ) -> PermissionResultAllow | PermissionResultDeny: ...
```

```typescript
canUseTool: async (toolName, input, { signal, suggestions }) =>
  ({ behavior: "allow", updatedInput: input })   // or { behavior: "deny", message: "..." }
```

Tools in `allowedTools` skip the callback entirely. `disallowedTools` accepts bare names
(removes the tool) or scoped patterns (`Bash(rm *)`).

**Hooks** (`PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `UserPromptSubmit`, `Stop`,
`SubagentStart`, `SubagentStop`, `PreCompact`, `PermissionRequest`, `Notification`, …) are
passed as callbacks in options with regex matchers and timeouts. Hook return can carry
`hookSpecificOutput.permissionDecision: allow|deny|ask|defer`, `updatedInput`,
`additionalContext`, and a `systemMessage`. PostToolUse hooks on Write/Edit are the natural
place to track file changes for artifact publication.

Docs: permissions.md, hooks.md, user-input.md.

## 5. Custom tools, MCP servers, subagents

- **In-process MCP servers:** `tool(name, desc, schema, handler)` + `createSdkMcpServer` (TS,
  Zod schemas) / `@tool` decorator + `create_sdk_mcp_server` (Python). Wire into
  `options.mcpServers` and allow via `mcp__<server>__<tool>` names. Tool results carry
  `content[]` blocks (`text`, `image`, `resource`, …), optional `structuredContent`, `isError`.
- **Subagents:** programmatic `agents` map of `AgentDefinition(description, prompt, tools,
  model, max_turns, effort)`; include `"Agent"` in `allowedTools` (older SDKs called it
  `"Task"`). Subagents get their own context; the parent sees only the final message as a tool
  result. They do not inherit parent conversation history.
- Docs: custom-tools.md, subagents.md, mcp.md.

## 6. Interrupts and cancellation

- TS: `const q = query(...); await q.interrupt();`
- Python: `await client.interrupt()` on `ClaudeSDKClient`.
- Hooks receive an `AbortSignal` (TS) for cooperative cancellation.
- On interrupt you get messages up to that point and a result with
  `subtype: "error_during_execution"`; no structured output.

## 7. Structured outputs, partial streaming, cost

- **Structured outputs:** `options.outputFormat = { type: "json_schema", schema }` /
  `output_format={...}`; validated JSON arrives on `ResultMessage.structured_output`;
  exhausted retries → `error_max_structured_output_retries`.
- **Partial message streaming:** `includePartialMessages: true` /
  `include_partial_messages=True` → `stream_event` messages carrying raw API SSE events
  (`message_start`, `content_block_start`, `content_block_delta` with
  `text_delta`/`input_json_delta`/`thinking_delta`, `content_block_stop`, `message_delta`,
  `message_stop`). This is how a wrapper gets token-level text deltas.
- **Cost/usage:** `ResultMessage.total_cost_usd` + per-model `modelUsage`/`model_usage`;
  per-step token usage on each assistant message (dedupe by message id). Prompt caching is
  automatic; cache tokens appear in usage.
- Docs: structured-outputs.md, streaming-output.md, cost-tracking.md.

## 8. Configuration & environment — including pointing at a fake API

- **Endpoint override:** set `ANTHROPIC_BASE_URL` (plus credentials) in `options.env`.
  Caveat: TS `options.env` **replaces** the environment (spread `process.env` in), Python
  `env=` **merges**. The SDK passes env through to the underlying agent process; full endpoint
  behavior is documented in Pass 4 (gateway protocol). Prefer `ANTHROPIC_AUTH_TOKEN` over
  `ANTHROPIC_API_KEY` for non-interactive runs (the API key path can require one-time
  interactive approval).
- **Model:** `options.model` (aliases like `sonnet`/`opus`/`haiku` or full ids); TS has runtime
  `q.setModel(...)`.
- **cwd:** `options.cwd` (TS); this also determines where session transcripts land — i.e. the
  workspace-isolation knob for a wrapper.
- **System prompt:** `options.systemPrompt` / `system_prompt`.
- **Settings sources:** `settingSources: ["user", "project", "local"]` controls CLAUDE.md /
  skills / hooks loading — a wrapper probably wants this pinned explicitly for reproducibility.
- Useful env for hermetic operation: `API_TIMEOUT_MS`, `CLAUDE_CODE_MAX_RETRIES`,
  `ENABLE_PROMPT_CACHING_1H`, `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` (see Pass 4).
- Docs: claude-code-features.md, typescript.md, python.md.

## 9. Limitations vs interactive Claude Code

No slash commands (`/compact` etc. — use programmatic equivalents or send the text via
streaming input); limited extended-thinking control; AskUserQuestion works but not in
subagents; Python streaming-input + `canUseTool` needs a keep-alive workaround; claude.ai MCP
connectors aren't available (pass explicit MCP servers); no terminal UI (all streaming is raw
deltas); Python client lacks SessionStart/SessionEnd hooks (TS-only). There is no official
"limitations" page — this is compiled from the overview, hooks, and reference pages.

## 10. Implications for an A2A wrapper (preview of the design doc)

1. The `query()` async-iterator is a natural event source to transcode into A2A
   status/artifact update events; the message taxonomy above is the mapping domain.
2. `session_id` + `resume` is the natural backing for A2A `contextId` multi-turn semantics.
3. `canUseTool` is the natural bridge to A2A's `input-required` state for human/agent-in-the-
   loop approval — the callback blocks, which maps cleanly onto pausing a task.
4. `interrupt()` maps onto A2A `CancelTask`.
5. `options.env` + `ANTHROPIC_BASE_URL` is the seam for a deterministic fake backend.
6. `cwd` is the workspace-isolation lever; `settingSources` should be pinned.
