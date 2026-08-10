// The conversation plane: a real a2a-js client per chat, talking through the
// service's contextId-routed proxy, distilled into renderable events.
//
// The approval payload rides the status message's metadata under
// `a2acode_permission` ({tool, request_id, input}) — metadata is a plain JS
// object in a2a-js, so no Struct decoding. The stream ends when a task parks
// in input_required; answering is a new message on the same taskId.

import { Role, TaskState, taskStateToJSON, type Part } from '@a2a-js/sdk'
import { ClientFactory, type Client } from '@a2a-js/sdk/client'

export interface Permission {
  tool: string
  request_id: string
  input: Record<string, unknown>
}

export type ChatEvent =
  | { kind: 'task'; taskId: string; contextId: string }
  | { kind: 'status'; state: string; text: string }
  | { kind: 'permission'; taskId: string; contextId: string; permission: Permission }
  | { kind: 'artifact-text'; text: string }

// createFromUrl resolves the card relative to its argument, so the trailing
// slash the service puts on a2a_url is load-bearing — without it the last
// path segment drops and the card fetch 404s.
export function connect(a2aUrl: string): Promise<Client> {
  const base = new URL(a2aUrl, window.location.origin).toString()
  return new ClientFactory().createFromUrl(base)
}

function textOf(parts: Part[] | undefined): string {
  return (parts ?? [])
    .map((part) => (part.content?.$case === 'text' ? part.content.value : ''))
    .join('')
}

function stateName(state: TaskState | undefined): string {
  if (state === undefined) return 'unknown'
  return taskStateToJSON(state).replace('TASK_STATE_', '').toLowerCase()
}

export async function* sendTurn(
  client: Client,
  text: string,
  ids: { contextId: string; taskId?: string },
): AsyncGenerator<ChatEvent> {
  const stream = client.sendMessageStream({
    tenant: '',
    configuration: undefined,
    metadata: undefined,
    message: {
      messageId: crypto.randomUUID(),
      contextId: ids.contextId,
      taskId: ids.taskId ?? '',
      role: Role.ROLE_USER,
      parts: [
        {
          content: { $case: 'text', value: text },
          metadata: undefined,
          filename: '',
          mediaType: '',
        },
      ],
      metadata: undefined,
      extensions: [],
      referenceTaskIds: [],
    },
  })
  for await (const response of stream) {
    const payload = response.payload
    if (!payload) continue
    if (payload.$case === 'task') {
      yield { kind: 'task', taskId: payload.value.id, contextId: payload.value.contextId }
    } else if (payload.$case === 'statusUpdate') {
      const { taskId, contextId, status } = payload.value
      const state = stateName(status?.state)
      const permission = status?.message?.metadata?.['a2acode_permission'] as
        | Permission
        | undefined
      if (state === 'input_required' && permission) {
        yield { kind: 'permission', taskId, contextId, permission }
      } else {
        yield { kind: 'status', state, text: textOf(status?.message?.parts) }
      }
    } else if (payload.$case === 'artifactUpdate') {
      yield { kind: 'artifact-text', text: textOf(payload.value.artifact?.parts) }
    }
  }
}
