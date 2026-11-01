'use client'

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { ChevronDown, Lock } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { api } from '@/lib/api'

/**
 * One PIN an owner can set that covers every screen in the workspace, so a venue with many
 * panels does not need a code memorised per wall. A screen keeps its own instead if "Keep
 * this screen on its own pin" is checked on that screen's Settings (screen-settings-dialog).
 *
 * Owners only -- this is a fleet-wide setting, same reasoning as Team and Billing.
 */
export function WorkspaceLockPinCard() {
  const queryClient = useQueryClient()
  const lockPinQuery = useQuery({ queryKey: ['workspace-lock-pin'], queryFn: api.getWorkspaceLockPin })
  const [draft, setDraft] = useState<string | null>(null)
  const pin = draft ?? lockPinQuery.data?.lock_pin ?? ''

  const save = useMutation({
    mutationFn: (value: string) => api.setWorkspaceLockPin(value || null),
    onSuccess: (updated) => {
      queryClient.setQueryData(['workspace-lock-pin'], updated)
      setDraft(null)
      toast.success(updated.lock_pin ? 'Workspace lock pin saved' : 'Workspace lock pin cleared')
    },
    onError: (error: Error) => toast.error(error.message),
  })

  if (lockPinQuery.isLoading) return null

  return (
    <details className="ring-hairline bg-card group rounded-2xl ring-1 [&[open]_.chevron]:rotate-180">
      <summary className="flex cursor-pointer list-none items-center gap-2.5 p-5 font-semibold sm:p-6 [&::-webkit-details-marker]:hidden">
        <span className="bg-primary/10 text-primary dark:text-brand hidden size-9 shrink-0 place-items-center rounded-lg sm:grid">
          <Lock className="size-4" aria-hidden="true" />
        </span>
        <span className="text-foreground min-w-0 flex-1 truncate">Workspace lock pin</span>
        {lockPinQuery.data?.lock_pin ? (
          <code className="bg-primary/10 text-primary dark:text-brand shrink-0 rounded-lg px-2.5 py-1 text-sm font-semibold tracking-[0.2em]">
            {lockPinQuery.data.lock_pin}
          </code>
        ) : (
          <span className="text-muted-foreground shrink-0 text-sm">Not set</span>
        )}
        <ChevronDown className="chevron text-muted-foreground size-4 shrink-0 transition-transform" aria-hidden="true" />
      </summary>

      <div className="px-5 pb-5 sm:px-6 sm:pb-6">
        <p className="text-muted-foreground text-sm">
          Opens the maintenance screen on every TV in this workspace, instead of each one
          having its own code. A screen can still keep its own pin under its own Settings.
        </p>
        <div className="mt-4 flex items-center gap-2">
          <Input
            inputMode="numeric"
            maxLength={4}
            value={pin}
            onChange={(event) => setDraft(event.target.value.replace(/\D/g, '').slice(0, 4))}
            placeholder="0000"
            className="max-w-32"
          />
          <Button
            size="sm"
            disabled={save.isPending || (pin !== '' && !/^\d{4}$/.test(pin))}
            onClick={() => save.mutate(pin)}
          >
            {save.isPending ? 'Saving…' : pin ? 'Save' : 'Clear'}
          </Button>
        </div>
      </div>
    </details>
  )
}
