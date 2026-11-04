'use client'

import { useMemo, useState } from 'react'
import Link from 'next/link'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Layers3, Search, Settings2, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { AssetCard, AssetGrid, OverlayBadge } from '@/components/dashboard/asset-card'
import { EmptyState } from '@/components/dashboard/empty-state'
import { ErrorState } from '@/components/dashboard/error-state'
import { ListToolbar, commonSorts, sortItems, type CommonSort } from '@/components/dashboard/list-toolbar'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import { DropdownMenuItem } from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { api } from '@/lib/api'
import { useAuthStore } from '@/lib/store'
import { canEditTenantContent } from '@/lib/roles'
import type { Screen, ScreenGroup } from '@/lib/types'

export default function GroupsPage() {
  const queryClient = useQueryClient()
  const user = useAuthStore((state) => state.user)
  const canEdit = canEditTenantContent(user)

  const groupsQuery = useQuery({ queryKey: ['groups'], queryFn: api.getGroups })
  const screensQuery = useQuery({ queryKey: ['screens'], queryFn: api.getScreens })
  const playlistsQuery = useQuery({ queryKey: ['playlists'], queryFn: api.getPlaylists })
  const groups = useMemo(() => groupsQuery.data || [], [groupsQuery.data])
  const screens = useMemo(() => screensQuery.data || [], [screensQuery.data])
  const playlists = useMemo(() => playlistsQuery.data || [], [playlistsQuery.data])

  const [search, setSearch] = useState('')
  const [sort, setSort] = useState<CommonSort>('newest')
  const [createOpen, setCreateOpen] = useState(false)
  const [name, setName] = useState('')
  const [deleteTarget, setDeleteTarget] = useState<ScreenGroup | null>(null)

  const membersOf = (groupId: number) => screens.filter((s: Screen) => s.group_id === groupId)
  // Naming the loop is the thing an operator actually wants from this card; today they
  // have to open the group to find out what it plays.
  const playlistName = (id: number | null) => playlists.find((p) => p.id === id)?.name

  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase()
    const matches = term ? groups.filter((group) => group.name.toLowerCase().includes(term)) : groups
    return sortItems(matches, sort, (group) => group.name, (group) => group.created_at)
  }, [groups, search, sort])

  const createMutation = useMutation({
    mutationFn: () => api.createGroup(name.trim()),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['groups'] })
      toast.success('Screen group created')
      setCreateOpen(false)
      setName('')
    },
    onError: (error: Error) => toast.error(error.message),
  })

  const deleteMutation = useMutation({
    mutationFn: (groupId: number) => api.deleteGroup(groupId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['groups'] })
      queryClient.invalidateQueries({ queryKey: ['screens'] })
      toast.success('Group removed. Its screens are now ungrouped.')
      setDeleteTarget(null)
    },
    onError: (error: Error) => toast.error(error.message),
  })

  const addGroup = (
    <Dialog open={createOpen} onOpenChange={setCreateOpen}>
      <DialogTrigger render={<Button variant="outline" className="bg-card" />}>Add screen group</DialogTrigger>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Add screen group</DialogTitle>
          <DialogDescription>
            Name the group now — you assign its playlist and its screens from the group&apos;s own page.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4 pt-2">
          <div className="space-y-2">
            <Label htmlFor="group-name">Group name</Label>
            <Input
              id="group-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="Reception screens"
              autoFocus
              onKeyDown={(event) => { if (event.key === 'Enter' && name.trim()) createMutation.mutate() }}
            />
          </div>
          <Button className="w-full" disabled={!name.trim() || createMutation.isPending} onClick={() => createMutation.mutate()}>
            {createMutation.isPending ? 'Creating…' : 'Create group'}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )

  if (groupsQuery.isError || screensQuery.isError) {
    return <ErrorState message="Screen groups could not be loaded." onRetry={() => { groupsQuery.refetch(); screensQuery.refetch() }} />
  }

  return (
    <div>
      <ListToolbar
        title="Screen groups"
        action={canEdit ? addGroup : <Badge variant="outline">View only</Badge>}
        sort={{ value: sort, onChange: setSort, options: commonSorts }}
        search={{ value: search, onChange: setSearch }}
      />

      {groupsQuery.isLoading ? (
        <AssetGrid>{Array.from({ length: 3 }).map((_, index) => <Skeleton key={index} className="h-64 rounded-xl" />)}</AssetGrid>
      ) : !groups.length ? (
        <EmptyState
          icon={Layers3}
          title="Screen groups"
          description="If you have multiple screens with the same playlist, a screen group lets you manage them all in one place. Create a group playlist, then assign screens to the group."
          action={canEdit ? <Button onClick={() => setCreateOpen(true)}>Add screen group</Button> : undefined}
        />
      ) : !filtered.length ? (
        <EmptyState icon={Search} title="No matching groups" description="Try a different search term." action={<Button variant="outline" onClick={() => setSearch('')}>Clear search</Button>} />
      ) : (
        <AssetGrid>
          {filtered.map((group) => {
            const members = membersOf(group.id)
            const online = members.filter((screen) => screen.status === 'online').length
            return (
              <AssetCard
                key={group.id}
                href={`/dashboard/groups/${group.id}`}
                title={group.name}
                subtitle={
                  <>
                    {members.length} screen{members.length === 1 ? '' : 's'}
                    {' • '}
                    {playlistName(group.playlist_id) ?? <span className="text-amber-600 dark:text-amber-400">no playlist yet</span>}
                  </>
                }
                preview={
                  <div className="text-muted-foreground/40 grid size-full place-items-center">
                    <Layers3 className="size-9" aria-hidden="true" />
                  </div>
                }
                badges={members.length > 0 ? <OverlayBadge tone={online ? 'online' : 'offline'}>{online} of {members.length} online</OverlayBadge> : undefined}
                menu={canEdit ? (
                  <>
                    <DropdownMenuItem render={<Link href={`/dashboard/groups/${group.id}`} />}>
                      <Settings2 aria-hidden="true" /> Open group
                    </DropdownMenuItem>
                    <DropdownMenuItem onClick={() => setDeleteTarget(group)} className="text-destructive">
                      <Trash2 aria-hidden="true" /> Delete group
                    </DropdownMenuItem>
                  </>
                ) : undefined}
              />
            )
          })}
        </AssetGrid>
      )}

      <Dialog open={Boolean(deleteTarget)} onOpenChange={(open) => !open && setDeleteTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete “{deleteTarget?.name}”?</DialogTitle>
            <DialogDescription>
              The group&apos;s screens are not deleted — they become ungrouped and stop following the group playlist.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter showCloseButton>
            <Button variant="destructive" disabled={deleteMutation.isPending} onClick={() => deleteTarget && deleteMutation.mutate(deleteTarget.id)}>
              {deleteMutation.isPending ? 'Deleting…' : 'Delete group'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
