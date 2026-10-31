'use client'

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle, Clock, ShieldCheck, XCircle } from 'lucide-react'
import { adminApi } from '@/lib/api'
import type { TenantSummary } from '@/lib/types'
import { Feedback, PageHeader, formatBytes } from '@/components/admin/admin-ui'

/**
 * The approvals queue: which companies get in, and on what package.
 *
 * The page this replaces offered two fixed buttons ("Approve (10 TVs)" / "Approve (50
 * TVs)") that hardcoded the limits at the call site. Approval now assigns a package, so
 * the limits live in one editable place and every tenant on that package moves together.
 */
export default function AdminApprovalsPage() {
  const queryClient = useQueryClient()
  const [selectedPackage, setSelectedPackage] = useState<Record<number, number | ''>>({})
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  const pendingQuery = useQuery({
    queryKey: ['admin', 'tenants', 'pending_approval'],
    queryFn: () => adminApi.listTenants('pending_approval'),
  })
  const packagesQuery = useQuery({ queryKey: ['admin', 'packages'], queryFn: adminApi.listPackages })

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['admin', 'tenants'] })
  }

  const approve = useMutation({
    mutationFn: ({ id, planId }: { id: number; planId: number | '' }) =>
      adminApi.approveTenant(id, planId === '' ? {} : { plan_id: planId }),
    onSuccess: (tenant) => {
      setError('')
      setMessage(`${tenant.name} approved — ${tenant.max_screens ?? '∞'} screens, ${tenant.max_ad_slots ?? '∞'} ad slots.`)
      refresh()
    },
    onError: (e: Error) => { setMessage(''); setError(e.message) },
  })

  const reject = useMutation({
    mutationFn: ({ id, reason }: { id: number; reason: string }) => adminApi.rejectTenant(id, reason),
    onSuccess: (tenant) => {
      setError('')
      setMessage(`${tenant.name} was rejected.`)
      refresh()
    },
    onError: (e: Error) => { setMessage(''); setError(e.message) },
  })

  const pending = pendingQuery.data ?? []
  const packages = (packagesQuery.data ?? []).filter((p) => p.is_active)

  const handleReject = (tenant: TenantSummary) => {
    const reason = window.prompt(`Why is "${tenant.name}" being rejected? The owner sees this.`)
    if (reason === null) return
    reject.mutate({ id: tenant.id, reason: reason.trim() || 'Application could not be approved at this time.' })
  }

  return (
    <div className="space-y-6 p-6 text-foreground lg:p-8">
      <PageHeader title="Approvals Queue" description="Companies waiting to be let onto the platform" />

      <Feedback ok={message} error={error} />

      {packages.length === 0 && !packagesQuery.isLoading && (
        <div className="flex items-center gap-2 rounded-xl border border-amber-500/30 bg-amber-500/10 p-3 text-sm text-amber-300">
          <Clock className="size-4 shrink-0" />
          No active packages yet. Approving without one leaves the workspace unlimited — create packages first.
        </div>
      )}

      <section className="overflow-hidden rounded-2xl border border-border bg-card">
        <div className="flex items-center gap-2 border-b border-border p-4 text-sm font-semibold">
          <ShieldCheck className="size-4 text-amber-400" />
          {pending.length} awaiting review
        </div>

        {pendingQuery.isLoading ? (
          <p className="p-10 text-center text-sm text-muted-foreground">Loading…</p>
        ) : pending.length === 0 ? (
          <p className="p-10 text-center text-sm text-muted-foreground">
            Nothing waiting. New signups appear here automatically.
          </p>
        ) : (
          <ul className="divide-y divide-border">
            {pending.map((tenant) => (
              <li key={tenant.id} className="space-y-3 p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="font-semibold">{tenant.name}</p>
                    <p className="font-mono text-xs text-emerald-400">{tenant.owner_email ?? 'No owner email'}</p>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      {tenant.owner_name ?? 'Unknown owner'} · registered{' '}
                      {tenant.created_at ? new Date(tenant.created_at).toLocaleDateString() : '—'}
                      {tenant.screens_count > 0 && ` · ${tenant.screens_count} screen(s) already connected`}
                    </p>
                  </div>

                  <div className="flex flex-wrap items-center gap-2">
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
                          {pkg.name} — {pkg.max_screens == null ? '∞' : pkg.max_screens} screens, {pkg.max_storage_bytes ? formatBytes(pkg.max_storage_bytes) : 'unlimited storage'}
                        </option>
                      ))}
                    </select>

                    <button
                      onClick={() => approve.mutate({ id: tenant.id, planId: selectedPackage[tenant.id] ?? '' })}
                      disabled={approve.isPending}
                      className="flex items-center gap-1.5 rounded-lg border border-emerald-500/20 bg-emerald-500/10 px-3 py-1.5 text-xs text-emerald-400 transition-all hover:bg-emerald-500/20 disabled:opacity-50"
                    >
                      <CheckCircle className="size-3" />
                      Approve
                    </button>
                    <button
                      onClick={() => handleReject(tenant)}
                      disabled={reject.isPending}
                      className="flex items-center gap-1.5 rounded-lg border border-rose-500/20 bg-rose-500/10 px-3 py-1.5 text-xs text-rose-400 transition-all hover:bg-rose-500/20 disabled:opacity-50"
                    >
                      <XCircle className="size-3" />
                      Reject
                    </button>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}
