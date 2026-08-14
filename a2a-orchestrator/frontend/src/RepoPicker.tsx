import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import type { RepoEntry } from './api'
import { cn } from './lib/utils'

export function RepoPicker({
  repos,
  value,
  onChange,
}: {
  repos: RepoEntry[]
  value: string
  onChange: (name: string) => void
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [activeIndex, setActiveIndex] = useState(0)
  const rootRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    setActiveIndex(0)
  }, [query])

  useEffect(() => {
    if (!open) return
    const onPointerDown = (e: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('pointerdown', onPointerDown)
    return () => document.removeEventListener('pointerdown', onPointerDown)
  }, [open])

  const matches = repos.filter((r) =>
    (r.name + ' ' + r.description).toLowerCase().includes(query.toLowerCase()),
  )

  const openPicker = (initialQuery = '') => {
    setOpen(true)
    setQuery(initialQuery)
  }

  const select = (name: string) => {
    onChange(name)
    setOpen(false)
    setQuery('')
  }

  const onTriggerKeyDown = (e: KeyboardEvent<HTMLButtonElement>) => {
    if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
      e.preventDefault()
      openPicker(e.key)
    }
  }

  const onInputKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActiveIndex((i) => (matches.length === 0 ? 0 : (i + 1) % matches.length))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActiveIndex((i) =>
        matches.length === 0 ? 0 : (i - 1 + matches.length) % matches.length,
      )
    } else if (e.key === 'Enter') {
      e.preventDefault()
      const match = matches[activeIndex]
      if (match) select(match.name)
    } else if (e.key === 'Escape') {
      e.preventDefault()
      setOpen(false)
    }
  }

  return (
    <div
      ref={rootRef}
      role="combobox"
      aria-expanded={open}
      aria-haspopup="listbox"
      className="relative"
    >
      {open ? (
        <div className="flex h-8 items-center gap-1.5 rounded-sm border border-ring px-2.5 text-[12px]">
          <span className="text-muted-foreground">repo:</span>
          <input
            autoFocus
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onInputKeyDown}
            aria-activedescendant={matches[activeIndex] ? `repo-opt-${activeIndex}` : undefined}
            className="caret-primary w-[12ch] bg-transparent outline-none"
          />
          <span aria-hidden>▴</span>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => openPicker()}
          onKeyDown={onTriggerKeyDown}
          className="flex h-8 items-center gap-1.5 rounded-sm border border-border px-2.5 text-[12px]"
        >
          <span className="text-muted-foreground">repo:</span>
          <span className="max-w-[9ch] truncate md:max-w-none">{value || 'select'}</span>
          <span aria-hidden>▾</span>
        </button>
      )}

      {open && (
        <div
          role="listbox"
          className="absolute right-0 top-full z-50 w-[320px] border border-ring border-t-0 bg-popover [box-shadow:0_24px_48px_-20px_rgba(0,0,0,.85)]"
        >
          {matches.length === 0 ? (
            <div className="px-3 py-2 text-[11px] text-muted-foreground">no repos match</div>
          ) : (
            matches.map((r, i) => (
              <div
                key={r.name}
                id={`repo-opt-${i}`}
                role="option"
                aria-selected={i === activeIndex}
                onMouseEnter={() => setActiveIndex(i)}
                onClick={() => select(r.name)}
                className={cn(
                  'cursor-pointer border-l-2 px-3 py-2',
                  i === activeIndex ? 'bg-muted border-l-primary' : 'border-l-transparent',
                )}
              >
                <div className="text-[12px]">{r.name}</div>
                <div className="text-muted-foreground dark:text-[oklch(0.68_0.03_150)] text-[10.5px]">
                  {r.description}
                </div>
              </div>
            ))
          )}
          <div className="flex justify-between border-t border-divider-soft px-3 py-1.5 text-[10px] text-muted-foreground">
            <span>↑↓ move · ⏎ select · esc close</span>
            <span>{`${matches.length} of ${repos.length} match`}</span>
          </div>
        </div>
      )}
    </div>
  )
}
