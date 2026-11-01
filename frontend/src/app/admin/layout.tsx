'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import { useQuery } from '@tanstack/react-query'
import { adminApi, api } from '@/lib/api'
import { isSuperAdmin } from '@/lib/roles'
import { useAuthStore } from '@/lib/store'
import { ThemeToggle } from '@/components/dashboard/theme-toggle'
import { ConfirmProvider } from '@/components/admin/kit/confirm-dialog'
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import {
  ShieldCheck, Users, Film, LogOut, LayoutDashboard, Package, Receipt, Rocket, QrCode, Menu, X,
  Activity, HardDrive, PanelLeftClose, PanelLeftOpen, ChevronRight,
} from 'lucide-react'

type NavItem = { href: string; label: string; icon: typeof LayoutDashboard; exact?: boolean; badge?: number }

// Grouped the way an operator thinks: who they sell to, what is for sale, what is running
// in the field, and the platform itself.
const navSections: { label: string; items: NavItem[] }[] = [
  { label: '', items: [{ href: '/admin', label: 'Overview', icon: LayoutDashboard, exact: true }] },
  {
    label: 'Customers',
    items: [
      { href: '/admin/approvals', label: 'Approvals', icon: ShieldCheck },
      { href: '/admin/tenants', label: 'Tenants', icon: Users },
    ],
  },
  {
    label: 'Commercial',
    items: [
      { href: '/admin/packages', label: 'Packages', icon: Package },
      { href: '/admin/billing', label: 'Billing', icon: Receipt },
    ],
  },
  {
    label: 'Fleet',
    items: [
      { href: '/admin/fleet', label: 'TV Fleet', icon: Activity },
      { href: '/admin/releases', label: 'App Releases', icon: Rocket },
      { href: '/admin/provisioning', label: 'Provisioning', icon: QrCode },
    ],
  },
  {
    label: 'Platform',
    items: [
      { href: '/admin/storage', label: 'Storage', icon: HardDrive },
      { href: '/admin/demo-video', label: 'Demo Video', icon: Film },
    ],
  },
]

const allItems = navSections.flatMap((section) => section.items.map((item) => ({ ...item, section: section.label })))

function isActive(item: NavItem, pathname: string) {
  return item.exact ? pathname === item.href : pathname === item.href || pathname.startsWith(`${item.href}/`)
}

/** Where we are, as Section / Page / Details, from the nav itself so the two cannot drift. */
function breadcrumbsFor(pathname: string) {
  const item = allItems.filter((candidate) => isActive(candidate, pathname)).sort((a, b) => b.href.length - a.href.length)[0]
  if (!item) return ['Admin']
  const crumbs = item.section ? [item.section, item.label] : [item.label]
  if (pathname !== item.href) crumbs.push('Details')
  return crumbs
}

const RAIL_KEY = 'admin-rail-collapsed'

function readCollapsed(): boolean {
  try {
    return typeof window !== 'undefined' && window.localStorage.getItem(RAIL_KEY) === '1'
  } catch {
    return false
  }
}

function writeCollapsed(value: boolean) {
  try {
    window.localStorage.setItem(RAIL_KEY, value ? '1' : '0')
  } catch {
    // A per-viewer convenience; losing it just means the rail opens expanded next time.
  }
}

/** "Local" or "Production", so nobody suspends a real tenant thinking they are on a test copy. */
function EnvironmentBadge({ enabled }: { enabled: boolean }) {
  const health = useQuery({ queryKey: ['admin', 'platform-health'], queryFn: adminApi.platformHealth, enabled, staleTime: 5 * 60_000 })
  if (!health.data) return null
  const host = (health.data.host ?? '').toLowerCase()
  const local = health.data.backend === 'sqlite' || ['localhost', '127.0.0.1', '::1'].includes(host)
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] font-semibold tracking-wide uppercase ${
        local ? 'border-border text-muted-foreground' : 'border-red-500/40 text-red-700 dark:text-red-400'
      }`}
      title={local ? 'Connected to a local database' : 'Connected to the production database'}
    >
      <span className={`size-1.5 rounded-full ${local ? 'bg-muted-foreground' : 'bg-red-500'}`} aria-hidden="true" />
      {local ? 'Local' : 'Production'}
    </span>
  )
}

function Brand({ compact }: { compact: boolean }) {
  return (
    <div className="flex items-center gap-2.5">
      <div className="grid size-8 shrink-0 place-items-center rounded-lg bg-foreground text-[11px] font-bold tracking-tight text-background">
        MR
      </div>
      {!compact && (
        <div className="min-w-0 leading-tight">
          <p className="truncate text-sm font-semibold text-foreground">MR Signage</p>
          <p className="truncate text-[11px] text-muted-foreground">Control Center</p>
        </div>
      )}
    </div>
  )
}

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const { token, user, hydrated, clearSession } = useAuthStore()
  const router = useRouter()
  const pathname = usePathname()

  // Off-canvas nav for phones, closed on every navigation -- reset during render on the route
  // change, not in an effect that fires after the new page has painted under the drawer.
  const [navOpen, setNavOpen] = useState(false)
  const [navPath, setNavPath] = useState(pathname)
  if (navPath !== pathname) {
    setNavPath(pathname)
    setNavOpen(false)
  }
  const [collapsed, setCollapsed] = useState(readCollapsed)

  // The server's answer, not the browser's memory of it: a cached user that predates a
  // promotion would otherwise bounce a legitimate operator out of /admin.
  const meQuery = useQuery({ queryKey: ['me'], queryFn: api.me, enabled: hydrated && Boolean(token) })
  const account = meQuery.data ?? user
  // Undefined until the first answer arrives, so the redirect waits rather than bouncing a
  // legitimate operator out on a cache miss.
  const resolved = meQuery.isSuccess || !meQuery.isFetching
  const isAdmin = resolved && isSuperAdmin(account)

  // Shares its cache with the Billing page and the custom-requests panel (same queryKey).
  const requestsQuery = useQuery({
    queryKey: ['admin', 'custom-requests'], queryFn: adminApi.listCustomRequests,
    enabled: isAdmin, refetchInterval: 15000,
  })
  const requestedCount = (requestsQuery.data ?? []).filter((r) => r.status === 'requested').length

  useEffect(() => {
    if (!hydrated) return
    if (!token) { router.replace('/login'); return }
    if (!resolved) return
    if (!isSuperAdmin(account)) {
      router.replace(account?.organization_status === 'pending_approval' ? '/dashboard/pending' : '/dashboard/screens')
    }
  }, [hydrated, token, account, resolved, router])

  // On <body> as well as the wrapper: dialogs, menus and toasts render in portals outside
  // the wrapper and would otherwise take the tenant theme.
  useEffect(() => {
    document.body.classList.add('admin-console')
    return () => document.body.classList.remove('admin-console')
  }, [])

  if (!hydrated || !token || !resolved || !isSuperAdmin(account)) {
    return <div className="admin-console min-h-screen bg-background" />
  }

  const logout = () => { clearSession(); router.replace('/login') }
  const toggleCollapsed = () => setCollapsed((value) => { writeCollapsed(!value); return !value })
  const crumbs = breadcrumbsFor(pathname)
  // The rail only collapses on desktop; the phone drawer always shows labels.
  const compact = collapsed && !navOpen

  return (
    <ConfirmProvider>
      <div className="admin-console min-h-screen bg-background text-foreground lg:flex">
        {navOpen && (
          <div className="fixed inset-0 z-40 bg-black/50 lg:hidden" onClick={() => setNavOpen(false)} aria-hidden="true" />
        )}

        {/* A drawer under lg, a fixed column above it. The closed offset is scoped to
            max-lg so nothing at lg+ competes with it for the same property. */}
        <aside
          className={`fixed inset-y-0 left-0 z-50 flex w-64 shrink-0 flex-col border-r border-border bg-background transition-[transform,width] duration-200 lg:sticky lg:top-0 lg:z-auto lg:h-screen ${
            navOpen ? '' : 'max-lg:-translate-x-full'
          } ${compact ? 'lg:w-16' : 'lg:w-60'}`}
          aria-label="Admin navigation"
        >
          <div className={`flex h-14 items-center border-b border-border ${compact ? 'justify-center px-2' : 'justify-between px-4'}`}>
            <Brand compact={compact} />
            <button
              onClick={() => setNavOpen(false)}
              className="grid size-8 place-items-center rounded-lg text-muted-foreground hover:bg-accent hover:text-foreground lg:hidden"
              aria-label="Close admin navigation"
            >
              <X className="size-4" />
            </button>
          </div>

          <nav className="flex-1 space-y-5 overflow-y-auto px-2 py-4">
            {navSections.map((section) => (
              <div key={section.label || 'top'} className="space-y-0.5">
                {section.label && !compact && (
                  <p className="px-3 pb-1.5 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
                    {section.label}
                  </p>
                )}
                {section.items.map((item) => {
                  const active = isActive(item, pathname)
                  const Icon = item.icon
                  const badge = item.href === '/admin/billing' && requestedCount > 0 ? requestedCount : 0
                  return (
                    <Link
                      key={item.href}
                      href={item.href}
                      title={compact ? item.label : undefined}
                      aria-current={active ? 'page' : undefined}
                      className={`relative flex items-center gap-3 rounded-lg py-2 text-sm transition-colors ${
                        compact ? 'justify-center px-2' : 'px-3'
                      } ${
                        active
                          ? 'bg-accent font-medium text-foreground'
                          : 'text-muted-foreground hover:bg-accent/60 hover:text-foreground'
                      }`}
                    >
                      {active && <span className="absolute inset-y-1.5 left-0 w-0.5 rounded-full bg-foreground" aria-hidden="true" />}
                      <Icon className="size-4 shrink-0" />
                      {!compact && <span className="truncate">{item.label}</span>}
                      {badge > 0 && (
                        <span
                          className={`grid min-w-5 place-items-center rounded-full bg-amber-500 px-1 text-[10px] font-bold text-black ${
                            compact ? 'absolute -top-0.5 -right-0.5 h-4 min-w-4' : 'ml-auto h-5'
                          }`}
                          aria-label={`${badge} new`}
                        >
                          {badge}
                        </span>
                      )}
                    </Link>
                  )
                })}
              </div>
            ))}
          </nav>

          <div className="hidden border-t border-border p-2 lg:block">
            <button
              onClick={toggleCollapsed}
              className={`flex w-full items-center gap-3 rounded-lg py-2 text-sm text-muted-foreground hover:bg-accent hover:text-foreground ${
                compact ? 'justify-center px-2' : 'px-3'
              }`}
              aria-label={compact ? 'Expand navigation' : 'Collapse navigation'}
            >
              {compact ? <PanelLeftOpen className="size-4" /> : <PanelLeftClose className="size-4" />}
              {!compact && 'Collapse'}
            </button>
          </div>
        </aside>

        {/* min-w-0 so a wide table scrolls itself instead of pushing the page sideways. */}
        <div className="flex min-w-0 flex-1 flex-col">
          <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-border bg-background/90 px-4 backdrop-blur supports-[backdrop-filter]:bg-background/75 sm:px-6">
            <button
              onClick={() => setNavOpen(true)}
              className="grid size-9 place-items-center rounded-lg text-muted-foreground hover:bg-accent hover:text-foreground lg:hidden"
              aria-label="Open admin navigation"
            >
              <Menu className="size-5" />
            </button>

            <nav aria-label="Breadcrumb" className="min-w-0 flex-1">
              <ol className="flex min-w-0 items-center gap-1.5 text-sm">
                {crumbs.map((crumb, index) => (
                  <li key={`${crumb}-${index}`} className="flex min-w-0 items-center gap-1.5">
                    {index > 0 && <ChevronRight className="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />}
                    <span
                      className={`truncate ${index === crumbs.length - 1 ? 'font-medium text-foreground' : 'text-muted-foreground max-sm:hidden'}`}
                      aria-current={index === crumbs.length - 1 ? 'page' : undefined}
                    >
                      {crumb}
                    </span>
                  </li>
                ))}
              </ol>
            </nav>

            <EnvironmentBadge enabled={isAdmin} />
            <ThemeToggle />
            <DropdownMenu>
              <DropdownMenuTrigger
                className="grid size-9 place-items-center rounded-full bg-foreground text-xs font-semibold text-background outline-none focus-visible:ring-2 focus-visible:ring-ring"
                aria-label="Account"
              >
                {(account?.full_name || account?.username || '?').slice(0, 1).toUpperCase()}
              </DropdownMenuTrigger>
              <DropdownMenuContent>
                <DropdownMenuLabel>
                  <p className="truncate text-sm font-medium text-foreground">{account?.full_name || account?.username}</p>
                  <p className="truncate text-xs text-muted-foreground">{account?.email}</p>
                  <p className="mt-1 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">Platform administrator</p>
                </DropdownMenuLabel>
                <DropdownMenuSeparator />
                <DropdownMenuItem onClick={logout}>
                  <LogOut />
                  Sign out
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </header>

          <main className="min-w-0 flex-1">{children}</main>
        </div>
      </div>
    </ConfirmProvider>
  )
}
