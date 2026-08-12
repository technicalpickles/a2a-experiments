// Management REST: typed wrappers over the service's /api endpoints.

export interface ChatRef {
  context_id: string
  mission_id: string
  agent: string
  created_at: string
}

export interface Mission {
  id: string
  title: string
  created_at: string
  chats: ChatRef[]
}

export interface RepoEntry {
  name: string
  description: string
}

async function json<T>(response: Response): Promise<T> {
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}: ${await response.text()}`)
  }
  return response.json() as Promise<T>
}

function post(url: string, body: unknown): Promise<Response> {
  return fetch(url, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export async function listMissions(): Promise<Mission[]> {
  const data = await json<{ missions: Mission[] }>(await fetch('/api/missions'))
  return data.missions
}

export async function createMission(title?: string): Promise<Mission> {
  return json<Mission>(await post('/api/missions', title ? { title } : {}))
}

export async function listRepos(): Promise<RepoEntry[]> {
  const data = await json<{ repos: RepoEntry[] }>(await fetch('/api/catalog'))
  return data.repos
}

export async function openChat(missionId: string, agent: string): Promise<ChatRef> {
  return json<ChatRef>(await post(`/api/missions/${missionId}/chats`, { agent }))
}
