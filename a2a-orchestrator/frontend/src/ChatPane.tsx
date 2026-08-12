import { useMemo } from 'react'
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
  return (
    <section>
      <h2>{chat.agent}</h2>
      <CopilotKitProvider agents__unsafe_dev_only={agents}>
        <PermissionTool />
        <CopilotChat agentId={chat.context_id} threadId={chat.context_id} />
      </CopilotKitProvider>
    </section>
  )
}
