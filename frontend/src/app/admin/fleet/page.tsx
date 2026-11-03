'use client'

import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Activity,
  AlertTriangle,
  CheckCircle,
  Download,
  Eye,
  Lock,
  MapPin,
  Maximize,
  MonitorPlay,
  RefreshCw,
  Rocket,
  Search,
  Send,
  Shield,
  ShieldAlert,
  ShieldCheck,
  Tv,
  Unlock,
  X,
} from 'lucide-react'
import { adminApi, api } from '@/lib/api'
import type { AppRelease, FleetScreen } from '@/lib/types'
import { PageHeader, StatCard } from '@/components/admin/admin-ui'
import { MaintenancePinCard } from '@/components/admin/maintenance-pin-card'
import { AdminDataTable, type DataTableColumn } from '@/components/admin/kit/admin-data-table'
import { ScreenMap, type MapPoint } from '@/components/dashboard/screen-map'
import { toast } from 'sonner'

export default function AdminFleetPage() {
  const queryClient = useQueryClient()
  const [viewMode, setViewMode] = useState<'table' | 'map'>('table')
  const [selectedScreen, setSelectedScreen] = useState<FleetScreen | null>(null)

  // Filters
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState<'all' | 'online' | 'offline'>('all')
  const [lockFilter, setLockFilter] = useState<'all' | 'owner' | 'standard'>('all')
  const [secretFilter, setSecretFilter] = useState<'all' | 'missing' | 'has_secret'>('all')
  const [updateFilter, setUpdateFilter] = useState<'all' | 'failed' | 'updating' | 'latest'>('all')

  // Bulk selection
  const [selectedScreenIds, setSelectedScreenIds] = useState<number[]>([])
  const [bulkConfirmModal, setBulkConfirmModal] = useState<{
    command: 'check_update' | 'bring_to_front' | 'request_screenshot'
    commandLabel: string
  } | null>(null)
  const [bulkReason, setBulkReason] = useState('')

  const { data, isLoading, isFetching } = useQuery({
    queryKey: ['admin', 'fleet'],
    queryFn: adminApi.getFleet,
    refetchInterval: 15000,
  })

  const { data: releases } = useQuery({ queryKey: ['releases'], queryFn: api.getReleases })
  const installable = useMemo(
    () => (releases ?? []).filter((r) => r.rollout_state === 'released' && r.sha256),
    [releases],
  )

  const latest = data?.latest_version_name ?? null
  const versions = data ? Object.entries(data.versions).sort((a, b) => b[1] - a[1]) : []

  // Filtered screens for table & map
  const screens = data?.screens
  const filteredScreens = useMemo(() => {
    if (!screens) return []
    return screens.filter((s) => {
      // Search
      if (search.trim()) {
        const q = search.toLowerCase()
        const matchesName = s.name?.toLowerCase().includes(q)
        const matchesOrg = s.organization_name.toLowerCase().includes(q)
        const matchesDevice = s.device_id?.toLowerCase().includes(q)
        const matchesVersion = s.app_version?.toLowerCase().includes(q)
        if (!matchesName && !matchesOrg && !matchesDevice && !matchesVersion) return false
      }
      // Status
      if (statusFilter === 'online' && !s.online) return false
      if (statusFilter === 'offline' && s.online) return false
      // Lock
      if (lockFilter === 'owner' && !s.device_owner) return false
      if (lockFilter === 'standard' && s.device_owner) return false
      // Secret
      if (secretFilter === 'missing' && s.has_device_secret) return false
      if (secretFilter === 'has_secret' && !s.has_device_secret) return false
      // Update
      if (updateFilter === 'failed') {
        if (s.update_status !== 'failed' && s.update_status !== 'rolled_back' && s.update_failure_count === 0)
          return false
      }
      if (updateFilter === 'updating') {
        if (s.update_status !== 'pending' && s.update_status !== 'downloading' && s.update_status !== 'installing')
          return false
      }
      if (updateFilter === 'latest') {
        if (!latest || s.app_version !== latest) return false
      }
      return true
    })
  }, [screens, search, statusFilter, lockFilter, secretFilter, updateFilter, latest])

  // Map points
  const mapPoints: MapPoint[] = useMemo(() => {
    return filteredScreens
      .filter((s) => s.latitude && s.longitude)
      .map((s) => ({
        id: s.id,
        name: s.name || `TV ${s.id}`,
        status: s.online ? 'online' : 'offline',
        location: s.location || s.organization_name,
        latitude: s.latitude!,
        longitude: s.longitude!,
        model: s.model,
      }))
  }, [filteredScreens])

  // Bulk command mutation
  const bulkCommandMutation = useMutation({
    mutationFn: async ({
      command,
      reason,
    }: {
      command: 'check_update' | 'bring_to_front' | 'request_screenshot'
      reason: string
    }) => {
      return adminApi.sendBulkScreenCommands({
        screen_ids: selectedScreenIds.slice(0, 100),
        command,
        reason,
      })
    },
    onSuccess: (res) => {
      toast.success(`Dispatched ${res.command} to ${res.queued_count} of ${res.total} screens`)
      setBulkConfirmModal(null)
      setBulkReason('')
      setSelectedScreenIds([])
      queryClient.invalidateQueries({ queryKey: ['admin', 'fleet'] })
    },
    onError: (err: Error) => {
      toast.error(`Bulk command failed: ${err.message}`)
    },
  })

  // Table columns
  const columns: DataTableColumn<FleetScreen>[] = [
    {
      id: 'select',
      header: () => (
        <input
          type="checkbox"
          checked={filteredScreens.length > 0 && selectedScreenIds.length === filteredScreens.length}
          onChange={(e) => {
            if (e.target.checked) {
              setSelectedScreenIds(filteredScreens.map((s) => s.id))
            } else {
              setSelectedScreenIds([])
            }
          }}
          className="size-4 rounded border-border"
          aria-label="Select all screens"
        />
      ),
      cell: ({ row }) => (
        <input
          type="checkbox"
          checked={selectedScreenIds.includes(row.id)}
          onChange={(e) => {
            if (e.target.checked) {
              setSelectedScreenIds((prev) => [...prev, row.id])
            } else {
              setSelectedScreenIds((prev) => prev.filter((id) => id !== row.id))
            }
          }}
          className="size-4 rounded border-border"
          aria-label={`Select screen ${row.id}`}
        />
      ),
    },
    {
      id: 'name',
      header: 'Screen / TV',
      accessorKey: 'name',
      cell: ({ row }) => (
        <div className="flex flex-col">
          <span className="font-medium text-foreground">{row.name || `Screen ${row.id}`}</span>
          <span className="font-mono text-[11px] text-muted-foreground">{row.device_id || `ID: ${row.id}`}</span>
        </div>
      ),
    },
    {
      id: 'workspace',
      header: 'Workspace',
      accessorKey: 'organization_name',
      cell: ({ row }) => (
        <div className="flex flex-col">
          <span className="text-foreground">{row.organization_name}</span>
          {row.location && (
            <span className="flex items-center gap-1 text-[11px] text-muted-foreground">
              <MapPin className="size-2.5" />
              {row.location}
            </span>
          )}
        </div>
      ),
    },
    {
      id: 'lock_strength',
      header: 'Lock Strength',
      cell: ({ row }) => (
        <div>
          {row.device_owner ? (
            <span
              title="Device Owner mode active: Full kiosk lock. Home & Back buttons are intercepted."
              className="inline-flex items-center gap-1 rounded-md border border-emerald-500/20 bg-emerald-500/10 px-2 py-0.5 text-[11px] font-medium text-emerald-700 dark:text-emerald-400"
            >
              <Lock className="size-3" />
              Full Kiosk Lock
            </span>
          ) : (
            <span
              title="Standard Install: Back button intercepted. Note: Android hardware limitation prevents blocking Home button without Device Owner provisioning."
              className="inline-flex items-center gap-1 rounded-md border border-amber-500/20 bg-amber-500/10 px-2 py-0.5 text-[11px] font-medium text-amber-700 dark:text-amber-400"
            >
              <Unlock className="size-3" />
              Standard (Back only)
            </span>
          )}
        </div>
      ),
    },
    {
      id: 'status',
      header: 'Status',
      cell: ({ row }) => (
        <span
          className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs ${
            row.online ? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-400' : 'bg-muted text-muted-foreground'
          }`}
        >
          <span className={`size-1.5 rounded-full ${row.online ? 'bg-emerald-400' : 'bg-muted-foreground'}`} />
          {row.online ? 'Online' : 'Offline'}
        </span>
      ),
    },
    {
      id: 'version',
      header: 'Version',
      cell: ({ row }) => {
        const isLatest = latest !== null && row.app_version === latest
        return (
          <div className="flex flex-col">
            <span
              className={`font-mono text-xs ${
                isLatest ? 'text-emerald-700 dark:text-emerald-400 font-semibold' : 'text-muted-foreground'
              }`}
            >
              {row.app_version || 'unknown'} {isLatest && '✓'}
            </span>
            {row.target_version_code && (
              <span className="text-[10px] text-muted-foreground">Pinned: build {row.target_version_code}</span>
            )}
          </div>
        )
      },
    },
    {
      id: 'secret',
      header: 'Secret Auth',
      cell: ({ row }) => (
        <div>
          {row.has_device_secret ? (
            <span
              title="Secure: TV authenticates using its own device secret token."
              className="inline-flex items-center gap-1 text-[11px] text-emerald-700 dark:text-emerald-400"
            >
              <ShieldCheck className="size-3.5" />
              Secret OK
            </span>
          ) : (
            <span
              title="Legacy auth risk: TV holds no secret. Disabling legacy auth will strand this device."
              className="inline-flex items-center gap-1 text-[11px] font-medium text-amber-700 dark:text-amber-400"
            >
              <ShieldAlert className="size-3.5" />
              No Secret
            </span>
          )}
        </div>
      ),
    },
    {
      id: 'update_status',
      header: 'Update State',
      cell: ({ row }) => <UpdateChip screen={row} />,
    },
    {
      id: 'last_seen',
      header: 'Last Seen',
      cell: ({ row }) => (
        <span className="text-xs text-muted-foreground">
          {row.last_seen ? new Date(row.last_seen).toLocaleString() : '—'}
        </span>
      ),
    },
    {
      id: 'actions',
      header: 'Actions',
      cell: ({ row }) => (
        <div className="flex items-center gap-2">
          <button
            onClick={() => setSelectedScreen(row)}
            className="flex items-center gap-1 rounded-lg border border-border bg-muted/60 px-2.5 py-1 text-xs font-medium text-foreground transition hover:bg-muted"
          >
            <Eye className="size-3" />
            Inspect
          </button>
        </div>
      ),
    },
  ]

  return (
    <div className="space-y-6 p-6 text-foreground lg:p-8">
      <PageHeader
        title="TV Fleet Control Center"
        description="Comprehensive real-time telemetry, rollout monitoring, diagnostics, and remote command orchestration"
      >
        <div className="flex items-center gap-2">
          <div className="flex rounded-xl border border-border bg-muted/40 p-1">
            <button
              onClick={() => setViewMode('table')}
              className={`rounded-lg px-3 py-1 text-xs font-medium transition ${
                viewMode === 'table' ? 'bg-card text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'
              }`}
            >
              List View
            </button>
            <button
              onClick={() => setViewMode('map')}
              className={`rounded-lg px-3 py-1 text-xs font-medium transition ${
                viewMode === 'map' ? 'bg-card text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'
              }`}
            >
              Map View ({mapPoints.length})
            </button>
          </div>
          <button
            onClick={() => queryClient.invalidateQueries({ queryKey: ['admin', 'fleet'] })}
            className="flex items-center gap-2 rounded-xl border border-border bg-muted px-4 py-2 text-sm text-muted-foreground transition hover:bg-muted/80"
          >
            <RefreshCw className={`size-3.5 ${isFetching ? 'animate-spin' : ''}`} />
            Refresh
          </button>
        </div>
      </PageHeader>

      <MaintenancePinCard />

      {/* Legacy auth status warning */}
      {data?.legacy_device_auth && (
        <div
          role="status"
          className={`rounded-2xl border p-4 text-sm ${
            data.screens_without_secret === 0
              ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-800 dark:text-emerald-200'
              : 'border-amber-500/30 bg-amber-500/10 text-amber-800 dark:text-amber-200'
          }`}
        >
          <div className="flex items-start gap-3">
            <Shield className="mt-0.5 size-5 shrink-0" />
            <div>
              <p className="font-semibold">
                {data.screens_without_secret === 0
                  ? 'Legacy TV login is enabled, but 100% of the fleet holds a unique device secret.'
                  : `Legacy TV login is active, and ${data.screens_without_secret} screen${
                      data.screens_without_secret === 1 ? '' : 's'
                    } lack a dedicated device secret.`}
              </p>
              <p className="mt-1 opacity-80">
                {data.screens_without_secret === 0
                  ? 'Zero stranded devices will occur if ALLOW_LEGACY_DEVICE_AUTH=false is toggled on backend.'
                  : 'Do not disable legacy auth until these screens update and pair their own cryptographic secrets.'}
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Metrics Grid */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard label="Total Paired TVs" value={data?.total ?? 0} icon={MonitorPlay} accent="violet" />
        <StatCard
          label="Online Now"
          value={`${data?.online ?? 0} / ${data?.total ?? 0}`}
          icon={Activity}
          accent="emerald"
        />
        <StatCard
          label={latest ? `On Latest (${latest})` : 'On Latest Build'}
          value={data?.on_latest ?? 0}
          icon={CheckCircle}
          accent="cyan"
        />
        <StatCard label="Failed / Rolled Back" value={data?.failed ?? 0} icon={AlertTriangle} accent="amber" />
      </div>

      {/* Version Spread Chart */}
      <section className="rounded-2xl border border-border bg-card p-5">
        <div className="mb-3 flex items-center gap-2 text-sm font-semibold">
          <Rocket className="size-4 text-foreground" />
          <span>Release Deployment Spread</span>
          {latest && (
            <span className="ml-2 text-xs font-normal text-muted-foreground">
              Target released version:{' '}
              <span className="font-medium text-emerald-700 dark:text-emerald-400">{latest}</span>
            </span>
          )}
          {data && data.updating > 0 && (
            <span className="ml-auto rounded-full bg-amber-500/10 px-2.5 py-0.5 text-xs text-amber-700 dark:text-amber-300">
              {data.updating} TVs in active update
            </span>
          )}
        </div>
        {versions.length === 0 ? (
          <p className="text-sm text-muted-foreground">No fleet data available.</p>
        ) : (
          <div className="space-y-2">
            {versions.map(([version, count]) => {
              const pct = data && data.total ? Math.round((count / data.total) * 100) : 0
              const isLatest = latest !== null && version === latest
              return (
                <div key={version} className="flex items-center gap-3">
                  <span
                    className={`w-32 shrink-0 truncate font-mono text-xs ${
                      isLatest ? 'font-semibold text-emerald-700 dark:text-emerald-400' : 'text-muted-foreground'
                    }`}
                  >
                    {version} {isLatest && '✓'}
                  </span>
                  <div className="h-2.5 flex-1 overflow-hidden rounded-full bg-muted">
                    <div
                      className={`h-full rounded-full transition-all duration-500 ${
                        isLatest ? 'bg-emerald-500' : 'bg-primary'
                      }`}
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                  <span className="w-16 shrink-0 text-right text-xs text-muted-foreground">
                    {count} ({pct}%)
                  </span>
                </div>
              )
            })}
          </div>
        )}
      </section>

      {/* Multi-screen Command Action Bar */}
      {selectedScreenIds.length > 0 && (
        <div className="sticky top-4 z-20 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-primary/30 bg-card p-4 shadow-lg backdrop-blur">
          <div className="flex items-center gap-2">
            <span className="flex size-6 items-center justify-center rounded-full bg-primary text-xs font-bold text-primary-foreground">
              {selectedScreenIds.length}
            </span>
            <span className="text-sm font-medium text-foreground">
              Screens Selected (capped at 100 per bulk dispatch)
            </span>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() =>
                setBulkConfirmModal({
                  command: 'check_update',
                  commandLabel: 'Force Check for Updates',
                })
              }
              className="flex items-center gap-1.5 rounded-xl border border-border bg-muted/60 px-3 py-1.5 text-xs font-medium text-foreground hover:bg-muted"
            >
              <Download className="size-3.5" />
              Check Updates
            </button>
            <button
              onClick={() =>
                setBulkConfirmModal({
                  command: 'bring_to_front',
                  commandLabel: 'Bring TV Player to Front',
                })
              }
              className="flex items-center gap-1.5 rounded-xl border border-border bg-muted/60 px-3 py-1.5 text-xs font-medium text-foreground hover:bg-muted"
            >
              <Maximize className="size-3.5" />
              Bring to Front
            </button>
            <button
              onClick={() =>
                setBulkConfirmModal({
                  command: 'request_screenshot',
                  commandLabel: 'Request Live Screenshots',
                })
              }
              className="flex items-center gap-1.5 rounded-xl border border-border bg-muted/60 px-3 py-1.5 text-xs font-medium text-foreground hover:bg-muted"
            >
              <Tv className="size-3.5" />
              Request Screenshot
            </button>
            <button
              onClick={() => setSelectedScreenIds([])}
              className="rounded-xl px-2.5 py-1.5 text-xs text-muted-foreground hover:text-foreground"
            >
              Deselect All
            </button>
          </div>
        </div>
      )}

      {/* Main Content: Table or Map */}
      {viewMode === 'map' ? (
        <section className="space-y-4 rounded-2xl border border-border bg-card p-5">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-base font-semibold text-foreground">Fleet Geographic Deployment</h3>
              <p className="text-xs text-muted-foreground">
                Showing {mapPoints.length} screens reporting GPS or location coordinates
              </p>
            </div>
            <button
              onClick={() => setViewMode('table')}
              className="text-xs text-primary underline underline-offset-4"
            >
              Switch to List View
            </button>
          </div>
          <ScreenMap points={mapPoints} height={520} />
        </section>
      ) : (
        <section className="space-y-4 rounded-2xl border border-border bg-card p-5">
          {/* Filters Bar */}
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="relative min-w-[240px] flex-1 max-w-md">
              <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search by name, workspace, device ID, version…"
                className="w-full rounded-xl border border-border bg-muted/40 py-2 pl-9 pr-4 text-xs text-foreground placeholder:text-muted-foreground focus:border-primary focus:outline-none"
              />
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <select
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value as 'all' | 'online' | 'offline')}
                className="rounded-xl border border-border bg-muted/40 px-3 py-2 text-xs text-foreground focus:outline-none"
              >
                <option value="all">All Statuses</option>
                <option value="online">Online Only</option>
                <option value="offline">Offline Only</option>
              </select>

              <select
                value={lockFilter}
                onChange={(e) => setLockFilter(e.target.value as 'all' | 'owner' | 'standard')}
                className="rounded-xl border border-border bg-muted/40 px-3 py-2 text-xs text-foreground focus:outline-none"
              >
                <option value="all">All Lock Modes</option>
                <option value="owner">Device Owner (Full Lock)</option>
                <option value="standard">Standard (Back only)</option>
              </select>

              <select
                value={secretFilter}
                onChange={(e) => setSecretFilter(e.target.value as 'all' | 'missing' | 'has_secret')}
                className="rounded-xl border border-border bg-muted/40 px-3 py-2 text-xs text-foreground focus:outline-none"
              >
                <option value="all">All Auth Types</option>
                <option value="has_secret">Has Device Secret</option>
                <option value="missing">Missing Secret (Legacy)</option>
              </select>

              <select
                value={updateFilter}
                onChange={(e) => setUpdateFilter(e.target.value as 'all' | 'failed' | 'updating' | 'latest')}
                className="rounded-xl border border-border bg-muted/40 px-3 py-2 text-xs text-foreground focus:outline-none"
              >
                <option value="all">All Update States</option>
                <option value="latest">On Latest Release</option>
                <option value="updating">Currently Updating</option>
                <option value="failed">Failed / Rolled Back</option>
              </select>
            </div>
          </div>

          {/* AdminDataTable */}
          <AdminDataTable
            columns={columns}
            data={filteredScreens}
            isLoading={isLoading}
            getRowId={(r) => r.id}
            emptyState={{
              title: 'No screens found',
              description: 'No screens match the current filter selection.',
            }}
          />
        </section>
      )}

      {/* Screen Detail & Remote Command Drawer */}
      {selectedScreen && (
        <FleetScreenDrawer
          screen={selectedScreen}
          releases={installable}
          latestRelease={latest}
          onClose={() => setSelectedScreen(null)}
          onCommandSent={() => queryClient.invalidateQueries({ queryKey: ['admin', 'fleet'] })}
        />
      )}

      {/* Bulk Confirmation Modal (Level 2 with reason) */}
      {bulkConfirmModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
          <div className="w-full max-w-md rounded-2xl border border-border bg-card p-6 shadow-2xl">
            <h3 className="text-lg font-semibold text-foreground">Confirm Bulk Screen Command</h3>
            <p className="mt-2 text-xs text-muted-foreground">
              You are about to execute <strong className="text-foreground">{bulkConfirmModal.commandLabel}</strong> on{' '}
              <strong className="text-foreground">{Math.min(selectedScreenIds.length, 100)} screens</strong>.
            </p>

            <div className="mt-4">
              <label className="text-xs font-medium text-foreground">
                Operator Audit Reason <span className="text-rose-500">* (Level-2 Confirmation)</span>
              </label>
              <textarea
                value={bulkReason}
                onChange={(e) => setBulkReason(e.target.value)}
                placeholder="State operational justification (e.g. emergency app restart, staging verification)..."
                rows={3}
                className="mt-1 w-full rounded-xl border border-border bg-muted/40 p-3 text-xs text-foreground placeholder:text-muted-foreground focus:border-primary focus:outline-none"
              />
            </div>

            <div className="mt-6 flex justify-end gap-3">
              <button
                onClick={() => {
                  setBulkConfirmModal(null)
                  setBulkReason('')
                }}
                className="rounded-xl border border-border px-4 py-2 text-xs font-medium text-foreground hover:bg-muted"
              >
                Cancel
              </button>
              <button
                onClick={() =>
                  bulkCommandMutation.mutate({
                    command: bulkConfirmModal.command,
                    reason: bulkReason,
                  })
                }
                disabled={!bulkReason.trim() || bulkCommandMutation.isPending}
                className="rounded-xl bg-primary px-4 py-2 text-xs font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
              >
                {bulkCommandMutation.isPending ? 'Dispatching…' : 'Confirm & Execute'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function UpdateChip({ screen }: { screen: FleetScreen }) {
  const s = screen.update_status
  if (s === 'failed' || s === 'rolled_back') {
    return (
      <span className="rounded-full border border-rose-500/20 bg-rose-500/10 px-2 py-0.5 text-xs text-rose-700 dark:text-rose-400">
        {s === 'rolled_back' ? 'rolled back' : 'failed'}
        {screen.update_failure_count > 0 ? ` ×${screen.update_failure_count}` : ''}
      </span>
    )
  }
  if (s === 'pending' || s === 'downloading' || s === 'installing') {
    return (
      <span className="rounded-full border border-amber-500/20 bg-amber-500/10 px-2 py-0.5 text-xs text-amber-700 dark:text-amber-300">
        {s}…
      </span>
    )
  }
  if (s === 'success') {
    return (
      <span className="rounded-full border border-emerald-500/20 bg-emerald-500/10 px-2 py-0.5 text-xs text-emerald-700 dark:text-emerald-400">
        up to date
      </span>
    )
  }
  return <span className="text-xs text-muted-foreground">—</span>
}

/**
 * Screen Diagnostics & Remote Command Drawer
 */
function FleetScreenDrawer({
  screen,
  releases,
  latestRelease,
  onClose,
  onCommandSent,
}: {
  screen: FleetScreen
  releases: AppRelease[]
  latestRelease: string | null
  onClose: () => void
  onCommandSent: () => void
}) {
  const [unlockReason, setUnlockReason] = useState('')
  const [showUnlockModal, setShowUnlockModal] = useState(false)
  const [pinChoice, setPinChoice] = useState<string>(screen.target_version_code ? String(screen.target_version_code) : '')

  const commandMutation = useMutation({
    mutationFn: async ({
      command,
      reason,
    }: {
      command: 'check_update' | 'bring_to_front' | 'request_screenshot' | 'remote_unlock'
      reason?: string
    }) => {
      return adminApi.sendScreenCommand(screen.id, { command, reason })
    },
    onSuccess: (data) => {
      toast.success(`Command '${data.command}' ${data.queued ? 'queued successfully' : 'already dispatched'}`)
      onCommandSent()
      setShowUnlockModal(false)
      setUnlockReason('')
    },
    onError: (err: Error) => {
      toast.error(`Command failed: ${err.message}`)
    },
  })

  const updatePinMutation = useMutation({
    mutationFn: () => adminApi.updateScreenNow(screen.id, pinChoice ? Number(pinChoice) : null),
    onSuccess: () => {
      toast.success('Update target updated')
      onCommandSent()
    },
    onError: (err: Error) => toast.error(err.message),
  })

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/60 backdrop-blur-sm">
      <div className="flex h-full w-full max-w-lg flex-col border-l border-border bg-card p-6 shadow-2xl">
        {/* Drawer Header */}
        <div className="flex items-center justify-between border-b border-border pb-4">
          <div>
            <div className="flex items-center gap-2">
              <h3 className="text-lg font-semibold text-foreground">{screen.name || `TV ${screen.id}`}</h3>
              <span
                className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs ${
                  screen.online ? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-400' : 'bg-muted text-muted-foreground'
                }`}
              >
                <span className={`size-1.5 rounded-full ${screen.online ? 'bg-emerald-400' : 'bg-muted-foreground'}`} />
                {screen.online ? 'Online' : 'Offline'}
              </span>
            </div>
            <p className="text-xs text-muted-foreground">Workspace: {screen.organization_name}</p>
          </div>
          <button onClick={onClose} className="rounded-lg p-1 text-muted-foreground hover:bg-muted hover:text-foreground">
            <X className="size-5" />
          </button>
        </div>

        {/* Drawer Content */}
        <div className="flex-1 space-y-6 overflow-y-auto py-6">
          {/* Diagnostic Checklist */}
          <div className="space-y-3 rounded-2xl border border-border bg-muted/20 p-4">
            <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Hardware & Security Diagnostics
            </h4>

            <div className="space-y-2 text-xs">
              <div className="flex items-center justify-between">
                <span className="text-muted-foreground">Lock Strength:</span>
                {screen.device_owner ? (
                  <span className="flex items-center gap-1 text-emerald-700 dark:text-emerald-400 font-medium">
                    <ShieldCheck className="size-3.5" />
                    Device Owner (Full Kiosk Lock)
                  </span>
                ) : (
                  <span className="flex items-center gap-1 text-amber-700 dark:text-amber-400 font-medium">
                    <ShieldAlert className="size-3.5" />
                    Standard Install (Back Button Only)
                  </span>
                )}
              </div>
              {!screen.device_owner && (
                <p className="rounded-lg bg-amber-500/10 p-2 text-[11px] text-amber-800 dark:text-amber-300">
                  Note: Android hardware limitation prevents non-device-owner apps from intercepting the physical Home button.
                </p>
              )}

              <div className="flex items-center justify-between">
                <span className="text-muted-foreground">Device Secret Hash:</span>
                {screen.has_device_secret ? (
                  <span className="flex items-center gap-1 text-emerald-700 dark:text-emerald-400 font-medium">
                    <CheckCircle className="size-3.5" />
                    Present & Verified
                  </span>
                ) : (
                  <span className="flex items-center gap-1 text-rose-700 dark:text-rose-400 font-medium">
                    <AlertTriangle className="size-3.5" />
                    Missing (Legacy Auth Dependent)
                  </span>
                )}
              </div>

              <div className="flex items-center justify-between">
                <span className="text-muted-foreground">Device ID:</span>
                <span className="font-mono text-foreground">{screen.device_id || '—'}</span>
              </div>

              <div className="flex items-center justify-between">
                <span className="text-muted-foreground">TV Model:</span>
                <span className="text-foreground">{screen.model || 'Unknown TV'}</span>
              </div>

              <div className="flex items-center justify-between">
                <span className="text-muted-foreground">Last Heartbeat:</span>
                <span className="text-foreground">{screen.last_seen ? new Date(screen.last_seen).toLocaleString() : 'Never'}</span>
              </div>

              {screen.latitude && screen.longitude && (
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">GPS Location:</span>
                  <span className="font-mono text-foreground">
                    {screen.latitude.toFixed(4)}, {screen.longitude.toFixed(4)}
                  </span>
                </div>
              )}
            </div>
          </div>

          {/* Remote Commands Action Center */}
          <div className="space-y-3 rounded-2xl border border-border bg-muted/20 p-4">
            <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Orchestrate Remote Commands
            </h4>

            <div className="space-y-2">
              <button
                onClick={() => commandMutation.mutate({ command: 'bring_to_front', reason: 'Operator requested focus' })}
                disabled={commandMutation.isPending || !screen.device_id}
                className="flex w-full items-center justify-between rounded-xl border border-border bg-card p-3 text-xs font-medium text-foreground transition hover:bg-muted disabled:opacity-50"
              >
                <div className="flex items-center gap-2">
                  <Maximize className="size-4 text-primary" />
                  <div className="text-left">
                    <p>Bring App to Front</p>
                    <p className="text-[10px] text-muted-foreground">Forces MR Signage app over system launcher or third-party apps</p>
                  </div>
                </div>
                <Send className="size-3.5 text-muted-foreground" />
              </button>

              <button
                onClick={() => commandMutation.mutate({ command: 'check_update', reason: 'Operator forced update poll' })}
                disabled={commandMutation.isPending || !screen.device_id}
                className="flex w-full items-center justify-between rounded-xl border border-border bg-card p-3 text-xs font-medium text-foreground transition hover:bg-muted disabled:opacity-50"
              >
                <div className="flex items-center gap-2">
                  <Download className="size-4 text-emerald-500" />
                  <div className="text-left">
                    <p>Trigger Immediate Update Check</p>
                    <p className="text-[10px] text-muted-foreground">Signals TV device to poll for new APK release binary</p>
                  </div>
                </div>
                <Send className="size-3.5 text-muted-foreground" />
              </button>

              <button
                onClick={() => commandMutation.mutate({ command: 'request_screenshot', reason: 'Operator live preview' })}
                disabled={commandMutation.isPending || !screen.device_id}
                className="flex w-full items-center justify-between rounded-xl border border-border bg-card p-3 text-xs font-medium text-foreground transition hover:bg-muted disabled:opacity-50"
              >
                <div className="flex items-center gap-2">
                  <Tv className="size-4 text-cyan-500" />
                  <div className="text-left">
                    <p>Request Live Screen Capture</p>
                    <p className="text-[10px] text-muted-foreground">Queues an instant screenshot telemetry request</p>
                  </div>
                </div>
                <Send className="size-3.5 text-muted-foreground" />
              </button>

              <button
                onClick={() => setShowUnlockModal(true)}
                disabled={commandMutation.isPending || !screen.device_id}
                className="flex w-full items-center justify-between rounded-xl border border-rose-500/20 bg-rose-500/5 p-3 text-xs font-medium text-rose-700 dark:text-rose-400 transition hover:bg-rose-500/10 disabled:opacity-50"
              >
                <div className="flex items-center gap-2">
                  <Unlock className="size-4 text-rose-500" />
                  <div className="text-left">
                    <p>Emergency Remote Unlock (Level 2)</p>
                    <p className="text-[10px] text-muted-foreground">Temporarily unpins screen kiosk lock for on-site tech maintenance</p>
                  </div>
                </div>
                <Send className="size-3.5" />
              </button>
            </div>
          </div>

          {/* Version Pin Control */}
          <div className="space-y-3 rounded-2xl border border-border bg-muted/20 p-4">
            <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Build Version Pinning
            </h4>
            <p className="text-xs text-muted-foreground">
              Pin this TV to a specific release (canary ring) or leave unpinned to automatically follow the globally released build.
            </p>
            <div className="flex items-center gap-2">
              <select
                value={pinChoice}
                onChange={(e) => setPinChoice(e.target.value)}
                className="flex-1 rounded-xl border border-border bg-card px-3 py-2 text-xs text-foreground focus:outline-none"
              >
                <option value="">Follow Global Released Build ({latestRelease || 'none'})</option>
                {releases.map((r) => (
                  <option key={r.version_code} value={r.version_code}>
                    Pin to {r.version_name} (code: {r.version_code})
                  </option>
                ))}
              </select>
              <button
                onClick={() => updatePinMutation.mutate()}
                disabled={updatePinMutation.isPending}
                className="rounded-xl bg-primary px-3 py-2 text-xs font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
              >
                {updatePinMutation.isPending ? 'Saving…' : 'Apply'}
              </button>
            </div>
          </div>
        </div>

        {/* Level-2 Unlock Confirmation Modal */}
        {showUnlockModal && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm">
            <div className="w-full max-w-md rounded-2xl border border-border bg-card p-6 shadow-2xl">
              <h3 className="text-base font-semibold text-rose-700 dark:text-rose-400">
                Confirm Emergency Remote Kiosk Unlock
              </h3>
              <p className="mt-2 text-xs text-muted-foreground">
                This will release the kiosk lock on <strong className="text-foreground">{screen.name || `TV ${screen.id}`}</strong>, exposing device settings to anyone with the physical remote.
              </p>
              <div className="mt-4">
                <label className="text-xs font-medium text-foreground">
                  Audit Reason <span className="text-rose-500">* (Required)</span>
                </label>
                <textarea
                  value={unlockReason}
                  onChange={(e) => setUnlockReason(e.target.value)}
                  placeholder="State reason for technician kiosk bypass..."
                  rows={3}
                  className="mt-1 w-full rounded-xl border border-border bg-muted/40 p-3 text-xs text-foreground placeholder:text-muted-foreground focus:border-rose-500 focus:outline-none"
                />
              </div>
              <div className="mt-6 flex justify-end gap-3">
                <button
                  onClick={() => setShowUnlockModal(false)}
                  className="rounded-xl border border-border px-4 py-2 text-xs font-medium text-foreground hover:bg-muted"
                >
                  Cancel
                </button>
                <button
                  onClick={() => commandMutation.mutate({ command: 'remote_unlock', reason: unlockReason })}
                  disabled={!unlockReason.trim() || commandMutation.isPending}
                  className="rounded-xl bg-rose-600 px-4 py-2 text-xs font-medium text-white hover:bg-rose-500 disabled:opacity-50"
                >
                  {commandMutation.isPending ? 'Unlocking…' : 'Authorize Unlock'}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
