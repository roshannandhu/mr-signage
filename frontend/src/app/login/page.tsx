'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import {
  ArrowRight,
  Eye,
  EyeOff,
  Lock,
  Mail,
  RadioTower,
  ShieldCheck,
  Tv,
  WifiOff,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { api } from '@/lib/api'
import { destinationFor } from '@/lib/roles'
import { useAuthStore } from '@/lib/store'

/**
 * Where a user belongs after signing in.
 *
 * One login page serves both audiences, so the split happens here. A platform operator
 * goes to /admin, which has none of the tenant features; everyone else goes to their own
 * workspace. This used to be a two-way `organization_status` check copy-pasted into three
 * places, none of which looked at the role -- so a super admin landed on the tenant
 * dashboard, where the nav is gated on `role === 'owner'` and therefore showed them
 * nothing, and /admin was reachable only by typing the URL.
 */
export { destinationFor }

export default function LoginPage() {
  const router = useRouter()
  const { token, user, hydrated, setSession, clearSession } = useAuthStore()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [error, setError] = useState('')
  const [pending, setPending] = useState(false)
  const [googleEnabled, setGoogleEnabled] = useState(true)

  useEffect(() => {
    // Only auto-redirect if ?redirect=true is in the query params
    const params = new URLSearchParams(window.location.search)
    if (params.get('redirect') === 'true' && hydrated && token) {
      router.replace(destinationFor(user))
    }
  }, [hydrated, token, user, router])

  useEffect(() => {
    api.authMethods()
      .then((methods) => setGoogleEnabled(methods.google ?? true))
      .catch(() => setGoogleEnabled(true))
  }, [])

  // Google OAuth redirect handler.
  //
  // Stays an effect: it reads window.location, which does not exist while this route is
  // prerendered on the server, and it rewrites browser history -- a side effect that must
  // not run during render, where React may call the body more than once. Mount-only, so
  // the cascading-render the rule guards against cannot happen here.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const code = params.get('code')
    const denied = params.get('error')
    if (denied) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- reads window + rewrites history; see above
      setError('Google authentication was cancelled.')
      window.history.replaceState({}, '', window.location.pathname)
      return
    }
    if (!code) return

    setPending(true)
    const redirectUri = `${window.location.origin}${window.location.pathname}`
    window.history.replaceState({}, '', window.location.pathname)
    api
      .loginWithGoogle(code, redirectUri)
      .then((session) => {
        setSession(session.access_token, session.user)
        router.replace(destinationFor(session.user))
      })
      .catch((reason) => setError(reason instanceof Error ? reason.message : 'Google authentication failed'))
      .finally(() => setPending(false))
  }, [router, setSession])

  const startGoogle = async () => {
    setPending(true)
    setError('')
    const redirectUri = `${window.location.origin}/login`

    // The server owns this decision, because only it holds the client secret. The final
    // `else` here used to send the browser to /api/auth/google/oauth-page -- a local page
    // that rendered a Google-looking account chooser built from real rows in the users
    // table, listing six people's names and email addresses to anyone who asked. That
    // route is gone, and so is this fallback.
    try {
      const { url } = await api.googleAuthUrl(redirectUri)
      if (url) {
        window.location.href = url
        return
      }
      setError('Google sign-in is not enabled on this server. Use your email and password.')
    } catch {
      setError('Could not reach the sign-in service. Check your connection and try again.')
    } finally {
      setPending(false)
    }
  }

  const handleLogin = async (event: React.FormEvent) => {
    event.preventDefault()
    setError('')
    setPending(true)
    try {
      const form = event.currentTarget as HTMLFormElement
      const data = new FormData(form)
      const formUser = String(data.get('username') ?? '').trim() || username.trim()
      const formPass = String(data.get('password') ?? '') || password
      const session = await api.login(formUser, formPass)
      setSession(session.access_token, session.user)
      router.replace(destinationFor(session.user))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Incorrect email or password. Please try again.')
    } finally {
      setPending(false)
    }
  }

  return (
    <main className="grid min-h-screen grid-cols-1 bg-background text-foreground lg:grid-cols-[1.1fr_.9fr]">
      {/* Left Feature Showcase */}
      <section className="relative hidden flex-col justify-between overflow-hidden border-r border-border bg-card p-12 lg:flex xl:p-16">
        {/* Glow ambient spots */}
        <div className="absolute -left-20 -top-20 size-96 rounded-full bg-emerald-500/15 blur-[120px] pointer-events-none" />
        <div className="absolute -bottom-20 right-0 size-96 rounded-full bg-cyan-500/10 blur-[140px] pointer-events-none" />

        {/* Brand Header */}
        <div className="relative flex items-center gap-3.5">
          <div className="grid size-11 place-items-center rounded-2xl bg-gradient-to-tr from-emerald-500 to-teal-400 text-black shadow-lg shadow-emerald-500/20">
            <RadioTower className="size-6 text-black" />
          </div>
          <div>
            <p className="font-extrabold tracking-wider text-xl text-foreground">MR <span className="text-emerald-400 font-medium">SIGNAGE</span></p>
            <p className="text-[11px] font-semibold tracking-wider text-emerald-400">(Midhun-Roshan)</p>
            <p className="text-xs uppercase tracking-[0.2em] text-muted-foreground mt-0.5">Next-Gen TV Network Control</p>
          </div>
        </div>

        {/* Hero Copy & Live Badges */}
        <div className="relative max-w-xl py-8">
          <div className="inline-flex items-center gap-2 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-3.5 py-1.5 text-xs font-semibold text-emerald-400 mb-6 backdrop-blur-md">
            <span className="size-2 rounded-full bg-emerald-400 animate-pulse" />
            Active Fleet Engine · Multi-Tenant Cloud
          </div>

          <h1 className="text-balance text-5xl font-bold leading-[1.08] tracking-tight xl:text-6xl text-foreground">
            Manage 50+ Displays. <br />
            <span className="bg-gradient-to-r from-emerald-400 via-teal-300 to-cyan-400 bg-clip-text text-transparent">
              One Unified Signal.
            </span>
          </h1>

          <p className="mt-6 text-lg leading-relaxed text-muted-foreground">
            Schedule dynamic ad campaigns, stream commercial video loops, and keep your screens alive 24/7 with zero pairing codes.
          </p>

          {/* Feature Highlights Grid */}
          <div className="mt-10 grid grid-cols-2 gap-4">
            <div className="flex items-start gap-3 rounded-2xl border border-border bg-muted p-4 backdrop-blur-sm">
              <div className="rounded-xl bg-emerald-500/15 p-2 text-emerald-400">
                <WifiOff className="size-5" />
              </div>
              <div>
                <p className="text-sm font-semibold text-foreground">100% Offline Resilient</p>
                <p className="text-xs text-muted-foreground mt-0.5">Proof-of-play records locally & auto-syncs on Wi-Fi reconnect</p>
              </div>
            </div>

            <div className="flex items-start gap-3 rounded-2xl border border-border bg-muted p-4 backdrop-blur-sm">
              <div className="rounded-xl bg-cyan-500/15 p-2 text-cyan-400">
                <Tv className="size-5" />
              </div>
              <div>
                <p className="text-sm font-semibold text-foreground">Remote TV Control</p>
                <p className="text-xs text-muted-foreground mt-0.5">Wake up screens & bring app to front with 1 click</p>
              </div>
            </div>
          </div>
        </div>

        {/* Footer info */}
        <div className="relative flex items-center justify-between text-xs text-muted-foreground">
          <p>© 2026 MR Signage Systems Inc. • Developed by Legit Forge</p>
          <div className="flex items-center gap-2">
            <ShieldCheck className="size-4 text-emerald-400" />
            <span>Encrypted End-to-End</span>
          </div>
        </div>
      </section>

      {/* Right Login Section */}
      <section className="flex items-center justify-center bg-background px-6 py-12 sm:px-12">
        <div className="w-full max-w-[440px]">
          {/* Mobile Brand Logo */}
          <div className="mb-8 flex items-center gap-3 lg:hidden">
            <div className="grid size-10 place-items-center rounded-xl bg-emerald-500 text-black">
              <RadioTower className="size-5" />
            </div>
            <div>
              <span className="font-bold tracking-wider text-lg">MR SIGNAGE</span>
              <p className="text-[10px] font-semibold text-emerald-400 tracking-wider">(Midhun-Roshan)</p>
            </div>
          </div>

          <div className="mb-8">
            <p className="text-xs font-bold uppercase tracking-[0.2em] text-emerald-400">Secure Access</p>
            <h2 className="mt-2 text-3xl font-bold tracking-tight text-foreground sm:text-4xl">Sign in to your account</h2>
            <p className="mt-2 text-sm text-muted-foreground">Manage your digital signage screens, playlists, and analytics.</p>
          </div>

          {/* Active Session Notice */}
          {token && user && (
            <div className="mb-6 rounded-2xl border border-emerald-500/30 bg-emerald-500/10 p-4 text-xs text-muted-foreground shadow-lg">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p className="font-semibold text-emerald-400">Active session: {user.username}</p>
                  <p className="text-[11px] text-muted-foreground">{user.organization_name || 'Organization'}</p>
                </div>
                <div className="flex items-center gap-2">
                  <Button
                    type="button"
                    size="sm"
                    onClick={() => router.push(user.organization_status === 'pending_approval' ? '/dashboard/pending' : '/dashboard/screens')}
                    className="bg-emerald-500 hover:bg-emerald-400 text-black font-semibold text-xs h-8 px-3 rounded-lg"
                  >
                    Open Dashboard
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={() => clearSession()}
                    className="border-border bg-muted hover:bg-muted text-foreground text-xs h-8 px-2.5 rounded-lg"
                  >
                    Sign Out
                  </Button>
                </div>
              </div>
            </div>
          )}

          {/* Google Official Sign In Button */}
          {googleEnabled && (
            <div className="space-y-4 mb-6">
              <button
                type="button"
                onClick={startGoogle}
                disabled={pending}
                className="group relative flex h-12 w-full items-center justify-center gap-3.5 rounded-xl border border-[#dadce0] bg-white px-5 font-semibold text-[#1f1f1f] text-sm shadow-[0_1px_3px_rgba(0,0,0,0.12),0_1px_2px_rgba(0,0,0,0.24)] transition-all hover:bg-[#f8f9fa] hover:shadow-[0_2px_6px_rgba(0,0,0,0.15),0_1px_3px_rgba(0,0,0,0.2)] active:scale-[0.99] active:bg-[#eeeeee] disabled:opacity-60 cursor-pointer"
              >
                <svg className="size-5 shrink-0" viewBox="0 0 24 24" aria-hidden="true">
                  <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" />
                  <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" />
                  <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.06H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.94l2.85-2.22.81-.63z" />
                  <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.06l3.66 2.84c.87-2.6 3.3-4.52 6.16-4.52z" />
                </svg>
                <span className="font-medium tracking-wide">Continue with Google</span>
              </button>

              <div className="flex items-center gap-3">
                <div className="h-px flex-1 bg-muted" />
                <span className="text-xs uppercase tracking-wider text-muted-foreground font-medium">or continue with email</span>
                <div className="h-px flex-1 bg-muted" />
              </div>
            </div>
          )}

          {/* Form */}
          <form onSubmit={handleLogin} className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="username" className="text-xs font-semibold text-muted-foreground">Email or Username</Label>
              <div className="relative">
                <Mail className="absolute left-3.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  id="username"
                  name="username"
                  autoComplete="username"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  placeholder="you@company.com"
                  required
                  className="h-12 border-border bg-muted pl-10 text-foreground placeholder:text-muted-foreground focus-visible:border-emerald-500 focus-visible:ring-emerald-500/20"
                />
              </div>
            </div>

            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <Label htmlFor="password" className="text-xs font-semibold text-muted-foreground">Password</Label>
              </div>
              <div className="relative">
                <Lock className="absolute left-3.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  id="password"
                  name="password"
                  type={showPassword ? 'text' : 'password'}
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="••••••••••••"
                  required
                  className="h-12 border-border bg-muted pl-10 pr-10 text-foreground placeholder:text-muted-foreground focus-visible:border-emerald-500 focus-visible:ring-emerald-500/20"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(!showPassword)}
                  className="absolute right-3.5 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-muted-foreground"
                  tabIndex={-1}
                >
                  {showPassword ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
                </button>
              </div>
            </div>

            {error && (
              <div role="alert" className="flex items-center gap-2 rounded-xl border border-rose-500/30 bg-rose-500/10 p-3.5 text-xs text-rose-300">
                <span className="size-1.5 rounded-full bg-rose-400" />
                {error}
              </div>
            )}

            <Button
              type="submit"
              size="lg"
              disabled={pending}
              className="h-12 w-full rounded-xl bg-gradient-to-r from-emerald-500 to-teal-500 font-semibold text-black hover:from-emerald-400 hover:to-teal-400 shadow-lg shadow-emerald-500/20 active:scale-[0.99]"
            >
              {pending ? 'Authenticating…' : (
                <span className="flex items-center justify-center gap-2">
                  Sign in to Control Plane <ArrowRight className="size-4" />
                </span>
              )}
            </Button>
          </form>
        </div>
      </section>
    </main>
  )
}
