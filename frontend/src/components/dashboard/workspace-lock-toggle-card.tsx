'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { ShieldCheck } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { api } from '@/lib/api'

/**
 * The tenant's own on/off switch for TV lock, separate from whether their plan sells the
 * feature at all. A plan gaining kiosk_lock must never retroactively trap an already-paired
 * fleet the moment it next syncs -- this card exists so the tenant decides when, not the
 * plan. Rendered only when usePlanFeatures().has('kiosk_lock') is true; a plan without the
 * feature shows nothing here, same as every other Phase 3.1 flag.
 */
export function WorkspaceLockToggleCard() {
  const queryClient = useQueryClient()
  const lockEnabledQuery = useQuery({ queryKey: ['workspace-lock-enabled'], queryFn: api.getWorkspaceLockEnabled })
  const lockPinQuery = useQuery({ queryKey: ['workspace-lock-pin'], queryFn: api.getWorkspaceLockPin })

  const toggle = useMutation({
    mutationFn: (enabled: boolean) => api.setWorkspaceLockEnabled(enabled),
    onSuccess: (updated) => {
      queryClient.setQueryData(['workspace-lock-enabled'], updated)
      toast.success(updated.lock_enabled ? 'TV lock enabled' : 'TV lock disabled')
    },
    onError: (error: Error) => toast.error(error.message),
  })

  if (lockEnabledQuery.isLoading) return null
  const enabled = lockEnabledQuery.data?.lock_enabled ?? false
  const hasLockPin = Boolean(lockPinQuery.data?.lock_pin)

  const handleToggle = (checked: boolean) => {
    if (checked && !hasLockPin) {
      toast.error('A 4-digit Workspace Lock PIN is required before enabling TV lock. Please configure a PIN below.')
      return
    }
    toggle.mutate(checked)
  }

  return (
    <div className="ring-hairline bg-card rounded-2xl p-5 ring-1 sm:p-6">
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <span className="bg-primary/10 text-primary dark:text-brand grid size-9 shrink-0 place-items-center rounded-lg">
            <ShieldCheck className="size-4" aria-hidden="true" />
          </span>
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-foreground font-semibold">TV lock</h2>
              <Badge variant="outline" className="text-[10px]">Pro feature</Badge>
            </div>
            <p className="text-muted-foreground mt-1 text-sm">
              Protect paired TVs from unauthorized exit.
            </p>
          </div>
        </div>
        <label className="relative inline-flex shrink-0 cursor-pointer items-center">
          <input
            type="checkbox"
            className="peer sr-only"
            checked={enabled}
            disabled={toggle.isPending}
            onChange={(event) => handleToggle(event.target.checked)}
          />
          <div className="bg-muted peer-checked:bg-primary h-6 w-11 rounded-full transition-colors" />
          <div className="absolute left-1 size-4 rounded-full bg-white transition-transform peer-checked:translate-x-5" />
        </label>
      </div>
      {!enabled && (
        <p className="text-muted-foreground mt-4 text-xs">
          Locking is disabled. TVs operate normally -- Back and Home work like any other app.
          {!hasLockPin && (
            <span className="block mt-1 text-amber-600 dark:text-amber-400 font-medium">
              Note: You must set a 4-digit Workspace PIN below before turning TV lock ON.
            </span>
          )}
        </p>
      )}
    </div>
  )
}
