'use client'

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, CheckCircle, KeyRound } from 'lucide-react'
import { adminApi } from '@/lib/api'

/**
 * One PIN that opens any screen which has not been given its own.
 *
 * A provisioned panel runs in kiosk: Home and Back are swallowed, which is the point. If that
 * screen has no maintenance PIN there is no way into it at all, and the only remaining move
 * is a factory reset — which unpairs the panel and loses its playlist. The per-screen field is
 * optional and usually skipped, so without a platform-wide fallback that state is the default
 * rather than the exception.
 *
 * Shown in full rather than masked. What protects a screen is physical access to it, not
 * whether the operator who set the code can read it back; a hidden PIN becomes a sticky note
 * on the bezel.
 */
export function MaintenancePinCard() {
  const queryClient = useQueryClient()
  const { data } = useQuery({ queryKey: ['admin', 'maintenance-pin'], queryFn: adminApi.getMaintenancePin })

  const [pin, setPin] = useState('')
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  const save = useMutation({
    mutationFn: (value: string) => adminApi.setMaintenancePin(value),
    onSuccess: (result) => {
      setError('')
      setPin('')
      setMessage(
        result.pin
          ? `Saved. Every screen without its own PIN now opens with ${result.pin}.`
          : 'Cleared. Screens without their own PIN can no longer be opened — set one.',
      )
      queryClient.invalidateQueries({ queryKey: ['admin', 'maintenance-pin'] })
    },
    onError: (e: Error) => { setMessage(''); setError(e.message) },
  })

  const valid = /^\d{4}$/.test(pin)

  return (
    <section className="rounded-2xl border border-border bg-card p-5">
      <div className="flex items-start gap-3">
        <div className="grid size-9 shrink-0 place-items-center rounded-xl border border-border bg-accent">
          <KeyRound className="size-4 text-foreground" />
        </div>
        <div className="min-w-0 flex-1">
          <h2 className="font-semibold text-foreground">Universal maintenance PIN</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Opens any screen that has not been given a PIN of its own. A kiosked screen with no
            PIN cannot be reached at all — the only way back in is a factory reset, which
            unpairs it.
          </p>
        </div>
        <span
          className={`shrink-0 rounded-lg border px-2.5 py-1 text-xs ${
            data?.pin
              ? 'border-emerald-500/20 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400'
              : 'border-amber-500/20 bg-amber-500/10 text-amber-700 dark:text-amber-400'
          }`}
        >
          {data?.pin ? `In use: ${data.pin}` : 'Not set'}
        </span>
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <input
          value={pin}
          onChange={(e) => setPin(e.target.value.replace(/\D/g, '').slice(0, 4))}
          placeholder={data?.pin ? 'new 4-digit PIN' : 'e.g. 4821'}
          inputMode="numeric"
          className="w-40 rounded-lg border border-border bg-muted px-3 py-2 text-center font-mono text-lg tracking-[0.3em] text-foreground placeholder:font-sans placeholder:text-sm placeholder:tracking-normal placeholder:text-muted-foreground/60"
        />
        <button
          onClick={() => { setMessage(''); setError(''); save.mutate(pin) }}
          disabled={!valid || save.isPending}
          className="rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-40"
        >
          {save.isPending ? 'Saving…' : 'Set PIN'}
        </button>
        {data?.pin && (
          <button
            onClick={() => { setMessage(''); setError(''); save.mutate('') }}
            disabled={save.isPending}
            className="rounded-lg border border-border px-4 py-2 text-sm text-muted-foreground hover:bg-muted disabled:opacity-40"
          >
            Clear
          </button>
        )}
      </div>

      <p className="mt-2 text-xs text-muted-foreground/70">
        Tenants can see this on each screen&apos;s page, together with the gesture that reveals
        the prompt — 7 corner taps on a touch screen, or Up Up Down Down OK on a remote. A
        screen keeps the code locally, so it still opens with no internet.
      </p>

      {message && (
        <p className="mt-3 flex items-start gap-2 rounded-lg bg-emerald-500/10 p-2.5 text-xs text-emerald-700 dark:text-emerald-400">
          <CheckCircle className="mt-0.5 size-3.5 shrink-0" />{message}
        </p>
      )}
      {error && (
        <p className="mt-3 flex items-start gap-2 rounded-lg bg-rose-500/10 p-2.5 text-xs text-rose-700 dark:text-rose-400">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />{error}
        </p>
      )}
    </section>
  )
}
