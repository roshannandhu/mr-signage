'use client'

import { useEffect } from 'react'
import { AlertTriangle, RotateCcw } from 'lucide-react'

/**
 * Scoped error boundary for the admin area.
 *
 * Without one, a failure in any admin page fell through to app/global-error.tsx, which
 * replaces the entire application shell -- the operator lost the admin chrome and the nav
 * along with it, so the only way out of a single failed table was a manual reload.
 *
 * `unstable_retry`, not `reset`: that is the prop name in this version of Next, and
 * app/dashboard/error.tsx already uses it. Getting it wrong renders a dead button.
 */
export default function AdminError({
  error,
  unstable_retry,
}: {
  error: Error & { digest?: string }
  unstable_retry: () => void
}) {
  useEffect(() => { console.error(error) }, [error])

  return (
    <div className="grid min-h-[60vh] place-items-center p-6">
      <div className="max-w-md text-center">
        <span className="mx-auto grid size-14 place-items-center rounded-2xl bg-red-500/10 text-red-700 dark:text-red-400">
          <AlertTriangle className="size-6" />
        </span>
        <h1 className="mt-5 text-2xl font-semibold tracking-tight text-foreground">
          This admin view could not load
        </h1>
        <p className="mt-2 text-sm leading-6 text-muted-foreground">
          Nothing was changed. Retry the view, or pick another section from the menu.
        </p>
        <button
          onClick={unstable_retry}
          className="mt-6 inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2.5 text-sm font-semibold text-primary-foreground transition-colors hover:bg-primary/90"
        >
          <RotateCcw className="size-4" />
          Retry view
        </button>
      </div>
    </div>
  )
}
