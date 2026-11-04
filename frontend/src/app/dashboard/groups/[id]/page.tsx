'use client'

import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import Link from 'next/link'
import { useParams } from 'next/navigation'
import { ArrowLeft, Layers3, ListVideo, MonitorSmartphone, Settings } from 'lucide-react'
import { AssignPlaylistCard } from '@/components/dashboard/assign-playlist-card'
import { EmptyState } from '@/components/dashboard/empty-state'
import { ErrorState } from '@/components/dashboard/error-state'
import { AssignScreensDialog, GroupSettingsDialog } from '@/components/dashboard/group-dialogs'
import { PlaylistBuilder } from '@/components/dashboard/playlist-builder'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { api } from '@/lib/api'
import { useAuthStore } from '@/lib/store'
import { canEditTenantContent } from '@/lib/roles'
import type { Screen } from '@/lib/types'

/**
 * A group is one playlist shared by several screens.
 *
 * Laid out like the screen page on purpose — the loop is the page, and membership and
 * settings are dialogs rather than tabs, so editing what plays never costs a click.
 */
export default function GroupDetailPage() {
  const params = useParams()
  const groupId = Number(params.id)
  const user = useAuthStore((state) => state.user)
  const canEdit = canEditTenantContent(user)

  const groupsQuery = useQuery({ queryKey: ['groups'], queryFn: api.getGroups })
  const screensQuery = useQuery({ queryKey: ['screens'], queryFn: api.getScreens })

  const group = groupsQuery.data?.find((item) => item.id === groupId)
  const screens = useMemo(() => (screensQuery.data || []) as Screen[], [screensQuery.data])
  const members = useMemo(() => screens.filter((s) => s.group_id === groupId), [screens, groupId])
  const online = members.filter((s) => s.status === 'online').length

  const [assignOpen, setAssignOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)

  const backLink = (
    <Link href="/dashboard/groups" className="text-muted-foreground hover:text-foreground mb-4 inline-flex items-center gap-2 text-sm font-medium">
      <ArrowLeft className="size-4" /> Back to groups
    </Link>
  )

  if (groupsQuery.isError || screensQuery.isError) {
    return <ErrorState message="This group could not be loaded." onRetry={() => { groupsQuery.refetch(); screensQuery.refetch() }} />
  }
  if (groupsQuery.isLoading) {
    return <div className="space-y-6"><Skeleton className="h-32" /><Skeleton className="h-[480px]" /></div>
  }
  if (!group) {
    return (
      <div>
        {backLink}
        <EmptyState icon={Layers3} title="Group not found" description="It may have been deleted, or the link is out of date." action={<Button variant="outline" render={<Link href="/dashboard/groups" />}>View all groups</Button>} />
      </div>
    )
  }

  return (
    <div>
      {backLink}

      <header className="bg-secondary/50 ring-hairline mb-6 rounded-2xl p-4 ring-1 sm:p-5">
        <div className="flex flex-wrap items-start gap-4">
          <span className="bg-primary/10 text-primary dark:text-brand grid size-16 shrink-0 place-items-center rounded-xl">
            <Layers3 className="size-7" aria-hidden="true" />
          </span>
          <div className="min-w-0 flex-1">
            <h1 className="text-foreground truncate text-2xl font-bold tracking-tight">{group.name}</h1>
            <p className="text-muted-foreground mt-1 text-sm">
              One playlist, every screen in this group.
            </p>
          </div>
          <div className="flex flex-col items-start gap-2 sm:items-end">
            <span className="text-muted-foreground text-sm">
              {members.length === 0
                ? 'No screens in this group'
                : `${members.length} screen${members.length === 1 ? '' : 's'} · ${online} online`}
            </span>
            {canEdit && (
              <div className="flex items-center gap-1">
                <button
                  onClick={() => setAssignOpen(true)}
                  className="text-primary dark:text-brand hover:bg-accent flex cursor-pointer items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-sm font-medium"
                >
                  <MonitorSmartphone className="size-4" aria-hidden="true" /> Assign screens
                </button>
                <button
                  onClick={() => setSettingsOpen(true)}
                  className="text-primary dark:text-brand hover:bg-accent flex cursor-pointer items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-sm font-medium"
                >
                  <Settings className="size-4" aria-hidden="true" /> Settings
                </button>
              </div>
            )}
          </div>
        </div>
      </header>

      {!group.playlist_id ? (
        canEdit
          ? <AssignPlaylistCard target={{ kind: 'group', id: group.id, name: group.name, screenCount: members.length }} />
          : <EmptyState icon={ListVideo} title="No playlist assigned" description="Ask an editor to schedule content on this group." />
      ) : (
        <PlaylistBuilder playlistId={group.playlist_id} showHeader={false} />
      )}

      {canEdit && (
        <>
          {/* Remount on close so a cancelled edit does not linger in the form. */}
          <AssignScreensDialog key={`assign-${assignOpen}`} group={group} screens={screens} open={assignOpen} onOpenChange={setAssignOpen} />
          <GroupSettingsDialog key={`settings-${settingsOpen}`} group={group} open={settingsOpen} onOpenChange={setSettingsOpen} />
        </>
      )}
    </div>
  )
}
