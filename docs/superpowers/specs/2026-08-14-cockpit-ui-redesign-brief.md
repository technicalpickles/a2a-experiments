# Redesign brief: "cockpit" (a2a-orchestrator frontend)

## What this app is

"cockpit" is a small internal tool for driving A2A (Agent2Agent) coding-agent sessions. A user
creates a "mission," picks a repo, opens a chat against an agent for that repo, and drives it
like a chat UI — including approving/denying tool-use permission requests the agent raises
mid-run. It's a dev tool, not a customer-facing product, but it's used daily and should feel
considered rather than thrown together.

## Current stack

- React 19 + TypeScript + Vite, single-page app, no router (three view states handled by local
  state in one component tree)
- `@copilotkit/react-core` v1.67.1 (`/v2` entrypoint) powers the chat pane: `CopilotChat`,
  `CopilotKitProvider`, `useHumanInTheLoop` for the approval flow
- No component library, no CSS framework, no design tokens of our own — one 30-line
  `index.css`

## The problem: two unrelated design languages stacked on top of each other

The app has three screens, and they visually don't belong together:

1. **Mission list / mission detail / repo picker** (`App.tsx`) — raw semantic HTML with almost
   no styling: `<main>`, `<ul>`, plain `<button>`, plain `<select>`. The only CSS is a handful
   of utility classes (`.error`, `.approval`, `.approval-done`) plus a system font and a
   max-width body. It reads like a dev tool with no design pass yet, because it hasn't had one.
2. **The approval card** (`ApprovalCard.tsx`) — same bare-HTML treatment, one custom class
   (`aside.approval`) with a `darkorange` border to flag it as needing attention. That's the
   entire visual vocabulary for a life-critical control (approve/deny a tool call an agent wants
   to run).
3. **The chat pane** (`ChatPane.tsx`) — imports CopilotKit's whole stylesheet
   (`@copilotkit/react-core/v2/styles.css`) and drops in `<CopilotChat>` as-is. This is a fully
   designed, Tailwind-v4/shadcn-token-based chat UI (rounded bubbles, its own type scale, its
   own color system) with zero customization. It just sits inside a `<section>` that otherwise
   looks like screen 1.

Nothing bridges these. No shared font, spacing, radius, or color decisions between the custom
shell and CopilotKit's chat widget. It's not "one app with a bug" — it's two apps' worth of
design vocabulary next to each other, and it shows.

## Technical constraint that should shape the plan

CopilotKit v2's stylesheet is a Tailwind v4 build that defines a full shadcn-style semantic
token set (`--background`, `--foreground`, `--primary`, `--secondary`, `--muted`, `--accent`,
`--destructive`, `--border`, `--input`, `--ring`, `--radius`, `--card`, `--popover`,
`--sidebar-*`, `--chart-1..5`), scoped to a `[data-copilotkit]` selector — verified by reading
the shipped CSS directly, not assumed. Its defaults (as of 1.67.1) are OKLCH grayscale — pure
white/near-black, no brand hue at all. Two implications:

- **Theming CopilotKit doesn't require fighting its internals.** Overriding the same token
  names at `[data-copilotkit]` (higher specificity than `:root`, since that's exactly where it
  redefines them) reskins the chat pane without touching CopilotKit's component code. A new
  design system for this app should almost certainly speak in these same token names, so one
  palette drives both the custom shell and the chat pane.
- **CopilotKit's own components are built on Radix primitives under the hood** (its CSS carries
  real Radix vars like `--radix-dropdown-menu-content-available-height`), which is the same
  foundation shadcn/ui components are built on. That makes adopting Tailwind + actual shadcn/ui
  components for the custom screens (buttons, selects, the approval card) a legitimate option
  here, not just a naming-convention borrow — real shadcn components would share the exact
  token system, focus-ring behavior, and radius/spacing scale CopilotKit already uses, "for
  free." I'm open to this; it's not required, but it's on the table.
- **Dark mode is scoped to a `.dark` class ancestor, and nothing in the app sets it today.**
  `index.css` only has `color-scheme: light dark` (native form-control theming), so CopilotKit
  is stuck in light mode regardless of OS/browser theme preference. If the redesign wants dark
  mode, that class needs to be wired up (e.g. from `prefers-color-scheme` or a toggle) — it
  isn't a CSS-only fix.

## Files in play (attach these — small, ~350 lines total)

- `a2a-orchestrator/frontend/src/App.tsx` — mission list, mission detail, repo picker
- `a2a-orchestrator/frontend/src/ChatPane.tsx` — CopilotKit integration, permission tool,
  pending-approval re-arm on reload
- `a2a-orchestrator/frontend/src/ApprovalCard.tsx` — the approve/deny control
- `a2a-orchestrator/frontend/src/index.css` — everything styled today
- `a2a-orchestrator/frontend/index.html` — page shell, title "cockpit"

## What I want out of this session

A cohesive visual design for the whole app — not a CopilotKit reskin and a separate shell
redesign done independently. Specifically:

1. A small token palette (color, type scale, spacing, radius) expressed as CSS custom
   properties using CopilotKit's own token names where they overlap, so the custom screens and
   the chat pane draw from the same system.
2. A real treatment for the approval card — it's the one place the user makes a consequential
   decision (allow an agent to run a tool or not) and currently looks like an afterthought.
3. A light/dark story, including actually wiring the `.dark` class (not just picking colors
   that would work if it were wired).
4. Tailwind + shadcn/ui for the custom screens is on the table and my likely preference — I was
   already considering Tailwind, and since CopilotKit already loads a Tailwind v4/shadcn-token
   runtime, adopting real shadcn components for the shell buys literal consistency (same focus
   rings, radii, primitives) instead of hand-rolled CSS trying to approximate it. Stay a plain
   Vite/React/TS app either way — no router, no state-management library, nothing beyond what
   three screens need.
5. Distinctive, not templated — whatever the palette ends up being (shadcn-based or not), this
   shouldn't read as the stock shadcn/ui gray-and-blue default, which is exactly what CopilotKit
   ships out of the box and what makes screen 3 feel like a generic demo today. If you use
   shadcn/ui, that means actually setting a real theme (color, radius, type), not accepting its
   default palette as the finished look.

Deliverable: updated `index.css` (or equivalent token file) plus any markup changes needed in
the three `.tsx` files, with rationale for the palette/type choices.
