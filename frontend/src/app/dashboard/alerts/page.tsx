'use client'

import { useMemo, useState } from 'react'
import Link from 'next/link'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  BellOff,
  CalendarClock,
  Check,
  DownloadCloud,
  HardDrive,
  Megaphone,
  MonitorX,
  PauseCircle,
  XCircle,
} from 'lucide-react'
import { EmptyState } from '@/components/dashboard/empty-state'
import { ErrorState } from '@/components/dashboard/error-state'
import { ListToolbar } from '@/components/dashboard/list-toolbar'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { DropdownMenuItem } from '@/components/ui/dropdown-menu'
import { Skeleton } from '@/components/ui/skeleton'
import { api } from '@/lib/api'
import { relativeTime } from '@/lib/format'
import type { AlertSeverity, FleetAlert } from '@/lib/types'

// One icon per server-side alert kind (backend/alerting.py). Unknown kinds -- a future
// one the frontend has not been taught yet -- fall back to a plain triangle rather than
// disappearing or crashing the page.
const KIND_ICONS: Record<string, typeof MonitorX> = {
  screen_offline: MonitorX,
  playback_error: AlertTriangle,
  screen_idle: PauseCircle,
  low_storage: HardDrive,
  update_failed: DownloadCloud,
  content_failed: XCircle,
  campaign_ending: CalendarClock,
  plan_underused: Megaphone,
}

function alertHref(alert: FleetAlert): string {
  if (alert.screen_id) return `/dashboard/screens/${alert.screen_id}`
  if (alert.content_id) return `/dashboard/content/${alert.content_id}`
  return '/dashboard/alerts'
}

export default function AlertsPage() {
  const queryClient = useQueryClient()
  const [severity, setSeverity] = useState<AlertSeverity | 'all'>('all')
  const [showResolved, setShowResolved] = useState(false)
  const [search, setSearch] = useState('')

  const alertsQuery = useQuery({
    queryKey: ['alerts', showResolved],
    queryFn: () => api.getAlerts(showResolved),
    // The socket (use-fleet-alerts, mounted once in the dashboard layout) invalidates this
    // the moment the server raises or resolves something; this interval is only the
    // fallback for a dropped or never-opened connection, not the primary path.
    refetchInterval: 30_000,
  })

  const acknowledge = useMutation({
    mutationFn: (id: number) => api.acknowledgeAlert(id),
    onSuccess: (updated) => {
      queryClient.setQueryData<FleetAlert[]>(['alerts', showResolved], (current) =>
        current?.map((alert) => (alert.id === updated.id ? updated : alert)),
      )
    },
  })

  const alerts = useMemo(() => alertsQuery.data ?? [], [alertsQuery.data])

  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase()
    return alerts.filter((alert) => {
      const matchesSeverity = severity === 'all' || alert.severity === severity
      const matchesSearch = !term || alert.title.toLowerCase().includes(term) || (alert.detail ?? '').toLowerCase().includes(term)
      return matchesSeverity && matchesSearch
    })
  }, [alerts, severity, search])

  const open = alerts.filter((alert) => !alert.resolved_at)
  const openCriticals = open.filter((alert) => alert.severity === 'critical').length

  if (alertsQuery.isError) {
    return <ErrorState message="Alerts could not be loaded." onRetry={() => alertsQuery.refetch()} />
  }

  return (
    <div className="space-y-6">
      <ListToolbar
        title="Alerts"
        action={
          open.length > 0 ? (
            <Badge variant={openCriticals ? 'danger' : 'warning'}>
              {openCriticals ? `${openCriticals} critical` : `${open.length} to review`}
            </Badge>
          ) : (
            <Badge variant="success">All clear</Badge>
          )
        }
        search={{ value: search, onChange: setSearch }}
        filters={
          <>
            {(['all', 'critical', 'warning'] as const).map((value) => (
              <DropdownMenuItem key={value} onClick={() => setSeverity(value)} className={severity === value ? 'bg-accent font-medium' : undefined}>
                <span className="capitalize">{value === 'all' ? 'All severities' : value}</span>
              </DropdownMenuItem>
            ))}
            <DropdownMenuItem onClick={() => setShowResolved(!showResolved)} className={showResolved ? 'bg-accent font-medium' : undefined}>
              <span>{showResolved ? 'Showing resolved (7d)' : 'Show resolved (7d)'}</span>
            </DropdownMenuItem>
          </>
        }
      />

      {alertsQuery.isLoading ? (
        <div className="space-y-3">{Array.from({ length: 3 }).map((_, index) => <Skeleton key={index} className="h-20 rounded-xl" />)}</div>
      ) : !filtered.length ? (
        <EmptyState
          icon={BellOff}
          title={showResolved ? 'No resolved alerts in the last week' : 'Nothing needs attention'}
          description={showResolved ? 'Nothing has cleared in the last 7 days.' : 'Every screen is reporting in and all content processed cleanly.'}
        />
      ) : (
        <div className="space-y-3">
          {filtered.map((alert) => {
            const Icon = KIND_ICONS[alert.kind] ?? AlertTriangle
            const critical = alert.severity === 'critical'
            const resolved = Boolean(alert.resolved_at)
            const acknowledged = Boolean(alert.acknowledged_at)

            return (
              <div
                key={alert.id}
                className={`ring-hairline bg-card flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 rounded-xl p-4 ring-1 transition-shadow hover:shadow-md ${
                  resolved ? 'opacity-60 bg-muted/20' : ''
                }`}
              >
                <Link href={alertHref(alert)} className="flex items-start gap-4 min-w-0 flex-1 group">
                  <span className={`grid size-10 shrink-0 place-items-center rounded-xl ${
                    resolved
                      ? 'bg-muted text-muted-foreground'
                      : critical
                      ? 'bg-rose-500/10 text-rose-600 dark:text-rose-400'
                      : 'bg-amber-500/10 text-amber-600 dark:text-amber-400'
                  }`}>
                    <Icon className="size-5" aria-hidden="true" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <h2 className="text-foreground font-semibold group-hover:text-primary transition-colors">{alert.title}</h2>
                      <Badge variant={critical ? 'danger' : 'warning'}>{alert.severity}</Badge>
                      {resolved && (
                        <Badge variant="outline" className="text-[10px] text-muted-foreground">Resolved</Badge>
                      )}
                      {!resolved && acknowledged && (
                        <Badge variant="outline" className="text-[10px] text-muted-foreground">Acknowledged</Badge>
                      )}
                    </div>
                    {alert.detail && <p className="text-muted-foreground mt-1 text-sm">{alert.detail}</p>}
                  </div>
                </Link>

                <div className="flex items-center gap-2 self-end sm:self-center shrink-0">
                  <span className="text-muted-foreground/70 text-xs">{relativeTime(alert.raised_at)}</span>

                  {!resolved && !acknowledged && (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => acknowledge.mutate(alert.id)}
                      disabled={acknowledge.isPending}
                      title="Mark as seen -- the alert stays open until the fault itself clears"
                      className="h-8 px-2.5 text-xs gap-1 text-muted-foreground hover:text-foreground"
                    >
                      <Check className="size-3.5 text-emerald-600 dark:text-emerald-400" /> Acknowledge
                    </Button>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
