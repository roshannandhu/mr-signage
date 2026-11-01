'use client'

import { useState } from 'react'
import Link from 'next/link'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  CalendarPlus, CalendarX, CheckCircle, Clock, Film, Gauge, MonitorPlay, RefreshCw, RotateCcw,
  Sliders, Trash2, Users, XCircle,
} from 'lucide-react'
import { adminApi } from '@/lib/api'
import type { TenantSummary } from '@/lib/types'
import { GrantLimitsDialog } from '@/components/admin/grant-limits-dialog'
import { useConfirm } from '@/components/admin/kit/confirm-dialog'
import {
  Feedback, PageHeader, QuotaBar, StatCard, StatusPill, formatBytes,
} from '@/components/admin/admin-ui'

/**
 * Every workspace, with the controls to police it.
 *
 * Two things this page previously got wrong, both now handled by going through adminApi:
 * it called the API with a bare fetch() and a hand-built auth header (so an expired
 * session showed an empty table rather than redirecting to login), and its stat cards
 * built Tailwind classes by interpolation (`bg-${color}-500/10`), which Tailwind v4 cannot
 * see -- so none of them were styled.
 */
const formatWindowEnd = (value?: string | null) =>
  value ? new Date(value).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }) : 'no end date'

/**
 * The paid window, in the two words an operator needs at a glance.
 *
 * Reads `subscription_state`, never `subscription_status`: the stored column says "active"
 * regardless of the calendar, which is precisely how a period that had run out went on
 * granting full access.
 */
function PlanWindow({ tenant }: { tenant: TenantSummary }) {
  const state = tenant.subscription_state
  if (!state) return <span className="text-xs text-muted-foreground">—</span>

  const tone =
    state === 'expired' ? 'border-rose-500/20 bg-rose-500/10 text-rose-700 dark:text-rose-400'
      : state === 'grace' ? 'border-amber-500/20 bg-amber-500/10 text-amber-700 dark:text-amber-400'
        : 'border-emerald-500/20 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400'
  const label = state === 'expired' ? 'Ended' : state === 'grace' ? 'Grace' : 'Running'

  return (
    <div className="space-y-1">
      <span className={`inline-block rounded-md border px-2 py-0.5 text-[11px] ${tone}`}>{label}</span>
      <p className="text-[11px] text-muted-foreground">
        {tenant.current_period_end ? `to ${formatWindowEnd(tenant.current_period_end)}` : 'no end date set'}
      </p>
    </div>
  )
}

/**
 * What this tenant's package actually grants, as initials.
 *
 * The console could show an operator a workspace's screens, ad slots and storage but never
 * its features -- the one part of a package that cannot be read off the numbers, and the
 * part they are most often asked to change for a single customer.
 */
function Features({ tenant }: { tenant: TenantSummary }) {
  const on = Object.entries(tenant.feature_flags ?? {}).filter(([, enabled]) => enabled)
  if (on.length === 0) return <span className="text-xs text-muted-foreground">None</span>
  return (
    <div className="flex flex-wrap gap-1">
      {on.map(([key]) => (
        <span
          key={key}
          title={key.replace(/_/g, ' ')}
          className="rounded border border-border bg-accent px-1.5 py-0.5 text-[10px] uppercase text-foreground"
        >
          {key.split('_').map((word) => word[0]).join('')}
        </span>
      ))}
    </div>
  )
}

export default function AdminTenantsPage() {
  const queryClient = useQueryClient()
  const confirm = useConfirm()
  const [search, setSearch] = useState('')
  const [quotaFor, setQuotaFor] = useState<TenantSummary | null>(null)
  const [grantFor, setGrantFor] = useState<TenantSummary | null>(null)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  const { data: tenants = [], isLoading, isFetching } = useQuery({
    queryKey: ['admin', 'tenants'],
    queryFn: () => adminApi.listTenants(),
  })
  const { data: packages = [] } = useQuery({ queryKey: ['admin', 'packages'], queryFn: adminApi.listPackages })

  const done = (text: string) => {
    setError('')
    setMessage(text)
    queryClient.invalidateQueries({ queryKey: ['admin', 'tenants'] })
  }
  const failed = (e: Error) => { setMessage(''); setError(e.message) }

  const setStatus = useMutation({
    mutationFn: ({ id, action, reason }: { id: number; action: 'suspend' | 'reinstate'; reason?: string }) =>
      action === 'suspend' ? adminApi.suspendTenant(id, reason) : adminApi.reinstateTenant(id),
    onSuccess: (tenant) => done(`${tenant.name} is now ${tenant.status}.`),
    onError: failed,
  })

  const setPlanWindow = useMutation({
    mutationFn: ({ id, body, reason }: { id: number; body: { extend_days?: number; status?: 'active' | 'expired' }; reason?: string }) =>
      adminApi.updateSubscription(id, body, reason),
    onSuccess: (tenant) => done(
      tenant.subscription_state === 'expired'
        ? `${tenant.name}'s plan has been ended. Their screens now show the demo reel.`
        : `${tenant.name}'s plan runs until ${formatWindowEnd(tenant.current_period_end)}.`,
    ),
    onError: failed,
  })

  // Removal is reversible for 30 days, so the confirmation asks for the workspace name
  // rather than a click: everything in it is destroyed at the end of that window, and this
  // is the last point at which reading the name carefully still helps.
  const setRemoved = useMutation({
    mutationFn: ({ id, action, reason }: { id: number; action: 'remove' | 'restore'; reason?: string }) =>
      action === 'remove' ? adminApi.removeTenant(id, reason) : adminApi.restoreTenant(id),
    onSuccess: (tenant) => done(
      tenant.deleted_at
        ? `${tenant.name} has been removed. Everything in it is deleted for good on ${formatWindowEnd(tenant.purge_at)}.`
        : `${tenant.name} has been restored.`,
    ),
    onError: failed,
  })

  const deleteNow = useMutation({
    mutationFn: ({ id, name, reason }: { id: number; name: string; reason?: string }) =>
      adminApi.deleteTenantPermanently(id, name, reason),
    onSuccess: (report) => done(`${report.name} has been deleted permanently.`),
    onError: failed,
  })

  const saveQuota = useMutation({
    mutationFn: ({ id, body }: { id: number; body: { plan_id?: number; max_screens: number; max_ad_slots: number } }) =>
      adminApi.updateQuota(id, body),
    onSuccess: (tenant) => { setQuotaFor(null); done(`Limits updated for ${tenant.name}.`) },
    onError: failed,
  })

  const filtered = tenants.filter((t) => {
    const q = search.trim().toLowerCase()
    if (!q) return true
    return [t.name, t.slug, t.owner_email ?? ''].some((v) => v.toLowerCase().includes(q))
  })

  const totals = {
    active: tenants.filter((t) => t.status === 'active' && !t.deleted_at).length,
    pending: tenants.filter((t) => t.status === 'pending_approval').length,
    screens: tenants.reduce((s, t) => s + t.screens_count, 0),
    online: tenants.reduce((s, t) => s + t.online_screens_count, 0),
    ads: tenants.reduce((s, t) => s + t.ad_slots_used, 0),
  }

  return (
    <div className="space-y-6 p-6 text-foreground lg:p-8">
      <PageHeader title="All Tenants" description="Limits, status and usage for every workspace">
        <button
          onClick={() => queryClient.invalidateQueries({ queryKey: ['admin', 'tenants'] })}
          className="flex items-center gap-2 rounded-xl border border-border bg-muted px-4 py-2 text-sm text-muted-foreground transition-all hover:bg-muted"
        >
          <RefreshCw className={`size-3.5 ${isFetching ? 'animate-spin' : ''}`} />
          Refresh
        </button>
      </PageHeader>

      <Feedback ok={message} error={error} />

      {grantFor && (
        <GrantLimitsDialog
          tenant={grantFor}
          onClose={() => { setGrantFor(null); done(`Limits updated for ${grantFor.name}.`) }}
        />
      )}

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard label="Active workspaces" value={totals.active} icon={CheckCircle} accent="emerald" />
        <StatCard label="Pending approvals" value={totals.pending} icon={Clock} accent="amber" />
        <StatCard label="TVs online (of paired)" value={`${totals.online} / ${totals.screens}`} icon={MonitorPlay} accent="violet" />
        <StatCard label="Active ad placements" value={totals.ads} icon={Film} accent="cyan" />
      </div>

      <input
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        placeholder="Search by workspace, slug or owner email…"
        className="w-full rounded-xl border border-border bg-muted px-4 py-2.5 text-sm text-foreground placeholder:text-muted-foreground outline-none focus:border-ring"
      />

      <section className="overflow-hidden rounded-2xl border border-border bg-card">
        <div className="flex items-center gap-2 border-b border-border p-4 text-sm font-semibold">
          <Users className="size-4 text-foreground" />
          {filtered.length} of {tenants.length} tenants
        </div>

        {isLoading ? (
          <p className="p-10 text-center text-sm text-muted-foreground">Loading…</p>
        ) : (
          <div className="overflow-x-auto">
            {/* min-w keeps the nine columns readable and lets the wrapper scroll, rather
                than squeezing them into 375px until every cell wraps to three lines. */}
            <table className="w-full min-w-[1100px] text-sm">
              <thead className="border-b border-border bg-muted text-xs uppercase tracking-wider text-muted-foreground">
                <tr>
                  <th className="p-4 pl-5 text-left">Workspace</th>
                  <th className="p-4 text-left">Owner</th>
                  <th className="p-4 text-left">Package</th>
                  <th className="p-4 text-left">Status</th>
                  <th className="p-4 text-left">Plan window</th>
                  <th className="p-4 text-left">Features</th>
                  <th className="p-4 text-left">Screens</th>
                  <th className="p-4 text-left">Ad slots</th>
                  <th className="p-4 text-left">Storage</th>
                  <th className="p-4 pr-5 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {filtered.map((tenant) => (
                  <tr key={tenant.id} className="transition-colors hover:bg-muted">
                    <td className="p-4 pl-5">
                      <Link href={`/admin/tenants/${tenant.id}`} className="font-semibold text-foreground hover:underline">
                        {tenant.name}
                      </Link>
                      <p className="font-mono text-xs text-muted-foreground">{tenant.slug}</p>
                    </td>
                    <td className="p-4 font-mono text-xs text-emerald-700 dark:text-emerald-400">{tenant.owner_email ?? '—'}</td>
                    <td className="p-4 text-xs text-muted-foreground">{tenant.plan_name ?? '—'}</td>
                    <td className="p-4">
                      {tenant.deleted_at ? (
                        <span className="inline-block rounded-md border border-rose-500/30 bg-rose-500/10 px-2 py-0.5 text-[11px] text-rose-700 dark:text-rose-400">
                          Removed
                        </span>
                      ) : (
                        <StatusPill status={tenant.status} />
                      )}
                    </td>
                    <td className="p-4"><PlanWindow tenant={tenant} /></td>
                    <td className="p-4"><Features tenant={tenant} /></td>
                    <td className="w-32 p-4"><QuotaBar used={tenant.screens_count} max={tenant.max_screens} /></td>
                    <td className="w-32 p-4"><QuotaBar used={tenant.ad_slots_used} max={tenant.max_ad_slots} /></td>
                    <td className="p-4 text-xs text-muted-foreground">
                      {/* 0 is the unlimited marker, so rendering it through formatBytes
                          would read "0 B" — the exact opposite of what it means. */}
                      {formatBytes(tenant.storage_used_bytes)} /{' '}
                      {tenant.storage_quota_bytes ? formatBytes(tenant.storage_quota_bytes) : 'No limit'}
                    </td>
                    <td className="p-4 pr-5">
                      <div className="flex items-center justify-end gap-2">
                        {/* A removed workspace is counting down to being destroyed. Every
                            other control would be editing something that is about to stop
                            existing, so only the one that calls it off is offered. */}
                        {tenant.deleted_at ? (
                          <>
                            <span className="text-[11px] text-rose-700 dark:text-rose-400">
                              Deleted for good on {formatWindowEnd(tenant.purge_at)}
                            </span>
                            <button
                              onClick={() => setRemoved.mutate({ id: tenant.id, action: 'restore' })}
                              disabled={setRemoved.isPending}
                              className="flex items-center gap-1.5 rounded-lg border border-emerald-500/20 bg-emerald-500/10 px-3 py-1.5 text-xs text-emerald-700 dark:text-emerald-400 transition-all hover:bg-emerald-500/20 disabled:opacity-50"
                            >
                              <RotateCcw className="size-3" />
                              Restore
                            </button>
                            <button
                              onClick={async () => {
                                const result = await confirm({
                                  title: `Delete ${tenant.name} permanently?`,
                                  description: 'Every screen, advert, playlist, user and file in this workspace is erased from the database and storage. This cannot be undone.',
                                  level: 3,
                                  confirmText: tenant.name,
                                  destructive: true,
                                  confirmLabel: 'Delete permanently',
                                })
                                if (result) deleteNow.mutate({ id: tenant.id, name: tenant.name, reason: result.reason })
                              }}
                              disabled={deleteNow.isPending}
                              title="Erase this workspace from the database now instead of waiting 30 days"
                              className="flex items-center gap-1.5 rounded-lg border border-rose-500/30 bg-rose-500/20 px-3 py-1.5 text-xs text-rose-700 dark:text-rose-300 transition-all hover:bg-rose-500/30 disabled:opacity-50"
                            >
                              <Trash2 className="size-3" />
                              {deleteNow.isPending ? 'Deleting…' : 'Delete now'}
                            </button>
                          </>
                        ) : (
                          <>
                        <button
                          onClick={() => { setQuotaFor(tenant); setMessage(''); setError('') }}
                          className="flex items-center gap-1.5 rounded-lg border border-border bg-accent px-3 py-1.5 text-xs text-foreground transition-all hover:bg-foreground/10"
                        >
                          <Gauge className="size-3" />
                          Limits
                        </button>
                        <button
                          onClick={() => { setGrantFor(tenant); setMessage(''); setError('') }}
                          title="Set this tenant's screens, ads, clients, storage and features"
                          className="flex items-center gap-1.5 rounded-lg border border-fuchsia-500/20 bg-fuchsia-500/10 px-3 py-1.5 text-xs text-fuchsia-700 dark:text-fuchsia-400 transition-all hover:bg-fuchsia-500/20"
                        >
                          <Sliders className="size-3" />
                          Grant
                        </button>
                        <button
                          onClick={() => setPlanWindow.mutate({ id: tenant.id, body: { extend_days: 30 } })}
                          disabled={setPlanWindow.isPending}
                          title="Add 30 days to this workspace's paid window"
                          className="flex items-center gap-1.5 rounded-lg border border-sky-500/20 bg-sky-500/10 px-3 py-1.5 text-xs text-sky-700 dark:text-sky-400 transition-all hover:bg-sky-500/20 disabled:opacity-50"
                        >
                          <CalendarPlus className="size-3" />
                          +30 days
                        </button>
                        {tenant.subscription_state === 'active' && (
                          <button
                            onClick={async () => {
                              const result = await confirm({
                                title: `End ${tenant.name}'s plan now?`,
                                description: 'Their adverts stop and their screens show the demo reel. Nothing is deleted, and extending the plan puts it all back.',
                                impact: `${tenant.screens_count} TV${tenant.screens_count === 1 ? '' : 's'} switch to the demo reel.`,
                                level: 2,
                                confirmLabel: 'End plan',
                              })
                              if (result) setPlanWindow.mutate({ id: tenant.id, body: { status: 'expired' }, reason: result.reason })
                            }}
                            disabled={setPlanWindow.isPending}
                            className="flex items-center gap-1.5 rounded-lg border border-amber-500/20 bg-amber-500/10 px-3 py-1.5 text-xs text-amber-700 dark:text-amber-400 transition-all hover:bg-amber-500/20 disabled:opacity-50"
                          >
                            <CalendarX className="size-3" />
                            End plan
                          </button>
                        )}
                        {tenant.status === 'suspended' ? (
                          <button
                            onClick={() => setStatus.mutate({ id: tenant.id, action: 'reinstate' })}
                            className="flex items-center gap-1.5 rounded-lg border border-emerald-500/20 bg-emerald-500/10 px-3 py-1.5 text-xs text-emerald-700 dark:text-emerald-400 transition-all hover:bg-emerald-500/20"
                          >
                            <CheckCircle className="size-3" />
                            Reinstate
                          </button>
                        ) : (
                          <button
                            onClick={async () => {
                              const result = await confirm({
                                title: `Block ${tenant.name}?`,
                                description: 'Their dashboard and API access stop immediately. Reinstating gives it back.',
                                level: 2,
                                destructive: true,
                                confirmLabel: 'Block workspace',
                              })
                              if (result) setStatus.mutate({ id: tenant.id, action: 'suspend', reason: result.reason })
                            }}
                            className="flex items-center gap-1.5 rounded-lg border border-rose-500/20 bg-rose-500/10 px-3 py-1.5 text-xs text-rose-700 dark:text-rose-400 transition-all hover:bg-rose-500/20"
                          >
                            <XCircle className="size-3" />
                            Block
                          </button>
                        )}
                        <button
                          onClick={async () => {
                            const result = await confirm({
                              title: `Remove ${tenant.name}?`,
                              description: 'Their access stops now, and in 30 days every screen, advert, playlist and file in this workspace is deleted permanently. It can be restored at any point before then.',
                              level: 3,
                              confirmText: tenant.name,
                              destructive: true,
                              confirmLabel: 'Remove workspace',
                            })
                            if (result) setRemoved.mutate({ id: tenant.id, action: 'remove', reason: result.reason })
                          }}
                          disabled={setRemoved.isPending}
                          title="Remove this workspace and delete everything in it after 30 days"
                          className="flex items-center gap-1.5 rounded-lg border border-rose-500/30 bg-rose-500/20 px-3 py-1.5 text-xs text-rose-700 dark:text-rose-300 transition-all hover:bg-rose-500/30 disabled:opacity-50"
                        >
                          <Trash2 className="size-3" />
                          Remove
                        </button>
                          </>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {quotaFor && (
        <QuotaDialog
          tenant={quotaFor}
          packages={packages}
          saving={saveQuota.isPending}
          onCancel={() => setQuotaFor(null)}
          onSave={(body) => saveQuota.mutate({ id: quotaFor.id, body })}
        />
      )}
    </div>
  )
}

function QuotaDialog({
  tenant, packages, saving, onCancel, onSave,
}: {
  tenant: TenantSummary
  // max_screens is nullable now: null is a package with NO screen limit, which is a
  // different thing from 0 (a package granting none) and must not be rendered as one.
  packages: { id: number; name: string; max_screens: number | null; max_ad_slots: number; is_active: boolean }[]
  saving: boolean
  onCancel: () => void
  onSave: (body: { plan_id?: number; max_screens: number; max_ad_slots: number }) => void
}) {
  const [planId, setPlanId] = useState<number | ''>(tenant.plan_id ?? '')
  // The OVERRIDE, not the effective limit. Seeding these from the effective value would
  // make merely opening and saving the dialog pin the tenant to their package's current
  // number, after which they would no longer follow that package.
  const [screens, setScreens] = useState(tenant.max_screens_override)
  const [ads, setAds] = useState(tenant.max_ad_slots_override)

  // Picking a package pre-fills its limits, but they stay editable: the override is the
  // point, so one tenant can be raised without moving everyone on that package.
  const choosePackage = (value: number | '') => {
    setPlanId(value)
    const pkg = packages.find((p) => p.id === value)
    // A package with no screen limit pre-fills 0, which on THIS field means "no override,
    // follow the package" — so the tenant keeps the package's unlimited rather than being
    // pinned to a number. Use the Grant dialog to give one tenant their own unlimited.
    if (pkg) { setScreens(pkg.max_screens ?? 0); setAds(pkg.max_ad_slots) }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm" onClick={onCancel}>
      <div className="w-full max-w-md space-y-5 rounded-3xl border border-border bg-card p-6 shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center gap-3">
          <div className="grid size-10 place-items-center rounded-2xl border border-border bg-accent">
            <Gauge className="size-5 text-foreground" />
          </div>
          <div>
            <h2 className="font-bold text-foreground">Edit limits</h2>
            <p className="text-xs text-muted-foreground">{tenant.name}</p>
          </div>
        </div>

        <label className="block">
          <span className="mb-1.5 block text-xs font-semibold text-muted-foreground">Package</span>
          <select
            value={planId}
            onChange={(e) => choosePackage(e.target.value === '' ? '' : Number(e.target.value))}
            className="w-full rounded-xl border border-border bg-muted px-4 py-2.5 text-sm text-foreground outline-none focus:border-ring"
          >
            <option value="">No package</option>
            {packages.filter((p) => p.is_active || p.id === tenant.plan_id).map((p) => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
        </label>

        <label className="block">
          <span className="mb-1.5 block text-xs font-semibold text-muted-foreground">
            Max TV screens <span className="font-normal text-muted-foreground">(0 = follow the package)</span>
          </span>
          <input
            type="number" min={0} value={screens} onChange={(e) => setScreens(Number(e.target.value) || 0)}
            className="w-full rounded-xl border border-border bg-muted px-4 py-2.5 text-sm text-foreground outline-none focus:border-ring"
          />
          <p className="mt-1 text-xs text-muted-foreground">
            Currently using {tenant.screens_count}. For no limit at all, use Limits &amp; features.
          </p>
        </label>

        <label className="block">
          <span className="mb-1.5 block text-xs font-semibold text-muted-foreground">
            Max ad slots <span className="font-normal text-muted-foreground">(0 = follow the package)</span>
          </span>
          <input
            type="number" min={0} value={ads} onChange={(e) => setAds(Number(e.target.value) || 0)}
            className="w-full rounded-xl border border-border bg-muted px-4 py-2.5 text-sm text-foreground outline-none focus:border-ring"
          />
          <p className="mt-1 text-xs text-muted-foreground">Currently using {tenant.ad_slots_used}.</p>
        </label>

        <div className="flex gap-3 pt-1">
          <button
            onClick={() => onSave({ ...(planId === '' ? {} : { plan_id: planId }), max_screens: screens, max_ad_slots: ads })}
            disabled={saving}
            className="flex-1 rounded-xl bg-primary py-2.5 text-sm font-semibold text-primary-foreground transition-all hover:bg-primary/90 disabled:opacity-50"
          >
            {saving ? 'Saving…' : 'Save limits'}
          </button>
          <button onClick={onCancel} className="rounded-xl border border-border bg-muted px-5 py-2.5 text-sm text-muted-foreground transition-all hover:bg-muted">
            Cancel
          </button>
        </div>
      </div>
    </div>
  )
}
