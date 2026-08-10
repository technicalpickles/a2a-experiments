import { useEffect, useRef, useState } from 'react'
import type { Client } from '@a2a-js/sdk/client'
import type { ChatRef } from './api'
import { connect, sendTurn, type Permission } from './a2a'
import { ApprovalCard } from './ApprovalCard'

interface LogItem {
  who: 'you' | 'agent' | 'system'
  text: string
}

interface PendingApproval {
  taskId: string
  permission: Permission
}

export function ChatPane({ chat }: { chat: ChatRef }) {
  const clientRef = useRef<Promise<Client> | null>(null)
  const [log, setLog] = useState<LogItem[]>([])
  const [approval, setApproval] = useState<PendingApproval | null>(null)
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)

  if (clientRef.current === null) clientRef.current = connect(chat.a2a_url)

  const aliveRef = useRef(true)
  useEffect(() => {
    aliveRef.current = true
    return () => { aliveRef.current = false }
  }, [])

  const append = (item: LogItem) => setLog((prev) => [...prev, item])

  // One turn: send, then drain the stream. The stream ends on terminal
  // states and on input_required alike, so this always returns; a parked
  // approval is left in state for the card to answer as its own turn.
  const runTurn = async (text: string, taskId?: string) => {
    setBusy(true)
    try {
      const client = await clientRef.current!
      const turn = sendTurn(client, text, { contextId: chat.context_id, taskId })
      for await (const event of turn) {
        if (!aliveRef.current) break
        if (event.kind === 'artifact-text' && event.text) {
          append({ who: 'agent', text: event.text })
        } else if (event.kind === 'permission') {
          setApproval({ taskId: event.taskId, permission: event.permission })
        } else if (event.kind === 'status') {
          append({
            who: 'system',
            text: event.text ? `${event.state} — ${event.text}` : event.state,
          })
        }
      }
    } catch (error) {
      append({ who: 'system', text: `error: ${String(error)}` })
    } finally {
      if (aliveRef.current) setBusy(false)
    }
  }

  const sendDraft = async () => {
    const text = draft.trim()
    if (!text) return
    append({ who: 'you', text })
    setDraft('')
    await runTurn(text)
  }

  const answer = async (decision: 'allow' | 'deny') => {
    if (!approval) return
    const parked = approval
    setApproval(null)
    append({ who: 'you', text: decision })
    await runTurn(decision, parked.taskId)
  }

  return (
    <section>
      <h2>{chat.agent}</h2>
      <ol className="log">
        {log.map((item, i) => (
          <li key={i} className={item.who}>
            <b>{item.who}</b> {item.text}
          </li>
        ))}
      </ol>
      {approval && (
        <ApprovalCard permission={approval.permission} onAnswer={answer} />
      )}
      <form
        onSubmit={(e) => {
          e.preventDefault()
          sendDraft()
        }}
      >
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          disabled={busy || approval !== null}
          placeholder={`Message ${chat.agent}`}
          size={60}
        />
        <button disabled={busy || approval !== null}>Send</button>
      </form>
    </section>
  )
}
