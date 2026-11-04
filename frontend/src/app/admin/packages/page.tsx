'use client'

import { useState, useMemo } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Check,
  Edit2,
  Eye,
  Layers,
  Plus,
  Table as TableIcon,
  Trash2,
  Users,
  X,
} from 'lucide-react'
import { adminApi } from '@/lib/api'
import type { Package, PackageWrite, FeatureItem } from '@/lib/types'
import {
  Feedback,
  LimitInput,
  PageHeader,
  UNLIMITED,
  formatBytes,
  formatPaise,
} from '@/components/admin/admin-ui'
import { useConfirm } from '@/components/admin/kit/confirm-dialog'

const GIB = 1024 ** 3

const defaultNewPackage: PackageWrite = {
  name: '',
  slug: '',
  monthly_price_paise: 99900,
  yearly_price_paise: 999000,
  price_paise: 99900,
  duration_days: 30,
  max_screens: 10,
  max_clients: 10,
  max_storage_bytes: 10 * GIB,
  max_ad_slots: 20,
  feature_flags: { scheduling: true },
  is_active: true,
}

export default function AdminPackagesPage() {
  const queryClient = useQueryClient()
  const confirm = useConfirm()

  const [viewMode, setViewMode] = useState<'cards' | 'matrix'>('cards')
  const [editingPlan, setEditingPlan] = useState<Package | null>(null)
  const [isCreating, setIsCreating] = useState(false)
  const [previewPlan, setPreviewPlan] = useState<Package | null>(null)
  const [planForm, setPlanForm] = useState<PackageWrite>(defaultNewPackage)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  // Queries
  const { data: packages = [] } = useQuery({
    queryKey: ['admin', 'packages'],
    queryFn: adminApi.listPackages,
  })

  const { data: canonicalFeatures = [] } = useQuery({
    queryKey: ['admin', 'features'],
    queryFn: adminApi.listFeatures,
  })

  const { data: allTenants = [] } = useQuery({
    queryKey: ['admin', 'tenants', 'all'],
    queryFn: () => adminApi.listTenants(),
  })

  // Count active tenants per plan
  const tenantCounts = useMemo(() => {
    const counts: Record<number, number> = {}
    for (const t of allTenants) {
      if (t.plan_id && !t.deleted_at) {
        counts[t.plan_id] = (counts[t.plan_id] || 0) + 1
      }
    }
    return counts
  }, [allTenants])

  const done = (text: string) => {
    setError('')
    setMessage(text)
    queryClient.invalidateQueries({ queryKey: ['admin', 'packages'] })
  }
  const failed = (e: Error) => {
    setMessage('')
    setError(e.message)
  }

  const create = useMutation({
    mutationFn: (body: PackageWrite) => adminApi.createPackage(body),
    onSuccess: (pkg) => {
      setIsCreating(false)
      done(`Package "${pkg.name}" created.`)
    },
    onError: failed,
  })

  const update = useMutation({
    mutationFn: ({ id, body }: { id: number; body: Partial<PackageWrite> }) =>
      adminApi.updatePackage(id, body),
    onSuccess: (pkg) => {
      setEditingPlan(null)
      done(`Package "${pkg.name}" updated.`)
    },
    onError: failed,
  })

  const remove = useMutation({
    mutationFn: (id: number) => adminApi.deletePackage(id),
    onSuccess: (res) => done(res.detail ?? 'Package deleted.'),
    onError: failed,
  })

  // Handle saving plan edits with impact check
  const handleSavePlan = async () => {
    if (isCreating) {
      create.mutate(planForm)
      return
    }

    if (!editingPlan) return

    // Run impact check before saving modifications
    try {
      const impact = await adminApi.checkPlanImpact(editingPlan.id, {
        max_screens: planForm.max_screens == null ? UNLIMITED : planForm.max_screens,
        max_ad_slots: planForm.max_ad_slots,
        max_clients: planForm.max_clients,
        max_storage_bytes: planForm.max_storage_bytes,
        feature_flags: planForm.feature_flags,
      })

      const hasImpact =
        impact.exceeding_screens > 0 ||
        impact.exceeding_ads > 0 ||
        impact.exceeding_storage > 0 ||
        impact.lost_features.length > 0

      if (hasImpact) {
        const warnings: string[] = []
        if (impact.exceeding_screens > 0)
          warnings.push(`${impact.exceeding_screens} tenant(s) will exceed the new TV limit`)
        if (impact.exceeding_ads > 0)
          warnings.push(`${impact.exceeding_ads} tenant(s) will exceed the new ad slots limit`)
        if (impact.exceeding_storage > 0)
          warnings.push(`${impact.exceeding_storage} tenant(s) will exceed storage limit`)
        if (impact.lost_features.length > 0)
          warnings.push(
            `Tenants will lose access to: ${impact.lost_features.join(', ')}`,
          )

        const confirmed = await confirm({
          title: `Apply package changes to ${editingPlan.name}?`,
          description: `This package is active on ${impact.tenants_count} workspace(s). Saving will impact live operations:\n\n• ${warnings.join('\n• ')}`,
          level: 2,
          confirmLabel: 'Apply changes anyway',
        })

        if (!confirmed) return
      }

      update.mutate({ id: editingPlan.id, body: planForm })
    } catch {
      // Fallback direct update if impact check encounters an issue
      update.mutate({ id: editingPlan.id, body: planForm })
    }
  }

  const openEditDrawer = (pkg: Package) => {
    setIsCreating(false)
    setEditingPlan(pkg)
    setPlanForm({
      name: pkg.name,
      slug: pkg.slug,
      monthly_price_paise: pkg.monthly_price_paise,
      yearly_price_paise: pkg.yearly_price_paise,
      price_paise: pkg.price_paise,
      duration_days: pkg.duration_days,
      max_screens: pkg.max_screens == null ? UNLIMITED : pkg.max_screens,
      max_clients: pkg.max_clients,
      max_storage_bytes: pkg.max_storage_bytes,
      max_ad_slots: pkg.max_ad_slots,
      feature_flags: pkg.feature_flags || {},
      is_active: pkg.is_active,
    })
  }

  const openCreateDrawer = () => {
    setEditingPlan(null)
    setIsCreating(true)
    setPlanForm({ ...defaultNewPackage })
  }

  // Feature categorization
  const featureCategories = useMemo(() => {
    const cats: Record<string, FeatureItem[]> = {}
    for (const f of canonicalFeatures) {
      if (!cats[f.category]) cats[f.category] = []
      cats[f.category].push(f)
    }
    return cats
  }, [canonicalFeatures])

  return (
    <div className="space-y-6 p-3.5 sm:p-6 lg:p-8 text-foreground">
      <PageHeader
        title="Feature Control Studio"
        description="Configure commercial packages, hardware quotas, feature tiers and tenant pricing"
      >
        <div className="flex flex-wrap items-center gap-2">
          <button
            onClick={() => setViewMode(viewMode === 'cards' ? 'matrix' : 'cards')}
            className="flex items-center gap-2 rounded-xl border border-border bg-card px-4 py-2 text-sm text-foreground transition-all hover:bg-muted"
          >
            {viewMode === 'cards' ? <TableIcon className="size-4" /> : <Layers className="size-4" />}
            {viewMode === 'cards' ? 'Compare Matrix' : 'Plan Cards'}
          </button>
          <button
            onClick={openCreateDrawer}
            className="flex items-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground transition-all hover:bg-primary/90"
          >
            <Plus className="size-4" />
            New Package
          </button>
        </div>
      </PageHeader>

      <Feedback ok={message} error={error} />

      {/* PLAN CARDS VIEW */}
      {viewMode === 'cards' && (
        <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
          {packages.map((pkg) => {
            const count = tenantCounts[pkg.id] || 0
            return (
              <div
                key={pkg.id}
                className="flex flex-col justify-between overflow-hidden rounded-3xl border border-border bg-card p-6 shadow-sm transition-all hover:shadow-md"
              >
                <div className="space-y-4">
                  <div className="flex items-start justify-between">
                    <div>
                      <div className="flex items-center gap-2">
                        <h3 className="text-lg font-bold text-foreground">{pkg.name}</h3>
                        {!pkg.is_active && (
                          <span className="rounded bg-muted px-2 py-0.5 text-[10px] uppercase font-semibold text-muted-foreground">
                            Archived
                          </span>
                        )}
                      </div>
                      <p className="font-mono text-xs text-muted-foreground">{pkg.slug}</p>
                    </div>

                    <div className="flex items-center gap-1 rounded-full border border-border bg-muted/40 px-2.5 py-1 text-xs">
                      <Users className="size-3 text-muted-foreground" />
                      <span className="font-semibold text-foreground">{count}</span>
                      <span className="text-muted-foreground">workspaces</span>
                    </div>
                  </div>

                  <div className="rounded-2xl border border-border bg-muted/20 p-4">
                    <p className="text-2xl font-bold text-foreground">
                      {pkg.price_paise > 0 ? formatPaise(pkg.price_paise) : 'Free'}
                      <span className="text-xs font-normal text-muted-foreground">
                        {' '}
                        / {pkg.duration_days} days
                      </span>
                    </p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      Monthly: {formatPaise(pkg.monthly_price_paise)} • Yearly:{' '}
                      {formatPaise(pkg.yearly_price_paise)}
                    </p>
                  </div>

                  <div className="space-y-2 text-xs">
                    <span className="font-semibold uppercase tracking-wider text-muted-foreground text-[10px]">
                      Hardware & Media Quotas
                    </span>
                    <ul className="space-y-1.5 text-muted-foreground">
                      <li className="flex justify-between">
                        <span>TV screens limit:</span>
                        <span className="font-semibold text-foreground">
                          {pkg.max_screens == null ? 'Unlimited (∞)' : pkg.max_screens}
                        </span>
                      </li>
                      <li className="flex justify-between">
                        <span>Ad placements:</span>
                        <span className="font-semibold text-foreground">
                          {pkg.max_ad_slots === 0 ? 'Unlimited (∞)' : pkg.max_ad_slots}
                        </span>
                      </li>
                      <li className="flex justify-between">
                        <span>Cloud storage quota:</span>
                        <span className="font-semibold text-foreground">
                          {pkg.max_storage_bytes === 0 ? 'Unlimited' : formatBytes(pkg.max_storage_bytes)}
                        </span>
                      </li>
                    </ul>
                  </div>

                  <div className="space-y-2 text-xs">
                    <span className="font-semibold uppercase tracking-wider text-muted-foreground text-[10px]">
                      Features Included
                    </span>
                    <div className="flex flex-wrap gap-1">
                      {Object.entries(pkg.feature_flags || {})
                        .filter(([, v]) => v)
                        .map(([k]) => (
                          <span
                            key={k}
                            className="rounded-md border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 text-[11px] font-medium text-emerald-800 dark:text-emerald-300"
                          >
                            {k.replace(/_/g, ' ')}
                          </span>
                        ))}
                    </div>
                  </div>
                </div>

                <div className="mt-6 flex items-center justify-between border-t border-border pt-4">
                  <div className="flex gap-2">
                    <button
                      onClick={() => openEditDrawer(pkg)}
                      className="flex items-center gap-1.5 rounded-xl border border-border bg-background px-3 py-1.5 text-xs font-semibold text-foreground hover:bg-muted"
                    >
                      <Edit2 className="size-3.5" />
                      Edit Package
                    </button>
                    <button
                      onClick={() => setPreviewPlan(pkg)}
                      className="flex items-center gap-1.5 rounded-xl border border-border bg-background px-3 py-1.5 text-xs text-muted-foreground hover:text-foreground hover:bg-muted"
                      title="Preview how customer sees this plan on storefront"
                    >
                      <Eye className="size-3.5" />
                      Preview
                    </button>
                  </div>

                  <button
                    onClick={async () => {
                      const res = await confirm({
                        title: `Delete or retire ${pkg.name}?`,
                        description:
                          'If workspaces are currently subscribed to this package, it is safely retired (deactivated) so live operations remain intact.',
                        level: 2,
                        destructive: true,
                        confirmLabel: 'Retire package',
                      })
                      if (res) remove.mutate(pkg.id)
                    }}
                    className="grid size-8 place-items-center rounded-xl border border-border text-muted-foreground hover:border-rose-500/30 hover:bg-rose-500/10 hover:text-rose-600"
                    title="Retire package"
                  >
                    <Trash2 className="size-3.5" />
                  </button>
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* MATRIX VIEW */}
      {viewMode === 'matrix' && (
        <section className="overflow-x-auto rounded-3xl border border-border bg-card p-6">
          <table className="w-full min-w-[700px] text-sm">
            <thead>
              <tr className="border-b border-border text-left">
                <th className="p-3 text-xs font-semibold uppercase text-muted-foreground">
                  Capability / Feature
                </th>
                {packages.map((pkg) => (
                  <th key={pkg.id} className="p-3 text-center">
                    <p className="font-bold text-foreground">{pkg.name}</p>
                    <p className="text-xs text-muted-foreground">
                      {pkg.price_paise > 0 ? formatPaise(pkg.price_paise) : 'Free'}
                    </p>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-border text-xs">
              <tr>
                <td className="p-3 font-medium text-foreground">Screen Cap</td>
                {packages.map((pkg) => (
                  <td key={pkg.id} className="p-3 text-center font-mono">
                    {pkg.max_screens == null ? '∞' : pkg.max_screens}
                  </td>
                ))}
              </tr>
              <tr>
                <td className="p-3 font-medium text-foreground">Ad Placements</td>
                {packages.map((pkg) => (
                  <td key={pkg.id} className="p-3 text-center font-mono">
                    {pkg.max_ad_slots === 0 ? '∞' : pkg.max_ad_slots}
                  </td>
                ))}
              </tr>
              <tr>
                <td className="p-3 font-medium text-foreground">Storage Limit</td>
                {packages.map((pkg) => (
                  <td key={pkg.id} className="p-3 text-center font-mono">
                    {pkg.max_storage_bytes === 0 ? '∞' : formatBytes(pkg.max_storage_bytes)}
                  </td>
                ))}
              </tr>

              {canonicalFeatures.map((feat) => (
                <tr key={feat.key}>
                  <td className="p-3">
                    <p className="font-medium text-foreground">{feat.label}</p>
                    <p className="text-[11px] text-muted-foreground">{feat.description}</p>
                  </td>
                  {packages.map((pkg) => {
                    const hasFeat = Boolean(pkg.feature_flags?.[feat.key])
                    return (
                      <td key={pkg.id} className="p-3 text-center">
                        {hasFeat ? (
                          <Check className="mx-auto size-4 text-emerald-500" />
                        ) : (
                          <X className="mx-auto size-4 text-muted-foreground/30" />
                        )}
                      </td>
                    )
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {/* PLAN EDITOR DRAWER */}
      {(editingPlan || isCreating) && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-end bg-black/60 backdrop-blur-sm"
          onClick={() => {
            setEditingPlan(null)
            setIsCreating(false)
          }}
        >
          <div
            className="h-full w-full max-w-xl space-y-6 overflow-y-auto border-l border-border bg-card p-6 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between border-b border-border pb-4">
              <div>
                <h3 className="text-lg font-bold text-foreground">
                  {isCreating ? 'Create Package' : `Edit ${editingPlan?.name}`}
                </h3>
                <p className="text-xs text-muted-foreground">
                  {isCreating ? 'New commercial tier' : `Slug: ${editingPlan?.slug}`}
                </p>
              </div>
              <button
                onClick={() => {
                  setEditingPlan(null)
                  setIsCreating(false)
                }}
                className="grid size-8 place-items-center rounded-lg border border-border hover:bg-muted"
              >
                <X className="size-4" />
              </button>
            </div>

            <div className="space-y-4 text-sm">
              <div className="grid gap-3 sm:grid-cols-2">
                <label className="block">
                  <span className="mb-1 block text-xs font-semibold text-muted-foreground">
                    Package Name
                  </span>
                  <input
                    type="text"
                    value={planForm.name}
                    onChange={(e) => setPlanForm({ ...planForm, name: e.target.value })}
                    className="w-full rounded-xl border border-border bg-muted px-3 py-2 text-sm text-foreground outline-none"
                    placeholder="e.g. Enterprise Pro"
                  />
                </label>

                <label className="block">
                  <span className="mb-1 block text-xs font-semibold text-muted-foreground">
                    Slug ID
                  </span>
                  <input
                    type="text"
                    value={planForm.slug}
                    disabled={!isCreating}
                    onChange={(e) => setPlanForm({ ...planForm, slug: e.target.value })}
                    className="w-full rounded-xl border border-border bg-muted px-3 py-2 text-sm text-foreground outline-none disabled:opacity-60"
                    placeholder="e.g. enterprise-pro"
                  />
                </label>
              </div>

              {/* Pricing */}
              <div className="rounded-2xl border border-border bg-muted/20 p-4 space-y-3">
                <span className="text-xs font-bold uppercase tracking-wider text-muted-foreground">
                  Pricing (in Paise: ₹1 = 100 paise)
                </span>
                <div className="grid gap-3 sm:grid-cols-3">
                  <label className="block">
                    <span className="mb-1 block text-xs text-muted-foreground">Storefront Price</span>
                    <input
                      type="number"
                      value={planForm.price_paise}
                      onChange={(e) =>
                        setPlanForm({ ...planForm, price_paise: Number(e.target.value) || 0 })
                      }
                      className="w-full rounded-xl border border-border bg-card px-3 py-1.5 text-xs text-foreground outline-none"
                    />
                  </label>
                  <label className="block">
                    <span className="mb-1 block text-xs text-muted-foreground">Monthly Rate</span>
                    <input
                      type="number"
                      value={planForm.monthly_price_paise}
                      onChange={(e) =>
                        setPlanForm({
                          ...planForm,
                          monthly_price_paise: Number(e.target.value) || 0,
                        })
                      }
                      className="w-full rounded-xl border border-border bg-card px-3 py-1.5 text-xs text-foreground outline-none"
                    />
                  </label>
                  <label className="block">
                    <span className="mb-1 block text-xs text-muted-foreground">Yearly Rate</span>
                    <input
                      type="number"
                      value={planForm.yearly_price_paise}
                      onChange={(e) =>
                        setPlanForm({
                          ...planForm,
                          yearly_price_paise: Number(e.target.value) || 0,
                        })
                      }
                      className="w-full rounded-xl border border-border bg-card px-3 py-1.5 text-xs text-foreground outline-none"
                    />
                  </label>
                </div>
              </div>

              {/* Limits */}
              <div className="rounded-2xl border border-border bg-muted/20 p-4 space-y-3">
                <span className="text-xs font-bold uppercase tracking-wider text-muted-foreground">
                  Hardware & Placement Limits (-1 = Unlimited)
                </span>
                <div className="grid gap-3 sm:grid-cols-2">
                  <LimitInput
                    label="Max TV Screens"
                    value={planForm.max_screens == null ? UNLIMITED : planForm.max_screens}
                    onChange={(val) => setPlanForm({ ...planForm, max_screens: val })}
                  />
                  <LimitInput
                    label="Max Ad Placements"
                    value={planForm.max_ad_slots}
                    onChange={(val) => setPlanForm({ ...planForm, max_ad_slots: val })}
                  />
                  <LimitInput
                    label="Max Advertisers"
                    value={planForm.max_clients}
                    onChange={(val) => setPlanForm({ ...planForm, max_clients: val })}
                  />
                  <LimitInput
                    label="Cloud Storage (GiB)"
                    value={planForm.max_storage_bytes}
                    scale={GIB}
                    onChange={(val) => setPlanForm({ ...planForm, max_storage_bytes: val })}
                  />
                </div>
              </div>

              {/* Features by Category */}
              <div className="space-y-3">
                <span className="text-xs font-bold uppercase tracking-wider text-muted-foreground">
                  Feature Flags
                </span>
                <div className="space-y-4">
                  {Object.entries(featureCategories).map(([category, items]) => (
                    <div key={category} className="rounded-2xl border border-border p-4 space-y-2">
                      <h4 className="text-xs font-bold text-foreground">{category}</h4>
                      <div className="grid gap-2 sm:grid-cols-2">
                        {items.map((item) => {
                          const checked = Boolean(planForm.feature_flags?.[item.key])
                          return (
                            <label
                              key={item.key}
                              className={`flex items-start gap-2.5 rounded-xl border p-2.5 text-xs cursor-pointer transition-colors ${
                                checked
                                  ? 'border-emerald-500/30 bg-emerald-500/10'
                                  : 'border-border bg-card hover:bg-muted/40'
                              }`}
                            >
                              <input
                                type="checkbox"
                                checked={checked}
                                onChange={(e) => {
                                  setPlanForm({
                                    ...planForm,
                                    feature_flags: {
                                      ...planForm.feature_flags,
                                      [item.key]: e.target.checked,
                                    },
                                  })
                                }}
                                className="mt-0.5 size-4 rounded border-border"
                              />
                              <div>
                                <p className="font-semibold text-foreground">{item.label}</p>
                                <p className="text-[11px] text-muted-foreground">
                                  {item.description}
                                </p>
                              </div>
                            </label>
                          )
                        })}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            <div className="flex gap-2 border-t border-border pt-4">
              <button
                onClick={handleSavePlan}
                disabled={create.isPending || update.isPending}
                className="flex-1 rounded-xl bg-primary py-2.5 text-sm font-semibold text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
              >
                {create.isPending || update.isPending ? 'Saving…' : 'Save Package'}
              </button>
              <button
                onClick={() => {
                  setEditingPlan(null)
                  setIsCreating(false)
                }}
                className="rounded-xl border border-border bg-muted px-4 py-2.5 text-sm text-muted-foreground hover:bg-muted/80"
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}

      {/* STOREFRONT PREVIEW MODAL */}
      {previewPlan && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm"
          onClick={() => setPreviewPlan(null)}
        >
          <div
            className="w-full max-w-sm rounded-3xl border border-border bg-card p-6 shadow-2xl space-y-4"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between">
              <h3 className="font-bold text-foreground">Customer Storefront Preview</h3>
              <button
                onClick={() => setPreviewPlan(null)}
                className="grid size-7 place-items-center rounded-lg border border-border hover:bg-muted"
              >
                <X className="size-4" />
              </button>
            </div>

            <div className="rounded-2xl border-2 border-primary/40 bg-muted/20 p-5 space-y-4 text-center">
              <h4 className="text-xl font-bold text-foreground">{previewPlan.name}</h4>
              <p className="text-3xl font-extrabold text-foreground">
                {previewPlan.price_paise > 0 ? formatPaise(previewPlan.price_paise) : 'Free'}
                <span className="text-xs font-normal text-muted-foreground">
                  {' '}
                  / {previewPlan.duration_days} days
                </span>
              </p>

              <ul className="space-y-2 text-xs text-left border-t border-border pt-3">
                <li className="flex items-center gap-2">
                  <Check className="size-3.5 text-emerald-500" />
                  <span>
                    Up to{' '}
                    <strong>
                      {previewPlan.max_screens == null ? 'Unlimited' : previewPlan.max_screens}
                    </strong>{' '}
                    TV screens
                  </span>
                </li>
                <li className="flex items-center gap-2">
                  <Check className="size-3.5 text-emerald-500" />
                  <span>
                    <strong>
                      {previewPlan.max_ad_slots === 0 ? 'Unlimited' : previewPlan.max_ad_slots}
                    </strong>{' '}
                    Advertiser slots
                  </span>
                </li>
                <li className="flex items-center gap-2">
                  <Check className="size-3.5 text-emerald-500" />
                  <span>
                    <strong>
                      {previewPlan.max_storage_bytes === 0
                        ? 'Unlimited'
                        : formatBytes(previewPlan.max_storage_bytes)}
                    </strong>{' '}
                    Media storage
                  </span>
                </li>
                {Object.entries(previewPlan.feature_flags || {})
                  .filter(([, v]) => v)
                  .map(([k]) => (
                    <li key={k} className="flex items-center gap-2">
                      <Check className="size-3.5 text-emerald-500" />
                      <span className="capitalize">{k.replace(/_/g, ' ')} included</span>
                    </li>
                  ))}
              </ul>

              <button className="w-full rounded-xl bg-primary py-2.5 text-sm font-semibold text-primary-foreground shadow-sm">
                Get Started
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
