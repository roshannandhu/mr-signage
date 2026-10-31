'use client'

import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { CalendarRange, Check, MonitorPlay, Plus } from 'lucide-react'
import { api } from '@/lib/api'
import { rupees } from '@/lib/format'
import { invalidateBookingViews } from '@/lib/query-keys'
import type { ContentItem, Placement, Screen } from '@/lib/types'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { CreateBookingModal } from '@/components/dashboard/create-booking-modal'

/**
 * Putting an advert on a screen when it may already be sold.
 *
 * The "+" beside a creative used to open Create New Booking unconditionally, which is right
 * for a first sale and wrong for every one after it. One advert sold once to one client is
 * ONE booking with several locations; making a second booking for the same sale charges the
 * client twice on the invoice, lists the campaign twice on their report, and — the quiet one
 * — spends a second ad slot out of the workspace's quota, so a tenant allowed ten slots hits
 * the cap at three real campaigns and cannot see why.
 *
 * So: look first, then ask. Never sold → straight through, no extra click. Already running →
 * offer to add these screens to the booking that exists. There is deliberately no "start a
 * separate one" escape hatch: the server refuses a second live booking of one creative for
 * the reasons above, so the only honest thing this dialog can do is say so.
 */
export function BookOrExtendDialog({
  content,
  screens,
  open,
  onOpenChange,
  onBooked,
}: {
  content: ContentItem
  /** The screens showing this loop — what "add it here" means on this page. */
  screens: Screen[]
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Fired once the advert is actually on these screens, so the caller can react. */
  onBooked?: () => void
}) {
  const queryClient = useQueryClient()
  const [daysOverride, setDaysOverride] = useState('')

  const placementsQuery = useQuery({
    queryKey: ['placements', content.id],
    queryFn: () => api.getPlacements(content.id),
    enabled: open,
  })

  // A booking worth extending is one that has not finished. Extending a campaign that ended
  // last month would backdate the new location into a run nobody is paying for.
  //
  // "Now" is the moment the bookings were fetched, not Date.now() during render: reading the
  // clock while rendering is impure (React flags it, and the same render can then produce
  // two different answers). dataUpdatedAt is a plain number that changes only when the data
  // does, which is also the honest reading -- this list is as fresh as the response it came
  // from, and a refetch re-evaluates it.
  const asOf = placementsQuery.dataUpdatedAt
  const live = useMemo(
    () =>
      (placementsQuery.data ?? []).filter((placement) => {
        const finishes = placement.effective_ends_at ?? placement.ends_at
        return finishes ? new Date(finishes).getTime() >= asOf : true
      }),
    [placementsQuery.data, asOf],
  )

  const finishedCount = (placementsQuery.data?.length ?? 0) - live.length

  // A screen added now cannot outlast the sale that pays for it: the backend refuses a run
  // that ends after the booking does, because stretching it there would silently extend the
  // whole campaign without billing a day of it. Bound the box so the operator sees the
  // limit instead of meeting it as an error.
  //
  // The smallest remaining run across the live bookings, since one box serves them all —
  // with the one-live-booking rule that is simply the booking's own remaining days.
  const maxDays = useMemo(() => {
    const remaining = live.map((placement) => {
      const finishes = placement.effective_ends_at ?? placement.ends_at
      if (!finishes) return Infinity
      return Math.floor((new Date(finishes).getTime() - asOf) / 864e5)
    })
    const smallest = remaining.length ? Math.min(...remaining) : Infinity
    return Number.isFinite(smallest) ? Math.max(1, smallest) : undefined
  }, [live, asOf])

  const close = (next: boolean) => {
    if (!next) {
      setDaysOverride('')
    }
    onOpenChange(next)
  }

  const addTargets = useMutation({
    mutationFn: async ({ placement, targets }: { placement: Placement; targets: Screen[] }) => {
      const days = Number(daysOverride)
      // One at a time, not Promise.all: the screen cap and the plan's location limit are
      // checked per call, and firing them together turns "the 4th screen is over your plan"
      // into three successes and one confusing failure in an unpredictable order.
      for (const screen of targets) {
        await api.addPlacementTarget(placement.id, {
          screen_id: screen.id,
          // Omitted by default so the location follows the campaign's own end date. Sent
          // only when the operator typed a different run length for these screens.
          ...(days > 0 ? { days } : {}),
        })
      }
      return targets.length
    },
    // Whatever the outcome. The screens go on one at a time, so a refusal part-way (the plan's
    // cap, say) leaves the earlier ones already on the booking; without a refetch the dialog kept
    // offering them as missing, and the next attempt tried to add them again and was refused
    // before it ever reached the screen that needed attention.
    onSettled: () => {
      invalidateBookingViews(queryClient)
      queryClient.invalidateQueries({ queryKey: ['placements', content.id] })
    },
    onSuccess: (count, { placement }) => {
      toast.success(
        `${count} screen${count === 1 ? '' : 's'} added to ${placement.advertiser}'s booking.`,
      )
      onBooked?.()
      close(false)
    },
    // The backend refuses over the plan's location cap with a message naming the plan and
    // the limit, so show what it said rather than a generic failure.
    onError: (error: Error) => toast.error(error.message),
  })

  if (!open) return null

  // Deciding needs the answer. Showing the booking form first and swapping it underneath the
  // operator would be worse than a moment's wait.
  if (placementsQuery.isLoading) {
    return (
      <Dialog open onOpenChange={close}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Checking this advert…</DialogTitle>
            <DialogDescription>Looking for a booking it already belongs to.</DialogDescription>
          </DialogHeader>
        </DialogContent>
      </Dialog>
    )
  }

  // Never sold: the original behaviour, straight through to the booking form with no extra
  // click. Once something IS live, a second booking is not offered at all -- see below.
  if (live.length === 0) {
    return (
      <CreateBookingModal
        open
        onOpenChange={close}
        contentId={content.id}
        contentTitle={content.name}
        defaultScreenIds={screens.map((screen) => screen.id)}
        initialClientId={content.client_id ?? null}
        initialAdvertiser={content.client_name ?? ''}
        // The first sale of an advert lands here, not through addTargets above, so without
        // this the library row only animated out on the SECOND booking onwards.
        onBooked={onBooked}
      />
    )
  }

  return (
    <Dialog open onOpenChange={close}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>This advert is already running</DialogTitle>
          <DialogDescription>
            Add these screens to the booking it belongs to, so it stays one campaign on the
            invoice and on the client&apos;s report.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          {live.map((placement) => {
            // A screen counts as already covered if the booking names it directly OR names a
            // group it belongs to — a group target puts the advert on every screen inside it,
            // and offering to add one of those again would book it twice on the same panel.
            const bookedScreenIds = new Set(
              placement.targets.filter((t) => t.screen_id).map((t) => t.screen_id),
            )
            const bookedGroupIds = new Set(
              placement.targets.filter((t) => t.kind === 'group' && t.group_id).map((t) => t.group_id),
            )
            const missing = screens.filter(
              (screen) =>
                !bookedScreenIds.has(screen.id) &&
                !(screen.group_id && bookedGroupIds.has(screen.group_id)),
            )
            const finishes = placement.effective_ends_at ?? placement.ends_at
            // What the sale actually covers. The server refuses the screen that breaches it
            // -- one call per screen, so an over-cap click used to land as "two added, the
            // third failed" in an order nobody could predict. Said up front instead, with
            // the button held, so the operator raises the cap or drops a screen knowingly.
            const capped = placement.plan_max_locations > 0
            const room = capped ? placement.screens_unused : Infinity
            const overCap = missing.length > room

            return (
              <div key={placement.id} className="rounded-xl border border-hairline p-3.5">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <p className="font-semibold text-foreground">{placement.advertiser}</p>
                  <p className="text-sm text-muted-foreground">
                    {rupees(placement.total_price_paise ?? placement.price_paise)}
                  </p>
                </div>
                <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                  <span className="inline-flex items-center gap-1">
                    <CalendarRange className="size-3" />
                    until {finishes ? new Date(finishes).toLocaleDateString() : 'no end date'}
                  </span>
                  <span className="inline-flex items-center gap-1">
                    <MonitorPlay className="size-3" />
                    {capped
                      ? `${placement.screens_used} of ${placement.plan_max_locations} screens`
                      : `${placement.screens_used} screen${placement.screens_used === 1 ? '' : 's'}`}
                  </span>
                </p>

                {missing.length === 0 ? (
                  <p className="mt-3 inline-flex items-center gap-1.5 rounded-lg bg-emerald-500/10 px-2.5 py-1.5 text-xs text-emerald-600 dark:text-emerald-400">
                    <Check className="size-3.5" />
                    Already running on {screens.length === 1 ? 'this screen' : 'these screens'}
                  </p>
                ) : (
                  <>
                    <Button
                      size="sm"
                      className="mt-3"
                      disabled={addTargets.isPending || overCap}
                      onClick={() => addTargets.mutate({ placement, targets: missing })}
                    >
                      <Plus />
                      Add {missing.length === 1 ? 'this screen' : `${missing.length} screens`} to it
                    </Button>
                    {overCap && (
                      <p className="mt-2 rounded-lg bg-amber-500/10 px-2.5 py-1.5 text-xs text-amber-700 dark:text-amber-300">
                        {placement.advertiser} bought {placement.plan_max_locations} screen
                        {placement.plan_max_locations === 1 ? '' : 's'} and is using{' '}
                        {placement.screens_used}
                        {room > 0
                          ? `, so only ${room} more can be added — this would add ${missing.length}.`
                          : `, so there is no room for another.`}{' '}
                        {placement.plan
                          ? 'Move the booking to a larger plan under Change plan.'
                          : 'Raise the screen count under Change plan.'}
                      </p>
                    )}
                  </>
                )}
              </div>
            )
          })}

          <div className="space-y-1.5">
            <Label htmlFor="extend-days" className="text-xs">
              Run length for the added screens (optional)
            </Label>
            <Input
              id="extend-days"
              type="number"
              min={1}
              max={maxDays}
              placeholder="Follows the campaign's end date"
              value={daysOverride}
              onChange={(event) => setDaysOverride(event.target.value)}
            />
            <p className="text-xs text-muted-foreground/70">
              Leave empty and the new screens finish with the campaign. Set a number to sell
              them their own run — &ldquo;30 days in the mall, 10 in the shop&rdquo;.
              {maxDays !== undefined && ` This booking has ${maxDays} day${maxDays === 1 ? '' : 's'} left; extend it to sell longer.`}
            </p>
          </div>
        </div>

        {/* "Create a separate new booking" used to live here, and the server now refuses
            exactly that: one advert has one live sale, because a second booking of a running
            creative bills the client twice, lists the campaign twice on their report and
            spends a second ad slot out of the workspace's quota. A button whose only
            possible outcome is a red error is worse than no button, so it says why instead
            -- and what to do, since both routes to "more" are one menu away on the ad. */}
        <div className="mt-2 border-t border-hairline pt-3">
          <p className="text-center text-xs text-muted-foreground/70">
            This creative can only carry one live booking at a time. To sell it to someone
            else, wait for {live.length === 1 ? `${live[0].advertiser}'s` : 'the current'}{' '}
            campaign to finish — or add screens and time to it above.
            {finishedCount > 0 && ` ${finishedCount} earlier booking${finishedCount === 1 ? ' has' : 's have'} already finished.`}
          </p>
        </div>
      </DialogContent>
    </Dialog>
  )
}
