'use client'

import { use, useState, useMemo } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Activity,
  ArrowLeft,
  CalendarPlus,
  CalendarX,
  CheckCircle,
  CreditCard,
  Eye,
  FileVideo,
  Layers,
  LogIn,
  Map as MapIcon,
  MonitorPlay,
  Palette,
  Pin,
  Shield,
  Sliders,
  Sparkles,
  StickyNote,
  Table as TableIcon,
  Trash2,
  Unlock,
  Users,
  X,
} from 'lucide-react'
import { toast } from 'sonner'
import { adminApi } from '@/lib/api'
import { useAuthStore } from '@/lib/store'
import { useConfirm } from '@/components/admin/kit/confirm-dialog'
import {
  AdminDataTable,
  type DataTableColumn,
} from '@/components/admin/kit/admin-data-table'
import {
  Feedback,
  PageHeader,
  QuotaBar,
  StatusPill,
  formatBytes,
} from '@/components/admin/admin-ui'
import { computeTenantHealth } from '@/lib/tenant-health'
import { ScreenMap, type MapPoint } from '@/components/dashboard/screen-map'
import { GrantLimitsDialog } from '@/components/admin/grant-limits-dialog'
import type {
  AuditEvent,
  PlatformPayment,
  TenantContent,
  TenantScreen,
  TenantUser,
} from '@/lib/types'

type Tab =
  | 'overview'
  | 'screens'
  | 'content'
  | 'team'
  | 'billing'
  | 'limits'
  | 'branding'
  | 'security'
  | 'activity'
  | 'notes'

export default function AdminTenantDetailPage({
  params,
}: {
  params: Promise<{ id: string }>
}) {
  const { id } = use(params)
  const tenantId = Number(id)
  const [tab, setTab] = useState<Tab>('overview')
  const [screenView, setScreenView] = useState<'table' | 'map'>('table')
  const [selectedAuditLog, setSelectedAuditLog] = useState<AuditEvent | null>(null)
  const [isGrantOpen, setIsGrantOpen] = useState(false)
  const [newNoteBody, setNewNoteBody] = useState('')
  const [newNotePinned, setNewNotePinned] = useState(false)

  const router = useRouter()
  const queryClient = useQueryClient()
  const enterWorkspace = useAuthStore((state) => state.enterWorkspace)
  const confirm = useConfirm()

  // Queries
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
    enabled: tab === 'screens' || tab === 'overview',
  })

  const contentQuery = useQuery({
    queryKey: ['admin', 'tenant', tenantId, 'content'],
    queryFn: () => adminApi.getTenantContent(tenantId),
    enabled: tab === 'content',
  })

  const usersQuery = useQuery({
    queryKey: ['admin', 'tenant', tenantId, 'users'],
    queryFn: () => adminApi.getTenantUsers(tenantId),
    enabled: tab === 'team',
  })

  const kioskEventsQuery = useQuery({
    queryKey: ['admin', 'tenant', tenantId, 'kiosk-events'],
    queryFn: () => adminApi.getKioskUnlockEvents(tenantId),
    enabled: tab === 'security' || tab === 'overview',
  })

  const auditEventsQuery = useQuery({
    queryKey: ['admin', 'tenant', tenantId, 'audit'],
    queryFn: () => adminApi.listAuditEvents({ org_id: tenantId, limit: 100 }),
    enabled: tab === 'activity' || tab === 'overview',
  })

  const paymentsQuery = useQuery({
    queryKey: ['admin', 'tenant', tenantId, 'payments'],
    queryFn: () => adminApi.listPaymentsPaged({ organization_id: tenantId, limit: 50 }),
    enabled: tab === 'billing',
  })

  const customRequestsQuery = useQuery({
    queryKey: ['admin', 'tenant', tenantId, 'custom-requests'],
    queryFn: async () => {
      const all = await adminApi.listCustomRequests()
      return all.filter((r) => r.organization_id === tenantId)
    },
    enabled: tab === 'billing',
  })

  const remoteUnlock = useMutation({
    mutationFn: (screenId: number) => adminApi.remoteUnlockScreen(tenantId, screenId),
    onSuccess: (result) => {
      toast.success(
        result.queued
          ? 'Remote unlock queued — takes effect at the screen’s next sync'
          : 'Could not queue unlock',
      )
      queryClient.invalidateQueries({
        queryKey: ['admin', 'tenant', tenantId, 'kiosk-events'],
      })
    },
    onError: (error: Error) => toast.error(error.message),
  })

  const setPlanWindow = useMutation({
    mutationFn: ({
      body,
      reason,
    }: {
      body: { extend_days?: number; status?: 'active' | 'expired' }
      reason?: string
    }) => adminApi.updateSubscription(tenantId, body, reason),
    onSuccess: () => {
      toast.success('Subscription window updated')
      queryClient.invalidateQueries({ queryKey: ['admin', 'tenant', tenantId] })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const updateUserRoleMutation = useMutation({
    mutationFn: ({
      userId,
      role,
      confirmUsername,
    }: {
      userId: number
      role: 'super_admin' | 'owner' | 'editor' | 'viewer'
      confirmUsername?: string
    }) =>
      adminApi.updateUserRole(userId, role, {
        confirmUsername,
        reason: `Role changed via Admin Tenant Deep-Dive for tenant #${tenantId}`,
      }),
    onSuccess: () => {
      toast.success('User role updated successfully')
      queryClient.invalidateQueries({ queryKey: ['admin', 'tenant', tenantId, 'team'] })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const notesQuery = useQuery({
    queryKey: ['admin', 'tenant', tenantId, 'notes'],
    queryFn: () => adminApi.listTenantNotes(tenantId),
    enabled: tab === 'notes',
  })

  const createNoteMutation = useMutation({
    mutationFn: () =>
      adminApi.createTenantNote(tenantId, { body: newNoteBody, pinned: newNotePinned }),
    onSuccess: () => {
      toast.success('Internal note saved')
      setNewNoteBody('')
      setNewNotePinned(false)
      queryClient.invalidateQueries({ queryKey: ['admin', 'tenant', tenantId, 'notes'] })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const deleteNoteMutation = useMutation({
    mutationFn: (noteId: number) => adminApi.deleteTenantNote(tenantId, noteId),
    onSuccess: () => {
      toast.success('Note removed')
      queryClient.invalidateQueries({ queryKey: ['admin', 'tenant', tenantId, 'notes'] })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const tenant = tenantQuery.data
  const health = useMemo(
    () => (tenant ? computeTenantHealth(tenant) : null),
    [tenant],
  )

  const mapPoints: MapPoint[] = useMemo(() => {
    if (!screensQuery.data) return []
    return screensQuery.data.map((s) => ({
      id: s.id,
      name: s.name || `Screen #${s.id}`,
      location: s.location,
      latitude: s.latitude,
      longitude: s.longitude,
      online: Boolean(s.is_online),
      detail: `Version: ${s.app_version || 'unknown'}`,
    }))
  }, [screensQuery.data])

  // Screen columns
  const screenColumns: DataTableColumn<TenantScreen>[] = [
    {
      id: 'name',
      header: 'Screen Name',
      sortable: true,
      cell: ({ row }) => (
        <div>
          <p className="font-semibold text-foreground">{row.name}</p>
          <p className="font-mono text-xs text-muted-foreground">{row.device_id}</p>
        </div>
      ),
    },
    {
      id: 'status',
      header: 'Online State',
      width: 'w-28',
      cell: ({ row }) => (
        <span
          className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-xs font-medium ${
            row.is_online
              ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400'
              : 'border-muted-foreground/30 bg-muted text-muted-foreground'
          }`}
        >
          <span
            className={`size-1.5 rounded-full ${row.is_online ? 'bg-emerald-500' : 'bg-muted-foreground'}`}
          />
          {row.is_online ? 'Online' : 'Offline'}
        </span>
      ),
    },
    {
      id: 'location',
      header: 'Location',
      hideOnMobile: true,
      cell: ({ row }) => (
        <span className="text-xs text-muted-foreground">{row.location ?? '—'}</span>
      ),
    },
    {
      id: 'version',
      header: 'App Version',
      width: 'w-28',
      hideOnMobile: true,
      cell: ({ row }) => (
        <span className="font-mono text-xs text-muted-foreground">
          {row.app_version ?? 'Legacy'}
        </span>
      ),
    },
    {
      id: 'last_seen',
      header: 'Last Heartbeat',
      width: 'w-36',
      hideOnMobile: true,
      cell: ({ row }) => (
        <span className="font-mono text-xs text-muted-foreground">
          {row.last_seen ? new Date(row.last_seen).toLocaleString() : 'Never'}
        </span>
      ),
    },
    {
      id: 'actions',
      header: '',
      align: 'right',
      width: 'w-24',
      cell: ({ row }) => (
        <button
          onClick={async () => {
            const res = await confirm({
              title: `Remote unlock ${row.name}?`,
              description:
                'Sends a signed command bypass to exit kiosk mode on this specific panel.',
              level: 2,
              confirmLabel: 'Remote unlock',
            })
            if (res) remoteUnlock.mutate(row.id)
          }}
          disabled={remoteUnlock.isPending}
          className="flex items-center gap-1 rounded-lg border border-border bg-background px-2.5 py-1 text-xs text-foreground hover:bg-muted disabled:opacity-50"
        >
          <Unlock className="size-3" />
          Unlock
        </button>
      ),
    },
  ]

  // Content columns
  const contentColumns: DataTableColumn<TenantContent>[] = [
    {
      id: 'name',
      header: 'Media Title',
      cell: ({ row }) => (
        <div className="flex items-center gap-3">
          <div className="grid size-9 place-items-center rounded-lg border border-border bg-muted">
            <FileVideo className="size-4 text-muted-foreground" />
          </div>
          <div>
            <p className="font-semibold text-foreground">{row.name}</p>
            <p className="font-mono text-xs text-muted-foreground">{row.mime_type}</p>
          </div>
        </div>
      ),
    },
    {
      id: 'duration',
      header: 'Duration',
      width: 'w-24',
      hideOnMobile: true,
      cell: ({ row }) => (
        <span className="text-xs text-muted-foreground">
          {row.duration_seconds ? `${row.duration_seconds}s` : 'Static'}
        </span>
      ),
    },
    {
      id: 'size',
      header: 'File Size',
      width: 'w-24',
      hideOnMobile: true,
      cell: ({ row }) => (
        <span className="text-xs text-muted-foreground">{formatBytes(row.file_size_bytes)}</span>
      ),
    },
    {
      id: 'uploaded',
      header: 'Uploaded',
      width: 'w-36',
      hideOnMobile: true,
      cell: ({ row }) => (
        <span className="font-mono text-xs text-muted-foreground">
          {row.uploaded_at ? new Date(row.uploaded_at).toLocaleDateString() : '—'}
        </span>
      ),
    },
  ]

  // Team columns
  const teamColumns: DataTableColumn<TenantUser>[] = [
    {
      id: 'user',
      header: 'User & Email',
      cell: ({ row }) => (
        <div>
          <p className="font-semibold text-foreground">{row.username}</p>
          <p className="font-mono text-xs text-emerald-700 dark:text-emerald-400">
            {row.email ?? 'No email'}
          </p>
        </div>
      ),
    },
    {
      id: 'role',
      header: 'Assigned Role',
      width: 'w-28',
      cell: ({ row }) => (
        <span className="inline-block rounded-md border border-border bg-muted px-2 py-0.5 text-xs font-semibold capitalize text-foreground">
          {row.role}
        </span>
      ),
    },
    {
      id: 'active',
      header: 'State',
      width: 'w-24',
      hideOnMobile: true,
      cell: ({ row }) => (
        <span
          className={`inline-block rounded-md border px-2 py-0.5 text-xs font-medium ${
            row.is_active
              ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400'
              : 'border-rose-500/30 bg-rose-500/10 text-rose-700 dark:text-rose-400'
          }`}
        >
          {row.is_active ? 'Active' : 'Deactivated'}
        </span>
      ),
    },
    {
      id: 'created',
      header: 'Joined',
      width: 'w-36',
      cell: ({ row }) => (
        <span className="font-mono text-xs text-muted-foreground">
          {row.created_at ? new Date(row.created_at).toLocaleDateString() : '—'}
        </span>
      ),
    },
    {
      id: 'actions',
      header: 'Role Action',
      width: 'w-44',
      cell: ({ row }) => (
        <select
          value={row.role}
          disabled={updateUserRoleMutation.isPending}
          onChange={async (e) => {
            const newRole = e.target.value as 'super_admin' | 'owner' | 'editor' | 'viewer'
            if (newRole === row.role) return
            const res = await confirm({
              title: `Change role for ${row.username}?`,
              description: `Update role from "${row.role}" to "${newRole}". This immediately adjusts what permissions they have in this workspace.`,
              confirmLabel: `Change to ${newRole}`,
            })
            if (res) {
              updateUserRoleMutation.mutate({
                userId: row.id,
                role: newRole,
                confirmUsername: newRole === 'super_admin' ? row.username : undefined,
              })
            }
          }}
          className="rounded-lg border border-border bg-background px-2.5 py-1 text-xs font-medium text-foreground hover:border-primary focus:outline-none"
        >
          <option value="owner">Owner</option>
          <option value="editor">Editor</option>
          <option value="viewer">Viewer</option>
          <option value="super_admin">Super Admin</option>
        </select>
      ),
    },
  ]

  // Payment columns
  const paymentColumns: DataTableColumn<PlatformPayment>[] = [
    {
      id: 'date',
      header: 'Date',
      width: 'w-32',
      cell: ({ row }) => (
        <span className="font-mono text-xs text-muted-foreground">
          {row.paid_at ? new Date(row.paid_at).toLocaleDateString() : '—'}
        </span>
      ),
    },
    {
      id: 'kind',
      header: 'Category',
      width: 'w-28',
      cell: ({ row }) => (
        <span className="rounded border border-border bg-muted px-2 py-0.5 text-[11px] uppercase font-semibold text-foreground">
          {row.kind}
        </span>
      ),
    },
    {
      id: 'amount',
      header: 'Amount',
      width: 'w-28',
      cell: ({ row }) => (
        <span className="font-semibold text-foreground">
          ₹{(row.amount_paise / 100).toFixed(2)}
        </span>
      ),
    },
    {
      id: 'provider',
      header: 'Gateway',
      width: 'w-24',
      cell: ({ row }) => (
        <span className="font-mono text-xs text-muted-foreground">{row.provider}</span>
      ),
    },
    {
      id: 'status',
      header: 'Status',
      width: 'w-24',
      cell: ({ row }) => (
        <span
          className={`rounded-md border px-2 py-0.5 text-xs font-medium ${
            row.status === 'captured'
              ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400'
              : 'border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400'
          }`}
        >
          {row.status}
        </span>
      ),
    },
  ]

  // Audit columns
  const auditColumns: DataTableColumn<AuditEvent>[] = [
    {
      id: 'time',
      header: 'Timestamp',
      width: 'w-36',
      cell: ({ row }) => (
        <span className="font-mono text-xs text-muted-foreground">
          {row.occurred_at ? new Date(row.occurred_at).toLocaleString() : '—'}
        </span>
      ),
    },
    {
      id: 'action',
      header: 'Action',
      width: 'w-36',
      cell: ({ row }) => (
        <span className="font-mono text-xs font-semibold text-foreground">{row.action}</span>
      ),
    },
    {
      id: 'actor',
      header: 'Operator',
      width: 'w-32',
      cell: ({ row }) => (
        <span className="font-mono text-xs text-muted-foreground">
          {row.actor_username ?? 'System'}
        </span>
      ),
    },
    {
      id: 'reason',
      header: 'Reason',
      cell: ({ row }) => (
        <span className="text-xs text-muted-foreground">
          {row.reason ? decodeURIComponent(row.reason) : '—'}
        </span>
      ),
    },
    {
      id: 'diff',
      header: '',
      align: 'right',
      width: 'w-20',
      cell: ({ row }) => (
        <button
          onClick={() => setSelectedAuditLog(row)}
          className="rounded border border-border bg-background px-2 py-1 text-xs text-foreground hover:bg-muted"
        >
          Diff
        </button>
      ),
    },
  ]

  return (
    <div className="space-y-6 p-3.5 sm:p-6 lg:p-8 text-foreground">
      <Link
        href="/admin/tenants"
        className="inline-flex items-center gap-2 text-xs text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-3.5" />
        Back to Tenant Directory
      </Link>

      <Feedback error={tenantQuery.error ? (tenantQuery.error as Error).message : undefined} />

      {tenantQuery.isLoading ? (
        <p className="text-sm text-muted-foreground">Loading workspace 360…</p>
      ) : !tenant ? (
        <p className="text-sm text-muted-foreground">This workspace does not exist.</p>
      ) : (
        <>
          <PageHeader
            title={tenant.name}
            description={`Org #${tenant.id} • ${tenant.owner_email ?? 'No owner email'}`}
          >
            <div className="flex flex-wrap items-center gap-2">
              <StatusPill status={tenant.status} />

              {health && (
                <div
                  title={health.reasons.join(' • ')}
                  className="flex items-center gap-1.5 rounded-lg border border-border bg-card px-2.5 py-1 text-xs"
                >
                  <span className="text-muted-foreground">Health:</span>
                  <span
                    className={`font-mono font-bold ${
                      health.tone === 'emerald'
                        ? 'text-emerald-600 dark:text-emerald-400'
                        : health.tone === 'amber'
                          ? 'text-amber-600 dark:text-amber-400'
                          : 'text-rose-600 dark:text-rose-400'
                    }`}
                  >
                    {health.score}/100
                  </span>
                </div>
              )}

              <button
                onClick={() => {
                  enterWorkspace({ id: tenant.id, name: tenant.name, readOnly: true })
                  router.push('/dashboard')
                }}
                title="Explore workspace in read-only act-as mode"
                className="inline-flex items-center gap-2 rounded-xl border border-border bg-background px-3 py-2 text-sm font-semibold text-foreground transition-colors hover:bg-muted"
              >
                <Eye className="size-4" />
                View (read-only)
              </button>

              <button
                onClick={() => {
                  enterWorkspace({ id: tenant.id, name: tenant.name, readOnly: false })
                  router.push('/dashboard')
                }}
                className="inline-flex items-center gap-2 rounded-xl bg-primary px-3 py-2 text-sm font-semibold text-primary-foreground transition-colors hover:bg-primary/90"
              >
                <LogIn className="size-4" />
                Enter workspace
              </button>

              <button
                onClick={async () => {
                  const result = await confirm({
                    title: `Delete ${tenant.name} permanently?`,
                    description:
                      'Every screen, advert, playlist, user, and database row in Supabase and all media files in Cloudflare R2 will be permanently deleted immediately. This cannot be undone.',
                    level: 3,
                    confirmText: tenant.name,
                    destructive: true,
                    confirmLabel: 'Delete permanently now',
                  })
                  if (result) {
                    try {
                      await adminApi.deleteTenantPermanently(tenant.id, tenant.name, result.reason)
                      toast.success(`${tenant.name} has been permanently deleted from Supabase and R2.`)
                      router.push('/admin/tenants')
                    } catch (err: any) {
                      toast.error(err.message || 'Could not delete workspace')
                    }
                  }
                }}
                title="Permanently erase workspace from Supabase and Cloudflare R2"
                className="inline-flex items-center gap-2 rounded-xl border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-sm font-semibold text-rose-500 transition-colors hover:bg-rose-500/20"
              >
                <Trash2 className="size-4" />
                Delete workspace
              </button>
            </div>
          </PageHeader>

          {/* 10-Tab Navigation */}
          <div className="flex overflow-x-auto border-b border-border text-sm scrollbar-none">
            {[
              { id: 'overview', label: 'Overview', icon: Layers },
              { id: 'screens', label: `Screens (${tenant.screens_count})`, icon: MonitorPlay },
              { id: 'content', label: 'Content', icon: FileVideo },
              { id: 'team', label: 'Team', icon: Users },
              { id: 'billing', label: 'Billing', icon: CreditCard },
              { id: 'limits', label: 'Plan & Limits', icon: Sliders },
              { id: 'branding', label: 'Branding', icon: Palette },
              { id: 'security', label: 'Security', icon: Shield },
              { id: 'activity', label: 'Activity', icon: Activity },
              { id: 'notes', label: 'Internal Notes', icon: StickyNote },
            ].map((t) => {
              const Icon = t.icon
              const active = tab === t.id
              return (
                <button
                  key={t.id}
                  onClick={() => setTab(t.id as Tab)}
                  className={`flex shrink-0 items-center gap-2 border-b-2 px-4 py-2.5 font-medium transition-colors ${
                    active
                      ? 'border-primary text-foreground'
                      : 'border-transparent text-muted-foreground hover:text-foreground'
                  }`}
                >
                  <Icon className="size-4" />
                  {t.label}
                </button>
              )
            })}
          </div>

          {/* TAB 1: OVERVIEW */}
          {tab === 'overview' && (
            <div className="space-y-6">
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <div className="rounded-2xl border border-border bg-card p-4">
                  <p className="mb-2 text-xs text-muted-foreground">TV Screens</p>
                  <QuotaBar used={tenant.screens_count} max={tenant.max_screens} />
                  <p className="mt-2 text-xs text-muted-foreground">
                    {tenant.online_screens_count} of {tenant.screens_count} online now
                  </p>
                </div>
                <div className="rounded-2xl border border-border bg-card p-4">
                  <p className="mb-2 text-xs text-muted-foreground">Ad Placements</p>
                  <QuotaBar used={tenant.ad_slots_used} max={tenant.max_ad_slots} />
                  <p className="mt-2 text-xs text-muted-foreground">
                    {tenant.plan_name ?? 'Custom / Bespoke Plan'}
                  </p>
                </div>
                <div className="rounded-2xl border border-border bg-card p-4">
                  <p className="mb-2 text-xs text-muted-foreground">Storage Consumed</p>
                  <p className="text-sm font-semibold">
                    {formatBytes(tenant.storage_used_bytes)}{' '}
                    <span className="font-normal text-muted-foreground">
                      of {tenant.storage_quota_bytes ? formatBytes(tenant.storage_quota_bytes) : 'Unlimited'}
                    </span>
                  </p>
                  <p className="mt-2 text-xs text-muted-foreground">
                    Joined {tenant.created_at ? new Date(tenant.created_at).toLocaleDateString() : '—'}
                  </p>
                </div>
                <div className="rounded-2xl border border-border bg-card p-4">
                  <p className="mb-2 text-xs text-muted-foreground">Ad Plays (30 Days)</p>
                  {playsQuery.isLoading ? (
                    <p className="text-sm text-muted-foreground">Loading…</p>
                  ) : (
                    <>
                      <p className="text-lg font-bold">
                        {(playsQuery.data?.total_plays ?? 0).toLocaleString()}
                      </p>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {playsQuery.data?.online_plays ?? 0} online • {playsQuery.data?.offline_plays ?? 0} offline
                      </p>
                    </>
                  )}
                </div>
              </div>

              {/* Health Score Breakdown Card */}
              {health && (
                <div className="rounded-2xl border border-border bg-card p-5">
                  <div className="flex items-center justify-between border-b border-border pb-3">
                    <h3 className="text-sm font-bold text-foreground">Health Analysis</h3>
                    <span className="text-xs font-mono text-muted-foreground">
                      Score: {health.score}/100
                    </span>
                  </div>
                  <ul className="mt-3 space-y-1.5 text-xs text-muted-foreground">
                    {health.reasons.map((r, i) => (
                      <li key={i} className="flex items-center gap-2">
                        <CheckCircle className="size-3.5 text-emerald-500" />
                        <span>{r}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}

          {/* TAB 2: SCREENS */}
          {tab === 'screens' && (
            <div className="space-y-4">
              <div className="flex justify-end gap-2">
                <button
                  onClick={() => setScreenView('table')}
                  className={`flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-medium ${
                    screenView === 'table'
                      ? 'border-border bg-muted text-foreground'
                      : 'border-transparent text-muted-foreground hover:bg-muted'
                  }`}
                >
                  <TableIcon className="size-3.5" />
                  Table
                </button>
                <button
                  onClick={() => setScreenView('map')}
                  className={`flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-medium ${
                    screenView === 'map'
                      ? 'border-border bg-muted text-foreground'
                      : 'border-transparent text-muted-foreground hover:bg-muted'
                  }`}
                >
                  <MapIcon className="size-3.5" />
                  Map View
                </button>
              </div>

              {screenView === 'map' ? (
                <div className="overflow-hidden rounded-2xl border border-border bg-card p-4">
                  <ScreenMap points={mapPoints} height={420} />
                </div>
              ) : (
                <AdminDataTable
                  data={screensQuery.data ?? []}
                  columns={screenColumns}
                  isLoading={screensQuery.isLoading}
                />
              )}
            </div>
          )}

          {/* TAB 3: CONTENT */}
          {tab === 'content' && (
            <AdminDataTable
              data={contentQuery.data ?? []}
              columns={contentColumns}
              isLoading={contentQuery.isLoading}
            />
          )}

          {/* TAB 4: TEAM */}
          {tab === 'team' && (
            <AdminDataTable
              data={usersQuery.data ?? []}
              columns={teamColumns}
              isLoading={usersQuery.isLoading}
            />
          )}

          {/* TAB 5: BILLING */}
          {tab === 'billing' && (
            <div className="space-y-6">
              <div className="flex flex-wrap items-center justify-between gap-4 rounded-2xl border border-border bg-card p-5">
                <div>
                  <h3 className="font-bold text-foreground">Subscription Status</h3>
                  <p className="text-xs text-muted-foreground">
                    State:{' '}
                    <span className="font-semibold text-foreground uppercase">
                      {tenant.subscription_state ?? 'None'}
                    </span>{' '}
                    • Period End:{' '}
                    {tenant.current_period_end
                      ? new Date(tenant.current_period_end).toLocaleDateString()
                      : 'Indefinite'}
                  </p>
                </div>

                <div className="flex items-center gap-2">
                  <button
                    onClick={() => setPlanWindow.mutate({ body: { extend_days: 30 } })}
                    disabled={setPlanWindow.isPending}
                    className="flex items-center gap-1.5 rounded-lg border border-sky-500/30 bg-sky-500/10 px-3 py-1.5 text-xs text-sky-700 dark:text-sky-400 hover:bg-sky-500/20"
                  >
                    <CalendarPlus className="size-3.5" />
                    +30 days extension
                  </button>
                  {tenant.subscription_state === 'active' && (
                    <button
                      onClick={async () => {
                        const res = await confirm({
                          title: 'End subscription now?',
                          description:
                            'This workspace will expire immediately and screens will show the demo loop.',
                          level: 2,
                          confirmLabel: 'End plan',
                        })
                        if (res) {
                          setPlanWindow.mutate({
                            body: { status: 'expired' },
                            reason: res.reason,
                          })
                        }
                      }}
                      className="flex items-center gap-1.5 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-1.5 text-xs text-amber-700 dark:text-amber-400 hover:bg-amber-500/20"
                    >
                      <CalendarX className="size-3.5" />
                      End plan
                    </button>
                  )}
                </div>
              </div>

              {(customRequestsQuery.data ?? []).length > 0 && (
                <div className="space-y-3 rounded-2xl border border-border bg-card p-5">
                  <div className="flex items-center gap-2">
                    <Sparkles className="size-4 text-emerald-500" />
                    <h4 className="text-sm font-bold text-foreground">Custom Plan Requests</h4>
                  </div>
                  <ul className="divide-y divide-border text-xs">
                    {(customRequestsQuery.data ?? []).map((req) => (
                      <li key={req.id} className="flex items-center justify-between py-2.5">
                        <div>
                          <p className="font-semibold text-foreground">
                            Request #{req.id} • {req.max_screens} screens, {req.max_ad_slots} ad slots
                          </p>
                          <p className="text-muted-foreground">{req.notes || 'No notes'}</p>
                        </div>
                        <div className="flex items-center gap-2">
                          <span className="font-semibold text-foreground">
                            {req.price_paise ? `₹${(req.price_paise / 100).toFixed(2)}` : 'Unpriced'}
                          </span>
                          <span className="rounded border border-border bg-muted px-2 py-0.5 font-mono text-[10px] uppercase">
                            {req.status}
                          </span>
                        </div>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              <div className="space-y-3">
                <h4 className="text-sm font-bold text-foreground">Payment History</h4>
                <AdminDataTable
                  data={paymentsQuery.data?.items ?? []}
                  columns={paymentColumns}
                  isLoading={paymentsQuery.isLoading}
                />
              </div>
            </div>
          )}

          {/* TAB 6: PLAN & LIMITS */}
          {tab === 'limits' && (
            <div className="space-y-6">
              <div className="flex items-center justify-between rounded-2xl border border-border bg-card p-5">
                <div>
                  <h3 className="font-bold text-foreground">Package & Entitlements</h3>
                  <p className="text-xs text-muted-foreground">
                    Current plan: {tenant.plan_name ?? 'Custom bespoke'}
                  </p>
                </div>
                <button
                  onClick={() => setIsGrantOpen(true)}
                  className="flex items-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground hover:bg-primary/90"
                >
                  <Sliders className="size-4" />
                  Grant Custom Limits & Features
                </button>
              </div>

              {isGrantOpen && (
                <GrantLimitsDialog
                  tenant={tenant}
                  onClose={() => {
                    setIsGrantOpen(false)
                    queryClient.invalidateQueries({ queryKey: ['admin', 'tenant', tenantId] })
                  }}
                />
              )}

              <div className="rounded-2xl border border-border bg-card p-5 space-y-4">
                <h4 className="text-sm font-bold text-foreground">Active Feature Flags</h4>
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
                  {Object.entries(tenant.feature_flags ?? {}).map(([key, enabled]) => (
                    <div
                      key={key}
                      className={`flex items-center justify-between rounded-xl border p-3 text-xs ${
                        enabled
                          ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-800 dark:text-emerald-300'
                          : 'border-border bg-muted/30 text-muted-foreground'
                      }`}
                    >
                      <span className="font-medium capitalize">{key.replace(/_/g, ' ')}</span>
                      <span className="font-bold">{enabled ? 'ON' : 'OFF'}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}

          {/* TAB 7: BRANDING */}
          {tab === 'branding' && (
            <div className="space-y-6 rounded-2xl border border-border bg-card p-6">
              <div className="flex items-center justify-between border-b border-border pb-4">
                <div>
                  <h3 className="font-bold text-foreground">Brand Customization</h3>
                  <p className="text-xs text-muted-foreground">
                    White-label assets and customer portal styling
                  </p>
                </div>
                <button
                  onClick={() => {
                    enterWorkspace({ id: tenant.id, name: tenant.name })
                    router.push('/dashboard/branding')
                  }}
                  className="flex items-center gap-2 rounded-xl border border-border bg-background px-4 py-2 text-sm font-semibold text-foreground hover:bg-muted"
                >
                  <Palette className="size-4" />
                  Open Branding Studio (Act-as)
                </button>
              </div>

              <div className="grid gap-4 sm:grid-cols-2">
                <div className="rounded-xl border border-border bg-muted/20 p-4 space-y-2">
                  <span className="text-xs font-semibold text-muted-foreground">Portal Name</span>
                  <p className="text-sm font-bold text-foreground">{tenant.name}</p>
                </div>
                <div className="rounded-xl border border-border bg-muted/20 p-4 space-y-2">
                  <span className="text-xs font-semibold text-muted-foreground">URL Slug</span>
                  <p className="font-mono text-xs text-foreground">/workspace/{tenant.slug}</p>
                </div>
              </div>
            </div>
          )}

          {/* TAB 8: SECURITY */}
          {tab === 'security' && (
            <div className="space-y-6">
              <div className="rounded-2xl border border-border bg-card p-5">
                <h3 className="font-bold text-foreground mb-1">Kiosk Hardware Protection</h3>
                <p className="text-xs text-muted-foreground mb-4">
                  Screen PIN unlock attempts and remote emergency bypass operations
                </p>

                <div className="space-y-2">
                  {(kioskEventsQuery.data ?? []).length === 0 ? (
                    <p className="text-xs text-muted-foreground py-4">No kiosk unlock events recorded.</p>
                  ) : (
                    <ul className="divide-y divide-border text-xs">
                      {(kioskEventsQuery.data ?? []).map((ev) => (
                        <li key={ev.id} className="flex items-center justify-between py-2.5">
                          <div className="flex items-center gap-3">
                            <span
                              className={`size-2 rounded-full ${
                                ev.success ? 'bg-emerald-500' : 'bg-rose-500'
                              }`}
                            />
                            <div>
                              <p className="font-semibold text-foreground">
                                {ev.method === 'remote' ? 'Remote Operator Unlock' : 'Local PIN Entry'}
                              </p>
                              <p className="font-mono text-[11px] text-muted-foreground">
                                Screen: {ev.screen_name ?? `ID ${ev.screen_id}`}
                              </p>
                            </div>
                          </div>
                          <span className="font-mono text-muted-foreground">
                            {new Date(ev.occurred_at).toLocaleString()}
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </div>
            </div>
          )}

          {/* TAB 9: ACTIVITY */}
          {tab === 'activity' && (
            <div className="space-y-4">
              <AdminDataTable
                data={auditEventsQuery.data?.items ?? []}
                columns={auditColumns}
                isLoading={auditEventsQuery.isLoading}
              />
            </div>
          )}

          {/* TAB 10: INTERNAL NOTES */}
          {tab === 'notes' && (
            <div className="space-y-6">
              <div className="rounded-2xl border border-border bg-card p-6">
                <h3 className="font-bold text-foreground mb-1">Add Operator Note</h3>
                <p className="text-xs text-muted-foreground mb-4">
                  Confidential notes visible only to super admins. Not visible to the tenant.
                </p>
                <div className="space-y-3">
                  <textarea
                    rows={3}
                    value={newNoteBody}
                    onChange={(e) => setNewNoteBody(e.target.value)}
                    placeholder="E.g., High-value VIP client, requires phone confirmation before plan changes..."
                    className="w-full rounded-xl border border-border bg-background p-3 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-foreground"
                  />
                  <div className="flex items-center justify-between">
                    <label className="flex items-center gap-2 text-xs text-muted-foreground cursor-pointer select-none">
                      <input
                        type="checkbox"
                        checked={newNotePinned}
                        onChange={(e) => setNewNotePinned(e.target.checked)}
                        className="rounded border-border accent-primary"
                      />
                      <Pin className="size-3.5 text-amber-500" />
                      <span>Pin note to top</span>
                    </label>
                    <button
                      disabled={!newNoteBody.trim() || createNoteMutation.isPending}
                      onClick={() => createNoteMutation.mutate()}
                      className="rounded-xl bg-primary px-4 py-2 text-xs font-semibold text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
                    >
                      {createNoteMutation.isPending ? 'Saving…' : 'Save Note'}
                    </button>
                  </div>
                </div>
              </div>

              <div className="space-y-3">
                {notesQuery.isLoading ? (
                  <p className="text-xs text-muted-foreground">Loading internal notes…</p>
                ) : (notesQuery.data ?? []).length === 0 ? (
                  <p className="text-xs text-muted-foreground py-6 text-center">
                    No internal operator notes recorded for this workspace.
                  </p>
                ) : (
                  <div className="grid gap-3">
                    {(notesQuery.data ?? []).map((note) => (
                      <div
                        key={note.id}
                        className={`rounded-2xl border p-4 transition-colors ${
                          note.pinned
                            ? 'border-amber-500/40 bg-amber-500/5'
                            : 'border-border bg-card'
                        }`}
                      >
                        <div className="flex items-start justify-between gap-4">
                          <div className="space-y-1">
                            <div className="flex items-center gap-2">
                              {note.pinned && (
                                <span className="inline-flex items-center gap-1 rounded bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-bold uppercase text-amber-600 dark:text-amber-400">
                                  <Pin className="size-3" /> Pinned
                                </span>
                              )}
                              <span className="text-xs font-semibold text-foreground">
                                {note.author_username || 'Super Admin'}
                              </span>
                              <span className="text-xs text-muted-foreground">•</span>
                              <span className="font-mono text-[11px] text-muted-foreground">
                                {new Date(note.created_at).toLocaleString()}
                              </span>
                            </div>
                            <p className="whitespace-pre-wrap text-sm text-foreground pt-1">
                              {note.body}
                            </p>
                          </div>
                          <button
                            onClick={async () => {
                              const ok = await confirm({
                                title: 'Delete Internal Note',
                                description: 'Are you sure you want to delete this internal note?',
                                confirmLabel: 'Delete',
                                destructive: true,
                              })
                              if (ok) deleteNoteMutation.mutate(note.id)
                            }}
                            className="rounded-lg p-1.5 text-muted-foreground hover:bg-rose-500/10 hover:text-rose-500"
                            title="Delete note"
                          >
                            <Trash2 className="size-4" />
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}

          {/* JSON Diff Drawer */}
          {selectedAuditLog && (
            <div
              className="fixed inset-0 z-50 flex items-center justify-end bg-black/60 backdrop-blur-sm"
              onClick={() => setSelectedAuditLog(null)}
            >
              <div
                className="h-full w-full max-w-lg space-y-4 overflow-y-auto border-l border-border bg-card p-6 shadow-2xl"
                onClick={(e) => e.stopPropagation()}
              >
                <div className="flex items-center justify-between border-b border-border pb-3">
                  <div>
                    <h3 className="font-bold text-foreground">{selectedAuditLog.action}</h3>
                    <p className="font-mono text-xs text-muted-foreground">
                      Event #{selectedAuditLog.id}
                    </p>
                  </div>
                  <button
                    onClick={() => setSelectedAuditLog(null)}
                    className="grid size-8 place-items-center rounded-lg border border-border hover:bg-muted"
                  >
                    <X className="size-4" />
                  </button>
                </div>

                <div className="space-y-4 text-xs font-mono">
                  <div>
                    <span className="font-sans font-semibold text-muted-foreground block mb-1">
                      Before State
                    </span>
                    <pre className="overflow-x-auto rounded-xl border border-border bg-muted/50 p-3 text-muted-foreground">
                      {selectedAuditLog.before_json || 'null'}
                    </pre>
                  </div>

                  <div>
                    <span className="font-sans font-semibold text-muted-foreground block mb-1">
                      After State
                    </span>
                    <pre className="overflow-x-auto rounded-xl border border-border bg-muted/50 p-3 text-foreground">
                      {selectedAuditLog.after_json || 'null'}
                    </pre>
                  </div>
                </div>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}
