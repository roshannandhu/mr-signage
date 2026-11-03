'use client'

import { useMemo, useState, useSyncExternalStore } from 'react'
import Link from 'next/link'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowUpDown, LayoutGrid, Layers3, List, ListVideo, MonitorPlay, Search, Settings2, Trash2, Tv2 } from 'lucide-react'
import { toast } from 'sonner'
import { AssetCard, AssetGrid, OverlayBadge } from '@/components/dashboard/asset-card'
import { BulkActionBar, SelectAllCheckbox } from '@/components/dashboard/bulk-action-bar'
import { EmptyState } from '@/components/dashboard/empty-state'
import { ErrorState } from '@/components/dashboard/error-state'
import { commonSorts, sortItems, type CommonSort } from '@/components/dashboard/list-toolbar'
import { PageHeader } from '@/components/dashboard/page-header'
import { StatusIndicator } from '@/components/dashboard/status-indicator'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { WorkspaceLockPinCard } from '@/components/dashboard/workspace-lock-pin-card'
import { WorkspaceLockToggleCard } from '@/components/dashboard/workspace-lock-toggle-card'
import { useBulkSelection } from '@/hooks/use-bulk-selection'
import { api } from '@/lib/api'
import { bulkSummary, runBulk } from '@/lib/bulk'
import { relativeTime } from '@/lib/format'
import { canEditTenantContent } from '@/lib/roles'
import { useAuthStore } from '@/lib/store'
import { usePlanFeatures } from '@/lib/use-plan-features'
import { cn } from '@/lib/utils'
import type { Screen, ScreenGroup } from '@/lib/types'

type StatusFilter = 'all' | 'online' | 'offline'
type ViewMode = 'cards' | 'list'
const VIEW_KEY = 'mr.screens.view'
const VIEW_EVENT = 'mr:screens-view'

/**
 * The saved layout choice, read from localStorage without a render-time side effect.
 *
 * useSyncExternalStore gives the server (and the hydration pass) the "cards" default and
 * the client the stored value, so there is no markup mismatch and no setState in an
 * effect — the same approach the theme toggle uses.
 */
function useStoredView(): ViewMode {
  return useSyncExternalStore(
    (onChange) => {
      window.addEventListener(VIEW_EVENT, onChange)
      window.addEventListener('storage', onChange)
      return () => {
        window.removeEventListener(VIEW_EVENT, onChange)
        window.removeEventListener('storage', onChange)
      }
    },
    () => (window.localStorage.getItem(VIEW_KEY) === 'list' ? 'list' : 'cards'),
    () => 'cards',
  )
}

export default function ScreensPage() {
  const queryClient = useQueryClient()
  const user = useAuthStore((state) => state.user)
  const canEdit = canEditTenantContent(user)
  const hasKioskLock = usePlanFeatures().has('kiosk_lock')

  // Screens report in on their own cadence; a slow poll keeps the wall of thumbnails
  // roughly live without hammering the API at 500 screens.
  const screensQuery = useQuery({ queryKey: ['screens'], queryFn: api.getScreens, refetchInterval: 10000 })
  const groupsQuery = useQuery({ queryKey: ['groups'], queryFn: api.getGroups })
  const playlistsQuery = useQuery({ queryKey: ['playlists'], queryFn: api.getPlaylists })

  const screens = useMemo(() => screensQuery.data || [], [screensQuery.data])
  const groups = useMemo(() => (groupsQuery.data || []) as ScreenGroup[], [groupsQuery.data])
  const playlists = playlistsQuery.data || []

  const [search, setSearch] = useState('')
  const [sort, setSort] = useState<CommonSort>('newest')
  const [status, setStatus] = useState<StatusFilter>('all')
  const view = useStoredView()
  const [pairOpen, setPairOpen] = useState(false)
  const [pairCode, setPairCode] = useState('')
  const [deleteTarget, setDeleteTarget] = useState<Screen | null>(null)
  const [bulkDeleteOpen, setBulkDeleteOpen] = useState(false)

  const chooseView = (next: ViewMode) => {
    window.localStorage.setItem(VIEW_KEY, next)
    // Notify this tab: the storage event only fires in *other* tabs.
    window.dispatchEvent(new Event(VIEW_EVENT))
  }

  const searched = useMemo(() => {
    const term = search.trim().toLowerCase()
    if (!term) return screens
    return screens.filter((screen: Screen) => {
      const label = (screen.name || `Screen ${screen.id}`).toLowerCase()
      return label.includes(term) || String(screen.device_id || '').toLowerCase().includes(term)
    })
  }, [screens, search])

  // Counts follow the search box but NOT the status tabs — a tab that only ever reported
  // its own selection would be a mirror, not a picker.
  const counts = useMemo(() => ({
    all: searched.length,
    online: searched.filter((screen: Screen) => screen.status === 'online').length,
    offline: searched.filter((screen: Screen) => screen.status !== 'online').length,
  }), [searched])

  const filtered = useMemo(() => {
    const matches = status === 'all'
      ? searched
      : searched.filter((screen: Screen) => (status === 'online' ? screen.status === 'online' : screen.status !== 'online'))
    return sortItems(matches, sort, (s) => s.name || `Screen ${s.id}`, (s) => s.last_seen)
  }, [searched, status, sort])

  const bulk = useBulkSelection(filtered)
  const groupName = (id: number | null) => groups.find((g) => g.id === id)?.name

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['screens'] })
    queryClient.invalidateQueries({ queryKey: ['groups'] })
  }
  const fail = (error: Error) => toast.error(error.message)

  const pairMutation = useMutation({
    mutationFn: api.pairScreen,
    onSuccess: () => { refresh(); toast.success('Screen paired'); setPairOpen(false); setPairCode('') },
    onError: fail,
  })

  const deleteMutation = useMutation({
    mutationFn: (screenId: number) => api.deleteScreen(screenId),
    onSuccess: () => {
      refresh()
      toast.success('Display removed and unpairing signal sent')
      setDeleteTarget(null)
    },
    onError: fail,
  })

  // Each runs one screen at a time (see runBulk), refreshes whatever the outcome -- a part-failed
  // run has still changed some screens -- and only clears the selection when all of it worked.
  const bulkDelete = useMutation({
    mutationFn: () => runBulk(bulk.selected, (id) => api.deleteScreen(id)),
    onSettled: refresh,
    onSuccess: ({ done, failed }) => {
      setBulkDeleteOpen(false)
      if (failed.length) return void toast.error(bulkSummary('removed', done, failed))
      toast.success(`Removed ${done} displays and sent unpairing signals`)
      bulk.clear()
    },
    onError: fail,
  })

  const bulkAssign = useMutation({
    mutationFn: (playlistId: number) => runBulk(bulk.selected, (id) => api.assignPlaylist(id, playlistId)),
    onSettled: refresh,
    onSuccess: ({ done, failed }) => {
      if (failed.length) return void toast.error(bulkSummary('assigned', done, failed))
      toast.success(`Playlist assigned to ${done} screens`)
      bulk.clear()
    },
    onError: fail,
  })

  const bulkGroup = useMutation({
    mutationFn: (groupId: number) => runBulk(bulk.selected, (id) => api.patchScreen(id, { group_id: groupId })),
    onSettled: refresh,
    onSuccess: ({ done, failed }) => {
      if (failed.length) return void toast.error(bulkSummary('moved', done, failed))
      toast.success(`Moved ${done} screens`)
      bulk.clear()
    },
    onError: fail,
  })

  const addScreen = (
    <Dialog open={pairOpen} onOpenChange={setPairOpen}>
      <DialogTrigger render={<Button />}>Add Screen</DialogTrigger>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Add a screen</DialogTitle>
          <DialogDescription>
            Open MR Signage on the TV and sign in with your account — it joins this workspace on its own. No keyboard on hand? Type the six-digit code it shows.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4 pt-2">
          <div className="space-y-2">
            <Label htmlFor="pair-code">Pairing code</Label>
            <Input
              id="pair-code"
              value={pairCode}
              onChange={(event) => setPairCode(event.target.value.replace(/\D/g, '').slice(0, 6))}
              placeholder="000000"
              inputMode="numeric"
              autoFocus
              className="text-center font-mono text-2xl tracking-[0.4em]"
            />
          </div>
          <Button className="w-full" disabled={pairCode.length !== 6 || pairMutation.isPending} onClick={() => pairMutation.mutate(pairCode)}>
            {pairMutation.isPending ? 'Pairing…' : 'Pair screen'}
          </Button>
          <p className="text-muted-foreground text-center text-xs">
            Rolling out many screens? <Link href="/dashboard/provisioning" className="text-primary dark:text-brand underline underline-offset-2">Use zero-touch provisioning</Link>
          </p>
        </div>
      </DialogContent>
    </Dialog>
  )

  /**
   * Status lives on the page, not behind a Filters menu.
   *
   * "How many of my screens are dark right now" is the question this page exists to
   * answer, and it was two clicks deep. Same visual language as the layout toggle below,
   * so the control row reads as one bar rather than a row of unrelated widgets.
   */
  const statusFilter = (
    <div className="border-hairline bg-card inline-flex items-center rounded-xl border p-0.5" role="group" aria-label="Filter by status">
      {([['all', 'All'], ['online', 'Online'], ['offline', 'Offline']] as const).map(([value, label]) => (
        <button
          key={value}
          onClick={() => setStatus(value)}
          aria-pressed={status === value}
          className={cn(
            'focus-visible:ring-ring flex h-9 cursor-pointer items-center gap-2 rounded-lg px-3.5 text-sm font-medium transition-colors focus-visible:ring-2 focus-visible:outline-none',
            status === value ? 'bg-secondary text-foreground' : 'text-muted-foreground hover:text-foreground',
          )}
        >
          {label}
          <span className="text-muted-foreground/70 tabular-nums">{counts[value]}</span>
        </button>
      ))}
    </div>
  )

  const searchField = (
    <div className="relative w-full sm:w-64">
      <Input
        value={search}
        onChange={(event) => setSearch(event.target.value)}
        placeholder="Search screens"
        aria-label="Search screens"
        className="bg-card h-10 pr-9"
      />
      <Search className="text-muted-foreground pointer-events-none absolute top-1/2 right-3 size-[18px] -translate-y-1/2" aria-hidden="true" />
    </div>
  )

  const sortMenu = (
    <DropdownMenu>
      <DropdownMenuTrigger render={<Button variant="outline" className="bg-card shrink-0" />}>
        <ArrowUpDown data-icon="inline-start" /> Sort
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-64">
        {commonSorts.map((option) => (
          <DropdownMenuItem
            key={option.value}
            onClick={() => setSort(option.value)}
            className={cn(sort === option.value && 'bg-accent text-accent-foreground font-medium')}
          >
            {option.label}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  )

  const viewToggle = (
    <div className="border-hairline bg-card flex shrink-0 items-center rounded-xl border p-0.5" role="group" aria-label="Layout">
      {([['cards', LayoutGrid, 'Card view'], ['list', List, 'List view']] as const).map(([mode, Icon, label]) => (
        <button
          key={mode}
          onClick={() => chooseView(mode)}
          aria-label={label}
          aria-pressed={view === mode}
          className={cn(
            'grid size-9 cursor-pointer place-items-center rounded-lg transition-colors',
            view === mode ? 'bg-secondary text-foreground' : 'text-muted-foreground hover:text-foreground',
          )}
        >
          <Icon className="size-4" aria-hidden="true" />
        </button>
      ))}
    </div>
  )

  if (screensQuery.isError) {
    return <ErrorState message="Your screens could not be loaded." onRetry={() => screensQuery.refetch()} />
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Screens"
        description="Every display paired to this workspace — what it is showing, and whether it has reported in."
        actions={canEdit ? addScreen : <Badge variant="outline">View only</Badge>}
      />

      {hasKioskLock && user?.role === 'owner' && (
        <div className="space-y-3">
          <WorkspaceLockToggleCard />
          <WorkspaceLockPinCard />
        </div>
      )}

      {/* Two groups, not six loose children: nothing here can wrap into a ragged third row. */}
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
        {statusFilter}
        <div className="flex items-center gap-2 lg:ml-auto">
          {searchField}
          {sortMenu}
          {viewToggle}
        </div>
      </div>

      {canEdit && filtered.length > 0 && (
        <SelectAllCheckbox
          checked={bulk.allVisibleSelected}
          indeterminate={bulk.someVisibleSelected}
          onChange={bulk.toggleAll}
          label={`Select all ${filtered.length}`}
        />
      )}

      {canEdit && (
        <BulkActionBar count={bulk.selected.length} noun="screen" onClear={bulk.clear}>
          <DropdownMenu>
            <DropdownMenuTrigger render={<Button size="sm" variant="outline" />}>
              <ListVideo data-icon="inline-start" /> Assign playlist
            </DropdownMenuTrigger>
            <DropdownMenuContent>
              {playlists.length === 0 && <p className="text-muted-foreground px-3 py-2 text-sm">No playlists yet</p>}
              {playlists.map((playlist) => (
                <DropdownMenuItem key={playlist.id} onClick={() => bulkAssign.mutate(playlist.id)}>
                  {playlist.name}
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>

          <DropdownMenu>
            <DropdownMenuTrigger render={<Button size="sm" variant="outline" />}>
              <Layers3 data-icon="inline-start" /> Add to group
            </DropdownMenuTrigger>
            <DropdownMenuContent>
              {groups.length === 0 && <p className="text-muted-foreground px-3 py-2 text-sm">No groups yet</p>}
              {groups.map((group) => (
                <DropdownMenuItem key={group.id} onClick={() => bulkGroup.mutate(group.id)}>
                  {group.name}
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>

          <Button
            size="sm"
            variant="destructive"
            onClick={() => setBulkDeleteOpen(true)}
          >
            <Trash2 data-icon="inline-start" /> Remove screens
          </Button>
        </BulkActionBar>
      )}

      {screensQuery.isLoading ? (
        <AssetGrid>{Array.from({ length: 4 }).map((_, index) => <Skeleton key={index} className="h-72 rounded-xl" />)}</AssetGrid>
      ) : !screens.length ? (
        <EmptyState
          icon={Tv2}
          title="No screens yet"
          description="Open MR Signage on a TV and sign in with your account — the screen joins this workspace on its own."
          action={canEdit ? <Button onClick={() => setPairOpen(true)}>Add Screen</Button> : undefined}
        />
      ) : !filtered.length ? (
        <EmptyState
          icon={Search}
          title="No matching screens"
          description="Try a different search term or clear the status filter."
          action={<Button variant="outline" onClick={() => { setSearch(''); setStatus('all') }}>Clear filters</Button>}
        />
      ) : view === 'list' ? (
        <div className="ring-hairline bg-card overflow-hidden rounded-xl ring-1">
          <Table>
            <TableHeader>
              <TableRow className="border-hairline hover:bg-transparent">
                {canEdit && <TableHead className="w-10 pl-4" />}
                <TableHead className="w-20">Preview</TableHead>
                <TableHead>Name</TableHead>
                <TableHead className="w-32">Status</TableHead>
                <TableHead className="hidden w-40 lg:table-cell">Group</TableHead>
                <TableHead className="hidden w-24 xl:table-cell">Version</TableHead>
                <TableHead className="hidden w-40 sm:table-cell">Last seen</TableHead>
                {canEdit && <TableHead className="w-12 pr-4" />}
              </TableRow>
            </TableHeader>
            <TableBody>
              {filtered.map((screen: Screen) => {
                const label = screen.name || `Screen ${screen.id}`
                return (
                  <TableRow key={screen.id} className="border-hairline">
                    {canEdit && (
                      <TableCell className="pl-4">
                        <input
                          type="checkbox"
                          className="accent-primary size-4"
                          checked={bulk.isSelected(screen.id)}
                          onChange={(event) => bulk.toggle(screen.id, event.target.checked)}
                          aria-label={`Select ${label}`}
                        />
                      </TableCell>
                    )}
                    <TableCell className="py-2.5">
                      <div className="bg-muted h-10 w-[72px] overflow-hidden rounded-md">
                        <div className="text-muted-foreground/40 grid size-full place-items-center"><MonitorPlay className="size-4" /></div>
                      </div>
                    </TableCell>
                    <TableCell className="py-2.5">
                      {/* The whole name cell is the target — previously only the text was. */}
                      <Link href={`/dashboard/screens/${screen.id}`} className="block min-w-0">
                        <p className="text-foreground truncate text-sm font-medium">{label}</p>
                        {screen.device_id && <p className="text-muted-foreground mt-0.5 truncate text-sm">{screen.device_id}</p>}
                      </Link>
                    </TableCell>
                    <TableCell className="py-2.5"><StatusIndicator status={screen.status} /></TableCell>
                    <TableCell className="text-muted-foreground hidden py-2.5 text-sm lg:table-cell">
                      {groupName(screen.group_id) || '—'}
                    </TableCell>
                    <TableCell className="hidden py-2.5 xl:table-cell">
                      {screen.app_version ? <Badge variant="outline">v{screen.app_version}</Badge> : <span className="text-muted-foreground text-sm">—</span>}
                    </TableCell>
                    <TableCell className="text-muted-foreground hidden py-2.5 text-sm sm:table-cell" title={screen.last_seen || undefined}>
                      {screen.status === 'online' ? 'Online now' : relativeTime(screen.last_seen)}
                    </TableCell>
                    {canEdit && (
                      <TableCell className="py-2.5 pr-4">
                        <button
                          onClick={() => setDeleteTarget(screen)}
                          title="Remove display"
                          aria-label={`Remove ${label}`}
                          className="text-muted-foreground hover:text-destructive hover:bg-destructive/10 rounded-md p-1.5 transition-colors"
                        >
                          <Trash2 className="size-4" />
                        </button>
                      </TableCell>
                    )}
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        </div>
      ) : (
        <AssetGrid>
          {filtered.map((screen: Screen) => {
            const label = screen.name || `Screen ${screen.id}`
            const online = screen.status === 'online'
            const group = groupName(screen.group_id)
            return (
              <div key={screen.id} className="relative">
                {canEdit && (
                  <label className="absolute top-2.5 right-2.5 z-10 grid size-7 cursor-pointer place-items-center rounded-md bg-black/50 backdrop-blur">
                    <input
                      type="checkbox"
                      className="accent-primary size-4"
                      checked={bulk.isSelected(screen.id)}
                      onChange={(event) => bulk.toggle(screen.id, event.target.checked)}
                      aria-label={`Select ${label}`}
                    />
                  </label>
                )}
                <AssetCard
                  href={`/dashboard/screens/${screen.id}`}
                  title={label}
                  subtitle={
                    <>
                      {online ? 'Online now' : `Last seen ${relativeTime(screen.last_seen)}`}
                      {group && ` · ${group}`}
                    </>
                  }
                  preview={
                    <div className="text-muted-foreground/40 grid size-full place-items-center">
                      <MonitorPlay className="size-9" aria-hidden="true" />
                    </div>
                  }
                  badges={<OverlayBadge tone={online ? 'online' : 'offline'}>{online ? 'Online' : 'Offline'}</OverlayBadge>}
                  menu={
                    <>
                      <DropdownMenuItem render={<Link href={`/dashboard/screens/${screen.id}`} />}>
                        <Settings2 aria-hidden="true" /> Open screen
                      </DropdownMenuItem>
                      <DropdownMenuItem render={<Link href="/dashboard/groups" />}>
                        <Layers3 aria-hidden="true" /> Manage groups
                      </DropdownMenuItem>
                      {canEdit && (
                        <DropdownMenuItem
                          onClick={() => setDeleteTarget(screen)}
                          className="text-destructive focus:text-destructive"
                        >
                          <Trash2 aria-hidden="true" /> Remove screen
                        </DropdownMenuItem>
                      )}
                    </>
                  }
                />
              </div>
            )
          })}
        </AssetGrid>
      )}

      {/* Single Screen Delete Dialog */}
      <Dialog open={Boolean(deleteTarget)} onOpenChange={(open) => !open && setDeleteTarget(null)}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Remove {deleteTarget?.name || `Screen ${deleteTarget?.id}`}?</DialogTitle>
            <DialogDescription>
              This will permanently unpair and remove this display from your workspace. The physical TV will be automatically signed out and return to the pairing screen.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="gap-2 sm:gap-0">
            <Button variant="outline" onClick={() => setDeleteTarget(null)}>Cancel</Button>
            <Button
              variant="destructive"
              disabled={deleteMutation.isPending}
              onClick={() => deleteTarget && deleteMutation.mutate(deleteTarget.id)}
            >
              {deleteMutation.isPending ? 'Removing…' : 'Remove Display'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Bulk Delete Dialog */}
      <Dialog open={bulkDeleteOpen} onOpenChange={setBulkDeleteOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Remove {bulk.selected.length} displays?</DialogTitle>
            <DialogDescription>
              This will unpair and remove all {bulk.selected.length} selected displays from your workspace. Connected physical TVs will be automatically signed out.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="gap-2 sm:gap-0">
            <Button variant="outline" onClick={() => setBulkDeleteOpen(false)}>Cancel</Button>
            <Button
              variant="destructive"
              disabled={bulkDelete.isPending}
              onClick={() => bulkDelete.mutate()}
            >
              {bulkDelete.isPending ? 'Removing…' : `Remove ${bulk.selected.length} Displays`}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
