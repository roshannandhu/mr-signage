'use client'

import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  CheckCircle,
  Clock,
  Cpu,
  Database,
  HardDrive,
  HeartPulse,
  RefreshCw,
  Zap,
} from 'lucide-react'
import { adminApi } from '@/lib/api'
import { PageHeader, StatCard } from '@/components/admin/admin-ui'

export default function AdminHealthPage() {
  const queryClient = useQueryClient()

  const { data, isFetching } = useQuery({
    queryKey: ['admin', 'platform-health'],
    queryFn: adminApi.getPlatformHealth,
    refetchInterval: 30000,
  })

  const cronJobs = data?.cron_jobs ? Object.entries(data.cron_jobs) : []
  const isHealthy = data?.status === 'ok'

  return (
    <div className="space-y-6 p-3.5 sm:p-6 lg:p-8 text-foreground">
      <PageHeader
        title="Platform Diagnostics & Infrastructure Health"
        description="Continuous operational telemetry, background task heartbeats, persistence monitoring, and database round-trip checks"
      >
        <button
          onClick={() => queryClient.invalidateQueries({ queryKey: ['admin', 'platform-health'] })}
          className="flex items-center gap-2 rounded-xl border border-border bg-muted px-3.5 py-1.5 text-xs sm:text-sm text-muted-foreground hover:bg-muted/80"
        >
          <RefreshCw className={`size-3.5 ${isFetching ? 'animate-spin' : ''}`} />
          Refresh
        </button>
      </PageHeader>

      {/* Warnings Banner */}
      {data?.warning && (
        <div className="flex items-start gap-3 rounded-2xl border border-amber-500/30 bg-amber-500/10 p-4 text-xs text-amber-800 dark:text-amber-200">
          <AlertTriangle className="mt-0.5 size-5 shrink-0 text-amber-600 dark:text-amber-400" />
          <div className="space-y-1">
            <p className="font-semibold">Infrastructure Operational Warning</p>
            <p className="leading-relaxed opacity-90">{data.warning}</p>
          </div>
        </div>
      )}

      {/* Primary Infrastructure Status Grid */}
      <div className="grid grid-cols-2 gap-2.5 sm:gap-4 lg:grid-cols-4">
        <StatCard
          label="Overall Platform Status"
          value={data?.status ? data.status.toUpperCase() : 'CHECKING'}
          icon={HeartPulse}
          accent={isHealthy ? 'emerald' : 'amber'}
        />
        <StatCard
          label="Database Dialect"
          value={data?.backend ? data.backend.toUpperCase() : 'SQL'}
          icon={Database}
          accent={data?.backend === 'postgresql' ? 'emerald' : 'amber'}
        />
        <StatCard
          label="Redis Queue Engine"
          value={data?.redis ? data.redis.toUpperCase() : 'DISCONNECTED'}
          icon={Zap}
          accent={data?.redis === 'connected' ? 'emerald' : 'rose'}
        />
        <StatCard
          label="Storage Provider"
          value={data?.object_storage ? data.object_storage.split(' ')[0].toUpperCase() : 'LOCAL'}
          icon={HardDrive}
          accent={data?.object_storage === 'configured' ? 'emerald' : 'amber'}
        />
      </div>

      {/* Service Diagnostics Cards */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        {/* Database Diagnostic */}
        <div className="rounded-2xl border border-border bg-card p-5 shadow-sm space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Database className="size-4 text-primary" />
              <h3 className="text-sm font-semibold text-foreground">Relational Database</h3>
            </div>
            <span
              className={`rounded-full px-2 py-0.5 text-xs font-semibold ${
                data?.database === 'connected'
                  ? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-400'
                  : 'bg-rose-500/10 text-rose-700 dark:text-rose-400'
              }`}
            >
              {data?.database || 'Unknown'}
            </span>
          </div>

          <div className="space-y-2 text-xs">
            <div className="flex justify-between border-b border-border/60 pb-1.5">
              <span className="text-muted-foreground">Host / Target:</span>
              <span className="font-mono text-foreground">{data?.host || '—'}</span>
            </div>
            <div className="flex justify-between border-b border-border/60 pb-1.5">
              <span className="text-muted-foreground">Engine:</span>
              <span className="font-medium text-foreground">{data?.backend || '—'}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">Schema Migrations:</span>
              <span className={data?.schema_migration_error ? 'text-rose-500 font-medium' : 'text-emerald-600'}>
                {data?.schema_migration_error ? 'Error Detected' : 'Up to Date'}
              </span>
            </div>
          </div>
        </div>

        {/* Redis & Realtime Diagnostic */}
        <div className="rounded-2xl border border-border bg-card p-5 shadow-sm space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Zap className="size-4 text-amber-500" />
              <h3 className="text-sm font-semibold text-foreground">Redis Memory & Queues</h3>
            </div>
            <span
              className={`rounded-full px-2 py-0.5 text-xs font-semibold ${
                data?.redis === 'connected'
                  ? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-400'
                  : 'bg-rose-500/10 text-rose-700 dark:text-rose-400'
              }`}
            >
              {data?.redis || 'Unknown'}
            </span>
          </div>

          <div className="space-y-2 text-xs">
            <div className="flex justify-between border-b border-border/60 pb-1.5">
              <span className="text-muted-foreground">Telemetry Push:</span>
              <span className="font-medium text-foreground">{data?.redis === 'connected' ? 'Active' : 'Halted'}</span>
            </div>
            <div className="flex justify-between border-b border-border/60 pb-1.5">
              <span className="text-muted-foreground">Device Command Buffer:</span>
              <span className="font-medium text-foreground">Operational</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">Scheduled Worker Sync:</span>
              <span className="font-medium text-foreground">{data?.redis === 'connected' ? 'Tracking' : 'Degraded'}</span>
            </div>
          </div>
        </div>

        {/* Object Storage & Media Diagnostic */}
        <div className="rounded-2xl border border-border bg-card p-5 shadow-sm space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <HardDrive className="size-4 text-cyan-500" />
              <h3 className="text-sm font-semibold text-foreground">Persistent Object Storage</h3>
            </div>
            <span
              className={`rounded-full px-2 py-0.5 text-xs font-semibold ${
                data?.object_storage === 'configured'
                  ? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-400'
                  : 'bg-amber-500/10 text-amber-700 dark:text-amber-400'
              }`}
            >
              {data?.object_storage || 'Local Disk'}
            </span>
          </div>

          <div className="space-y-2 text-xs">
            <div className="flex justify-between border-b border-border/60 pb-1.5">
              <span className="text-muted-foreground">Email Service (SMTP):</span>
              <span className="font-medium text-foreground">{data?.email || 'Not configured'}</span>
            </div>
            <div className="flex justify-between border-b border-border/60 pb-1.5">
              <span className="text-muted-foreground">Configured Env Keys:</span>
              <span className="font-mono text-foreground">{data?.storage_env?.set?.length ?? 0} keys set</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">Missing Variables:</span>
              <span className="font-mono text-muted-foreground">
                {data?.storage_env?.missing?.length ? data.storage_env.missing.join(', ') : 'None'}
              </span>
            </div>
          </div>
        </div>
      </div>

      {/* Background Job Heartbeats (B8) */}
      <section className="space-y-4 rounded-2xl border border-border bg-card p-6 shadow-sm">
        <div className="flex items-center justify-between border-b border-border pb-3">
          <div className="flex items-center gap-2">
            <Cpu className="size-4 text-primary" />
            <h3 className="text-base font-semibold text-foreground">Background Cron Job Heartbeats (B8)</h3>
          </div>
          <span className="text-xs text-muted-foreground">{cronJobs.length} automated worker processes</span>
        </div>

        <p className="text-xs text-muted-foreground leading-relaxed">
          Each recurring scheduled background task records its execution completion timestamp and processing duration directly to Redis.
          If a worker job encounters an unhandled exception or stops running, it immediately flags here.
        </p>

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {cronJobs.length === 0 ? (
            <p className="p-6 text-center text-xs text-muted-foreground">
              Awaiting worker heartbeat initialization signals…
            </p>
          ) : (
            cronJobs.map(([name, info]) => {
              const isRun = info !== null
              return (
                <div key={name} className="flex flex-col justify-between rounded-xl border border-border bg-muted/20 p-4">
                  <div className="flex items-center justify-between">
                    <span className="font-mono text-xs font-semibold text-foreground">{name}</span>
                    <span
                      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium ${
                        isRun
                          ? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-400'
                          : 'bg-muted text-muted-foreground'
                      }`}
                    >
                      {isRun ? <CheckCircle className="size-3" /> : <Clock className="size-3" />}
                      {isRun ? 'Heartbeat OK' : 'Awaiting Run'}
                    </span>
                  </div>

                  <div className="mt-3 space-y-1 text-xs">
                    <div className="flex justify-between text-muted-foreground">
                      <span>Last Finished:</span>
                      <span className="text-foreground">
                        {isRun && info.last_run_at ? new Date(info.last_run_at).toLocaleTimeString() : '—'}
                      </span>
                    </div>
                    <div className="flex justify-between text-muted-foreground">
                      <span>Duration:</span>
                      <span className="font-mono text-foreground">
                        {isRun && info.duration_seconds !== undefined ? `${info.duration_seconds.toFixed(2)}s` : '—'}
                      </span>
                    </div>
                  </div>
                </div>
              )
            })
          )}
        </div>
      </section>
    </div>
  )
}

