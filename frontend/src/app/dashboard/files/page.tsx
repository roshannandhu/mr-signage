'use client'

import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { FolderCheck, HardDrive, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { EmptyState } from '@/components/dashboard/empty-state'
import { ErrorState } from '@/components/dashboard/error-state'
import { ListToolbar, type SortOption } from '@/components/dashboard/list-toolbar'
import { MediaThumbnail } from '@/components/dashboard/media-thumbnail'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { DropdownMenuItem } from '@/components/ui/dropdown-menu'
import { Skeleton } from '@/components/ui/skeleton'
import { api } from '@/lib/api'
import { bulkSummary, runBulk } from '@/lib/bulk'
import { formatBytes, relativeTime } from '@/lib/format'
import { useAuthStore } from '@/lib/store'
import { canEditTenantContent } from '@/lib/roles'
import type { ContentItem, Playlist } from '@/lib/types'

type FileSort = 'largest' | 'smallest' | 'newest' | 'az'

const SORTS: SortOption<FileSort>[] = [
  { value: 'largest', label: 'Size (largest first)' },
  { value: 'smallest', label: 'Size (smallest first)' },
  { value: 'newest', label: 'Date added (newest first)' },
  { value: 'az', label: 'Alphabetical (ascending)' },
]

/**
 * Storage housekeeping, separate from the Content library.
 *
 * The library is for choosing what to play; this is for finding what to delete. It leads
 * with size and with whether anything still references the file, because those are the
 * two things that decide whether it is safe to remove.
 */
export default function FileManagementPage() {
  const queryClient = useQueryClient()
  const user = useAuthStore((state) => state.user)
  const canEdit = canEditTenantContent(user)

  const contentQuery = useQuery({ queryKey: ['content'], queryFn: api.getContent })
  const playlistsQuery = useQuery({ queryKey: ['playlists'], queryFn: api.getPlaylists })

  const content = useMemo(() => contentQuery.data || [], [contentQuery.data])
  const playlists = useMemo(() => playlistsQuery.data || [], [playlistsQuery.data])

  // How many loops reference each asset — deleting an unused file is safe, deleting a
  // scheduled one pulls it off screens.
  const usage = useMemo(() => {
    const counts = new Map<number, number>()
    for (const playlist of playlists as Playlist[]) {
      for (const item of playlist.items || []) {
        counts.set(item.content_id, (counts.get(item.content_id) || 0) + 1)
      }
    }
    return counts
  }, [playlists])

  const [sort, setSort] = useState<FileSort>('largest')
  const [search, setSearch] = useState('')
  const [onlyUnused, setOnlyUnused] = useState(false)
  const [selected, setSelected] = useState<number[]>([])
  const [confirmOpen, setConfirmOpen] = useState(false)

  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase()
    const matches = content.filter((item) => {
      const matchesSearch = !term || item.name.toLowerCase().includes(term)
      const matchesUsage = !onlyUnused || !usage.get(item.id)
      return matchesSearch && matchesUsage
    })
    return [...matches].sort((a, b) => {
      switch (sort) {
        case 'smallest': return a.file_size_bytes - b.file_size_bytes
        case 'newest': return Date.parse(b.uploaded_at) - Date.parse(a.uploaded_at)
        case 'az': return a.name.localeCompare(b.name)
        default: return b.file_size_bytes - a.file_size_bytes
      }
    })
  }, [content, search, sort, onlyUnused, usage])

  const totalBytes = content.reduce((total, item) => total + item.file_size_bytes, 0)
  const selectedBytes = content.filter((item) => selected.includes(item.id)).reduce((total, item) => total + item.file_size_bytes, 0)

  const deleteSelected = useMutation({
    // Sequential: the media worker cleans up renditions per asset, and a burst of
    // parallel deletes has it competing with itself for the same storage handles.
    mutationFn: () => runBulk(selected, (id) => api.deleteContent(id)),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ['content'] })
      queryClient.invalidateQueries({ queryKey: ['playlists'] })
    },
    onSuccess: ({ done, failed }) => {
      setConfirmOpen(false)
      if (failed.length) {
        // This page keeps its own list of ids, so drop the ones that went: left in, the next
        // attempt starts with a file that no longer exists and fails on it.
        toast.error(bulkSummary('deleted', done, failed))
        setSelected(failed.map((entry) => entry.id))
        return
      }
      toast.success(`${done} file${done === 1 ? '' : 's'} deleted`)
      setSelected([])
    },
    onError: (error: Error) => toast.error(error.message),
  })

  // Usage is what separates "safe to delete" from "playing on a screen right now". If the
  // playlists failed to load, every file would read "Unused" and a bulk delete would pull live
  // adverts off screens, so the page does not render rather than guess.
  if (contentQuery.isError || playlistsQuery.isError) {
    return (
      <ErrorState
        message="File management could not be loaded."
        onRetry={() => { contentQuery.refetch(); playlistsQuery.refetch() }}
      />
    )
  }
  const usageKnown = playlistsQuery.isSuccess

  const inUseSelected = selected.filter((id) => usage.get(id)).length

  return (
    <div>
      <ListToolbar
        title="File management"
        action={
          <span className="text-muted-foreground flex items-center gap-2 text-sm">
            <HardDrive className="size-4" aria-hidden="true" />
            {formatBytes(totalBytes)} across {content.length} file{content.length === 1 ? '' : 's'}
          </span>
        }
        sort={{ value: sort, onChange: setSort, options: SORTS }}
        search={{ value: search, onChange: setSearch }}
        filters={
          <>
            <DropdownMenuItem onClick={() => setOnlyUnused(false)} className={!onlyUnused ? 'bg-accent font-medium' : undefined}>All files</DropdownMenuItem>
            <DropdownMenuItem onClick={() => setOnlyUnused(true)} className={onlyUnused ? 'bg-accent font-medium' : undefined}>Not in any playlist</DropdownMenuItem>
          </>
        }
      />

      {selected.length > 0 && canEdit && (
        <div className="ring-hairline bg-secondary/60 mb-4 flex flex-wrap items-center gap-3 rounded-xl p-3 ring-1">
          <span className="text-foreground text-sm font-medium">
            {selected.length} selected · {formatBytes(selectedBytes)}
          </span>
          {inUseSelected > 0 && <Badge variant="warning">{inUseSelected} still scheduled</Badge>}
          <div className="ml-auto flex gap-2">
            <Button variant="outline" size="sm" onClick={() => setSelected([])}>Clear</Button>
            <Button variant="destructive" size="sm" disabled={!usageKnown} onClick={() => setConfirmOpen(true)}>
              <Trash2 data-icon="inline-start" /> Delete selected
            </Button>
          </div>
        </div>
      )}

      {contentQuery.isLoading ? (
        <div className="space-y-2">{Array.from({ length: 6 }).map((_, index) => <Skeleton key={index} className="h-16 rounded-xl" />)}</div>
      ) : !filtered.length ? (
        <EmptyState
          icon={FolderCheck}
          title={content.length ? 'No matching files' : 'Nothing stored yet'}
          description={content.length ? 'Try a different search term or filter.' : 'Upload media from the Content library and it will show up here.'}
        />
      ) : (
        <div className="ring-hairline divide-hairline bg-card divide-y overflow-hidden rounded-xl ring-1">
          {filtered.map((item: ContentItem) => {
            const uses = usage.get(item.id) || 0
            return (
              <label key={item.id} className="hover:bg-muted/50 flex cursor-pointer items-center gap-4 p-3">
                {canEdit && (
                  <input
                    type="checkbox"
                    className="accent-primary size-4 shrink-0"
                    checked={selected.includes(item.id)}
                    onChange={(event) => setSelected((current) => event.target.checked
                      ? [...current, item.id]
                      : current.filter((id) => id !== item.id))}
                    aria-label={`Select ${item.name}`}
                  />
                )}
                <MediaThumbnail item={item} className="h-10 w-16 shrink-0 rounded-lg" />
                <div className="min-w-0 flex-1">
                  <p className="text-foreground truncate text-sm font-medium">{item.name}</p>
                  <p className="text-muted-foreground text-xs">
                    <span className="capitalize">{item.type}</span> · added {relativeTime(item.uploaded_at)}
                  </p>
                </div>
                {!usageKnown
                  ? <Badge variant="outline">Checking…</Badge>
                  : uses > 0
                    ? <Badge variant="secondary">In {uses} playlist{uses === 1 ? '' : 's'}</Badge>
                    : <Badge variant="outline">Unused</Badge>}
                <span className="text-foreground w-20 shrink-0 text-right font-mono text-sm tabular-nums">
                  {formatBytes(item.file_size_bytes)}
                </span>
              </label>
            )
          })}
        </div>
      )}

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete {selected.length} file{selected.length === 1 ? '' : 's'}?</DialogTitle>
            <DialogDescription>
              This frees {formatBytes(selectedBytes)}.
              {inUseSelected > 0 && ` ${inUseSelected} of them ${inUseSelected === 1 ? 'is' : 'are'} still in a playlist and will stop playing on every screen using it.`}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter showCloseButton>
            <Button variant="destructive" disabled={deleteSelected.isPending} onClick={() => deleteSelected.mutate()}>
              {deleteSelected.isPending ? 'Deleting…' : 'Delete files'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
