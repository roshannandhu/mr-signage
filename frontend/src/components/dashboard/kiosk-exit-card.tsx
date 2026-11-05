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
  ShieldAlert,
  Unlock,
  Lock,
  CheckCircle2,
  Clock,
  Sparkles,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { api } from '@/lib/api'
import type { Screen } from '@/lib/types'
import { usePlanFeatures } from '@/lib/use-plan-features'

/**
 * Screen Kiosk Lock & Maintenance Security Controls.
 *
 * Provides:
 * 1. Bidirectional Lock/Unlock Switch reflecting live device state
 * 2. On-site secret unlock gesture instructions (7 corner taps / remote sequence + PIN)
 * 3. Screen custom PIN vs Workspace PIN inheritance
 * 4. Plan feature gating (enforced by super-admin plan configuration)
 */
export function KioskExitCard({ screen }: { screen: Screen }) {
  const queryClient = useQueryClient()
  const hasKioskLock = usePlanFeatures().has('kiosk_lock')

  // Real-time maintenance PIN & sync telemetry
  const pinQuery = useQuery({
    queryKey: ['screen-maintenance-pin', screen.id],
    queryFn: () => api.getScreenMaintenancePin(screen.id),
    refetchInterval: 3000,
  })

  // Live Screen Lock status query (bidirectional reflection with TV/tablet)
  const lockStatusQuery = useQuery({
    queryKey: ['screen-lock-status', screen.id],
    queryFn: () => api.getScreenLockStatus(screen.id),
    refetchInterval: 2000,
  })

  const [pinDraft, setPinDraft] = useState('')

  const pinData = pinQuery.data
  const rawPin = pinData?.effective_maintenance_pin ?? pinData?.maintenance_pin ?? (screen.effective_maintenance_pin || screen.maintenance_pin)
  const effectivePin = rawPin && /^\d{4}$/.test(String(rawPin).trim()) ? String(rawPin).trim() : null
  const isInherited = pinData ? (pinData.is_inherited ?? !pinData.uses_own_pin) : (!screen.effective_maintenance_pin || screen.effective_maintenance_pin === screen.maintenance_pin)
  const isSynced = pinData
    ? pinData.synced_credentials_version >= pinData.workspace_credentials_version
    : true

  // Current live lock state (reflects both dashboard toggle and on-device secret unlocks)
  const isLocked = lockStatusQuery.data?.locked ?? screen.is_locked ?? true

  // Bidirectional Screen Lock Mutation
  const setLockMutation = useMutation({
    mutationFn: (newLocked: boolean) => api.setScreenLockStatus(screen.id, newLocked),
    onMutate: async (newLocked: boolean) => {
      await queryClient.cancelQueries({ queryKey: ['screen-lock-status', screen.id] })
      const prev = queryClient.getQueryData(['screen-lock-status', screen.id])
      queryClient.setQueryData(['screen-lock-status', screen.id], {
        screen_id: screen.id,
        locked: newLocked,
        feature_enabled: true,
      })
      return { prev }
    },
    onError: (err: Error, _, context) => {
      if (context?.prev) queryClient.setQueryData(['screen-lock-status', screen.id], context.prev)
      toast.error(err.message)
    },
    onSuccess: (data) => {
      queryClient.setQueryData(['screen-lock-status', screen.id], data)
      queryClient.invalidateQueries({ queryKey: ['screen', screen.id] })
      queryClient.invalidateQueries({ queryKey: ['screens'] })
      toast.success(data.locked ? 'Screen locked in kiosk playback' : 'Screen unlocked for maintenance')
    },
  })

  // Set custom PIN mutation
  const setPinMutation = useMutation({
    mutationFn: (newPin: string) => api.setScreenMaintenancePin(screen.id, newPin),
    onSuccess: (res) => {
      queryClient.setQueryData(['screen-maintenance-pin', screen.id], res)
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
    onSuccess: (res) => {
      queryClient.setQueryData(['screen-maintenance-pin', screen.id], res)
      queryClient.invalidateQueries({ queryKey: ['screen-maintenance-pin', screen.id] })
      queryClient.invalidateQueries({ queryKey: ['screens'] })
      toast.success('Screen reset to inherit the Workspace PIN')
    },
    onError: (err: Error) => toast.error(err.message),
  })

  return (
    <details className="ring-hairline bg-card group rounded-2xl ring-1 [&[open]_.chevron]:rotate-180" open>
      <summary className="flex cursor-pointer list-none items-center gap-2.5 p-5 font-semibold sm:p-6 [&::-webkit-details-marker]:hidden">
        <span className="bg-primary/10 text-primary dark:text-brand hidden size-9 shrink-0 place-items-center rounded-lg sm:grid">
          <KeyRound className="size-4" aria-hidden="true" />
        </span>
        <div className="flex flex-1 items-center gap-2 min-w-0">
          <span className="text-foreground font-semibold truncate">Screen Lock & Security</span>
          <Badge
            variant="outline"
            className={`text-xs gap-1 font-medium ${
              isLocked
                ? 'bg-emerald-500/10 text-emerald-600 border-emerald-500/30 dark:text-emerald-400'
                : 'bg-amber-500/10 text-amber-600 border-amber-500/30 dark:text-amber-400'
            }`}
          >
            {isLocked ? (
              <>
                <Lock className="size-3" /> Locked
              </>
            ) : (
              <>
                <Unlock className="size-3" /> Unlocked
              </>
            )}
          </Badge>
        </div>
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
        {/* 1. Bidirectional Kiosk Lock Switch */}
        <div className="rounded-xl border border-border bg-muted/20 p-4 sm:p-5">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
            <div className="space-y-1">
              <div className="flex items-center gap-2">
                <span className="text-sm font-semibold text-foreground">Screen Kiosk Lock</span>
                <Badge
                  variant="outline"
                  className={`text-[11px] gap-1 font-semibold ${
                    isLocked
                      ? 'bg-emerald-500/15 text-emerald-600 border-emerald-500/30 dark:text-emerald-400'
                      : 'bg-amber-500/15 text-amber-600 border-amber-500/30 dark:text-amber-400'
                  }`}
                >
                  {isLocked ? 'Locked in Kiosk' : 'Unlocked (Operator Access)'}
                </Badge>
              </div>
              <p className="text-xs text-muted-foreground max-w-xl">
                {isLocked
                  ? 'All Android navigation (Home, Back, Recents) and status bars are disabled. Advertisements play uninterrupted. To unlock on-site, perform the secret gesture and enter the PIN.'
                  : 'Kiosk is unlocked. Operators can use Android Home, Settings, or other apps freely without being pulled back to ads. Toggle ON to lock back into playback.'}
              </p>
            </div>

            {hasKioskLock ? (
              <div className="flex items-center gap-3 shrink-0 self-end sm:self-center">
                <span className="text-xs font-medium text-muted-foreground">
                  {isLocked ? 'Locked' : 'Unlocked'}
                </span>
                <label className="relative inline-flex items-center cursor-pointer">
                  <input
                    type="checkbox"
                    className="peer sr-only"
                    checked={isLocked}
                    disabled={setLockMutation.isPending}
                    onChange={(e) => setLockMutation.mutate(e.target.checked)}
                  />
                  <div className="h-7 w-12 rounded-full bg-amber-500/30 peer-checked:bg-emerald-600 transition-colors after:content-[''] after:absolute after:top-[3px] after:left-[4px] after:bg-white after:border-gray-300 after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:after:translate-x-5" />
                </label>
              </div>
            ) : (
              <Badge variant="outline" className="text-xs text-amber-600 border-amber-500/30 gap-1 shrink-0">
                <ShieldAlert className="size-3.5" /> Plan feature managed by Admin
              </Badge>
            )}
          </div>
        </div>

        {/* 2. Status & Sync Telemetry */}
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

        {/* 3. On-Site Secret Unlock Gestures */}
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="bg-secondary/40 rounded-xl p-3.5">
            <p className="text-foreground flex items-center gap-1.5 text-sm font-medium">
              <Hand className="size-4" aria-hidden="true" /> Touch screen / Tablet
            </p>
            <p className="text-muted-foreground mt-1 text-sm">
              Tap any <span className="text-foreground font-semibold">corner 7 times</span> within 3 seconds, then enter 4-digit PIN.
            </p>
          </div>
          <div className="bg-secondary/40 rounded-xl p-3.5">
            <p className="text-foreground flex items-center gap-1.5 text-sm font-medium">
              <Gamepad2 className="size-4" aria-hidden="true" /> TV with Remote
            </p>
            <p className="text-muted-foreground mt-1 text-sm">
              Press <span className="text-foreground font-semibold">Up, Up, Down, Down, OK</span>, then enter 4-digit PIN.
            </p>
          </div>
        </div>

        {/* 4. PIN Management Controls */}
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
      </div>
    </details>
  )
}
