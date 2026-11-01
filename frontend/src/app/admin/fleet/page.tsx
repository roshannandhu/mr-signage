'use client'

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Activity, AlertTriangle, CheckCircle, Download, MonitorPlay, RefreshCw, Rocket } from 'lucide-react'
import { adminApi, api } from '@/lib/api'
import type { AppRelease, FleetScreen } from '@/lib/types'
import { PageHeader, StatCard } from '@/components/admin/admin-ui'
import { MaintenancePinCard } from '@/components/admin/maintenance-pin-card'

/**
 * The whole fleet's version spread in one place.
 *
 * Per-tenant screen lists already show a single screen's version; this is the platform
 * operator's global view — which TVs are online, what build each is on, and how a rollout
 * is landing. Polls so a release can be watched propagating.
 */
export default function AdminFleetPage() {
  const queryClient = useQueryClient()
  const { data, isLoading, isFetching } = useQuery({
    queryKey: ['admin', 'fleet'],
    queryFn: adminApi.getFleet,
    refetchInterval: 15000,
  })

  // Only builds a screen can actually install: released, and carrying the digest the player
  // refuses to install without.
  const { data: releases } = useQuery({ queryKey: ['releases'], queryFn: api.getReleases })
  const installable = (releases ?? []).filter((r) => r.rollout_state === 'released' && r.sha256)

  const versions = data ? Object.entries(data.versions).sort((a, b) => b[1] - a[1]) : []
  const latest = data?.latest_version_name ?? null

  return (
    <div className="space-y-6 p-6 text-foreground lg:p-8">
      <PageHeader title="Fleet Versions" description="Every TV across every tenant — the build it runs and its update state">
        <button
          onClick={() => queryClient.invalidateQueries({ queryKey: ['admin', 'fleet'] })}
          className="flex items-center gap-2 rounded-xl border border-border bg-muted px-4 py-2 text-sm text-muted-foreground transition-all hover:bg-muted"
        >
          <RefreshCw className={`size-3.5 ${isFetching ? 'animate-spin' : ''}`} />
          Refresh
        </button>
      </PageHeader>

      <MaintenancePinCard />

      {/* Legacy login lets a TV call the API knowing only its device id, which is not a secret.
          It exists to keep older players alive, so it should be switched off once no TV needs it. */}
      {data?.legacy_device_auth && (
        <div
          role="status"
          className={`rounded-2xl border p-4 text-sm ${
            data.screens_without_secret === 0
              ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-800 dark:text-emerald-200'
              : 'border-amber-500/30 bg-amber-500/10 text-amber-800 dark:text-amber-200'
          }`}
        >
          <p className="font-semibold">
            {data.screens_without_secret === 0
              ? 'Legacy TV login is still on, but every TV now has its own secret.'
              : `Legacy TV login is on, and ${data.screens_without_secret} TV${data.screens_without_secret === 1 ? '' : 's'} still hold no device secret.`}
          </p>
          <p className="mt-1 opacity-80">
            {data.screens_without_secret === 0
              ? 'It is safe to switch it off: set ALLOW_LEGACY_DEVICE_AUTH=false on the server.'
              : 'Switching it off now would sign those TVs out. Update or re-pair them first; this count falls to 0 as they do.'}
          </p>
        </div>
      )}

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard label="Total TVs" value={data?.total ?? 0} icon={MonitorPlay} accent="violet" />
        <StatCard label="Online now" value={`${data?.online ?? 0} / ${data?.total ?? 0}`} icon={Activity} accent="emerald" />
        <StatCard
          label={latest ? `On latest (${latest})` : 'On latest'}
          value={data?.on_latest ?? 0}
          icon={CheckCircle}
          accent="cyan"
        />
        <StatCard label="Failed / rolled back" value={data?.failed ?? 0} icon={AlertTriangle} accent="amber" />
      </div>

      {/* Version spread */}
      <section className="rounded-2xl border border-border bg-card p-5">
        <div className="mb-3 flex items-center gap-2 text-sm font-semibold">
          <Rocket className="size-4 text-foreground" />
          Version spread
          {latest && <span className="ml-2 text-xs font-normal text-muted-foreground">latest released: <span className="text-emerald-700 dark:text-emerald-400">{latest}</span></span>}
          {data && data.updating > 0 && <span className="ml-auto text-xs font-normal text-amber-700 dark:text-amber-300">{data.updating} updating…</span>}
        </div>
        {versions.length === 0 ? (
          <p className="text-sm text-muted-foreground">No paired TVs yet.</p>
        ) : (
          <div className="space-y-2">
            {versions.map(([version, count]) => {
              const pct = data && data.total ? Math.round((count / data.total) * 100) : 0
              const isLatest = latest !== null && version === latest
              return (
                <div key={version} className="flex items-center gap-3">
                  <span className={`w-28 shrink-0 truncate font-mono text-xs ${isLatest ? 'text-emerald-700 dark:text-emerald-400' : 'text-muted-foreground'}`}>
                    {version}{isLatest && ' ✓'}
                  </span>
                  <div className="h-2 flex-1 overflow-hidden rounded-full bg-muted">
                    <div className={`h-full rounded-full ${isLatest ? 'bg-emerald-500' : 'bg-primary'}`} style={{ width: `${pct}%` }} />
                  </div>
                  <span className="w-10 shrink-0 text-right text-xs text-muted-foreground">{count}</span>
                </div>
              )
            })}
          </div>
        )}
      </section>

      {/* Per-TV table */}
      <section className="overflow-hidden rounded-2xl border border-border bg-card">
        <div className="flex items-center gap-2 border-b border-border p-4 text-sm font-semibold">
          <MonitorPlay className="size-4 text-foreground" />
          {data?.screens.length ?? 0} TVs
        </div>
        {isLoading ? (
          <p className="p-10 text-center text-sm text-muted-foreground">Loading…</p>
        ) : !data?.screens.length ? (
          <p className="p-10 text-center text-sm text-muted-foreground">No TVs paired across the fleet yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[1040px] text-sm">
              <thead className="border-b border-border bg-muted text-xs uppercase tracking-wider text-muted-foreground">
                <tr>
                  <th className="p-4 pl-5 text-left">TV</th>
                  <th className="p-4 text-left">Workspace</th>
                  <th className="p-4 text-left">Version</th>
                  <th className="p-4 text-left">Pin</th>
                  <th className="p-4 text-left">Update</th>
                  <th className="p-4 text-left">State</th>
                  <th className="p-4 text-left">Last seen</th>
                  <th className="p-4 pr-5 text-left">Manual update</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {data.screens.map((s) => (
                  <tr key={s.id} className="transition-colors hover:bg-muted">
                    <td className="p-4 pl-5 font-medium">{s.name || `Screen ${s.id}`}</td>
                    <td className="p-4 text-xs text-muted-foreground">{s.organization_name}</td>
                    <td className="p-4">
                      <span className={`font-mono text-xs ${latest && s.app_version === latest ? 'text-emerald-700 dark:text-emerald-400' : 'text-muted-foreground'}`}>
                        {s.app_version || '—'}
                      </span>
                    </td>
                    <td className="p-4 text-xs text-muted-foreground">{s.target_version_code ?? '—'}</td>
                    <td className="p-4"><UpdateChip screen={s} /></td>
                    <td className="p-4">
                      <span className={`inline-flex items-center gap-1.5 text-xs ${s.online ? 'text-emerald-700 dark:text-emerald-400' : 'text-muted-foreground'}`}>
                        <span className={`size-1.5 rounded-full ${s.online ? 'bg-emerald-400' : 'bg-muted'}`} />
                        {s.online ? 'Online' : 'Offline'}
                      </span>
                    </td>
                    <td className="p-4 text-xs text-muted-foreground">{s.last_seen ? new Date(s.last_seen).toLocaleString() : '—'}</td>
                    <td className="p-4 pr-5"><UpdateNowControl screen={s} releases={installable} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}

function UpdateChip({ screen }: { screen: FleetScreen }) {
  const s = screen.update_status
  if (s === 'failed' || s === 'rolled_back') {
    return <span className="rounded-full border border-rose-500/20 bg-rose-500/10 px-2 py-0.5 text-xs text-rose-700 dark:text-rose-400">{s === 'rolled_back' ? 'rolled back' : 'failed'}{screen.update_failure_count > 0 ? ` ×${screen.update_failure_count}` : ''}</span>
  }
  if (s === 'pending' || s === 'downloading' || s === 'installing') {
    return <span className="rounded-full border border-amber-500/20 bg-amber-500/10 px-2 py-0.5 text-xs text-amber-700 dark:text-amber-300">{s}…</span>
  }
  if (s === 'success') {
    return <span className="rounded-full border border-emerald-500/20 bg-emerald-500/10 px-2 py-0.5 text-xs text-emerald-700 dark:text-emerald-400">up to date</span>
  }
  return <span className="text-xs text-muted-foreground">—</span>
}

/**
 * Make one TV look for its update now.
 *
 * A screen normally updates itself when a release goes out, but one that failed, was
 * offline, or had its install prompt dismissed has nothing to tell it to try again short
 * of publishing yet another build. This is that lever, for a single screen: the latest
 * released build by default, or a specific one to pin it to.
 */
function UpdateNowControl({ screen, releases }: { screen: FleetScreen; releases: AppRelease[] }) {
  const queryClient = useQueryClient()
  const [choice, setChoice] = useState<string>('')
  const [note, setNote] = useState<{ tone: 'ok' | 'err'; text: string } | null>(null)

  const update = useMutation({
    mutationFn: () => adminApi.updateScreenNow(screen.id, choice ? Number(choice) : null),
    onSuccess: (result) => {
      const build = result.offered_version_name ?? 'the latest build'
      setNote({
        tone: 'ok',
        text: result.already_current
          ? `Already on ${build}`
          : result.online
            ? `Sent — installing ${build}`
            : `Queued — installs ${build} when it comes online`,
      })
      queryClient.invalidateQueries({ queryKey: ['admin', 'fleet'] })
    },
    onError: (e: Error) => setNote({ tone: 'err', text: e.message }),
  })

  return (
    <div className="flex min-w-[220px] flex-col gap-1">
      <div className="flex items-center gap-1.5">
        <select
          value={choice}
          onChange={(e) => { setChoice(e.target.value); setNote(null) }}
          aria-label={`Build for ${screen.name || `screen ${screen.id}`}`}
          className="rounded-lg border border-border bg-muted px-2 py-1.5 text-xs text-foreground"
        >
          <option value="">Latest</option>
          {releases.map((r) => (
            <option key={r.version_code} value={r.version_code}>{r.version_name}</option>
          ))}
        </select>
        <button
          onClick={() => { setNote(null); update.mutate() }}
          disabled={update.isPending}
          className="flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-40"
        >
          <Download className={`size-3.5 ${update.isPending ? 'animate-pulse' : ''}`} />
          {update.isPending ? 'Sending…' : 'Update now'}
        </button>
      </div>
      {note && (
        <span className={`text-[11px] ${note.tone === 'ok' ? 'text-emerald-700 dark:text-emerald-400' : 'text-rose-700 dark:text-rose-400'}`}>{note.text}</span>
      )}
    </div>
  )
}
