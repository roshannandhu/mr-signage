'use client'

import { useCallback, useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, LogOut, RadioTower, RefreshCw, Sparkles } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { api } from '@/lib/api'
import { useAuthStore } from '@/lib/store'
import { rupees } from '@/lib/format'
import { loadRazorpay } from '@/lib/razorpay'
import type { CustomPlanRequestItem, PurchaseResponse } from '@/lib/types'

const GIB = 1024 ** 3
const gb = (bytes: number) => `${Math.round(bytes / GIB)} GB`

const FEATURES: { key: string; label: string }[] = [
  { key: 'scheduling', label: 'Scheduling' },
  { key: 'transitions', label: 'Transitions' },
  { key: 'payslips', label: 'Payslips & payments' },
  { key: 'ads_counting', label: 'Ads counting reports' },
  { key: 'pdf_reports', label: 'PDF playback reports' },
  { key: 'remote_control', label: 'Remote control & alerts' },
  { key: 'kiosk_lock', label: 'TV lock' },
  { key: 'emergency_alert', label: 'Emergency alert' },
  { key: 'priority_support', label: 'Priority support' },
]

const CUSTOM_BLANK = { screens: '20', clients: '20', storage: '50', days: '30', notes: '' }

/**
 * The storefront a new workspace lands on. It is blocked from the dashboard until it pays --
 * paying is what flips the org to `active` (backend billing.purchase / webhook). The page
 * also polls, so a workspace let in by the platform operator (the manual-approval fallback),
 * or a custom request approved and priced while it sits here, is carried through without a
 * reload.
 */
export default function ActivateWorkspacePage() {
  const router = useRouter()
  const queryClient = useQueryClient()
  const { user, token, setSession, clearSession } = useAuthStore()
  const [busyKey, setBusyKey] = useState<string | null>(null)
  const [showCustom, setShowCustom] = useState(false)
  const [custom, setCustom] = useState({ ...CUSTOM_BLANK })
  const [customFeatures, setCustomFeatures] = useState<Record<string, boolean>>({})
  const [highlightPlanId, setHighlightPlanId] = useState<number | null>(null)
  const isOwner = user?.role === 'owner'

  const plansQuery = useQuery({ queryKey: ['storefront-plans'], queryFn: api.getPlans })
  const requestsQuery = useQuery({
    queryKey: ['my-custom-requests'],
    queryFn: api.getMyCustomRequests,
    // Poll so a request the operator prices while the tenant waits becomes payable here.
    refetchInterval: 8000,
  })

  const enterIfActive = useCallback(async () => {
    if (!token) return false
    try {
      const me = await api.me()
      if (me.organization_status === 'active') {
        setSession(token, me)
        // A full-document navigation, NOT router.replace: the app wraps every page in
        // <ViewTransition>, and a client transition into a freshly-activated dashboard was
        // aborting on the browser's DOM-update timeout and leaving a blank page. A hard load
        // sidesteps the transition entirely and enters the dashboard with the fresh session
        // already in localStorage.
        window.location.assign('/dashboard/screens')
        return true
      }
    } catch {
      /* transient — the interval retries */
    }
    return false
  }, [token, setSession])

  useEffect(() => {
    if (!token) {
      router.replace('/login')
      return
    }
    enterIfActive()
    const interval = setInterval(enterIfActive, 5000)
    return () => clearInterval(interval)
  }, [token, router, enterIfActive])

  // One purchase path for both standard packages and priced custom requests: they both hand
  // back a PurchaseResponse, and the provider branch is identical.
  const runPurchase = async (key: string, label: string, start: () => Promise<PurchaseResponse>) => {
    if (!isOwner) return
    setBusyKey(key)
    try {
      const res = await start()
      if (res.provider === 'internal') {
        await enterIfActive()
      } else if (res.provider === 'mock') {
        await api.mockConfirmOrder(res.order_id as string)
        await enterIfActive()
      } else if (res.provider === 'razorpay') {
        const Razorpay = await loadRazorpay()
        new Razorpay({
          key: res.key_id,
          order_id: res.order_id,
          amount: res.amount_paise,
          currency: 'INR',
          name: 'MR Signage',
          description: label,
          handler: () => { void enterIfActive() },
          modal: { ondismiss: () => setBusyKey(null) },
        }).open()
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Could not start checkout')
    } finally {
      setBusyKey(null)
    }
  }

  // A plan chosen on /signup before any account existed, carried here through Google's OAuth
  // state and /login (see that page's "signup_plan:" prefix). Runs once the real plan list has
  // loaded, so an id that does not exist on THIS deployment is silently ignored rather than
  // matched against nothing. The query param is stripped immediately either way, so refreshing
  // or revisiting this page never re-fires it.
  useEffect(() => {
    if (plansQuery.isLoading) return
    const params = new URLSearchParams(window.location.search)
    const intent = params.get('plan')
    if (!intent) return
    window.history.replaceState({}, '', window.location.pathname)
    if (intent === 'custom') {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- reads window.location + rewrites history, same as /login's callback handler
      setShowCustom(true)
      return
    }
    const plan = (plansQuery.data ?? []).find((p) => String(p.id) === intent)
    if (!plan) return
    if (plan.price_paise <= 0) {
      // Free is meant to start straight away -- no second click to confirm what signup
      // already asked for.
      void runPurchase(`plan-${plan.id}`, plan.name, () => api.purchasePlan(plan.id))
    } else {
      // A paid plan is not charged without an explicit click here; just point at it.
      setHighlightPlanId(plan.id)
      document.getElementById(`plan-card-${plan.id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- runs once the plan list settles, not on every render of runPurchase
  }, [plansQuery.isLoading, plansQuery.data])

  const submitCustom = async () => {
    if (!isOwner) return
    setBusyKey('custom-submit')
    try {
      await api.submitCustomRequest({
        // At least one: a request for zero TVs is not a request, and the server would price a
        // package with no screens.
        max_screens: Math.max(1, Number(custom.screens) || 1),
        max_clients: Number(custom.clients) || 0,
        max_ad_slots: 0,
        max_storage_bytes: (Number(custom.storage) || 0) * GIB,
        duration_days: Number(custom.days) || 30,
        feature_flags: customFeatures,
        notes: custom.notes.trim() || null,
      })
      toast.success('Request sent. A manager will price it shortly — this page updates when they do.')
      setShowCustom(false)
      setCustom({ ...CUSTOM_BLANK })
      setCustomFeatures({})
      queryClient.invalidateQueries({ queryKey: ['my-custom-requests'] })
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Could not send request')
    } finally {
      setBusyKey(null)
    }
  }

  const logout = () => { clearSession(); router.replace('/login') }
  const plans = plansQuery.data ?? []
  const requests = (requestsQuery.data ?? []).filter((r) => r.status !== 'paid')

  return (
    <main className="relative min-h-screen bg-background px-6 py-12 text-foreground">
      <div className="pointer-events-none absolute -left-32 -top-32 size-[500px] rounded-full bg-emerald-500/10 blur-[140px]" />
      <div className="pointer-events-none absolute -bottom-32 -right-32 size-[500px] rounded-full bg-teal-500/10 blur-[140px]" />

      <div className="relative mx-auto w-full max-w-6xl">
        <header className="mb-10 flex items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="grid size-10 place-items-center rounded-xl bg-gradient-to-tr from-emerald-500 to-teal-400 text-black shadow-lg shadow-emerald-500/20">
              <RadioTower className="size-5" />
            </div>
            <div>
              <p className="text-lg font-bold tracking-wider">MR <span className="font-medium text-emerald-400">SIGNAGE</span></p>
              <p className="text-[10px] font-semibold text-emerald-400 tracking-wider">(Midhun-Roshan)</p>
            </div>
          </div>
          <div className="flex items-center gap-4">
            <span className="hidden items-center gap-1.5 text-xs text-muted-foreground sm:flex">
              <RefreshCw className="size-3 text-emerald-400" /> Activates the moment you pay
            </span>
            <Button onClick={logout} variant="outline" className="h-10 border-border bg-muted text-foreground hover:bg-muted">
              <LogOut className="mr-2 size-4" /> Sign out
            </Button>
          </div>
        </header>

        <div className="mb-10 max-w-2xl">
          <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">Choose a plan to activate your workspace</h1>
          <p className="mt-3 text-sm leading-relaxed text-muted-foreground">
            Welcome <span className="font-semibold text-foreground">{user?.full_name || user?.username}</span>. Your workspace{' '}
            <span className="font-semibold text-emerald-400">{user?.organization_name}</span> is ready — pick a package to unlock the
            dashboard. Each is a one-time charge for its access period; your TVs play the demo reel until you activate.
            {!isOwner && <span className="mt-2 block text-amber-300/80">Only the workspace owner can purchase a plan.</span>}
          </p>
        </div>

        {/* Priced / pending custom requests */}
        {requests.length > 0 && (
          <div className="mb-8 space-y-3">
            {requests.map((request) => (
              <CustomRequestRow
                key={request.id}
                request={request}
                canPay={isOwner && request.status === 'priced'}
                busy={busyKey === `custom-${request.id}`}
                onPay={() => runPurchase(`custom-${request.id}`, 'Custom plan', () => api.purchaseCustomRequest(request.id))}
              />
            ))}
          </div>
        )}

        {/* Without this a failed request left `plans` empty and the grid showed only the Custom
            card, as if the platform simply sold nothing else. */}
        {plansQuery.isError && (
          <div role="alert" className="mb-5 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm">
            <span>The plans could not be loaded: {plansQuery.error.message}</span>
            <Button size="sm" variant="outline" onClick={() => plansQuery.refetch()}>
              <RefreshCw data-icon="inline-start" /> Try again
            </Button>
          </div>
        )}

        {plansQuery.isLoading ? (
          <div className="grid grid-cols-1 gap-5 md:grid-cols-3">
            {[0, 1, 2].map((i) => <div key={i} className="h-80 animate-pulse rounded-3xl bg-muted" />)}
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-5 md:grid-cols-3">
            {plans.map((plan) => {
              const features = Object.entries(plan.feature_flags).filter(([, on]) => on).map(([f]) => f)
              const free = plan.price_paise <= 0
              return (
                <div
                  key={plan.id}
                  id={`plan-card-${plan.id}`}
                  className={`flex flex-col rounded-3xl border bg-card p-6 shadow-xl backdrop-blur-xl transition-all ${
                    highlightPlanId === plan.id ? 'border-emerald-500 ring-2 ring-emerald-500/40' : 'border-border'
                  }`}
                >
                  <h3 className="text-lg font-semibold">{plan.name}</h3>
                  <p className="mt-3 text-3xl font-bold">
                    {free ? 'Free' : rupees(plan.price_paise)}
                    <span className="ml-1 text-sm font-normal text-muted-foreground">/ {plan.duration_days} days</span>
                  </p>
                  <ul className="mt-6 space-y-2.5 text-sm text-muted-foreground">
                    {/* null is unlimited; 0 is a package that grants none. This used to
                        read `max_screens > 0 ? n : 'Unlimited'`, which advertised a
                        zero-screen package as unlimited — the one case where the storefront
                        promised the exact opposite of what the quota would enforce. */}
                    <Line>
                      {plan.max_screens == null
                        ? 'Unlimited TVs'
                        : plan.max_screens > 0
                          ? `${plan.max_screens} TVs`
                          : 'No TVs included'}
                    </Line>
                    <Line>{plan.max_clients > 0 ? `${plan.max_clients} clients` : 'Unlimited clients'}</Line>
                    <Line>{plan.max_storage_bytes ? `${gb(plan.max_storage_bytes)} storage` : 'Unlimited storage'}</Line>
                    {features.map((f) => <Line key={f}>{f.replaceAll('_', ' ')}</Line>)}
                  </ul>
                  <Button
                    onClick={() => runPurchase(`plan-${plan.id}`, plan.name, () => api.purchasePlan(plan.id))}
                    disabled={!isOwner || busyKey !== null}
                    className="mt-6 h-11 w-full bg-emerald-500 font-semibold text-black hover:bg-emerald-400 disabled:opacity-50"
                  >
                    {busyKey === `plan-${plan.id}` ? 'Starting…' : free ? 'Activate free' : 'Choose plan'}
                  </Button>
                </div>
              )
            })}

            {/* Custom card */}
            <div className="flex flex-col rounded-3xl border border-dashed border-emerald-500/30 bg-card p-6">
              <div className="flex items-center gap-2">
                <Sparkles className="size-4 text-emerald-400" />
                <h3 className="text-lg font-semibold">Custom</h3>
              </div>
              <p className="mt-3 text-sm text-muted-foreground">
                Need a specific number of TVs, clients or storage, or a particular set of features? Tell us the shape and a
                manager prices it — you pay once and you are in.
              </p>
              <Button
                onClick={() => setShowCustom((v) => !v)}
                disabled={!isOwner}
                variant="outline"
                className="mt-auto h-11 w-full border-emerald-500/40 bg-emerald-500/10 font-semibold text-emerald-300 hover:bg-emerald-500/20 disabled:opacity-50"
              >
                {showCustom ? 'Close' : 'Request a custom plan'}
              </Button>
            </div>
          </div>
        )}

        {/* Custom request form */}
        {showCustom && isOwner && (
          <form
            onSubmit={(e) => { e.preventDefault(); submitCustom() }}
            className="mt-8 space-y-5 rounded-3xl border border-border bg-card p-6"
          >
            <h3 className="font-semibold">Describe your custom plan</h3>
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <NumField label="TVs" value={custom.screens} onChange={(v) => setCustom({ ...custom, screens: v })} min={1} />
              <NumField label="Clients" value={custom.clients} onChange={(v) => setCustom({ ...custom, clients: v })} />
              <NumField label="Storage (GB)" value={custom.storage} onChange={(v) => setCustom({ ...custom, storage: v })} />
              <NumField label="Access period (days)" value={custom.days} onChange={(v) => setCustom({ ...custom, days: v })} min={1} />
            </div>
            <div>
              <span className="mb-2 block text-xs font-semibold text-muted-foreground">Features</span>
              <div className="flex flex-wrap gap-4">
                {FEATURES.map((f) => (
                  <label key={f.key} className="flex items-center gap-2 text-sm text-muted-foreground">
                    <input
                      type="checkbox"
                      checked={Boolean(customFeatures[f.key])}
                      onChange={(e) => setCustomFeatures({ ...customFeatures, [f.key]: e.target.checked })}
                      className="size-4 accent-emerald-500"
                    />
                    {f.label}
                  </label>
                ))}
              </div>
            </div>
            <div>
              <span className="mb-2 block text-xs font-semibold text-muted-foreground">Anything else (optional)</span>
              <textarea
                value={custom.notes}
                onChange={(e) => setCustom({ ...custom, notes: e.target.value })}
                rows={2}
                className="w-full rounded-xl border border-border bg-muted px-3 py-2 text-sm text-foreground outline-none focus:border-emerald-500/50"
                placeholder="e.g. rollout across 3 airport terminals"
              />
            </div>
            <Button
              type="submit"
              disabled={busyKey === 'custom-submit'}
              className="h-11 bg-emerald-500 font-semibold text-black hover:bg-emerald-400 disabled:opacity-50"
            >
              {busyKey === 'custom-submit' ? 'Sending…' : 'Send request'}
            </Button>
          </form>
        )}
      </div>
    </main>
  )
}

function CustomRequestRow({ request, canPay, busy, onPay }: {
  request: CustomPlanRequestItem
  canPay: boolean
  busy: boolean
  onPay: () => void
}) {
  const shape = `${request.max_screens} TVs · ${request.max_clients || '∞'} clients · ${gb(request.max_storage_bytes)} · ${request.duration_days} days`
  return (
    <div className="flex flex-wrap items-center justify-between gap-4 rounded-2xl border border-border bg-card p-5">
      <div>
        <p className="text-sm font-semibold">Custom plan request</p>
        <p className="mt-0.5 text-xs text-muted-foreground">{shape}</p>
      </div>
      <div className="flex items-center gap-4">
        {request.status === 'priced' ? (
          <span className="text-lg font-bold">{rupees(request.price_paise)}</span>
        ) : (
          <span className="rounded-full border border-amber-500/30 bg-amber-500/10 px-3 py-1 text-xs text-amber-300">
            {request.status === 'rejected' ? 'Declined' : 'Awaiting price'}
          </span>
        )}
        {canPay && (
          <Button onClick={onPay} disabled={busy} className="h-10 bg-emerald-500 font-semibold text-black hover:bg-emerald-400 disabled:opacity-50">
            {busy ? 'Starting…' : 'Pay & activate'}
          </Button>
        )}
      </div>
    </div>
  )
}

function Line({ children }: { children: React.ReactNode }) {
  return (
    <li className="flex items-center gap-2 capitalize">
      <Check className="size-4 shrink-0 text-emerald-400" /> {children}
    </li>
  )
}

function NumField({ label, value, onChange, min = 0 }: { label: string; value: string; onChange: (v: string) => void; min?: number }) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-xs font-semibold text-muted-foreground">{label}</span>
      <input
        type="number"
        min={min}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-xl border border-border bg-muted px-3 py-2 text-sm text-foreground outline-none focus:border-emerald-500/50"
      />
    </label>
  )
}
