'use client'

import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import Link from 'next/link'
import { useParams, useRouter } from 'next/navigation'
import { ArrowLeft, ChevronDown, Clock, Info, ListVideo, Loader2, MapPin, Monitor, MonitorPlay, MoreVertical, PlaySquare, Settings, Smartphone, Trash2, Tv2 } from 'lucide-react'
import { EmptyState } from '@/components/dashboard/empty-state'
import { ErrorState } from '@/components/dashboard/error-state'
import { OverlayBadge } from '@/components/dashboard/asset-card'
import { PlaylistBuilder } from '@/components/dashboard/playlist-builder'
import { ScreenDetailsDrawer } from '@/components/dashboard/screen-details-drawer'
import { ScreenHoursDialog } from '@/components/dashboard/screen-hours-dialog'
import { KioskExitCard } from '@/components/dashboard/kiosk-exit-card'
import { ScreenMap } from '@/components/dashboard/screen-map'
import { ScreenSettingsDialog } from '@/components/dashboard/screen-settings-dialog'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { Skeleton } from '@/components/ui/skeleton'
import { api } from '@/lib/api'
import { relativeTime } from '@/lib/format'
import { canEditTenantContent } from '@/lib/roles'
import { useAuthStore } from '@/lib/store'
import type { Screen } from '@/lib/types'

const orientationLabel = (degrees: number) =>
  degrees === 90 || degrees === 270 ? 'Portrait orientation' : 'Landscape orientation'

/**
 * Always renders the PlaylistBuilder for a screen. If the screen has no playlist yet
 * (pre-migration data), it calls ensure-playlist to provision one on the fly, then
 * refreshes the screens query so the page re-renders with a valid playlist id.
 */
function ScreenPlaylistSection({ screen, screenId, inherited }: { screen: Screen; screenId: number; inherited: boolean }) {
  const queryClient = useQueryClient()

  const ensureMutation = useMutation({
    mutationFn: () => api.ensureScreenPlaylist(screenId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['screens'] }),
    onError: (error: Error) => toast.error(`Could not provision playlist: ${error.message}`),
  })

  useEffect(() => {
    if (!screen.effective_playlist_id && !ensureMutation.isPending && !ensureMutation.isSuccess && !ensureMutation.isError) {
      ensureMutation.mutate()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [screen.effective_playlist_id])

  if (!screen.effective_playlist_id) {
    // Say what went wrong rather than spinning forever. Retried on demand, not on a loop:
    // if the call is failing, hammering it once per render helps nobody.
    if (ensureMutation.isError) {
      return (
        <EmptyState
          icon={ListVideo}
          title="This screen has no playlist yet"
          description="It could not be set up automatically. Try again, or assign a playlist from the screen's group."
          action={<Button variant="outline" onClick={() => ensureMutation.reset()}>Try again</Button>}
        />
      )
    }
    return (
      <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
        <Loader2 className="size-8 animate-spin mb-3" />
        <p className="text-sm font-medium">Setting up playlist…</p>
      </div>
    )
  }

  return (
    <div className="space-y-5">
      {inherited && (
        <div className="border-hairline bg-secondary/50 text-muted-foreground rounded-xl border p-4 text-sm">
          This loop comes from the screen&apos;s group, so changes here apply to{' '}
          <strong className="text-foreground font-medium">every screen in that group</strong>.{' '}
          <Link href="/dashboard/groups" className="text-primary dark:text-brand underline underline-offset-2">Manage groups</Link>
        </div>
      )}
      <PlaylistBuilder playlistId={screen.effective_playlist_id} showHeader={false} screenId={screen.id} />
    </div>
  )
}

export default function ScreenDetailPage() {
  const params = useParams()
  const router = useRouter()
  const screenId = Number(params.id)
  const user = useAuthStore((state) => state.user)
  const canEdit = canEditTenantContent(user)

  const screensQuery = useQuery({ queryKey: ['screens'], queryFn: api.getScreens })
  const screens = useMemo(() => screensQuery.data || [], [screensQuery.data])
  const screen: Screen | undefined = screens.find((s) => s.id === screenId)

  const [settingsOpen, setSettingsOpen] = useState(false)
  const [hoursOpen, setHoursOpen] = useState(false)
  const [detailsOpen, setDetailsOpen] = useState(false)
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [mapOpen, setMapOpen] = useState(false)

  const bringToFrontMutation = useMutation({
    mutationFn: () => api.bringToFront(screenId),
    onSuccess: () => toast.success('Command sent: opening signage app on screen.'),
    onError: (error: Error) => toast.error(error.message),
  })

  const deleteMutation = useMutation({
    mutationFn: () => api.deleteScreen(screenId),
    onSuccess: () => {
      toast.success('Display removed and unpairing signal sent')
      router.push('/dashboard/screens')
    },
    onError: (error: Error) => toast.error(error.message),
  })

  const backLink = (
    <Link href="/dashboard/screens" className="text-muted-foreground hover:text-foreground inline-flex items-center gap-2 text-sm font-medium">
      <ArrowLeft className="size-4" /> Back to screens
    </Link>
  )

  if (screensQuery.isError) {
    return <ErrorState message="This screen could not be loaded." onRetry={() => screensQuery.refetch()} />
  }

  if (screensQuery.isLoading) {
    return <div className="space-y-8"><Skeleton className="h-44 rounded-2xl" /><Skeleton className="h-20 rounded-2xl" /><Skeleton className="h-[480px] rounded-2xl" /></div>
  }

  if (!screen) {
    return (
      <div className="space-y-4">
        {backLink}
        <EmptyState icon={Tv2} title="Screen not found" description="It may have been removed from this workspace, or the link is out of date." action={<Button variant="outline" render={<Link href="/dashboard/screens" />}>View all screens</Button>} />
      </div>
    )
  }

  const label = screen.name || `Screen ${screen.id}`
  const online = screen.status === 'online'
  // A screen with no playlist of its own can still be playing one inherited from its
  // group; editing that loop changes every screen in the group, so it is called out.
  const inherited = !screen.playlist_id && Boolean(screen.effective_playlist_id)

  return (
    <div className="space-y-8">
      <div className="space-y-4">
        {backLink}

        <header className="ring-hairline bg-card rounded-2xl p-5 ring-1 sm:p-6">
          <div className="flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">
            <div className="flex min-w-0 flex-col gap-4 sm:flex-row sm:items-start sm:gap-5">
              <div className="bg-muted relative aspect-video w-full shrink-0 overflow-hidden rounded-xl sm:w-44">
                <div className="text-muted-foreground/40 grid size-full place-items-center"><MonitorPlay className="size-8" aria-hidden="true" /></div>
                <div className="absolute bottom-2 left-2">
                  <OverlayBadge tone={online ? 'online' : 'offline'}>{online ? 'Online' : 'Offline'}</OverlayBadge>
                </div>
              </div>

              <div className="min-w-0">
                <h1 className="text-foreground truncate text-2xl font-semibold tracking-[-0.03em] sm:text-3xl">{label}</h1>
                {screen.description && <p className="text-muted-foreground mt-1.5 text-sm">{screen.description}</p>}
                <div className="text-muted-foreground mt-3 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-sm">
                  <span>{online ? 'Online now' : `Last seen ${relativeTime(screen.last_seen)}`}</span>
                  <span className="flex items-center gap-1.5">
                    {screen.orientation === 90 || screen.orientation === 270
                      ? <Smartphone className="size-4" aria-hidden="true" />
                      : <Monitor className="size-4" aria-hidden="true" />}
                    {orientationLabel(screen.orientation ?? 0)}
                  </span>
                  {screen.app_version && <span>Player v{screen.app_version}</span>}
                </div>
              </div>
            </div>

            {/* Two real buttons and an overflow, rather than five look-alike text links. */}
            <div className="flex shrink-0 items-center gap-2">
              {canEdit && (
                <Button className="flex-1 sm:flex-none" onClick={() => bringToFrontMutation.mutate()} disabled={bringToFrontMutation.isPending}>
                  <PlaySquare data-icon="inline-start" /> {bringToFrontMutation.isPending ? 'Opening…' : 'Open app on TV'}
                </Button>
              )}
              {/* Details lives in the menu at every width. Hiding it only below sm would
                  leave a viewer -- who has no edit items -- with an empty menu on a laptop. */}
              <DropdownMenu>
                <DropdownMenuTrigger render={<Button variant="outline" size="icon" aria-label="More actions" />}>
                  <MoreVertical />
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem onClick={() => setDetailsOpen(true)}>
                    <Info aria-hidden="true" /> Details
                  </DropdownMenuItem>
                  {canEdit && (
                    <>
                      <DropdownMenuItem onClick={() => setSettingsOpen(true)}>
                        <Settings aria-hidden="true" /> Settings
                      </DropdownMenuItem>
                      <DropdownMenuItem onClick={() => setHoursOpen(true)}>
                        <Clock aria-hidden="true" /> Operating hours
                      </DropdownMenuItem>
                      <DropdownMenuItem onClick={() => setDeleteOpen(true)} className="text-destructive focus:text-destructive">
                        <Trash2 aria-hidden="true" /> Remove screen
                      </DropdownMenuItem>
                    </>
                  )}
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          </div>
        </header>
      </div>

      {/* Before the timeline: somebody opening this page because they are standing
          in front of a screen they cannot get into needs it first, not last. Paired with
          the map so the two help panels cost one band of height instead of two. */}
      <div className="space-y-3">
        {/* The server withholds the PIN from viewers, so the card would only say "No PIN". */}
        {canEdit && <KioskExitCard screen={screen} />}

        {screen.location && (
          <details
            className="ring-hairline bg-card rounded-2xl ring-1 [&[open]_.chevron]:rotate-180"
            onToggle={(event) => setMapOpen(event.currentTarget.open)}
          >
            <summary className="flex cursor-pointer list-none items-center gap-2.5 p-5 font-semibold sm:p-6 [&::-webkit-details-marker]:hidden">
              <span className="bg-primary/10 text-primary dark:text-brand hidden size-9 shrink-0 place-items-center rounded-lg sm:grid">
                <MapPin className="size-4" aria-hidden="true" />
              </span>
              <span className="text-foreground min-w-0 flex-1 truncate">Location</span>
              <span className="text-muted-foreground hidden shrink-0 text-sm font-normal sm:block">{screen.location}</span>
              <ChevronDown className="chevron text-muted-foreground size-4 shrink-0 transition-transform" aria-hidden="true" />
            </summary>
            <div className="px-5 pb-5 sm:px-6 sm:pb-6">
              {/* Mounted only once open. Leaflet and Google Maps both measure their container
                  on mount, and a container inside a closed <details> measures 0x0. */}
              {mapOpen && (
                <ScreenMap
                  points={[{
                    id: screen.id,
                    name: label,
                    location: screen.location,
                    latitude: screen.latitude,
                    longitude: screen.longitude,
                    online: online,
                  }]}
                  height={280}
                />
              )}
            </div>
          </details>
        )}
      </div>

      {/* No commercial list here. This page is about what the screen PLAYS -- the loop
          below, and the content library it is built from. The bookings sold onto it are a
          commercial record that belongs to the advert, and repeating it here made the page
          two things at once. It lives on the ad detail page, which is where it can be
          edited rather than only stared at. */}

      {/* Every screen gets its own playlist at pairing time. If one is still missing
          (pre-migration data), the ensure-playlist call provisions it on the fly. */}
      <ScreenPlaylistSection screen={screen} screenId={screenId} inherited={inherited} />

      {canEdit && (
        <>
          <ScreenSettingsDialog
            // Remount on close so the form re-reads the saved screen rather than keeping
            // whatever was half-typed last time.
            key={`settings-${settingsOpen}`}
            screen={screen}
            siblings={screens.filter((candidate) => candidate.id !== screen.id)}
            open={settingsOpen}
            onOpenChange={setSettingsOpen}
          />
          <ScreenHoursDialog key={`hours-${hoursOpen}`} screen={screen} open={hoursOpen} onOpenChange={setHoursOpen} />
          <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
            <DialogContent className="sm:max-w-md">
              <DialogHeader>
                <DialogTitle>Remove {label}?</DialogTitle>
                {/* Says what is actually cleared and what is not. "Permanently" was not
                    true: the screen is archived precisely so its play history survives, and
                    that history is what client reports and past invoices are built from. */}
                <DialogDescription>
                  The TV is signed out immediately, clears its content and returns to the
                  pairing screen. Its slot is freed, so you can pair another display in its
                  place. Play history is kept, so past campaign reports and invoices stay
                  correct — and the same TV can be paired again later.
                </DialogDescription>
              </DialogHeader>
              <DialogFooter className="gap-2 sm:gap-0">
                <Button variant="outline" onClick={() => setDeleteOpen(false)}>Cancel</Button>
                <Button
                  variant="destructive"
                  disabled={deleteMutation.isPending}
                  onClick={() => deleteMutation.mutate()}
                >
                  {deleteMutation.isPending ? 'Removing…' : 'Remove Display'}
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        </>
      )}
      <ScreenDetailsDrawer screen={screen} open={detailsOpen} onOpenChange={setDetailsOpen} />
    </div>
  )
}
