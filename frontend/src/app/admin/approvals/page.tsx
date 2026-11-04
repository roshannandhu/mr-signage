'use client'

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertCircle,
  CheckCircle,
  Clock,
  History,
  Info,
  Mail,
  ShieldCheck,
  Sparkles,
  X,
  XCircle,
} from 'lucide-react'
import { adminApi } from '@/lib/api'
import type { TenantSummary, AuditEvent } from '@/lib/types'
import { Feedback, PageHeader, formatBytes } from '@/components/admin/admin-ui'
import { useConfirm } from '@/components/admin/kit/confirm-dialog'
import { CustomRequestsPanel } from '@/components/admin/custom-requests-panel'
import { AdminDataTable, type DataTableColumn } from '@/components/admin/kit/admin-data-table'

export default function AdminApprovalsPage() {
  const queryClient = useQueryClient()
  const confirm = useConfirm()

  const [activeTab, setActiveTab] = useState<'signups' | 'custom' | 'history'>('signups')
  const [selectedPackage, setSelectedPackage] = useState<Record<number, number | ''>>({})
  const [inspectTenant, setInspectTenant] = useState<TenantSummary | null>(null)
  const [bulkPlanId, setBulkPlanId] = useState<number | ''>('')
  const [selectedTenantIds, setSelectedTenantIds] = useState<number[]>([])
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  // Queries
  const pendingQuery = useQuery({
    queryKey: ['admin', 'tenants', 'pending_approval'],
    queryFn: () => adminApi.listTenants('pending_approval'),
  })

  const allTenantsQuery = useQuery({
    queryKey: ['admin', 'tenants', 'all'],
    queryFn: () => adminApi.listTenants(),
  })

  const packagesQuery = useQuery({
    queryKey: ['admin', 'packages'],
    queryFn: adminApi.listPackages,
  })

  const customRequestsQuery = useQuery({
    queryKey: ['admin', 'custom-requests'],
    queryFn: adminApi.listCustomRequests,
  })

  // History tab: audit events for approve / reject
  const historyQuery = useQuery({
    queryKey: ['admin', 'audit', 'approvals-history'],
    queryFn: () =>
      adminApi.listAuditEvents({
        action: 'tenant.approve,tenant.reject',
        limit: 100,
      }),
    enabled: activeTab === 'history',
  })

  const pending = pendingQuery.data ?? []
  const allTenants = allTenantsQuery.data ?? []
  const packages = (packagesQuery.data ?? []).filter((p) => p.is_active)
  const customRequests = customRequestsQuery.data ?? []
  const pendingCustomCount = customRequests.filter((r) => r.status === 'requested').length

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['admin', 'tenants'] })
    queryClient.invalidateQueries({ queryKey: ['admin', 'custom-requests'] })
    queryClient.invalidateQueries({ queryKey: ['admin', 'audit'] })
  }

  const approve = useMutation({
    mutationFn: ({ id, planId }: { id: number; planId: number | '' }) =>
      adminApi.approveTenant(id, planId === '' ? {} : { plan_id: planId }),
    onSuccess: (tenant) => {
      setError('')
      setMessage(
        `${tenant.name} approved — ${tenant.max_screens ?? '∞'} screens, ${tenant.max_ad_slots ?? '∞'} ad slots.`,
      )
      refresh()
    },
    onError: (e: Error) => {
      setMessage('')
      setError(e.message)
    },
  })

  const reject = useMutation({
    mutationFn: ({ id, reason }: { id: number; reason: string }) => adminApi.rejectTenant(id, reason),
    onSuccess: (tenant) => {
      setError('')
      setMessage(`${tenant.name} was rejected.`)
      refresh()
    },
    onError: (e: Error) => {
      setMessage('')
      setError(e.message)
    },
  })

  const handleReject = async (tenant: TenantSummary) => {
    const result = await confirm({
      title: `Reject ${tenant.name}?`,
      description: 'The owner sees the reason you give, and it is saved to the audit log.',
      level: 2,
      destructive: true,
      confirmLabel: 'Reject application',
    })
    if (!result) return
    reject.mutate({
      id: tenant.id,
      reason: result.reason || 'Application could not be approved at this time.',
    })
  }

  const handleBulkApprove = async () => {
    if (selectedTenantIds.length === 0) return
    const names = pending.filter((t) => selectedTenantIds.includes(t.id)).map((t) => t.name)
    const result = await confirm({
      title: `Approve ${selectedTenantIds.length} workspaces?`,
      description: `The following workspaces will be approved onto the chosen package: ${names.join(', ')}`,
      level: 2,
      confirmLabel: 'Approve selected',
    })
    if (!result) return

    let approvedCount = 0
    const failures: { id: number; name: string; error: string }[] = []
    for (const id of selectedTenantIds) {
      const tenant = pending.find((t) => t.id === id)
      const name = tenant?.name || `Org #${id}`
      try {
        await adminApi.approveTenant(id, bulkPlanId === '' ? {} : { plan_id: bulkPlanId })
        approvedCount++
      } catch (err) {
        const errorMsg = err instanceof Error ? err.message : 'Unknown error'
        failures.push({ id, name, error: errorMsg })
        console.error(`Failed to approve tenant ${id}:`, err)
      }
    }
    if (failures.length > 0) {
      setError(
        `Failed to approve ${failures.length} workspace(s): ${failures.map((f) => `${f.name} (${f.error})`).join(', ')}`
      )
    } else {
      setError('')
    }
    if (approvedCount > 0) {
      setMessage(`Successfully bulk approved ${approvedCount} workspace(s).`)
    }
    setSelectedTenantIds([])
    refresh()
  }

  // Duplicate hints detector
  const getDuplicateHints = (tenant: TenantSummary) => {
    const emailDomain = tenant.owner_email ? tenant.owner_email.split('@')[1] : null
    const commonDomains = ['gmail.com', 'yahoo.com', 'outlook.com', 'hotmail.com', 'icloud.com']
    const isGenericDomain = !emailDomain || commonDomains.includes(emailDomain.toLowerCase())

    const duplicates = allTenants.filter((other) => {
      if (other.id === tenant.id) return false
      // Same custom domain
      if (
        !isGenericDomain &&
        emailDomain &&
        other.owner_email &&
        other.owner_email.toLowerCase().endsWith(`@${emailDomain.toLowerCase()}`)
      ) {
        return true
      }
      // Exact or very close name
      if (
        other.name.toLowerCase() === tenant.name.toLowerCase() ||
        other.slug === tenant.slug
      ) {
        return true
      }
      return false
    })

    return duplicates
  }

  const historyColumns: DataTableColumn<AuditEvent>[] = [
    {
      id: 'date',
      header: 'Time',
      width: 'w-36',
      cell: ({ row }) => (
        <span className="font-mono text-xs text-muted-foreground">
          {row.occurred_at ? new Date(row.occurred_at).toLocaleString() : '—'}
        </span>
      ),
    },
    {
      id: 'action',
      header: 'Decision',
      width: 'w-28',
      cell: ({ row }) => (
        <span
          className={`inline-block rounded-md border px-2 py-0.5 text-xs font-semibold ${
            row.action === 'tenant.approve'
              ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400'
              : 'border-rose-500/30 bg-rose-500/10 text-rose-700 dark:text-rose-400'
          }`}
        >
          {row.action === 'tenant.approve' ? 'Approved' : 'Rejected'}
        </span>
      ),
    },
    {
      id: 'actor',
      header: 'Operator',
      width: 'w-36',
      hideOnMobile: true,
      cell: ({ row }) => (
        <span className="font-mono text-xs text-foreground">{row.actor_username ?? 'System'}</span>
      ),
    },
    {
      id: 'target',
      header: 'Workspace ID',
      width: 'w-28',
      cell: ({ row }) => (
        <span className="font-mono text-xs text-muted-foreground">Org #{row.target_org_id}</span>
      ),
    },
    {
      id: 'reason',
      header: 'Stated Reason',
      hideOnMobile: true,
      cell: ({ row }) => (
        <span className="text-xs text-muted-foreground">
          {row.reason ? decodeURIComponent(row.reason) : 'No reason recorded'}
        </span>
      ),
    },
  ]

  return (
    <div className="space-y-6 p-3.5 sm:p-6 lg:p-8 text-foreground">
      <PageHeader
        title="Review Center"
        description="Unified decisions inbox for workspace signups, bespoke custom requests and approval history"
      />

      <Feedback
        ok={message}
        error={error}
        onDismiss={() => {
          setMessage('')
          setError('')
        }}
      />

      {/* Navigation tabs */}
      <div className="flex overflow-x-auto border-b border-border text-sm scrollbar-none">
        <button
          onClick={() => setActiveTab('signups')}
          className={`flex items-center gap-2 border-b-2 px-4 py-2.5 font-medium transition-colors ${
            activeTab === 'signups'
              ? 'border-primary text-foreground'
              : 'border-transparent text-muted-foreground hover:text-foreground'
          }`}
        >
          <ShieldCheck className="size-4" />
          Signups Pending ({pending.length})
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
          Custom Requests ({pendingCustomCount})
        </button>
        <button
          onClick={() => setActiveTab('history')}
          className={`flex items-center gap-2 border-b-2 px-4 py-2.5 font-medium transition-colors ${
            activeTab === 'history'
              ? 'border-primary text-foreground'
              : 'border-transparent text-muted-foreground hover:text-foreground'
          }`}
        >
          <History className="size-4" />
          Decision History
        </button>
      </div>

      {activeTab === 'signups' && (
        <div className="space-y-4">
          {packages.length === 0 && !packagesQuery.isLoading && (
            <div className="flex items-center gap-2 rounded-xl border border-amber-500/30 bg-amber-500/10 p-3 text-sm text-amber-700 dark:text-amber-300">
              <Clock className="size-4 shrink-0" />
              No active packages yet. Approving without one leaves the workspace unlimited.
            </div>
          )}

          {pending.length > 0 && (
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-border bg-card p-4">
              <div className="flex items-center gap-3">
                <input
                  type="checkbox"
                  aria-label="Select all pending workspaces"
                  checked={
                    selectedTenantIds.length > 0 && selectedTenantIds.length === pending.length
                  }
                  onChange={(e) => {
                    if (e.target.checked) setSelectedTenantIds(pending.map((t) => t.id))
                    else setSelectedTenantIds([])
                  }}
                  className="size-4 rounded border-border"
                />
                <span className="text-xs font-medium text-muted-foreground">
                  {selectedTenantIds.length} of {pending.length} selected for bulk action
                </span>
              </div>

              <div className="flex items-center gap-2">
                <select
                  aria-label="Choose package for bulk approval"
                  value={bulkPlanId}
                  onChange={(e) =>
                    setBulkPlanId(e.target.value === '' ? '' : Number(e.target.value))
                  }
                  className="rounded-lg border border-border bg-muted px-3 py-1.5 text-xs text-foreground outline-none"
                >
                  <option value="">Default: No package (unlimited)</option>
                  {packages.map((pkg) => (
                    <option key={pkg.id} value={pkg.id}>
                      {pkg.name} ({pkg.max_screens == null ? '∞' : pkg.max_screens} screens)
                    </option>
                  ))}
                </select>

                <button
                  onClick={handleBulkApprove}
                  disabled={selectedTenantIds.length === 0}
                  className="flex items-center gap-1.5 rounded-lg border border-emerald-500/30 bg-emerald-500/20 px-3.5 py-1.5 text-xs font-semibold text-emerald-700 dark:text-emerald-400 transition-all hover:bg-emerald-500/30 disabled:opacity-50"
                >
                  <CheckCircle className="size-3.5" />
                  Approve selected ({selectedTenantIds.length})
                </button>
              </div>
            </div>
          )}

          <section className="overflow-hidden rounded-2xl border border-border bg-card">
            <div className="flex items-center gap-2 border-b border-border p-4 text-sm font-semibold">
              <ShieldCheck className="size-4 text-amber-700 dark:text-amber-400" />
              {pending.length} applications awaiting decision
            </div>

            {pendingQuery.isLoading ? (
              <p className="p-10 text-center text-sm text-muted-foreground">Loading…</p>
            ) : pending.length === 0 ? (
              <p className="p-10 text-center text-sm text-muted-foreground">
                Nothing waiting in the queue. New signups appear here automatically.
              </p>
            ) : (
              <ul className="divide-y divide-border">
                {pending.map((tenant) => {
                  const duplicates = getDuplicateHints(tenant)
                  return (
                    <li key={tenant.id} className="space-y-3 p-4 transition-colors hover:bg-muted/40">
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div className="flex items-start gap-3">
                          <input
                            type="checkbox"
                            aria-label={`Select ${tenant.name}`}
                            checked={selectedTenantIds.includes(tenant.id)}
                            onChange={(e) => {
                              if (e.target.checked) {
                                setSelectedTenantIds((prev) => [...prev, tenant.id])
                              } else {
                                setSelectedTenantIds((prev) => prev.filter((id) => id !== tenant.id))
                              }
                            }}
                            className="mt-1 size-4 rounded border-border"
                          />
                          <div className="min-w-0">
                            <div className="flex items-center gap-2">
                              <p className="font-semibold text-foreground">{tenant.name}</p>
                              {duplicates.length > 0 && (
                                <span
                                  title={`Possible duplicate: matches ${duplicates.map((d) => d.name).join(', ')}`}
                                  className="inline-flex items-center gap-1 rounded bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-medium text-amber-600 dark:text-amber-400 border border-amber-500/20"
                                >
                                  <AlertCircle className="size-3" />
                                  {duplicates.length} duplicate match
                                </span>
                              )}
                            </div>
                            <p className="font-mono text-xs text-emerald-700 dark:text-emerald-400">
                              {tenant.owner_email ?? 'No owner email'}
                            </p>
                            <p className="mt-0.5 text-xs text-muted-foreground">
                              {tenant.owner_name ?? 'Unknown owner'} • registered{' '}
                              {tenant.created_at
                                ? new Date(tenant.created_at).toLocaleDateString()
                                : '—'}
                              {tenant.screens_count > 0 &&
                                ` • ${tenant.screens_count} screen(s) pre-registered`}
                            </p>
                          </div>
                        </div>

                        <div className="flex flex-wrap items-center gap-2">
                          <button
                            onClick={() => setInspectTenant(tenant)}
                            className="flex items-center gap-1.5 rounded-lg border border-border bg-background px-2.5 py-1.5 text-xs text-foreground transition-all hover:bg-muted"
                            title="Inspect details and duplicate hints"
                          >
                            <Info className="size-3" />
                            Details
                          </button>

                          <a
                            href={`mailto:${tenant.owner_email ?? ''}?subject=${encodeURIComponent(
                              `Your MR Signage Application: ${tenant.name}`,
                            )}&body=${encodeURIComponent(
                              `Hello ${tenant.owner_name || 'there'},\n\nThank you for signing up for MR Signage. Before approving your workspace, could you please tell us a little more about your planned screen network?\n\nBest regards,\nMR Signage Operations`,
                            )}`}
                            className="flex items-center gap-1.5 rounded-lg border border-border bg-background px-2.5 py-1.5 text-xs text-foreground transition-all hover:bg-muted"
                            title="Draft email inquiry to owner"
                          >
                            <Mail className="size-3" />
                            Ask for info
                          </a>

                          <select
                            aria-label={`Package for ${tenant.name}`}
                            value={selectedPackage[tenant.id] ?? ''}
                            onChange={(e) =>
                              setSelectedPackage((prev) => ({
                                ...prev,
                                [tenant.id]: e.target.value === '' ? '' : Number(e.target.value),
                              }))
                            }
                            className="rounded-lg border border-border bg-muted px-3 py-1.5 text-xs text-foreground"
                          >
                            <option value="">No package (unlimited)</option>
                            {packages.map((pkg) => (
                              <option key={pkg.id} value={pkg.id}>
                                {pkg.name} — {pkg.max_screens == null ? '∞' : pkg.max_screens} screens
                              </option>
                            ))}
                          </select>

                          <button
                            onClick={() =>
                              approve.mutate({
                                id: tenant.id,
                                planId: selectedPackage[tenant.id] ?? '',
                              })
                            }
                            disabled={approve.isPending}
                            className="flex items-center gap-1.5 rounded-lg border border-emerald-500/20 bg-emerald-500/10 px-3 py-1.5 text-xs font-medium text-emerald-700 dark:text-emerald-400 transition-all hover:bg-emerald-500/20 disabled:opacity-50"
                          >
                            <CheckCircle className="size-3" />
                            Approve
                          </button>
                          <button
                            onClick={() => handleReject(tenant)}
                            disabled={reject.isPending}
                            className="flex items-center gap-1.5 rounded-lg border border-rose-500/20 bg-rose-500/10 px-3 py-1.5 text-xs font-medium text-rose-700 dark:text-rose-400 transition-all hover:bg-rose-500/20 disabled:opacity-50"
                          >
                            <XCircle className="size-3" />
                            Reject
                          </button>
                        </div>
                      </div>
                    </li>
                  )
                })}
              </ul>
            )}
          </section>
        </div>
      )}

      {activeTab === 'custom' && (
        <div className="space-y-4">
          <CustomRequestsPanel />
        </div>
      )}

      {activeTab === 'history' && (
        <div className="space-y-4">
          <AdminDataTable
            data={historyQuery.data?.items ?? []}
            columns={historyColumns}
            isLoading={historyQuery.isLoading}
            pagination={{
              page: 1,
              pageSize: 50,
              totalCount: historyQuery.data?.total ?? 0,
              onPageChange: () => {},
              onPageSizeChange: () => {},
            }}
          />
        </div>
      )}

      {/* Details drawer for tenant review */}
      {inspectTenant && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-end bg-black/60 backdrop-blur-sm"
          onClick={() => setInspectTenant(null)}
        >
          <div
            className="h-full w-full max-w-md space-y-6 overflow-y-auto border-l border-border bg-card p-6 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between border-b border-border pb-4">
              <div>
                <h3 className="text-lg font-bold text-foreground">{inspectTenant.name}</h3>
                <p className="font-mono text-xs text-muted-foreground">Org ID #{inspectTenant.id}</p>
              </div>
              <button
                onClick={() => setInspectTenant(null)}
                className="grid size-8 place-items-center rounded-lg border border-border hover:bg-muted text-foreground"
              >
                <X className="size-4" />
              </button>
            </div>

            <div className="space-y-4 text-sm">
              <div className="rounded-xl border border-border bg-muted/30 p-3 space-y-2">
                <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                  Applicant Identity
                </span>
                <p className="font-medium text-foreground">
                  Owner: {inspectTenant.owner_name || 'Not provided'}
                </p>
                <p className="font-mono text-xs text-emerald-700 dark:text-emerald-400">
                  Email: {inspectTenant.owner_email || '—'}
                </p>
                <p className="text-xs text-muted-foreground">
                  Signed up: {inspectTenant.created_at ? new Date(inspectTenant.created_at).toLocaleString() : '—'}
                </p>
              </div>

              <div className="rounded-xl border border-border bg-muted/30 p-3 space-y-2">
                <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                  Network Footprint
                </span>
                <p className="text-xs text-foreground">
                  Screens pre-paired: {inspectTenant.screens_count}
                </p>
                <p className="text-xs text-foreground">
                  Ad slots active: {inspectTenant.ad_slots_used}
                </p>
                <p className="text-xs text-foreground">
                  Storage: {formatBytes(inspectTenant.storage_used_bytes)}
                </p>
              </div>

              {/* Duplicate analysis */}
              <div className="rounded-xl border border-border bg-muted/30 p-3 space-y-2">
                <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                  Duplicate Risk Analysis
                </span>
                {(() => {
                  const dupes = getDuplicateHints(inspectTenant)
                  if (dupes.length === 0) {
                    return (
                      <p className="text-xs text-emerald-600 dark:text-emerald-400">
                        No name or domain duplicates detected across the platform.
                      </p>
                    )
                  }
                  return (
                    <div className="space-y-2">
                      <p className="text-xs text-amber-600 dark:text-amber-400 font-medium">
                        Found {dupes.length} potential matching workspace(s):
                      </p>
                      <ul className="space-y-1 text-xs text-muted-foreground">
                        {dupes.map((d) => (
                          <li key={d.id} className="rounded border border-border bg-card p-2">
                            <span className="font-semibold text-foreground">{d.name}</span> (slug:{' '}
                            {d.slug}) • Owner: {d.owner_email}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )
                })()}
              </div>
            </div>

            <div className="flex gap-2 pt-4">
              <button
                onClick={() => {
                  approve.mutate({
                    id: inspectTenant.id,
                    planId: selectedPackage[inspectTenant.id] ?? '',
                  })
                  setInspectTenant(null)
                }}
                className="flex-1 rounded-xl bg-primary py-2.5 text-sm font-semibold text-primary-foreground hover:bg-primary/90"
              >
                Approve application
              </button>
              <button
                onClick={() => {
                  setInspectTenant(null)
                  handleReject(inspectTenant)
                }}
                className="rounded-xl border border-rose-500/30 bg-rose-500/10 px-4 py-2.5 text-sm font-medium text-rose-700 dark:text-rose-400 hover:bg-rose-500/20"
              >
                Reject
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
