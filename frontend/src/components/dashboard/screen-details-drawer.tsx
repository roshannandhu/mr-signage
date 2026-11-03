'use client'

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { PlaySquare, X } from 'lucide-react'
import { Dialog as BaseDialog } from '@base-ui/react/dialog'
import { Button } from '@/components/ui/button'
import { Tabs, TabsIndicator, TabsList, TabsPanel, TabsTrigger } from '@/components/ui/tabs'
import { api } from '@/lib/api'
import { relativeTime } from '@/lib/format'
import type { Screen } from '@/lib/types'

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="border-hairline flex items-center justify-between gap-4 border-b py-3.5 text-sm last:border-0">
      <span className="text-muted-foreground shrink-0">{label}</span>
      <span className="text-foreground text-right font-medium">{value ?? '—'}</span>
    </div>
  )
}

const dateTime = (value: string | null | undefined) =>
  value ? new Date(value).toLocaleString() : '—'

/**
 * Everything the device reports about itself, in a drawer rather than a tab.
 *
 * It is reference material an operator checks when something looks wrong, so it should
 * not cost them the playlist they were editing.
 */
/**
 * How recoverable this screen is if the TV is reset or the app reinstalled.
 *
 * "serial" is the only identifier that survives a factory reset, and reading it needs the
 * player to be device owner — which it is on a zero-touch provisioned panel and is not on a
 * sideloaded install. "android_id" survives a reinstall but a reset gives it a new value.
 * "random" survives neither, so that panel WILL return as a duplicate if it is ever wiped.
 */
function identityLabel(source?: string | null): string | null {
  if (!source) return 'Not reported yet'
  if (source === 'serial') return 'Yes — survives a factory reset'
  if (source === 'android_id') return 'Reinstall only — a factory reset makes a duplicate'
  if (source === 'random') return 'No — any wipe makes a duplicate'
  return source
}

/**
 * Whether a release reaches this screen unattended.
 *
 * Android permits a silent install only for the device owner. Anything else raises a system
 * dialog on a television nobody is standing in front of, so the update simply waits -- which
 * looked from the dashboard exactly like a screen that had not been offered one.
 */
function updateLabel(deviceOwner?: boolean | null): string {
  if (deviceOwner == null) return 'Not reported yet'
  return deviceOwner
    ? 'Yes — installs silently'
    : 'No — waits for someone to tap Install on the TV'
}

export function ScreenDetailsDrawer({
  screen,
  open,
  onOpenChange,
}: {
  screen: Screen
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const queryClient = useQueryClient()

  const bringToFrontMutation = useMutation({
    mutationFn: () => api.bringToFront(screen.id),
    onSuccess: () => {
      toast.success('Command sent: opening signage app on screen.')
    },
    onError: (error: Error) => toast.error(error.message),
  })

  return (
    <BaseDialog.Root open={open} onOpenChange={onOpenChange}>
      <BaseDialog.Portal>
        <BaseDialog.Backdrop className="fixed inset-0 z-50 bg-black/30 backdrop-blur-[2px]" />
        <BaseDialog.Popup className="bg-card fixed inset-y-0 right-0 z-50 flex w-full max-w-lg flex-col shadow-2xl">
          <div className="flex items-center justify-between p-4 border-b border-hairline">
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                variant="outline"
                disabled={bringToFrontMutation.isPending}
                onClick={() => bringToFrontMutation.mutate()}
                className="text-xs"
              >
                <PlaySquare data-icon="inline-start" className="size-3.5" />
                {bringToFrontMutation.isPending ? 'Opening…' : 'Open app on TV'}
              </Button>
            </div>
            <BaseDialog.Title className="sr-only">Screen details</BaseDialog.Title>
            <BaseDialog.Close
              aria-label="Close details"
              className="text-muted-foreground hover:bg-accent hover:text-foreground grid size-9 cursor-pointer place-items-center rounded-lg"
            >
              <X className="size-5" />
            </BaseDialog.Close>
          </div>

          <Tabs defaultValue="information" className="min-h-0 flex-1 gap-0">
            <TabsList className="px-4">
              <TabsTrigger value="information">Information</TabsTrigger>
              <TabsTrigger value="history">History</TabsTrigger>
              <TabsIndicator />
            </TabsList>

            <div className="min-h-0 flex-1 overflow-y-auto px-5 py-2">
              <TabsPanel value="information">
                <Row label="Last seen" value={dateTime(screen.last_seen)} />
                <Row label="Status" value={screen.last_error ? 'Error' :'OK'} />
                <Row label="Orientation" value={`${screen.orientation ?? 0}° (${screen.orientation_source === 'manual' ?'set by you' :'auto-detected'})`} />
                <Row label="Manufacturer" value={screen.manufacturer} />
                <Row label="Model" value={screen.model} />
                <Row label="Android" value={screen.android_version ? `${screen.android_version} (API ${screen.sdk_int})` : null} />
                {/* Whether this panel can be recognised again after being wiped. The player
                    has always worked this out; it was never sent, so an operator found out
                    the answer by finding a duplicate screen in their fleet. */}
                <Row label="Survives a wipe" value={identityLabel(screen.identity_source)} />
                {/* Whether a published release actually reaches this screen on its own, or
                    waits for somebody to walk up and tap a dialog. */}
                <Row label="Updates by itself" value={updateLabel(screen.device_owner)} />
                <Row label="Resolution" value={screen.screen_width ? `${screen.screen_width}×${screen.screen_height}` : null} />
                <Row label="Storage total" value={screen.total_storage_mb ? `${screen.total_storage_mb} MB` : null} />
                <Row label="Storage free" value={screen.free_storage_mb ? `${screen.free_storage_mb} MB` : null} />
                <Row label="Memory" value={screen.total_ram_mb ? `${screen.total_ram_mb} MB` : null} />
                <Row label="Network" value={screen.network_type} />
                <Row label="Timezone" value={screen.timezone} />
                <Row label="Player version" value={screen.app_version} />
                <Row label="Device id" value={screen.device_id ? <code className="text-xs">{screen.device_id}</code> : null} />
                {/* What an installer types on the remote after Up, Up, Down, Down, OK. */}
                <Row
                  label="Maintenance pin"
                  value={screen.maintenance_pin ? <code className="text-xs">{screen.maintenance_pin}</code> : null}
                />
              </TabsPanel>

              <TabsPanel value="history">
                <Row label="Playback state" value={<span className="capitalize">{screen.playback_state}</span>} />
                <Row label="Last error" value={screen.last_error} />
                <Row label="Last error at" value={screen.last_error_at ? dateTime(screen.last_error_at) : '—'} />
                {!screen.last_error && (
                  <p className="text-muted-foreground py-6 text-sm">No faults reported by this screen.</p>
                )}
              </TabsPanel>
            </div>
          </Tabs>
        </BaseDialog.Popup>
      </BaseDialog.Portal>
    </BaseDialog.Root>
  )
}
