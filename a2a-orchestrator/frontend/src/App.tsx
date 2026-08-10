import { useEffect, useState } from 'react'
import {
  createMission,
  listMissions,
  listRepos,
  openChat,
  type ChatRef,
  type Mission,
  type RepoEntry,
} from './api'

export default function App() {
  const [missions, setMissions] = useState<Mission[]>([])
  const [repos, setRepos] = useState<RepoEntry[]>([])
  const [missionId, setMissionId] = useState<string | null>(null)
  const [chat, setChat] = useState<ChatRef | null>(null)
  const [repoChoice, setRepoChoice] = useState('')
  const [error, setError] = useState('')

  const refresh = () =>
    listMissions()
      .then((m) => {
        setMissions(m)
        setError('')
      })
      .catch((e) => setError(String(e)))

  useEffect(() => {
    refresh()
    listRepos()
      .then((entries) => {
        setRepos(entries)
        if (entries.length > 0) setRepoChoice(entries[0].name)
      })
      .catch((e) => setError(String(e)))
  }, [])

  const startMission = async () => {
    try {
      const created = await createMission()
      await refresh()
      setMissionId(created.id)
      setChat(null)
    } catch (e) {
      setError(String(e))
    }
  }

  const startChat = async () => {
    if (!missionId || !repoChoice) return
    try {
      const opened = await openChat(missionId, repoChoice)
      await refresh()
      setChat(opened)
    } catch (e) {
      setError(String(e))
    }
  }

  const mission = missions.find((m) => m.id === missionId) ?? null

  if (!mission) {
    return (
      <main>
        <h1>cockpit</h1>
        {error && <p className="error">{error}</p>}
        <button onClick={startMission}>New mission</button>
        <ul>
          {missions.map((m) => (
            <li key={m.id}>
              <a href="#" onClick={(e) => { e.preventDefault(); setMissionId(m.id) }}>
                {m.title}
              </a>{' '}
              — {m.chats.length} chat{m.chats.length === 1 ? '' : 's'}
            </li>
          ))}
        </ul>
      </main>
    )
  }

  return (
    <main>
      <h1>
        <a href="#" onClick={(e) => { e.preventDefault(); setMissionId(null); setChat(null) }}>
          cockpit
        </a>{' '}
        / {mission.title}
      </h1>
      {error && <p className="error">{error}</p>}
      <p>
        <select value={repoChoice} onChange={(e) => setRepoChoice(e.target.value)}>
          {repos.map((r) => (
            <option key={r.name} value={r.name}>{r.name}</option>
          ))}
        </select>{' '}
        <button onClick={startChat}>Open chat</button>
      </p>
      <ul>
        {mission.chats.map((c) => (
          <li key={c.context_id}>
            <a href="#" onClick={(e) => { e.preventDefault(); setChat(c) }}>
              {c.agent}
            </a>
          </li>
        ))}
      </ul>
      {chat && <p>chat bound: {chat.context_id}</p>}
    </main>
  )
}
