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
import { ChatPane } from './ChatPane'
import { RepoPicker } from './RepoPicker'
import { Button } from './components/ui/button'
import { cn } from './lib/utils'

type Theme = 'dark' | 'light'

function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(() =>
    document.documentElement.classList.contains('dark') ? 'dark' : 'light',
  )
  const toggle = () =>
    setTheme((t) => {
      const next: Theme = t === 'dark' ? 'light' : 'dark'
      localStorage.theme = next
      document.documentElement.classList.toggle('dark', next === 'dark')
      return next
    })
  return [theme, toggle]
}

function timeAgo(iso: string): string {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
  if (s < 60) return 'now'
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}

function Wordmark() {
  return (
    <span className="text-[13px] font-bold tracking-[.08em]">
      COCKPIT<span className="text-primary">_</span>
    </span>
  )
}

function ThemeToggle({ theme, onToggle }: { theme: Theme; onToggle: () => void }) {
  return (
    <Button variant="ghost" size="sm" onClick={onToggle}>
      {theme === 'dark' ? '[DARK]' : '[LIGHT]'}
    </Button>
  )
}

function ErrorStrip({
  error,
  onDismiss,
}: {
  error: string
  onDismiss?: () => void
}) {
  if (!error) return null
  return (
    <div className="flex items-center gap-2.5 border-b border-[oklch(0.40_0.14_340)] bg-[oklch(0.20_0.07_340)] px-4 py-2 text-[12px]">
      <span className="font-bold text-[oklch(0.80_0.20_340)]">ERR</span>
      <span className="min-w-0 flex-1 truncate">{error}</span>
      {onDismiss && (
        <button
          onClick={onDismiss}
          className="shrink-0 cursor-pointer border-none bg-transparent text-[11px] text-[oklch(0.80_0.20_340)] hover:text-white"
        >
          ✕ dismiss
        </button>
      )}
    </div>
  )
}

export default function App() {
  const [missions, setMissions] = useState<Mission[]>([])
  const [repos, setRepos] = useState<RepoEntry[]>([])
  const [missionId, setMissionId] = useState<string | null>(null)
  const [chat, setChat] = useState<ChatRef | null>(null)
  const [repoChoice, setRepoChoice] = useState('')
  const [error, setError] = useState('')
  const [chatNonce, setChatNonce] = useState(0)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [theme, toggleTheme] = useTheme()

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
      <main className="flex h-dvh flex-col overflow-hidden">
        <header className="flex h-[52px] shrink-0 items-center justify-between border-b border-divider px-5">
          <Wordmark />
          <ThemeToggle theme={theme} onToggle={toggleTheme} />
        </header>

        <ErrorStrip error={error} onDismiss={() => setError('')} />

        <div className="flex items-baseline gap-3 px-5 pt-4 pb-2">
          <span className="text-[10.5px] tracking-[.16em] text-muted-foreground">MISSIONS/</span>
          <span className="h-px flex-1 self-center bg-divider-soft" />
          <Button variant="ghost" size="sm" className="text-primary" onClick={startMission}>
            [+] NEW
          </Button>
        </div>

        {missions.length === 0 ? (
          <div className="flex flex-col items-center gap-3 px-5 pt-[120px] text-center">
            <span className="text-[12px] tracking-[.14em] text-muted-foreground">
              NO MISSIONS YET
            </span>
            <p className="text-muted-foreground text-[12.5px] leading-[1.7] dark:text-[oklch(0.76_0.02_150)]">
              A mission is one unit of work. Create one, pick a repo,
              <br />
              then drive the agent from the chat pane.
            </p>
            <Button variant="primary" size="md" onClick={startMission}>
              [+] NEW MISSION
            </Button>
          </div>
        ) : (
          <ul className="flex-1 overflow-y-auto">
            {missions.map((m) => (
              <li key={m.id} className="border-b border-divider-soft">
                <button
                  type="button"
                  onClick={() => setMissionId(m.id)}
                  className="flex min-h-10 w-full items-center gap-2.5 px-5 text-left hover:bg-accent focus-visible:-outline-offset-1 max-md:min-h-11"
                >
                  <span
                    className={
                      m.id === missionId ? 'text-primary' : 'text-[oklch(0.50_0.03_150)]'
                    }
                  >
                    ▸
                  </span>
                  <span className="flex-1 text-[12.5px] max-md:text-[13px]">{m.title}</span>
                  <span className="text-[11px] text-muted-foreground">
                    {m.chats.length} chat{m.chats.length === 1 ? '' : 's'} · {timeAgo(m.created_at)}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}

        <footer className="px-5 py-2.5 text-[10.5px] text-muted-foreground">
          mission list · {missions.length} missions
        </footer>
      </main>
    )
  }

  return (
    <div className="grid h-dvh grid-cols-[248px_1fr] overflow-hidden max-md:grid-cols-1">
      {drawerOpen && (
        <div
          className="fixed inset-0 z-30 bg-[rgba(4,8,5,.72)] md:hidden"
          onClick={() => setDrawerOpen(false)}
        />
      )}

      <aside
        className={cn(
          'flex flex-col overflow-y-auto border-r border-divider bg-card p-4',
          'max-md:fixed max-md:inset-y-0 max-md:left-0 max-md:z-40 max-md:w-[300px] max-md:transition-transform max-md:duration-[120ms]',
          drawerOpen ? 'max-md:translate-x-0' : 'max-md:-translate-x-full',
        )}
      >
        <div className="flex items-center justify-between">
          <button
            type="button"
            onClick={() => {
              setMissionId(null)
              setChat(null)
              setDrawerOpen(false)
            }}
          >
            <Wordmark />
          </button>
          <ThemeToggle theme={theme} onToggle={toggleTheme} />
        </div>

        <div className="flex items-baseline gap-3 pt-4 pb-2">
          <span className="text-[10.5px] tracking-[.16em] text-muted-foreground">MISSIONS/</span>
          <span className="h-px flex-1 self-center bg-divider-soft" />
        </div>

        <ul className="flex flex-col gap-0.5">
          {missions.map((m) => {
            const selected = m.id === missionId
            return (
              <li key={m.id}>
                <button
                  type="button"
                  onClick={() => {
                    setMissionId(m.id)
                    setChat(null)
                    setDrawerOpen(false)
                  }}
                  className={cn(
                    'w-full rounded-sm px-2 py-1.5 text-left text-[12.5px] focus-visible:-outline-offset-1',
                    selected
                      ? 'border-y border-[oklch(0.34_0.06_150)] bg-muted font-medium'
                      : 'hover:bg-accent',
                  )}
                >
                  {m.title}
                </button>
                {selected && m.chats.length > 0 && (
                  <ul className="flex flex-col gap-0.5 pl-[30px]">
                    {m.chats.map((c) => (
                      <li key={c.context_id}>
                        <button
                          type="button"
                          onClick={() => {
                            setChat(c)
                            setDrawerOpen(false)
                          }}
                          className="w-full rounded-sm py-1 text-left text-[12px] text-primary hover:bg-accent focus-visible:-outline-offset-1"
                        >
                          {c.agent}
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            )
          })}
        </ul>

        <div className="mt-auto pt-4">
          <Button variant="ghost" size="sm" className="text-primary" onClick={startMission}>
            [+] NEW MISSION
          </Button>
        </div>
      </aside>

      <section className="flex min-w-0 flex-col overflow-hidden">
        <header className="flex h-[52px] shrink-0 items-center justify-between gap-3 border-b border-divider px-5">
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => setDrawerOpen(true)}
              className="flex h-11 w-11 flex-col items-center justify-center gap-[5px] md:hidden"
              aria-label="Open menu"
            >
              <div className="h-[1.5px] w-4 bg-foreground" />
              <div className="h-[1.5px] w-4 bg-foreground" />
            </button>
            <span className="text-[12.5px]">
              <span className="text-muted-foreground">cockpit /</span>{' '}
              <span className="font-bold">{mission.title}</span>
            </span>
          </div>
          <div className="flex items-center gap-2">
            <RepoPicker repos={repos} value={repoChoice} onChange={setRepoChoice} />
            <Button
              variant={repoChoice ? 'primary' : 'secondary'}
              size="md"
              disabled={!repoChoice}
              onClick={startChat}
            >
              OPEN CHAT
            </Button>
          </div>
        </header>

        <ErrorStrip error={error} onDismiss={() => setError('')} />

        {chat ? (
          <div className="min-h-0 flex-1">
            <ChatPane
              chat={chat}
              key={`${chat.context_id}:${chatNonce}`}
              onRemount={() => setChatNonce((n) => n + 1)}
            />
          </div>
        ) : (
          <div className="flex flex-1 flex-col items-center justify-center gap-3 px-5 text-center">
            <span className="text-[12px] tracking-[.14em] text-muted-foreground">
              NO CHAT OPEN
            </span>
            <p className="text-muted-foreground text-[12.5px] leading-[1.7] dark:text-[oklch(0.74_0.02_150)]">
              Pick a repo and open a chat — the agent card is fetched
              <br />
              when the chat is created, not before.
            </p>
          </div>
        )}
      </section>
    </div>
  )
}
