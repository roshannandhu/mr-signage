'use client'

import { useState, useSyncExternalStore } from 'react'
import Link from 'next/link'
import { useQuery } from '@tanstack/react-query'
import {
  Activity,
  AlertTriangle,
  ArrowUpRight,
  CheckCircle,
  Clock,
  HardDrive,
  IndianRupee,
  Layers,
  MonitorPlay,
  Play,
  QrCode,
  RefreshCw,
  Rocket,
  ShieldAlert,
  ShieldCheck,
  Users,
  XCircle,
} from 'lucide-react'
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { adminApi } from '@/lib/api'
import { formatBytes, relativeTime, rupees } from '@/lib/format'
import type { AdminOverview } from '@/lib/types'

type RangeKey = '7d' | '30d' | '90d'
type SecondaryChart = 'revenue' | 'signups'

const emptySubscribe = () => () => {}
function useMounted() {
  return useSyncExternalStore(emptySubscribe, () => true, () => false)
}

function formatAction(action: string): { label: string; tone: 'emerald' | 'amber' | 'rose' | 'neutral' } {
  if (action.startsWith('tenant.approve')) return { label: 'Approved workspace', tone: 'emerald' }
  if (action.startsWith('tenant.reject')) return { label: 'Rejected workspace', tone: 'rose' }
  if (action.startsWith('tenant.suspend')) return { label: 'Suspended workspace', tone: 'rose' }
  if (action.startsWith('tenant.reinstate')) return { label: 'Reinstated workspace', tone: 'emerald' }
  if (action.startsWith('tenant.remove')) return { label: 'Marked for purge', tone: 'rose' }
  if (action.startsWith('tenant.restore')) return { label: 'Restored workspace', tone: 'emerald' }
  if (action.startsWith('tenant.quota')) return { label: 'Updated quotas', tone: 'neutral' }
  if (action.startsWith('tenant.grant')) return { label: 'Granted custom limits', tone: 'neutral' }
  if (action.startsWith('tenant.subscription')) return { label: 'Updated subscription', tone: 'neutral' }
  if (action.startsWith('tenant.provisioning_qr')) return { label: 'Generated setup QR', tone: 'neutral' }
  if (action.startsWith('screen.remote_unlock')) return { label: 'Remote unlocked screen', tone: 'amber' }
  if (action.startsWith('act_as.write')) return { label: 'Act-as workspace edit', tone: 'amber' }
  if (action.startsWith('plan.')) return { label: 'Modified package', tone: 'neutral' }
  if (action.startsWith('release.')) return { label: 'App release action', tone: 'neutral' }
  return { label: action.replaceAll('_', ' '), tone: 'neutral' }
}

function CustomChartTooltip({
  active,
  payload,
  label,
  valuePrefix = '',
  valueSuffix = '',
}: {
  active?: boolean
  payload?: Array<{ name: string; value: number; color?: string }>
  label?: string
  valuePrefix?: string
  valueSuffix?: string
}) {
  if (!active || !payload || !payload.length) return null
  return (
    <div className="rounded-xl border border-border bg-card/95 p-3 text-xs shadow-xl backdrop-blur">
      <p className="font-semibold text-foreground">{label}</p>
      <div className="mt-1.5 space-y-1">
        {payload.map((item, i) => (
          <div key={`${item.name}-${i}`} className="flex items-center justify-between gap-4">
            <span className="flex items-center gap-1.5 text-muted-foreground">
              <span className="size-2 rounded-full" style={{ backgroundColor: item.color ?? 'currentColor' }} />
              {item.name}:
            </span>
            <span className="font-mono font-medium text-foreground">
              {valuePrefix}
              {typeof item.value === 'number' ? item.value.toLocaleString() : item.value}
              {valueSuffix}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}

export default function AdminOverviewPage() {
  const [range, setRange] = useState<RangeKey>('30d')
  const [secondaryChart, setSecondaryChart] = useState<SecondaryChart>('revenue')
  const mounted = useMounted()

  const {
    data,
    isLoading,
    error,
    refetch,
    isFetching,
  } = useQuery<AdminOverview>({
    queryKey: ['admin', 'overview', range],
    queryFn: () => adminApi.getOverview(range),
    refetchInterval: 30_000,
  })

  // Format chart series by combining online and offline plays
  const playsChartData = data
    ? data.plays_online_by_day.map((p, i) => {
        const off = data.plays_offline_by_day[i]?.value ?? 0
        return {
          date: p.date.slice(5), // MM-DD
          fullDate: p.date,
          Online: p.value,
          Offline: off,
          Total: p.value + off,
        }
      })
    : []

  const totalPlays = playsChartData.reduce((acc, p) => acc + p.Total, 0)
  const totalOnlinePlays = playsChartData.reduce((acc, p) => acc + p.Online, 0)
  const totalOfflinePlays = playsChartData.reduce((acc, p) => acc + p.Offline, 0)
  const totalSignups = data ? data.signups_by_day.reduce((acc, p) => acc + p.value, 0) : 0

  const revenueChartData = data
    ? data.revenue_paise_by_day.map((p) => ({
        date: p.date.slice(5),
        fullDate: p.date,
        Revenue: Math.round(p.value / 100),
      }))
    : []

  const signupsChartData = data
    ? data.signups_by_day.map((p) => ({
        date: p.date.slice(5),
        fullDate: p.date,
        Signups: p.value,
      }))
    : []

  return (
    <div className="space-y-6 p-4 text-foreground sm:p-6 lg:p-8">
      {/* Header bar */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div className="flex items-center gap-2.5">
            <h1 className="text-2xl font-bold tracking-tight text-foreground lg:text-3xl">Mission Control</h1>
            {data?.cached && (
              <span className="rounded-full border border-border px-2 py-0.5 text-[10px] font-semibold tracking-wider text-muted-foreground uppercase">
                Cached (30s)
              </span>
            )}
          </div>
          <p className="mt-1 text-sm text-muted-foreground">
            Live telemetry, platform operations queue and business analytics
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {/* Range picker */}
          <div className="inline-flex rounded-xl border border-border bg-card p-1 text-xs">
            {(['7d', '30d', '90d'] as RangeKey[]).map((key) => (
              <button
                key={key}
                type="button"
                onClick={() => setRange(key)}
                className={`rounded-lg px-3 py-1 font-medium transition-colors ${
                  range === key
                    ? 'bg-foreground text-background shadow-xs'
                    : 'text-muted-foreground hover:text-foreground'
                }`}
              >
                {key}
              </button>
            ))}
          </div>

          <button
            type="button"
            onClick={() => refetch()}
            disabled={isFetching}
            className="inline-flex size-9 items-center justify-center rounded-xl border border-border bg-card text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:opacity-50"
            title="Refresh overview metrics"
            aria-label="Refresh overview metrics"
          >
            <RefreshCw className={`size-4 ${isFetching ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </div>

      {/* Error state */}
      {error && (
        <div className="flex items-center justify-between rounded-2xl border border-rose-500/30 bg-rose-500/10 p-4 text-sm text-rose-700 dark:text-rose-300">
          <div className="flex items-center gap-3">
            <AlertTriangle className="size-5 shrink-0" />
            <div>
              <p className="font-semibold">Unable to load mission control data</p>
              <p className="text-xs text-rose-600 dark:text-rose-400">{(error as Error).message}</p>
            </div>
          </div>
          <button
            type="button"
            onClick={() => refetch()}
            className="rounded-lg border border-rose-500/40 bg-rose-500/20 px-3 py-1.5 text-xs font-semibold hover:bg-rose-500/30"
          >
            Retry
          </button>
        </div>
      )}

      {/* Loading state skeleton */}
      {isLoading && (
        <div className="space-y-6">
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="h-28 animate-pulse rounded-2xl border border-border bg-card/60 p-4" />
            ))}
          </div>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="h-20 animate-pulse rounded-2xl border border-border bg-card/40 p-4" />
            ))}
          </div>
          <div className="h-80 animate-pulse rounded-2xl border border-border bg-card/50" />
        </div>
      )}

      {/* Loaded dashboard */}
      {data && (
        <>
          {/* Top Operational Metrics Grid (6 tiles) */}
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
            {/* Active Tenants */}
            <Link
              href="/admin/tenants"
              className="group flex flex-col justify-between rounded-2xl border border-border bg-card p-4 transition-all hover:border-foreground/30 hover:shadow-xs"
            >
              <div className="flex items-center justify-between">
                <span className="text-xs font-medium text-muted-foreground">Workspaces</span>
                <Users className="size-4 text-muted-foreground transition-colors group-hover:text-foreground" />
              </div>
              <div className="mt-2">
                <p className="text-2xl font-bold tracking-tight text-foreground">{data.tenants_active}</p>
                <p className="mt-0.5 truncate text-[11px] text-muted-foreground">
                  +{totalSignups} new in {range}
                </p>
              </div>
              {data.tenants_suspended > 0 && (
                <span className="mt-2 inline-flex items-center gap-1 text-[10px] font-semibold text-rose-600 dark:text-rose-400">
                  <XCircle className="size-3" /> {data.tenants_suspended} suspended
                </span>
              )}
            </Link>

            {/* TV Fleet Presence */}
            <Link
              href="/admin/fleet"
              className="group flex flex-col justify-between rounded-2xl border border-border bg-card p-4 transition-all hover:border-foreground/30 hover:shadow-xs"
            >
              <div className="flex items-center justify-between">
                <span className="text-xs font-medium text-muted-foreground">Fleet Online</span>
                <MonitorPlay className="size-4 text-muted-foreground transition-colors group-hover:text-foreground" />
              </div>
              <div className="mt-2">
                <p className="text-2xl font-bold tracking-tight text-foreground">
                  {data.screens_online} <span className="text-sm font-normal text-muted-foreground">/ {data.screens_total}</span>
                </p>
                <p className="mt-0.5 truncate text-[11px] text-muted-foreground">
                  {data.screens_total ? Math.round((data.screens_online / data.screens_total) * 100) : 0}% live presence
                </p>
              </div>
              {data.screens_offline_24h > 0 ? (
                <span className="mt-2 inline-flex items-center gap-1 text-[10px] font-semibold text-amber-600 dark:text-amber-400">
                  <AlertTriangle className="size-3" /> {data.screens_offline_24h} offline &gt;24h
                </span>
              ) : (
                <span className="mt-2 inline-flex items-center gap-1 text-[10px] text-emerald-600 dark:text-emerald-400">
                  <CheckCircle className="size-3" /> Healthy fleet
                </span>
              )}
            </Link>

            {/* Total Ad Plays */}
            <div className="flex flex-col justify-between rounded-2xl border border-border bg-card p-4">
              <div className="flex items-center justify-between">
                <span className="text-xs font-medium text-muted-foreground">Ad Plays</span>
                <Play className="size-4 text-muted-foreground" />
              </div>
              <div className="mt-2">
                <p className="text-2xl font-bold tracking-tight text-foreground">
                  {totalPlays.toLocaleString()}
                </p>
                <p className="mt-0.5 truncate text-[11px] text-muted-foreground">
                  {totalOnlinePlays.toLocaleString()} online · {totalOfflinePlays.toLocaleString()} offline
                </p>
              </div>
              <span className="mt-2 text-[10px] text-muted-foreground">
                Proof-of-play verified
              </span>
            </div>

            {/* Collected Revenue */}
            <Link
              href="/admin/billing"
              className="group flex flex-col justify-between rounded-2xl border border-border bg-card p-4 transition-all hover:border-foreground/30 hover:shadow-xs"
            >
              <div className="flex items-center justify-between">
                <span className="text-xs font-medium text-muted-foreground">Collected</span>
                <IndianRupee className="size-4 text-muted-foreground transition-colors group-hover:text-foreground" />
              </div>
              <div className="mt-2">
                <p className="text-2xl font-bold tracking-tight text-foreground">
                  {rupees(data.revenue_paise_total)}
                </p>
                <p className="mt-0.5 truncate text-[11px] text-muted-foreground">
                  Captured in {range}
                </p>
              </div>
              <span className="mt-2 text-[10px] text-muted-foreground">
                Real billing transactions
              </span>
            </Link>

            {/* Open Alerts */}
            <Link
              href="/admin/fleet"
              className="group flex flex-col justify-between rounded-2xl border border-border bg-card p-4 transition-all hover:border-foreground/30 hover:shadow-xs"
            >
              <div className="flex items-center justify-between">
                <span className="text-xs font-medium text-muted-foreground">Open Alerts</span>
                <AlertTriangle className={`size-4 ${data.alerts_critical_open > 0 ? 'text-red-500' : 'text-muted-foreground'}`} />
              </div>
              <div className="mt-2">
                <p className="text-2xl font-bold tracking-tight text-foreground">
                  {data.alerts_critical_open + data.alerts_warning_open}
                </p>
                <p className="mt-0.5 truncate text-[11px] text-muted-foreground">
                  {data.alerts_critical_open} critical · {data.alerts_warning_open} warning
                </p>
              </div>
              {data.alerts_critical_open > 0 ? (
                <span className="mt-2 inline-flex items-center gap-1 text-[10px] font-semibold text-red-600 dark:text-red-400">
                  <ShieldAlert className="size-3" /> Critical action needed
                </span>
              ) : (
                <span className="mt-2 text-[10px] text-muted-foreground">
                  Telemetry nominal
                </span>
              )}
            </Link>

            {/* Decisions Queue */}
            <Link
              href="/admin/approvals"
              className="group flex flex-col justify-between rounded-2xl border border-border bg-card p-4 transition-all hover:border-foreground/30 hover:shadow-xs"
            >
              <div className="flex items-center justify-between">
                <span className="text-xs font-medium text-muted-foreground">Decisions</span>
                <Clock className="size-4 text-muted-foreground transition-colors group-hover:text-foreground" />
              </div>
              <div className="mt-2">
                <p className="text-2xl font-bold tracking-tight text-foreground">
                  {data.pending_approvals + data.pending_custom_requests}
                </p>
                <p className="mt-0.5 truncate text-[11px] text-muted-foreground">
                  {data.pending_approvals} approvals · {data.pending_custom_requests} custom
                </p>
              </div>
              <span className="mt-2 inline-flex items-center gap-1 text-[10px] font-medium text-foreground group-hover:underline">
                Review queue →
              </span>
            </Link>
          </div>

          {/* Fleet Health & Infrastructure Strip */}
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {/* Version Rollout */}
            <div className="flex items-center gap-3.5 rounded-2xl border border-border bg-card p-4">
              <div className="grid size-10 shrink-0 place-items-center rounded-xl border border-border bg-muted">
                <Rocket className="size-5 text-foreground" />
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center justify-between text-xs">
                  <span className="font-semibold text-foreground">App Rollout</span>
                  <span className="text-muted-foreground">
                    {data.on_latest_version_pct !== null ? `${data.on_latest_version_pct}%` : '—'}
                  </span>
                </div>
                <p className="truncate text-[11px] text-muted-foreground">
                  {data.on_latest_version_pct !== null ? 'Screens on latest released build' : 'No active released build'}
                </p>
                {data.on_latest_version_pct !== null && (
                  <div className="mt-2 h-1 w-full overflow-hidden rounded-full bg-muted">
                    <div
                      className="h-full bg-foreground transition-all duration-500"
                      style={{ width: `${Math.min(100, data.on_latest_version_pct)}%` }}
                    />
                  </div>
                )}
              </div>
            </div>

            {/* Media Storage Consumption */}
            <Link
              href="/admin/storage"
              className="group flex items-center gap-3.5 rounded-2xl border border-border bg-card p-4 transition-colors hover:border-foreground/30"
            >
              <div className="grid size-10 shrink-0 place-items-center rounded-xl border border-border bg-muted">
                <HardDrive className="size-5 text-foreground" />
              </div>
              <div className="min-w-0 flex-1">
                <span className="text-xs font-semibold text-foreground">Media Storage</span>
                <p className="text-base font-bold text-foreground">{formatBytes(data.storage_used_bytes)}</p>
                <p className="truncate text-[11px] text-muted-foreground">Platform-wide assets & renditions</p>
              </div>
            </Link>

            {/* Renewals Window (14d) */}
            <Link
              href="/admin/billing"
              className="group flex items-center gap-3.5 rounded-2xl border border-border bg-card p-4 transition-colors hover:border-foreground/30"
            >
              <div className="grid size-10 shrink-0 place-items-center rounded-xl border border-border bg-muted">
                <Clock className="size-5 text-foreground" />
              </div>
              <div className="min-w-0 flex-1">
                <span className="text-xs font-semibold text-foreground">Upcoming Renewals</span>
                <p className="text-base font-bold text-foreground">
                  {data.renewals_due_14d_count} <span className="text-xs font-normal text-muted-foreground">workspaces</span>
                </p>
                <p className="truncate text-[11px] text-muted-foreground">
                  {rupees(data.renewals_due_14d_value_paise)} expected in 14d
                </p>
              </div>
            </Link>

            {/* Offline Attention */}
            <Link
              href="/admin/fleet"
              className="group flex items-center gap-3.5 rounded-2xl border border-border bg-card p-4 transition-colors hover:border-foreground/30"
            >
              <div className="grid size-10 shrink-0 place-items-center rounded-xl border border-border bg-muted">
                <Activity className="size-5 text-foreground" />
              </div>
              <div className="min-w-0 flex-1">
                <span className="text-xs font-semibold text-foreground">Stale Screens</span>
                <p className="text-base font-bold text-foreground">
                  {data.screens_offline_24h} <span className="text-xs font-normal text-muted-foreground">TVs offline &gt;24h</span>
                </p>
                <p className="truncate text-[11px] text-muted-foreground">Requires field inspection</p>
              </div>
            </Link>
          </div>

          {/* Timeseries Analytics Charts */}
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
            {/* Playback Volume Chart (2 cols on lg) */}
            <div className="rounded-2xl border border-border bg-card p-5 lg:col-span-2">
              <div className="flex flex-col justify-between gap-1 sm:flex-row sm:items-center">
                <div>
                  <h2 className="text-sm font-semibold tracking-tight text-foreground">Playback Airtime & Volume</h2>
                  <p className="text-xs text-muted-foreground">
                    Online vs offline ad impressions verified by proof-of-play ({range})
                  </p>
                </div>
                <div className="flex items-center gap-3 text-xs text-muted-foreground">
                  <span className="inline-flex items-center gap-1.5">
                    <span className="size-2 rounded-full bg-foreground" /> Online
                  </span>
                  <span className="inline-flex items-center gap-1.5">
                    <span className="size-2 rounded-full bg-amber-500" /> Offline
                  </span>
                </div>
              </div>

              <div className="mt-5 h-64 w-full">
                {mounted ? (
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={playsChartData} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                      <defs>
                        <linearGradient id="playsOnlineGrad" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor="var(--chart-1)" stopOpacity={0.4} />
                          <stop offset="95%" stopColor="var(--chart-1)" stopOpacity={0.0} />
                        </linearGradient>
                        <linearGradient id="playsOfflineGrad" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor="#f59e0b" stopOpacity={0.3} />
                          <stop offset="95%" stopColor="#f59e0b" stopOpacity={0.0} />
                        </linearGradient>
                      </defs>
                      <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="rgba(128,128,128,0.15)" />
                      <XAxis
                        dataKey="date"
                        stroke="currentColor"
                        className="text-[10px] text-muted-foreground"
                        tickLine={false}
                        axisLine={false}
                      />
                      <YAxis
                        stroke="currentColor"
                        className="text-[10px] text-muted-foreground"
                        tickLine={false}
                        axisLine={false}
                        tickFormatter={(v) => (v >= 1000 ? `${(v / 1000).toFixed(0)}k` : v)}
                      />
                      <Tooltip content={<CustomChartTooltip />} />
                      <Area
                        type="monotone"
                        dataKey="Online"
                        stroke="var(--chart-1)"
                        strokeWidth={2}
                        fillOpacity={1}
                        fill="url(#playsOnlineGrad)"
                      />
                      <Area
                        type="monotone"
                        dataKey="Offline"
                        stroke="#f59e0b"
                        strokeWidth={2}
                        fillOpacity={1}
                        fill="url(#playsOfflineGrad)"
                      />
                    </AreaChart>
                  </ResponsiveContainer>
                ) : (
                  <div className="h-full w-full animate-pulse bg-muted/40" />
                )}
              </div>
            </div>

            {/* Revenue / Signups Chart (1 col on lg with toggle) */}
            <div className="rounded-2xl border border-border bg-card p-5">
              <div className="flex items-center justify-between">
                <div>
                  <h2 className="text-sm font-semibold tracking-tight text-foreground">
                    {secondaryChart === 'revenue' ? 'Revenue Inflow' : 'Tenant Signups'}
                  </h2>
                  <p className="text-xs text-muted-foreground">
                    {secondaryChart === 'revenue' ? `Captured payments (${range})` : `New signups (${range})`}
                  </p>
                </div>
                {/* Secondary chart switcher */}
                <div className="inline-flex rounded-lg border border-border bg-muted p-0.5 text-[11px]">
                  <button
                    type="button"
                    onClick={() => setSecondaryChart('revenue')}
                    className={`rounded-md px-2 py-0.5 font-medium transition-colors ${
                      secondaryChart === 'revenue' ? 'bg-background text-foreground shadow-xs' : 'text-muted-foreground'
                    }`}
                  >
                    Revenue
                  </button>
                  <button
                    type="button"
                    onClick={() => setSecondaryChart('signups')}
                    className={`rounded-md px-2 py-0.5 font-medium transition-colors ${
                      secondaryChart === 'signups' ? 'bg-background text-foreground shadow-xs' : 'text-muted-foreground'
                    }`}
                  >
                    Signups
                  </button>
                </div>
              </div>

              <div className="mt-5 h-64 w-full">
                {mounted ? (
                  <ResponsiveContainer width="100%" height="100%">
                    {secondaryChart === 'revenue' ? (
                      <BarChart data={revenueChartData} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                        <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="rgba(128,128,128,0.15)" />
                        <XAxis
                          dataKey="date"
                          stroke="currentColor"
                          className="text-[10px] text-muted-foreground"
                          tickLine={false}
                          axisLine={false}
                        />
                        <YAxis
                          stroke="currentColor"
                          className="text-[10px] text-muted-foreground"
                          tickLine={false}
                          axisLine={false}
                          tickFormatter={(v) => (v >= 1000 ? `₹${(v / 1000).toFixed(0)}k` : `₹${v}`)}
                        />
                        <Tooltip content={<CustomChartTooltip valuePrefix="₹" />} />
                        <Bar dataKey="Revenue" fill="var(--chart-2)" radius={[4, 4, 0, 0]} />
                      </BarChart>
                    ) : (
                      <BarChart data={signupsChartData} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                        <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="rgba(128,128,128,0.15)" />
                        <XAxis
                          dataKey="date"
                          stroke="currentColor"
                          className="text-[10px] text-muted-foreground"
                          tickLine={false}
                          axisLine={false}
                        />
                        <YAxis
                          stroke="currentColor"
                          className="text-[10px] text-muted-foreground"
                          tickLine={false}
                          axisLine={false}
                          allowDecimals={false}
                        />
                        <Tooltip content={<CustomChartTooltip />} />
                        <Bar dataKey="Signups" fill="var(--chart-1)" radius={[4, 4, 0, 0]} />
                      </BarChart>
                    )}
                  </ResponsiveContainer>
                ) : (
                  <div className="h-full w-full animate-pulse bg-muted/40" />
                )}
              </div>
            </div>
          </div>

          {/* Operational Focus & Recent Activity Section */}
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            {/* Attention / Action Center */}
            <div className="flex flex-col justify-between rounded-2xl border border-border bg-card p-5">
              <div>
                <div className="flex items-center justify-between border-b border-border pb-3">
                  <div className="flex items-center gap-2">
                    <ShieldCheck className="size-4 text-foreground" />
                    <h2 className="text-sm font-semibold text-foreground">Action Center</h2>
                  </div>
                  <span className="text-xs text-muted-foreground">Pending administrative decisions</span>
                </div>

                <div className="mt-4 space-y-3">
                  {/* Pending approvals card */}
                  {data.pending_approvals > 0 ? (
                    <div className="flex items-center justify-between rounded-xl border border-amber-500/30 bg-amber-500/10 p-3.5 text-sm">
                      <div className="flex items-center gap-3">
                        <Clock className="size-4 shrink-0 text-amber-700 dark:text-amber-400" />
                        <div>
                          <p className="font-semibold text-amber-900 dark:text-amber-200">
                            {data.pending_approvals} workspace{data.pending_approvals === 1 ? '' : 's'} awaiting approval
                          </p>
                          <p className="text-xs text-amber-800/80 dark:text-amber-300/80">
                            Review tenant signups and assign initial plan
                          </p>
                        </div>
                      </div>
                      <Link
                        href="/admin/approvals"
                        className="rounded-lg bg-amber-500 px-3 py-1.5 text-xs font-semibold text-black transition-opacity hover:opacity-90"
                      >
                        Review
                      </Link>
                    </div>
                  ) : (
                    <div className="flex items-center gap-3 rounded-xl border border-border bg-muted/40 p-3 text-xs text-muted-foreground">
                      <CheckCircle className="size-4 text-emerald-600 dark:text-emerald-400" />
                      <span>Approvals queue is clear. All registered workspaces are reviewed.</span>
                    </div>
                  )}

                  {/* Custom Requests Card */}
                  {data.pending_custom_requests > 0 && (
                    <div className="flex items-center justify-between rounded-xl border border-border bg-accent/40 p-3.5 text-sm">
                      <div className="flex items-center gap-3">
                        <Layers className="size-4 shrink-0 text-foreground" />
                        <div>
                          <p className="font-semibold text-foreground">
                            {data.pending_custom_requests} custom plan request{data.pending_custom_requests === 1 ? '' : 's'}
                          </p>
                          <p className="text-xs text-muted-foreground">Tenants requesting custom limits or pricing</p>
                        </div>
                      </div>
                      <Link
                        href="/admin/billing"
                        className="rounded-lg border border-border bg-card px-3 py-1.5 text-xs font-medium text-foreground hover:bg-accent"
                      >
                        Price request
                      </Link>
                    </div>
                  )}

                  {/* Critical alerts item */}
                  {data.alerts_critical_open > 0 && (
                    <div className="flex items-center justify-between rounded-xl border border-red-500/30 bg-red-500/10 p-3.5 text-sm">
                      <div className="flex items-center gap-3">
                        <AlertTriangle className="size-4 shrink-0 text-red-600 dark:text-red-400" />
                        <div>
                          <p className="font-semibold text-red-900 dark:text-red-200">
                            {data.alerts_critical_open} critical telemetry alert{data.alerts_critical_open === 1 ? '' : 's'}
                          </p>
                          <p className="text-xs text-red-800/80 dark:text-red-300/80">TV crashes or hardware storage exhaustion</p>
                        </div>
                      </div>
                      <Link
                        href="/admin/fleet"
                        className="rounded-lg bg-red-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-red-700"
                      >
                        Inspect
                      </Link>
                    </div>
                  )}
                </div>
              </div>

              {/* Quick links grid */}
              <div className="mt-6 border-t border-border pt-4">
                <p className="text-xs font-semibold tracking-wider text-muted-foreground uppercase">Operational Short-cuts</p>
                <div className="mt-3 grid grid-cols-2 gap-2 text-xs sm:grid-cols-3">
                  <Link
                    href="/admin/tenants"
                    className="flex items-center justify-between rounded-xl border border-border bg-muted/30 p-2.5 transition-colors hover:bg-accent hover:text-foreground"
                  >
                    <span>Tenants</span>
                    <ArrowUpRight className="size-3.5 text-muted-foreground" />
                  </Link>
                  <Link
                    href="/admin/fleet"
                    className="flex items-center justify-between rounded-xl border border-border bg-muted/30 p-2.5 transition-colors hover:bg-accent hover:text-foreground"
                  >
                    <span>TV Fleet</span>
                    <ArrowUpRight className="size-3.5 text-muted-foreground" />
                  </Link>
                  <Link
                    href="/admin/releases"
                    className="flex items-center justify-between rounded-xl border border-border bg-muted/30 p-2.5 transition-colors hover:bg-accent hover:text-foreground"
                  >
                    <span>Releases</span>
                    <ArrowUpRight className="size-3.5 text-muted-foreground" />
                  </Link>
                  <Link
                    href="/admin/packages"
                    className="flex items-center justify-between rounded-xl border border-border bg-muted/30 p-2.5 transition-colors hover:bg-accent hover:text-foreground"
                  >
                    <span>Packages</span>
                    <ArrowUpRight className="size-3.5 text-muted-foreground" />
                  </Link>
                  <Link
                    href="/admin/billing"
                    className="flex items-center justify-between rounded-xl border border-border bg-muted/30 p-2.5 transition-colors hover:bg-accent hover:text-foreground"
                  >
                    <span>Payments</span>
                    <ArrowUpRight className="size-3.5 text-muted-foreground" />
                  </Link>
                  <Link
                    href="/admin/provisioning"
                    className="flex items-center justify-between rounded-xl border border-border bg-muted/30 p-2.5 transition-colors hover:bg-accent hover:text-foreground"
                  >
                    <span>Setup QR</span>
                    <QrCode className="size-3.5 text-muted-foreground" />
                  </Link>
                </div>
              </div>
            </div>

            {/* Recent Platform Activity Feed */}
            <div className="rounded-2xl border border-border bg-card p-5">
              <div className="flex items-center justify-between border-b border-border pb-3">
                <div className="flex items-center gap-2">
                  <Activity className="size-4 text-foreground" />
                  <h2 className="text-sm font-semibold text-foreground">Recent Activity</h2>
                </div>
                <span className="text-xs text-muted-foreground">Admin audit trail</span>
              </div>

              {data.recent_activity.length === 0 ? (
                <div className="flex h-64 flex-col items-center justify-center text-center text-xs text-muted-foreground">
                  <Activity className="size-8 stroke-[1.5] text-muted-foreground/60" />
                  <p className="mt-2 font-medium">No recent administrative actions recorded</p>
                </div>
              ) : (
                <ul className="mt-3 divide-y divide-border/60">
                  {data.recent_activity.slice(0, 8).map((event) => {
                    const formatted = formatAction(event.action)
                    return (
                      <li key={event.id} className="flex items-start justify-between gap-3 py-2.5 text-xs">
                        <div className="min-w-0">
                          <p className="font-medium text-foreground">{formatted.label}</p>
                          <p className="truncate text-[11px] text-muted-foreground">
                            by <span className="font-medium text-foreground">{event.actor_username || 'system'}</span>
                            {event.target_type && event.target_id && ` · ${event.target_type} #${event.target_id}`}
                          </p>
                        </div>
                        <span className="shrink-0 text-[11px] text-muted-foreground">
                          {relativeTime(event.occurred_at)}
                        </span>
                      </li>
                    )
                  })}
                </ul>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  )
}
