// The one error strip. App uses it for fetch/mission failures (dismiss),
// ChatPane for RUN_ERROR (remount) — same bar, different message and action.
//
// Both themes are painted explicitly: the magenta fill is dark-pinned, so the
// light counterpart has to be declared or `--foreground` flips to near-black
// on a near-black bar. Light approximation first, `dark:` restores the
// original values.
export function ErrorStrip({
  message,
  action,
}: {
  message: string
  action?: { label: string; onClick: () => void }
}) {
  if (!message) return null
  return (
    <div className="flex items-center gap-2.5 border-b border-[oklch(0.78_0.10_340)] bg-[oklch(0.92_0.05_340)] px-4 py-2 text-[12px] dark:border-[oklch(0.40_0.14_340)] dark:bg-[oklch(0.20_0.07_340)]">
      <span className="font-bold text-[oklch(0.45_0.20_340)] dark:text-[oklch(0.80_0.20_340)]">
        ERR
      </span>
      <span className="min-w-0 flex-1 truncate">{message}</span>
      {action && (
        <button
          onClick={action.onClick}
          className="shrink-0 cursor-pointer border-none bg-transparent text-[11px] text-[oklch(0.45_0.20_340)] hover:text-[oklch(0.30_0.16_340)] dark:text-[oklch(0.80_0.20_340)] dark:hover:text-white"
        >
          {action.label}
        </button>
      )}
    </div>
  )
}
