'use client'

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Check, CreditCard, HardDrive, MonitorPlay } from 'lucide-react'
import { toast } from 'sonner'
import { ErrorState } from '@/components/dashboard/error-state'
import { PageHeader } from '@/components/dashboard/page-header'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { api } from '@/lib/api'
import { loadRazorpay } from '@/lib/razorpay'
import { useAuthStore } from '@/lib/store'
import type { Plan, PurchaseResponse } from '@/lib/types'

const bytes = (value: number) => value >= 1024 ** 3 ? `${(value / 1024 ** 3).toFixed(1)} GB` : `${(value / 1024 ** 2).toFixed(1)} MB`
const percent = (used: number, limit: number) => Math.min(100, Math.round((used / Math.max(limit, 1)) * 100))

export default function BillingPage() {
  const queryClient = useQueryClient()
  const user = useAuthStore((state) => state.user)
  const [period, setPeriod] = useState<'monthly' | 'yearly'>('monthly')
  const summaryQuery = useQuery({ queryKey: ['billing-summary'], queryFn: api.getBillingSummary })
  const plansQuery = useQuery({ queryKey: ['billing-plans'], queryFn: api.getPlans })
  const checkout = useMutation({
    mutationFn: (plan: Plan) => api.createCheckout(plan.id, period),
    onSuccess: (session) => {
      if (session.provider === 'internal') {
        queryClient.invalidateQueries({ queryKey: ['billing-summary'] })
        toast.success('Plan updated')
      } else {
        window.location.assign(session.checkout_url)
      }
    },
    onError: (error: Error) => toast.error(error.message),
  })
  // Pay once for the plan's own fixed access period, as an alternative to the recurring
  // subscription above -- the same three provider branches the /signup storefront uses
  // (internal activates now, mock needs one more confirm call, razorpay opens its own modal),
  // since /purchase answers with the same shape in all three places.
  const [purchasingId, setPurchasingId] = useState<number | null>(null)
  const purchase = useMutation({
    mutationFn: (plan: Plan) => { setPurchasingId(plan.id); return api.purchasePlan(plan.id) },
    onSuccess: async (res: PurchaseResponse) => {
      if (res.provider === 'internal') {
        queryClient.invalidateQueries({ queryKey: ['billing-summary'] })
        toast.success('Plan updated')
        setPurchasingId(null)
      } else if (res.provider === 'mock') {
        await api.mockConfirmOrder(res.order_id as string)
        queryClient.invalidateQueries({ queryKey: ['billing-summary'] })
        toast.success('Plan updated')
        setPurchasingId(null)
      } else if (res.provider === 'razorpay') {
        const Razorpay = await loadRazorpay()
        new Razorpay({
          key: res.key_id,
          order_id: res.order_id,
          amount: res.amount_paise,
          currency: 'INR',
          name: 'MR Signage',
          handler: () => {
            queryClient.invalidateQueries({ queryKey: ['billing-summary'] })
            toast.success('Plan updated')
            setPurchasingId(null)
          },
          modal: { ondismiss: () => setPurchasingId(null) },
        }).open()
      }
    },
    onError: (error: Error) => { toast.error(error.message); setPurchasingId(null) },
  })

  if (summaryQuery.isError || plansQuery.isError) return <ErrorState message="Billing details could not be loaded." onRetry={() => { summaryQuery.refetch(); plansQuery.refetch() }} />
  if (summaryQuery.isLoading || plansQuery.isLoading || !summaryQuery.data) return <div className="space-y-6"><Skeleton className="h-28" /><Skeleton className="h-72" /></div>

  const summary = summaryQuery.data
  // A limit of "unlimited" is null for screens and 0 for storage; a usage bar against
  // either is meaningless, so there is nothing to fill.
  const unlimitedScreens = summary.plan.max_screens == null
  const unlimitedStorage = !summary.plan.max_storage_bytes
  const storagePercent = unlimitedStorage ? 0 : percent(summary.storage_used_bytes, summary.plan.max_storage_bytes)
  const screenPercent = unlimitedScreens ? 0 : percent(summary.screens_used, summary.plan.max_screens ?? 0)
  const statusTone: 'danger' | 'warning' | 'secondary' = summary.is_read_only ? 'danger' : summary.subscription.status === 'grace' ? 'warning' : 'secondary'

  return <div className="space-y-8">
    <PageHeader eyebrow="Account" title="Plan & billing" description="Track screen and storage usage, manage your plan, and keep billing status visible to account owners." />

    {(summary.subscription.status === 'grace' || summary.is_read_only) && <div className="flex gap-3 rounded-2xl border border-amber-500/30 bg-amber-500/10 p-4 text-sm text-amber-950 dark:text-amber-100"><AlertTriangle className="mt-0.5 size-5 shrink-0" /><div><p className="font-semibold">Billing needs attention</p><p className="mt-1 opacity-80">{summary.is_read_only ? 'The grace period has ended, so dashboard changes are read-only. Screens continue playing cached content.' : `Your grace period runs until ${summary.subscription.grace_period_end ? new Date(summary.subscription.grace_period_end).toLocaleString() : 'the recovery deadline'}. Playback remains uninterrupted.`}</p></div></div>}

    <Card className="border-0 ring-1 ring-hairline"><CardContent className="grid grid-cols-1 gap-6 p-6 lg:grid-cols-[1fr_1.4fr]">
      <div><div className="flex items-center gap-3"><span className="grid size-11 place-items-center rounded-xl bg-primary/10 text-primary"><CreditCard className="size-5" /></span><div><p className="text-sm text-muted-foreground">Current plan</p><div className="flex items-center gap-2"><h2 className="text-xl font-semibold">{summary.plan.name}</h2><Badge variant={statusTone}>{summary.is_read_only ? 'Read only' : summary.subscription.status}</Badge></div></div></div>{summary.subscription.current_period_end && <p className="mt-4 text-xs text-muted-foreground">Current period ends {new Date(summary.subscription.current_period_end).toLocaleDateString()}</p>}</div>
      <div className="grid grid-cols-1 gap-5 sm:grid-cols-2"><Usage icon={MonitorPlay} label="Screens" value={unlimitedScreens ? `${summary.screens_used} · no limit` : `${summary.screens_used} of ${summary.plan.max_screens}`} percent={screenPercent} /><Usage icon={HardDrive} label="Storage" value={unlimitedStorage ? `${bytes(summary.storage_used_bytes)} · no limit` : `${bytes(summary.storage_used_bytes)} of ${bytes(summary.plan.max_storage_bytes)}`} percent={storagePercent} /></div>
    </CardContent></Card>

    <section aria-labelledby="available-plans"><div className="mb-4 flex flex-wrap items-center justify-between gap-3"><div><h2 id="available-plans" className="font-semibold">Available plans</h2><p className="mt-1 text-sm text-muted-foreground">Limits increase as soon as a verified payment webhook arrives.</p></div><div className="flex rounded-xl bg-muted p-1"><Button size="sm" variant={period === 'monthly' ? 'default' : 'ghost'} onClick={() => setPeriod('monthly')}>Monthly</Button><Button size="sm" variant={period === 'yearly' ? 'default' : 'ghost'} onClick={() => setPeriod('yearly')}>Yearly</Button></div></div>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">{plansQuery.data?.map((plan) => { const current = plan.id === summary.plan.id; const price = period === 'monthly' ? plan.monthly_price_paise : plan.yearly_price_paise; return <Card key={plan.id} className={current ? 'border-primary/40 ring-1 ring-primary/20' : 'border-0 ring-1 ring-hairline'}><CardContent className="p-6"><div className="flex items-start justify-between gap-2"><div><h3 className="font-semibold">{plan.name}</h3><p className="mt-2 text-3xl font-bold">{price ? `₹${(price / 100).toLocaleString('en-IN')}` : 'Free'}</p><p className="text-xs text-muted-foreground">{price ? `per ${period === 'monthly' ? 'month' : 'year'}` : 'No card required'}</p></div>{current && <Badge>Current</Badge>}</div><ul className="mt-6 space-y-2 text-sm"><li className="flex gap-2"><Check className="size-4 text-emerald-600" /> {plan.max_screens == null ? 'Unlimited' : plan.max_screens} screens</li><li className="flex gap-2"><Check className="size-4 text-emerald-600" /> {plan.max_storage_bytes ? `${bytes(plan.max_storage_bytes)} storage` : 'Unlimited storage'}</li>{Object.entries(plan.feature_flags).filter(([, enabled]) => enabled).map(([feature]) => <li key={feature} className="flex gap-2 capitalize"><Check className="size-4 text-emerald-600" /> {feature.replaceAll('_', ' ')}</li>)}</ul>{current ? <Button className="mt-6 w-full" variant="outline" disabled>Current plan</Button> : plan.price_paise <= 0 && !plan.monthly_price_paise && !plan.yearly_price_paise ? <Button className="mt-6 w-full" disabled={user?.role !== 'owner' || purchase.isPending} onClick={() => purchase.mutate(plan)}>{purchasingId === plan.id ? 'Activating…' : 'Activate free'}</Button> : <div className="mt-6 space-y-2"><Button className="w-full" disabled={user?.role !== 'owner' || checkout.isPending} onClick={() => checkout.mutate(plan)}>{checkout.isPending ? 'Preparing…' : `Subscribe ${period}`}</Button><Button className="w-full" variant="outline" disabled={user?.role !== 'owner' || purchase.isPending} onClick={() => purchase.mutate(plan)}>{purchasingId === plan.id ? 'Preparing…' : `Pay once for ${plan.duration_days} days`}</Button></div>}</CardContent></Card> })}</div>
    </section>
  </div>
}

function Usage({ icon: Icon, label, value, percent: amount }: { icon: typeof MonitorPlay; label: string; value: string; percent: number }) {
  return <div><div className="flex items-center justify-between gap-3 text-sm"><span className="flex items-center gap-2 text-muted-foreground"><Icon className="size-4" /> {label}</span><span className="font-semibold text-foreground">{value}</span></div><div className="mt-3 h-2 overflow-hidden rounded-full bg-muted"><div className="h-full rounded-full bg-primary transition-[width]" style={{ width: `${amount}%` }} /></div><p className="mt-1 text-right text-[11px] text-muted-foreground">{amount}% used</p></div>
}
