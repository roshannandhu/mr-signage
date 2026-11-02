'use client'

import React from 'react'
import Link from 'next/link'
import { useQuery } from '@tanstack/react-query'
import {
  AlertTriangle,
  ArrowRight,
  Bell,
  CheckCircle2,
  CreditCard,
  Receipt,
  RotateCcw,
  ShieldCheck,
} from 'lucide-react'
import { adminApi } from '@/lib/api'
import { cn } from '@/lib/utils'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'

export function AdminNotificationsBell() {
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['admin', 'inbox-counts'],
    queryFn: adminApi.getInboxCounts,
    refetchInterval: 30000,
    staleTime: 10000,
  })

  const pendingApprovals = data?.pending_approvals ?? 0
  const newCustomRequests = data?.new_custom_requests ?? 0
  const criticalAlerts = data?.critical_alerts ?? 0
  const failedPayments = data?.failed_payments_7d ?? 0

  const totalAttention = pendingApprovals + newCustomRequests + criticalAlerts + failedPayments
  const hasCritical = criticalAlerts > 0

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        className="relative grid size-9 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring cursor-pointer"
        aria-label={`Admin notifications: ${totalAttention} items requiring attention`}
      >
        <Bell className="size-4.5" aria-hidden="true" />
        {totalAttention > 0 && (
          <span
            className={cn(
              'absolute -top-1 -right-1 flex h-4 min-w-4 items-center justify-center rounded-full px-1 text-[10px] font-bold leading-none shadow-xs',
              hasCritical ? 'bg-rose-600 text-white animate-pulse' : 'bg-amber-500 text-black',
            )}
          >
            {totalAttention > 99 ? '99+' : totalAttention}
          </span>
        )}
      </DropdownMenuTrigger>

      <DropdownMenuContent align="end" sideOffset={8} className="w-80 sm:w-88 p-2">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-border px-3 py-2.5">
          <div className="flex items-center gap-2">
            <Bell className="size-4 text-foreground" />
            <h3 className="text-sm font-semibold text-foreground">Operational Attention</h3>
          </div>
          {totalAttention > 0 ? (
            <span
              className={cn(
                'rounded-full px-2 py-0.5 text-[11px] font-semibold',
                hasCritical
                  ? 'bg-rose-500/15 text-rose-700 dark:text-rose-400 border border-rose-500/20'
                  : 'bg-amber-500/15 text-amber-700 dark:text-amber-400 border border-amber-500/20',
              )}
            >
              {totalAttention} action{totalAttention === 1 ? '' : 's'}
            </span>
          ) : (
            <span className="rounded-full bg-emerald-500/15 px-2 py-0.5 text-[11px] font-semibold text-emerald-700 dark:text-emerald-400 border border-emerald-500/20">
              All clear
            </span>
          )}
        </div>

        {/* Content Body */}
        <div className="py-2">
          {isLoading ? (
            <div className="p-6 text-center text-xs text-muted-foreground">Checking operational queues…</div>
          ) : isError ? (
            <div className="flex items-center justify-between p-3 text-xs text-rose-700 dark:text-rose-400 bg-rose-500/10 rounded-lg">
              <span>Could not load attention counts.</span>
              <button
                type="button"
                onClick={() => refetch()}
                className="flex items-center gap-1 font-semibold hover:underline"
              >
                <RotateCcw className="size-3" /> Retry
              </button>
            </div>
          ) : totalAttention === 0 ? (
            <div className="flex flex-col items-center justify-center p-6 text-center">
              <CheckCircle2 className="size-8 text-emerald-600 dark:text-emerald-400 mb-2" />
              <p className="text-xs font-semibold text-foreground">All queues are clear</p>
              <p className="text-[11px] text-muted-foreground mt-0.5">
                No pending tenant approvals, custom requests, critical alerts or failed payments.
              </p>
            </div>
          ) : (
            <div className="space-y-1">
              {/* Critical Alerts */}
              {criticalAlerts > 0 && (
                <Link
                  href="/admin/fleet"
                  className="flex items-start gap-3 rounded-lg p-2.5 transition-colors hover:bg-muted"
                >
                  <div className="grid size-7 shrink-0 place-items-center rounded-lg border border-rose-500/30 bg-rose-500/15 text-rose-600 dark:text-rose-400">
                    <AlertTriangle className="size-4" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center justify-between">
                      <p className="text-xs font-semibold text-rose-700 dark:text-rose-400">Critical Alerts</p>
                      <span className="rounded bg-rose-600 px-1.5 py-0.2 text-[10px] font-bold text-white">
                        {criticalAlerts}
                      </span>
                    </div>
                    <p className="text-[11px] text-muted-foreground leading-tight mt-0.5">
                      {criticalAlerts} open TV crash or hardware alert{criticalAlerts === 1 ? '' : 's'} requiring immediate attention.
                    </p>
                  </div>
                </Link>
              )}

              {/* Pending Approvals */}
              {pendingApprovals > 0 && (
                <Link
                  href="/admin/approvals"
                  className="flex items-start gap-3 rounded-lg p-2.5 transition-colors hover:bg-muted"
                >
                  <div className="grid size-7 shrink-0 place-items-center rounded-lg border border-amber-500/30 bg-amber-500/15 text-amber-600 dark:text-amber-400">
                    <ShieldCheck className="size-4" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center justify-between">
                      <p className="text-xs font-semibold text-foreground">Pending Approvals</p>
                      <span className="rounded bg-amber-500 px-1.5 py-0.2 text-[10px] font-bold text-black">
                        {pendingApprovals}
                      </span>
                    </div>
                    <p className="text-[11px] text-muted-foreground leading-tight mt-0.5">
                      {pendingApprovals} tenant application{pendingApprovals === 1 ? '' : 's'} waiting for operator approval.
                    </p>
                  </div>
                </Link>
              )}

              {/* Custom Plan Requests */}
              {newCustomRequests > 0 && (
                <Link
                  href="/admin/billing"
                  className="flex items-start gap-3 rounded-lg p-2.5 transition-colors hover:bg-muted"
                >
                  <div className="grid size-7 shrink-0 place-items-center rounded-lg border border-cyan-500/30 bg-cyan-500/15 text-cyan-600 dark:text-cyan-400">
                    <Receipt className="size-4" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center justify-between">
                      <p className="text-xs font-semibold text-foreground">Custom Plan Requests</p>
                      <span className="rounded bg-cyan-500 px-1.5 py-0.2 text-[10px] font-bold text-black">
                        {newCustomRequests}
                      </span>
                    </div>
                    <p className="text-[11px] text-muted-foreground leading-tight mt-0.5">
                      {newCustomRequests} enterprise pricing request{newCustomRequests === 1 ? '' : 's'} waiting for quotation.
                    </p>
                  </div>
                </Link>
              )}

              {/* Failed Payments */}
              {failedPayments > 0 && (
                <Link
                  href="/admin/billing"
                  className="flex items-start gap-3 rounded-lg p-2.5 transition-colors hover:bg-muted"
                >
                  <div className="grid size-7 shrink-0 place-items-center rounded-lg border border-rose-500/30 bg-rose-500/15 text-rose-600 dark:text-rose-400">
                    <CreditCard className="size-4" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center justify-between">
                      <p className="text-xs font-semibold text-foreground">Failed Payments (7d)</p>
                      <span className="rounded bg-rose-500 px-1.5 py-0.2 text-[10px] font-bold text-white">
                        {failedPayments}
                      </span>
                    </div>
                    <p className="text-[11px] text-muted-foreground leading-tight mt-0.5">
                      {failedPayments} checkout or renewal transaction{failedPayments === 1 ? '' : 's'} failed in the past week.
                    </p>
                  </div>
                </Link>
              )}
            </div>
          )}
        </div>

        <DropdownMenuSeparator />

        {/* Footer Link */}
        <Link
          href="/admin"
          className="flex items-center justify-between rounded-lg px-3 py-2 text-xs font-medium text-muted-foreground hover:bg-accent hover:text-foreground transition-colors"
        >
          <span>Open Mission Control</span>
          <ArrowRight className="size-3.5" />
        </Link>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
