'use client'

import Image from 'next/image'
import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  BarChart3,
  Bell,
  ChevronDown,
  CreditCard,
  FolderCheck,
  Image as ImageIcon,
  Layers3,
  LogOut,
  MonitorPlay,
  RadioTower,
  Settings2,
  ShieldCheck,
  Contact,
  Palette,
  Tags,
  UserRound,
  Users,
  Receipt,
} from 'lucide-react'
import { ThemeToggle } from '@/components/dashboard/theme-toggle'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuLinkItem,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { api } from '@/lib/api'
import { isSuperAdmin as roleIsSuperAdmin } from '@/lib/roles'
import { useAuthStore } from '@/lib/store'
import { useFleetAlerts } from '@/lib/use-fleet-alerts'
import { usePlanFeatures } from '@/lib/use-plan-features'
import { cn } from '@/lib/utils'

const primaryLinks = [
  { href: '/dashboard/content', label: 'Content', icon: ImageIcon },
  { href: '/dashboard/screens', label: 'Screens', icon: MonitorPlay },
  { href: '/dashboard/groups', label: 'Groups', icon: Layers3 },
]

const toolLinks = [
  { href: '/dashboard/campaigns', label: 'Playback report', icon: BarChart3, ownerOnly: false },
  // Directly under the report on purpose: they are the two halves of one booking, and
  // keeping them adjacent is what makes the split legible. NOT /dashboard/billing, which
  // is this workspace's own subscription to MR Signage.
  { href: '/dashboard/invoices', label: 'Invoices', icon: Receipt, ownerOnly: false },
  { href: '/dashboard/alerts', label: 'Alerts', icon: Bell, ownerOnly: false },
  { href: '/dashboard/files', label: 'File management', icon: FolderCheck, ownerOnly: false },
  { href: '/dashboard/emergency', label: 'Emergency broadcast', icon: RadioTower, ownerOnly: false, feature: 'emergency_alert' },
]

const accountLinks = [
  // The page exists, renders and has a search box; it just had no link, so it was
  // reachable only by typing the URL. 7a60dd2 moved client creation inline into the upload
  // flow and removed this entry, but left the route and the Contact import behind -- an
  // orphaned page rather than a deleted one. Clients are still worth managing somewhere:
  // the campaign report is addressed from this record, and emailing one needs the address
  // kept up to date after the ad was booked. Delete app/dashboard/clients if that is not
  // wanted, rather than leaving it unreachable.
  { href: '/dashboard/clients', label: 'Clients', icon: Contact, ownerOnly: false },
  { href: '/dashboard/plans', label: 'Plans', icon: Tags, ownerOnly: false },
  { href: '/dashboard/branding', label: 'Branding', icon: Palette, ownerOnly: false },
  { href: '/dashboard/team', label: 'Team', icon: Users, ownerOnly: true },
  { href: '/dashboard/billing', label: 'Billing', icon: CreditCard, ownerOnly: true },
]

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  const router = useRouter()
  const { token, user, hydrated, clearSession, setUser, acting, leaveWorkspace } = useAuthStore()
  const meQuery = useQuery({ queryKey: ['me'], queryFn: api.me, enabled: hydrated && Boolean(token) })

  // One socket for the whole dashboard, opened here so every page benefits and no page
  // opens a second one. Alerts arrive as toasts wherever the operator happens to be, which
  // is the point: previously they only existed on /dashboard/alerts, and only if you were
  // looking at it.
  useFleetAlerts()
  const { has: hasFeature } = usePlanFeatures()

  const isPending = (meQuery.data?.organization_status || user?.organization_status) === 'pending_approval'
  // A platform operator has no tenant workspace to manage, and every ownerOnly link below
  // is gated on role === 'owner' -- so a super_admin landing here (from a bookmark, or the
  // old post-login redirect) got a dashboard with an empty Admin menu and no way to reach
  // the console. Send them where their tools actually are.
  // ...unless they came here ON PURPOSE, through "Enter workspace". Then this IS their
  // tool: every call is scoped to that tenant, so the operator works the real screens
  // rather than a set of admin-only copies. Bouncing them would make the feature
  // impossible -- the redirect would fire the instant they arrived.
  const isSuperAdmin = roleIsSuperAdmin(meQuery.data ?? user) && !acting
  // A platform operator not inside any workspace has none of their own to brand; the fetch
  // waits for that the same way meQuery already does, rather than firing (and 403ing) early.
  const brandingQuery = useQuery({
    queryKey: ['branding'],
    queryFn: api.getBranding,
    enabled: hydrated && Boolean(token) && !isSuperAdmin,
  })

  useEffect(() => {
    if (hydrated && !token) {
      router.replace('/login')
      return
    }
    if (hydrated && token && isSuperAdmin) {
      router.replace('/admin')
      return
    }
    // An operator inside a workspace is not subject to that workspace's own status: a
    // pending or lapsed tenant is often exactly the one they went in to sort out.
    if (hydrated && token && isPending && !acting && pathname !== '/dashboard/pending') {
      router.replace('/dashboard/pending')
    }
  }, [hydrated, token, isSuperAdmin, isPending, acting, pathname, router])

  useEffect(() => {
    if (meQuery.data) setUser(meQuery.data)
  }, [meQuery.data, setUser])

  const isOwner = user?.role === 'owner'
  const toolItems = toolLinks.filter((item) => (!item.ownerOnly || isOwner) && (!('feature' in item) || hasFeature(item.feature ?? '')))
  const accountItems = accountLinks.filter((item) => !item.ownerOnly || isOwner)

  const isActive = (href: string) => pathname.startsWith(href)
  const toolsActive = toolItems.some((item) => isActive(item.href))
  const logout = () => {
    clearSession()
    router.replace('/login')
  }

  if (!hydrated || !token || isSuperAdmin) {
    return <div className="bg-background min-h-screen" aria-label="Loading dashboard" />
  }

  if (isPending && !acting) {
    if (pathname !== '/dashboard/pending') {
      return <div className="bg-background min-h-screen" aria-label="Redirecting to pending verification" />
    }
    return <main className="bg-background min-h-screen">{children}</main>
  }

  return (
    <div className="bg-background min-h-screen">
      <a href="#dashboard-content" className="bg-card sr-only z-100 rounded-lg px-4 py-3 font-medium focus:not-sr-only focus:fixed focus:top-4 focus:left-4">
        Skip to content
      </a>

      {/* Impersonation has to be impossible to forget. Everything below this bar writes to
          someone else's workspace, so it sits above the header, states whose workspace it
          is, and keeps the way out one click away at all times. */}
      {acting && (
        <div className="sticky top-0 z-50 flex flex-wrap items-center gap-x-3 gap-y-1 bg-violet-600 px-4 py-2 text-sm text-white sm:px-6 lg:px-8">
          <ShieldCheck className="size-4 shrink-0" aria-hidden="true" />
          <p className="min-w-0 flex-1">
            Platform operator — you are working inside{' '}
            <strong className="font-semibold">{acting.name}</strong>. Everything you change here
            belongs to them.
          </p>
          <button
            onClick={() => {
              const id = acting.id
              leaveWorkspace()
              router.replace(`/admin/tenants/${id}`)
            }}
            className="shrink-0 rounded-lg bg-white/15 px-3 py-1 font-semibold transition-colors hover:bg-white/25"
          >
            Leave workspace
          </button>
        </div>
      )}

      <header className="border-hairline bg-card sticky top-0 z-40 border-b">
        <div className="mx-auto flex h-16 w-full max-w-[1600px] items-center gap-2 px-4 sm:px-6 lg:px-8">
          <Link href="/dashboard/screens" className="mr-4 flex shrink-0 items-center gap-2.5">
            {brandingQuery.data?.logo_url ? (
              <span className="grid size-8 shrink-0 place-items-center overflow-hidden rounded-lg bg-muted">
                <Image
                  src={brandingQuery.data.logo_url}
                  alt=""
                  width={32}
                  height={32}
                  unoptimized
                  className="size-full object-contain"
                />
              </span>
            ) : (
              <span className="bg-primary text-primary-foreground grid size-8 place-items-center rounded-lg">
                <RadioTower className="size-[18px]" aria-hidden="true" />
              </span>
            )}
            <div className="hidden sm:flex sm:flex-col sm:leading-tight">
              {/* Falls back to "MR Signage" (effective_name already resolves brand_name ||
                  org.name server-side, but the query has not answered on first paint). A
                  tenant with its own brand set sees it here, same as on their reports and
                  invoices; the login page keeps the platform's own mark, unconditionally. */}
              <span className="text-foreground text-[16px] font-bold tracking-tight">
                {brandingQuery.data?.effective_name || 'MR Signage'}
              </span>
              <span className="text-muted-foreground text-[10px] font-medium tracking-wide">
                {brandingQuery.data?.brand_name ? 'MR Signage' : '(Midhun-Roshan)'}
              </span>
            </div>
          </Link>

          <nav aria-label="Main" className="hidden items-center gap-1 lg:flex">
            {primaryLinks.map(({ href, label }) => (
              <Link key={href} href={href} className={cn(
                'focus-visible:ring-ring rounded-lg px-3.5 py-2 text-[15px] transition-colors focus-visible:ring-2 focus-visible:outline-none',
                isActive(href)
                  ? 'text-primary dark:text-brand font-semibold'
                  : 'text-muted-foreground hover:text-foreground font-medium',
              )}>
                {label}
              </Link>
            ))}
          </nav>

          <div className="ml-auto flex items-center gap-1">
            <DropdownMenu>
              <DropdownMenuTrigger className={cn(
                'focus-visible:ring-ring flex h-10 cursor-pointer items-center gap-1 rounded-lg px-3 text-[15px] transition-colors focus-visible:ring-2 focus-visible:outline-none',
                toolsActive ? 'text-primary dark:text-brand font-semibold' : 'text-muted-foreground hover:text-foreground font-medium',
              )}>
                Tools <ChevronDown className="size-4" aria-hidden="true" />
              </DropdownMenuTrigger>
              <DropdownMenuContent>
                {toolItems.map(({ href, label, icon: Icon }) => (
                  <DropdownMenuLinkItem key={href} href={href} render={<Link href={href} />}>
                    <Icon aria-hidden="true" /> {label}
                  </DropdownMenuLinkItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>

            <ThemeToggle />

            <DropdownMenu>
              <DropdownMenuTrigger
                aria-label="Account"
                className="bg-primary text-primary-foreground focus-visible:ring-ring ml-1 grid size-9 cursor-pointer place-items-center rounded-full text-[13px] font-bold focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none"
              >
                {(user?.full_name || user?.username)?.slice(0, 2).toUpperCase() || '—'}
              </DropdownMenuTrigger>
              <DropdownMenuContent>
                <DropdownMenuLabel>
                  <p className="text-foreground truncate text-sm font-semibold">{user?.full_name || user?.username || 'Account'}</p>
                  <p className="text-muted-foreground truncate text-xs">{user?.email || user?.username}</p>
                  <p className="text-muted-foreground/70 mt-0.5 truncate text-xs">
                    <span className="capitalize">{user?.role || 'Loading'}</span>
                    {user?.organization_name ? ` · ${user.organization_name}` : ''}
                  </p>
                </DropdownMenuLabel>
                <DropdownMenuSeparator />
                <DropdownMenuLinkItem href="/dashboard/account" render={<Link href="/dashboard/account" />}>
                  <UserRound aria-hidden="true" /> Profile
                </DropdownMenuLinkItem>
                {accountItems.map(({ href, label, icon: Icon }) => (
                  <DropdownMenuLinkItem key={href} href={href} render={<Link href={href} />}>
                    <Icon aria-hidden="true" /> {label}
                  </DropdownMenuLinkItem>
                ))}
                <DropdownMenuLinkItem href="/dashboard/screens" render={<Link href="/dashboard/screens" />}>
                  <Settings2 aria-hidden="true" /> Manage screens
                </DropdownMenuLinkItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem onClick={logout}>
                  <LogOut aria-hidden="true" /> Log out
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>
      </header>

      <main id="dashboard-content" className="mx-auto w-full max-w-[1600px] px-4 py-8 pb-[calc(6rem+env(safe-area-inset-bottom))] sm:px-6 lg:px-8 lg:py-10 lg:pb-12">
        {children}
      </main>

      <nav aria-label="Main" className="border-hairline bg-card/95 fixed inset-x-0 bottom-0 z-40 grid min-h-[72px] grid-cols-3 border-t px-2 pt-1 pb-[env(safe-area-inset-bottom)] backdrop-blur-xl lg:hidden">
        {primaryLinks.map(({ href, label, icon: Icon }) => (
          <Link key={href} href={href} className={cn(
            'focus-visible:ring-ring flex min-w-0 flex-col items-center justify-center gap-1 text-[10px] font-semibold transition-colors focus-visible:ring-2 focus-visible:ring-inset focus-visible:outline-none',
            isActive(href) ? 'text-primary dark:text-brand' : 'text-muted-foreground',
          )}>
            <Icon className="size-5" aria-hidden="true" />
            <span className="truncate">{label}</span>
          </Link>
        ))}
      </nav>
    </div>
  )
}
