'use client'

import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  Building2,
  CalendarRange,
  Check,
  IndianRupee,
  Layers3,
  MonitorPlay,
  Receipt,
  Search,
  Sparkles,
} from 'lucide-react'
import { api } from '@/lib/api'
import { addDays, dateInput, rupees } from '@/lib/format'
import { invalidateBookingViews } from '@/lib/query-keys'
import type { Client, Screen, ScreenGroup, TenantPlan } from '@/lib/types'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

interface CreateBookingModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  contentId: number
  contentTitle?: string
  /** Pre-selected screen IDs (e.g. passed from Playlist Builder) */
  defaultScreenIds?: number[]
  /** Initial client ID if known */
  initialClientId?: number | null
  /** Initial advertiser name if known */
  initialAdvertiser?: string
  /** Fired once the booking exists, so the caller can react (the library row animates out). */
  onBooked?: () => void
}

export function CreateBookingModal({
  open,
  onOpenChange,
  contentId,
  contentTitle,
  defaultScreenIds,
  initialClientId,
  initialAdvertiser,
  onBooked,
}: CreateBookingModalProps) {
  const queryClient = useQueryClient()

  const [clientId, setClientId] = useState<string>(initialClientId ? String(initialClientId) : '')
  const [advertiser, setAdvertiser] = useState<string>(initialAdvertiser || '')
  const [planId, setPlanId] = useState<number | null>(null)
  const [price, setPrice] = useState<string>('')
  const [startsAt, setStartsAt] = useState<string>(() => dateInput(new Date()))
  // What the operator typed. The value actually used is `endsAt` below, which prefers the
  // run length being sold until they take the field over.
  const [endsAtInput, setEndsAtInput] = useState<string>(() => dateInput(Date.now() + 30 * 864e5))
  // Whether the operator has set the end date THEMSELVES. Until they do, the booking
  // window follows the longest run being sold -- a bespoke sale states its length per
  // location ("30 in the mall, 10 in the shop") and the longest of those is the
  // campaign. Once they type a date it is theirs and nothing here rewrites it, which is
  // what stops a window a client may already have been invoiced for from moving.
  const [endsAtTouched, setEndsAtTouched] = useState(false)
  const [notes, setNotes] = useState<string>('')
  const [picked, setPicked] = useState<string[]>([])
  const [targetDays, setTargetDays] = useState<Record<string, number>>({})
  // How many screens a CUSTOM sale covers. Blank is the historical meaning -- no cap -- so
  // a negotiated deal that genuinely is open-ended still works, while "three screens for
  // 40,000" finally has somewhere to be written down and something enforcing it.
  const [customScreens, setCustomScreens] = useState('')
  const [placeFilter, setPlaceFilter] = useState<string>('')

  // Load clients
  const { data: clients = [] } = useQuery<Client[]>({
    queryKey: ['clients'],
    queryFn: () => api.getClients(),
    enabled: open,
  })

  // Load active plans
  const { data: plans = [] } = useQuery<TenantPlan[]>({
    queryKey: ['tenant-plans'],
    queryFn: () => api.getTenantPlans(),
    enabled: open,
  })

  // Load screens
  const { data: screens = [] } = useQuery<Screen[]>({
    queryKey: ['screens'],
    queryFn: () => api.getScreens(),
    enabled: open,
  })

  // Load groups
  const { data: groups = [] } = useQuery<ScreenGroup[]>({
    queryKey: ['groups'],
    queryFn: () => api.getGroups(),
    enabled: open,
  })

  // Seed from the advert ONCE per opening, during render rather than in an effect.
  //
  // As an effect this re-ran every time the incoming client changed, which was harmless
  // only while nothing passed one in. Now that the ad's client is fed in, the value
  // arrives from the server a moment AFTER the dialog opens -- so the effect would fire
  // again and overwrite a name the operator had already corrected by hand. Seeding on the
  // first render that actually has something to seed with, and not again until the dialog
  // is closed and reopened, is what stops a late response fighting the person typing.
  const [seeded, setSeeded] = useState(false)
  if (!open && seeded) {
    // Forget it on close, so the next opening reads the advert afresh.
    setSeeded(false)
  }
  if (open && !seeded && (initialClientId || initialAdvertiser || defaultScreenIds?.length)) {
    setSeeded(true)
    setClientId(initialClientId ? String(initialClientId) : '')
    setAdvertiser(initialAdvertiser || '')
    if (defaultScreenIds?.length) {
      setPicked(defaultScreenIds.map((id) => `s${id}`))
    }
  }

  const selectedPlan = useMemo(
    () => (planId != null ? plans.find((p) => p.id === planId) || null : null),
    [plans, planId],
  )

  const selectedClient = useMemo(
    () => (clientId ? clients.find((c) => String(c.id) === clientId) || null : null),
    [clients, clientId],
  )

  // Calculate covered screens count
  const coveredScreenIds = useMemo(() => {
    const directScreenIds = picked
      .filter((k) => k.startsWith('s'))
      .map((k) => Number(k.slice(1)))
    const bookedGroupIds = new Set(
      picked.filter((k) => k.startsWith('g')).map((k) => Number(k.slice(1))),
    )
    const groupScreenIds = screens
      .filter((s) => s.group_id && bookedGroupIds.has(s.group_id))
      .map((s) => s.id)
    return new Set([...directScreenIds, ...groupScreenIds])
  }, [picked, screens])

  const coveredCount = coveredScreenIds.size
  // The cap in force. A package states its own and wins; off one, it is whatever the
  // operator typed for this sale. The server applies exactly this rule (placements
  // .location_cap), so what the badge says and what the save does cannot drift apart.
  const maxAllowedScreens =
    selectedPlan && selectedPlan.max_locations > 0
      ? selectedPlan.max_locations
      : (!selectedPlan && Number(customScreens) > 0 ? Number(customScreens) : null)
  const isOverCap = maxAllowedScreens !== null && coveredCount > maxAllowedScreens

  // Handle plan selection
  const handleSelectPlan = (plan: TenantPlan | null) => {
    if (plan === null) {
      setPlanId(null)
      return
    }
    const previousPlan = selectedPlan
    setPlanId(plan.id)

    // Prefill price if currently empty or if it matched previous plan's list price
    const currentPriceMatchesPrev = previousPlan && price === String(previousPlan.price_paise / 100)
    if (!price.trim() || currentPriceMatchesPrev) {
      setPrice(String(plan.price_paise / 100))
    }

    // Auto calculate endsAt based on plan duration, unless the operator set one.
    if (plan.duration_days > 0 && !endsAtTouched) {
      setEndsAtInput(addDays(startsAt, plan.duration_days))
    }
  }

  // Handle start date modification
  const handleStartDateChange = (newStart: string) => {
    setStartsAt(newStart)
    // `endsAt` re-derives from the new start on its own; this only keeps the typed value
    // underneath it in step for a plan sale, so the field still reads correctly if the
    // operator later takes it over.
    if (selectedPlan && selectedPlan.duration_days > 0 && !endsAtTouched) {
      setEndsAtInput(addDays(newStart, selectedPlan.duration_days))
    }
  }

  // The longest run being sold right now, in days.
  //
  // On a package the package states it. On a bespoke sale it is the longest per-location
  // length, because that is when the campaign actually finishes -- the backend already
  // agrees (AdPlacement.effective_ends_at takes the max of the window, its extensions and
  // every per-location window). Locations left on the default inherit the booking, so they
  // cannot drag the longest DOWN.
  const longestRunDays = useMemo(() => {
    const custom = picked.map((key) => targetDays[key]).filter((d): d is number => Boolean(d && d > 0))
    const longestCustom = custom.length ? Math.max(...custom) : 0
    const planDays = selectedPlan && selectedPlan.duration_days > 0 ? selectedPlan.duration_days : 0
    // The MAX of the two, where a package used to simply win. A location sold longer than
    // the package still runs longer -- effective_ends_at takes the max of the booking
    // window and every per-location window -- so a package that short-circuited here sold
    // the campaign with an end date its own screens then ran past.
    return Math.max(planDays, longestCustom)
  }, [selectedPlan, picked, targetDays])

  // Keep the end date on the longest run until the operator takes it over. Without this a
  // booking sold "10 days here, 30 there" was SOLD with whatever date happened to be in the
  // box -- the screens ran their own windows correctly, but the booking, its invoice and
  // the bookings list all showed a campaign that had already finished.
  //
  // Derived during render rather than written back from an effect. The effect version set
  // state on every change to a value it also depended on, which React flags
  // (react-hooks/set-state-in-effect) because it renders twice for one edit and can loop if
  // the derivation is ever not exactly idempotent. Here the truth is simply "the typed date
  // if the operator typed one, otherwise start + longest run", which needs no state at all.
  const endsAt =
    !endsAtTouched && longestRunDays > 0 && startsAt
      ? addDays(startsAt, longestRunDays)
      : endsAtInput

  // A location cannot be sold longer than the campaign it belongs to -- but only once the
  // operator has FIXED the end date. Until they do, `endsAt` follows the longest location,
  // so bounding these boxes against it would be the field capping itself at whatever was
  // typed first. Afterwards the date is theirs, and a location typed past it would stretch
  // the run anyway (effective_ends_at takes the max over the targets) without a day of it
  // being billed -- the same breach add_target refuses server side.
  // Calculate run duration in days
  const runDurationDays = useMemo(() => {
    if (!startsAt || !endsAt) return 0
    const start = new Date(`${startsAt}T00:00:00`).getTime()
    const end = new Date(`${endsAt}T00:00:00`).getTime()
    const diffDays = Math.round((end - start) / (1000 * 60 * 60 * 24))
    return diffDays > 0 ? diffDays : 0
  }, [startsAt, endsAt])

  const maxLocationDays = endsAtTouched && runDurationDays > 0 ? runDurationDays : 3650

  const toggleTarget = (key: string) => {
    setPicked((prev) =>
      prev.includes(key) ? prev.filter((item) => item !== key) : [...prev, key],
    )
  }

  // Filter place options
  const filteredGroups = useMemo(() => {
    if (!placeFilter.trim()) return groups
    const q = placeFilter.toLowerCase()
    return groups.filter((g) => g.name.toLowerCase().includes(q))
  }, [groups, placeFilter])

  const filteredScreens = useMemo(() => {
    if (!placeFilter.trim()) return screens
    const q = placeFilter.toLowerCase()
    return screens.filter(
      (s) =>
        (s.name || '').toLowerCase().includes(q) ||
        (s.location || '').toLowerCase().includes(q),
    )
  }, [screens, placeFilter])

  // Screens x days, each location counted for the length it was actually sold.
  //
  // One path for both now that a per-location length is offered on a package as well. The
  // custom branch also counted a GROUP as a single screen, so a group of eight sold for ten
  // days was quoted ten screen-days instead of eighty.
  const totalScreenDays = useMemo(() => {
    if (picked.length === 0) return 0
    const base = (selectedPlan?.duration_days || 0) || runDurationDays
    return picked.reduce((acc, key) => {
      const days = targetDays[key] ?? base
      if (days <= 0) return acc
      const screensHere = key.startsWith('g')
        ? screens.filter((screen) => screen.group_id === Number(key.slice(1))).length
        : 1
      return acc + days * screensHere
    }, 0)
  }, [picked, selectedPlan, runDurationDays, targetDays, screens])

  // Blank means the package's list price -- which is what the box's placeholder shows -- not
  // free. A free booking is a typed 0, and off a package the price has to be entered; it used
  // to be saved as 0 without a word, which then read as an unpaid-for ad on every invoice.
  const priceRupees =
    price.trim() !== '' ? Number(price) : selectedPlan ? selectedPlan.price_paise / 100 : null

  // Mutation to create booking
  const createMutation = useMutation({
    mutationFn: () => {
      const targets = picked.map((key) => {
        const isScreen = key.startsWith('s')
        const id = Number(key.slice(1))
        const customDays = targetDays[key]
        return {
          ...(isScreen ? { screen_id: id } : { group_id: id }),
          ...(customDays && customDays > 0 ? { days: customDays } : {}),
        }
      })
      return api.createPlacement({
        content_id: contentId,
        client_id: clientId ? Number(clientId) : undefined,
        advertiser: clientId ? undefined : advertiser.trim(),
        plan_id: planId,
        // Only meaningful off a package, and 0 means the booking is uncapped -- which is
        // what leaving the box empty has always meant for a custom sale.
        max_locations: planId === null && Number(customScreens) > 0 ? Number(customScreens) : 0,
        price_paise: Math.round((priceRupees ?? 0) * 100),
        is_paid: false,
        starts_at: new Date(`${startsAt}T00:00:00`).toISOString(),
        ends_at: new Date(`${endsAt}T23:59:59`).toISOString(),
        notes: notes.trim() || null,
        targets,
      })
    },
    onSuccess: () => {
      invalidateBookingViews(queryClient)
      toast.success('Booking created and scheduled successfully')
      onBooked?.()
      onOpenChange(false)
      // Reset form
      setClientId('')
      setAdvertiser('')
      setPlanId(null)
      setPrice('')
      setNotes('')
      setPicked([])
      setTargetDays({})
      setCustomScreens('')
      // Or the next booking silently inherits the last operator's hand-typed date and
      // stops following the run being sold.
      setEndsAtTouched(false)
    },
    onError: (err: Error) => {
      toast.error(err.message || 'Failed to create booking')
    },
  })

  const isValid =
    (Boolean(clientId) || Boolean(advertiser.trim())) &&
    picked.length > 0 &&
    endsAt > startsAt &&
    priceRupees !== null && Number.isFinite(priceRupees) && priceRupees >= 0 &&
    !isOverCap

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-2xl sm:p-7">
        <DialogHeader>
          <div className="flex items-center gap-2.5">
            <div className="size-9 rounded-xl bg-primary/10 text-primary grid place-items-center">
              <Receipt className="size-5" />
            </div>
            <div>
              <DialogTitle className="text-xl">Create New Booking</DialogTitle>
              <DialogDescription className="text-xs text-muted-foreground mt-0.5">
                {contentTitle ? (
                  <>Selling <strong className="text-foreground">{contentTitle}</strong> to a client.</>
                ) : (
                  'Record commercial agreement, pricing plan, and target screens for this ad.'
                )}
              </DialogDescription>
            </div>
          </div>
        </DialogHeader>

        <div className="space-y-6 py-2">
          {/* Section: Client Selection */}
          <div className="rounded-2xl border border-primary/20 bg-primary/[0.02] p-4 sm:p-5 space-y-3.5">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Building2 className="size-4 text-primary" />
                <Label htmlFor="booking-client" className="text-xs font-bold uppercase tracking-wider text-primary">
                  Client / Advertiser <span className="text-rose-500">*</span>
                </Label>
              </div>
              {selectedClient && (
                <Badge variant="outline" className="text-[10px] font-medium border-primary/30 text-primary">
                  {selectedClient.client_code}
                </Badge>
              )}
            </div>

            <div className="space-y-2">
              <select
                id="booking-client"
                className="h-10 w-full rounded-xl border border-input bg-background px-3 text-sm focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
                value={clientId}
                onChange={(e) => {
                  setClientId(e.target.value)
                  if (e.target.value) setAdvertiser('')
                }}
              >
                <option value="">— Select registered client or enter brand name below —</option>
                {clients.map((c) => (
                  <option key={c.id} value={String(c.id)}>
                    {c.name} ({c.client_code}){c.email ? ` • ${c.email}` : ''}
                  </option>
                ))}
              </select>

              {!clientId && (
                <div className="pt-1">
                  <Input
                    placeholder="Or type brand / advertiser name (e.g. Moolans Grand Store)"
                    value={advertiser}
                    onChange={(e) => setAdvertiser(e.target.value)}
                    className="text-sm bg-background"
                  />
                </div>
              )}

              {selectedClient && (selectedClient.email || selectedClient.phone) && (
                <p className="text-[11px] text-muted-foreground mt-1">
                  Contact: {[selectedClient.email, selectedClient.phone].filter(Boolean).join(' • ')}
                </p>
              )}
            </div>
          </div>

          {/* Section: Pricing Plan & Package (Visual Cards) */}
          <div className="space-y-2.5">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-1.5">
                <Sparkles className="size-3.5 text-primary" />
                <Label className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                  Pricing Plan &amp; Package
                </Label>
              </div>
              {selectedPlan ? (
                <Badge variant="outline" className="text-[11px] font-semibold text-emerald-500 border-emerald-500/30">
                  {rupees(selectedPlan.price_paise)} • {selectedPlan.duration_days} Days
                </Badge>
              ) : (
                <Badge variant="outline" className="text-[11px] font-semibold text-primary border-primary/30">
                  Custom Terms
                </Badge>
              )}
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
              {/* Custom Package Card */}
              <button
                type="button"
                onClick={() => handleSelectPlan(null)}
                className={`text-left p-3.5 rounded-xl border transition-all ${
                  planId === null
                    ? 'border-primary bg-primary/10 shadow-sm ring-1 ring-primary/30'
                    : 'border-border/60 hover:border-border hover:bg-muted/30'
                }`}
              >
                <div className="flex items-center justify-between">
                  <span className="font-semibold text-xs text-foreground">Custom — no package</span>
                  <Badge variant="secondary" className="text-[10px] font-semibold">
                    You set price
                  </Badge>
                </div>
                <p className="mt-1.5 text-[11px] text-muted-foreground leading-snug">
                  Negotiated deal without a package. Custom price, custom duration, and the
                  screen count you agree below.
                </p>
              </button>

              {/* Active Plans Cards */}
              {plans.map((p) => {
                const isSelected = planId === p.id
                return (
                  <button
                    key={p.id}
                    type="button"
                    onClick={() => handleSelectPlan(p)}
                    className={`text-left p-3.5 rounded-xl border transition-all ${
                      isSelected
                        ? 'border-primary bg-primary/10 shadow-sm ring-1 ring-primary/30'
                        : 'border-border/60 hover:border-border hover:bg-muted/30'
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-semibold text-xs text-foreground truncate">{p.name}</span>
                      <span className="text-xs font-bold text-primary shrink-0">{rupees(p.price_paise)}</span>
                    </div>
                    <div className="flex items-center gap-2 mt-1.5 text-[11px] text-muted-foreground">
                      <span>{p.duration_days} days</span>
                      <span>•</span>
                      <span>Max {p.max_locations} screen{p.max_locations > 1 ? 's' : ''}</span>
                      {!p.is_active && <Badge variant="warning" className="text-[9px]">retired</Badge>}
                    </div>
                  </button>
                )
              })}
            </div>
          </div>

          {/* How many screens a custom deal covers. A package answers this itself, so the box
              only appears off one -- and blank keeps the old meaning, no cap, rather than
              forcing a number onto a deal that genuinely is open-ended. The server enforces
              the same rule everywhere afterwards: Add places, the screen page and the
              playlist builder all refuse the screen that breaches it. */}
          {planId === null && (
            <div className="grid grid-cols-1 sm:grid-cols-[200px_minmax(0,1fr)] gap-3 items-center rounded-xl border border-border/60 bg-muted/20 p-3">
              <div className="space-y-1.5">
                <Label htmlFor="booking-custom-screens" className="text-xs font-medium">
                  Screens included
                </Label>
                <div className="relative">
                  <MonitorPlay className="absolute left-3 top-1/2 -translate-y-1/2 size-3.5 text-muted-foreground" />
                  <Input
                    id="booking-custom-screens"
                    type="number"
                    min={0}
                    max={10000}
                    value={customScreens}
                    onChange={(e) => setCustomScreens(e.target.value)}
                    placeholder="No limit"
                    className="pl-9 text-sm bg-background"
                  />
                </div>
              </div>
              <p className="text-[11px] text-muted-foreground leading-snug">
                What the client is buying. Leave it empty for an open-ended deal; set a number
                and this booking can never be given a screen beyond it — here or anywhere
                else. Editable later under <span className="text-foreground">Change plan</span>.
              </p>
            </div>
          )}

          {/* Section: Price & Booking Schedule */}
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 pt-1">
            <div className="space-y-1.5">
              <Label htmlFor="booking-price" className="text-xs font-medium">
                Agreed Price (₹)
              </Label>
              <div className="relative">
                <IndianRupee className="absolute left-3 top-1/2 -translate-y-1/2 size-3.5 text-muted-foreground" />
                <Input
                  id="booking-price"
                  type="number"
                  min={0}
                  step="0.01"
                  value={price}
                  onChange={(e) => setPrice(e.target.value)}
                  placeholder={selectedPlan ? String(selectedPlan.price_paise / 100) : 'e.g. 25000'}
                  className="pl-9 text-sm bg-background"
                />
              </div>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="booking-from" className="text-xs font-medium">
                Starts From
              </Label>
              <div className="relative">
                <CalendarRange className="absolute left-3 top-1/2 -translate-y-1/2 size-3.5 text-muted-foreground" />
                <Input
                  id="booking-from"
                  type="date"
                  value={startsAt}
                  onChange={(e) => handleStartDateChange(e.target.value)}
                  className="pl-9 text-sm bg-background"
                />
              </div>
            </div>

            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <Label htmlFor="booking-until" className="text-xs font-medium">
                  Runs Until
                </Label>
                {runDurationDays > 0 && (
                  <span className="text-[10px] font-semibold text-muted-foreground">
                    {runDurationDays} days
                  </span>
                )}
              </div>
              <div className="relative">
                <CalendarRange className="absolute left-3 top-1/2 -translate-y-1/2 size-3.5 text-muted-foreground" />
                <Input
                  id="booking-until"
                  type="date"
                  value={endsAt}
                  onChange={(e) => { setEndsAtTouched(true); setEndsAtInput(e.target.value) }}
                  className="pl-9 text-sm bg-background"
                />
              </div>
            </div>
          </div>

          {/* Section: Where it plays (Screens & Groups) */}
          <div className="space-y-2.5">
            <div className="flex items-center justify-between">
              <Label className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                Where it plays (Locations) <span className="text-rose-500">*</span>
              </Label>
              <div className="flex items-center gap-2">
                {picked.length > 0 && planId === null && (
                  <Badge variant="secondary" className="text-[10px] font-semibold bg-primary/10 text-primary border-primary/20">
                    {totalScreenDays} screen-days
                    {Number(price) > 0 && totalScreenDays > 0 ? ` • ~₹${Math.round(Number(price) / totalScreenDays)}/day` : ''}
                  </Badge>
                )}
                <Badge
                  variant={isOverCap ? 'danger' : 'outline'}
                  className="text-[11px] font-semibold"
                >
                  {maxAllowedScreens !== null
                    ? `${coveredCount} of ${maxAllowedScreens} screens covered`
                    : `${coveredCount} screen${coveredCount === 1 ? '' : 's'} selected`}
                </Badge>
              </div>
            </div>

            {isOverCap && (
              <div className="p-2.5 rounded-xl bg-destructive/10 border border-destructive/30 text-destructive text-xs">
                {selectedPlan
                  ? `Your selected plan (${selectedPlan.name}) allows a maximum of ${maxAllowedScreens} screen(s), but your current selection covers ${coveredCount} screens. Please adjust your target screens/groups or switch to Custom.`
                  : `This booking is being sold ${maxAllowedScreens} screen(s), but your current selection covers ${coveredCount}. Raise "Screens included" above, or unpick a location.`}
              </div>
            )}

            {/* Filter Search */}
            {(screens.length > 6 || groups.length > 2) && (
              <div className="relative">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 size-3.5 text-muted-foreground" />
                <Input
                  placeholder="Filter screens or groups…"
                  value={placeFilter}
                  onChange={(e) => setPlaceFilter(e.target.value)}
                  className="pl-8 text-xs bg-background h-8"
                />
              </div>
            )}

            {screens.length === 0 && groups.length === 0 ? (
              <div className="rounded-xl border border-dashed border-border/70 p-4 text-center text-xs text-muted-foreground">
                No active screens or groups available in this workspace.
              </div>
            ) : (
              <div className="max-h-60 overflow-y-auto space-y-2 rounded-xl border border-border/60 bg-muted/20 p-2.5">
                {/* Groups */}
                {filteredGroups.length > 0 && (
                  <div className="space-y-1.5 pb-1">
                    <p className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider px-1">
                      Venue Groups
                    </p>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                      {filteredGroups.map((group) => {
                        const key = `g${group.id}`
                        const isChecked = picked.includes(key)
                        const groupScreenCount = screens.filter((s) => s.group_id === group.id).length
                        const currentDays = targetDays[key] ?? runDurationDays
                        return (
                          <div
                            key={key}
                            className={`p-2.5 rounded-xl border text-left transition-all ${
                              isChecked
                                ? 'border-primary/60 bg-primary/[0.04] text-foreground shadow-sm'
                                : 'border-border/50 hover:bg-muted/40 text-muted-foreground'
                            }`}
                          >
                            <div
                              role="button"
                              tabIndex={0}
                              onClick={() => toggleTarget(key)}
                              onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') toggleTarget(key) }}
                              className="flex items-center gap-2 cursor-pointer select-none"
                            >
                              <div
                                className={`size-4 rounded border grid place-items-center shrink-0 transition-colors ${
                                  isChecked ? 'border-primary bg-primary text-primary-foreground' : 'border-muted-foreground/40'
                                }`}
                              >
                                {isChecked && <Check className="size-3" />}
                              </div>
                              <Layers3 className="size-3.5 text-primary shrink-0" />
                              <div className="min-w-0 flex-1">
                                <p className="text-xs font-medium truncate text-foreground">{group.name}</p>
                                <p className="text-[10px] text-muted-foreground truncate">
                                  {groupScreenCount} screen{groupScreenCount === 1 ? '' : 's'}
                                </p>
                              </div>
                              <Badge variant="secondary" className="text-[9px] shrink-0">Group</Badge>
                            </div>

                            {/* Custom airtime days per venue group */}
                            {/* Offered on a package too. The server honours a per-location length either
                                way (place_advert reads ref.days with no reference to the plan, and a
                                plan caps only HOW MANY locations), the "Add places" dialog has always
                                sent one for a booking already on a package, and the locations list
                                renders it back -- so hiding it only here meant a run sold "50 days at
                                the airport, 30 everywhere else" could not be entered at the point of
                                sale, only bolted on afterwards. */}
                            {isChecked && (
                              <div
                                className="flex items-center justify-between gap-1.5 pt-2 mt-2 border-t border-primary/20 text-xs"
                                onClick={(e) => e.stopPropagation()}
                              >
                                <span className="text-[11px] text-muted-foreground font-medium">Airtime:</span>
                                <div className="flex items-center gap-1.5">
                                  <input
                                    type="number"
                                    min={1}
                                    max={maxLocationDays}
                                    value={currentDays}
                                    onChange={(e) => {
                                      const val = parseInt(e.target.value, 10)
                                      setTargetDays((prev) => ({
                                        ...prev,
                                        [key]: isNaN(val) ? 1 : Math.max(1, Math.min(maxLocationDays, val)),
                                      }))
                                    }}
                                    className="w-14 h-6 px-1 text-center text-xs font-bold rounded-lg border border-input bg-background text-foreground focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
                                  />
                                  <span className="text-[11px] text-muted-foreground">days</span>
                                  <span className="text-[10px] text-primary font-medium ml-1">
                                    (until {addDays(startsAt, currentDays)})
                                  </span>
                                </div>
                              </div>
                            )}
                          </div>
                        )
                      })}
                    </div>
                  </div>
                )}

                {/* Individual Screens */}
                {filteredScreens.length > 0 && (
                  <div className="space-y-1.5 pt-1">
                    <p className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider px-1">
                      Individual Screens
                    </p>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                      {filteredScreens.map((screen) => {
                        const key = `s${screen.id}`
                        const isChecked = picked.includes(key)
                        const currentDays = targetDays[key] ?? runDurationDays
                        return (
                          <div
                            key={key}
                            className={`p-2.5 rounded-xl border text-left transition-all ${
                              isChecked
                                ? 'border-primary/60 bg-primary/[0.04] text-foreground shadow-sm'
                                : 'border-border/50 hover:bg-muted/40 text-muted-foreground'
                            }`}
                          >
                            <div
                              role="button"
                              tabIndex={0}
                              onClick={() => toggleTarget(key)}
                              onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') toggleTarget(key) }}
                              className="flex items-center gap-2 cursor-pointer select-none"
                            >
                              <div
                                className={`size-4 rounded border grid place-items-center shrink-0 transition-colors ${
                                  isChecked ? 'border-primary bg-primary text-primary-foreground' : 'border-muted-foreground/40'
                                }`}
                              >
                                {isChecked && <Check className="size-3" />}
                              </div>
                              <MonitorPlay className="size-3.5 text-muted-foreground shrink-0" />
                              <div className="min-w-0 flex-1">
                                <p className="text-xs font-medium truncate text-foreground">
                                  {screen.name || `Screen #${screen.id}`}
                                </p>
                                <p className="text-[10px] text-muted-foreground truncate">
                                  {screen.location || 'Default Location'}
                                </p>
                              </div>
                            </div>

                            {/* Custom airtime days per screen */}
                            {/* Offered on a package too. The server honours a per-location length either
                                way (place_advert reads ref.days with no reference to the plan, and a
                                plan caps only HOW MANY locations), the "Add places" dialog has always
                                sent one for a booking already on a package, and the locations list
                                renders it back -- so hiding it only here meant a run sold "50 days at
                                the airport, 30 everywhere else" could not be entered at the point of
                                sale, only bolted on afterwards. */}
                            {isChecked && (
                              <div
                                className="flex items-center justify-between gap-1.5 pt-2 mt-2 border-t border-primary/20 text-xs"
                                onClick={(e) => e.stopPropagation()}
                              >
                                <span className="text-[11px] text-muted-foreground font-medium">Airtime:</span>
                                <div className="flex items-center gap-1.5">
                                  <input
                                    type="number"
                                    min={1}
                                    max={maxLocationDays}
                                    value={currentDays}
                                    onChange={(e) => {
                                      const val = parseInt(e.target.value, 10)
                                      setTargetDays((prev) => ({
                                        ...prev,
                                        [key]: isNaN(val) ? 1 : Math.max(1, Math.min(maxLocationDays, val)),
                                      }))
                                    }}
                                    className="w-14 h-6 px-1 text-center text-xs font-bold rounded-lg border border-input bg-background text-foreground focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
                                  />
                                  <span className="text-[11px] text-muted-foreground">days</span>
                                  <span className="text-[10px] text-primary font-medium ml-1">
                                    (until {addDays(startsAt, currentDays)})
                                  </span>
                                </div>
                              </div>
                            )}
                          </div>
                        )
                      })}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Section: Notes */}
          <div className="space-y-1.5">
            <Label htmlFor="booking-notes" className="text-xs font-medium text-muted-foreground">
              Campaign Notes / PO Reference (Optional)
            </Label>
            <Input
              id="booking-notes"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="e.g. Diwali festive campaign, contract ref #982"
              className="text-xs bg-background"
            />
          </div>
        </div>

        <DialogFooter className="gap-2 sm:gap-0 mt-4">
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            type="button"
            disabled={!isValid || createMutation.isPending}
            onClick={() => createMutation.mutate()}
            className="font-semibold shadow-md"
          >
            {createMutation.isPending ? 'Creating Booking…' : 'Create Booking'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
