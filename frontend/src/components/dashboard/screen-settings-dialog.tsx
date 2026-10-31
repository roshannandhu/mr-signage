'use client'

import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { ChevronDown, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { PlaceSearch } from '@/components/dashboard/place-search'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'
import type { FitMode, Screen, SyncRole } from '@/lib/types'

const ORIENTATIONS = [
  { value: '0', label: 'Landscape' },
  { value: '90', label: 'Portrait' },
  { value: '180', label: 'Landscape (flipped)' },
  { value: '270', label: 'Portrait (flipped)' },
]

const FIT_MODES: { value: FitMode; label: string }[] = [
  { value: 'contain', label: 'Ensure the entire content is displayed (possible letterbox)' },
  { value: 'cover', label: 'Fill the screen (content may be cropped)' },
]

/** The browser knows every zone the host supports; no need to ship a list. */
function timezones(): string[] {
  const supported = Intl.supportedValuesOf?.('timeZone')
  if (supported?.length) return supported
  return [Intl.DateTimeFormat().resolvedOptions().timeZone]
}

const splitTags = (value: string | null | undefined) =>
  (value || '').split(',').map((tag) => tag.trim()).filter(Boolean)

export function ScreenSettingsDialog({
  screen,
  siblings,
  open,
  onOpenChange,
}: {
  screen: Screen
  /** Candidate leaders — every other screen in the workspace. */
  siblings: Screen[]
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const queryClient = useQueryClient()

  const [name, setName] = useState(screen.name || '')
  const [description, setDescription] = useState(screen.description || '')
  const [place, setPlace] = useState({
    location: screen.location || '',
    latitude: screen.latitude ?? null,
    longitude: screen.longitude ?? null,
    place_id: screen.place_id ?? null,
  })
  const [orientation, setOrientation] = useState(String(screen.orientation ?? 0))
  const [tags, setTags] = useState<string[]>(splitTags(screen.tags))
  const [tagDraft, setTagDraft] = useState('')
  const [advanced, setAdvanced] = useState(false)
  const [fitMode, setFitMode] = useState<FitMode>(screen.fit_mode || 'contain')
  const [maintenancePin, setMaintenancePin] = useState(screen.maintenance_pin || '')
  const [timezone, setTimezone] = useState(screen.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone)
  const [syncPlayback, setSyncPlayback] = useState(Boolean(screen.sync_playback))
  const [syncRole, setSyncRole] = useState<SyncRole>(screen.sync_role || 'leader')
  const [leaderId, setLeaderId] = useState<string | null>(screen.leader_screen_id ? String(screen.leader_screen_id) : null)

  const addTag = () => {
    const value = tagDraft.trim().replace(/,/g, '')
    if (value && !tags.includes(value)) setTags((current) => [...current, value])
    setTagDraft('')
  }

  const save = useMutation({
    mutationFn: () => api.patchScreen(screen.id, {
      name: name.trim(),
      description: description.trim() || null,
      location: place.location.trim() || null,
      latitude: place.latitude,
      longitude: place.longitude,
      place_id: place.place_id,
      // Only when it was changed: the server treats any orientation in the request as the
      // operator pinning it, so sending it back untouched stopped a TV auto-rotating just
      // because its name or a tag was edited.
      ...(Number(orientation) !== (screen.orientation ?? 0) ? { orientation: Number(orientation) } : {}),
      tags: tags.join(',') || null,
      fit_mode: fitMode,
      // Omitted when unchanged or incomplete: the API rejects anything but four digits,
      // and a half-typed pin must not blank out a working one.
      ...(/^\d{4}$/.test(maintenancePin) && maintenancePin !== screen.maintenance_pin
        ? { maintenance_pin: maintenancePin }
        : {}),
      timezone,
      sync_playback: syncPlayback,
      sync_role: syncRole,
      leader_screen_id: syncRole === 'follower' && leaderId ? Number(leaderId) : null,
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['screens'] })
      toast.success('Screen settings saved')
      onOpenChange(false)
    },
    onError: (error: Error) => toast.error(error.message),
  })

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Screen settings</DialogTitle>
        </DialogHeader>

        <div className="max-h-[60vh] space-y-5 overflow-y-auto px-1 pt-2">
          <div className="space-y-2">
            <Label htmlFor="screen-orientation">Screen orientation</Label>
            <Select value={orientation} onValueChange={(value) => setOrientation(value as string)}>
              <SelectTrigger id="screen-orientation" className="w-full"><SelectValue /></SelectTrigger>
              <SelectContent>
                {ORIENTATIONS.map((option) => <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>)}
              </SelectContent>
            </Select>
            {screen.orientation_source === 'auto' && (
              <p className="text-muted-foreground text-xs">
                Currently auto-detected from the panel. Choosing a different orientation pins it, and the TV will stop changing it.
              </p>
            )}
          </div>

          <div className="space-y-2">
            <Label htmlFor="screen-name">Name <span className="text-destructive">*</span></Label>
            <Input id="screen-name" value={name} onChange={(event) => setName(event.target.value)} />
          </div>

          <div className="space-y-2">
            <Label htmlFor="screen-description">Description</Label>
            <Input id="screen-description" value={description} onChange={(event) => setDescription(event.target.value)} />
          </div>

          <div className="space-y-2">
            <Label htmlFor="screen-location">Location</Label>
            <PlaceSearch value={place} onChange={setPlace} />
            <p className="text-muted-foreground text-xs">
              Where this screen physically is. Client reports group plays by place and show it on a map.
            </p>
          </div>

          <div className="space-y-2">
            <Label htmlFor="screen-tags">Tags</Label>
            <div className="border-input bg-card flex flex-wrap items-center gap-1.5 rounded-lg border p-2">
              {tags.map((tag) => (
                <span key={tag} className="bg-secondary text-secondary-foreground inline-flex h-7 items-center gap-1 rounded-md pr-1 pl-2.5 text-xs font-medium">
                  {tag}
                  <button
                    type="button"
                    onClick={() => setTags((current) => current.filter((value) => value !== tag))}
                    aria-label={`Remove tag ${tag}`}
                    className="hover:bg-background/60 grid size-5 cursor-pointer place-items-center rounded"
                  >
                    <X className="size-3" />
                  </button>
                </span>
              ))}
              <input
                id="screen-tags"
                value={tagDraft}
                onChange={(event) => setTagDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ',') { event.preventDefault(); addTag() }
                  // Backspace on an empty box removes the last chip, the usual chip-input feel.
                  if (event.key === 'Backspace' && !tagDraft) setTags((current) => current.slice(0, -1))
                }}
                onBlur={addTag}
                placeholder="Type and press enter"
                className="text-foreground placeholder:text-muted-foreground min-w-40 flex-1 bg-transparent px-1 py-1 text-sm outline-none"
              />
            </div>
            <p className="text-muted-foreground text-xs">Enter optional tags to help organize your screens</p>
          </div>

          <button
            type="button"
            onClick={() => setAdvanced((value) => !value)}
            className="text-primary dark:text-brand hover:bg-accent flex cursor-pointer items-center gap-1 rounded-lg px-2 py-1.5 text-sm font-semibold"
          >
            Advanced settings
            <ChevronDown className={cn('size-4 transition-transform', advanced && 'rotate-180')} aria-hidden="true" />
          </button>

          {advanced && (
            <div className="space-y-5 pt-1">
              <div className="space-y-2">
                <Label htmlFor="screen-fit">Fit content to the screen</Label>
                <Select value={fitMode} onValueChange={(value) => setFitMode(value as FitMode)}>
                  <SelectTrigger id="screen-fit" className="w-full"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {FIT_MODES.map((option) => <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-2">
                <Label htmlFor="screen-pin">Maintenance pin</Label>
                <Input
                  id="screen-pin"
                  inputMode="numeric"
                  maxLength={4}
                  value={maintenancePin}
                  onChange={(event) => setMaintenancePin(event.target.value.replace(/\D/g, '').slice(0, 4))}
                  placeholder="0000"
                />
                <p className="text-muted-foreground text-xs">
                  Four digits, asked for on the TV after pressing Up, Up, Down, Down, OK on the
                  remote. The player caches it, so it still opens while the screen is offline —
                  a change here applies at the next sync.
                </p>
              </div>

              <div className="space-y-2">
                <Label htmlFor="screen-timezone">Timezone</Label>
                <Select value={timezone} onValueChange={(value) => setTimezone(value as string)}>
                  <SelectTrigger id="screen-timezone" className="w-full"><SelectValue /></SelectTrigger>
                  <SelectContent className="max-h-72">
                    {timezones().map((zone) => <SelectItem key={zone} value={zone}>{zone}</SelectItem>)}
                  </SelectContent>
                </Select>
                <p className="text-muted-foreground text-xs">Operating hours and schedules are evaluated in this zone.</p>
              </div>

              <label className="flex cursor-pointer items-center gap-3 text-sm">
                <input
                  type="checkbox"
                  className="accent-primary size-4"
                  checked={syncPlayback}
                  onChange={(event) => setSyncPlayback(event.target.checked)}
                />
                Synchronize playback with other screens
              </label>

              {syncPlayback && (
                <>
                  <div className="space-y-2">
                    <Label htmlFor="screen-role">Role of this screen</Label>
                    <Select value={syncRole} onValueChange={(value) => setSyncRole(value as SyncRole)}>
                      <SelectTrigger id="screen-role" className="w-full"><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="leader">Leader</SelectItem>
                        <SelectItem value="follower">Follower</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>

                  {syncRole === 'follower' && (
                    <div className="space-y-2">
                      <Label htmlFor="screen-leader">Select the leader screen</Label>
                      <Select value={leaderId} onValueChange={(value) => setLeaderId(value as string | null)}>
                        <SelectTrigger id="screen-leader" className="w-full">
                          <SelectValue placeholder={siblings.length ? 'Choose a leader' : 'No leader screens found'} />
                        </SelectTrigger>
                        <SelectContent>
                          {siblings.map((candidate) => (
                            <SelectItem key={candidate.id} value={String(candidate.id)}>
                              {candidate.name || `Screen ${candidate.id}`}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                  )}
                </>
              )}
            </div>
          )}
        </div>

        <div className="flex justify-end pt-4">
          <Button disabled={!name.trim() || save.isPending} onClick={() => save.mutate()}>
            {save.isPending ? 'Saving…' : 'Save'}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
