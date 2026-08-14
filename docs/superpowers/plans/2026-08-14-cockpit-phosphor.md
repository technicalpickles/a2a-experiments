# Cockpit "Phosphor" Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recreate the "Phosphor" design (dark-first, all-mono, phosphor-green) across cockpit's shell and CopilotKit chat pane, per the design handoff in `~/Downloads/design_handoff_cockpit_phosphor/`.

**Architecture:** Tailwind v4 (`@tailwindcss/vite`) with the Phosphor palette declared as CSS custom properties at `:root`/`[data-copilotkit]` (dark under `.dark`), mapped into Tailwind via `@theme inline`. The chat pane reskins through two mechanisms: token override at `[data-copilotkit]` (beats CopilotKit's own `:root`-scoped defaults), and CopilotKit v2's slot system (`messageView.userMessage`, `messageView.assistantMessage`, `messageView.cursor`, `input`) for the structural changes (prefix-style messages, custom composer). shadcn-style `components/ui/button.tsx` via `class-variance-authority`; the repo picker is a hand-rolled listbox (the design's trigger-as-input combobox doesn't match cmdk's panel-input pattern, so cmdk/radix are deliberately not used).

**Tech Stack:** React 19, TypeScript, Vite 8, Tailwind v4, class-variance-authority + clsx + tailwind-merge, @fontsource/jetbrains-mono, CopilotKit `@copilotkit/react-core@1.67.1` (v2 API).

## Global Constraints

- Design authority: `~/Downloads/design_handoff_cockpit_phosphor/README.md`. **High-fidelity: exact colors, sizes, tracking, spacing from that README.** Where a mock and the README disagree, the README wins. Key values are copied into tasks below; consult the README section named in each task when in doubt.
- All work happens in `a2a-orchestrator/frontend/` inside the worktree at `~/worktrees/a2a-experiments/cockpit-phosphor`.
- **Do not touch** `src/api.ts` or `src/agent.ts`. **Do not restructure** `PendingRearm` in `ChatPane.tsx` (its ordering constraints are load-bearing and commented in place; adding a callback invocation is OK, reordering its awaits is not).
- No new dependencies beyond those already added to `package.json` (tailwindcss, @tailwindcss/vite, class-variance-authority, clsx, tailwind-merge, @fontsource/jetbrains-mono). They are installed in the worktree already.
- No router, no state library. Local `useState` only.
- Verification per task: `npm run build` (tsc + vite) and `npm run lint` (oxlint) from `a2a-orchestrator/frontend/`, both clean. There is no test harness in this package and this plan does not add one; final visual acceptance is a separate task driven by the orchestrator against the design mocks.
- Type: JetBrains Mono only, weights 400/500/700. No sans anywhere.
- Radius 2px everywhere (`--radius: 0.125rem`); no radius inside cards (dividers do the work).
- Motion: `background-color`/`border-color`/`color` transitions at 120ms linear only. Blinks are `steps(1)`. Nothing moves on hover, no transforms, no hover shadows.
- Focus: one rule globally — `outline: 1px solid var(--ring); outline-offset: 2px` on `:focus-visible` (already in the base CSS; don't add per-component rings). Sidebar rows use `outline-offset: -1px`.
- Text glyphs, not icons: `▸ ▾ ▴ ✕ ⏎ ↑↓ ↻ >`. The mobile drawer toggle is two 1.5px-tall divs.
- Commit after each task, on branch `cockpit-phosphor`. Commit messages reference the task slug. Include the standard trailers (this is Josh's own repo):
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>` and the Claude-Session URL from the session context.

**Known data gaps (deliberate deviations, do not "fix" by touching the API):**

- `RepoEntry` has no reachability field → all repo rows render selectable; skip the dimmed "unreachable" state.
- `ChatRef` has no status field → nested chat rows omit the `·paused`/`·running` suffix.
- The block-style typing caret in *editable inputs* (repo filter, composer) uses native `caret-color: var(--primary)` instead of a simulated block caret (a real input can't render a fake block caret without hiding the real one). The *streaming* caret in assistant messages is the real 7×14px block.

---

### tokens-theme-wiring

**Files:**
- Modify: `a2a-orchestrator/frontend/vite.config.ts`
- Modify: `a2a-orchestrator/frontend/index.html`
- Modify: `a2a-orchestrator/frontend/src/main.tsx`
- Modify: `a2a-orchestrator/frontend/src/index.css` (full replacement)
- Modify: `a2a-orchestrator/frontend/src/ChatPane.tsx` (remove one import line only)

**Interfaces:**
- Produces: Tailwind utilities backed by tokens (`bg-background`, `bg-card`, `text-primary`, `text-muted-foreground`, `border-border`, `border-divider`, `border-divider-soft`, `bg-primary-hover`, `bg-primary-active`, `ring`, `font-mono`, flat 2px radius scale), the `.caret` class, and the `cockpit-blink` animation (`animate-cockpit-blink`). Dark mode = `.dark` class on `<html>`, toggled pre-paint.

- [ ] **Step 1: Wire the Tailwind plugin**

Replace `vite.config.ts` content:

```ts
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// The dev server proxies to the service so the browser sees one origin.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:9300',
      '/agui': 'http://127.0.0.1:9300',
    },
  },
})
```

- [ ] **Step 2: Pre-paint theme bootstrap in `index.html`**

Add this script inside `<head>`, after the `<title>` (it must run before the module script so the first paint has the right theme):

```html
    <script>
      const t =
        localStorage.theme ??
        (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
      document.documentElement.classList.toggle('dark', t !== 'light')
    </script>
```

- [ ] **Step 3: Import order in `main.tsx`**

CopilotKit's stylesheet must load before ours so our `[data-copilotkit]` token override wins by order as well as specificity. Font weights come from @fontsource (no Google Fonts request). Replace the import block:

```tsx
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import '@fontsource/jetbrains-mono/400.css'
import '@fontsource/jetbrains-mono/500.css'
import '@fontsource/jetbrains-mono/700.css'
import '@copilotkit/react-core/v2/styles.css'
import './index.css'
import App from './App.tsx'
```

Then delete the `import '@copilotkit/react-core/v2/styles.css'` line from `ChatPane.tsx` (only that line).

- [ ] **Step 4: Replace `src/index.css`**

Full replacement with the content below. This is the handoff's `tokens.css` merged with the Tailwind v4 scaffolding (`@import`, `@custom-variant dark`, `@theme inline`):

```css
/* cockpit — "Phosphor" tokens + Tailwind v4.

   Every custom-property name here is one CopilotKit v2 already defines in its
   shipped globals.css (a Tailwind v4 build of the shadcn set). It scopes them
   to [data-copilotkit], so we redeclare at the same selector — higher
   specificity than :root — and the chat pane reskins with no component
   overrides. Import order in main.tsx: CopilotKit's styles.css first, this
   file after. Dark is the default; .dark lands on <html> pre-paint. */

@import 'tailwindcss';

@custom-variant dark (&:is(.dark *));

:root,
[data-copilotkit] {
  --background: oklch(0.97 0.005 150);
  --foreground: oklch(0.26 0.02 150);
  --card: oklch(0.94 0.008 150);
  --card-foreground: oklch(0.26 0.02 150);
  --popover: oklch(1 0 0);
  --popover-foreground: oklch(0.26 0.02 150);
  --primary: oklch(0.55 0.16 145);
  --primary-foreground: oklch(0.99 0.02 145);
  --secondary: oklch(0.94 0.008 150);
  --secondary-foreground: oklch(0.26 0.02 150);
  --muted: oklch(0.92 0.06 150);
  --muted-foreground: oklch(0.52 0.03 150);
  --accent: oklch(0.95 0.02 150);
  --accent-foreground: oklch(0.26 0.02 150);
  --destructive: oklch(0.5 0.2 340);
  --destructive-foreground: oklch(0.45 0.2 340);
  --border: oklch(0.87 0.02 150);
  --input: oklch(0.87 0.02 150);
  --ring: oklch(0.55 0.16 145);
  --radius: 0.125rem;

  /* app-local, no CopilotKit equivalent */
  --divider: oklch(0.88 0.02 150);
  --divider-soft: oklch(0.92 0.015 150);
  --primary-hover: oklch(0.6 0.17 145);
  --primary-active: oklch(0.48 0.15 145);
  --font-mono: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace;

  color-scheme: light;
}

.dark,
.dark [data-copilotkit] {
  --background: oklch(0.13 0.005 150);
  --foreground: oklch(0.93 0.02 150);
  --card: oklch(0.16 0.008 150);
  --card-foreground: oklch(0.93 0.02 150);
  --popover: oklch(0.16 0.008 150);
  --popover-foreground: oklch(0.93 0.02 150);
  --primary: oklch(0.88 0.19 145);
  --primary-foreground: oklch(0.2 0.06 145);
  --secondary: oklch(0.22 0.05 150);
  --secondary-foreground: oklch(0.93 0.02 150);
  --muted: oklch(0.22 0.05 150);
  --muted-foreground: oklch(0.6 0.03 150);
  --accent: oklch(0.18 0.02 150);
  --accent-foreground: oklch(0.93 0.02 150);
  --destructive: oklch(0.62 0.22 340);
  --destructive-foreground: oklch(0.9 0.05 340);
  --border: oklch(0.3 0.03 150);
  --input: oklch(0.3 0.03 150);
  --ring: oklch(0.88 0.19 145);

  --divider: oklch(0.24 0.03 150);
  --divider-soft: oklch(0.18 0.02 150);
  --primary-hover: oklch(0.93 0.19 145);
  --primary-active: oklch(0.79 0.17 145);

  color-scheme: dark;
}

/* Map the palette into Tailwind's theme so utilities resolve to the live
   custom properties. The radius scale is pinned flat: Phosphor is 2px
   everywhere, no ramp. font-sans is remapped to mono — no sans in Phosphor. */
@theme inline {
  --color-background: var(--background);
  --color-foreground: var(--foreground);
  --color-card: var(--card);
  --color-card-foreground: var(--card-foreground);
  --color-popover: var(--popover);
  --color-popover-foreground: var(--popover-foreground);
  --color-primary: var(--primary);
  --color-primary-foreground: var(--primary-foreground);
  --color-secondary: var(--secondary);
  --color-secondary-foreground: var(--secondary-foreground);
  --color-muted: var(--muted);
  --color-muted-foreground: var(--muted-foreground);
  --color-accent: var(--accent);
  --color-accent-foreground: var(--accent-foreground);
  --color-destructive: var(--destructive);
  --color-destructive-foreground: var(--destructive-foreground);
  --color-border: var(--border);
  --color-input: var(--input);
  --color-ring: var(--ring);
  --color-divider: var(--divider);
  --color-divider-soft: var(--divider-soft);
  --color-primary-hover: var(--primary-hover);
  --color-primary-active: var(--primary-active);

  --radius-sm: var(--radius);
  --radius-md: var(--radius);
  --radius-lg: var(--radius);
  --radius-xl: var(--radius);

  --font-sans: var(--font-mono);

  --animate-cockpit-blink: cockpit-blink 0.9s steps(1) infinite;

  @keyframes cockpit-blink {
    0%,
    100% {
      opacity: 1;
    }
    50% {
      opacity: 0.15;
    }
  }
}

@layer base {
  html,
  body,
  #root {
    height: 100%;
  }

  body {
    margin: 0;
    background: var(--background);
    color: var(--foreground);
    font-family: var(--font-mono);
    font-size: 13px;
    line-height: 1.7;
    -webkit-font-smoothing: antialiased;
  }

  /* One focus rule for the whole app — matches CopilotKit's focus-visible
     ring behaviour without borrowing its 2px glow. */
  :focus-visible {
    outline: 1px solid var(--ring);
    outline-offset: 2px;
  }

  a {
    color: var(--primary);
    text-decoration: none;
    border-bottom: 1px solid color-mix(in oklch, var(--primary) 45%, transparent);
  }

  a:hover {
    color: var(--primary-hover);
    border-bottom-color: var(--primary-hover);
  }

  button,
  select,
  input,
  textarea {
    font: inherit;
    color: inherit;
    border-radius: var(--radius);
  }

  /* CopilotKit sets its own font stack on inner nodes; force mono. */
  [data-copilotkit],
  [data-copilotkit] * {
    font-family: var(--font-mono);
  }
}

/* Streaming caret: a block, not a bar, and un-eased on purpose. */
.caret {
  display: inline-block;
  width: 7px;
  height: 14px;
  margin-left: 4px;
  vertical-align: -2px;
  background: var(--primary);
  animation: cockpit-blink 0.9s steps(1) infinite;
}
```

- [ ] **Step 5: Verify build + lint**

Run from `a2a-orchestrator/frontend/`: `npm run build && npm run lint`
Expected: both pass. The old `index.css` selectors (`p.error`, `aside.approval`, `p.approval-done`) are gone; the components that reference those classes still render unstyled — that's fine, later tasks replace them.

- [ ] **Step 6: Smoke the acceptance criterion**

Run `npm run dev` briefly and load the app (the `/api` proxy may 502 if the service isn't running — irrelevant here). Confirm: page background is near-black `#08090a`-ish, text is mono. This proves tokens flow; the chat-pane token check happens in visual-verification with a live chat.

- [ ] **Step 7: Commit**

```bash
git add -A a2a-orchestrator/frontend
git commit -m "cockpit: phosphor tokens + tailwind v4 + theme bootstrap (tokens-theme-wiring)"
```

---

### app-shell

**Files:**
- Create: `a2a-orchestrator/frontend/src/lib/utils.ts`
- Create: `a2a-orchestrator/frontend/src/components/ui/button.tsx`
- Modify: `a2a-orchestrator/frontend/src/App.tsx` (full rewrite of the JSX; keep the existing state/handlers `missions`, `repos`, `missionId`, `chat`, `repoChoice`, `error`, `refresh`, `startMission`, `startChat` exactly as they are)

**Interfaces:**
- Consumes: Tailwind utilities from tokens-theme-wiring.
- Produces: `cn(...inputs)` from `lib/utils.ts`; `Button` + `buttonVariants` from `components/ui/button.tsx` with `variant: 'primary' | 'secondary' | 'deny' | 'ghost'` and `size: 'sm' | 'md' | 'lg'` — approval-card and error-surfaces import these. `App.tsx` renders `<ChatPane chat={chat} key={chat.context_id} />` unchanged (repo-picker and approval-card tasks extend this call site later).

Design reference: README §“Screens / views” 1–2, §“Spacing & geometry”, §“Interactions & behavior”. Mock `2a` in `Cockpit - Redesign.dc.html`.

- [ ] **Step 1: `src/lib/utils.ts`**

```ts
import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}
```

- [ ] **Step 2: `src/components/ui/button.tsx`**

shadcn-shaped, Phosphor variants. Dark-theme hover/active fills for `deny` use the README's exact values under `dark:`; light theme approximates with `destructive` alpha (the README specifies dark only).

```tsx
import { forwardRef } from 'react'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '../../lib/utils'

const buttonVariants = cva(
  'inline-flex cursor-pointer items-center justify-center gap-1.5 whitespace-nowrap rounded-sm border font-bold tracking-[.06em] transition-colors duration-[120ms] ease-linear disabled:pointer-events-none',
  {
    variants: {
      variant: {
        primary:
          'border-transparent bg-primary text-primary-foreground hover:bg-primary-hover active:bg-primary-active disabled:bg-[oklch(0.26_0.02_150)] disabled:text-[oklch(0.50_0.02_150)]',
        secondary:
          'border-border bg-transparent text-foreground hover:bg-accent active:bg-muted disabled:text-muted-foreground',
        deny:
          'border-[oklch(0.55_0.20_340)] bg-transparent text-destructive hover:bg-destructive/10 active:bg-destructive/25 dark:hover:border-[oklch(0.68_0.22_340)] dark:hover:bg-[oklch(0.22_0.08_340)] dark:active:bg-[oklch(0.34_0.14_340)] dark:active:text-white',
        ghost:
          'border-transparent bg-transparent text-primary hover:bg-accent active:bg-muted',
      },
      size: {
        sm: 'h-7 px-2.5 text-[11.5px]',
        md: 'h-8 px-3.5 text-[12px] tracking-[.08em]',
        lg: 'h-11 w-full text-[12px] tracking-[.08em]',
      },
    },
    defaultVariants: { variant: 'secondary', size: 'sm' },
  },
)

type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> &
  VariantProps<typeof buttonVariants>

const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, ...props }, ref) => (
    <button ref={ref} className={cn(buttonVariants({ variant, size }), className)} {...props} />
  ),
)
Button.displayName = 'Button'

export { Button, buttonVariants }
```

- [ ] **Step 3: Rewrite `App.tsx` presentation**

Keep all existing state and handlers verbatim. Add:

```tsx
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
```

Plus `drawerOpen` state (`useState(false)`), closed on any mission/chat selection.

**Layout spec (values are authoritative):**

*View A — no mission selected (`mission === null`): standalone list panel.* Full-viewport column, `max-width` unconstrained. Structure:
- Header row: 52px tall, padding `0 20px`, bottom border `border-divider`. Left: wordmark `COCKPIT_` — 13px / 700 / tracking `.08em`, the trailing `_` in `text-primary`. Right: theme toggle button (ghost, 11.5px) showing `[DARK]` when dark, `[LIGHT]` when light.
- Missions bar: padding `16px 20px 8px`, flex row, gap 12px, `items-baseline`: label `MISSIONS/` 10.5px tracking `.16em` `text-muted-foreground`; a 1px `bg-divider-soft` rule (`flex-1 h-px self-center`); `[+] NEW` ghost button (11.5px, `text-primary`).
- Rows (buttons, full-width, text-left): min-height 40px (44px under `md:` breakpoint inverse — use `max-md:min-h-11`), padding `0 20px`, flex with gap 10px, separated by `border-b border-divider-soft`. Content: `▸` glyph (`text-primary` when `m.id === missionId`, else `text-[oklch(0.50_0.03_150)]`), title 12.5px (`max-md:text-[13px]`), right-aligned meta `text-muted-foreground` 11px: `` `${m.chats.length} chat${m.chats.length === 1 ? '' : 's'} · ${timeAgo(m.created_at)}` ``. Hover `bg-accent`; `focus-visible` uses `outline-offset:-1px` → class `focus-visible:-outline-offset-1`.
- Footer: padding `10px 20px`, 10.5px `text-muted-foreground`: `mission list · {missions.length} missions`.
- Empty state (no missions): centered flex column, gap 12px, padding-top ~120px: `NO MISSIONS YET` 12px tracking `.14em` `text-muted-foreground`; explanation two lines 12.5px/1.7 centered, color `oklch(0.76 0.02 150)` dark / `text-muted-foreground` light (mock copy, verbatim): `A mission is one unit of work. Create one, pick a repo,` `<br/>` `then drive the agent from the chat pane.`; then `[+] NEW MISSION` primary `md` button.
- Error: if `error`, a strip above the list — will be restyled in error-surfaces; for now render `<p className="px-5 py-2 text-destructive text-[12px]">{error}</p>`.

*View B — mission selected: sidebar + main grid.* Root: `grid h-dvh grid-cols-[248px_1fr] overflow-hidden max-md:grid-cols-1`.

Sidebar (`<aside>`): `border-r border-divider bg-card p-4 flex flex-col overflow-y-auto`. On mobile it becomes a drawer: `max-md:fixed max-md:inset-y-0 max-md:left-0 max-md:z-40 max-md:w-[300px] max-md:transition-transform max-md:duration-[120ms]` with `-translate-x-full` when closed, `translate-x-0` when open; plus a scrim `<div>` behind it when open: `fixed inset-0 z-30 bg-[rgba(4,8,5,.72)] md:hidden` (click closes). Contents:
- Wordmark row: `COCKPIT_` (as View A, clicking it goes to mission list: `setMissionId(null); setChat(null)`), theme toggle right.
- `MISSIONS/` label as in View A (without the `[+] NEW` on the right).
- Mission rows: 12.5px, padding `6px 8px`, rounded-sm. Selected mission: `bg-muted font-medium border-y border-[oklch(0.34_0.06_150)]`; its chats nest beneath at `padding-left: 30px`, `text-primary`, 12px, each a button selecting that chat (`setChat(c)`); suffix omitted (no status field).
- Footer (mt-auto): `[+] NEW MISSION` ghost button, 11.5px, `text-primary`, calls `startMission`.

Main (`<section className="flex min-w-0 flex-col overflow-hidden">`):
- Header: `flex h-[52px] shrink-0 items-center justify-between gap-3 border-b border-divider px-5`. Left: on mobile, the drawer toggle button first (`md:hidden`, 44px tap target, two `h-[1.5px] w-4 bg-foreground` divs stacked with 5px gap); then breadcrumb 12.5px: `<span className="text-muted-foreground">cockpit /</span> <span className="font-bold">{mission.title}</span>`. Right: repo select + `OPEN CHAT` button:
  - This task keeps the native `<select>` (restyled: `h-8 rounded-sm border border-border bg-transparent px-2.5 text-[12px]`) — repo-picker replaces it.
  - `OPEN CHAT`: `<Button variant={repoChoice ? 'primary' : 'secondary'} size="md" disabled={!repoChoice} onClick={startChat}>OPEN CHAT</Button>`. Change the initial `repoChoice` behavior: **remove the auto-select of `entries[0].name`** in the `listRepos().then` so the unchosen state actually exists (set `''` — i.e. just drop the `if (entries.length > 0) setRepoChoice(...)` line).
- Body: if `chat`: `<div className="min-h-0 flex-1"><ChatPane chat={chat} key={chat.context_id} /></div>`. Else centered empty state: `NO CHAT OPEN` 12px tracking `.14em` muted + two lines 12.5px, color `oklch(0.74 0.02 150)` dark / `text-muted-foreground` light (mock copy, verbatim): `Pick a repo and open a chat — the agent card is fetched` `<br/>` `when the chat is created, not before.`
- Error strip as in View A.

- [ ] **Step 4: Verify**

`npm run build && npm run lint` — clean. `npm run dev`: mission list renders in Phosphor dark; toggling theme flips instantly and persists across reload (check `localStorage.theme`); narrow the window below 768px and confirm the drawer + scrim behavior.

- [ ] **Step 5: Commit**

```bash
git add -A a2a-orchestrator/frontend
git commit -m "cockpit: phosphor app shell — sidebar, mission list, theme toggle, drawer (app-shell)"
```

---

### repo-picker

**Files:**
- Create: `a2a-orchestrator/frontend/src/RepoPicker.tsx`
- Modify: `a2a-orchestrator/frontend/src/App.tsx` (swap the native `<select>` for `<RepoPicker>`)

**Interfaces:**
- Consumes: `cn` from `lib/utils.ts`; `RepoEntry` from `./api`.
- Produces: `export function RepoPicker({ repos, value, onChange }: { repos: RepoEntry[]; value: string; onChange: (name: string) => void })`.

Design reference: README §“Screens / views” 3, mock `3a`.

- [ ] **Step 1: Write `RepoPicker.tsx`**

A combobox where the trigger itself is the filter input. Behavior spec:

- Closed: a button, `flex h-8 items-center gap-1.5 rounded-sm border border-border px-2.5 text-[12px]`, content: `<span className="text-muted-foreground">repo:</span> {value || 'select'} <span aria-hidden>▾</span>`. On mobile, truncate the name: wrap it in `max-w-[9ch] truncate md:max-w-none`.
- Open (click or any typing while focused): border becomes `border-ring`, caret flips to `▴`, and a text input replaces the name (`autoFocus`, `caret-color` primary via class `caret-primary`, transparent background, width `12ch`). State: `open: boolean`, `query: string`, `activeIndex: number` (reset to 0 when `query` changes).
- Panel: absolutely positioned under the trigger, right-aligned (`absolute right-0 top-full z-50 w-[320px]`), `border border-ring border-t-0 bg-popover`, shadow exactly `[box-shadow:0_24px_48px_-20px_rgba(0,0,0,.85)]`. The wrapping div is `relative`.
- Filtering: `const matches = repos.filter((r) => (r.name + ' ' + r.description).toLowerCase().includes(query.toLowerCase()))`.
- Option rows (`role="option"`, `aria-selected` on active): padding `8px 12px`, name 12px, description 10.5px `text-[oklch(0.68_0.03_150)]` (light: `text-muted-foreground` — use `text-muted-foreground dark:text-[oklch(0.68_0.03_150)]`). Active row: `bg-muted border-l-2 border-l-primary` (others get `border-l-2 border-l-transparent` so text doesn't shift). Mouse hover sets `activeIndex`; click selects.
- Footer strip: `flex justify-between border-t border-divider-soft px-3 py-1.5 text-[10px] text-muted-foreground`, left `↑↓ move · ⏎ select · esc close`, right `` `${matches.length} of ${repos.length} match` ``.
- Keyboard on the input: `ArrowDown`/`ArrowUp` move `activeIndex` with wraparound; `Enter` selects `matches[activeIndex]` (call `onChange(name)`, close, clear query); `Escape` closes without selecting. All three `preventDefault()`.
- Close on outside click: `useEffect` adding a `pointerdown` listener that closes when the event target is outside the root ref.
- Empty matches: a single non-interactive row `no repos match` 11px muted.
- A11y: root div `role="combobox" aria-expanded={open} aria-haspopup="listbox"`; panel `role="listbox"`; options get `id={'repo-opt-' + i}` and the input `aria-activedescendant`.

- [ ] **Step 2: Swap it into `App.tsx`**

Replace the native `<select>` with `<RepoPicker repos={repos} value={repoChoice} onChange={setRepoChoice} />`. `OPEN CHAT` logic unchanged.

- [ ] **Step 3: Verify**

`npm run build && npm run lint` — clean. Dev server: picker opens with `--ring` border and flipped caret, typing filters live, ↑↓/⏎/esc all work, match counter updates, outside click closes, `OPEN CHAT` flips secondary→primary when a repo is chosen.

- [ ] **Step 4: Commit**

```bash
git add -A a2a-orchestrator/frontend
git commit -m "cockpit: repo picker listbox — filter, keyboard nav, match count (repo-picker)"
```

---

### chat-stream-reskin

**Files:**
- Create: `a2a-orchestrator/frontend/src/chat-ui.tsx`
- Modify: `a2a-orchestrator/frontend/src/ChatPane.tsx`

**Interfaces:**
- Consumes: CopilotKit v2 slot API (verified against installed 1.67.1 — see facts below).
- Produces: `ChatUiContext` (React context `{ repoName: string; approvalPending: boolean }`, default `{ repoName: '', approvalPending: false }`) exported from `chat-ui.tsx` — approval-card's composer-disable consumes `approvalPending`; `PhosphorUserMessage`, `PhosphorAssistantMessage`, `PhosphorCursor`, `PhosphorComposer` components wired into `<CopilotChat>` via slots.

**Verified API facts (from `node_modules/@copilotkit/react-core/dist/copilotkit-D0aAnD3i.d.mts`):**

- `SlotValue<C> = C | string | Partial<React.ComponentProps<C>>` — a slot accepts a replacement component, a className string, or partial props.
- `CopilotChatProps` extends `CopilotChatViewProps` which has slots `messageView` (type `CopilotChatMessageView`), `input` (type `CopilotChatInput`), `scrollView`, `suggestionView`, `welcomeScreen`.
- `CopilotChatMessageViewProps` has slots `assistantMessage` (`CopilotChatAssistantMessage`), `userMessage` (`CopilotChatUserMessage`), `cursor` (`CopilotChatMessageView.Cursor`). So nested slot config: `messageView={{ assistantMessage: X, userMessage: Y, cursor: Z }}`.
- Custom `assistantMessage` receives `{ message: AssistantMessage; messages?: Message[]; isRunning?: boolean; ... }`. Default renderers are importable: `CopilotChatAssistantMessage.MarkdownRenderer` (props `{ content: string }`) and `CopilotChatToolCallsView` (props `{ message, messages, isRunning }`). **Render `CopilotChatToolCallsView` inside the custom assistant message — it is what invokes `useHumanInTheLoop` renders, i.e. the ApprovalCard. Dropping it kills HITL.**
- Custom `userMessage` receives `{ message: UserMessage }`.
- Custom `input` receives `CopilotChatInputProps`: `{ onSubmitMessage?, onStop?, isRunning?, value?, onChange?, mode?, autoFocus?, ... }`.
- `AssistantMessage`/`UserMessage` types are exported from `@copilotkit/react-core/v2` (re-exported `@ag-ui` types; both have optional `content?: string`).

Design reference: README §“Screens / views” 4.

- [ ] **Step 1: Write `src/chat-ui.tsx`**

```tsx
import { createContext, useContext, useState, type FormEvent } from 'react'
import {
  CopilotChatAssistantMessage,
  CopilotChatToolCallsView,
  type CopilotChatAssistantMessageProps,
  type CopilotChatInputProps,
  type CopilotChatUserMessageProps,
} from '@copilotkit/react-core/v2'

export const ChatUiContext = createContext<{ repoName: string; approvalPending: boolean }>({
  repoName: '',
  approvalPending: false,
})

export function PhosphorUserMessage({ message }: CopilotChatUserMessageProps) {
  return (
    <div className="flex gap-2 text-[13px] leading-[1.65]">
      <span className="select-none text-[oklch(0.66_0.03_150)]">you&gt;</span>
      <div className="min-w-0 whitespace-pre-wrap">{message.content ?? ''}</div>
    </div>
  )
}

export function PhosphorAssistantMessage({
  message,
  messages,
  isRunning,
}: CopilotChatAssistantMessageProps) {
  const isLast = messages?.at(-1)?.id === message.id
  return (
    <div className="flex gap-2 text-[13px] leading-[1.7]">
      <span className="select-none text-primary">cc&gt;</span>
      <div className="phosphor-assistant-body min-w-0 flex-1">
        {message.content ? (
          <CopilotChatAssistantMessage.MarkdownRenderer content={message.content} />
        ) : null}
        <CopilotChatToolCallsView message={message} messages={messages} isRunning={isRunning} />
        {isRunning && isLast ? <span className="caret" aria-hidden /> : null}
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
      <div className="flex items-center gap-2 rounded-sm border border-dashed border-border bg-[#0a0b0a] px-3.5 py-3 text-[12px] text-muted-foreground">
        answer the permission request to continue
      </div>
    )
  }
  return (
    <form
      onSubmit={submit}
      className="group flex items-center gap-2 rounded-sm border border-border bg-card px-3.5 py-3 transition-colors duration-[120ms] focus-within:border-primary"
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
```

Note: the composer intentionally does not implement transcription/attachments modes — this app never enables them. If `CopilotChatInputProps` type demands nothing else, done; if tsc complains about required props, loosen to `Pick<CopilotChatInputProps, 'onSubmitMessage' | 'isRunning'>`. If `message.id` doesn't exist on the message types, compare object identity (`messages?.at(-1) === message`) instead.

- [ ] **Step 2: Wire slots in `ChatPane.tsx`**

In the `<CopilotChat …>` call, add:

```tsx
<CopilotChat
  agentId={chat.context_id}
  threadId={chat.context_id}
  messageView={{
    assistantMessage: PhosphorAssistantMessage,
    userMessage: PhosphorUserMessage,
    cursor: PhosphorCursor,
  }}
  input={PhosphorComposer}
  onError={…unchanged…}
/>
```

Wrap the provider contents in `<ChatUiContext.Provider value={{ repoName: chat.agent, approvalPending: false }}>` (state arrives in approval-card; hardcode `false` for now via a `useMemo`d object so the context value is stable). Replace `<section>`/`<h2>` shell: the pane root becomes `<section className="flex h-full min-h-0 flex-col">` with no `h2` (the main header already carries the breadcrumb); the chat column constrains to `mx-auto w-full max-w-[660px] px-5` — apply via a wrapper div `className="flex min-h-0 flex-1 flex-col"` around `CopilotChat` and pass `className="h-full"` to `CopilotChat` if it forwards it (it extends `HTMLAttributes<HTMLDivElement>`; it does). Message stack gap: 18px — if the default message view spacing disagrees, add a scoped rule to `index.css`: `[data-copilotkit] .phosphor-assistant-body { … }` only as a last resort; prefer layout via the slot components.

- [ ] **Step 3: Tool-call line styling**

The default `CopilotChatToolCallsView` renders tool calls; the design wants each as a quiet line: 2px left border `oklch(0.40 0.06 150)`, `--card` fill, 11.5px. Scope it in `index.css` (this is CSS-only, targeting whatever container class the rendered view exposes — inspect the DOM in dev tools; CopilotKit's classes are stable kebab/data attributes in v2):

```css
.phosphor-assistant-body [data-copilotkit-tool-calls-view],
.phosphor-assistant-body .copilot-chat-tool-calls-view {
  border-left: 2px solid oklch(0.4 0.06 150);
  background: var(--card);
  font-size: 11.5px;
  padding: 4px 10px;
}
```

Adjust the selector to the actual DOM (whichever attribute/class the installed build emits — check in the browser; if neither exists, wrap `CopilotChatToolCallsView` in a `<div className="phosphor-tool-calls">` in `chat-ui.tsx` and target that).

- [ ] **Step 4: Verify**

`npm run build && npm run lint` — clean. With the service running (`/api` proxy live) open a chat: user messages show `you>` prefix, assistant `cc>` in green, no bubbles, composer shows `>` prompt + `⏎ send` and the placeholder names the repo, focus turns the composer border green. The pending ApprovalCard still renders (HITL flows through `CopilotChatToolCallsView`). If the service can't run, defer the interactive checks to visual-verification but confirm the build.

- [ ] **Step 5: Commit**

```bash
git add -A a2a-orchestrator/frontend
git commit -m "cockpit: chat stream reskin — prefix messages, block caret, phosphor composer (chat-stream-reskin)"
```

---

### approval-card

**Files:**
- Modify: `a2a-orchestrator/frontend/src/ApprovalCard.tsx` (full rewrite)
- Modify: `a2a-orchestrator/frontend/src/ChatPane.tsx` (PermissionTool render branches + approvalPending state)
- Modify: `a2a-orchestrator/frontend/src/chat-ui.tsx` (nothing — composer already reads `approvalPending` from context)

**Interfaces:**
- Consumes: `Button` from `components/ui/button.tsx`; `ChatUiContext` from `chat-ui.tsx`.
- Produces: `ApprovalCard` props change to `{ permission: Permission; repo: string; status: 'executing' | 'complete'; onAnswer: (decision: 'allow' | 'deny') => void; onPendingChange: (pending: boolean) => void }`. `Permission` gains `description?: string`.

Design reference: README §“Screens / views” 5. The receipt replaces today's `p.approval-done` (“answered”), which loses which request was answered and how.

- [ ] **Step 1: Rewrite `ApprovalCard.tsx`**

```tsx
import { useEffect, useState } from 'react'
import { Button } from './components/ui/button'

export interface Permission {
  tool: string
  request_id: string
  input: Record<string, unknown>
  description?: string
}

type Decision = 'allow' | 'deny'

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
  onAnswer,
  onPendingChange,
}: {
  permission: Permission
  repo: string
  status: 'executing' | 'complete'
  onAnswer: (decision: Decision) => void
  onPendingChange: (pending: boolean) => void
}) {
  const [sent, setSent] = useState<{ decision: Decision; at: Date } | null>(null)
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

  // Receipts stay in the transcript. A reload leaves no memory of the
  // decision (CopilotKit's HITL render only exposes status), so fall back
  // to a neutral ANSWERED receipt.
  if (status === 'complete') {
    const denied = sent?.decision === 'deny'
    const meta = `${shortId(permission.request_id)}${sent ? ' · ' + clock(sent.at) : ''}`
    return (
      <div
        className={
          denied
            ? 'rounded-sm border border-[oklch(0.45_0.16_340)] bg-[#120a10] opacity-85'
            : 'rounded-sm border border-[oklch(0.32_0.04_150)] bg-[#0b0d0c] opacity-72'
        }
      >
        <div className="flex items-center justify-between px-4 pt-2.5 text-[11px] tracking-[.14em]">
          <span className={denied ? 'text-[oklch(0.82_0.14_340)]' : 'text-[oklch(0.70_0.02_150)]'}>
            {sent ? (denied ? 'DENIED' : 'ALLOWED') : 'ANSWERED'}
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
      className="rounded-sm border border-primary bg-[#0b120d]"
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
      <div className="flex flex-col gap-3 px-4 py-3.5 max-md:-mx-4">
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
```

Note the mobile treatment: the card goes edge-to-edge (`max-md:-mx-4` on the body works only if the stream gutter is 16px — match the actual gutter; adjust to the chat column's real padding) and ALLOW/DENY stack full-width at 44px.

- [ ] **Step 2: Rework `PermissionTool` + pending state in `ChatPane.tsx`**

`ChatPane` gains `const [approvalPending, setApprovalPending] = useState(false)` and provides it through the context (the `useMemo` dependency list from chat-stream-reskin now includes it):

```tsx
const chatUi = useMemo(
  () => ({ repoName: chat.agent, approvalPending }),
  [chat.agent, approvalPending],
)
```

`PermissionTool` takes `{ repo, onPendingChange }` props and renders the card for both live and completed states so the component instance (and its `sent` state) survives the transition:

```tsx
function PermissionTool({
  repo,
  onPendingChange,
}: {
  repo: string
  onPendingChange: (pending: boolean) => void
}) {
  useHumanInTheLoop({
    name: 'request_permission',
    description: 'Ask the user to allow or deny a tool use',
    render: ({ args, status, respond }) => {
      if (status !== 'executing' && status !== 'complete') return <></>
      return (
        <ApprovalCard
          permission={args as unknown as Permission}
          repo={repo}
          status={status}
          onAnswer={(decision) => respond?.({ decision })}
          onPendingChange={onPendingChange}
        />
      )
    },
  })
  return null
}
```

Call site: `<PermissionTool repo={chat.agent} onPendingChange={setApprovalPending} />`. The deny decision still round-trips as `{ decision: 'deny' }` and `PendingRearm` is untouched.

- [ ] **Step 3: Verify**

`npm run build && npm run lint` — clean. Live check (or defer to visual-verification): trigger a permission request; card takes focus, ALLOW is first in tab order; clicking ALLOW shows SENDING + blinking square with both buttons disabled; after the round-trip the card collapses to the dimmed ALLOWED receipt with id · time; deny path shows the DENIED receipt with the "agent told" line; while pending, the composer shows the dashed "answer the permission request to continue" state; reload mid-pending → re-arm → answering yields the neutral ANSWERED receipt.

- [ ] **Step 4: Commit**

```bash
git add -A a2a-orchestrator/frontend
git commit -m "cockpit: approval card — header bar, args block, receipts, composer gate (approval-card)"
```

---

### error-surfaces

**Files:**
- Modify: `a2a-orchestrator/frontend/src/ChatPane.tsx` (banner + re-arm notice placement)
- Modify: `a2a-orchestrator/frontend/src/App.tsx` (shell error strip + remount plumbing)

**Interfaces:**
- Consumes: existing `runError` state in `ChatPane`, `error` state in `App`.
- Produces: `ChatPane` props gain optional `onRemount?: () => void`. `PendingRearm` props gain `onArmed?: () => void`.

Design reference: README §“Screens / views” 4 (run error banner, re-arm notice).

- [ ] **Step 1: Run-error banner in `ChatPane.tsx`**

Replace `{runError && <p className="error">…}` with a full-bleed strip above the stream:

```tsx
{runError && (
  <div className="flex items-center gap-2.5 border-b border-[oklch(0.40_0.14_340)] bg-[oklch(0.20_0.07_340)] px-4 py-2 text-[12px]">
    <span className="font-bold text-[oklch(0.80_0.20_340)]">ERR</span>
    <span className="min-w-0 flex-1 truncate">run failed: {runError}</span>
    {onRemount && (
      <button
        onClick={onRemount}
        className="shrink-0 cursor-pointer border-none bg-transparent text-[11px] text-[oklch(0.80_0.20_340)] hover:text-white"
      >
        ↻ remount
      </button>
    )}
  </div>
)}
```

The banner stays sticky until remount — no new clearing logic (matches today's behavior; the gap is CopilotKit's, tracked for UPSTREAM).

- [ ] **Step 2: Remount plumbing in `App.tsx`**

```tsx
const [chatNonce, setChatNonce] = useState(0)
```

Render: `<ChatPane chat={chat} key={`${chat.context_id}:${chatNonce}`} onRemount={() => setChatNonce((n) => n + 1)} />`. Also restyle the shell `error` strip the same way as the banner (without the remount button, with a `✕ dismiss` button calling `setError('')`).

- [ ] **Step 3: Re-arm notice**

`PendingRearm` gains an optional `onArmed?: () => void` prop, called immediately after `await copilotkit.runTool({ … })` resolves (this is an addition after the existing awaits — do not reorder anything). `ChatPane` holds `const [rearmed, setRearmed] = useState(false)`, passes `onArmed={() => setRearmed(true)}`, and when `rearmed` renders one muted comment line above the chat stream:

```tsx
{rearmed && (
  <p className="m-0 px-4 py-1 text-[11px] text-muted-foreground">
    {'// reload detected · pending approval re-armed via runTool()'}
  </p>
)}
```

The re-arm *failure* path already routes through `onError` → the ERR banner from Step 1; no extra styling needed.

- [ ] **Step 4: Verify**

`npm run build && npm run lint` — clean. Kill the service mid-run (or point the proxy at a dead port) to see the ERR strip; `↻ remount` clears it by remounting the pane. Reload with a pending approval to see the `// reload detected` line.

- [ ] **Step 5: Commit**

```bash
git add -A a2a-orchestrator/frontend
git commit -m "cockpit: error surfaces — ERR strip, remount control, re-arm notice (error-surfaces)"
```

---

### visual-verification

Orchestrator-driven (browser tools against the dev server + design mocks side by side); not dispatched to an implementer subagent.

- [ ] Serve the app (`npm run dev`, service on 9300 if available) and open mock `2a`/`3a` from `~/Downloads/design_handoff_cockpit_phosphor/Cockpit - Redesign.dc.html` for comparison.
- [ ] Dark + light: mission list, empty state, mission detail, repo picker open, chat stream, approval card pending/answered, error banner.
- [ ] 390px width: drawer + scrim, stacked 44px ALLOW/DENY, truncated repo pill, pinned composer.
- [ ] Keyboard: tab order sidebar → header → chat; repo picker listbox keys; approval card takes focus on mount.
- [ ] Fix deviations found, amend the relevant commit or add a `fixup` commit per issue.

---

### docs-and-upstream

**Files:**
- Modify: `docs/DEVLOG.md` (new dated section, 2026-08-14)
- Modify: `docs/UPSTREAM.md` (two new CopilotKit entries)
- Create: `docs/superpowers/specs/2026-08-14-cockpit-phosphor-implementation-notes.md`

- [ ] **Step 1: DEVLOG entry** — narrative: design handoff received (external session produced the Phosphor system), implementation approach chosen (Tailwind v4 + cva; cmdk skipped because the trigger-as-input combobox doesn't fit its panel-input pattern), the `[data-copilotkit]` token override verified against 1.67.1, the v2 slot system (`messageView`/`input` slots) as the mechanism for the structural chat reskin, and the known data gaps (no repo reachability, no chat status → those states omitted).
- [ ] **Step 2: UPSTREAM.md entries** — both are CopilotKit gaps this design ran into (frame as leads, per the file's own rules): (a) HITL `render` exposes no decision once `status === 'complete'` — an honest receipt ("ALLOWED"/"DENIED") requires the component to remember what it sent, and a reload loses it; (b) `RUN_ERROR` has no supported way to clear a sticky error banner on the next run — both are half-documented in `ChatPane.tsx` comments already; cite the file/lines.
- [ ] **Step 3: Implementation notes doc** — short: palette rationale recap (link the brief), the `[data-copilotkit]` override technique (why it beats `:root`, import-order requirement), and the slot-wiring map (which slot renders what), so anyone theming CopilotKit v2 can reuse it.
- [ ] **Step 4: Commit**

```bash
git add docs
git commit -m "docs: phosphor implementation notes, DEVLOG, CopilotKit upstream leads (docs-and-upstream)"
```
