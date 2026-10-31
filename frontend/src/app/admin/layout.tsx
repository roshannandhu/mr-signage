'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { isSuperAdmin } from '@/lib/roles'
import { useAuthStore } from '@/lib/store'
import {
  ShieldCheck, Users, Film, LogOut, ChevronRight, LayoutDashboard,
  Package, Rocket, QrCode, Menu, X, Activity, HardDrive
} from 'lucide-react'

// Platform operator navigation items
const navItems = [
  { href: '/admin', label: 'Overview', icon: LayoutDashboard, exact: true },
  { href: '/admin/approvals', label: 'Approvals Queue', icon: ShieldCheck },
  { href: '/admin/tenants', label: 'All Tenants', icon: Users },
  { href: '/admin/packages', label: 'Packages', icon: Package },
  { href: '/admin/storage', label: 'Storage', icon: HardDrive },
  { href: '/admin/releases', label: 'App Releases', icon: Rocket },
  { href: '/admin/fleet', label: 'Fleet Versions', icon: Activity },
  { href: '/admin/provisioning', label: 'Zero-Touch Provisioning', icon: QrCode },
  { href: '/admin/demo-video', label: 'Demo Video', icon: Film },
]

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const { token, user, hydrated, clearSession } = useAuthStore()
  const router = useRouter()
  const pathname = usePathname()
  // The server's answer, not the browser's memory of it.
  //
  // This guard used to read `user` alone, which is whatever was written to localStorage at
  // the last sign-in. A platform operator whose cached copy predated the role column -- or
  // who was promoted to super_admin during a live session -- therefore failed the check and
  // was redirected to /dashboard/screens, a tenant workspace they do not belong in, while
  // the API served /api/admin/* to them perfectly well. The dashboard layout already
  // resolved this the right way (`meQuery.data?.role || user?.role`); the two guards simply
  // disagreed, and the one guarding /admin was the stale one.
  // Off-canvas nav for phones. Closed on every navigation, or the drawer stays over the
  // page the operator just asked for.
  const [navOpen, setNavOpen] = useState(false)
  // Reset on the route CHANGE, during render, rather than in an effect that fires after
  // the new page has already painted with the drawer still over it.
  const [navPath, setNavPath] = useState(pathname)
  if (navPath !== pathname) {
    setNavPath(pathname)
    setNavOpen(false)
  }

  const meQuery = useQuery({ queryKey: ['me'], queryFn: api.me, enabled: hydrated && Boolean(token) })
  const account = meQuery.data ?? user
  // Undefined until the first answer arrives, so the redirect below waits rather than
  // bouncing a legitimate operator out on a cache miss.
  const resolved = meQuery.isSuccess || !meQuery.isFetching

  useEffect(() => {
    if (!hydrated) return
    if (!token) { router.replace('/login'); return }
    if (!resolved) return
    if (!isSuperAdmin(account)) {
      if (account?.organization_status === 'pending_approval') {
        router.replace('/dashboard/pending')
      } else {
        router.replace('/dashboard/screens')
      }
      return
    }
  }, [hydrated, token, account, resolved, router])

  if (!hydrated || !token || !resolved || !isSuperAdmin(account)) {
    return <div className="dark admin-console bg-background min-h-screen" />
  }

  const logout = () => { clearSession(); router.replace('/login') }

  return (
    // `dark admin-console`: the console palette is scoped here (see globals.css) so admin
    // is always a dark control plane whatever the site theme says, and every page below
    // re-skins without being touched. `dark` is required alongside it because the dark
    // variant is defined as `&:is(.dark *)` -- without it the `dark:` utilities already
    // written into these pages would follow the tenant theme and fight the console.
    <div className="dark admin-console min-h-screen bg-background lg:flex">
      {/* Phone header. The sidebar below is 256px wide and was rendered at every width,
          which left about 110px of usable content on a 375px screen. */}
      <header className="sticky top-0 z-30 flex items-center gap-3 border-b border-border bg-card px-4 py-3 lg:hidden">
        <button
          onClick={() => setNavOpen(true)}
          className="grid size-9 place-items-center rounded-xl text-muted-foreground hover:bg-muted hover:text-foreground"
          aria-label="Open admin navigation"
        >
          <Menu className="size-5" />
        </button>
        <div className="flex items-center gap-2">
          <div className="grid size-7 place-items-center rounded-lg bg-gradient-to-br from-violet-500 to-purple-700">
            <ShieldCheck className="size-3.5 text-white" />
          </div>
          <div>
            <p className="text-sm font-bold text-foreground">MR Admin</p>
            <p className="text-[10px] text-violet-400 font-medium leading-none">(Midhun-Roshan)</p>
          </div>
        </div>
      </header>

      {/* Scrim. Clicking away is how a drawer is dismissed on a phone. */}
      {navOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/60 lg:hidden"
          onClick={() => setNavOpen(false)}
          aria-hidden="true"
        />
      )}

      {/* Sidebar: a drawer under lg, a fixed column above it. */}
      <aside
        // max-lg: on the closed state, and NO lg:translate-x-0 to fight it.
        //
        // The pair `-translate-x-full` + `lg:translate-x-0` looked right and did not work:
        // both target the same property, so which one won came down to their order in
        // Tailwind's generated sheet rather than to the viewport. Below lg the drawer
        // resolved to translate:0% and sat on top of the page at x=0 -- a 256px panel
        // covering the content on every tablet and small laptop, with elementFromPoint over
        // the page returning a nav link.
        //
        // Scoping the offset to max-lg means it simply does not exist at lg and above,
        // so there is nothing left to conflict with.
        className={`fixed inset-y-0 left-0 z-50 w-64 shrink-0 border-r border-border flex flex-col bg-card transition-transform lg:static lg:z-auto ${
          navOpen ? '' : 'max-lg:-translate-x-full'
        }`}
      >
        <button
          onClick={() => setNavOpen(false)}
          className="absolute top-4 right-3 grid size-8 place-items-center rounded-lg text-muted-foreground hover:bg-muted hover:text-foreground lg:hidden"
          aria-label="Close admin navigation"
        >
          <X className="size-4" />
        </button>
        {/* Logo */}
        <div className="p-5 border-b border-border">
          <div className="flex items-center gap-3">
            <div className="size-8 rounded-xl bg-gradient-to-br from-violet-500 to-purple-700 flex items-center justify-center">
              <ShieldCheck className="size-4 text-white" />
            </div>
            <div>
              <p className="text-sm font-bold text-foreground">MR Admin</p>
              <p className="text-[10px] text-violet-400 font-medium">(Midhun-Roshan) • Platform</p>
            </div>
          </div>
        </div>

        {/* Nav */}
        <nav className="flex-1 p-3 space-y-0.5">
          {navItems.map((item) => {
            const active = item.exact ? pathname === item.href : pathname.startsWith(item.href)
            const Icon = item.icon
            return (
              // Link, not <a>. A raw anchor made every click a full document reload: the
              // React Query cache was thrown away and refetched, the auth guard re-ran and
              // flashed its empty div, and the whole admin area felt broken on a slow
              // connection. The dashboard layout has always used Link; this one did not.
              <Link
                key={item.href}
                href={item.href}
                className={`flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm transition-all ${
                  active
                    ? 'bg-violet-500/15 text-violet-700 dark:text-violet-300 font-semibold border border-violet-500/20'
                    : 'text-muted-foreground hover:bg-muted hover:text-foreground'
                }`}
              >
                <Icon className="size-4 shrink-0" />
                {item.label}
                {active && <ChevronRight className="size-3 ml-auto text-violet-400" />}
              </Link>
            )
          })}
        </nav>

        {/* Divider — tenant dashboard link */}
        <div className="p-3 border-t border-border space-y-1">
          <button
            onClick={logout}
            className="w-full flex items-center gap-3 px-3 py-2 rounded-xl text-xs text-muted-foreground hover:text-rose-400 hover:bg-rose-500/5 transition-all"
          >
            <LogOut className="size-3.5" />
            Sign Out
          </button>
          <div className="px-3 pt-2">
            <p className="text-[10px] text-muted-foreground font-mono truncate">{user?.email || user?.username}</p>
          </div>
        </div>
      </aside>

      {/* Main content. min-w-0 so a wide table inside scrolls itself instead of stretching
          the flex row and pushing the page sideways. */}
      <main className="min-w-0 flex-1 overflow-auto">
        {children}
      </main>
    </div>
  )
}
