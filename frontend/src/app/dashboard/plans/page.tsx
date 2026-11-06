'use client'

import { useState } from 'react'
import Link from 'next/link'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CalendarRange, MapPin, Plus, Tags, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { EmptyState } from '@/components/dashboard/empty-state'
import { ErrorState } from '@/components/dashboard/error-state'
import { PageHeader } from '@/components/dashboard/page-header'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { api } from '@/lib/api'
import { canEditTenantContent } from '@/lib/roles'
import { useAuthStore } from '@/lib/store'
import { usePlanFeatures } from '@/lib/use-plan-features'
import type { TenantPlan } from '@/lib/types'
import { rupees } from '@/lib/format'


const BLANK = {
  name: '', description: '', duration_days: '30',
  max_locations: '5', ad_slots: '1', price: '', support_tier: 'Basic Support',
}

/**
 * The packages this workspace sells to its own clients.
 *
 * Not the plan MR Signage bills you on -- that lives under Billing. A booking made against one
 * of these COPIES its price and duration at the moment it is sold, so changing a price
 * here never restates an invoice a client has already been given.
 */
export default function PlansPage() {
  const queryClient = useQueryClient()
  const user = useAuthStore((state) => state.user)
  const canEdit = canEditTenantContent(user)
  const { hasCommercial } = usePlanFeatures()

  const plansQuery = useQuery({ queryKey: ['tenant-plans'], queryFn: () => api.getTenantPlans(true) })

  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<TenantPlan | null>(null)
  const [form, setForm] = useState({ ...BLANK })

  const set = (key: keyof typeof BLANK, value: string) => setForm((current) => ({ ...current, [key]: value }))
  const reset = () => { setEditing(null); setForm({ ...BLANK }) }
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['tenant-plans'] })
  const fail = (error: Error) => toast.error(error.message)

  const save = useMutation({
    mutationFn: () => {
      const payload = {
        name: form.name.trim(),
        description: form.description.trim() || null,
        duration_days: Number(form.duration_days) || 30,
        max_locations: Number(form.max_locations) || 1,
        ad_slots: Number(form.ad_slots) || 1,
        // Rupees in the box, paise on the wire -- money never rides on a float.
        price_paise: Math.round(Number(form.price || 0) * 100),
        support_tier: form.support_tier.trim() || 'Basic Support',
      }
      return editing ? api.updateTenantPlan(editing.id, payload) : api.createTenantPlan(payload)
    },
    onSuccess: () => { refresh(); toast.success(editing ? 'Plan updated' : 'Plan created'); setOpen(false); reset() },
    onError: fail,
  })

  const remove = useMutation({
    mutationFn: (id: number) => api.deleteTenantPlan(id),
    onSuccess: (result) => {
      refresh()
      // A plan that has been sold is retired, not destroyed, or the report loses the plan
      // name it prints for those bookings. Say which happened.
      toast.success(result.status === 'retired'
        ? `Plan retired — ${result.bookings} booking(s) still reference it.`
        : 'Plan deleted')
    },
    onError: fail,
  })

  if (!hasCommercial) {
    return (
      <div className="space-y-6">
        <PageHeader
          eyebrow="Commercial"
          title="Advertising Packages"
          description="Standard rate cards for campaigns across your screen network."
        />
        <EmptyState
          icon={Tags}
          title="Custom plans not available on your plan"
          description="Creating and selling ad packages requires a commercial plan tier with Ad Booking & Scheduling enabled. Contact your administrator or upgrade your subscription."
          action={
            <Button variant="outline" render={<Link href="/dashboard/billing" />}>
              View Billing &amp; Plans
            </Button>
          }
        />
      </div>
    )
  }

  if (plansQuery.isError) {
    return <ErrorState message="Plans could not be loaded." onRetry={() => plansQuery.refetch()} />
  }
  const plans = plansQuery.data || []

  const openFor = (plan: TenantPlan | null) => {
    if (plan) {
      setEditing(plan)
      setForm({
        name: plan.name,
        description: plan.description || '',
        duration_days: String(plan.duration_days),
        max_locations: String(plan.max_locations),
        ad_slots: String(plan.ad_slots),
        price: String(plan.price_paise / 100),
        support_tier: plan.support_tier,
      })
    } else {
      reset()
    }
    setOpen(true)
  }

  return (
    <div className="space-y-8">
      <PageHeader
        eyebrow="Advertising"
        title="Plans"
        description="The packages you sell to your clients. Booking an advert on a plan fills in its price and end date, and names it on the client's report."
        actions={canEdit ? (
          <Dialog open={open} onOpenChange={(next) => { setOpen(next); if (!next) reset() }}>
            <DialogTrigger render={<Button onClick={() => openFor(null)} />}>
              <Plus data-icon="inline-start" /> New plan
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>{editing ? 'Edit plan' : 'New plan'}</DialogTitle>
                <DialogDescription>
                  Bookings copy these terms when they are sold, so editing a plan never changes a campaign already running on it.
                </DialogDescription>
              </DialogHeader>
              <form onSubmit={(event) => { event.preventDefault(); save.mutate() }} className="space-y-4 pt-2">
                <div className="space-y-2">
                  <Label htmlFor="plan-name">Plan name</Label>
                  <Input id="plan-name" value={form.name} onChange={(event) => set('name', event.target.value)} placeholder="Standard Plan" required />
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-2">
                    <Label htmlFor="plan-price">Price (₹)</Label>
                    <Input id="plan-price" type="number" min="0" step="0.01" value={form.price} onChange={(event) => set('price', event.target.value)} placeholder="25000" required />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="plan-days">Duration (days)</Label>
                    <Input id="plan-days" type="number" min="1" value={form.duration_days} onChange={(event) => set('duration_days', event.target.value)} required />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="plan-locations">Locations</Label>
                    <Input id="plan-locations" type="number" min="1" value={form.max_locations} onChange={(event) => set('max_locations', event.target.value)} required />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="plan-slots">Ad slots</Label>
                    <Input id="plan-slots" type="number" min="1" value={form.ad_slots} onChange={(event) => set('ad_slots', event.target.value)} required />
                  </div>
                </div>
                <div className="space-y-2">
                  <Label htmlFor="plan-support">Support tier</Label>
                  <Input id="plan-support" value={form.support_tier} onChange={(event) => set('support_tier', event.target.value)} placeholder="Basic Support" />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="plan-description">Description</Label>
                  <Input id="plan-description" value={form.description} onChange={(event) => set('description', event.target.value)} placeholder="5 Locations | 1 Ad Slot | Basic Support" />
                </div>
                <div className="flex justify-end gap-2 pt-2">
                  <Button type="button" variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
                  <Button type="submit" disabled={save.isPending}>{editing ? 'Save' : 'Create plan'}</Button>
                </div>
              </form>
            </DialogContent>
          </Dialog>
        ) : undefined}
      />

      {plansQuery.isLoading ? (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 3 }).map((_, index) => <Skeleton key={index} className="h-40" />)}
        </div>
      ) : !plans.length ? (
        <EmptyState
          icon={Tags}
          title="No plans yet"
          description="Create the packages you sell — a price, how long it runs for, and how many locations it covers."
          action={canEdit ? <Button onClick={() => openFor(null)}>New plan</Button> : undefined}
        />
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          {plans.map((plan) => (
            <Card key={plan.id} className={`ring-hairline bg-card border-0 ring-1 ${plan.is_active ? '' : 'opacity-60'}`}>
              <CardContent className="p-5">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h3 className="text-foreground truncate font-semibold">{plan.name}</h3>
                    <p className="text-foreground mt-1 text-lg font-semibold">
                      {rupees(plan.price_paise)}
                      <span className="text-muted-foreground text-sm font-normal"> / {plan.duration_days} days</span>
                    </p>
                  </div>
                  {!plan.is_active && <Badge variant="outline">Retired</Badge>}
                </div>

                <div className="text-muted-foreground mt-3 space-y-1.5 text-sm">
                  <p className="flex items-center gap-2">
                    <MapPin className="size-3.5 shrink-0" aria-hidden="true" />
                    {plan.max_locations} location{plan.max_locations === 1 ? '' : 's'} · {plan.ad_slots} ad slot{plan.ad_slots === 1 ? '' : 's'}
                  </p>
                  <p className="flex items-center gap-2">
                    <CalendarRange className="size-3.5 shrink-0" aria-hidden="true" />
                    {plan.support_tier}
                  </p>
                  {plan.description && <p className="pt-1">{plan.description}</p>}
                </div>

                {canEdit && (
                  <div className="mt-4 flex items-center gap-2">
                    <Button size="sm" variant="outline" onClick={() => openFor(plan)}>Edit</Button>
                    {plan.is_active && (
                      <Button
                        size="sm"
                        variant="ghost"
                        className="text-destructive"
                        onClick={() => remove.mutate(plan.id)}
                        aria-label={`Remove ${plan.name}`}
                      >
                        <Trash2 />
                      </Button>
                    )}
                  </div>
                )}
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  )
}
