# Implementation notes: cockpit "Phosphor" redesign

Short reference doc, written after the fact. Not a spec in the design-then-build sense — the
spec that drove this work is `docs/superpowers/specs/2026-08-14-cockpit-ui-redesign-brief.md`
and the plan is `docs/superpowers/plans/2026-08-14-cockpit-phosphor.md`. This is the "how the
CopilotKit theming actually works" reference for whoever touches it next, plus a pointer back
to where the palette came from. See `docs/DEVLOG.md`'s 2026-08-14 entry for the narrative.

## Palette

The Phosphor palette (dark-first, phosphor-green accent, 2px flat radius, all-monospace) is
defined in full in `~/Downloads/design_handoff_cockpit_phosphor/README.md` and
`tokens.css` — the design authority for this work, produced by an external design session
working from `docs/superpowers/specs/2026-08-14-cockpit-ui-redesign-brief.md`. That bundle
isn't checked into this repo; the values it specifies landed verbatim as CSS custom properties
in `a2a-orchestrator/frontend/src/index.css`. If the palette needs to change, that README is
where the rationale and exact oklch values live — don't reverse-engineer them from the CSS.

## The `[data-copilotkit]` token override

CopilotKit v2 ships its own shadcn-style design tokens (`--background`, `--primary`, `--card`,
etc.) as CSS custom properties in its bundled `styles.css`, scoped to a `[data-copilotkit]`
attribute selector rather than to `:root`. `index.css` redeclares the same variable names at
the same selector:

```css
:root,
[data-copilotkit] {
  --background: oklch(0.97 0.005 150);
  --primary: oklch(0.55 0.16 145);
  /* ... */
}
```

Two things make this work rather than silently losing to CopilotKit's own values:

1. **Selector, not specificity hacks.** `[data-copilotkit]` and CopilotKit's own default
   declaration are both attribute-selector specificity. What decides the winner between two
   rules of equal specificity is *source order* — the later one wins. So `index.css` has to
   load *after* CopilotKit's `styles.css`, not merely be present. `main.tsx` imports them in
   that order deliberately:
   ```ts
   import '@copilotkit/react-core/v2/styles.css'
   import './index.css'
   ```
   Reversing that import order silently reverts the whole chat pane to CopilotKit's stock
   theme with no error — worth checking first if a future edit "does nothing."
2. **Redeclaring `:root` too.** The app shell outside the CopilotKit subtree (sidebar, app
   header, repo picker) never gets a `data-copilotkit` attribute, so it needs the same tokens
   under a plain `:root` selector to pick up the palette at all. Both selectors are declared
   together so the two trees can't drift out of sync from a one-sided edit.

This beats writing per-component overrides against CopilotKit's own class names: those class
names aren't part of its public API and can rename across versions, while the token *names*
(`--primary`, `--card`, ...) are the same shadcn-convention names CopilotKit itself chose to
expose, so overriding them is closer to "supported" than fighting internal class selectors.

Dark mode is the default; `.dark` on `<html>` is applied pre-paint by a bootstrap script in
`index.html` (reads `localStorage.theme`, falls back to system preference) so there's no
flash of the wrong theme on load. `.dark [data-copilotkit]` and `.dark :root` carry the dark
variant of every token.

## Slot wiring: which slot renders what

The structural chat reskin (prefix-style messages instead of bubbles, block streaming caret,
custom composer) uses CopilotKit v2's slot system rather than CSS overrides, because the
structural changes aren't expressible as CSS on the stock markup. All slot components live in
`a2a-orchestrator/frontend/src/chat-ui.tsx`; they're wired into `<CopilotChat>` in
`ChatPane.tsx`:

| Slot | Component | Renders |
|---|---|---|
| `messageView.userMessage` | `PhosphorUserMessage` | User turns, prefix-style (`>` glyph, no bubble) |
| `messageView.assistantMessage` | `PhosphorAssistantMessage` | Assistant turns; wraps `CopilotChatToolCallsView` internally (see below) |
| `messageView.cursor` | `PhosphorCursor` | The streaming block caret — owns caret rendering exclusively (see below) |
| `input` | `PhosphorComposer` | The composer: textarea, send button, approval-pending gate |

Two things about this wiring that aren't obvious from the slot API alone:

- **`CopilotChatToolCallsView` has to stay nested inside `PhosphorAssistantMessage`,** not get
  pulled out into its own slot or removed. It's the only path in the v2 API that actually
  invokes `useHumanInTheLoop`'s `render()` callback — the approval card is a HITL tool-call
  render, so pulling `CopilotChatToolCallsView` out of the assistant message tree stops the
  approval card from rendering at all, silently (no error, no fallback UI).
- **The caret lives only in the `cursor` slot, not inline in the assistant message.** The
  first draft put a `isRunning && isLast` caret span directly in `PhosphorAssistantMessage`,
  matching the design mock's "caret at the end of the streaming text." That broke:
  `CopilotChat`'s `MemoizedAssistantMessage` comparator never re-renders a message once it
  stops being the last one in the thread, so an inline caret could go stale and stick on
  permanently after a later message arrived. `CopilotChatMessageView`'s own `cursor` slot
  doesn't have that problem — it's driven by run state, not by the memoized message component —
  so caret ownership moved there entirely. This is a deliberate, human-approved deviation from
  the mock's literal caret placement, not an oversight.
- **Slot prop objects need a stable identity.** `messageView` is an object literal passed as a
  prop; if it's freshly constructed on every `ChatPane` render (e.g. from `setRunError`
  firing), CopilotKit's slot memoization can't tell it apart from a real change and re-renders
  more than necessary. `ChatPane.tsx` wraps it in `useMemo` with an empty dependency array,
  since the referenced components are module-scope constants.

## Other things worth knowing before touching this again

- `useHumanInTheLoop`'s `render` closure is captured once, at the tool call's first
  registration — the effect's dependency array excludes `render` itself. Anything the render
  callback reads from outer scope (`repo`, `onPendingChange` in `PermissionTool`) has to be
  stable for the component's whole lifetime, not just correct at the moment it's passed in.
- Two CopilotKit gaps this design ran into (HITL `render()` losing the resolved decision after
  `status === 'complete'`, and `RUN_ERROR` having no clear-on-next-run hook) are written up as
  upstream leads in `docs/UPSTREAM.md`, under `@copilotkit/react-core`.
