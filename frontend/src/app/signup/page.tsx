'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useQuery } from '@tanstack/react-query'
import { Check, RadioTower, Sparkles } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { api } from '@/lib/api'
import { rupees } from '@/lib/format'
import { destinationFor } from '@/lib/roles'
import { useAuthStore } from '@/lib/store'

const GIB = 1024 ** 3
const gb = (bytes: number) => `${Math.round(bytes / GIB)} GB`

// Namespaced so /login's callback handler can tell "a plan chosen on /signup" apart from
// any other future use of the OAuth state parameter.
const STATE_PREFIX = 'signup_plan:'

/**
 * Public storefront: real plans, a Custom/Business card, and "Sign up with Google" -- the
 * ONLY way to create an account (there is no password-based self-registration route; see
 * routers/auth.py's sign_in_with_google). No session exists here, so a plan choice cannot be
 * purchased on this page -- it is carried through Google's own `state` round-trip to
 * /login, which forwards it as /dashboard/pending?plan=<id|custom> once the new (or
 * existing) account is signed in. That page does the actual activating or form-opening.
 */
export default function SignupPage() {
  const router = useRouter()
  const { token, user, hydrated } = useAuthStore()
  const [googleEnabled, setGoogleEnabled] = useState(true)
  const [starting, setStarting] = useState<string | null>(null)
  const [error, setError] = useState('')

  const plansQuery = useQuery({ queryKey: ['public-plans'], queryFn: api.getPublicPlans })

  // Already signed in? There is nothing to sign up for.
  useEffect(() => {
    if (hydrated && token) router.replace(destinationFor(user))
  }, [hydrated, token, user, router])

  useEffect(() => {
    api.authMethods().then((methods) => setGoogleEnabled(methods.google ?? true)).catch(() => setGoogleEnabled(true))
  }, [])

  const startGoogle = async (intent: string) => {
    setStarting(intent)
    setError('')
    try {
      const redirectUri = `${window.location.origin}/login`
      let res = await api.googleAuthUrl(redirectUri, `${STATE_PREFIX}${intent}`).catch(() => null)
      if (!res?.url) {
        await new Promise((r) => setTimeout(r, 2000))
        res = await api.googleAuthUrl(redirectUri, `${STATE_PREFIX}${intent}`).catch(() => null)
      }
      if (res?.url) {
        window.location.href = res.url
        return
      }
      setError('Google sign-in is not enabled on this server.')
    } catch {
      setError('Could not reach the sign-in service. The backend server may be waking up (Render free tier cold start). Please wait 10 seconds and try again.')
    } finally {
      setStarting(null)
    }
  }

  const plans = plansQuery.data ?? []

  return (
    <main className="relative min-h-screen bg-background px-4 py-8 sm:px-6 sm:py-12 text-foreground overflow-x-hidden">
      <div className="pointer-events-none absolute -left-32 -top-32 size-[500px] rounded-full bg-emerald-500/10 blur-[140px]" />
      <div className="pointer-events-none absolute -bottom-32 -right-32 size-[500px] rounded-full bg-teal-500/10 blur-[140px]" />

      <div className="relative mx-auto w-full max-w-6xl">
        <header className="mb-8 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-center gap-3">
            <div className="grid size-10 place-items-center rounded-xl bg-gradient-to-tr from-emerald-500 to-teal-400 text-black shadow-lg shadow-emerald-500/20 shrink-0">
              <RadioTower className="size-5" />
            </div>
            <div>
              <p className="text-lg font-bold tracking-wider">MR <span className="font-medium text-emerald-400">SIGNAGE</span></p>
              <p className="text-[10px] font-semibold text-emerald-400 tracking-wider">(Midhun-Roshan)</p>
            </div>
          </div>
          <Link href="/login" className="text-sm font-medium text-muted-foreground hover:text-foreground">
            Already have an account? <span className="text-emerald-400 font-semibold">Sign in</span>
          </Link>
        </header>

        <div className="mb-10 max-w-2xl">
          <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">Create your workspace</h1>
          <p className="mt-3 text-sm leading-relaxed text-muted-foreground">
            Pick a plan and sign up with Google -- it both creates your account and starts activating the one you chose. No card needed for Free.
          </p>
        </div>

        {error && (
          <div role="alert" className="mb-6 rounded-2xl border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
            {error}
          </div>
        )}

        {!googleEnabled && (
          <div role="alert" className="mb-6 rounded-2xl border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm text-amber-300">
            Google sign-in is not enabled on this server. Sign-up is not available right now.
          </div>
        )}

        {plansQuery.isError && (
          <div role="alert" className="mb-5 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm">
            <span>The plans could not be loaded.</span>
            <Button size="sm" variant="outline" onClick={() => plansQuery.refetch()}>Try again</Button>
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
                <div key={plan.id} className="flex flex-col rounded-3xl border border-border bg-card p-6 shadow-xl backdrop-blur-xl">
                  <h3 className="text-lg font-semibold">{plan.name}</h3>
                  <p className="mt-3 text-3xl font-bold">
                    {free ? 'Free' : rupees(plan.price_paise)}
                    <span className="ml-1 text-sm font-normal text-muted-foreground">/ {plan.duration_days} days</span>
                  </p>
                  <ul className="mt-6 space-y-2.5 text-sm text-muted-foreground">
                    <Line>
                      {plan.max_screens == null ? 'Unlimited TVs' : plan.max_screens > 0 ? `${plan.max_screens} TVs` : 'No TVs included'}
                    </Line>
                    <Line>{plan.max_ad_slots > 0 ? `${plan.max_ad_slots} ad slots` : 'Unlimited ad slots'}</Line>
                    <Line>{plan.max_clients > 0 ? `${plan.max_clients} clients` : 'Unlimited clients'}</Line>
                    <Line>{plan.max_storage_bytes ? `${gb(plan.max_storage_bytes)} storage` : 'Unlimited storage'}</Line>
                    {features.map((f) => <Line key={f}>{f.replaceAll('_', ' ')}</Line>)}
                  </ul>
                  <Button
                    onClick={() => startGoogle(String(plan.id))}
                    disabled={!googleEnabled || starting !== null}
                    className="mt-6 h-11 w-full bg-emerald-500 font-semibold text-black hover:bg-emerald-400 disabled:opacity-50"
                  >
                    {starting === String(plan.id) ? 'Redirecting…' : free ? 'Get started free' : 'Choose plan'}
                  </Button>
                </div>
              )
            })}

            {/* Custom card: same shape as the dashboard/pending storefront's, since the request
                form itself lives there -- this just gets the new account to it with the
                custom form already open. */}
            <div className="flex flex-col rounded-3xl border border-dashed border-emerald-500/30 bg-card p-6">
              <div className="flex items-center gap-2">
                <Sparkles className="size-4 text-emerald-400" />
                <h3 className="text-lg font-semibold">Custom / Business</h3>
              </div>
              <p className="mt-3 text-sm text-muted-foreground">
                Need a specific number of TVs, clients or storage, or a particular set of features? Sign up and tell us the shape -- a manager prices it.
              </p>
              <Button
                onClick={() => startGoogle('custom')}
                disabled={!googleEnabled || starting !== null}
                variant="outline"
                className="mt-auto h-11 w-full border-emerald-500/40 bg-emerald-500/10 font-semibold text-emerald-300 hover:bg-emerald-500/20 disabled:opacity-50"
              >
                {starting === 'custom' ? 'Redirecting…' : 'Sign up & request a custom plan'}
              </Button>
            </div>
          </div>
        )}
      </div>
    </main>
  )
}

function Line({ children }: { children: React.ReactNode }) {
  return (
    <li className="flex items-center gap-2 capitalize">
      <Check className="size-4 shrink-0 text-emerald-400" /> {children}
    </li>
  )
}
