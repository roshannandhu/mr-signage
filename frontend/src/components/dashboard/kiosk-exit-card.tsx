'use client'

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  ChevronDown,
  Gamepad2,
  Hand,
  KeyRound,
  RotateCcw,
  Send,
  ShieldCheck,
  Unlock,
  CheckCircle2,
  Clock,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { api } from '@/lib/api'
import type { Screen } from '@/lib/types'

/**
 * How to get out of kiosk on this screen, the code that does it, and remote unlock controls.
 *
 * Provides:
 * 1. Independent Screen PIN vs Workspace PIN inheritance management
 * 2. Real-time credentials sync telemetry
 * 3. Instant remote unlock command execution via WebSocket & durable outbox
 */
export function KioskExitCard({ screen }: { screen: Screen }) {
  const queryClient = useQueryClient()

  // Real-time maintenance PIN & sync telemetry
  const pinQuery = useQuery({
    queryKey: ['screen-maintenance-pin', screen.id],
    queryFn: () => api.getScreenMaintenancePin(screen.id),
    refetchInterval: 10000,
  })

  const [pinDraft, setPinDraft] = useState('')
  const [unlockModalOpen, setUnlockModalOpen] = useState(false)
  const [unlockReason, setUnlockReason] = useState('')

  const pinData = pinQuery.data
  const effectivePin = pinData?.effective_maintenance_pin ?? pinData?.maintenance_pin ?? (screen.effective_maintenance_pin || screen.maintenance_pin)
  const isInherited = pinData ? (pinData.is_inherited ?? !pinData.uses_own_pin) : (!screen.effective_maintenance_pin || screen.effective_maintenance_pin === screen.maintenance_pin)
  const isSynced = pinData
    ? pinData.synced_credentials_version >= pinData.workspace_credentials_version
    : true

  // Set custom PIN mutation
  const setPinMutation = useMutation({
    mutationFn: (newPin: string) => api.setScreenMaintenancePin(screen.id, newPin),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ['screen-maintenance-pin', screen.id] })
      queryClient.invalidateQueries({ queryKey: ['screens'] })
      setPinDraft('')
      toast.success(`Custom maintenance PIN ${res.maintenance_pin} saved for this screen`)
    },
    onError: (err: Error) => toast.error(err.message),
  })

  // Reset to Workspace PIN mutation
  const resetPinMutation = useMutation({
    mutationFn: () => api.resetScreenMaintenancePin(screen.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['screen-maintenance-pin', screen.id] })
      queryClient.invalidateQueries({ queryKey: ['screens'] })
      toast.success('Screen reset to inherit the Workspace PIN')
    },
    onError: (err: Error) => toast.error(err.message),
  })

  // Remote Unlock mutation
  const remoteUnlockMutation = useMutation({
    mutationFn: (reason: string) => api.remoteUnlockScreen(screen.id, reason),
    onSuccess: (res) => {
      setUnlockModalOpen(false)
      setUnlockReason('')
      toast.success('Remote unlock command dispatched to TV (expires in 10m)')
      queryClient.invalidateQueries({ queryKey: ['screen-maintenance-pin', screen.id] })
    },
    onError: (err: Error) => toast.error(err.message),
  })

  return (
    <details className="ring-hairline bg-card group rounded-2xl ring-1 [&[open]_.chevron]:rotate-180">
      <summary className="flex cursor-pointer list-none items-center gap-2.5 p-5 font-semibold sm:p-6 [&::-webkit-details-marker]:hidden">
        <span className="bg-primary/10 text-primary dark:text-brand hidden size-9 shrink-0 place-items-center rounded-lg sm:grid">
          <KeyRound className="size-4" aria-hidden="true" />
        </span>
        <span className="text-foreground min-w-0 flex-1 truncate">Getting into this screen</span>
        {effectivePin ? (
          <code className="bg-primary/10 text-primary dark:text-brand shrink-0 rounded-lg px-2.5 py-1 text-sm font-semibold tracking-[0.2em]">
            {effectivePin}
          </code>
        ) : (
          <span className="shrink-0 text-sm font-medium text-amber-600 dark:text-amber-400">No PIN</span>
        )}
        <ChevronDown className="chevron text-muted-foreground size-4 shrink-0 transition-transform" aria-hidden="true" />
      </summary>

      <div className="space-y-6 px-5 pb-5 sm:px-6 sm:pb-6">
        <p className="text-muted-foreground text-sm">
          A signage screen blocks Home and Back on purpose to protect advertisement playback. Use on-site remote/touch gestures or trigger an instant remote unlock from this dashboard.
        </p>

        {/* Status & Sync Telemetry */}
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-muted/30 p-3.5">
          <div className="flex items-center gap-2">
            <span className="text-xs text-muted-foreground">PIN Source:</span>
            <Badge variant="outline" className="text-xs">
              {isInherited ? 'Workspace Shared PIN' : 'Screen Custom Override'}
            </Badge>
          </div>

          <div className="flex items-center gap-2">
            <span className="text-xs text-muted-foreground">TV Sync:</span>
            {isSynced ? (
              <span className="inline-flex items-center gap-1 text-xs font-medium text-emerald-600 dark:text-emerald-400">
                <CheckCircle2 className="size-3.5" /> Synced to TV (v{pinData?.synced_credentials_version ?? 1})
              </span>
            ) : (
              <span className="inline-flex items-center gap-1 text-xs font-medium text-amber-600 dark:text-amber-400">
                <Clock className="size-3.5 animate-spin" /> Sync Pending (v{pinData?.synced_credentials_version ?? 0} &rarr; v{pinData?.workspace_credentials_version ?? 1})
              </span>
            )}
          </div>
        </div>

        {/* Gestures Guide */}
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="bg-secondary/40 rounded-xl p-3.5">
            <p className="text-foreground flex items-center gap-1.5 text-sm font-medium">
              <Hand className="size-4" aria-hidden="true" /> Touch screen
            </p>
            <p className="text-muted-foreground mt-1 text-sm">
              Tap any <span className="text-foreground">corner 7 times</span> within 3 seconds.
            </p>
          </div>
          <div className="bg-secondary/40 rounded-xl p-3.5">
            <p className="text-foreground flex items-center gap-1.5 text-sm font-medium">
              <Gamepad2 className="size-4" aria-hidden="true" /> With a remote
            </p>
            <p className="text-muted-foreground mt-1 text-sm">
              Press <span className="text-foreground">Up, Up, Down, Down, OK</span>.
            </p>
          </div>
        </div>

        {/* PIN Management Controls */}
        <div className="space-y-3 rounded-xl border border-border bg-muted/20 p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Independent Screen PIN
            </h4>
            {!isInherited && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => resetPinMutation.mutate()}
                disabled={resetPinMutation.isPending}
                className="h-7 text-xs text-muted-foreground hover:text-foreground"
              >
                <RotateCcw className="mr-1 size-3" />
                Reset to Workspace PIN
              </Button>
            )}
          </div>

          <div className="flex items-center gap-2">
            <Input
              inputMode="numeric"
              maxLength={4}
              value={pinDraft}
              onChange={(e) => setPinDraft(e.target.value.replace(/\D/g, '').slice(0, 4))}
              placeholder={isInherited ? 'Set custom 4-digit PIN' : 'Change 4-digit PIN'}
              className="max-w-44 font-mono text-center tracking-[0.2em]"
            />
            <Button
              size="sm"
              disabled={setPinMutation.isPending || !/^\d{4}$/.test(pinDraft)}
              onClick={() => setPinMutation.mutate(pinDraft)}
            >
              {setPinMutation.isPending ? 'Saving…' : 'Save PIN'}
            </Button>
          </div>
        </div>

        {/* Remote Unlock Trigger */}
        <div className="flex items-center justify-between rounded-xl border border-rose-500/20 bg-rose-500/5 p-4">
          <div>
            <h4 className="text-sm font-medium text-rose-700 dark:text-rose-400">Remote Kiosk Unlock</h4>
            <p className="text-xs text-muted-foreground">
              Immediately opens the maintenance screen on the online TV without entering a PIN on-site.
            </p>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setUnlockModalOpen(true)}
            className="border-rose-500/30 text-rose-700 hover:bg-rose-500/10 dark:text-rose-400"
          >
            <Unlock className="mr-1.5 size-3.5" />
            Remote Unlock
          </Button>
        </div>

        {/* Remote Unlock Confirmation Modal */}
        {unlockModalOpen && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
            <div className="w-full max-w-md rounded-2xl border border-border bg-card p-6 shadow-2xl">
              <h3 className="text-base font-semibold text-rose-700 dark:text-rose-400">
                Confirm Remote TV Kiosk Unlock
              </h3>
              <p className="mt-2 text-xs text-muted-foreground">
                This sends an immediate unlock signal to <strong className="text-foreground">{screen.name || `Screen ${screen.id}`}</strong>. The TV will exit kiosk playback to the maintenance screen for technician access.
              </p>

              <div className="mt-4">
                <label className="text-xs font-medium text-foreground">
                  Reason for remote unlock <span className="text-rose-500">*</span>
                </label>
                <textarea
                  value={unlockReason}
                  onChange={(e) => setUnlockReason(e.target.value)}
                  placeholder="e.g. Technician on-site Wi-Fi reconfiguration..."
                  rows={2}
                  className="mt-1 w-full rounded-xl border border-border bg-muted/40 p-2.5 text-xs text-foreground placeholder:text-muted-foreground focus:border-rose-500 focus:outline-none"
                />
              </div>

              <div className="mt-6 flex justify-end gap-3">
                <Button variant="outline" size="sm" onClick={() => setUnlockModalOpen(false)}>
                  Cancel
                </Button>
                <Button
                  variant="destructive"
                  size="sm"
                  disabled={!unlockReason.trim() || remoteUnlockMutation.isPending}
                  onClick={() => remoteUnlockMutation.mutate(unlockReason)}
                >
                  {remoteUnlockMutation.isPending ? 'Sending…' : 'Send Unlock Signal'}
                </Button>
              </div>
            </div>
          </div>
        )}
      </div>
    </details>
  )
}
