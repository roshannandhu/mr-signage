'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Radio, ShieldCheck } from 'lucide-react'
import { toast } from 'sonner'
import { EmptyState } from '@/components/dashboard/empty-state'
import { ErrorState } from '@/components/dashboard/error-state'
import { PageHeader } from '@/components/dashboard/page-header'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { api } from '@/lib/api'
import { useAuthStore } from '@/lib/store'
import { canEditTenantContent } from '@/lib/roles'
import { usePlanFeatures } from '@/lib/use-plan-features'
import type { EmergencyBroadcast } from '@/lib/types'

const targetLabels: Record<string, string> = {
  all: 'Every screen',
  group: 'Screen group',
  screen: 'Single screen',
}

export default function EmergencyPage() {
  const queryClient = useQueryClient()
  const user = useAuthStore((state) => state.user)
  const canEdit = canEditTenantContent(user)

  const [targetType, setTargetType] = useState('all')
  const [targetId, setTargetId] = useState('')
  const [playlistId, setPlaylistId] = useState('')

  const broadcastsQuery = useQuery({
    queryKey: ['emergency_broadcasts'],
    queryFn: api.getEmergencyBroadcasts,
    refetchInterval: 5000,
  })
  const playlistsQuery = useQuery({ queryKey: ['playlists'], queryFn: api.getPlaylists })
  const screensQuery = useQuery({ queryKey: ['screens'], queryFn: api.getScreens })
  const groupsQuery = useQuery({ queryKey: ['groups'], queryFn: api.getGroups })
  // A tenant whose plan lacks this feature does not see the page at all -- not a locked
  // form with an upgrade hint, which told them about a capability their admin had not turned
  // on. The nav link is already hidden (dashboard/layout.tsx); this covers a direct visit to
  // the URL. isLoading reads as "has it" so a page in the middle of loading does not flash
  // this redirect on every visit before the real answer arrives.
  const { has: hasFeature, isLoading: featuresLoading } = usePlanFeatures()
  const hasEmergency = hasFeature('emergency_alert')
  const router = useRouter()
  useEffect(() => {
    if (!featuresLoading && !hasEmergency) router.replace('/dashboard')
  }, [featuresLoading, hasEmergency, router])

  const triggerMutation = useMutation({
    mutationFn: api.triggerEmergencyBroadcast,
    onSuccess: () => {
      toast.success('Emergency broadcast triggered')
      queryClient.invalidateQueries({ queryKey: ['emergency_broadcasts'] })
      setTargetId('')
      setPlaylistId('')
    },
    onError: (error: Error) => toast.error(error.message || 'Failed to trigger broadcast'),
  })

  const cancelMutation = useMutation({
    mutationFn: api.cancelEmergencyBroadcast,
    onSuccess: () => {
      toast.success('Broadcast cancelled')
      queryClient.invalidateQueries({ queryKey: ['emergency_broadcasts'] })
    },
    onError: (error: Error) => toast.error(error.message || 'Failed to cancel broadcast'),
  })

  const handleTrigger = () => {
    if (!playlistId) return toast.error('Choose the playlist to broadcast')
    if (targetType !== 'all' && !targetId) return toast.error(`Choose which ${targetType} to override`)
    triggerMutation.mutate({
      target_type: targetType,
      target_id: targetType === 'all' ? null : parseInt(targetId, 10),
      playlist_id: parseInt(playlistId, 10),
    })
  }

  const handleCancel = (broadcast: EmergencyBroadcast) => cancelMutation.mutate({
    target_type: broadcast.target_type,
    target_id: broadcast.target_id,
    playlist_id: broadcast.playlist_id,
  })

  if (broadcastsQuery.isError || playlistsQuery.isError || screensQuery.isError || groupsQuery.isError) {
    return <ErrorState message="Emergency broadcasts could not be loaded." onRetry={() => { broadcastsQuery.refetch(); playlistsQuery.refetch(); screensQuery.refetch(); groupsQuery.refetch() }} />
  }

  // Mid-redirect: the plan has resolved and does not include this. Render nothing rather
  // than the form, which is exactly the flash of locked UI this is meant to avoid. While the
  // plan is STILL resolving, hasEmergency reads optimistically true (see usePlanFeatures),
  // so this does not blank the page for every tenant on every first visit.
  if (!featuresLoading && !hasEmergency) {
    return <div className="bg-background min-h-screen" aria-label="Redirecting" />
  }

  const broadcasts = broadcastsQuery.data || []
  const playlists = playlistsQuery.data || []
  const screens = screensQuery.data || []
  const groups = groupsQuery.data || []
  const playlistName = (id: number) => playlists.find((playlist) => playlist.id === id)?.name || `Playlist ${id}`
  // Nobody knows their screen's database id, so the picker resolves it for them —
  // and the same map turns an active broadcast back into a name.
  const targetOptions = targetType === 'group'
    ? groups.map((group) => ({ id: group.id, label: group.name }))
    : screens.map((screen) => ({ id: screen.id, label: screen.name || `Screen ${screen.id}` }))
  const targetName = (type: string, id: number | null) => {
    if (type === 'all' || id === null) return 'Every screen'
    const source = type === 'group' ? groups : screens
    const match = source.find((entry) => entry.id === id)
    const label = match && 'name' in match ? match.name : null
    return label || `${type === 'group' ? 'Group' : 'Screen'} ${id}`
  }

  return (
    <div className="space-y-8">
      <PageHeader
        eyebrow="Override"
        title="Emergency broadcast"
        description="Interrupt normal playback everywhere at once. Screens return to their scheduled playlist as soon as the broadcast is cancelled."
        actions={canEdit ? <Badge variant="warning">Takes effect immediately</Badge> : <Badge variant="outline">View only</Badge>}
      />

      <div className="grid grid-cols-1 items-start gap-6 xl:grid-cols-[minmax(0,1fr)_360px]">
        <Card className="ring-hairline bg-card border-0 py-0 shadow-[0_1px_2px_rgba(15,23,42,.04)] ring-1">
          <CardContent className="p-5 sm:p-6">
            <div className="flex items-start gap-3">
              <span className="bg-destructive/10 text-destructive grid size-9 shrink-0 place-items-center rounded-lg">
                <AlertTriangle className="size-4" aria-hidden="true" />
              </span>
              <div>
                <h2 className="text-foreground font-semibold">Trigger a broadcast</h2>
                <p className="text-muted-foreground mt-1 text-sm">The chosen playlist replaces whatever is on air until you cancel it.</p>
              </div>
            </div>

            <fieldset disabled={!canEdit || triggerMutation.isPending} className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <Label>Target</Label>
                <Select value={targetType} onValueChange={(value) => { setTargetType(value || 'all'); setTargetId('') }}>
                  {/* Base UI renders the raw value unless given this function, so a
                      plain placeholder would show "all" instead of "Every screen". */}
                  <SelectTrigger className="w-full"><SelectValue>{(value: string | null) => value ? targetLabels[value] || value : 'Choose target'}</SelectValue></SelectTrigger>
                  <SelectContent>
                    {Object.entries(targetLabels).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>

              {targetType !== 'all' && (
                <div className="space-y-2">
                  <Label>{targetType === 'group' ? 'Which group' : 'Which screen'}</Label>
                  <Select value={targetId} onValueChange={(value) => setTargetId(value || '')}>
                    <SelectTrigger className="w-full"><SelectValue>{(value: string | null) => {
                      if (!value) return targetType === 'group' ? 'Choose a group…' : 'Choose a screen…'
                      return targetOptions.find((option) => String(option.id) === value)?.label || value
                    }}</SelectValue></SelectTrigger>
                    <SelectContent>
                      {targetOptions.map((option) => <SelectItem key={option.id} value={String(option.id)}>{option.label}</SelectItem>)}
                    </SelectContent>
                  </Select>
                  {!targetOptions.length && <p className="text-muted-foreground/70 text-xs">No {targetType === 'group' ? 'groups' : 'screens'} available yet.</p>}
                </div>
              )}

              <div className="space-y-2 sm:col-span-2">
                <Label>Emergency playlist</Label>
                <Select value={playlistId} onValueChange={(value) => setPlaylistId(value || '')}>
                  <SelectTrigger className="w-full"><SelectValue>{(value: string | null) => value ? playlistName(Number(value)) : 'Choose a playlist…'}</SelectValue></SelectTrigger>
                  <SelectContent>
                    {playlists.map((playlist) => <SelectItem key={playlist.id} value={String(playlist.id)}>{playlist.name}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
            </fieldset>

            {canEdit && (
              <Button variant="destructive" className="mt-6 w-full sm:w-auto" disabled={triggerMutation.isPending} onClick={handleTrigger}>
                <Radio data-icon="inline-start" /> {triggerMutation.isPending ? 'Broadcasting…' : 'Broadcast now'}
              </Button>
            )}
          </CardContent>
        </Card>

        <section aria-labelledby="active-broadcasts" className="ring-hairline bg-card rounded-2xl p-5 shadow-[0_1px_2px_rgba(15,23,42,.04)] ring-1">
          <h2 id="active-broadcasts" className="text-foreground font-semibold">Active broadcasts</h2>
          <p className="text-muted-foreground/70 mt-1 text-xs">Refreshed every few seconds.</p>

          <div className="mt-5">
            {broadcastsQuery.isLoading ? (
              <div className="space-y-3"><Skeleton className="h-20" /><Skeleton className="h-20" /></div>
            ) : !broadcasts.length ? (
              <EmptyState icon={ShieldCheck} title="Nothing overriding" description="Every screen is playing its scheduled content." />
            ) : (
              <ul className="space-y-3">
                {broadcasts.map((broadcast) => (
                  <li key={broadcast.id} className="border-hairline rounded-xl border p-4">
                    <Badge variant="danger">{targetLabels[broadcast.target_type] || broadcast.target_type}</Badge>
                    <p className="text-foreground mt-3 truncate text-sm font-medium">{targetName(broadcast.target_type, broadcast.target_id)}</p>
                    <p className="text-muted-foreground mt-1 truncate text-xs">Playing {playlistName(broadcast.playlist_id)}</p>
                    {canEdit && (
                      <Button variant="outline" size="sm" className="mt-3 w-full" onClick={() => handleCancel(broadcast)} disabled={cancelMutation.isPending}>
                        Cancel broadcast
                      </Button>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </section>
      </div>
    </div>
  )
}
