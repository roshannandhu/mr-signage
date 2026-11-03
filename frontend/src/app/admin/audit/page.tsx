'use client'

import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Download,
  Eye,
  RefreshCw,
  Search,
  User,
  X,
} from 'lucide-react'
import { adminApi } from '@/lib/api'
import type { AuditEvent } from '@/lib/types'
import { PageHeader } from '@/components/admin/admin-ui'
import { AdminDataTable, type DataTableColumn } from '@/components/admin/kit/admin-data-table'
import { toast } from 'sonner'

export default function AdminAuditPage() {
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(25)
  const [search, setSearch] = useState('')
  const [actionFilter, setActionFilter] = useState('all')
  const [selectedEvent, setSelectedEvent] = useState<AuditEvent | null>(null)

  // Fetch audit events
  const { data, isLoading, isFetching, refetch } = useQuery({
    queryKey: ['admin', 'audit', page, pageSize, actionFilter],
    queryFn: async () => {
      const offset = (page - 1) * pageSize
      return adminApi.listAuditEvents({
        limit: pageSize,
        offset,
        action: actionFilter !== 'all' ? actionFilter : undefined,
      })
    },
  })

  const rawItems = data?.items
  const events = useMemo(() => rawItems ?? [], [rawItems])
  const totalCount = data?.total ?? 0

  // Filtered by local search query if typed
  const filteredEvents = useMemo(() => {
    if (!search.trim()) return events
    const q = search.toLowerCase()
    return events.filter(
      (e) =>
        e.action.toLowerCase().includes(q) ||
        e.actor_username?.toLowerCase().includes(q) ||
        e.target_type?.toLowerCase().includes(q) ||
        String(e.target_id || '').toLowerCase().includes(q) ||
        e.reason?.toLowerCase().includes(q),
    )
  }, [events, search])

  // CSV Export
  const exportCsv = () => {
    if (!events.length) {
      toast.error('No audit records to export')
      return
    }
    const headers = ['ID', 'Occurred At', 'Actor', 'Target Type', 'Target ID', 'Action', 'Reason', 'IP', 'Batch ID']
    const rows = filteredEvents.map((e) => [
      e.id,
      e.occurred_at,
      `"${e.actor_username || 'System'}"`,
      `"${e.target_type || ''}"`,
      `"${e.target_id || ''}"`,
      `"${e.action}"`,
      `"${(e.reason || '').replace(/"/g, '""')}"`,
      `"${e.ip || ''}"`,
      `"${e.batch_id || ''}"`,
    ])
    const csvContent = [headers.join(','), ...rows.map((r) => r.join(','))].join('\n')
    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `audit-log-${new Date().toISOString().slice(0, 10)}.csv`
    a.click()
    URL.revokeObjectURL(url)
    toast.success('Audit log CSV exported')
  }

  const columns: DataTableColumn<AuditEvent>[] = [
    {
      id: 'occurred_at',
      header: 'Timestamp',
      cell: ({ row }) => {
        if (!row.occurred_at) return <span className="font-mono text-xs text-muted-foreground">—</span>
        const d = new Date(row.occurred_at)
        return (
          <div className="flex flex-col">
            <span className="font-mono text-xs text-foreground">
              {d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
            </span>
            <span className="text-[11px] text-muted-foreground">{d.toLocaleDateString()}</span>
          </div>
        )
      },
    },
    {
      id: 'actor',
      header: 'Operator / Actor',
      cell: ({ row }) => (
        <div className="flex items-center gap-1.5">
          <User className="size-3.5 text-muted-foreground" />
          <span className="font-medium text-foreground">{row.actor_username || 'System'}</span>
          {row.acting_org_id && (
            <span className="rounded bg-amber-500/10 px-1.5 py-0.5 text-[10px] text-amber-700 dark:text-amber-400">
              Act-As {row.acting_org_id}
            </span>
          )}
        </div>
      ),
    },
    {
      id: 'action',
      header: 'Action',
      cell: ({ row }) => {
        let badgeStyle = 'bg-muted text-muted-foreground border-border'
        if (row.action.includes('create') || row.action.includes('approve')) {
          badgeStyle = 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-500/20'
        } else if (row.action.includes('update') || row.action.includes('command')) {
          badgeStyle = 'bg-primary/10 text-primary border-primary/20'
        } else if (row.action.includes('delete') || row.action.includes('reject') || row.action.includes('suspend') || row.action.includes('unlock')) {
          badgeStyle = 'bg-rose-500/10 text-rose-700 dark:text-rose-400 border-rose-500/20'
        }
        return (
          <span className={`inline-flex items-center rounded-md border px-2 py-0.5 font-mono text-[11px] ${badgeStyle}`}>
            {row.action}
          </span>
        )
      },
    },
    {
      id: 'target',
      header: 'Target Resource',
      cell: ({ row }) => (
        <div className="flex items-center gap-1 text-xs">
          <span className="text-muted-foreground">{row.target_type || '—'}:</span>
          <span className="font-mono font-medium text-foreground">{row.target_id || '—'}</span>
        </div>
      ),
    },
    {
      id: 'reason',
      header: 'Audit Reason',
      cell: ({ row }) => (
        <span className="truncate max-w-[200px] text-xs text-muted-foreground" title={row.reason || ''}>
          {row.reason || '—'}
        </span>
      ),
    },
    {
      id: 'ip',
      header: 'IP Address',
      cell: ({ row }) => <span className="font-mono text-xs text-muted-foreground">{row.ip || '—'}</span>,
    },
    {
      id: 'actions',
      header: 'Diff',
      cell: ({ row }) => (
        <button
          onClick={() => setSelectedEvent(row)}
          className="flex items-center gap-1 rounded-lg border border-border bg-muted/60 px-2 py-1 text-xs font-medium text-foreground hover:bg-muted"
        >
          <Eye className="size-3" />
          View
        </button>
      ),
    },
  ]

  return (
    <div className="space-y-6 p-6 text-foreground lg:p-8">
      <PageHeader
        title="Immutable Platform Audit Log"
        description="Comprehensive cryptographically-audited history of all operator actions, permission grants, and systemic changes"
      >
        <div className="flex items-center gap-2">
          <button
            onClick={exportCsv}
            className="flex items-center gap-1.5 rounded-xl border border-border bg-card px-3 py-2 text-xs font-semibold text-foreground shadow-sm hover:bg-muted"
          >
            <Download className="size-3.5" />
            Export CSV
          </button>
          <button
            onClick={() => refetch()}
            className="flex items-center gap-1.5 rounded-xl border border-border bg-muted px-3 py-2 text-xs text-muted-foreground hover:bg-muted/80"
          >
            <RefreshCw className={`size-3.5 ${isFetching ? 'animate-spin' : ''}`} />
            Refresh
          </button>
        </div>
      </PageHeader>

      <section className="space-y-4 rounded-2xl border border-border bg-card p-5">
        {/* Filters */}
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="relative min-w-[260px] flex-1 max-w-md">
            <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search by action, operator, target, reason…"
              className="w-full rounded-xl border border-border bg-muted/40 py-2 pl-9 pr-4 text-xs text-foreground placeholder:text-muted-foreground focus:border-primary focus:outline-none"
            />
          </div>

          <div className="flex items-center gap-2">
            <select
              value={actionFilter}
              onChange={(e) => {
                setActionFilter(e.target.value)
                setPage(1)
              }}
              className="rounded-xl border border-border bg-muted/40 px-3 py-2 text-xs text-foreground focus:outline-none"
            >
              <option value="all">All Action Categories</option>
              <option value="tenant">Tenant Changes (tenant.*)</option>
              <option value="screen">Screen & Kiosk (screen.*)</option>
              <option value="plan">Packages & Plans (plan.*)</option>
              <option value="security">Security & Roles (security.* / user.*)</option>
              <option value="platform">Platform Settings (platform.*)</option>
            </select>
          </div>
        </div>

        {/* Audit DataTable */}
        <AdminDataTable
          columns={columns}
          data={filteredEvents}
          isLoading={isLoading}
          getRowId={(r) => r.id}
          pagination={{
            page,
            pageSize,
            totalCount,
            onPageChange: setPage,
            onPageSizeChange: (s) => {
              setPageSize(s)
              setPage(1)
            },
          }}
          emptyState={{
            title: 'No audit records found',
            description: 'No administrative actions match your current filter parameters.',
          }}
        />
      </section>

      {/* Before / After Diff Drawer */}
      {selectedEvent && (
        <div className="fixed inset-0 z-50 flex justify-end bg-black/60 backdrop-blur-sm">
          <div className="flex h-full w-full max-w-xl flex-col border-l border-border bg-card p-6 shadow-2xl">
            <div className="flex items-center justify-between border-b border-border pb-4">
              <div>
                <h3 className="text-base font-semibold text-foreground">Audit Record Details</h3>
                <p className="font-mono text-xs text-muted-foreground">
                  Event #{selectedEvent.id} — {selectedEvent.action}
                </p>
              </div>
              <button
                onClick={() => setSelectedEvent(null)}
                className="rounded-lg p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
              >
                <X className="size-5" />
              </button>
            </div>

            <div className="flex-1 space-y-4 overflow-y-auto py-4 text-xs">
              <div className="grid grid-cols-2 gap-3 rounded-xl border border-border bg-muted/20 p-3">
                <div>
                  <span className="text-muted-foreground">Operator:</span>
                  <p className="font-medium text-foreground">{selectedEvent.actor_username || 'System'}</p>
                </div>
                <div>
                  <span className="text-muted-foreground">Timestamp:</span>
                  <p className="font-medium text-foreground">
                    {selectedEvent.occurred_at ? new Date(selectedEvent.occurred_at).toLocaleString() : '—'}
                  </p>
                </div>
                <div>
                  <span className="text-muted-foreground">Target Type:</span>
                  <p className="font-medium text-foreground">{selectedEvent.target_type || '—'}</p>
                </div>
                <div>
                  <span className="text-muted-foreground">Target ID:</span>
                  <p className="font-mono text-foreground">{selectedEvent.target_id || '—'}</p>
                </div>
                <div>
                  <span className="text-muted-foreground">Client IP:</span>
                  <p className="font-mono text-foreground">{selectedEvent.ip || '—'}</p>
                </div>
                <div>
                  <span className="text-muted-foreground">Target Organization ID:</span>
                  <p className="font-mono text-foreground">{selectedEvent.target_org_id || '—'}</p>
                </div>
              </div>

              {selectedEvent.reason && (
                <div className="rounded-xl border border-primary/20 bg-primary/5 p-3">
                  <span className="font-semibold text-foreground">Operator Stated Reason:</span>
                  <p className="mt-1 text-foreground leading-relaxed">{selectedEvent.reason}</p>
                </div>
              )}

              {/* Diffs */}
              <div className="space-y-3 pt-2">
                <h4 className="font-semibold uppercase tracking-wider text-muted-foreground">Payload Diff Snapshot</h4>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <div className="space-y-1">
                    <span className="text-muted-foreground">Before State</span>
                    <pre className="max-h-64 overflow-auto rounded-xl border border-border bg-muted/40 p-3 font-mono text-[11px] text-foreground">
                      {selectedEvent.before_json
                        ? JSON.stringify(JSON.parse(selectedEvent.before_json), null, 2)
                        : '(None / New Resource)'}
                    </pre>
                  </div>
                  <div className="space-y-1">
                    <span className="text-muted-foreground">After State</span>
                    <pre className="max-h-64 overflow-auto rounded-xl border border-border bg-muted/40 p-3 font-mono text-[11px] text-foreground">
                      {selectedEvent.after_json
                        ? JSON.stringify(JSON.parse(selectedEvent.after_json), null, 2)
                        : '(None / Deleted Resource)'}
                    </pre>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

