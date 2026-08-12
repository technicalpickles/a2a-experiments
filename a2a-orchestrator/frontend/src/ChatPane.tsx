import { useMemo, useState } from 'react'
import {
  CopilotChat,
  CopilotKitProvider,
  HttpAgent,
  useHumanInTheLoop,
} from '@copilotkit/react-core/v2'
import '@copilotkit/react-core/v2/styles.css'
import type { ChatRef } from './api'
import { ApprovalCard, type Permission } from './ApprovalCard'

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

export function ChatPane({ chat }: { chat: ChatRef }) {
  // One HttpAgent per chat, registered under the chat's own key: registry
  // agents are singletons per key, so distinct chats must never share one
  // (same-key-different-threadId clobbers the shared instance's thread).
  const agents = useMemo(
    () => ({
      [chat.context_id]: new HttpAgent({
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
