'use client'

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Save, Sparkles } from 'lucide-react'
import { adminApi } from '@/lib/api'
import type { CustomPlanRequestItem, CustomPlanRequestUpdate } from '@/lib/types'
import { FEATURES, Feedback } from '@/components/admin/admin-ui'

const GIB = 1024 ** 3

const inputClass =
  'rounded-lg border border-border bg-muted px-3 py-2 text-sm text-foreground outline-none focus:border-violet-500/50'

function NumField({ label, value, onChange }: { label: string; value: number; onChange: (v: number) => void }) {
  return (
    <label className="block">
      <span className="mb-1 block text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">{label}</span>
      <input
        type="number"
        min={0}
        value={value}
        onChange={(e) => onChange(Math.max(0, Number(e.target.value) || 0))}
        className={`${inputClass} w-20 px-2 py-1 text-xs`}
      />
    </label>
  )
}

/**
 * Custom / Business plan requests: price one, revise its caps or features at any status
 * (including one already paid for -- it applies immediately, with no re-bill), or decline it.
 *
 * Self-contained (its own query, mutations and feedback banner) so it drops into the admin
 * Billing page on its own. Moved out of the Packages page rather than rewritten -- same
 * queries, same mutations, same markup.
 */
export function CustomRequestsPanel() {
  const queryClient = useQueryClient()
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  const { data: customRequests = [] } = useQuery({
    queryKey: ['admin', 'custom-requests'],
    queryFn: adminApi.listCustomRequests,
    refetchInterval: 15000,
  })
  const [prices, setPrices] = useState<Record<number, string>>({})
  const refreshRequests = () => queryClient.invalidateQueries({ queryKey: ['admin', 'custom-requests'] })
  const failed = (e: Error) => { setMessage(''); setError(e.message) }

  const priceReq = useMutation({
    mutationFn: ({ id, pricePaise }: { id: number; pricePaise: number }) => adminApi.priceCustomRequest(id, pricePaise),
    onSuccess: (r) => { setError(''); setMessage(`Priced request #${r.id}. The workspace can now pay.`); refreshRequests() },
    onError: failed,
  })
  // Unsaved edits to a request's caps/features, by request id -- a row can be revised at any
  // status, including one already paid for.
  const [reqEdits, setReqEdits] = useState<Record<number, CustomPlanRequestUpdate>>({})
  const editReq = (id: number, patch: CustomPlanRequestUpdate) =>
    setReqEdits((prev) => ({ ...prev, [id]: { ...prev[id], ...patch } }))

  const updateReq = useMutation({
    mutationFn: ({ id, body }: { id: number; body: CustomPlanRequestUpdate }) =>
      adminApi.updateCustomRequest(id, body),
    onSuccess: (r) => {
      setError('')
      setMessage(
        r.status === 'paid'
          ? `Updated request #${r.id}. The workspace moved to the new caps immediately; it was not re-billed.`
          : `Updated request #${r.id}.`,
      )
      setReqEdits((prev) => { const next = { ...prev }; delete next[r.id]; return next })
      refreshRequests()
    },
    onError: failed,
  })

  const rejectReq = useMutation({
    mutationFn: (id: number) => adminApi.rejectCustomRequest(id),
    onSuccess: (r) => { setError(''); setMessage(`Request #${r.id} declined.`); refreshRequests() },
    onError: failed,
  })

  return (
    <div className="space-y-4">
      <Feedback ok={message} error={error} />
      <section className="overflow-hidden rounded-2xl border border-border bg-card">
        <div className="flex items-center gap-2 border-b border-border p-4 text-sm font-semibold">
          <Sparkles className="size-4 text-emerald-400" />
          {customRequests.length} custom requests
        </div>
        {customRequests.length === 0 ? (
          <p className="p-10 text-center text-sm text-muted-foreground">No open custom requests. A workspace can send one from its storefront.</p>
        ) : (
          <ul className="divide-y divide-border">
            {customRequests.map((request: CustomPlanRequestItem) => {
              const draft = reqEdits[request.id] ?? {}
              const dirty = Object.keys(draft).length > 0
              const draftFeatures = draft.feature_flags ?? request.feature_flags ?? {}
              return (
                <li key={request.id} className="flex flex-wrap items-center justify-between gap-4 p-5">
                  <div className="min-w-0">
                    <p className="text-sm font-semibold">
                      {request.organization_name ?? `Workspace ${request.organization_id}`}
                      <span className={`ml-2 rounded-full px-2 py-0.5 text-xs ${request.status === 'paid' ? 'bg-violet-500/10 text-violet-300' : request.status === 'priced' ? 'bg-emerald-500/10 text-emerald-300' : 'bg-amber-500/10 text-amber-300'}`}>
                        {request.status}
                      </span>
                    </p>
                    {request.notes && <p className="mt-1 text-xs italic text-muted-foreground">“{request.notes}”</p>}
                    <div className="mt-3 flex flex-wrap items-end gap-2">
                      <NumField label="TVs" value={draft.max_screens ?? request.max_screens}
                        onChange={(v) => editReq(request.id, { max_screens: v })} />
                      <NumField label="Clients" value={draft.max_clients ?? request.max_clients}
                        onChange={(v) => editReq(request.id, { max_clients: v })} />
                      <NumField label="Ad slots" value={draft.max_ad_slots ?? request.max_ad_slots}
                        onChange={(v) => editReq(request.id, { max_ad_slots: v })} />
                      <NumField label="Storage GB"
                        value={Math.round((draft.max_storage_bytes ?? request.max_storage_bytes) / GIB)}
                        onChange={(v) => editReq(request.id, { max_storage_bytes: v * GIB })} />
                      <NumField label="Days" value={draft.duration_days ?? request.duration_days}
                        onChange={(v) => editReq(request.id, { duration_days: v })} />
                      <NumField label="Price ₹"
                        value={Math.round((draft.price_paise ?? request.price_paise) / 100)}
                        onChange={(v) => editReq(request.id, { price_paise: v * 100 })} />
                    </div>
                    <div className="mt-2 flex flex-wrap gap-3">
                      {FEATURES.map((f) => (
                        <label key={f.key} className="flex items-center gap-1.5 text-xs text-muted-foreground">
                          <input
                            type="checkbox"
                            checked={draftFeatures[f.key] ?? false}
                            onChange={(e) => editReq(request.id, { feature_flags: { ...draftFeatures, [f.key]: e.target.checked } })}
                            className="h-3.5 w-3.5 accent-violet-500"
                          />
                          {f.label}
                        </label>
                      ))}
                    </div>
                    {dirty && (
                      <button
                        onClick={() => updateReq.mutate({ id: request.id, body: draft })}
                        disabled={updateReq.isPending}
                        className="mt-3 inline-flex items-center gap-1.5 rounded-lg border border-violet-500/20 bg-violet-500/10 px-3 py-1.5 text-xs text-violet-300 transition-all hover:bg-violet-500/20 disabled:opacity-40"
                      >
                        <Save className="h-3 w-3" />
                        {request.status === 'paid' ? 'Save (applies now, no re-bill)' : 'Save changes'}
                      </button>
                    )}
                  </div>
                  <div className="flex items-center gap-2">
                    {request.status !== 'paid' && (
                      <>
                        <span className="text-xs text-muted-foreground">₹</span>
                        <input
                          type="number"
                          min={0}
                          placeholder={request.price_paise ? String(request.price_paise / 100) : 'price'}
                          value={prices[request.id] ?? ''}
                          onChange={(e) => setPrices((p) => ({ ...p, [request.id]: e.target.value }))}
                          className={`${inputClass} w-28`}
                        />
                        <button
                          onClick={() => priceReq.mutate({ id: request.id, pricePaise: Math.round(Number(prices[request.id] || 0) * 100) })}
                          disabled={priceReq.isPending || !prices[request.id]}
                          className="rounded-lg border border-emerald-500/20 bg-emerald-500/10 px-3 py-1.5 text-xs text-emerald-300 transition-all hover:bg-emerald-500/20 disabled:opacity-30"
                        >
                          Set price &amp; release
                        </button>
                      </>
                    )}
                    <button
                      onClick={() => rejectReq.mutate(request.id)}
                      disabled={rejectReq.isPending}
                      className="rounded-lg border border-rose-500/20 bg-rose-500/10 px-3 py-1.5 text-xs text-rose-400 transition-all hover:bg-rose-500/20 disabled:opacity-50"
                    >
                      Decline
                    </button>
                  </div>
                </li>
              )
            })}
          </ul>
        )}
      </section>
    </div>
  )
}
