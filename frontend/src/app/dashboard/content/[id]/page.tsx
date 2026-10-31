'use client'

import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { useParams } from 'next/navigation'
import { useQuery } from '@tanstack/react-query'
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import {
  ArrowLeft,
  BarChart3,
  Building2,
  CalendarRange,
  CheckCircle2,
  Film,
  IndianRupee,
  MapPin,
  MonitorPlay,
  Receipt,
  RefreshCw,
  TriangleAlert,
} from 'lucide-react'
import { EmptyState } from '@/components/dashboard/empty-state'
import { ErrorState } from '@/components/dashboard/error-state'
import { MediaThumbnail } from '@/components/dashboard/media-thumbnail'
import { EditClientAdModal } from '@/components/dashboard/edit-client-ad-modal'
import { ScreenMap } from '@/components/dashboard/screen-map'
import { AdBookings } from '@/components/dashboard/ad-bookings'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { api } from '@/lib/api'
import {
  asDate,
  assetOrientation,
  bookingState,
  clipDuration,
  formatBytes,
  money,
  relativeTime,
  rupees,
} from '@/lib/format'
import { canEditTenantContent } from '@/lib/roles'
import { cn } from '@/lib/utils'
import { useAuthStore } from '@/lib/store'
import type { MediaPeriodStats, Playlist, Screen } from '@/lib/types'

const SECTIONS = [
  { id: 'booking', label: 'Booking' },
  { id: 'locations', label: 'Locations' },
  { id: 'performance', label: 'Performance' },
  { id: 'technical', label: 'Technical' },
] as const

/**
 * Jump links for the sections below, pinned under the app header.
 *
 * This page used to be four tabs. On a phone that was worse than no navigation at all:
 * "Booking & billing" wrapped onto two lines and "Where it plays" onto three, so the strip
 * became a ragged block of uneven labels -- and three quarters of the page was a click
 * away. Everything is on one page now, and this only scrolls to it.
 *
 * `overflow-x-auto` with `whitespace-nowrap` rather than wrapping, because wrapping is the
 * exact failure being fixed. The negative gutters let the bar span the full width of the
 * page shell the way the app header above it does, and the background is opaque for the
 * same reason that header's is -- content scrolling under a translucent bar is the one
 * thing a section nav must never make hard to read.
 */
function SectionNav() {
  const [active, setActive] = useState<string>(SECTIONS[0].id)

  useEffect(() => {
    const elements = SECTIONS
      .map((section) => document.getElementById(section.id))
      .filter((element): element is HTMLElement => element !== null)
    if (!elements.length) return

    const observer = new IntersectionObserver(
      (entries) => {
        const onScreen = entries
          .filter((entry) => entry.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)
        if (onScreen[0]) setActive(onScreen[0].target.id)
      },
      // A band just below the two sticky bars. Without the large bottom inset the last
      // section counts as current the moment any sliver of it appears, and the highlight
      // jumps ahead of what is actually being read.
      { rootMargin: '-140px 0px -55% 0px', threshold: 0 },
    )
    elements.forEach((element) => observer.observe(element))
    return () => observer.disconnect()
  }, [])

  return (
    <nav
      aria-label="Sections"
      className="border-hairline bg-background sticky top-16 z-30 -mx-4 mb-6 border-b px-4 sm:-mx-6 sm:px-6 lg:-mx-8 lg:px-8"
    >
      <div className="flex gap-1 overflow-x-auto py-2">
        {SECTIONS.map((section) => (
          <a
            key={section.id}
            href={`#${section.id}`}
            aria-current={active === section.id ? 'location' : undefined}
            className={cn(
              'shrink-0 rounded-lg px-3 py-1.5 text-sm font-medium whitespace-nowrap transition-colors',
              active === section.id
                ? 'bg-primary/10 text-primary dark:text-brand'
                : 'text-muted-foreground hover:text-foreground',
            )}
          >
            {section.label}
          </a>
        ))}
      </div>
    </nav>
  )
}

function StatTile({ label, stats }: { label: string; stats: MediaPeriodStats }) {
  return (
    <div className="ring-hairline bg-card rounded-xl p-4 ring-1">
      <p className="text-muted-foreground text-xs font-semibold tracking-wide uppercase">{label}</p>
      <p className="text-foreground mt-1.5 text-2xl font-bold tabular-nums">{stats.total_plays.toLocaleString()}</p>
      <p className="text-muted-foreground mt-0.5 text-xs">
        {stats.total_plays ? `${stats.success_percent}% completed` : 'No plays recorded'}
      </p>
    </div>
  )
}

export default function AdDetailPage() {
  const params = useParams()
  const contentId = Number(params.id)
  const [editModalOpen, setEditModalOpen] = useState(false)
  const user = useAuthStore((state) => state.user)
  const canEdit = canEditTenantContent(user)

  // One row, not the whole library. The page used to fetch every asset and pick this one
  // out in the browser, so anything past the listing's 500-row cap rendered "not found".
  const contentQuery = useQuery({
    queryKey: ['content', contentId],
    queryFn: () => api.getContentItem(contentId),
    enabled: Number.isFinite(contentId),
  })
  // The same key AdBookings uses, so mounting both costs one request. This is the
  // authoritative commercial record -- price, payment, per-location windows -- and the
  // header above the tabs is built from it rather than from the content row's summary.
  const placementsQuery = useQuery({
    queryKey: ['placements', contentId],
    queryFn: () => api.getPlacements(contentId),
    enabled: Number.isFinite(contentId),
  })
  const groupsQuery = useQuery({ queryKey: ['groups'], queryFn: api.getGroups })
  const playlistsQuery = useQuery({ queryKey: ['playlists'], queryFn: api.getPlaylists })
  const screensQuery = useQuery({ queryKey: ['screens'], queryFn: api.getScreens })
  const reportQuery = useQuery({
    queryKey: ['media-report', contentId],
    queryFn: () => api.getMediaReport(contentId),
    enabled: Number.isFinite(contentId),
    refetchInterval: 10000,
  })

  const item = contentQuery.data
  const groupName = (id: number | null | undefined) =>
    groupsQuery.data?.find((group) => group.id === id)?.name

  // Highest id, matching what serialize_content reports as the asset's current booking.
  const booking = useMemo(() => {
    const rows = placementsQuery.data || []
    return rows.length ? [...rows].sort((a, b) => b.id - a.id)[0] : null
  }, [placementsQuery.data])

  /**
   * Where this ad is scheduled right now, independent of play history.
   *
   * Proof-of-play only exists once a TV has actually played something, so a freshly
   * scheduled advert would otherwise look like it is running nowhere. This answers
   * "is it live?" from the assignment graph instead.
   */
  const scheduledOn = useMemo(() => {
    const playlists = (playlistsQuery.data || []) as Playlist[]
    const screens = (screensQuery.data || []) as Screen[]
    const playlistIds = new Set(
      playlists.filter((playlist) => (playlist.items || []).some((entry) => entry.content_id === contentId))
        .map((playlist) => playlist.id),
    )

    const matchedScreenIds = new Set(
      screens.filter((screen) => screen.effective_playlist_id && playlistIds.has(screen.effective_playlist_id)).map((s) => s.id),
    )

    // Screens directly or via groups targeted by active commercial bookings
    if (booking?.targets) {
      for (const target of booking.targets) {
        if (target.screen_id) {
          matchedScreenIds.add(target.screen_id)
        } else if (target.group_id) {
          for (const s of screens) {
            if (s.group_id === target.group_id) matchedScreenIds.add(s.id)
          }
        }
      }
    }

    // Screens recorded in playback history
    if (reportQuery.data?.per_screen) {
      for (const row of reportQuery.data.per_screen) {
        if (row.total_plays > 0) matchedScreenIds.add(row.screen_id)
      }
    }

    return screens.filter((screen) => matchedScreenIds.has(screen.id))
  }, [playlistsQuery.data, screensQuery.data, contentId, booking, reportQuery.data])

  const activeLocations = useMemo(() => {
    const locs = new Set<string>()
    for (const screen of scheduledOn) {
      if (screen.location?.trim()) locs.add(screen.location.trim())
    }
    if (booking?.targets) {
      for (const t of booking.targets) {
        if (t.location?.trim()) locs.add(t.location.trim())
      }
    }
    return Array.from(locs)
  }, [scheduledOn, booking])

  const placesScheduled = useMemo(
    () => new Set(scheduledOn.map((screen) => screen.group_id ?? `ungrouped-${screen.id}`)).size,
    [scheduledOn],
  )

  // The assignment graph is two queries deep; if either failed, scheduledOn is [] and
  // saying "not scheduled anywhere" would be stating a failure as a fact.
  const placementUnknown = playlistsQuery.isError || screensQuery.isError

  const backLink = (
    <Link href="/dashboard/content" className="text-muted-foreground hover:text-foreground mb-4 inline-flex items-center gap-2 text-sm font-medium">
      <ArrowLeft className="size-4" aria-hidden="true" /> Back to content library
    </Link>
  )

  if (contentQuery.isError) {
    return <ErrorState message="This asset could not be loaded." onRetry={() => contentQuery.refetch()} />
  }
  if (contentQuery.isLoading) {
    return (
      <div>
        <Skeleton className="mb-6 h-44 rounded-2xl" />
        <Skeleton className="mb-6 h-28 rounded-2xl" />
        <Skeleton className="h-96 rounded-2xl" />
      </div>
    )
  }
  if (!item) {
    return (
      <div>
        {backLink}
        <EmptyState icon={Film} title="Asset not found" description="It may have been deleted from the library." action={<Button variant="outline" render={<Link href="/dashboard/content" />}>View library</Button>} />
      </div>
    )
  }

  const report = reportQuery.data
  const orientation = assetOrientation(item.renditions)
  const duration = clipDuration(item.duration_ms)
  const online = scheduledOn.filter((screen) => screen.status === 'online').length
  const state = booking ? bookingState(booking) : null

  // One derivation, shared with the bookings section below, the invoices page and the
  // invoice PDF. Subtracting it here as well is how this tile came to read "Paid in full"
  // over a booking the section underneath it showed as owing ₹45,000.
  const bill = booking ? money(booking) : null

  // The four commercial facts. Every one of these used to be a tab click away, or absent
  // from the page entirely, on the screen where an operator answers "what did we sell?".
  const kpis = booking
    ? [
        {
          label: 'Booked window',
          icon: CalendarRange,
          value: booking.days_remaining != null ? `${Math.max(0, booking.days_remaining)}d left` : '—',
          note: `${asDate(booking.starts_at)} → ${asDate(booking.effective_ends_at || booking.ends_at)}${booking.extensions.length ? ' · extended' : ''}`,
        },
        {
          label: 'Contract value',
          icon: IndianRupee,
          value: rupees(bill!.total),
          note: bill!.label,
        },
        {
          label: 'On air',
          icon: MonitorPlay,
          value: String(booking.screens_used),
          note: booking.plan_max_locations > 0
            ? `of ${booking.plan_max_locations} on the ${booking.plan?.name || 'plan'}`
            // "on this plan" reads as nonsense on a booking sold without one.
            : booking.plan ? 'No location cap on this plan' : 'Sold without a package',
        },
        {
          label: 'Plays',
          icon: Film,
          value: (report?.lifetime.total_plays ?? 0).toLocaleString(),
          note: report?.lifetime.total_plays
            ? `${report.lifetime.success_percent}% completed`
            : 'No plays recorded yet',
        },
      ]
    : []

  return (
    <div>
      {backLink}

      {/* Hero: what the creative is, who bought it, and where it stands. Absorbs the
          separate "client overview" card that used to restate these badges below, with a
          second Edit button pointing at the same modal. */}
      <header className="bg-secondary/50 ring-hairline mb-6 rounded-2xl p-4 ring-1 sm:p-5">
        <div className="flex flex-wrap items-start gap-5">
          {/* Full width until sm, then a fixed thumbnail beside the text. Left at w-56 the
              224px thumbnail never wrapped on a phone, so the title column was squeezed to
              a strip and the edit button ran off the side. */}
          <div className="bg-muted aspect-video w-full shrink-0 overflow-hidden rounded-xl sm:w-56">
            <MediaThumbnail item={item} className="size-full" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <h1 className="text-foreground text-2xl font-bold tracking-tight break-words">{item.name}</h1>
                <p className="text-muted-foreground mt-1 text-sm">
                  <span className="capitalize">{item.type}</span>
                  {orientation && ` • ${orientation}`}
                  {duration && ` • ${duration}`}
                  {` • added ${relativeTime(item.uploaded_at)}`}
                </p>
              </div>
              {canEdit && (
                <Button variant="outline" size="sm" onClick={() => setEditModalOpen(true)}>
                  <Building2 data-icon="inline-start" /> Edit client &amp; ad details
                </Button>
              )}
            </div>

            <div className="mt-3 flex flex-wrap items-center gap-2">
              <Badge variant="outline">
                <Building2 className="size-3" aria-hidden="true" /> {item.client_name || 'Direct advertiser'}
              </Badge>
              {item.plan_name
                ? <Badge variant="secondary">{item.plan_name}</Badge>
                // Sold on a negotiated price rather than a package. Rendering nothing
                // here made a custom sale look identical to an advert nobody has bought.
                : booking && <Badge variant="outline">Custom — no package</Badge>}
              {state && <Badge variant={state.tone}>{state.label}</Badge>}
              {item.status === 'ready' && <Badge variant="success"><CheckCircle2 className="size-3" aria-hidden="true" /> Ready to play</Badge>}
              {item.status === 'processing' && <Badge variant="warning">Still processing</Badge>}
              {item.status === 'failed' && <Badge variant="danger"><TriangleAlert className="size-3" aria-hidden="true" /> Processing failed</Badge>}
              {item.tags?.split(',').map((tag) => tag.trim()).filter(Boolean).map((tag) => (
                <Badge key={tag} variant="secondary">{tag}</Badge>
              ))}
            </div>

            {(item.client_email || item.client_phone) && (
              <p className="text-muted-foreground mt-2 truncate text-sm">
                {item.client_email}
                {item.client_email && item.client_phone && ' • '}
                {item.client_phone}
              </p>
            )}

            <p className="text-foreground mt-4 text-sm">
              {placementUnknown ? (
                <span className="text-muted-foreground">Could not check which screens this is on.</span>
              ) : scheduledOn.length === 0 ? (
                <span className="text-muted-foreground">Not scheduled on any screen yet.</span>
              ) : (
                <>
                  Scheduled on <strong>{scheduledOn.length} screen{scheduledOn.length === 1 ? '' : 's'}</strong>
                  {activeLocations.length > 0 ? (
                    <>
                      {' across '}
                      <strong>
                        {activeLocations.length} location{activeLocations.length === 1 ? `: ${activeLocations[0]}` : `s (${activeLocations.slice(0, 2).join(', ')}${activeLocations.length > 2 ? '…' : ''})`}
                      </strong>
                    </>
                  ) : placesScheduled > 0 ? (
                    <>
                      {' across '}
                      <strong>{placesScheduled} group{placesScheduled === 1 ? '' : 's'}</strong>
                    </>
                  ) : null}
                  {' — '}
                  <span className={online ? 'text-emerald-600 dark:text-emerald-400' : 'text-muted-foreground'}>
                    {online} online now
                  </span>
                </>
              )}
            </p>
          </div>
        </div>
      </header>

      {booking ? (
        <section aria-label="Booking summary" className="mb-6 grid grid-cols-2 gap-4 lg:grid-cols-4">
          {kpis.map(({ label, value, icon: Icon, note }) => (
            <Card
              key={label}
              className="ring-hairline bg-card border-0 py-0 shadow-[0_1px_2px_rgba(15,23,42,.04)] ring-1"
            >
              <CardContent className="p-4 sm:p-5">
                <div className="flex items-start justify-between gap-2">
                  <p className="text-muted-foreground text-sm font-medium">{label}</p>
                  <span className="bg-primary/10 text-primary dark:text-brand grid size-8 shrink-0 place-items-center rounded-lg sm:size-9 sm:rounded-xl">
                    <Icon className="size-4" aria-hidden="true" />
                  </span>
                </div>
                <p className="text-foreground mt-4 text-2xl font-semibold tracking-[-0.04em] tabular-nums sm:mt-5 sm:text-3xl">{value}</p>
                <p className="text-muted-foreground/70 mt-1 text-xs">{note}</p>
              </CardContent>
            </Card>
          ))}
        </section>
      ) : placementsQuery.isError ? (
        <div className="mb-6">
          <ErrorState message="This advert's booking could not be loaded." onRetry={() => placementsQuery.refetch()} />
        </div>
      ) : !placementsQuery.isLoading ? (
        <div className="ring-hairline bg-card mb-6 flex flex-wrap items-center justify-between gap-3 rounded-2xl p-5 shadow-sm ring-1">
          <div className="flex items-center gap-2.5">
            <span className="bg-primary/10 text-primary dark:text-brand grid size-9 place-items-center rounded-xl">
              <Receipt className="size-4" aria-hidden="true" />
            </span>
            <div>
              <p className="text-foreground text-sm font-semibold">Not sold to anyone yet</p>
              <p className="text-muted-foreground text-xs">Record who is paying for this advert, for how long, and where it runs.</p>
            </div>
          </div>
        </div>
      ) : null}

      {item.status === 'failed' && (
        <div className="border-destructive/30 bg-destructive/5 mb-6 rounded-xl border p-4">
          <p className="text-destructive text-sm font-semibold">This asset cannot play</p>
          <p className="text-muted-foreground mt-1 text-sm">{item.failed_reason || 'Processing did not complete.'}</p>
          <Button variant="outline" size="sm" className="mt-3" render={<Link href="/dashboard/content" />}>Retry from the library</Button>
        </div>
      )}

      <SectionNav />

      <section id="booking" aria-labelledby="booking-title" className="mb-8 scroll-mt-32">
        <h2 id="booking-title" className="text-foreground mb-4 flex items-center gap-2 text-lg font-semibold">
          <Receipt className="text-primary dark:text-brand size-5" aria-hidden="true" /> Booking &amp; billing
        </h2>
        <AdBookings contentId={contentId} />
      </section>

      <section id="locations" aria-labelledby="locations-title" className="mb-8 scroll-mt-32">
        <h2 id="locations-title" className="text-foreground mb-4 flex items-center gap-2 text-lg font-semibold">
          <MapPin className="text-primary dark:text-brand size-5" aria-hidden="true" /> Where it plays
        </h2>
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          <Card className="ring-hairline bg-card border-0 ring-1">
            <CardContent className="p-5">
              <h3 className="text-foreground mb-4 flex items-center gap-2 font-semibold">
                <CalendarRange className="text-primary dark:text-brand size-4" aria-hidden="true" /> Sold schedule per location
              </h3>
              {!booking?.targets.length ? (
                scheduledOn.length > 0 ? (
                  <div>
                    <p className="text-muted-foreground mb-3 text-sm">
                      Running across <strong>{scheduledOn.length} active screen{scheduledOn.length === 1 ? '' : 's'}</strong>
                      {activeLocations.length > 0 && <> in <strong>{activeLocations.join(', ')}</strong></>}. No commercial booking package attached.
                    </p>
                    <div className="divide-hairline divide-y">
                      {scheduledOn.map((screen) => (
                        <div key={screen.id} className="flex items-center justify-between gap-3 py-2.5">
                          <div className="flex min-w-0 flex-1 items-center gap-2.5">
                            <span className="bg-primary/10 text-primary dark:text-brand grid size-8 shrink-0 place-items-center rounded-lg">
                              <MapPin className="size-4" aria-hidden="true" />
                            </span>
                            <div className="min-w-0">
                              <p className="text-foreground truncate text-sm font-medium">
                                {screen.location || screen.name || `Screen ${screen.id}`}
                              </p>
                              <p className="text-muted-foreground truncate text-xs">
                                {screen.name}{screen.location ? ` • ${screen.location}` : ' • No location specified'}
                              </p>
                            </div>
                          </div>
                          <Badge variant={screen.status === 'online' ? 'success' : 'outline'}>
                            {screen.status === 'online' ? 'Online' : 'Offline'}
                          </Badge>
                        </div>
                      ))}
                    </div>
                  </div>
                ) : (
                  <p className="text-muted-foreground text-sm">
                    This advert is not booked or scheduled in any location yet.
                  </p>
                )
              ) : (
                <div className="divide-hairline divide-y">
                  {booking.targets.map((target) => {
                    const matchedScreen = (screensQuery.data || []).find((s) => s.id === target.screen_id)
                    const loc = target.location || matchedScreen?.location
                    return (
                      <div key={target.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
                        <div className="flex min-w-0 flex-1 items-center gap-2.5">
                          <span className="bg-primary/10 text-primary dark:text-brand grid size-8 shrink-0 place-items-center rounded-lg">
                            <MapPin className="size-4" aria-hidden="true" />
                          </span>
                          <div className="min-w-0">
                            <p className="text-foreground truncate text-sm font-medium">
                              {loc || target.name}
                            </p>
                            <p className="text-muted-foreground truncate text-xs">
                              {loc ? `${target.name} • ` : ''}
                              {target.starts_at && target.ends_at
                                ? `${asDate(target.starts_at)} → ${asDate(target.ends_at)}`
                                : 'Follows the booking window'}
                            </p>
                          </div>
                        </div>
                        <div className="flex shrink-0 items-center gap-2">
                          {target.days != null && <Badge variant="secondary">{target.days} days</Badge>}
                          {!target.is_placed && <Badge variant="warning">Removed by hand</Badge>}
                        </div>
                      </div>
                    )
                  })}
                </div>
              )}
            </CardContent>
          </Card>

          <Card className="ring-hairline bg-card border-0 ring-1">
            <CardContent className="p-5">
              <h3 className="text-foreground mb-4 flex items-center gap-2 font-semibold">
                <MonitorPlay className="text-primary dark:text-brand size-4" aria-hidden="true" /> Screens running this ad
              </h3>
              {placementUnknown ? (
                <ErrorState
                  message="The list of screens could not be loaded."
                  onRetry={() => { playlistsQuery.refetch(); screensQuery.refetch() }}
                />
              ) : scheduledOn.length === 0 ? (
                <p className="text-muted-foreground text-sm">
                  Not scheduled anywhere. Add it to a playlist from a screen or group page.
                </p>
              ) : (
                <div className="divide-hairline divide-y">
                  {scheduledOn.map((screen) => {
                    const played = report?.per_screen.find((row) => row.screen_id === screen.id)
                    return (
                      <Link
                        key={screen.id}
                        href={`/dashboard/screens/${screen.id}`}
                        className="hover:bg-muted/50 -mx-2 flex items-center justify-between gap-4 rounded-lg px-2 py-3"
                      >
                        <div className="min-w-0">
                          <p className="text-foreground truncate text-sm font-medium">{screen.name || `Screen ${screen.id}`}</p>
                          {/* The place, not just the grouping. This row answered "where
                              does my ad play?" with the group name -- or "Not in a group"
                              -- while the screen's actual location sat unused on the
                              record, readable only by hovering a pin on the map below.
                              Location first, group as the fallback. */}
                          <p className="text-muted-foreground truncate text-xs">
                            {screen.location || groupName(screen.group_id) || 'No location set'}
                            {played?.last_played && ` • last played ${relativeTime(played.last_played)}`}
                          </p>
                        </div>
                        <div className="flex shrink-0 items-center gap-3">
                          <span className="text-foreground font-mono text-sm tabular-nums">
                            {(played?.total_plays ?? 0).toLocaleString()}
                          </span>
                          <Badge variant={screen.status === 'online' ? 'success' : 'outline'}>
                            {screen.status === 'online' ? 'Online' : 'Offline'}
                          </Badge>
                        </div>
                      </Link>
                    )
                  })}
                </div>
              )}
            </CardContent>
          </Card>
        </div>
        <div className="mt-6">
        <Card className="ring-hairline bg-card border-0 ring-1">
          <CardContent className="p-5">
            <h3 className="text-foreground mb-4 flex items-center gap-2 font-semibold">
              <MapPin className="text-primary dark:text-brand size-4" aria-hidden="true" /> Where this ad is on screen
            </h3>
            <ScreenMap points={scheduledOn.map((screen) => {
              const played = report?.per_screen.find((row) => row.screen_id === screen.id)
              return {
                id: screen.id,
                name: screen.name || `Screen ${screen.id}`,
                location: screen.location,
                latitude: screen.latitude,
                longitude: screen.longitude,
                online: screen.status === 'online',
                detail: `${(played?.total_plays ?? 0).toLocaleString()} plays`,
              }
            })} height={260} />
          </CardContent>
        </Card>
        </div>
      </section>

      <section id="performance" aria-labelledby="performance-title" className="mb-8 scroll-mt-32">
        <h2 id="performance-title" className="text-foreground mb-4 flex items-center gap-2 text-lg font-semibold">
          <BarChart3 className="text-primary dark:text-brand size-5" aria-hidden="true" /> Performance
        </h2>
        {reportQuery.isError ? (
          <ErrorState message="The playback report could not be loaded." onRetry={() => reportQuery.refetch()} />
        ) : reportQuery.isLoading ? (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-4">{Array.from({ length: 4 }).map((_, index) => <Skeleton key={index} className="h-24 rounded-xl" />)}</div>
        ) : !report ? (
          <ErrorState message="The playback report could not be loaded." onRetry={() => reportQuery.refetch()} />
        ) : (
          <div className="space-y-6">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <p className="text-muted-foreground text-xs">Proof-of-play metrics auto-update in real time as screens report in.</p>
              <Button
                variant="outline"
                size="sm"
                onClick={() => reportQuery.refetch()}
                disabled={reportQuery.isFetching}
                className="bg-card h-8 text-xs"
              >
                <RefreshCw data-icon="inline-start" className={reportQuery.isFetching ? 'animate-spin' : undefined} />
                Refresh
              </Button>
            </div>

            <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
              <StatTile label="Today" stats={report.today} />
              <StatTile label="This week" stats={report.week} />
              <StatTile label="This month" stats={report.month} />
              <StatTile label="Lifetime" stats={report.lifetime} />
            </div>

            <Card className="ring-hairline bg-card border-0 ring-1">
              <CardContent className="p-5">
                <h3 className="text-foreground mb-4 font-semibold">Plays over the last 30 days</h3>
                {report.daily.length === 0 ? (
                  <p className="text-muted-foreground py-10 text-center text-sm">
                    No plays recorded yet. Figures appear once a screen reports playback.
                  </p>
                ) : (
                  <div className="h-64">
                    <ResponsiveContainer width="100%" height="100%">
                      <AreaChart data={report.daily}>
                        <defs>
                          <linearGradient id="plays" x1="0" y1="0" x2="0" y2="1">
                            <stop offset="0%" stopColor="var(--color-primary)" stopOpacity={0.35} />
                            <stop offset="100%" stopColor="var(--color-primary)" stopOpacity={0} />
                          </linearGradient>
                        </defs>
                        <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" vertical={false} />
                        <XAxis dataKey="date" tick={{ fontSize: 12 }} stroke="var(--color-muted-foreground)" />
                        <YAxis allowDecimals={false} tick={{ fontSize: 12 }} stroke="var(--color-muted-foreground)" />
                        <Tooltip
                          contentStyle={{
                            background: 'var(--color-card)',
                            border: '1px solid var(--color-border)',
                            borderRadius: 12,
                            color: 'var(--color-foreground)',
                          }}
                        />
                        <Area type="monotone" dataKey="total_plays" name="Plays" stroke="var(--color-primary)" fill="url(#plays)" strokeWidth={2} />
                      </AreaChart>
                    </ResponsiveContainer>
                  </div>
                )}
              </CardContent>
            </Card>
          </div>
        )}
        <div className="mt-6">
        <Card className="ring-hairline bg-card border-0 ring-1">
          <CardContent className="p-5">
            <h3 className="text-foreground mb-4 flex items-center gap-2 font-semibold">
              <MapPin className="text-primary dark:text-brand size-4" aria-hidden="true" /> Plays by location
            </h3>
            {!report?.per_location.length ? (
              <p className="text-muted-foreground text-sm">
                No plays reported yet. Put screens into groups so results roll up per venue.
              </p>
            ) : (
              <div className="divide-hairline divide-y">
                {report.per_location.map((place) => (
                  <div key={place.location} className="flex items-center justify-between gap-4 py-3">
                    <div className="min-w-0">
                      <p className="text-foreground truncate text-sm font-medium">{place.location}</p>
                      <p className="text-muted-foreground text-xs">{place.screens} screen{place.screens === 1 ? '' : 's'}</p>
                    </div>
                    <span className="text-foreground shrink-0 font-mono text-sm tabular-nums">{place.total_plays.toLocaleString()}</span>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
        </div>
      </section>

      <section id="technical" aria-labelledby="technical-title" className="mb-8 scroll-mt-32">
        <h2 id="technical-title" className="text-foreground mb-4 flex items-center gap-2 text-lg font-semibold">
          <Film className="text-primary dark:text-brand size-5" aria-hidden="true" /> Technical
        </h2>
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          <Card className="ring-hairline bg-card border-0 ring-1">
            <CardContent className="p-5">
              <h3 className="text-foreground mb-4 font-semibold">Source file</h3>
              <dl className="divide-hairline divide-y text-sm">
                {[
                  ['Type', <span key="t" className="capitalize">{item.type}</span>],
                  ['Duration', duration || '—'],
                  ['Orientation', orientation || 'Unknown until transcoded'],
                  ['File size', formatBytes(item.file_size_bytes)],
                  ['Status', <span key="s" className="capitalize">{item.status}</span>],
                  ['Uploaded', relativeTime(item.uploaded_at)],
                ].map(([label, value]) => (
                  <div key={String(label)} className="flex justify-between gap-4 py-2.5">
                    <dt className="text-muted-foreground">{label}</dt>
                    <dd className="text-foreground font-medium">{value}</dd>
                  </div>
                ))}
              </dl>
            </CardContent>
          </Card>

          <Card className="ring-hairline bg-card border-0 ring-1">
            <CardContent className="p-5">
              <h3 className="text-foreground mb-4 font-semibold">Transcoded renditions</h3>
              {!item.renditions?.length ? (
                <p className="text-muted-foreground text-sm">
                  {item.type === 'image'
                    ? 'Images are sent to screens as uploaded — no transcoding needed.'
                    : 'No renditions yet. They are produced when processing completes.'}
                </p>
              ) : (
                <div className="divide-hairline divide-y text-sm">
                  {[...item.renditions]
                    .sort((a, b) => b.width * b.height - a.width * a.height)
                    .map((rendition) => (
                      <div key={rendition.id} className="flex items-center justify-between gap-4 py-2.5">
                        <div className="min-w-0">
                          <p className="text-foreground font-medium">{rendition.resolution}</p>
                          <p className="text-muted-foreground text-xs">
                            {rendition.width}×{rendition.height}
                            {rendition.codec && ` • ${rendition.codec}`}
                          </p>
                        </div>
                        <span className="text-muted-foreground shrink-0 font-mono text-xs tabular-nums">
                          {formatBytes(rendition.file_size_bytes)}
                        </span>
                      </div>
                    ))}
                </div>
              )}
              <p className="text-muted-foreground/70 mt-4 text-xs">
                Each screen is sent the largest rendition its decoder, memory and panel can handle.
              </p>
            </CardContent>
          </Card>
        </div>
      </section>

      {canEdit && (
        <EditClientAdModal open={editModalOpen} onOpenChange={setEditModalOpen} contentItem={item} />
      )}
    </div>
  )
}
