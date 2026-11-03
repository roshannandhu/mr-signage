'use client'

import { useState, useMemo } from 'react'
import Link from 'next/link'
import { useQuery } from '@tanstack/react-query'
import {
  Calendar,
  Download,
  ExternalLink,
  IndianRupee,
  Receipt,
  Sparkles,
  TrendingUp,
  Users,
  X,
} from 'lucide-react'
import { adminApi } from '@/lib/api'
import type { PlatformPayment, TenantSummary } from '@/lib/types'
import { PageHeader, StatCard, formatPaise } from '@/components/admin/admin-ui'
import {
  AdminDataTable,
  type DataTableColumn,
} from '@/components/admin/kit/admin-data-table'

const kindLabel: Record<string, string> = {
  plan: 'Package Purchase',
  custom: 'Custom Package',
  subscription: 'Recurring Renewal',
}

function exportPaymentsToCsv(payments: PlatformPayment[]) {
  const headers = [
    'Payment ID',
    'Tenant Name',
    'Tenant Org ID',
    'Category',
    'Amount (₹)',
    'Currency',
    'Provider',
    'Order ID',
    'Provider Payment ID',
    'Status',
    'Paid Timestamp',
  ]
  const rows = payments.map((p) => [
    p.id,
    `"${(p.organization_name || `Org ${p.organization_id}`).replace(/"/g, '""')}"`,
    p.organization_id,
    p.kind,
    (p.amount_paise / 100).toFixed(2),
    p.currency,
    p.provider,
    p.provider_order_id || '',
    p.provider_payment_id || '',
    p.status,
    p.paid_at || '',
  ])

  const csvContent =
    'data:text/csv;charset=utf-8,' +
    [headers.join(','), ...rows.map((r) => r.join(','))].join('\n')
  const encodedUri = encodeURI(csvContent)
  const link = document.createElement('a')
  link.setAttribute('href', encodedUri)
  link.setAttribute('download', `mr_signage_payments_${new Date().toISOString().slice(0, 10)}.csv`)
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
}

export default function AdminBillingPage() {
  const [activeTab, setActiveTab] = useState<'payments' | 'renewals' | 'custom'>('payments')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(25)
  const [kindFilter, setKindFilter] = useState<string>('all')
  const [providerFilter, setProviderFilter] = useState<string>('all')
  const [selectedPayment, setSelectedPayment] = useState<PlatformPayment | null>(null)

  // Overview metrics (30d)
  const { data: overview } = useQuery({
    queryKey: ['admin', 'overview', '30d'],
    queryFn: () => adminApi.getOverview('30d'),
  })

  // All tenants to find upcoming renewals
  const { data: allTenants = [] } = useQuery({
    queryKey: ['admin', 'tenants', 'all'],
    queryFn: () => adminApi.listTenants(),
  })

  // Paged payments
  const {
    data: pagedPayments,
    isLoading: isPaymentsLoading,
  } = useQuery({
    queryKey: ['admin', 'payments', page, pageSize, kindFilter, providerFilter],
    queryFn: () =>
      adminApi.listPaymentsPaged({
        kind: kindFilter === 'all' ? undefined : kindFilter,
        provider: providerFilter === 'all' ? undefined : providerFilter,
        limit: pageSize,
        offset: (page - 1) * pageSize,
      }),
  })

  // Custom requests
  const { data: customRequests = [] } = useQuery({
    queryKey: ['admin', 'custom-requests'],
    queryFn: adminApi.listCustomRequests,
  })

  // Renewals calculation
  const renewals = useMemo(() => {
    const now = new Date().getTime()
    return allTenants
      .filter((t) => t.current_period_end && !t.deleted_at)
      .map((t) => {
        const end = new Date(t.current_period_end!).getTime()
        const daysLeft = Math.ceil((end - now) / (1000 * 60 * 60 * 24))
        return {
          ...t,
          daysLeft,
        }
      })
      .filter((t) => t.daysLeft <= 30)
      .sort((a, b) => a.daysLeft - b.daysLeft)
  }, [allTenants])

  const payments = pagedPayments?.items || []
  const totalPayments = pagedPayments?.total || 0

  const paymentColumns: DataTableColumn<PlatformPayment>[] = [
    {
      id: 'tenant',
      header: 'Workspace',
      cell: ({ row }) => (
        <div>
          <Link
            href={`/admin/tenants/${row.organization_id}`}
            className="font-semibold text-foreground hover:underline"
            onClick={(e) => e.stopPropagation()}
          >
            {row.organization_name ?? `Workspace #${row.organization_id}`}
          </Link>
          <p className="font-mono text-xs text-muted-foreground">Org ID #{row.organization_id}</p>
        </div>
      ),
    },
    {
      id: 'kind',
      header: 'Category',
      width: 'w-32',
      cell: ({ row }) => (
        <span className="rounded-md border border-border bg-muted px-2 py-0.5 text-xs font-semibold uppercase text-foreground">
          {kindLabel[row.kind] ?? row.kind}
        </span>
      ),
    },
    {
      id: 'amount',
      header: 'Amount Collected',
      width: 'w-32',
      cell: ({ row }) => (
        <span className="font-bold text-foreground">{formatPaise(row.amount_paise)}</span>
      ),
    },
    {
      id: 'provider',
      header: 'Gateway',
      width: 'w-24',
      hideOnMobile: true,
      cell: ({ row }) => (
        <span className="font-mono text-xs capitalize text-muted-foreground">{row.provider}</span>
      ),
    },
    {
      id: 'reference',
      header: 'Transaction ID',
      hideOnMobile: true,
      cell: ({ row }) => (
        <span className="font-mono text-xs text-muted-foreground">
          {row.provider_payment_id ?? row.provider_order_id ?? '—'}
        </span>
      ),
    },
    {
      id: 'date',
      header: 'Paid Date',
      width: 'w-36',
      hideOnMobile: true,
      cell: ({ row }) => (
        <span className="font-mono text-xs text-muted-foreground">
          {new Date(row.paid_at).toLocaleDateString(undefined, {
            day: 'numeric',
            month: 'short',
            year: 'numeric',
          })}
        </span>
      ),
    },
  ]

  const renewalColumns: DataTableColumn<TenantSummary & { daysLeft: number }>[] = [
    {
      id: 'name',
      header: 'Workspace',
      cell: ({ row }) => (
        <div>
          <Link
            href={`/admin/tenants/${row.id}`}
            className="font-semibold text-foreground hover:underline"
          >
            {row.name}
          </Link>
          <p className="font-mono text-xs text-muted-foreground">{row.owner_email ?? 'No email'}</p>
        </div>
      ),
    },
    {
      id: 'plan',
      header: 'Plan',
      cell: ({ row }) => (
        <span className="text-xs text-foreground">{row.plan_name ?? 'Custom plan'}</span>
      ),
    },
    {
      id: 'countdown',
      header: 'Renewal Countdown',
      width: 'w-36',
      cell: ({ row }) => {
        const isExpired = row.daysLeft <= 0
        return (
          <span
            className={`inline-block rounded-md border px-2 py-0.5 text-xs font-semibold ${
              isExpired
                ? 'border-rose-500/30 bg-rose-500/10 text-rose-700 dark:text-rose-400'
                : row.daysLeft <= 7
                  ? 'border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400'
                  : 'border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400'
            }`}
          >
            {isExpired ? 'Expired' : `${row.daysLeft} days remaining`}
          </span>
        )
      },
    },
    {
      id: 'screens',
      header: 'Screens',
      width: 'w-24',
      cell: ({ row }) => <span className="font-mono text-xs">{row.screens_count} screens</span>,
    },
    {
      id: 'actions',
      header: '',
      align: 'right',
      width: 'w-28',
      cell: ({ row }) => (
        <Link
          href={`/admin/tenants/${row.id}`}
          className="rounded-lg border border-border bg-background px-2.5 py-1 text-xs font-semibold text-foreground hover:bg-muted"
        >
          Manage
        </Link>
      ),
    },
  ]

  return (
    <div className="space-y-6 p-3.5 sm:p-6 lg:p-8 text-foreground">
      <PageHeader
        title="Financial Command Center"
        description="Platform revenue collections, subscription renewal forecasts and payment transaction explorer"
      >
        <div className="flex flex-wrap items-center gap-2">
          <button
            onClick={() => exportPaymentsToCsv(payments)}
            className="flex items-center gap-2 rounded-xl border border-border bg-card px-3.5 py-2 text-xs sm:text-sm text-foreground transition-all hover:bg-muted"
          >
            <Download className="size-3.5" />
            Export CSV
          </button>
          <Link
            href="/admin/settings"
            className="flex items-center gap-1.5 rounded-xl border border-border bg-muted px-3.5 py-2 text-xs sm:text-sm text-muted-foreground hover:text-foreground"
          >
            SMTP & Email Setup
            <ExternalLink className="size-3" />
          </Link>
        </div>
      </PageHeader>

      {/* METRIC TILES */}
      <div className="grid grid-cols-2 gap-2.5 sm:gap-4 lg:grid-cols-4">
        <StatCard
          label="Collected (30 days)"
          value={overview ? formatPaise(overview.revenue_paise_total) : '—'}
          icon={IndianRupee}
          accent="emerald"
        />
        <StatCard
          label="Active Workspaces"
          value={overview?.tenants_active ?? '—'}
          icon={Users}
          accent="violet"
        />
        <StatCard
          label="Expected Renewals (14d)"
          value={overview ? `${overview.renewals_due_14d_count} workspaces` : '—'}
          icon={Calendar}
          accent="amber"
        />
        <StatCard
          label="Expected Value (14d)"
          value={overview ? formatPaise(overview.renewals_due_14d_value_paise) : '—'}
          icon={TrendingUp}
          accent="cyan"
        />
      </div>

      {/* NAVIGATION TABS */}
      <div className="flex border-b border-border text-sm">
        <button
          onClick={() => setActiveTab('payments')}
          className={`flex items-center gap-2 border-b-2 px-4 py-2.5 font-medium transition-colors ${
            activeTab === 'payments'
              ? 'border-primary text-foreground'
              : 'border-transparent text-muted-foreground hover:text-foreground'
          }`}
        >
          <Receipt className="size-4" />
          Payments Ledger
        </button>
        <button
          onClick={() => setActiveTab('renewals')}
          className={`flex items-center gap-2 border-b-2 px-4 py-2.5 font-medium transition-colors ${
            activeTab === 'renewals'
              ? 'border-primary text-foreground'
              : 'border-transparent text-muted-foreground hover:text-foreground'
          }`}
        >
          <Calendar className="size-4" />
          Renewals Queue ({renewals.length})
        </button>
        <button
          onClick={() => setActiveTab('custom')}
          className={`flex items-center gap-2 border-b-2 px-4 py-2.5 font-medium transition-colors ${
            activeTab === 'custom'
              ? 'border-primary text-foreground'
              : 'border-transparent text-muted-foreground hover:text-foreground'
          }`}
        >
          <Sparkles className="size-4" />
          Custom Invoices ({customRequests.filter((r) => r.status === 'paid').length})
        </button>
      </div>

      {/* TAB 1: PAYMENTS */}
      {activeTab === 'payments' && (
        <AdminDataTable
          data={payments}
          columns={paymentColumns}
          isLoading={isPaymentsLoading}
          onRowClick={(row) => setSelectedPayment(row)}
          pagination={{
            page,
            pageSize,
            totalCount: totalPayments,
            onPageChange: setPage,
            onPageSizeChange: (s) => {
              setPageSize(s)
              setPage(1)
            },
            pageSizeOptions: [10, 25, 50, 100],
          }}
          filters={
            <div className="flex items-center gap-2">
              <select
                aria-label="Filter payment category"
                value={kindFilter}
                onChange={(e) => {
                  setKindFilter(e.target.value)
                  setPage(1)
                }}
                className="rounded-lg border border-border bg-background px-3 py-1.5 text-xs text-foreground outline-none"
              >
                <option value="all">All categories</option>
                <option value="plan">Packages</option>
                <option value="subscription">Renewals</option>
                <option value="custom">Custom plans</option>
              </select>

              <select
                aria-label="Filter payment gateway"
                value={providerFilter}
                onChange={(e) => {
                  setProviderFilter(e.target.value)
                  setPage(1)
                }}
                className="rounded-lg border border-border bg-background px-3 py-1.5 text-xs text-foreground outline-none"
              >
                <option value="all">All gateways</option>
                <option value="razorpay">Razorpay</option>
                <option value="mock">Mock / Test</option>
              </select>
            </div>
          }
        />
      )}

      {/* TAB 2: RENEWALS */}
      {activeTab === 'renewals' && (
        <AdminDataTable
          data={renewals}
          columns={renewalColumns}
          isLoading={false}
          pagination={{
            page: 1,
            pageSize: 50,
            totalCount: renewals.length,
            onPageChange: () => {},
            onPageSizeChange: () => {},
          }}
        />
      )}

      {/* TAB 3: CUSTOM INVOICES */}
      {activeTab === 'custom' && (
        <div className="rounded-2xl border border-border bg-card p-6 space-y-4">
          <div className="flex items-center justify-between border-b border-border pb-3">
            <h3 className="font-bold text-foreground">Paid Bespoke Contracts</h3>
            <span className="text-xs text-muted-foreground font-mono">
              {customRequests.filter((r) => r.status === 'paid').length} captured contracts
            </span>
          </div>

          <ul className="divide-y divide-border text-xs">
            {customRequests
              .filter((r) => r.status === 'paid')
              .map((r) => (
                <li key={r.id} className="flex items-center justify-between py-3">
                  <div>
                    <p className="font-semibold text-foreground">
                      Contract #{r.id} • Org ID #{r.organization_id} ({r.company_name || 'Bespoke'})
                    </p>
                    <p className="text-muted-foreground">
                      Quota: {r.max_screens} screens • {r.max_ad_slots} ads • {r.duration_days} days
                    </p>
                  </div>
                  <div className="text-right">
                    <p className="font-bold text-foreground">{formatPaise(r.price_paise)}</p>
                    <span className="text-[10px] font-mono text-emerald-600 dark:text-emerald-400 font-semibold uppercase">
                      Paid & Activated
                    </span>
                  </div>
                </li>
              ))}
          </ul>
        </div>
      )}

      {/* PAYMENT DETAILS DRAWER */}
      {selectedPayment && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-end bg-black/60 backdrop-blur-sm"
          onClick={() => setSelectedPayment(null)}
        >
          <div
            className="h-full w-full max-w-md space-y-5 overflow-y-auto border-l border-border bg-card p-6 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between border-b border-border pb-3">
              <div>
                <h3 className="font-bold text-foreground">Payment #{selectedPayment.id}</h3>
                <p className="font-mono text-xs text-muted-foreground">
                  Status: {selectedPayment.status}
                </p>
              </div>
              <button
                onClick={() => setSelectedPayment(null)}
                className="grid size-8 place-items-center rounded-lg border border-border hover:bg-muted text-foreground"
              >
                <X className="size-4" />
              </button>
            </div>

            <div className="space-y-3 text-xs">
              <div className="rounded-xl border border-border bg-muted/20 p-3 space-y-1.5">
                <span className="font-bold uppercase tracking-wider text-muted-foreground text-[10px]">
                  Financial Breakdown
                </span>
                <p className="text-xl font-extrabold text-foreground">
                  {formatPaise(selectedPayment.amount_paise)}
                </p>
                <p className="text-muted-foreground">
                  Category: {kindLabel[selectedPayment.kind] || selectedPayment.kind}
                </p>
              </div>

              <div className="rounded-xl border border-border bg-muted/20 p-3 space-y-1.5">
                <span className="font-bold uppercase tracking-wider text-muted-foreground text-[10px]">
                  Gateway Details
                </span>
                <p className="text-foreground">Provider: {selectedPayment.provider}</p>
                <p className="font-mono text-muted-foreground">
                  Order ID: {selectedPayment.provider_order_id || 'N/A'}
                </p>
                <p className="font-mono text-muted-foreground">
                  Payment ID: {selectedPayment.provider_payment_id || 'N/A'}
                </p>
              </div>

              <div className="rounded-xl border border-border bg-muted/20 p-3 space-y-1.5">
                <span className="font-bold uppercase tracking-wider text-muted-foreground text-[10px]">
                  Customer Entity
                </span>
                <p className="font-semibold text-foreground">
                  {selectedPayment.organization_name || `Workspace #${selectedPayment.organization_id}`}
                </p>
                <Link
                  href={`/admin/tenants/${selectedPayment.organization_id}`}
                  className="inline-flex items-center gap-1 text-primary hover:underline"
                >
                  Inspect in Tenant 360
                  <ExternalLink className="size-3" />
                </Link>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
