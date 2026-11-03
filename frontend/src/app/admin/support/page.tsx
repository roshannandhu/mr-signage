'use client'

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertCircle,
  AlertTriangle,
  ArrowRight,
  Bell,
  CheckCircle2,
  Clock,
  ExternalLink,
  Filter,
  LifeBuoy,
  RefreshCw,
  Search,
  ShieldAlert,
  Sparkles,
  Moon,
} from 'lucide-react'
import Link from 'next/link'
import { adminApi } from '@/lib/api'
import type { AttentionItem } from '@/lib/types'
import { PageHeader, StatCard } from '@/components/admin/admin-ui'
import { toast } from 'sonner'

export default function AdminSupportPage() {
  const queryClient = useQueryClient()
  const [severityFilter, setSeverityFilter] = useState<'all' | 'critical' | 'warning' | 'info'>('all')

  const { data: queue = [], isLoading, isFetching } = useQuery({
    queryKey: ['admin', 'attention-queue'],
    queryFn: adminApi.getAttentionQueue,
    refetchInterval: 30000,
  })

  // Dismiss / Snooze mutation
  const dismissMutation = useMutation({
    mutationFn: (payload: {
      rule: string
      target_id?: string
      organization_id?: number
      action: 'snooze' | 'resolve'
      snooze_hours?: number
    }) => adminApi.dismissAttentionItem(payload),
    onSuccess: (res) => {
      toast.success(res.action === 'snooze' ? 'Item snoozed for 24 hours' : 'Item marked as resolved')
      queryClient.invalidateQueries({ queryKey: ['admin', 'attention-queue'] })
      queryClient.invalidateQueries({ queryKey: ['admin', 'inbox-counts'] })
    },
    onError: (err: Error) => toast.error(err.message),
  })

  const filteredItems = queue.filter((item) => {
    if (severityFilter !== 'all' && item.severity !== severityFilter) return false
    return true
  })

  const criticalCount = queue.filter((i) => i.severity === 'critical').length
  const warningCount = queue.filter((i) => i.severity === 'warning').length
  const infoCount = queue.filter((i) => i.severity === 'info').length

  return (
    <div className="space-y-6 p-6 text-foreground lg:p-8">
      <PageHeader
        title="Operations Support & Attention Center"
        description="Proactive automated anomaly detection, device offline escalation, and SLA health enforcement"
      >
        <button
          onClick={() => queryClient.invalidateQueries({ queryKey: ['admin', 'attention-queue'] })}
          className="flex items-center gap-2 rounded-xl border border-border bg-muted px-4 py-2 text-sm text-muted-foreground hover:bg-muted/80"
        >
          <RefreshCw className={`size-3.5 ${isFetching ? 'animate-spin' : ''}`} />
          Refresh Queue
        </button>
      </PageHeader>

      {/* Summary Cards */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard label="Total Attention Items" value={queue.length} icon={LifeBuoy} accent="violet" />
        <StatCard label="Critical Escalations" value={criticalCount} icon={ShieldAlert} accent="rose" />
        <StatCard label="Warnings & Offline" value={warningCount} icon={AlertTriangle} accent="amber" />
        <StatCard label="Commercial Inquiries" value={infoCount} icon={Sparkles} accent="cyan" />
      </div>

      {/* Filter Tabs */}
      <section className="space-y-4">
        <div className="flex items-center justify-between border-b border-border pb-3">
          <div className="flex items-center gap-2">
            <button
              onClick={() => setSeverityFilter('all')}
              className={`rounded-xl px-3 py-1.5 text-xs font-medium transition ${
                severityFilter === 'all'
                  ? 'bg-primary text-primary-foreground'
                  : 'text-muted-foreground hover:text-foreground'
              }`}
            >
              All Items ({queue.length})
            </button>
            <button
              onClick={() => setSeverityFilter('critical')}
              className={`rounded-xl px-3 py-1.5 text-xs font-medium transition ${
                severityFilter === 'critical'
                  ? 'bg-rose-600 text-white'
                  : 'text-muted-foreground hover:text-foreground'
              }`}
            >
              Critical ({criticalCount})
            </button>
            <button
              onClick={() => setSeverityFilter('warning')}
              className={`rounded-xl px-3 py-1.5 text-xs font-medium transition ${
                severityFilter === 'warning'
                  ? 'bg-amber-600 text-white'
                  : 'text-muted-foreground hover:text-foreground'
              }`}
            >
              Warnings ({warningCount})
            </button>
            <button
              onClick={() => setSeverityFilter('info')}
              className={`rounded-xl px-3 py-1.5 text-xs font-medium transition ${
                severityFilter === 'info'
                  ? 'bg-primary text-primary-foreground'
                  : 'text-muted-foreground hover:text-foreground'
              }`}
            >
              Inquiries ({infoCount})
            </button>
          </div>
        </div>

        {/* Attention Items Feed */}
        {isLoading ? (
          <div className="rounded-2xl border border-border bg-card p-12 text-center text-xs text-muted-foreground">
            Evaluating platform attention rules…
          </div>
        ) : filteredItems.length === 0 ? (
          <div className="rounded-2xl border border-border bg-card p-12 text-center space-y-2">
            <CheckCircle2 className="size-10 text-emerald-500 mx-auto" />
            <h4 className="text-sm font-semibold text-foreground">Zero Escalations in Queue</h4>
            <p className="text-xs text-muted-foreground max-w-sm mx-auto">
              All screens are reporting healthy heartbeats, deployments have converged, and signups have been reviewed.
            </p>
          </div>
        ) : (
          <div className="space-y-3">
            {filteredItems.map((item) => (
              <div
                key={item.id}
                className={`flex flex-col justify-between gap-4 rounded-2xl border bg-card p-5 shadow-sm sm:flex-row sm:items-center ${
                  item.severity === 'critical'
                    ? 'border-rose-500/30 bg-rose-500/5'
                    : item.severity === 'warning'
                      ? 'border-amber-500/30 bg-amber-500/5'
                      : 'border-border'
                }`}
              >
                <div className="space-y-1.5 flex-1">
                  <div className="flex items-center gap-2">
                    <span
                      className={`rounded-full px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider ${
                        item.severity === 'critical'
                          ? 'bg-rose-500/20 text-rose-700 dark:text-rose-400'
                          : item.severity === 'warning'
                            ? 'bg-amber-500/20 text-amber-700 dark:text-amber-400'
                            : 'bg-primary/20 text-primary'
                      }`}
                    >
                      {item.severity}
                    </span>
                    <h4 className="text-sm font-semibold text-foreground">{item.title}</h4>
                    {item.organization_name && (
                      <span className="text-xs text-muted-foreground">• {item.organization_name}</span>
                    )}
                  </div>

                  <p className="text-xs text-foreground/80 leading-relaxed">{item.why}</p>

                  <div className="flex items-center gap-1.5 text-xs text-primary font-medium pt-1">
                    <span>Suggested Action:</span>
                    <span>{item.suggested_action}</span>
                  </div>
                </div>

                {/* Action buttons */}
                <div className="flex items-center gap-2 self-end sm:self-center shrink-0">
                  <Link
                    href={item.action_href}
                    className="flex items-center gap-1 rounded-xl bg-card border border-border px-3 py-1.5 text-xs font-semibold text-foreground hover:bg-muted"
                  >
                    <span>Open</span>
                    <ExternalLink className="size-3" />
                  </Link>

                  <button
                    onClick={() =>
                      dismissMutation.mutate({
                        rule: item.rule,
                        target_id: item.target_id || undefined,
                        organization_id: item.organization_id || undefined,
                        action: 'snooze',
                        snooze_hours: 24,
                      })
                    }
                    disabled={dismissMutation.isPending}
                    title="Snooze for 24 hours"
                    className="flex items-center gap-1 rounded-xl border border-border bg-card px-3 py-1.5 text-xs font-medium text-muted-foreground hover:bg-muted hover:text-foreground"
                  >
                    <Moon className="size-3" />
                    <span>Snooze</span>
                  </button>

                  <button
                    onClick={() =>
                      dismissMutation.mutate({
                        rule: item.rule,
                        target_id: item.target_id || undefined,
                        organization_id: item.organization_id || undefined,
                        action: 'resolve',
                      })
                    }
                    disabled={dismissMutation.isPending}
                    title="Mark resolved"
                    className="flex items-center gap-1 rounded-xl bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground hover:bg-primary/90"
                  >
                    <CheckCircle2 className="size-3.5" />
                    <span>Resolve</span>
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}
