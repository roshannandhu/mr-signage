'use client'

import Link from 'next/link'
import { useQuery } from '@tanstack/react-query'
import { CheckCircle, Clock, Film, MonitorPlay, ShieldCheck, Users, XCircle } from 'lucide-react'
import { adminApi } from '@/lib/api'
import { Feedback, PageHeader, StatCard, StatusPill } from '@/components/admin/admin-ui'

/**
 * Where a platform operator lands after signing in.
 *
 * This route had no page file at all, so /admin -- the first link in its own sidebar --
 * was a 404, and login never sent anyone here anyway.
 */
export default function AdminOverviewPage() {
  const { data: tenants = [], isLoading, error } = useQuery({
    queryKey: ['admin', 'tenants'],
    queryFn: () => adminApi.listTenants(),
  })

  const counts = {
    active: tenants.filter((t) => t.status === 'active').length,
    pending: tenants.filter((t) => t.status === 'pending_approval').length,
    suspended: tenants.filter((t) => t.status === 'suspended').length,
  }
  const screens = tenants.reduce((sum, t) => sum + t.screens_count, 0)
  const online = tenants.reduce((sum, t) => sum + t.online_screens_count, 0)
  const ads = tenants.reduce((sum, t) => sum + t.ad_slots_used, 0)
  const pending = tenants.filter((t) => t.status === 'pending_approval')

  return (
    <div className="space-y-6 p-6 text-foreground lg:p-8">
      <PageHeader title="Platform Overview" description="Every workspace, screen and booking on this deployment" />

      <Feedback error={error ? (error as Error).message : undefined} />

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard label="Active workspaces" value={counts.active} icon={CheckCircle} accent="emerald" />
        <StatCard label="Awaiting approval" value={counts.pending} icon={Clock} accent="amber" />
        <StatCard label="TVs online (of paired)" value={`${online} / ${screens}`} icon={MonitorPlay} accent="violet" />
        <StatCard label="Active ad placements" value={ads} icon={Film} accent="cyan" />
      </div>

      {counts.suspended > 0 && (
        <div className="flex items-center gap-2 rounded-xl border border-rose-500/20 bg-rose-500/5 p-3 text-sm text-rose-700 dark:text-rose-300">
          <XCircle className="size-4 shrink-0" />
          {counts.suspended} workspace{counts.suspended === 1 ? ' is' : 's are'} suspended and cannot access the API.
        </div>
      )}

      <section className="overflow-hidden rounded-2xl border border-border bg-card">
        <div className="flex items-center justify-between border-b border-border p-4">
          <span className="flex items-center gap-2 text-sm font-semibold">
            <ShieldCheck className="size-4 text-amber-700 dark:text-amber-400" />
            Approvals queue
          </span>
          <Link href="/admin/approvals" className="text-xs text-foreground hover:text-foreground">
            Open queue →
          </Link>
        </div>

        {isLoading ? (
          <p className="p-8 text-center text-sm text-muted-foreground">Loading…</p>
        ) : pending.length === 0 ? (
          <p className="p-8 text-center text-sm text-muted-foreground">Nothing waiting. Every workspace has been reviewed.</p>
        ) : (
          <ul className="divide-y divide-border">
            {pending.slice(0, 5).map((tenant) => (
              <li key={tenant.id} className="flex items-center justify-between gap-4 p-4">
                <div className="min-w-0">
                  <p className="truncate font-semibold">{tenant.name}</p>
                  <p className="truncate font-mono text-xs text-muted-foreground">{tenant.owner_email ?? '—'}</p>
                </div>
                <StatusPill status={tenant.status} />
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="overflow-hidden rounded-2xl border border-border bg-card">
        <div className="flex items-center justify-between border-b border-border p-4">
          <span className="flex items-center gap-2 text-sm font-semibold">
            <Users className="size-4 text-foreground" />
            {tenants.length} registered tenants
          </span>
          <Link href="/admin/tenants" className="text-xs text-foreground hover:text-foreground">
            Manage all →
          </Link>
        </div>
        <ul className="divide-y divide-border">
          {tenants.slice(0, 6).map((tenant) => (
            <li key={tenant.id} className="flex items-center justify-between gap-4 p-4">
              <Link href={`/admin/tenants/${tenant.id}`} className="min-w-0 hover:underline">
                <p className="truncate font-semibold">{tenant.name}</p>
                <p className="truncate text-xs text-muted-foreground">
                  {tenant.plan_name ?? 'No package'} · {tenant.screens_count} screens
                </p>
              </Link>
              <StatusPill status={tenant.status} />
            </li>
          ))}
        </ul>
      </section>
    </div>
  )
}
