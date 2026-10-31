'use client'

import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { useQuery } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  AlertTriangle,
  BellOff,
  Check,
  CheckCheck,
  DownloadCloud,
  HardDrive,
  MonitorX,
  PauseCircle,
  Volume2,
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
import type { ContentItem, Screen } from '@/lib/types'

type Severity = 'critical' | 'warning'

type Alert = {
  id: string
  severity: Severity
  icon: typeof MonitorX
  title: string
  detail: string
  href: string
  at: string | null
}

const OFFLINE_HOURS = 1
const LOW_STORAGE_MB = 500

const hoursSince = (value: string | null | undefined) =>
  value ? (Date.now() - Date.parse(value)) / 3_600_000 : Infinity

function buildAlerts(screens: Screen[], content: ContentItem[]): Alert[] {
  const alerts: Alert[] = []

  for (const screen of screens) {
    const label = screen.name || `Screen ${screen.id}`
    const href = `/dashboard/screens/${screen.id}`

    if (screen.status !== 'online' && hoursSince(screen.last_seen) > OFFLINE_HOURS) {
      alerts.push({
        id: `offline-${screen.id}`,
        severity: 'critical',
        icon: MonitorX,
        title: `${label} is offline`,
        detail: `No heartbeat since ${relativeTime(screen.last_seen)}. Check power and network at the site.`,
        href,
        at: screen.last_seen,
      })
    }

    if (screen.last_error) {
      alerts.push({
        id: `error-${screen.id}`,
        severity: 'critical',
        icon: AlertTriangle,
        title: `${label} reported a playback error`,
        detail: screen.last_error,
        href,
        at: screen.last_error_at,
      })
    }

    if (screen.free_storage_mb !== null && screen.free_storage_mb !== undefined && screen.free_storage_mb < LOW_STORAGE_MB) {
      alerts.push({
        id: `storage-${screen.id}`,
        severity: 'warning',
        icon: HardDrive,
        title: `${label} is low on storage`,
        detail: `${screen.free_storage_mb} MB free. New content may fail to cache for offline playback.`,
        href,
        at: screen.last_seen,
      })
    }

    if (screen.update_status === 'failed') {
      alerts.push({
        id: `update-${screen.id}`,
        severity: 'warning',
        icon: DownloadCloud,
        title: `${label} failed to update`,
        detail: `Still on ${screen.app_version || 'an unknown version'}, wanted ${screen.target_version_code ?? 'the latest'}. It will retry, but repeated failures need a look.`,
        href,
        at: screen.last_seen,
      })
    }

    if (screen.status === 'online' && screen.playback_state === 'idle') {
      alerts.push({
        id: `idle-${screen.id}`,
        severity: 'critical',
        icon: PauseCircle,
        title: `${label} is on but not playing`,
        detail: screen.effective_playlist_id
          ? 'It has a playlist but reports nothing playing. Check the content downloaded correctly.'
          : 'No playlist is assigned, so this screen is showing nothing to anybody.',
        href,
        at: screen.last_seen,
      })
    }
  }

  for (const item of content) {
    if (item.status === 'failed') {
      alerts.push({
        id: `content-${item.id}`,
        severity: 'warning',
        icon: XCircle,
        title: `“${item.name}” failed to process`,
        detail: item.failed_reason || 'Transcoding did not complete, so no screen can play this asset.',
        href: '/dashboard/content',
        at: item.uploaded_at,
      })
    }
  }

  return alerts.sort((a, b) => {
    if (a.severity !== b.severity) return a.severity === 'critical' ? -1 : 1
    return Date.parse(b.at || '0') - Date.parse(a.at || '0')
  })
}

export default function AlertsPage() {
  const screensQuery = useQuery({ queryKey: ['screens'], queryFn: api.getScreens, refetchInterval: 10000 })
  const contentQuery = useQuery({ queryKey: ['content'], queryFn: api.getContent, refetchInterval: 30000 })

  const [severity, setSeverity] = useState<Severity | 'all'>('all')
  const [showMuted, setShowMuted] = useState(false)
  const [search, setSearch] = useState('')
  const [mutedAlerts, setMutedAlerts] = useState<Record<string, number>>({})

  // Load muted alerts from localStorage.
  //
  // This one stays an effect. Seeding it in a useState initializer would read localStorage
  // during render, and render also happens on the server for this route -- where the
  // symbol does not exist and the page would fail to prerender. Mount-only deps, so it
  // runs exactly once and none of the cascading-render the rule is aimed at applies.
  useEffect(() => {
    try {
      const stored = localStorage.getItem('mr_muted_alerts') || localStorage.getItem('olrac_muted_alerts')
      if (stored) {
        const parsed = JSON.parse(stored)
        // Cleanup expired mutes (older than 24 hours)
        const now = Date.now()
        const activeMutes: Record<string, number> = {}
        for (const [key, ts] of Object.entries(parsed)) {
          if (typeof ts === 'number' && now - ts < 24 * 3600 * 1000) {
            activeMutes[key] = ts
          }
        }
        // eslint-disable-next-line react-hooks/set-state-in-effect -- browser-only store; see above
        setMutedAlerts(activeMutes)
      }
    } catch {
      // Ignore localStorage read errors
    }
  }, [])

  // Declared BEFORE the handlers that read them. `acknowledgeAll` used `alerts` while it
  // was still a const further down the body -- fine at runtime, because a click happens
  // long after render, but the React Compiler could not prove that and gave up on the
  // component: it reported the useMemo below as "existing memoization could not be
  // preserved" and then read the handlers as render-phase code, which is why two ordinary
  // Date.now() calls in click handlers were flagged as impure renders.
  const alerts = useMemo(
    () => buildAlerts(screensQuery.data || [], contentQuery.data || []),
    [screensQuery.data, contentQuery.data],
  )

  const activeAlerts = useMemo(() => alerts.filter((a) => !mutedAlerts[a.id]), [alerts, mutedAlerts])
  const mutedCount = useMemo(() => alerts.filter((a) => Boolean(mutedAlerts[a.id])).length, [alerts, mutedAlerts])

  const saveMutes = (mutes: Record<string, number>) => {
    setMutedAlerts(mutes)
    try {
      localStorage.setItem('mr_muted_alerts', JSON.stringify(mutes))
    } catch {
      // Ignore
    }
  }

  const muteAlert = (id: string, e?: React.MouseEvent) => {
    e?.preventDefault()
    e?.stopPropagation()
    // Reading the clock is the whole point: the mute expires 24 hours from the CLICK, so
    // it has to be the time of the click. This runs from onClick, never during render.
    // eslint-disable-next-line react-hooks/purity -- event handler, not render
    const next = { ...mutedAlerts, [id]: Date.now() }
    saveMutes(next)
    toast.success('Alert acknowledged and snoozed for 24h.')
  }

  const unmuteAlert = (id: string, e?: React.MouseEvent) => {
    e?.preventDefault()
    e?.stopPropagation()
    const next = { ...mutedAlerts }
    delete next[id]
    saveMutes(next)
    toast.info('Alert un-muted.')
  }

  const acknowledgeAll = () => {
    const next = { ...mutedAlerts }
    const now = Date.now()
    for (const a of alerts) {
      next[a.id] = now
    }
    saveMutes(next)
    toast.success('All current alerts acknowledged and snoozed.')
  }

  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase()
    const pool = showMuted ? alerts : activeAlerts
    return pool.filter((alert) => {
      const matchesSeverity = severity === 'all' || alert.severity === severity
      const matchesSearch = !term || alert.title.toLowerCase().includes(term) || alert.detail.toLowerCase().includes(term)
      return matchesSeverity && matchesSearch
    })
  }, [alerts, activeAlerts, showMuted, severity, search])

  const activeCriticals = activeAlerts.filter((alert) => alert.severity === 'critical').length

  if (screensQuery.isError || contentQuery.isError) {
    return <ErrorState message="Alerts could not be loaded." onRetry={() => { screensQuery.refetch(); contentQuery.refetch() }} />
  }

  return (
    <div className="space-y-6">
      <ListToolbar
        title="Alerts"
        action={
          <div className="flex items-center gap-2">
            {activeAlerts.length > 0 && (
              <Button variant="outline" size="sm" onClick={acknowledgeAll} className="h-8 text-xs gap-1.5">
                <CheckCheck className="size-3.5 text-emerald-600 dark:text-emerald-400" /> Acknowledge all
              </Button>
            )}
            {activeAlerts.length > 0 ? (
              <Badge variant={activeCriticals ? 'danger' : 'warning'}>
                {activeCriticals ? `${activeCriticals} critical` : `${activeAlerts.length} to review`}
              </Badge>
            ) : (
              <Badge variant="success">All clear</Badge>
            )}
          </div>
        }
        search={{ value: search, onChange: setSearch }}
        filters={
          <>
            {(['all', 'critical', 'warning'] as const).map((value) => (
              <DropdownMenuItem key={value} onClick={() => setSeverity(value)} className={severity === value ? 'bg-accent font-medium' : undefined}>
                <span className="capitalize">{value === 'all' ? 'All severities' : value}</span>
              </DropdownMenuItem>
            ))}
            <DropdownMenuItem onClick={() => setShowMuted(!showMuted)} className={showMuted ? 'bg-accent font-medium' : undefined}>
              <span>{showMuted ? 'Showing acknowledged alerts' : `Show acknowledged (${mutedCount})`}</span>
            </DropdownMenuItem>
          </>
        }
      />

      {screensQuery.isLoading ? (
        <div className="space-y-3">{Array.from({ length: 3 }).map((_, index) => <Skeleton key={index} className="h-20 rounded-xl" />)}</div>
      ) : !filtered.length ? (
        <EmptyState
          icon={BellOff}
          title={showMuted ? 'No acknowledged alerts' : 'Nothing needs attention'}
          description={showMuted ? 'No alerts are currently snoozed.' : 'Every screen is reporting in and all content processed cleanly.'}
        />
      ) : (
        <div className="space-y-3">
          {filtered.map((alert) => {
            const Icon = alert.icon
            const critical = alert.severity === 'critical'
            const isMuted = Boolean(mutedAlerts[alert.id])

            return (
              <div
                key={alert.id}
                className={`ring-hairline bg-card flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 rounded-xl p-4 ring-1 transition-shadow hover:shadow-md ${
                  isMuted ? 'opacity-60 bg-muted/20' : ''
                }`}
              >
                <Link href={alert.href} className="flex items-start gap-4 min-w-0 flex-1 group">
                  <span className={`grid size-10 shrink-0 place-items-center rounded-xl ${
                    isMuted
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
                      {isMuted && (
                        <Badge variant="outline" className="text-[10px] text-muted-foreground">
                          Acknowledged / Snoozed
                        </Badge>
                      )}
                    </div>
                    <p className="text-muted-foreground mt-1 text-sm">{alert.detail}</p>
                  </div>
                </Link>

                <div className="flex items-center gap-2 self-end sm:self-center shrink-0">
                  {alert.at && <span className="text-muted-foreground/70 text-xs">{relativeTime(alert.at)}</span>}

                  {isMuted ? (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={(e) => unmuteAlert(alert.id, e)}
                      title="Restore alert"
                      className="h-8 px-2 text-xs gap-1 text-muted-foreground hover:text-foreground"
                    >
                      <Volume2 className="size-3.5" /> Unmute
                    </Button>
                  ) : (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={(e) => muteAlert(alert.id, e)}
                      title="Acknowledge and snooze for 24h"
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
