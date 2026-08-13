# The AG-UI event log: persistence for the conversation plane

**Date:** 2026-08-13 · **Provenance:** executes the deferral the
`2026-08-12-agui-native-design.md` spec named twice — "History is advisory" promised
that when persistence arrives, the log format is AG-UI events; the message-gap
acknowledgment called the missing history "the sharpest gap" (empty pane on remount,
pending approvals lost on restart). This spec is that persistence, plus the hardening
batch that falls out of it (taskwarrior `dbcb5569`, spawned from `fc4eb2d8`). Everything
else in the 2026-08-12 spec stands.

In one sentence: every message that crosses the AG-UI seam — both directions — is
written to SQLite as it happens; a remounted pane replays it through the protocol's own
connect handshake; a pending approval survives a service restart and comes back
answerable; and three trust gaps in the run loop close along the way.

## Scope

**In:** reload replay (remount shows the full prior conversation), pending-approval
persistence across service restarts, and the hardening batch: truncated upstream stream
fails loudly, resume answers are verified, cached A2A clients are evicted with the
pending state. The stored log doubles as the recording substrate the roadmap's
recording milestone needs — that's why fidelity is full-stream, not summarized.

**Out:** out-of-band visibility. The log captures traffic that crosses the
orchestrator's seam. Turns made behind its back (a2a-cli straight at a2acode) stay
invisible to the cockpit; surfacing them would need upstream task-listing support and
is not attempted here. Also out: rich rendering of the replayed content (`13f576dc`)
and the Playwright suite (`d798cf14`) — separate threads.

## The event log

One new table in the existing SQLite file, alongside `missions` and `chats`:

```sql
CREATE TABLE IF NOT EXISTS events (
    context_id TEXT NOT NULL REFERENCES chats(context_id),
    seq        INTEGER NOT NULL,   -- monotonic per chat
    direction  TEXT NOT NULL,      -- 'in' (browser → service) | 'out' (service → browser)
    payload    TEXT NOT NULL,      -- JSON, verbatim
    created_at TEXT NOT NULL,
    PRIMARY KEY (context_id, seq)
);
```

**Both directions, because the wire is asymmetric.** AG-UI's event vocabulary only
flows server → browser; the user's own words travel the other way, as `Message` objects
inside `RunAgentInput`. A log of only the outbound stream would replay a conversation
with every user line missing. So:

- **`out` rows** are AG-UI events verbatim — every event `agui.py` encodes for the
  browser (`RUN_STARTED`, text deltas, tool calls, `RUN_ERROR`, all of it), written at
  the same point `encoder.encode` fires today.
- **`in` rows** are AG-UI `Message` objects verbatim — the new tail of each incoming
  turn (the user's message, or the allow/deny tool result on a resume), written right
  after `RunAgentInput` validates and before upstream is contacted, so even a turn that
  fails upstream shows the user's side of it.

Both directions stay in AG-UI's own vocabulary. No third format is minted, keeping the
2026-08-12 spec's "no new wire vocabulary" promise.

Write mechanics: single process, single event loop, the store's existing shared
connection; `seq` is assigned per-chat inside the insert transaction. No pool, no
queue, no coordination.

## The read path: replay through the connect handshake

The delivery mechanism is AG-UI's own, and the client is already asking. Verified
against the installed packages (`@copilotkit/react-core` 1.67.1, `@ag-ui/client`
0.0.57), by reading shipped source:

- On every mount, `CopilotChat` calls `connectAgent()`, which runs the normal event
  pipeline sourced from the agent's `connect()` method and expects
  `RUN_STARTED → MESSAGES_SNAPSHOT → RUN_FINISHED` (`verifyEvents` rejects a stream
  that doesn't open with `RUN_STARTED`).
- Plain `HttpAgent` doesn't implement `connect()`; the base class throws a sentinel
  error that `connectAgent` **silently swallows**. That silent no-op is the observed
  empty-pane-on-reload behavior.
- The tempting alternative is a trap: `AgentConfig.initialMessages` exists and
  populates `agent.messages`, but `CopilotChat`'s mount effect clears the agent's
  messages in both threadId branches (`connectAgent` on the explicit path,
  `setMessages([])` on the implicit one) before rendering. Seeded history paints once,
  then is wiped. Recorded here so nobody rediscovers it.

So the design answers the call the library is already making:

- **Server:** `POST /agui/connect`, threadId-routed like `/agui/run`. It folds the
  chat's event log into ordinary AG-UI messages and streams back
  `RUN_STARTED → MESSAGES_SNAPSHOT → RUN_FINISHED`.
- **Browser:** a small `HttpAgent` subclass whose `connect()` POSTs the
  `RunAgentInput` to `/agui/connect`. Nothing else in `ChatPane` changes shape.

**The fold** is a pure function next to `RunTranslator` in `translate.py`: events in,
`Message` list out. Text deltas concatenate into assistant messages; tool calls pair
with their results; `in` rows pass through as the user/tool messages they already are;
run-lifecycle events shape the fold but don't become messages, except that a run ending
in `RUN_ERROR` folds to an assistant message stating the run failed and why, so replay
doesn't silently launder a failure. Message ids are the ones the translator minted at stream time — stable ids
matter because `MESSAGES_SNAPSHOT` application is an id-keyed merge, not a blind
replace (0.0.57 behavior, contra older AG-UI docs). Since `connectAgent` starts from
cleared messages, the merge is effectively append-all on reload, but stable ids keep
any future re-sync honest.

The fold is also the distillation step the recording milestone will want: the raw
stream is the substrate, the fold is the reading of it, and both live at the one
tested seam.

## Pending state: rename and persistence

"Park" leaves the vocabulary — it never made sense as a noun here. The concept is a
task upstream waiting on the user's input (A2A's `input-required`), and the word is
**pending**:

- `chats` gains a nullable `pending_task_id` column.
- `Conversations.park()/clear()/parked_task()` become
  `set_pending()/clear_pending()/pending_task()`, reading and writing through the
  store instead of the in-memory dict.
- `RunTranslator.parked` and the surrounding comments rename to match.

The call sites in `agui.py` don't move: the same replaced-or-consumed-never-
incidentally-dropped decision runs at the same point after the translator has seen the
whole turn — it just lands on disk. A service restart finds the pending task where it
left it.

## Re-arming the approval card after reload

Replay gets a pending approval's *text* back on screen; it does not make the card
clickable. Verified in source: HITL status is computed from live run state
(`executingToolCallIds`, populated only by actual tool-handler execution), never from
message inspection — a seeded tool call with no result renders as `inProgress`, the
`PermissionTool` renderer shows nothing for that status, and `respond()` is a silent
no-op outside a run. `connectAgent` runs with `executeFrontendTools: false`, so the
connect stream can't arm it either.

The one supported primitive is `copilotkit.runTool()`: it synthesizes a fresh
assistant tool call (new toolCallId), fires the execution-start event so the card
renders `executing` with a live `respond()`, and on answer splices the tool result and
triggers the follow-up run.

So: when the folded history ends in an unanswered `request_permission`, ChatPane
re-arms it on mount via `runTool` with the same permission payload. The user gets a
live, answerable card. The freshly minted toolCallId is why resume verification (next
section) keys on the permission's `request_id`, not the toolCallId.

## Hardening

Three trust gaps, all in the run loop, all closed here (taskwarrior `dbcb5569`):

1. **Truncated upstream stream → `RUN_ERROR`, not `RUN_FINISHED`.** If the upstream
   A2A stream ends without a terminal state (no result, no pending input),
   `RunTranslator.finish()` emits `RUN_ERROR` ("upstream stream ended without a
   terminal state") instead of closing the run as a success. The error is an `out`
   event like any other, so replay shows the truth.
2. **Verified resumes.** An allow/deny answer names the tool call it responds to; the
   service resolves that call from the run input's history, extracts the permission
   payload's `request_id` from its args, and matches it against the pending task's
   request. Mismatch → `RUN_ERROR`, pending state untouched, the real card still
   answerable. This closes the stale-tab case and works identically for original and
   re-armed cards (which differ in toolCallId but carry the same payload).
3. **`clear_pending()` evicts the cached A2A client.** `Conversations` caches one A2A
   client per context; clearing pending state drops the cached client too, so a wedged
   connection doesn't outlive the exchange that wedged it.

## Error handling

- Event-log writes ride the same SQLite connection and transaction discipline the
  store already uses; a write failure is a real failure (the turn errors as
  `RUN_ERROR`), not a silent skip — a log with holes is worse than a loud stop,
  because replay and recording both trust it.
- `/agui/connect` for an unknown thread answers as `/agui/run` does: `RUN_STARTED`
  then `RUN_ERROR` inside the stream, never a broken transport.
- A chat with no events folds to an empty snapshot — a new chat's connect is cheap
  and clean, not an error.

## Testing

Same doctrine as the 2026-08-12 spec: the rig is the reference producer, pytest pins
the seam.

- **Fold tests:** run scripted turns through the real plane against the playback rig,
  then fold the stored events and assert the message list — text, tool-call pairing,
  user lines interleaved, error markers, stable ids.
- **Replay tests:** hit `/agui/connect` after those turns; assert the
  `RUN_STARTED → MESSAGES_SNAPSHOT → RUN_FINISHED` shape and snapshot contents.
- **Restart tests:** reopen the store on the same SQLite file mid-scenario (fresh
  `Store` + `Conversations`); assert the pending task is found and a resume completes
  against the rig.
- **Hardening tests:** a truncating scenario asserts `RUN_ERROR` on early stream end;
  a mismatched `request_id` resume asserts refusal with pending state intact; client
  eviction asserts a new upstream connection after `clear_pending()`.
- **Browser validation:** manual pass against the rig (reload mid-conversation,
  reload with a pending approval, answer the re-armed card), GIF-documented like the
  2026-08-12 runs. The Playwright suite stays its own thread (`d798cf14`).

## Risks and deferrals

- **Library-version coupling.** The connect handshake, the snapshot merge semantics,
  and the `runTool` re-arm are pinned to `@copilotkit/react-core` 1.67.1 /
  `@ag-ui/client` 0.0.57 by source reading, not docs — the versions in the lockfile.
  An upgrade re-opens those three questions; the replay tests catch the server side,
  the browser validation pass catches the client side.
- **Log growth.** Full-fidelity deltas are verbose. Accepted: this is a dev cockpit,
  SQLite is fine at this scale, and the recording milestone wants the verbosity.
  Compaction is a problem for the day it's a problem.
- **Out-of-band turns remain invisible** (scoped out above). The log is the
  orchestrator's seam, not a2acode's memory. If upstream grows task listing, a
  backfill becomes possible; nothing here forecloses it.
