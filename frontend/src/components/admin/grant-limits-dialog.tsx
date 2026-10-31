'use client'

import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { adminApi } from '@/lib/api'
import { FEATURES } from '@/components/admin/admin-ui'
import type { TenantSummary } from '@/lib/types'

const GIB = 1024 ** 3
/** What the API takes for "no limit" on every one of the four numbers. */
const UNLIMITED = -1

/**
 * One workspace's limits and features, set by hand.
 *
 * The packages page edits a PACKAGE, which moves every tenant on it. This edits one tenant:
 * it mints them a private package behind the scenes, so raising one customer's screens or
 * switching on emergency alerts for them alone does not touch anybody else.
 *
 * Every number has an Unlimited box because the API's markers disagree underneath — screens
 * store no-limit as NULL while the others store 0 — and an operator should never have to
 * know that. The box sends -1 and the server puts it where it belongs.
 */
export function GrantLimitsDialog({
  tenant,
  onClose,
}: {
  tenant: TenantSummary
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const [error, setError] = useState('')

  // Seeded from what is enforced today, so opening and saving changes nothing.
  const [screens, setScreens] = useState(tenant.max_screens == null ? '' : String(tenant.max_screens))
  const [screensUnlimited, setScreensUnlimited] = useState(tenant.max_screens == null)
  const [ads, setAds] = useState(tenant.max_ad_slots == null ? '' : String(tenant.max_ad_slots))
  const [adsUnlimited, setAdsUnlimited] = useState(tenant.max_ad_slots == null)
  const [clients, setClients] = useState(tenant.max_clients == null ? '' : String(tenant.max_clients))
  const [clientsUnlimited, setClientsUnlimited] = useState(tenant.max_clients == null)
  const [storageGib, setStorageGib] = useState(
    tenant.storage_quota_bytes ? String(Math.round(tenant.storage_quota_bytes / GIB)) : '',
  )
  const [storageUnlimited, setStorageUnlimited] = useState(!tenant.storage_quota_bytes)
  const [features, setFeatures] = useState<Record<string, boolean>>(tenant.feature_flags ?? {})

  const save = useMutation({
    mutationFn: () => {
      const number = (raw: string, unlimited: boolean, scale = 1) => {
        if (unlimited) return UNLIMITED
        const parsed = Number(raw)
        // Blank or nonsense leaves that limit exactly as it is, rather than silently
        // setting it to zero — which for screens would take every TV off the tenant.
        return raw.trim() === '' || Number.isNaN(parsed) || parsed < 0 ? undefined : parsed * scale
      }
      return adminApi.grantLimits(tenant.id, {
        max_screens: number(screens, screensUnlimited),
        max_ad_slots: number(ads, adsUnlimited),
        max_clients: number(clients, clientsUnlimited),
        max_storage_bytes: number(storageGib, storageUnlimited, GIB),
        features,
      })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin', 'tenants'] })
      queryClient.invalidateQueries({ queryKey: ['admin', 'tenant', tenant.id] })
      onClose()
    },
    onError: (e: Error) => setError(e.message),
  })

  const limit = (
    label: string,
    used: number | undefined,
    value: string,
    setValue: (v: string) => void,
    unlimited: boolean,
    setUnlimited: (v: boolean) => void,
    suffix?: string,
  ) => (
    <div className="space-y-1.5">
      <div className="flex items-baseline justify-between">
        <label className="text-xs font-medium text-foreground">{label}</label>
        {used != null && <span className="text-[11px] text-muted-foreground">{used} in use</span>}
      </div>
      <div className="flex items-center gap-2">
        <input
          type="number"
          min={0}
          value={unlimited ? '' : value}
          disabled={unlimited}
          onChange={(e) => setValue(e.target.value)}
          placeholder={unlimited ? 'No limit' : 'Leave blank to keep'}
          className="w-full rounded-lg border border-border bg-muted px-3 py-2 text-sm text-foreground disabled:opacity-40"
        />
        {suffix && <span className="text-xs text-muted-foreground">{suffix}</span>}
        <label className="flex shrink-0 items-center gap-1.5 text-xs text-muted-foreground">
          <input
            type="checkbox"
            checked={unlimited}
            onChange={(e) => setUnlimited(e.target.checked)}
            className="size-3.5 accent-violet-500"
          />
          Unlimited
        </label>
      </div>
    </div>
  )

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/60 p-4" role="dialog" aria-modal="true">
      <div className="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-2xl border border-border bg-card p-5">
        <h2 className="text-lg font-semibold text-foreground">Limits &amp; features</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          For <span className="text-foreground">{tenant.name}</span> only. This does not change
          the {tenant.plan_name ?? 'package'} anyone else is on.
        </p>

        <div className="mt-5 space-y-4">
          {limit('Screens (TVs)', tenant.screens_count, screens, setScreens, screensUnlimited, setScreensUnlimited)}
          {limit('Ad slots', tenant.ad_slots_used, ads, setAds, adsUnlimited, setAdsUnlimited)}
          {limit('Clients', tenant.clients_used, clients, setClients, clientsUnlimited, setClientsUnlimited)}
          {limit('Storage', undefined, storageGib, setStorageGib, storageUnlimited, setStorageUnlimited, 'GB')}

          <div className="space-y-2 border-t border-border pt-4">
            <p className="text-xs font-medium text-foreground">Features</p>
            {FEATURES.map((feature) => (
              <label key={feature.key} className="flex items-center gap-2 text-sm text-muted-foreground">
                <input
                  type="checkbox"
                  checked={Boolean(features[feature.key])}
                  onChange={(e) => setFeatures({ ...features, [feature.key]: e.target.checked })}
                  className="size-4 accent-violet-500"
                />
                {feature.label}
                {!feature.enforced && (
                  <span
                    className="rounded bg-white/10 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-muted-foreground/80"
                    title="Sold as a commitment, not enforced by the software"
                  >
                    Not enforced
                  </span>
                )}
              </label>
            ))}
          </div>
        </div>

        {error && <p className="mt-4 rounded-lg bg-rose-500/10 p-2.5 text-xs text-rose-400">{error}</p>}

        <div className="mt-5 flex justify-end gap-2">
          <button
            onClick={onClose}
            className="rounded-lg border border-border px-4 py-2 text-sm text-muted-foreground hover:bg-muted"
          >
            Cancel
          </button>
          <button
            onClick={() => { setError(''); save.mutate() }}
            disabled={save.isPending}
            className="rounded-lg bg-violet-500 px-4 py-2 text-sm font-medium text-white hover:bg-violet-600 disabled:opacity-50"
          >
            {save.isPending ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  )
}
