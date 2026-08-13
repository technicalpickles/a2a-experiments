import { useEffect, useMemo, useRef, useState } from 'react'
import {
  CopilotChat,
  CopilotKitProvider,
  useCopilotKit,
  useHumanInTheLoop,
} from '@copilotkit/react-core/v2'
import '@copilotkit/react-core/v2/styles.css'
import { fetchPending, type ChatRef } from './api'
import { ApprovalCard, type Permission } from './ApprovalCard'
import { ReplayHttpAgent } from './agent'

// request_permission is the one wire contract the cockpit mints (spec: Domain
// model): args are a2acode's permission payload verbatim, the result is
// {decision}. respond() resolves into a role:"tool" message and CopilotKit
// fires the follow-up run; the service resumes the parked task from it.
function PermissionTool() {
  useHumanInTheLoop({
    name: 'request_permission',
    description: 'Ask the user to allow or deny a tool use',
    render: ({ args, status, respond }) => {
      if (status === 'complete') return <p className="approval-done">answered</p>
      if (status !== 'executing') return <></>
      return (
        <ApprovalCard
          permission={args as unknown as Permission}
          onAnswer={(decision) => respond?.({ decision })}
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
function PendingRearm({ contextId, agent }: { contextId: string; agent: ReplayHttpAgent }) {
  const { copilotkit } = useCopilotKit()
  const armed = useRef(false)
  useEffect(() => {
    let cancelled = false
    ;(async () => {
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
      armed.current = true
      await copilotkit.runTool({
        name: 'request_permission',
        agentId: contextId,
        parameters: pending,
        followUp: 'generate',
      })
    })()
    return () => {
      cancelled = true
    }
  }, [contextId, agent, copilotkit])
  return null
}

export function ChatPane({ chat }: { chat: ChatRef }) {
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
  return (
    <section>
      <h2>{chat.agent}</h2>
      {runError && <p className="error">run failed: {runError}</p>}
      <CopilotKitProvider agents__unsafe_dev_only={agents}>
        <PermissionTool />
        <PendingRearm contextId={chat.context_id} agent={agents[chat.context_id]} />
        <CopilotChat
          agentId={chat.context_id}
          threadId={chat.context_id}
          onError={(event) => {
            // CopilotChatProps["onError"] is typed as a union with the plain
            // DOM `onError` (HTMLAttributes<HTMLDivElement> carries one too,
            // for <img>/<video> children) — narrow to CopilotKit's own shape
            // before reading `.error`.
            if ('error' in event) setRunError(String(event.error?.message ?? event.error))
          }}
        />
      </CopilotKitProvider>
    </section>
  )
}
