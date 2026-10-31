'use client'

import { useState } from 'react'
import Link from 'next/link'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Package as PackageIcon, Plus, Save, Trash2 } from 'lucide-react'
import { adminApi } from '@/lib/api'
import type { Package, PackageWrite } from '@/lib/types'
import { FEATURES, Feedback, LimitInput, PageHeader, UNLIMITED, formatBytes, formatPaise } from '@/components/admin/admin-ui'

const GIB = 1024 ** 3

const blank: PackageWrite = {
  name: '',
  slug: '',
  monthly_price_paise: 0,
  yearly_price_paise: 0,
  price_paise: 0,
  duration_days: 30,
  max_screens: 10,
  max_clients: 10,
  max_storage_bytes: 10 * GIB,
  max_ad_slots: 20,
  feature_flags: { scheduling: true },
  is_active: true,
}

/**
 * Packages: the named limits, price and features a workspace is sold on.
 *
 * The Plan table has existed since billing was added, with exactly one way to change it --
 * editing a hardcoded tuple in backend/billing.py, which `ensure_billing_catalog` then
 * skips for any slug that already exists. So prices and quotas could not be changed on a
 * live database at all without direct SQL. This is the admin surface for it.
 *
 * A package sells access as a ONE-TIME charge (`price_paise`) for a fixed window
 * (`duration_days`); the storefront reads those, not the legacy monthly/yearly figures.
 */
export default function AdminPackagesPage() {
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState<PackageWrite | null>(null)
  const [editing, setEditing] = useState<Record<number, Partial<PackageWrite>>>({})
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  const { data: packages = [], isLoading } = useQuery({
    queryKey: ['admin', 'packages'],
    queryFn: adminApi.listPackages,
  })

  const done = (text: string) => {
    setError('')
    setMessage(text)
    queryClient.invalidateQueries({ queryKey: ['admin', 'packages'] })
  }
  const failed = (e: Error) => { setMessage(''); setError(e.message) }

  const create = useMutation({
    mutationFn: (body: PackageWrite) => adminApi.createPackage(body),
    onSuccess: (pkg) => { setDraft(null); done(`Package "${pkg.name}" created.`) },
    onError: failed,
  })

  const update = useMutation({
    mutationFn: ({ id, body }: { id: number; body: Partial<PackageWrite> }) => adminApi.updatePackage(id, body),
    onSuccess: (pkg) => {
      setEditing((prev) => { const next = { ...prev }; delete next[pkg.id]; return next })
      done(`Package "${pkg.name}" updated.`)
    },
    onError: failed,
  })

  const remove = useMutation({
    mutationFn: (id: number) => adminApi.deletePackage(id),
    onSuccess: (res) => done(res.detail ?? 'Package deleted.'),
    onError: failed,
  })

  const fieldOf = (pkg: Package, key: keyof PackageWrite) =>
    (editing[pkg.id]?.[key] ?? pkg[key as keyof Package]) as never

  // A limit as the form shows it. Screens store "no limit" as null; ad slots, clients and
  // storage store it as 0. The form speaks one language -- UNLIMITED -- and the server
  // translates it back, so nobody has to know which column means what.
  type LimitKey = 'max_screens' | 'max_clients' | 'max_ad_slots' | 'max_storage_bytes'
  const limitOf = (pkg: Package, key: LimitKey): number => {
    const edited = editing[pkg.id]?.[key]
    if (edited !== undefined) return edited
    const stored = pkg[key]
    return stored == null || (key !== 'max_screens' && stored === 0) ? UNLIMITED : stored
  }

  const edit = (id: number, key: keyof PackageWrite, value: string | number | boolean | Record<string, boolean>) =>
    setEditing((prev) => ({ ...prev, [id]: { ...prev[id], [key]: value } }))

  // The working feature map for a row: its unsaved edit if any, else what is stored.
  const featuresOf = (pkg: Package): Record<string, boolean> =>
    (editing[pkg.id]?.feature_flags ?? pkg.feature_flags ?? {}) as Record<string, boolean>

  const toggleFeature = (pkg: Package, key: string, on: boolean) =>
    edit(pkg.id, 'feature_flags', { ...featuresOf(pkg), [key]: on })

  return (
    <div className="space-y-6 p-6 text-foreground lg:p-8">
      <PageHeader title="Packages" description="Price, access period, limits and features a workspace can be sold on">
        <button
          onClick={() => setDraft(draft ? null : blank)}
          className="flex items-center gap-2 rounded-xl border border-violet-500/20 bg-violet-500/10 px-4 py-2 text-sm text-violet-300 transition-all hover:bg-violet-500/20"
        >
          <Plus className="size-4" />
          {draft ? 'Cancel' : 'New package'}
        </button>
      </PageHeader>
      <p className="-mt-4 text-xs text-muted-foreground">
        Looking for a tenant&apos;s custom / Business request, payments, or the contact address on their email draft? That moved to{' '}
        <Link href="/admin/billing" className="text-violet-400 hover:underline">Billing</Link>.
      </p>

      <Feedback ok={message} error={error} />

      {draft && (
        <form
          onSubmit={(e) => { e.preventDefault(); create.mutate(draft) }}
          className="space-y-4 rounded-2xl border border-violet-500/20 bg-violet-500/[0.03] p-5"
        >
          <h2 className="font-semibold">New package</h2>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Field label="Name">
              <input required value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} className={inputClass} />
            </Field>
            <Field label="Slug" hint="lowercase, cannot change later">
              <input
                required pattern="[a-z0-9][a-z0-9-]*" value={draft.slug}
                onChange={(e) => setDraft({ ...draft, slug: e.target.value.toLowerCase() })}
                className={`${inputClass} font-mono`}
              />
            </Field>
            <Field label="Price (₹)" hint="one-time, per period">
              <input
                type="number" min={0} value={draft.price_paise / 100}
                onChange={(e) => setDraft({ ...draft, price_paise: Math.round(Number(e.target.value) * 100) })}
                className={inputClass}
              />
            </Field>
            <Field label="Access period (days)">
              <input
                type="number" min={1} value={draft.duration_days}
                onChange={(e) => setDraft({ ...draft, duration_days: Number(e.target.value) })}
                className={inputClass}
              />
            </Field>
            <Field label="Max screens" hint="0 = none">
              <LimitInput label="Max screens" value={draft.max_screens} onChange={(v) => setDraft({ ...draft, max_screens: v })} />
            </Field>
            <Field label="Max clients">
              <LimitInput label="Max clients" value={draft.max_clients} onChange={(v) => setDraft({ ...draft, max_clients: v })} />
            </Field>
            <Field label="Max ad slots">
              <LimitInput label="Max ad slots" value={draft.max_ad_slots} onChange={(v) => setDraft({ ...draft, max_ad_slots: v })} />
            </Field>
            <Field label="Storage (GB)">
              <LimitInput label="Storage in GB" scale={GIB} value={draft.max_storage_bytes} onChange={(v) => setDraft({ ...draft, max_storage_bytes: v })} />
            </Field>
          </div>
          <Field label="Features">
            <div className="flex flex-wrap gap-3 pt-1">
              {FEATURES.map((f) => (
                <label key={f.key} className="flex items-center gap-2 text-sm text-muted-foreground">
                  <input
                    type="checkbox"
                    checked={Boolean(draft.feature_flags[f.key])}
                    onChange={(e) => setDraft({ ...draft, feature_flags: { ...draft.feature_flags, [f.key]: e.target.checked } })}
                    className="size-4 accent-violet-500"
                  />
                  {f.label}
                  {!f.enforced && (
                    <span
                      className="rounded bg-white/10 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-muted-foreground/80"
                      title="Sold as a commitment, not enforced by the software"
                    >
                      Not enforced
                    </span>
                  )}
                </label>
              ))}
            </div>
          </Field>
          <button
            type="submit" disabled={create.isPending}
            className="rounded-xl bg-violet-600 px-5 py-2.5 text-sm font-semibold text-white transition-all hover:bg-violet-500 disabled:opacity-50"
          >
            {create.isPending ? 'Creating…' : 'Create package'}
          </button>
        </form>
      )}

      <section className="overflow-hidden rounded-2xl border border-border bg-card">
        <div className="flex items-center gap-2 border-b border-border p-4 text-sm font-semibold">
          <PackageIcon className="size-4 text-violet-400" />
          {packages.length} packages
        </div>

        {isLoading ? (
          <p className="p-10 text-center text-sm text-muted-foreground">Loading…</p>
        ) : packages.length === 0 ? (
          <p className="p-10 text-center text-sm text-muted-foreground">No packages yet. Create one to sell workspaces onto it.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="border-b border-border bg-muted text-xs uppercase tracking-wider text-muted-foreground">
                <tr>
                  <th className="p-4 pl-5 text-left">Package</th>
                  <th className="p-4 text-left">Price (once)</th>
                  <th className="p-4 text-left">Days</th>
                  <th className="p-4 text-left">Screens</th>
                  <th className="p-4 text-left">Clients</th>
                  <th className="p-4 text-left">Ad slots</th>
                  <th className="p-4 text-left">Storage (GB)</th>
                  <th className="p-4 text-left">Features</th>
                  <th className="p-4 text-left">Active</th>
                  <th className="p-4 pr-5 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {packages.map((pkg) => {
                  const dirty = Boolean(editing[pkg.id])
                  return (
                    <tr key={pkg.id} className="transition-colors hover:bg-muted">
                      <td className="p-4 pl-5">
                        <input
                          value={fieldOf(pkg, 'name')}
                          onChange={(e) => edit(pkg.id, 'name', e.target.value)}
                          className={`${inputClass} w-36`}
                        />
                        <p className="mt-1 font-mono text-xs text-muted-foreground">{pkg.slug}</p>
                      </td>
                      <td className="p-4">
                        <input
                          type="number" min={0}
                          value={Number(fieldOf(pkg, 'price_paise')) / 100}
                          onChange={(e) => edit(pkg.id, 'price_paise', Math.round(Number(e.target.value) * 100))}
                          className={`${inputClass} w-24`}
                        />
                        <p className="mt-1 text-xs text-muted-foreground">{formatPaise(pkg.price_paise)}</p>
                      </td>
                      <td className="p-4">
                        <input type="number" min={1} value={fieldOf(pkg, 'duration_days')} onChange={(e) => edit(pkg.id, 'duration_days', Number(e.target.value))} className={`${inputClass} w-16`} />
                      </td>
                      <td className="p-4">
                        <LimitInput label="Max screens" width="w-20" value={limitOf(pkg, 'max_screens')} onChange={(v) => edit(pkg.id, 'max_screens', v)} />
                      </td>
                      <td className="p-4">
                        <LimitInput label="Max clients" width="w-20" value={limitOf(pkg, 'max_clients')} onChange={(v) => edit(pkg.id, 'max_clients', v)} />
                      </td>
                      <td className="p-4">
                        <LimitInput label="Max ad slots" width="w-20" value={limitOf(pkg, 'max_ad_slots')} onChange={(v) => edit(pkg.id, 'max_ad_slots', v)} />
                      </td>
                      <td className="p-4">
                        <LimitInput label="Storage in GB" scale={GIB} width="w-24" value={limitOf(pkg, 'max_storage_bytes')} onChange={(v) => edit(pkg.id, 'max_storage_bytes', v)} />
                        <p className="mt-1 text-xs text-muted-foreground">{pkg.max_storage_bytes ? formatBytes(pkg.max_storage_bytes) : 'Unlimited'}</p>
                      </td>
                      <td className="p-4">
                        <div className="flex flex-col gap-1.5">
                          {FEATURES.map((f) => (
                            <label key={f.key} className="flex items-center gap-2 whitespace-nowrap text-xs text-muted-foreground">
                              <input
                                type="checkbox"
                                checked={Boolean(featuresOf(pkg)[f.key])}
                                onChange={(e) => toggleFeature(pkg, f.key, e.target.checked)}
                                className="size-3.5 accent-violet-500"
                              />
                              {f.label}
                            </label>
                          ))}
                        </div>
                      </td>
                      <td className="p-4">
                        <input
                          type="checkbox"
                          checked={Boolean(fieldOf(pkg, 'is_active'))}
                          onChange={(e) => edit(pkg.id, 'is_active', e.target.checked)}
                          className="size-4 accent-violet-500"
                        />
                      </td>
                      <td className="p-4 pr-5">
                        <div className="flex items-center justify-end gap-2">
                          <button
                            onClick={() => update.mutate({ id: pkg.id, body: editing[pkg.id] ?? {} })}
                            disabled={!dirty || update.isPending}
                            className="flex items-center gap-1.5 rounded-lg border border-violet-500/20 bg-violet-500/10 px-3 py-1.5 text-xs text-violet-400 transition-all hover:bg-violet-500/20 disabled:opacity-30"
                          >
                            <Save className="size-3" />
                            Save
                          </button>
                          <button
                            onClick={() => {
                              if (window.confirm(`Delete "${pkg.name}"? Tenants already on it keep their limits and the package is retired instead.`)) {
                                remove.mutate(pkg.id)
                              }
                            }}
                            disabled={remove.isPending}
                            className="flex items-center gap-1.5 rounded-lg border border-rose-500/20 bg-rose-500/10 px-3 py-1.5 text-xs text-rose-400 transition-all hover:bg-rose-500/20 disabled:opacity-50"
                          >
                            <Trash2 className="size-3" />
                          </button>
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}

const inputClass =
  'rounded-lg border border-border bg-muted px-3 py-2 text-sm text-foreground outline-none focus:border-violet-500/50'

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-xs font-semibold text-muted-foreground">
        {label} {hint && <span className="font-normal text-muted-foreground">({hint})</span>}
      </span>
      {children}
    </label>
  )
}
