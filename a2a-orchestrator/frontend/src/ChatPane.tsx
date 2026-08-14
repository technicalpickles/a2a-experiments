import { useEffect, useMemo, useRef, useState } from 'react'
import {
  CopilotChat,
  CopilotKitProvider,
  useCopilotKit,
  useHumanInTheLoop,
  type CopilotChatAssistantMessage,
  type CopilotChatInput,
  type CopilotChatUserMessage,
} from '@copilotkit/react-core/v2'
import { fetchPending, type ChatRef } from './api'
import { ApprovalCard, type Permission } from './ApprovalCard'
import { ReplayHttpAgent } from './agent'
import {
  ChatUiContext,
  PhosphorAssistantMessage,
  PhosphorComposer,
  PhosphorCursor,
  PhosphorUserMessage,
} from './chat-ui'

// request_permission is the one wire contract the cockpit mints (spec: Domain
// model): args are a2acode's permission payload verbatim, the result is
// {decision}. respond() resolves into a role:"tool" message and CopilotKit
// fires the follow-up run; the service resumes the pending task from it.
//
// Both 'executing' and 'complete' render the same ApprovalCard so the
// component instance (and its `sent` state) survives the status
// transition, instead of remounting into a stateless "answered" line that
// loses which request was answered and how.
function PermissionTool({
  repo,
  onPendingChange,
}: {
  repo: string
  onPendingChange: (pending: boolean) => void
}) {
  useHumanInTheLoop({
    name: 'request_permission',
    description: 'Ask the user to allow or deny a tool use',
    render: ({ args, status, respond }) => {
      if (status !== 'executing' && status !== 'complete') return <></>
      return (
        <ApprovalCard
          permission={args as unknown as Permission}
          repo={repo}
          status={status}
          onAnswer={(decision) => respond?.({ decision })}
          onPendingChange={onPendingChange}
        />
      )
    },
  })
  return null
}

// Replay paints a pending approval's text, but HITL status only goes
// live inside a run — a reloaded card renders inert (verified against
// 1.67.1: status derives from live tool execution, respond() is a no-op
// outside one). runTool() is the one supported re-arm: it fires the tool
// fresh (new toolCallId; the service reconciles by request_id) and
// followUp:'generate' carries the answer upstream as a normal resume.
function PendingRearm({
  contextId,
  agent,
  onError,
  onArmed,
}: {
  contextId: string
  agent: ReplayHttpAgent
  onError: (message: string) => void
  onArmed?: () => void
}) {
  const { copilotkit } = useCopilotKit()
  const armed = useRef(false)
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const pending = await fetchPending(contextId)
        if (!pending || cancelled || armed.current) return
        // Wait for the connect snapshot to land first: the snapshot merge
        // drops messages it doesn't know, so arming before it applies would
        // wipe the synthesized call. Pending implies history, so non-empty
        // messages means the snapshot arrived.
        for (let i = 0; i < 100 && agent.messages.length === 0 && !cancelled; i++) {
          await new Promise((resolve) => setTimeout(resolve, 100))
        }
        if (cancelled || armed.current) return
        if (agent.messages.length === 0) {
          // Loop exhausted without the snapshot landing: arming now would
          // fire runTool before connectAgent()'s merge has applied, which
          // wipes the synthesized call (see comment above). Surface it
          // instead of pretending the re-arm happened.
          onError('pending approval could not re-arm: connect snapshot did not arrive in time')
          return
        }
        armed.current = true
        await copilotkit.runTool({
          name: 'request_permission',
          agentId: contextId,
          parameters: pending,
          followUp: 'generate',
        })
        onArmed?.()
      } catch (err) {
        if (!cancelled) {
          const reason = err instanceof Error ? err.message : String(err)
          onError(`pending approval could not re-arm: ${reason}`)
        }
      }
    })()
    return () => {
      cancelled = true
    }
  }, [contextId, agent, copilotkit, onError, onArmed])
  return null
}

export function ChatPane({
  chat,
  onRemount,
}: {
  chat: ChatRef
  onRemount?: () => void
}) {
  // One HttpAgent per chat, registered under the chat's own key: registry
  // agents are singletons per key, so distinct chats must never share one
  // (same-key-different-threadId clobbers the shared instance's thread).
  const agents = useMemo(
    () => ({
      [chat.context_id]: new ReplayHttpAgent({
        url: '/agui/run',
        threadId: chat.context_id,
      }),
    }),
    [chat.context_id],
  )
  // CopilotChat swallows RUN_ERROR (AG-UI's failure event) into a console
  // log by default — no in-flow trace. onError is scoped to this chat's
  // agentId, so it only fires for runs this pane actually started. No cheap
  // hook clears it on the next run (see report), so the banner is sticky
  // until the chat is remounted.
  const [runError, setRunError] = useState('')
  // Tracks whether the live ApprovalCard has a pending (unanswered)
  // permission request, driven by ApprovalCard's onPendingChange. Gates the
  // composer via ChatUiContext (PhosphorComposer). useMemo keeps the context
  // value stable across renders so consumers don't re-render on every
  // ChatPane render.
  const [approvalPending, setApprovalPending] = useState(false)
  // Tracks whether a pending approval was re-armed via runTool.
  const [rearmed, setRearmed] = useState(false)
  const chatUiValue = useMemo(
    () => ({ repoName: chat.agent, approvalPending }),
    [chat.agent, approvalPending],
  )
  // The messageView slot props object is a literal in JSX; without this
  // memo, ChatPane re-renders (e.g. every setRunError) hand CopilotChat a
  // new object identity every time, defeating its slot memoization. The
  // referenced components are module-scope stable, so this has no deps.
  const messageView = useMemo(
    () => ({
      assistantMessage: PhosphorAssistantMessage as unknown as typeof CopilotChatAssistantMessage,
      userMessage: PhosphorUserMessage as unknown as typeof CopilotChatUserMessage,
      cursor: PhosphorCursor,
      className: 'gap-[18px]',
    }),
    [],
  )
  return (
    <section className="flex h-full min-h-0 flex-col">
      {runError && (
        <div className="flex items-center gap-2.5 border-b border-[oklch(0.40_0.14_340)] bg-[oklch(0.20_0.07_340)] px-4 py-2 text-[12px]">
          <span className="font-bold text-[oklch(0.80_0.20_340)]">ERR</span>
          <span className="min-w-0 flex-1 truncate">run failed: {runError}</span>
          {onRemount && (
            <button
              onClick={onRemount}
              className="shrink-0 cursor-pointer border-none bg-transparent text-[11px] text-[oklch(0.80_0.20_340)] hover:text-white"
            >
              ↻ remount
            </button>
          )}
        </div>
      )}
      {rearmed && (
        <p className="m-0 px-4 py-1 text-[11px] text-muted-foreground">
          {'// reload detected · pending approval re-armed via runTool()'}
        </p>
      )}
      <CopilotKitProvider agents__unsafe_dev_only={agents}>
        <PermissionTool repo={chat.agent} onPendingChange={setApprovalPending} />
        <PendingRearm
          contextId={chat.context_id}
          agent={agents[chat.context_id]}
          onError={setRunError}
          onArmed={() => setRearmed(true)}
        />
        <ChatUiContext.Provider value={chatUiValue}>
          <div className="mx-auto flex min-h-0 w-full max-w-[660px] flex-1 flex-col px-5">
            <CopilotChat
              agentId={chat.context_id}
              threadId={chat.context_id}
              className="h-full"
              // The messageView/input slot types are `SlotValue<typeof
              // CopilotChatXxx>`, and CopilotChatAssistantMessage/UserMessage/
              // Input are `declare function` + merged `declare namespace`
              // pairs (statics like `.MarkdownRenderer` attached) — so a
              // plain replacement component structurally fails the exact-type
              // branch of that union even though it satisfies the call
              // signature CopilotChat actually invokes. Cast rather than
              // reshape our components to fake those statics.
              messageView={messageView}
              input={PhosphorComposer as unknown as typeof CopilotChatInput}
              onError={(event) => {
                // CopilotChatProps["onError"] is typed as a union with the plain
                // DOM `onError` (HTMLAttributes<HTMLDivElement> carries one too,
                // for <img>/<video> children) — narrow to CopilotKit's own shape
                // before reading `.error`.
                if ('error' in event) setRunError(String(event.error?.message ?? event.error))
              }}
            />
          </div>
        </ChatUiContext.Provider>
      </CopilotKitProvider>
    </section>
  )
}
