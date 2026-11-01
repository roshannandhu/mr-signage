'use client'

import { use, useState } from 'react'
import Link from 'next/link'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useRouter } from 'next/navigation'
import { ArrowLeft, Eye, FileVideo, Lock, LogIn, MonitorPlay, Palette, Users } from 'lucide-react'
import { toast } from 'sonner'
import { adminApi } from '@/lib/api'
import { useAuthStore } from '@/lib/store'
import { useConfirm } from '@/components/admin/kit/confirm-dialog'
import {
  Feedback, PageHeader, QuotaBar, StatusPill, formatBytes,
} from '@/components/admin/admin-ui'

type Tab = 'screens' | 'content' | 'users'

/**
 * One tenant's workspace, as the platform operator sees it.
 *
 * The tabs are a read-only inspection: an operator needs to SEE what a workspace contains
 * to support it. Changing it is a separate, deliberate act -- "Enter workspace" steps into
 * the tenant's own dashboard with every call scoped to them, so support work happens on the
 * real screens instead of through admin-only copies that would drift from them.
 *
 * Status and limits are still changed from the tenants list, which is where those belong.
 * Remote unlock is the one exception: it is deliberately an OPERATOR act on the tenant's
 * behalf, logged under the operator's own identity, not something "enter workspace" could
 * express the same way.
 */
export default function AdminTenantDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params)
  const tenantId = Number(id)
  const [tab, setTab] = useState<Tab>('screens')
  const router = useRouter()
  const queryClient = useQueryClient()
  const enterWorkspace = useAuthStore((state) => state.enterWorkspace)
  const confirm = useConfirm()

  const tenantQuery = useQuery({
    queryKey: ['admin', 'tenant', tenantId],
    queryFn: () => adminApi.getTenant(tenantId),
  })
  const playsQuery = useQuery({
    queryKey: ['admin', 'tenant', tenantId, 'plays'],
    queryFn: () => adminApi.getTenantPlays(tenantId),
  })
  const screensQuery = useQuery({
    queryKey: ['admin', 'tenant', tenantId, 'screens'],
    queryFn: () => adminApi.getTenantScreens(tenantId),
    enabled: tab === 'screens',
  })
  const contentQuery = useQuery({
    queryKey: ['admin', 'tenant', tenantId, 'content'],
    queryFn: () => adminApi.getTenantContent(tenantId),
    enabled: tab === 'content',
  })
  const usersQuery = useQuery({
    queryKey: ['admin', 'tenant', tenantId, 'users'],
    queryFn: () => adminApi.getTenantUsers(tenantId),
    enabled: tab === 'users',
  })
  const kioskEventsQuery = useQuery({
    queryKey: ['admin', 'tenant', tenantId, 'kiosk-events'],
    queryFn: () => adminApi.getKioskUnlockEvents(tenantId),
    enabled: tab === 'screens',
  })
  const remoteUnlock = useMutation({
    mutationFn: (screenId: number) => adminApi.remoteUnlockScreen(tenantId, screenId),
    onSuccess: (result) => {
      toast.success(result.queued ? 'Unlock queued -- takes effect at the screen’s next sync' : 'Could not queue the unlock; try again')
      queryClient.invalidateQueries({ queryKey: ['admin', 'tenant', tenantId, 'kiosk-events'] })
    },
    onError: (error: Error) => toast.error(error.message),
  })

  const tenant = tenantQuery.data

  return (
    <div className="space-y-6 p-6 text-foreground lg:p-8">
      <Link href="/admin/tenants" className="inline-flex items-center gap-2 text-xs text-muted-foreground hover:text-muted-foreground">
        <ArrowLeft className="size-3.5" />
        All tenants
      </Link>

      <Feedback error={tenantQuery.error ? (tenantQuery.error as Error).message : undefined} />

      {tenantQuery.isLoading ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : !tenant ? (
        <p className="text-sm text-muted-foreground">This workspace does not exist.</p>
      ) : (
        <>
          <PageHeader title={tenant.name} description={tenant.owner_email ?? 'No owner email on record'}>
            <StatusPill status={tenant.status} />
            {/* The tabs below only READ this workspace. This is how an operator actually
                works inside it -- booking an advert, repairing a playlist -- using the
                tenant's own screens rather than admin-only copies of them. */}
            <button
              onClick={() => {
                enterWorkspace({ id: tenant.id, name: tenant.name })
                router.push('/dashboard/branding')
              }}
              className="inline-flex items-center gap-2 rounded-xl border border-border bg-muted px-3 py-2 text-sm font-semibold text-foreground transition-colors hover:bg-accent"
            >
              <Palette className="size-4" />
              Edit branding
            </button>
            <button
              onClick={() => {
                enterWorkspace({ id: tenant.id, name: tenant.name, readOnly: true })
                router.push('/dashboard/screens')
              }}
              title="Look around without being able to change anything"
              className="inline-flex items-center gap-2 rounded-xl border border-border bg-muted px-3 py-2 text-sm font-semibold text-foreground transition-colors hover:bg-accent"
            >
              <Eye className="size-4" />
              View (read-only)
            </button>
            <button
              onClick={() => {
                enterWorkspace({ id: tenant.id, name: tenant.name })
                router.push('/dashboard/screens')
              }}
              className="inline-flex items-center gap-2 rounded-xl bg-primary px-3 py-2 text-sm font-semibold text-primary-foreground transition-colors hover:bg-primary/90"
            >
              <LogIn className="size-4" />
              Enter workspace
            </button>
          </PageHeader>

          {tenant.rejection_reason && (
            <div className="rounded-xl border border-rose-500/20 bg-rose-500/5 p-3 text-sm text-rose-700 dark:text-rose-300">
              Rejection reason: {tenant.rejection_reason}
            </div>
          )}

          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <div className="rounded-2xl border border-border bg-muted p-4">
              <p className="mb-2 text-xs text-muted-foreground">TV screens</p>
              <QuotaBar used={tenant.screens_count} max={tenant.max_screens} />
              <p className="mt-2 text-xs text-muted-foreground">{tenant.online_screens_count} online now</p>
            </div>
            <div className="rounded-2xl border border-border bg-muted p-4">
              <p className="mb-2 text-xs text-muted-foreground">Ad slots</p>
              <QuotaBar used={tenant.ad_slots_used} max={tenant.max_ad_slots} />
              <p className="mt-2 text-xs text-muted-foreground">{tenant.plan_name ?? 'No package'}</p>
            </div>
            <div className="rounded-2xl border border-border bg-muted p-4">
              <p className="mb-2 text-xs text-muted-foreground">Storage</p>
              <p className="text-sm font-semibold">
                {formatBytes(tenant.storage_used_bytes)}{' '}
                <span className="font-normal text-muted-foreground">of {formatBytes(tenant.storage_quota_bytes)}</span>
              </p>
              <p className="mt-2 text-xs text-muted-foreground">
                Joined {tenant.created_at ? new Date(tenant.created_at).toLocaleDateString() : '—'}
              </p>
            </div>
            <div className="rounded-2xl border border-border bg-muted p-4">
              <p className="mb-2 text-xs text-muted-foreground">Ad plays, last 30 days</p>
              <p className="text-sm font-semibold">{(playsQuery.data?.total_plays ?? 0).toLocaleString()}</p>
              <p className="mt-2 text-xs text-muted-foreground">
                {playsQuery.isLoading ? 'Loading…' : `${(playsQuery.data?.online_plays ?? 0).toLocaleString()} online · ${(playsQuery.data?.offline_plays ?? 0).toLocaleString()} offline`}
              </p>
            </div>
          </div>

          <div className="flex gap-1 border-b border-border">
            {([
              ['screens', 'Screens', MonitorPlay],
              ['content', 'Content', FileVideo],
              ['users', 'Team', Users],
            ] as const).map(([key, label, Icon]) => (
              <button
                key={key}
                onClick={() => setTab(key)}
                className={`flex items-center gap-2 border-b-2 px-4 py-2.5 text-sm transition-all ${
                  tab === key
                    ? 'border-foreground font-semibold text-foreground'
                    : 'border-transparent text-muted-foreground hover:text-muted-foreground'
                }`}
              >
                <Icon className="size-4" />
                {label}
              </button>
            ))}
          </div>

          <section className="overflow-hidden rounded-2xl border border-border bg-card">
            {tab === 'screens' && (
              <Table
                loading={screensQuery.isLoading}
                empty="This workspace has no screens yet."
                headers={['Name', 'Status', 'Playback', 'Location', 'Model', 'App', 'Last seen', '']}
                rows={(screensQuery.data ?? []).map((s) => [
                  s.name ?? `Screen ${s.id}`,
                  <StatusPill key="s" status={s.status} />,
                  s.playback_state,
                  s.location ?? '—',
                  s.model ?? '—',
                  s.app_version ?? '—',
                  s.last_seen ? new Date(s.last_seen).toLocaleString() : '—',
                  // Harmless to queue on a screen that is not actually locked -- there is
                  // nothing for it to do there, so this is never gated on knowing that.
                  <button
                    key="u"
                    onClick={() => remoteUnlock.mutate(s.id)}
                    disabled={remoteUnlock.isPending}
                    title="Queue a remote unlock -- takes effect at this screen's next sync"
                    className="inline-flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1 text-xs font-medium text-foreground transition-colors hover:bg-accent disabled:opacity-50"
                  >
                    <Lock className="size-3" /> Remote unlock
                  </button>,
                ])}
              />
            )}
            {tab === 'content' && (
              <Table
                loading={contentQuery.isLoading}
                empty="No media uploaded."
                headers={['Name', 'Type', 'Status', 'Size', 'Uploaded']}
                rows={(contentQuery.data ?? []).map((c) => [
                  c.name ?? `Asset ${c.id}`,
                  c.type ?? '—',
                  c.status,
                  formatBytes(c.file_size_bytes),
                  c.uploaded_at ? new Date(c.uploaded_at).toLocaleDateString() : '—',
                ])}
              />
            )}
            {tab === 'users' && (
              <Table
                loading={usersQuery.isLoading}
                empty="No users."
                headers={['Name', 'Email', 'Role', 'Active', 'Super Admin Action']}
                rows={(usersQuery.data ?? []).map((u) => [
                  u.full_name ?? u.username,
                  u.email ?? '—',
                  <span
                    key={`role-${u.id}`}
                    className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-semibold ${
                      u.role === 'super_admin'
                        ? 'bg-accent text-foreground border border-border'
                        : 'bg-muted text-muted-foreground'
                    }`}
                  >
                    {u.role}
                  </span>,
                  u.is_active ? 'Yes' : 'No',
                  <button
                    key={`action-${u.id}`}
                    onClick={async () => {
                      const promoting = u.role !== 'super_admin'
                      // Granting platform-wide access is confirmed by typing the username,
                      // which the server checks too.
                      const result = await confirm(promoting ? {
                        title: `Make ${u.username} a platform administrator?`,
                        description: 'They will be able to see and change every tenant, package and TV on the platform.',
                        level: 3,
                        confirmText: u.username,
                        destructive: true,
                        confirmLabel: 'Grant admin access',
                      } : {
                        title: `Remove ${u.username}'s platform administrator access?`,
                        description: 'They become an owner of this workspace and lose access to the admin console.',
                        level: 2,
                        confirmLabel: 'Remove admin access',
                      })
                      if (!result) return
                      try {
                        await adminApi.updateUserRole(u.id, promoting ? 'super_admin' : 'owner', {
                          confirmUsername: promoting ? u.username : undefined,
                          reason: result.reason,
                        })
                        toast.success(promoting ? `${u.username} is now a platform administrator.` : `${u.username} is now an owner.`)
                        usersQuery.refetch()
                      } catch (err) {
                        toast.error(err instanceof Error ? err.message : 'Failed to update role')
                      }
                    }}
                    className={`rounded-lg px-2.5 py-1 text-xs font-medium transition-all ${
                      u.role === 'super_admin'
                        ? 'bg-rose-500/15 text-rose-700 dark:text-rose-300 hover:bg-rose-500/25 border border-rose-500/20'
                        : 'bg-accent text-foreground hover:bg-foreground/10 border border-border'
                    }`}
                  >
                    {u.role === 'super_admin' ? 'Demote to Owner' : 'Promote to Super Admin'}
                  </button>,
                ])}
              />
            )}
          </section>

          {tab === 'screens' && (
            <section className="overflow-hidden rounded-2xl border border-border bg-card">
              <div className="border-b border-border p-4">
                <h2 className="text-sm font-semibold text-foreground">Kiosk unlock activity</h2>
                <p className="mt-1 text-xs text-muted-foreground">
                  Every local PIN attempt and remote unlock for this workspace. Visibility only --
                  there is nothing here that lets an operator skip the PIN itself.
                </p>
              </div>
              <Table
                loading={kioskEventsQuery.isLoading}
                empty="No unlock activity recorded yet."
                headers={['When', 'Screen', 'Method', 'Result', 'By']}
                rows={(kioskEventsQuery.data ?? []).map((e) => [
                  new Date(e.occurred_at).toLocaleString(),
                  e.screen_name ?? (e.screen_id ? `Screen ${e.screen_id}` : '—'),
                  e.method === 'remote' ? 'Remote unlock' : 'PIN entry',
                  <span
                    key={`result-${e.id}`}
                    className={e.success ? 'text-emerald-700 dark:text-emerald-400' : 'text-rose-700 dark:text-rose-400'}
                  >
                    {e.success ? 'Success' : 'Failed'}
                  </span>,
                  e.actor_username ?? '—',
                ])}
              />
            </section>
          )}
        </>
      )}
    </div>
  )
}

function Table({
  loading, empty, headers, rows,
}: { loading: boolean; empty: string; headers: string[]; rows: React.ReactNode[][] }) {
  if (loading) return <p className="p-10 text-center text-sm text-muted-foreground">Loading…</p>
  if (rows.length === 0) return <p className="p-10 text-center text-sm text-muted-foreground">{empty}</p>
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="border-b border-border bg-muted text-xs uppercase tracking-wider text-muted-foreground">
          <tr>
            {headers.map((h) => <th key={h} className="p-4 text-left first:pl-5">{h}</th>)}
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {rows.map((cells, i) => (
            <tr key={i} className="transition-colors hover:bg-muted">
              {cells.map((cell, j) => (
                <td key={j} className="p-4 first:pl-5 first:font-medium first:text-foreground">{cell}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
