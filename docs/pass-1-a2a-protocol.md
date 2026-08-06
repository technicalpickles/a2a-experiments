# Pass 1 — The A2A Protocol and Official SDKs

*Research snapshot: 2026-08-05. All field/enum/method names verified against the normative
`a2a.proto` + `specification.md` in [a2aproject/A2A](https://github.com/a2aproject/A2A) (post-1.0.1),
`a2a-sdk` 1.1.2 (Python, introspected), and `@a2a-js/sdk` 1.0.x (npm/README).*

> **Heads-up on stale sources:** most blog posts still describe spec 0.2/0.3 (lowercase task
> states, `message/send`, `TextPart`/`FilePart`/`DataPart`, `/.well-known/agent.json`). All of
> that is obsolete in 1.0 except via compat layers. Trust the proto, not Medium.

## 1. Where the protocol stands

- **Current spec: v1.0** (1.0.0 released 2026-03-12; 1.0.1 on 2026-05-28). Prior lines: 0.3.0
  (2025-07-30), 0.2.x, 0.1.0. Wire versioning is `Major.Minor`; patch is ignored for compat.
- **Governance:** Linux Foundation project (donated by Google, June 2025), 150+ supporting orgs
  (AWS, Cisco, Google, IBM, Microsoft, Salesforce, SAP, ServiceNow…). GitHub org `a2aproject`;
  the spec repo is `a2aproject/A2A` (normative artifacts: `docs/specification.md` +
  `specification/a2a.proto`).
- **Breaking changes 0.3 → 1.0** (matters because lots of prior art targets 0.3):
  - JSON-RPC methods renamed to PascalCase gRPC-aligned names: `message/send` → `SendMessage`,
    `tasks/get` → `GetTask`, etc.
  - Enums switched to ProtoJSON SCREAMING_SNAKE_CASE: `"completed"` → `"TASK_STATE_COMPLETED"`,
    `"user"` → `"ROLE_USER"`.
  - Part flattened from a `kind`-discriminated union to a proto `oneof` (`text|raw|url|data`).
  - AgentCard: `url`/`preferredTransport`/`additionalInterfaces` → ordered `supportedInterfaces`;
    `supportsAuthenticatedExtendedCard` → `capabilities.extendedAgentCard`.
  - `final` field removed from `TaskStatusUpdateEvent` (terminality inferred from state).
  - Added `ListTasks`, native multi-tenancy (`tenant`), device-code + PKCE OAuth flows
    (implicit/password removed), merged push-config types, `application/a2a+json` media type
    for REST, no more `/v1` REST prefixes.
- **0.2 → 0.3 highlights:** gRPC + REST bindings formalized, well-known URI renamed
  `agent.json` → `agent-card.json`, mTLS scheme, card `signatures`, extended-card fetch.

## 2. Core data objects (v1.0, exact names)

JSON serialization: proto snake_case → camelCase; enums serialize as proto names
(`"TASK_STATE_INPUT_REQUIRED"`, `"ROLE_USER"`); timestamps ISO-8601 UTC.

### Task

`id` (server-generated, required), `contextId`, `status` (TaskStatus, required),
`artifacts` (Artifact[]), `history` (Message[]), `metadata` (Struct).
No createdAt/updatedAt — timing lives in `status.timestamp`.

**TaskStatus:** `state` (required), `message` (a Message — the agent's explanation, e.g. the
input-required question), `timestamp`.

**TaskState enum and lifecycle:**

| State | Kind |
|---|---|
| `TASK_STATE_SUBMITTED` | initial |
| `TASK_STATE_WORKING` | active |
| `TASK_STATE_INPUT_REQUIRED` | **interrupted** (resumable) |
| `TASK_STATE_AUTH_REQUIRED` | **interrupted** (resumable) |
| `TASK_STATE_COMPLETED` | **terminal** |
| `TASK_STATE_FAILED` | **terminal** |
| `TASK_STATE_CANCELED` | **terminal** |
| `TASK_STATE_REJECTED` | **terminal** |

Lifecycle: SUBMITTED → WORKING → terminal, with WORKING ⇄ INPUT_REQUIRED/AUTH_REQUIRED loops
via follow-up messages on the same `taskId`. Terminal tasks are **non-restartable**: a
`SendMessage` to a terminal task → `UnsupportedOperationError`; start a new task in the same
`contextId` instead.

### Message

`messageId` (required, sender-minted UUID), `contextId`, `taskId`, `role` (required:
`ROLE_USER` = client→server, `ROLE_AGENT` = server→client), `parts` (required, ≥1),
`metadata`, `extensions` (URI list), `referenceTaskIds` (related tasks for context).

Rules: a client-provided `taskId` must reference an existing task (else `TaskNotFoundError`);
taskId+contextId must be consistent; clients cannot mint task IDs.

### Part (v1.0 shape)

One message with a `oneof content`: `text` (string) | `raw` (bytes; base64 in JSON) |
`url` (file by reference) | `data` (JSON value), plus `metadata`, `filename`, `mediaType`.
(0.3's `{kind: "text"|"file"|"data"}` union is gone.)

### Artifact

`artifactId` (required, unique within task), `name`, `description`, `parts` (required, ≥1),
`metadata`, `extensions`.

### Streaming events

- **TaskStatusUpdateEvent:** `taskId`, `contextId`, `status`, `metadata` (all but metadata required).
- **TaskArtifactUpdateEvent:** `taskId`, `contextId`, `artifact`, `append` (bool — append to a
  previously-sent artifact with the same ID), `lastChunk` (bool), `metadata`. This is the
  chunking mechanism for streaming large outputs.

### AgentCard

`name`, `description`, `supportedInterfaces` (ordered `AgentInterface[]`, first = preferred),
`provider`, `version`, `documentationUrl`, `capabilities`, `securitySchemes` (named map),
`securityRequirements`, `defaultInputModes`/`defaultOutputModes` (media types), `skills`
(AgentSkill[]), `signatures` (JWS over JCS-canonicalized card), `iconUrl`.

- **AgentCapabilities:** `streaming`, `pushNotifications`, `extensions` (AgentExtension[]),
  `extendedAgentCard`.
- **AgentSkill:** `id`, `name`, `description`, `tags` (all required), `examples`,
  `inputModes`/`outputModes` overrides, per-skill `securityRequirements`.
- **AgentInterface:** `url` (HTTPS URL, or host:port for gRPC), `protocolBinding`
  (`JSONRPC` | `GRPC` | `HTTP+JSON` | custom URI), `tenant` (opaque; client must echo),
  `protocolVersion` (e.g. "1.0").

### Request/response wrappers

- `SendMessageRequest{tenant, message, configuration, metadata}`
- `SendMessageConfiguration{acceptedOutputModes[], taskPushNotificationConfig, historyLength, returnImmediately}`
- `SendMessageResponse` = oneof {`task` | `message`}
- `StreamResponse` = oneof {`task` | `message` | `statusUpdate` | `artifactUpdate`}
- `GetTaskRequest{tenant, id, historyLength}`; `CancelTaskRequest{tenant, id, metadata}`;
  `SubscribeToTaskRequest{tenant, id}`
- `ListTasksRequest{tenant, contextId, status, pageSize (≤100), pageToken, historyLength, statusTimestampAfter, includeArtifacts}`
- `TaskPushNotificationConfig{tenant, id, taskId, url, token, authentication}`

## 3. Message vs Task responses; contextId; multi-turn

`SendMessage`/`SendStreamingMessage` returns **either** a bare `Message` (direct reply, no
lifecycle; stream closes immediately after) **or** a `Task`. The server decides. Spec guidance:
Message for trivial interactions and pre-task clarification ("chit-chat"); Task whenever there is
trackable work, artifacts, or multi-turn state. **Task outputs should be Artifacts, not
Messages** (§3.7) — status-update messages are best-effort context, not a reliable delivery
channel.

- **Blocking semantics:** default (`returnImmediately: false`) the call waits until the task
  reaches a terminal **or interrupted** state. `returnImmediately: true` returns as soon as the
  task exists; the client then polls, subscribes, or registers a webhook.
- **contextId** is a server-generated (client may propose) opaque grouping ID tying tasks +
  messages into one conversational session. New task in the same conversation = send a message
  with `contextId` but no `taskId`. Continue a specific task (e.g. answer INPUT_REQUIRED) =
  send with `taskId`.
- **input-required flow:** task → `TASK_STATE_INPUT_REQUIRED` with the question in
  `status.message` → blocking send returns → client sends a new Message with the same `taskId`
  → task resumes WORKING → terminal. Same shape for AUTH_REQUIRED (credentials delivered
  out-of-band by default; the agent may resume *without* a follow-up message, so disconnected
  clients should subscribe/poll).

## 4. Operations

| Operation | JSON-RPC v1.0 | REST v1.0 | JSON-RPC v0.3 (legacy) |
|---|---|---|---|
| Send message | `SendMessage` | `POST /message:send` | `message/send` |
| Streaming send | `SendStreamingMessage` | `POST /message:stream` | `message/stream` |
| Get task | `GetTask` | `GET /tasks/{id}` | `tasks/get` |
| List tasks | `ListTasks` (new in 1.0) | `GET /tasks` | — |
| Cancel | `CancelTask` | `POST /tasks/{id}:cancel` | `tasks/cancel` |
| Subscribe/resubscribe | `SubscribeToTask` | `POST /tasks/{id}:subscribe` | `tasks/resubscribe` |
| Push config CRUD | `Create/Get/List/DeleteTaskPushNotificationConfig` | `/tasks/{id}/pushNotificationConfigs…` | `tasks/pushNotificationConfig/*` |
| Extended card | `GetExtendedAgentCard` | `GET /extendedAgentCard` | `agent/getAuthenticatedExtendedCard` |

(gRPC method names match the v1.0 JSON-RPC names; service `A2AService`.)

- **Discovery:** `https://{domain}/.well-known/agent-card.json` (plus registries and direct
  configuration). Card endpoints should set Cache-Control/ETag.
- **`SubscribeToTask` must emit the current Task snapshot as its first event** (prevents
  get/subscribe races); errors on terminal tasks.
- **Service parameters** (transport-level, per request): `A2A-Version` header/metadata
  (absent ⇒ 0.3 assumed; unsupported ⇒ `VersionNotSupportedError`) and `A2A-Extensions`
  (comma-separated extension URIs the client opts into).
- **Error codes (JSON-RPC):** `TaskNotFoundError` -32001, `TaskNotCancelableError` -32002,
  `PushNotificationNotSupportedError` -32003, `UnsupportedOperationError` -32004,
  `ContentTypeNotSupportedError` -32005, `InvalidAgentResponseError` -32006,
  `ExtendedAgentCardNotConfiguredError` -32007, `ExtensionSupportRequiredError` -32008,
  `VersionNotSupportedError` -32009.

## 5. Transports

Three standard bindings; **none individually mandatory** — declare what you support in
`supportedInterfaces` (ordered by preference) and all declared bindings must be functionally
equivalent. Clients pick the first they support.

- **JSON-RPC 2.0 over HTTP:** `Content-Type: application/json`; params = the request object;
  streaming = SSE (`text/event-stream`), each `data:` line a complete JSON-RPC response whose
  `result` is a `StreamResponse`, all sharing the originating request's `id`.
- **gRPC:** HTTP/2 + TLS, proto3; service params via lowercased metadata keys (`a2a-version`);
  streaming methods are server-streaming RPCs.
- **REST (HTTP+JSON):** `Content-Type: application/a2a+json` preferred; AIP-style custom verbs
  (`:send`, `:stream`, `:cancel`, `:subscribe`); streaming = SSE with raw `StreamResponse` JSON
  (no JSON-RPC envelope).

**Stream shape (all bindings):** first event = `Task` (or a single `Message`, then close), then
ordered `statusUpdate`/`artifactUpdate` events; stream closes at terminal state and pauses at
interrupted states (reconnect via `SubscribeToTask`). Ordering must be preserved; multiple
concurrent streams per task are allowed and must see identical event sequences.

## 6. Auth model

Identity is transport-layer, out-of-band of A2A payloads; HTTPS mandatory in production.
Security schemes are OpenAPI-modeled: `APIKeySecurityScheme`, `HTTPAuthSecurityScheme`,
`OAuth2SecurityScheme` (authorizationCode + PKCE, clientCredentials, deviceCode),
`OpenIdConnectSecurityScheme`, `MutualTlsSecurityScheme`. Declared in `securitySchemes`;
required combinations in `securityRequirements` (OR-of-ANDs, OpenAPI semantics); per-skill
overrides allowed. Client sends credentials as normal HTTP headers on every request.

Mid-task secondary auth uses `TASK_STATE_AUTH_REQUIRED` + explanatory `status.message`.
Push-notification webhooks carry their own auth: `AuthenticationInfo{scheme, credentials}` used
by the agent when POSTing to the client's webhook, plus a client-chosen per-task `token`
echoed back for validation (spec §13.2 covers SSRF mitigation and JWT/JWKS patterns).

## 7. Extensions

Declared in `capabilities.extensions` as `AgentExtension{uri, description, required, params}`.
Client opts in per request via `A2A-Extensions`. Servers must error
(`ExtensionSupportRequiredError`) if a `required` extension isn't echoed by the client;
non-required unsupported requests are ignored. Data-level extension points: `extensions` URI
lists + URI-keyed `metadata` entries on Message/Artifact/Task/Part.

## 8. Official SDKs

Six official SDKs (a2aproject org): **a2a-python, a2a-js, a2a-java, a2a-go, a2a-dotnet,
a2a-rs**, plus `a2a-samples`, `a2a-inspector`, `a2a-tck`. Python/JS/Java/Go/.NET are called
"production-ready" by the LF.

### Python — `a2a-sdk` (reference implementation)

PyPI `a2a-sdk`, **v1.1.2**; spec 1.0 with a 0.3 compat layer (`a2a.compat.v0_3`). Python ≥3.10,
async-first. Extras: `http-server`, `fastapi`, `grpc`, `telemetry`, `postgresql|mysql|sqlite`.

Server-side abstractions (verified against 1.1.2):

- **`AgentExecutor`** (ABC, `a2a.server.agent_execution`): implement
  `async execute(context: RequestContext, event_queue: EventQueue)` and
  `async cancel(context, event_queue)`. Contract: enqueue exactly one `Message` (immediate
  response) OR a `Task` followed by status/artifact update events; publish a terminal or
  interrupted status before returning; **the framework re-invokes `execute` when input arrives
  after INPUT_REQUIRED**; unhandled exceptions → failed task; cancellation = asyncio cancel +
  `cancel()` callback.
- **`RequestContext`**: `message`, `task_id`, `context_id`, `current_task`, `configuration`,
  `metadata`, `related_tasks`, `requested_extensions`, `tenant`, `call_context`,
  `get_user_input()`.
- **`EventQueue`** (`enqueue_event`) consumed by `EventConsumer`; `QueueManager` /
  `InMemoryQueueManager` enable multiple subscribers per task.
- **`TaskUpdater`** convenience wrapper: `submit()`, `start_work()`, `requires_input()`,
  `requires_auth()`, `complete()`, `failed()`, `cancel()`, `reject()`, `update_status()`,
  `add_artifact()`, `new_agent_message()`.
- **`RequestHandler`** ABC (`on_message_send`, `on_message_send_stream`, `on_get_task`,
  `on_list_tasks`, `on_cancel_task`, `on_subscribe_to_task`, push-config methods,
  `on_get_extended_agent_card`) with `DefaultRequestHandlerV2(agent_executor, task_store,
  agent_card, queue_manager=…, push_config_store=…, push_sender=…, …)`.
- **Persistence:** `TaskStore` ABC → `InMemoryTaskStore`, `DatabaseTaskStore` (SQLAlchemy
  async: Postgres/MySQL/SQLite); `PushNotificationConfigStore` variants;
  `BasePushNotificationSender` for webhook POSTs.
- **HTTP wiring (1.x is routes-based; the old `A2AStarletteApplication` classes are gone):**
  `create_agent_card_routes(...)`, `create_jsonrpc_routes(...)`, `create_rest_routes(...)`,
  `add_a2a_routes_to_fastapi(app, ...)`; gRPC via `GrpcHandler` + generated servicer.
- **Client:** `ClientFactory.create(card)` / `create_from_url`; `Client.send_message`
  (async-iterates events), `subscribe`, `get_task`, `list_tasks`, `cancel_task`, push-config
  methods; `A2ACardResolver`; interceptors incl. `AuthInterceptor`.
- Migration guide: https://github.com/a2aproject/a2a-python/blob/main/docs/migrations/v1_0/README.md

### JavaScript/TypeScript — `@a2a-js/sdk`

npm `@a2a-js/sdk`, **v1.0.1** (0.3.14 was the last 0.3 release). TypeScript-first; subpath
exports `/client`, `/server`, `/server/express`, `/server/grpc`, `/compat/v0_3/*`. Express
peer-dep for HTTP; `@grpc/grpc-js` for gRPC.

- **Server:** implement `AgentExecutor` — `execute(requestContext, eventBus)` +
  `cancelTask(taskId, eventBus)`; publish events to the **`ExecutionEventBus`** (JS analogue of
  Python's EventQueue). `DefaultRequestHandler(agentCard, taskStore, agentExecutor, …)`
  orchestrates routing/storage/cancellation/push. `TaskStore` interface with
  `InMemoryTaskStore`. Transport adapters mount on one handler: `jsonRpcHandler`,
  `restHandler`, `agentCardHandler` (Express), `grpcService` (gRPC). `RequestContext` carries
  the authenticated `User` built by a `UserBuilder` from Express middleware.
- **Client:** `ClientFactory.createFromUrl(baseUrl)` (fetches card, negotiates transport) or
  `createFromAgentCard(card)`; `sendMessage`, `sendMessageStream` (AsyncGenerator of
  task/status-update/artifact-update events), `getTask`, `cancelTask`, push-config methods;
  per-call `RequestOptions{signal, serviceParameters}`; `CallInterceptor`;
  `AuthenticationHandler` + retry-on-401 fetch wrapper.
- v0.3 compat: `legacyCompat: {enabled: true}`; legacy transports auto-selected when the peer
  card's `protocolVersion` < 1.0. `ListTasks` unavailable against 0.3 peers.
- Extensions are implemented as `AgentExecutor` decorators.

## 9. Long-running & disconnected operation

Three complementary update channels: polling (`GetTask`), streaming
(`SendStreamingMessage`/`SubscribeToTask` — resubscribe re-sends the current Task snapshot
first), and **push notifications**: client registers `TaskPushNotificationConfig{url, token,
authentication}` (inline at send time or later); webhook delivery is an HTTP POST of a
`StreamResponse` JSON (`application/a2a+json`, always plain HTTP+JSON regardless of binding);
at-least-once, client must 2xx-ack and should process idempotently; typical pattern is webhook
→ then `GetTask` for authoritative state. `returnImmediately: true` + `ListTasks` gives
fire-and-forget + reconciliation.

## Key sources

- Spec: https://a2a-protocol.org/latest/specification/ (v1.0: https://a2a-protocol.org/v1.0.0/specification/)
- Normative proto + markdown: https://github.com/a2aproject/A2A
- Releases/changelog: https://github.com/a2aproject/A2A/releases
- Python SDK: https://github.com/a2aproject/a2a-python · https://pypi.org/project/a2a-sdk/
- JS SDK: https://github.com/a2aproject/a2a-js · https://www.npmjs.com/package/@a2a-js/sdk
- SDK index: https://a2a-protocol.org/latest/sdk/
- LF announcements: https://www.linuxfoundation.org/press/linux-foundation-launches-the-agent2agent-protocol-project-to-enable-secure-intelligent-communication-between-ai-agents
