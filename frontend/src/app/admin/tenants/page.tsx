'use client'

import { useState, useMemo, useCallback } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  CalendarPlus,
  CalendarX,
  CheckCircle,
  Clock,
  Download,
  Eye,
  Film,
  Gauge,
  LogIn,
  MonitorPlay,
  MoreHorizontal,
  RefreshCw,
  RotateCcw,
  Sliders,
  Trash2,
  XCircle,
} from 'lucide-react'
import { adminApi } from '@/lib/api'
import type { TenantSummary } from '@/lib/types'
import { GrantLimitsDialog } from '@/components/admin/grant-limits-dialog'
import { useConfirm } from '@/components/admin/kit/confirm-dialog'
import {
  AdminDataTable,
  type DataTableColumn,
  type ColumnSortState,
} from '@/components/admin/kit/admin-data-table'
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
} from '@/components/ui/dropdown-menu'
import {
  Feedback,
  PageHeader,
  QuotaBar,
  StatCard,
  StatusPill,
  formatBytes,
} from '@/components/admin/admin-ui'
import { useAuthStore } from '@/lib/store'
import { computeTenantHealth } from '@/lib/tenant-health'

const formatWindowEnd = (value?: string | null) =>
  value
    ? new Date(value).toLocaleDateString(undefined, {
        day: 'numeric',
        month: 'short',
        year: 'numeric',
      })
    : 'no end date'

function PlanWindow({ tenant }: { tenant: TenantSummary }) {
  const state = tenant.subscription_state
  if (!state) return <span className="text-xs text-muted-foreground">—</span>

  const tone =
    state === 'expired'
      ? 'border-rose-500/20 bg-rose-500/10 text-rose-700 dark:text-rose-400'
      : state === 'grace'
        ? 'border-amber-500/20 bg-amber-500/10 text-amber-700 dark:text-amber-400'
        : 'border-emerald-500/20 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400'
  const label = state === 'expired' ? 'Ended' : state === 'grace' ? 'Grace' : 'Running'

  return (
    <div className="space-y-0.5">
      <span className={`inline-block rounded-md border px-2 py-0.5 text-[11px] font-medium ${tone}`}>
        {label}
      </span>
      <p className="text-[11px] text-muted-foreground">
        {tenant.current_period_end ? `to ${formatWindowEnd(tenant.current_period_end)}` : 'no end date'}
      </p>
    </div>
  )
}

function HealthBadge({ tenant }: { tenant: TenantSummary }) {
  const health = computeTenantHealth(tenant)
  const toneClasses = {
    emerald: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400',
    amber: 'border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400',
    rose: 'border-rose-500/30 bg-rose-500/10 text-rose-700 dark:text-rose-400',
  }[health.tone]

  return (
    <div
      title={health.reasons.join(' • ')}
      className="inline-flex cursor-help items-center gap-1.5"
    >
      <span
        className={`inline-flex items-center gap-1 rounded-md border px-2 py-0.5 font-mono text-xs font-semibold ${toneClasses}`}
      >
        <span
          className={`size-1.5 rounded-full ${
            health.tone === 'emerald'
              ? 'bg-emerald-500'
              : health.tone === 'amber'
                ? 'bg-amber-500'
                : 'bg-rose-500'
          }`}
        />
        {health.score}
      </span>
    </div>
  )
}

function exportTenantsToCsv(tenants: TenantSummary[]) {
  const headers = [
    'ID',
    'Name',
    'Slug',
    'Owner Email',
    'Status',
    'Plan Name',
    'Subscription State',
    'Screens Online',
    'Screens Total',
    'Ad Slots Used',
    'Ad Slots Max',
    'Storage Used (B)',
    'Created At',
  ]
  const rows = tenants.map((t) => [
    t.id,
    `"${(t.name || '').replace(/"/g, '""')}"`,
    t.slug,
    t.owner_email || '',
    t.status,
    t.plan_name || '',
    t.subscription_state || '',
    t.online_screens_count,
    t.screens_count,
    t.ad_slots_used,
    t.max_ad_slots,
    t.storage_used_bytes,
    t.created_at || '',
  ])

  const csvContent =
    'data:text/csv;charset=utf-8,' +
    [headers.join(','), ...rows.map((r) => r.join(','))].join('\n')
  const encodedUri = encodeURI(csvContent)
  const link = document.createElement('a')
  link.setAttribute('href', encodedUri)
  link.setAttribute('download', `mr_signage_tenants_${new Date().toISOString().slice(0, 10)}.csv`)
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
}

export default function AdminTenantsPage() {
  const router = useRouter()
  const queryClient = useQueryClient()
  const confirm = useConfirm()
  const enterWorkspace = useAuthStore((s) => s.enterWorkspace)

  // Query state for server pagination
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(25)
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState<string>('all')
  const [planFilter, setPlanFilter] = useState<string>('all')
  const [sorting, setSorting] = useState<ColumnSortState | null>({ key: 'created', direction: 'desc' })

  // Modal states
  const [quotaFor, setQuotaFor] = useState<TenantSummary | null>(null)
  const [grantFor, setGrantFor] = useState<TenantSummary | null>(null)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  // Backend sort param mapping
  const backendSort = useMemo((): 'created_desc' | 'created_asc' | 'name_asc' | 'name_desc' => {
    if (!sorting) return 'created_desc'
    if (sorting.key === 'name') {
      return sorting.direction === 'asc' ? 'name_asc' : 'name_desc'
    }
    return sorting.direction === 'asc' ? 'created_asc' : 'created_desc'
  }, [sorting])

  // Paged query
  const {
    data: pagedData,
    isLoading,
    isFetching,
    refetch,
  } = useQuery({
    queryKey: ['admin', 'tenants', page, pageSize, search, statusFilter, planFilter, backendSort],
    queryFn: () =>
      adminApi.listTenantsPaged({
        status: statusFilter === 'all' ? undefined : statusFilter,
        q: search.trim() || undefined,
        plan_id: planFilter === 'all' ? undefined : Number(planFilter),
        sort: backendSort,
        limit: pageSize,
        offset: (page - 1) * pageSize,
      }),
  })

  // Packages list for quota modal & filters
  const { data: packages = [] } = useQuery({
    queryKey: ['admin', 'packages'],
    queryFn: adminApi.listPackages,
  })

  // Full list for top metric totals
  const { data: allTenants = [] } = useQuery({
    queryKey: ['admin', 'tenants', 'all'],
    queryFn: () => adminApi.listTenants(),
  })

  const tenants = pagedData?.items || []
  const total = pagedData?.total || 0

  const done = (text: string) => {
    setError('')
    setMessage(text)
    queryClient.invalidateQueries({ queryKey: ['admin', 'tenants'] })
  }
  const failed = (e: Error) => {
    setMessage('')
    setError(e.message)
  }

  const setStatus = useMutation({
    mutationFn: ({ id, action, reason }: { id: number; action: 'suspend' | 'reinstate'; reason?: string }) =>
      action === 'suspend' ? adminApi.suspendTenant(id, reason) : adminApi.reinstateTenant(id),
    onSuccess: (tenant) => done(`${tenant.name} is now ${tenant.status}.`),
    onError: failed,
  })

  const setPlanWindow = useMutation({
    mutationFn: ({
      id,
      body,
      reason,
    }: {
      id: number
      body: { extend_days?: number; status?: 'active' | 'expired' }
      reason?: string
    }) => adminApi.updateSubscription(id, body, reason),
    onSuccess: (tenant) =>
      done(
        tenant.subscription_state === 'expired'
          ? `${tenant.name}'s plan has been ended. Their screens now show the demo reel.`
          : `${tenant.name}'s plan runs until ${formatWindowEnd(tenant.current_period_end)}.`,
      ),
    onError: failed,
  })

  const setRemoved = useMutation({
    mutationFn: ({ id, action, reason }: { id: number; action: 'remove' | 'restore'; reason?: string }) =>
      action === 'remove' ? adminApi.removeTenant(id, reason) : adminApi.restoreTenant(id),
    onSuccess: (tenant) =>
      done(
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
    mutationFn: ({
      id,
      body,
    }: {
      id: number
      body: { plan_id?: number; max_screens: number; max_ad_slots: number }
    }) => adminApi.updateQuota(id, body),
    onSuccess: (tenant) => {
      setQuotaFor(null)
      done(`Limits updated for ${tenant.name}.`)
    },
    onError: failed,
  })

  const totals = useMemo(() => {
    return {
      active: allTenants.filter((t) => t.status === 'active' && !t.deleted_at).length,
      pending: allTenants.filter((t) => t.status === 'pending_approval').length,
      screens: allTenants.reduce((s, t) => s + t.screens_count, 0),
      online: allTenants.reduce((s, t) => s + t.online_screens_count, 0),
      ads: allTenants.reduce((s, t) => s + t.ad_slots_used, 0),
    }
  }, [allTenants])

  const handleActAs = useCallback(
    (tenant: TenantSummary, readOnly: boolean) => {
      enterWorkspace({ id: tenant.id, name: tenant.name, readOnly })
      router.push('/dashboard')
    },
    [enterWorkspace, router],
  )

  const columns: DataTableColumn<TenantSummary>[] = useMemo(
    () => [
      {
        id: 'workspace',
        header: 'Workspace',
        sortable: true,
        sortKey: 'name',
        cell: ({ row }) => (
          <div className="space-y-0.5">
            <Link
              href={`/admin/tenants/${row.id}`}
              className="font-semibold text-foreground hover:underline"
              onClick={(e) => e.stopPropagation()}
            >
              {row.name}
            </Link>
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <span className="font-mono">{row.slug}</span>
              {row.owner_email && (
                <>
                  <span>•</span>
                  <span className="font-mono text-emerald-700 dark:text-emerald-400">
                    {row.owner_email}
                  </span>
                </>
              )}
            </div>
          </div>
        ),
      },
      {
        id: 'status',
        header: 'Status',
        width: 'w-28',
        cell: ({ row }) =>
          row.deleted_at ? (
            <span className="inline-block rounded-md border border-rose-500/30 bg-rose-500/10 px-2 py-0.5 text-[11px] font-semibold text-rose-700 dark:text-rose-400">
              Removed
            </span>
          ) : (
            <StatusPill status={row.status} />
          ),
      },
      {
        id: 'plan',
        header: 'Package & Window',
        cell: ({ row }) => (
          <div className="space-y-1">
            <span className="text-xs font-medium text-foreground">
              {row.plan_name ?? 'Custom / Bespoke'}
            </span>
            <PlanWindow tenant={row} />
          </div>
        ),
      },
      {
        id: 'health',
        header: 'Health',
        width: 'w-20',
        align: 'center',
        cell: ({ row }) => <HealthBadge tenant={row} />,
      },
      {
        id: 'screens',
        header: 'Screens',
        width: 'w-32',
        hideOnMobile: true,
        cell: ({ row }) => (
          <div className="space-y-1">
            <QuotaBar used={row.screens_count} max={row.max_screens} />
            <p className="text-[10px] text-muted-foreground">
              {row.online_screens_count} of {row.screens_count} online
            </p>
          </div>
        ),
      },
      {
        id: 'ads',
        header: 'Ad Slots',
        width: 'w-28',
        hideOnMobile: true,
        cell: ({ row }) => <QuotaBar used={row.ad_slots_used} max={row.max_ad_slots} />,
      },
      {
        id: 'storage',
        header: 'Storage',
        width: 'w-32',
        hideOnMobile: true,
        cell: ({ row }) => (
          <div className="text-xs text-muted-foreground">
            <p className="font-medium text-foreground">{formatBytes(row.storage_used_bytes)}</p>
            <p className="text-[11px]">
              {row.storage_quota_bytes ? `of ${formatBytes(row.storage_quota_bytes)}` : 'Unlimited'}
            </p>
          </div>
        ),
      },
      {
        id: 'actions',
        header: '',
        align: 'right',
        width: 'w-12',
        cell: ({ row: tenant }) => (
          <div onClick={(e) => e.stopPropagation()}>
            <DropdownMenu>
              <DropdownMenuTrigger
                className="grid size-8 place-items-center rounded-lg border border-border bg-background hover:bg-muted text-foreground transition-colors"
                aria-label={`Actions for ${tenant.name}`}
              >
                <MoreHorizontal className="size-4" />
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-56">
                <DropdownMenuItem onClick={() => router.push(`/admin/tenants/${tenant.id}`)}>
                  <Eye className="size-4" />
                  <span>Tenant 360</span>
                </DropdownMenuItem>

                {!tenant.deleted_at && (
                  <>
                    <DropdownMenuItem onClick={() => handleActAs(tenant, false)}>
                      <LogIn className="size-4" />
                      <span>Enter workspace</span>
                    </DropdownMenuItem>
                    <DropdownMenuItem onClick={() => handleActAs(tenant, true)}>
                      <Eye className="size-4" />
                      <span>View (read-only)</span>
                    </DropdownMenuItem>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem
                      onClick={() => {
                        setGrantFor(tenant)
                        setMessage('')
                        setError('')
                      }}
                    >
                      <Sliders className="size-4 text-fuchsia-500" />
                      <span>Grant limits & features</span>
                    </DropdownMenuItem>
                    <DropdownMenuItem
                      onClick={() => {
                        setQuotaFor(tenant)
                        setMessage('')
                        setError('')
                      }}
                    >
                      <Gauge className="size-4" />
                      <span>Edit quota</span>
                    </DropdownMenuItem>
                    <DropdownMenuItem
                      onClick={() => setPlanWindow.mutate({ id: tenant.id, body: { extend_days: 30 } })}
                    >
                      <CalendarPlus className="size-4 text-sky-500" />
                      <span>+30 days extend</span>
                    </DropdownMenuItem>
                    {tenant.subscription_state === 'active' && (
                      <DropdownMenuItem
                        onClick={async () => {
                          const result = await confirm({
                            title: `End ${tenant.name}'s plan now?`,
                            description:
                              'Their adverts stop and their screens show the demo reel. Nothing is deleted, and extending the plan puts it all back.',
                            impact: `${tenant.screens_count} TV${tenant.screens_count === 1 ? '' : 's'} switch to the demo reel.`,
                            level: 2,
                            confirmLabel: 'End plan',
                          })
                          if (result) {
                            setPlanWindow.mutate({
                              id: tenant.id,
                              body: { status: 'expired' },
                              reason: result.reason,
                            })
                          }
                        }}
                      >
                        <CalendarX className="size-4 text-amber-500" />
                        <span>End plan</span>
                      </DropdownMenuItem>
                    )}
                    <DropdownMenuSeparator />
                    {tenant.status === 'suspended' ? (
                      <DropdownMenuItem
                        onClick={() => setStatus.mutate({ id: tenant.id, action: 'reinstate' })}
                      >
                        <CheckCircle className="size-4 text-emerald-500" />
                        <span>Reinstate workspace</span>
                      </DropdownMenuItem>
                    ) : (
                      <DropdownMenuItem
                        onClick={async () => {
                          const result = await confirm({
                            title: `Block ${tenant.name}?`,
                            description:
                              'Their dashboard and API access stop immediately. Reinstating gives it back.',
                            level: 2,
                            destructive: true,
                            confirmLabel: 'Block workspace',
                          })
                          if (result) {
                            setStatus.mutate({ id: tenant.id, action: 'suspend', reason: result.reason })
                          }
                        }}
                      >
                        <XCircle className="size-4 text-rose-500" />
                        <span>Block workspace</span>
                      </DropdownMenuItem>
                    )}
                    <DropdownMenuItem
                      onClick={async () => {
                        const result = await confirm({
                          title: `Remove ${tenant.name}?`,
                          description:
                            'Their access stops now, and in 30 days every screen, advert, playlist and file in this workspace is deleted permanently. It can be restored at any point before then.',
                          level: 3,
                          confirmText: tenant.name,
                          destructive: true,
                          confirmLabel: 'Remove workspace',
                        })
                        if (result) {
                          setRemoved.mutate({ id: tenant.id, action: 'remove', reason: result.reason })
                        }
                      }}
                    >
                      <Trash2 className="size-4 text-rose-500" />
                      <span>Remove workspace</span>
                    </DropdownMenuItem>
                  </>
                )}

                {tenant.deleted_at && (
                  <>
                    <DropdownMenuItem onClick={() => setRemoved.mutate({ id: tenant.id, action: 'restore' })}>
                      <RotateCcw className="size-4 text-emerald-500" />
                      <span>Restore workspace</span>
                    </DropdownMenuItem>
                    <DropdownMenuItem
                      onClick={async () => {
                        const result = await confirm({
                          title: `Delete ${tenant.name} permanently?`,
                          description:
                            'Every screen, advert, playlist, user and file in this workspace is erased from the database and storage. This cannot be undone.',
                          level: 3,
                          confirmText: tenant.name,
                          destructive: true,
                          confirmLabel: 'Delete permanently',
                        })
                        if (result) {
                          deleteNow.mutate({ id: tenant.id, name: tenant.name, reason: result.reason })
                        }
                      }}
                    >
                      <Trash2 className="size-4 text-rose-500" />
                      <span>Delete permanently now</span>
                    </DropdownMenuItem>
                  </>
                )}
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        ),
      },
    ],
    [
      confirm,
      deleteNow,
      handleActAs,
      router,
      setPlanWindow,
      setRemoved,
      setStatus,
    ],
  )

  return (
    <div className="space-y-6 p-6 text-foreground lg:p-8">
      <PageHeader
        title="Tenant Directory"
        description="Comprehensive lifecycle controls, health metrics and quota management across all workspaces"
      >
        <div className="flex items-center gap-2">
          <button
            onClick={() => exportTenantsToCsv(allTenants.length ? allTenants : tenants)}
            className="flex items-center gap-2 rounded-xl border border-border bg-card px-4 py-2 text-sm text-foreground transition-all hover:bg-muted"
            title="Export CSV of all workspaces"
          >
            <Download className="size-3.5" />
            Export CSV
          </button>
          <button
            onClick={() => {
              refetch()
              queryClient.invalidateQueries({ queryKey: ['admin', 'tenants', 'all'] })
            }}
            className="flex items-center gap-2 rounded-xl border border-border bg-muted px-4 py-2 text-sm text-muted-foreground transition-all hover:bg-muted/80"
          >
            <RefreshCw className={`size-3.5 ${isFetching ? 'animate-spin' : ''}`} />
            Refresh
          </button>
        </div>
      </PageHeader>

      <Feedback ok={message} error={error} />

      {grantFor && (
        <GrantLimitsDialog
          tenant={grantFor}
          onClose={() => {
            setGrantFor(null)
            done(`Limits updated for ${grantFor.name}.`)
          }}
        />
      )}

      {quotaFor && (
        <QuotaDialog
          tenant={quotaFor}
          packages={packages}
          saving={saveQuota.isPending}
          onCancel={() => setQuotaFor(null)}
          onSave={(body) => saveQuota.mutate({ id: quotaFor.id, body })}
        />
      )}

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard label="Active workspaces" value={totals.active} icon={CheckCircle} accent="emerald" />
        <StatCard label="Pending approvals" value={totals.pending} icon={Clock} accent="amber" />
        <StatCard
          label="TVs online (of paired)"
          value={`${totals.online} / ${totals.screens}`}
          icon={MonitorPlay}
          accent="violet"
        />
        <StatCard label="Active ad placements" value={totals.ads} icon={Film} accent="cyan" />
      </div>

      <AdminDataTable
        data={tenants}
        columns={columns}
        isLoading={isLoading}
        onRowClick={(row) => router.push(`/admin/tenants/${row.id}`)}
        search={{
          value: search,
          onChange: (val) => {
            setSearch(val)
            setPage(1)
          },
          placeholder: 'Search workspace name, slug or owner email…',
        }}
        sort={sorting}
        onSortChange={(newSort: ColumnSortState | null) => {
          setSorting(newSort)
          setPage(1)
        }}
        pagination={{
          page,
          pageSize,
          totalCount: total,
          onPageChange: setPage,
          onPageSizeChange: (newSize) => {
            setPageSize(newSize)
            setPage(1)
          },
          pageSizeOptions: [10, 25, 50, 100],
        }}
        filters={
          <div className="flex flex-wrap items-center gap-2">
            <select
              value={statusFilter}
              onChange={(e) => {
                setStatusFilter(e.target.value)
                setPage(1)
              }}
              className="rounded-lg border border-border bg-background px-3 py-1.5 text-xs text-foreground outline-none focus:border-ring"
            >
              <option value="all">All statuses</option>
              <option value="active">Active</option>
              <option value="pending_approval">Pending approval</option>
              <option value="suspended">Suspended</option>
            </select>

            <select
              value={planFilter}
              onChange={(e) => {
                setPlanFilter(e.target.value)
                setPage(1)
              }}
              className="rounded-lg border border-border bg-background px-3 py-1.5 text-xs text-foreground outline-none focus:border-ring"
            >
              <option value="all">All packages</option>
              {packages.map((pkg) => (
                <option key={pkg.id} value={pkg.id}>
                  {pkg.name}
                </option>
              ))}
            </select>
          </div>
        }
      />
    </div>
  )
}

function QuotaDialog({
  tenant,
  packages,
  saving,
  onCancel,
  onSave,
}: {
  tenant: TenantSummary
  packages: { id: number; name: string; max_screens: number | null; max_ad_slots: number; is_active: boolean }[]
  saving: boolean
  onCancel: () => void
  onSave: (body: { plan_id?: number; max_screens: number; max_ad_slots: number }) => void
}) {
  const [planId, setPlanId] = useState<number | ''>(tenant.plan_id ?? '')
  const [screens, setScreens] = useState(tenant.max_screens_override)
  const [ads, setAds] = useState(tenant.max_ad_slots_override)

  const choosePackage = (value: number | '') => {
    setPlanId(value)
    const pkg = packages.find((p) => p.id === value)
    if (pkg) {
      setScreens(pkg.max_screens ?? 0)
      setAds(pkg.max_ad_slots)
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm"
      onClick={onCancel}
    >
      <div
        className="w-full max-w-md space-y-5 rounded-3xl border border-border bg-card p-6 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
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
            {packages
              .filter((p) => p.is_active || p.id === tenant.plan_id)
              .map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
          </select>
        </label>

        <label className="block">
          <span className="mb-1.5 block text-xs font-semibold text-muted-foreground">
            Max TV screens <span className="font-normal text-muted-foreground">(0 = follow the package)</span>
          </span>
          <input
            type="number"
            min={0}
            value={screens}
            onChange={(e) => setScreens(Number(e.target.value) || 0)}
            className="w-full rounded-xl border border-border bg-muted px-4 py-2.5 text-sm text-foreground outline-none focus:border-ring"
          />
          <p className="mt-1 text-xs text-muted-foreground">
            Currently using {tenant.screens_count}. For unlimited, use Grant limits.
          </p>
        </label>

        <label className="block">
          <span className="mb-1.5 block text-xs font-semibold text-muted-foreground">
            Max ad slots <span className="font-normal text-muted-foreground">(0 = follow the package)</span>
          </span>
          <input
            type="number"
            min={0}
            value={ads}
            onChange={(e) => setAds(Number(e.target.value) || 0)}
            className="w-full rounded-xl border border-border bg-muted px-4 py-2.5 text-sm text-foreground outline-none focus:border-ring"
          />
          <p className="mt-1 text-xs text-muted-foreground">Currently using {tenant.ad_slots_used}.</p>
        </label>

        <div className="flex gap-3 pt-1">
          <button
            onClick={() =>
              onSave({
                ...(planId === '' ? {} : { plan_id: planId }),
                max_screens: screens,
                max_ad_slots: ads,
              })
            }
            disabled={saving}
            className="flex-1 rounded-xl bg-primary py-2.5 text-sm font-semibold text-primary-foreground transition-all hover:bg-primary/90 disabled:opacity-50"
          >
            {saving ? 'Saving…' : 'Save limits'}
          </button>
          <button
            onClick={onCancel}
            className="rounded-xl border border-border bg-muted px-5 py-2.5 text-sm text-muted-foreground transition-all hover:bg-muted"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  )
}
