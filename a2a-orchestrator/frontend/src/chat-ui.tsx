import { createContext, useContext, useState, type FormEvent } from 'react'
import {
  CopilotChatAssistantMessage,
  CopilotChatToolCallsView,
  type CopilotChatAssistantMessageProps,
  type CopilotChatInputProps,
  type CopilotChatUserMessageProps,
  type UserMessage,
} from '@copilotkit/react-core/v2'

export const ChatUiContext = createContext<{ repoName: string; approvalPending: boolean }>({
  repoName: '',
  approvalPending: false,
})

// UserMessage.content is `string | ContentPart[]` (multimodal) per the
// installed @ag-ui/core schema, not the plain `string | undefined` the brief
// assumed — this app never enables attachments, so text parts are all we
// expect, but render defensively rather than crash on a non-text message.
function userMessageText(content: UserMessage['content']): string {
  if (typeof content === 'string') return content
  if (!content) return ''
  return content
    .filter((part): part is { type: 'text'; text: string } => part.type === 'text')
    .map((part) => part.text)
    .join('')
}

export function PhosphorUserMessage({ message }: CopilotChatUserMessageProps) {
  return (
    <div className="flex gap-2 text-[13px] leading-[1.65]">
      <span className="select-none text-[oklch(0.66_0.03_150)]">you&gt;</span>
      <div className="min-w-0 whitespace-pre-wrap">{userMessageText(message.content)}</div>
    </div>
  )
}

export function PhosphorAssistantMessage({
  message,
  messages,
}: CopilotChatAssistantMessageProps) {
  // No inline caret here: CopilotChatMessageView already renders the cursor
  // slot (PhosphorCursor) whenever a run is streaming, and the memoized
  // assistant-message comparator never compares `messages`, so a message
  // that stops being last never re-renders — an inline caret computed from
  // `isLast` would stick forever once superseded. The cursor slot owns the
  // caret exclusively.
  return (
    <div className="flex gap-2 text-[13px] leading-[1.7]">
      <span className="select-none text-primary">cc&gt;</span>
      <div className="phosphor-assistant-body min-w-0 flex-1">
        {message.content ? (
          <CopilotChatAssistantMessage.MarkdownRenderer content={message.content} />
        ) : null}
        <div className="phosphor-tool-calls">
          <CopilotChatToolCallsView message={message} messages={messages} />
        </div>
      </div>
    </div>
  )
}

export function PhosphorCursor() {
  return <span className="caret" aria-hidden />
}

export function PhosphorComposer({ onSubmitMessage, isRunning }: CopilotChatInputProps) {
  const { repoName, approvalPending } = useContext(ChatUiContext)
  const [draft, setDraft] = useState('')
  const submit = (e: FormEvent) => {
    e.preventDefault()
    const value = draft.trim()
    if (!value || approvalPending) return
    onSubmitMessage?.(value)
    setDraft('')
  }
  if (approvalPending) {
    return (
      <div className="pointer-events-auto flex items-center gap-2 rounded-sm border border-dashed border-border bg-[#0a0b0a] px-3.5 py-3 text-[12px] text-muted-foreground">
        answer the permission request to continue
      </div>
    )
  }
  return (
    <form
      onSubmit={submit}
      // CopilotChatView renders the input slot inside an overlay it marks
      // pointer-events-none; the stock input re-enables on its own wrapper.
      // Without this, clicks on the field and the send button are dead —
      // only Tab-focus reaches them.
      className="group pointer-events-auto flex items-center gap-2 rounded-sm border border-border bg-card px-3.5 py-3 transition-colors duration-[120ms] focus-within:border-primary"
    >
      <span className="select-none text-muted-foreground group-focus-within:text-primary">&gt;</span>
      <input
        className="min-w-0 flex-1 border-none bg-transparent text-[13px] outline-none caret-primary placeholder:text-muted-foreground"
        placeholder={repoName ? `message ${repoName}` : 'message'}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        disabled={isRunning}
      />
      <button
        type="submit"
        className="cursor-pointer select-none border-none bg-transparent text-[11px] text-muted-foreground hover:text-primary"
      >
        ⏎ send
      </button>
    </form>
  )
}
