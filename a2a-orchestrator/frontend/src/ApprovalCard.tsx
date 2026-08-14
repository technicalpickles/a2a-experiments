import { useEffect, useState } from 'react'
import { Button } from './components/ui/button'

export interface Permission {
  tool: string
  request_id: string
  input: Record<string, unknown>
  description?: string
}

export type Decision = 'allow' | 'deny'

export interface DecisionRecord {
  decision: Decision
  at: Date
}

// Which request_id was answered, how, and when. Owned by ChatPane (see the
// comment there) because this component does not survive the answer.
export type DecisionMemory = Map<string, DecisionRecord>

function shortId(id: string) {
  return id.replace(/-/g, '').slice(0, 6)
}

function clock(d: Date) {
  return d.toTimeString().slice(0, 8)
}

// The args block: `command` (if present) on the first line, description as a
// `# comment`, everything else pretty-printed.
function ArgsBlock({ permission }: { permission: Permission }) {
  const { command, description, ...rest } = permission.input as {
    command?: string
    description?: string
    [k: string]: unknown
  }
  const desc = permission.description ?? description
  const restKeys = Object.keys(rest)
  return (
    <pre className="m-0 overflow-x-auto whitespace-pre-wrap rounded-sm border border-dashed border-[oklch(0.32_0.04_150)] px-[13px] py-[11px] font-mono text-[12.5px] leading-[1.75] max-md:break-all">
      {typeof command === 'string' ? command + '\n' : null}
      {desc ? <span className="text-[oklch(0.60_0.03_150)]">{'# ' + desc + '\n'}</span> : null}
      {restKeys.length > 0 || typeof command !== 'string'
        ? JSON.stringify(typeof command === 'string' ? rest : permission.input, null, 2)
        : null}
    </pre>
  )
}

export function ApprovalCard({
  permission,
  repo,
  status,
  decisions,
  onAnswer,
  onPendingChange,
}: {
  permission: Permission
  repo: string
  status: 'executing' | 'complete'
  decisions: DecisionMemory
  onAnswer: (decision: Decision) => void
  onPendingChange: (pending: boolean) => void
}) {
  const [sent, setSent] = useState<DecisionRecord | null>(null)
  const answering = sent !== null && status === 'executing'
  const pending = status === 'executing'

  useEffect(() => {
    onPendingChange(pending)
    return () => onPendingChange(false)
  }, [pending, onPendingChange])

  const answer = (decision: Decision) => {
    setSent({ decision, at: new Date() })
    onAnswer(decision)
  }

  // Receipts stay in the transcript. `sent` covers the render right after
  // the click; ChatPane's decision memory covers everything after, because
  // this component gets torn down on the way to `complete` (see ChatPane).
  // Only a genuine reload — nothing in this session answered this
  // request_id — falls through to the neutral ANSWERED receipt.
  if (status === 'complete') {
    const record = sent ?? decisions.get(permission.request_id) ?? null
    const denied = record?.decision === 'deny'
    const meta = `${shortId(permission.request_id)}${record ? ' · ' + clock(record.at) : ''}`
    return (
      <div
        className={
          'phosphor-approval ' +
          (denied
            ? 'rounded-sm border border-[oklch(0.45_0.16_340)] bg-[#120a10] opacity-85'
            : 'rounded-sm border border-[oklch(0.32_0.04_150)] bg-[#0b0d0c] opacity-72')
        }
      >
        <div className="flex items-center justify-between px-4 pt-2.5 text-[11px] tracking-[.14em]">
          <span className={denied ? 'text-[oklch(0.82_0.14_340)]' : 'text-[oklch(0.70_0.02_150)]'}>
            {record ? (denied ? 'DENIED' : 'ALLOWED') : 'ANSWERED'}
          </span>
          <span className="tracking-normal text-muted-foreground">{meta}</span>
        </div>
        <div className="px-4 pb-3 pt-1 text-[12.5px]">
          <span className="font-bold">{permission.tool}</span>
          <span className="text-muted-foreground">
            {' — '}
            {typeof permission.input.command === 'string'
              ? permission.input.command
              : shortId(permission.request_id)}
          </span>
          {denied ? (
            <div className="text-[oklch(0.82_0.14_340)]">agent told: user denied this tool call</div>
          ) : null}
        </div>
      </div>
    )
  }

  return (
    <div
      className="phosphor-approval rounded-sm border border-primary bg-[#0b120d] max-md:-mx-5"
      ref={(el) => {
        // Take focus on mount so ⇥/⏎ reach ALLOW first. No bare-key
        // shortcuts — too consequential.
        if (el && !el.dataset.focused) {
          el.dataset.focused = 'true'
          el.querySelector('button')?.focus()
        }
      }}
    >
      <div className="flex items-center justify-between bg-primary px-4 py-1.5 text-primary-foreground">
        <span className="text-[11px] font-bold tracking-[.14em]">PERMISSION REQUEST</span>
        <span className="text-[11px] font-medium">{shortId(permission.request_id)}</span>
      </div>
      <div className="flex flex-col gap-3 px-4 py-3.5">
        <div className="text-[13.5px]">
          <span className="font-bold text-primary">{permission.tool}</span>
          <span className="text-muted-foreground"> — run command in </span>
          <span>{repo}</span>
        </div>
        <ArgsBlock permission={permission} />
        <div className="flex items-center gap-2.5 max-md:flex-col">
          <Button variant="primary" size="sm" className="max-md:h-11 max-md:w-full" disabled={answering} onClick={() => answer('allow')}>
            {answering && sent?.decision === 'allow' ? (
              <>
                SENDING
                <span className="inline-block size-1.5 animate-cockpit-blink bg-[oklch(0.40_0.10_150)]" aria-hidden />
              </>
            ) : (
              'ALLOW'
            )}
          </Button>
          <Button variant="deny" size="sm" className="max-md:h-11 max-md:w-full" disabled={answering} onClick={() => answer('deny')}>
            DENY
          </Button>
          <span className="ml-auto text-[11px] text-muted-foreground max-md:ml-0">task: input-required</span>
        </div>
      </div>
    </div>
  )
}
