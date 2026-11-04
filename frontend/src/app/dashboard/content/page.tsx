'use client'

import { useMemo, useRef, useState } from 'react'
import { useBulkSelection } from '@/hooks/use-bulk-selection'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Building2, CheckCircle2, ImageIcon, RefreshCw, Search, Target, Trash2, Upload, UploadCloud, Video, X } from 'lucide-react'
import { toast } from 'sonner'
import { EmptyState } from '@/components/dashboard/empty-state'
import { ErrorState } from '@/components/dashboard/error-state'
import { AssetCard, AssetGrid, OverlayBadge } from '@/components/dashboard/asset-card'
import { BulkActionBar, SelectAllCheckbox } from '@/components/dashboard/bulk-action-bar'
import { bulkSummary, runBulk } from '@/lib/bulk'
import { ListToolbar, commonSorts, sortItems, type CommonSort } from '@/components/dashboard/list-toolbar'
import { MediaThumbnail } from '@/components/dashboard/media-thumbnail'
import { EditClientAdModal } from '@/components/dashboard/edit-client-ad-modal'
import { StorageWarning } from '@/components/dashboard/storage-warning'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { api } from '@/lib/api'
import { DropdownMenuItem } from '@/components/ui/dropdown-menu'
import { assetOrientation, clipDuration, expiryLabel, relativeTime, rupees } from '@/lib/format'
import { useAuthStore } from '@/lib/store'
import { cn } from '@/lib/utils'
import { canEditTenantContent } from '@/lib/roles'
import type { ContentItem } from '@/lib/types'

type QueuedUpload = {
  file: File
  name: string
  status: 'queued' | 'uploading' | 'done' | 'error'
  progress: number
  error?: string
}

const stripExtension = (filename: string) => filename.replace(/\.[^.]+$/, '')
const isSupported = (file: File) => file.type.startsWith('image/') || file.type.startsWith('video/')

export default function ContentPage() {
  const queryClient = useQueryClient()
  const user = useAuthStore((state) => state.user)
  const canEdit = canEditTenantContent(user)

  const contentQuery = useQuery({
    queryKey: ['content'],
    queryFn: api.getContent,
    refetchInterval: (query) => {
      const data = query.state.data as ContentItem[] | undefined
      return data?.some((item) => item.status === 'processing') ? 3000 : false
    },
  })

  const content = useMemo(() => contentQuery.data || [], [contentQuery.data])

  const clientsQuery = useQuery({ queryKey: ['clients'], queryFn: api.getClients })
  const screensQuery = useQuery({ queryKey: ['screens'], queryFn: api.getScreens })
  const sellScreens = screensQuery.data || []
  const plansQuery = useQuery({ queryKey: ['tenant-plans'], queryFn: () => api.getTenantPlans() })
  const sellClients = clientsQuery.data || []
  const sellPlans = plansQuery.data || []

  const [uploadOpen, setUploadOpen] = useState(false)
  const [uploadMode, setUploadMode] = useState<'ad' | 'general'>('ad')
  const [queue, setQueue] = useState<QueuedUpload[]>([])
  const [tags, setTags] = useState('')

  // Client Selection / Creation state. Defaults to NEW client: uploading a new ad almost
  // always means a new advertiser, so that is the first thing offered; "Pick existing
  // client" switches away.
  const [isNewClient, setIsNewClient] = useState(true)
  const [newClientName, setNewClientName] = useState('')
  const [newClientEmail, setNewClientEmail] = useState('')
  const [newClientPhone, setNewClientPhone] = useState('')
  const [sellClientId, setSellClientId] = useState('')
  const [sellPlanId, setSellPlanId] = useState('')
  // Custom (no-package) pricing, mirroring the booking modal's "Custom — no package": the
  // operator sets the price and the run length themselves. Only read when sellPlanId is the
  // 'custom' sentinel.
  const [sellPrice, setSellPrice] = useState('')
  // The DEFAULT run length; each selected screen can override it below (per-TV airtime),
  // exactly like the booking modal's "custom airtime days per screen".
  const [sellDays, setSellDays] = useState('30')
  const [screenDays, setScreenDays] = useState<Record<number, string>>({})
  const [sellScreenIds, setSellScreenIds] = useState<number[]>([])
  const [uploading, setUploading] = useState(false)
  const [dragging, setDragging] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)
  // The client this dialog has already created, so retrying a partly failed upload reuses it
  // instead of creating the same company a second time (or being refused for the duplicate name).
  const createdClient = useRef<{ typed: string; id: number; name: string } | null>(null)

  const chosenPlan = sellPlans.find((plan) => String(plan.id) === sellPlanId)
  const isCustomPlan = sellPlanId === 'custom'
  // A real plan caps screens at its max_locations; a custom deal has no package and no cap.
  const screenCap = chosenPlan ? chosenPlan.max_locations : Infinity

  const [search, setSearch] = useState('')
  const [sort, setSort] = useState<CommonSort>('newest')
  const [tagFilter, setTagFilter] = useState<string | null>(null)
  const [deleteItem, setDeleteItem] = useState<ContentItem | null>(null)
  const [editingClientItem, setEditingClientItem] = useState<ContentItem | null>(null)
  const [bulkDeleteOpen, setBulkDeleteOpen] = useState(false)
  const [pageDragDepth, setPageDragDepth] = useState(0)

  const allTags = useMemo(() => [...new Set(content.flatMap((item) => item.tags?.split(',').map((tag) => tag.trim()).filter(Boolean) || []))].sort(), [content])
  const filtered = useMemo(() => {
    const matches = content.filter((item) => {
      const matchesSearch = item.name.toLowerCase().includes(search.toLowerCase()) || item.tags?.toLowerCase().includes(search.toLowerCase())
      const matchesTag = !tagFilter || item.tags?.split(',').map((tag) => tag.trim()).includes(tagFilter)
      return matchesSearch && matchesTag
    })
    return sortItems(matches, sort, (item) => item.name, (item) => item.uploaded_at)
  }, [content, search, sort, tagFilter])

  const bulk = useBulkSelection(filtered)

  // Which of the selected assets carry a booking, so the bulk dialog can name them.
  const soldInSelection = useMemo(
    () => content.filter((item) => bulk.selected.includes(item.id) && item.placement_id),
    [content, bulk.selected],
  )


  const bulkDelete = useMutation({
    mutationFn: () => runBulk(bulk.selected, (id) => api.deleteContent(id)),
    // Whatever happened: a run that part-failed has still deleted some, and a list that is not
    // told shows them, still selected, for the next attempt to fail on.
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ['content'] })
      queryClient.invalidateQueries({ queryKey: ['playlists'] })
    },
    onSuccess: ({ done, failed }) => {
      setBulkDeleteOpen(false)
      if (failed.length) {
        toast.error(bulkSummary('deleted', done, failed))
        return
      }
      toast.success(`${done} asset${done === 1 ? '' : 's'} deleted`)
      bulk.clear()
    },
    onError: (error: Error) => toast.error(error.message),
  })

  const addFiles = (files: FileList | null) => {
    if (!files?.length) return
    const accepted = [...files].filter(isSupported)
    const rejected = files.length - accepted.length
    if (rejected) toast.error(`${rejected} file${rejected === 1 ? '' : 's'} skipped — only images and video can be uploaded`)
    if (!accepted.length) return
    setQueue((current) => [
      ...current,
      ...accepted.map((file) => ({ file, name: stripExtension(file.name), status: 'queued' as const, progress: 0 })),
    ])
  }

  const patchItem = (index: number, patch: Partial<QueuedUpload>) =>
    setQueue((current) => current.map((entry, position) => position === index ? { ...entry, ...patch } : entry))

  const startUploads = async () => {
    if (uploadMode === 'ad') {
      if (isNewClient && !newClientName.trim()) {
        toast.error('Please enter the client / advertiser company name')
        return
      }
      if (!isNewClient && !sellClientId) {
        toast.error('Please select an advertiser client or click + Quick Add Client')
        return
      }
      if (!sellPlanId) {
        toast.error('Please select a pricing plan for this ad')
        return
      }
      if (isCustomPlan) {
        // A custom deal has no plan to borrow a price or duration from, so both are required
        // here — the backend rejects a placement with no end date and no plan.
        if (!sellPrice.trim() || Number(sellPrice) <= 0) {
          toast.error('Enter the custom price for this ad')
          return
        }
        if (!sellDays.trim() || Number(sellDays) < 1) {
          toast.error('Enter how many days the custom booking runs')
          return
        }
      }
      if (!sellScreenIds.length) {
        toast.error('Please select at least 1 screen to deploy this ad')
        return
      }
    }

    setUploading(true)
    let resolvedClientId: number | undefined = undefined
    let resolvedClientName = ''

    if (uploadMode === 'ad') {
      if (isNewClient) {
        const already = createdClient.current
        if (already && already.typed === newClientName.trim()) {
          resolvedClientId = already.id
          resolvedClientName = already.name
        } else {
          try {
            const created = await api.createClient({
              name: newClientName.trim(),
              email: newClientEmail.trim() || undefined,
              phone: newClientPhone.trim() || undefined,
            })
            createdClient.current = { typed: newClientName.trim(), id: created.id, name: created.name }
            resolvedClientId = created.id
            resolvedClientName = created.name
            queryClient.invalidateQueries({ queryKey: ['clients'] })
          } catch (err) {
            toast.error(`Could not create client: ${(err as Error).message}`)
            setUploading(false)
            return
          }
        }
      } else {
        resolvedClientId = Number(sellClientId)
        resolvedClientName = sellClients.find((c) => c.id === resolvedClientId)?.name || 'Client'
      }
    }

    // One clock read for the whole upload: every file shares the same booking window, and
    // it keeps the single impure Date() call out of the per-file loop.
    const bookingStartIso = new Date().toISOString()
    // Per-screen run lengths for a custom deal: each TV gets its own days, defaulting to the
    // base duration. Every target carries its own `days` so the backend writes a per-location
    // window (AdPlacementTarget), and the booking-level end must cover the longest of them.
    const baseDays = Math.max(1, Math.floor(Number(sellDays) || 1))
    const customTargetDays: Record<number, number> = {}
    let maxCustomDays = baseDays
    if (isCustomPlan) {
      for (const id of sellScreenIds) {
        const raw = screenDays[id]
        const parsed = raw !== undefined && raw !== '' ? Math.floor(Number(raw)) : baseDays
        const days = Number.isFinite(parsed) && parsed >= 1 ? parsed : baseDays
        customTargetDays[id] = days
        if (days > maxCustomDays) maxCustomDays = days
      }
    }
    const customEndsIso = isCustomPlan
      ? new Date(Date.parse(bookingStartIso) + maxCustomDays * 86_400_000).toISOString()
      : undefined

    let failures = 0
    let unbooked = 0
    for (const [index, entry] of queue.entries()) {
      if (entry.status === 'done') continue
      patchItem(index, { status: 'uploading', progress: 0, error: undefined })
      try {
        const uploaded = await api.uploadContent(
          entry.file,
          entry.name.trim() || entry.file.name,
          tags,
          (percent) => patchItem(index, { progress: percent })
        )

        if (uploadMode === 'ad' && uploaded?.id) {
          try {
            await api.createPlacement({
              content_id: uploaded.id,
              client_id: resolvedClientId,
              advertiser: resolvedClientName,
              // Custom = no package: plan_id null, price the operator set, and an explicit
              // end date (the backend takes duration from the plan otherwise, and a custom
              // deal has none). Same shape the booking modal's custom path sends.
              plan_id: isCustomPlan ? null : Number(sellPlanId),
              price_paise: isCustomPlan
                ? Math.round(Number(sellPrice) * 100)
                : (chosenPlan ? chosenPlan.price_paise : 0),
              ...(isCustomPlan ? { ends_at: customEndsIso } : {}),
              is_paid: false,
              starts_at: bookingStartIso,
              // Custom: each screen carries its own run length; a plan booking lets the
              // plan's duration apply uniformly.
              targets: sellScreenIds.map((id) =>
                isCustomPlan ? { screen_id: id, days: customTargetDays[id] } : { screen_id: id },
              ),
            })
          } catch (reason) {
            unbooked += 1
            toast.error(`Uploaded "${entry.name}", but ad booking failed: ${reason instanceof Error ? reason.message : 'unknown error'}`)
          }
        }
        patchItem(index, { status: 'done', progress: 100 })
      } catch (reason) {
        failures += 1
        patchItem(index, { status: 'error', error: reason instanceof Error ? reason.message : 'Upload failed' })
      }
    }

    setUploading(false)
    queryClient.invalidateQueries({ queryKey: ['content'] })
    queryClient.invalidateQueries({ queryKey: ['placements'] })
    queryClient.invalidateQueries({ queryKey: ['all-placements'] })
    queryClient.invalidateQueries({ queryKey: ['clients'] })
    queryClient.invalidateQueries({ queryKey: ['playlists'] })
    queryClient.invalidateQueries({ queryKey: ['screens'] })

    const uploaded = queue.length - failures
    if (failures === 0) {
      if (uploadMode === 'ad' && unbooked > 0) {
        // The files are in the library; only the booking failed. Saying "booked" here was
        // untrue, and it is the one message the operator reads before closing the dialog.
        toast.warning(`${uploaded} uploaded, but ${unbooked} could not be booked for ${resolvedClientName}. Book them from the library.`)
      } else if (uploadMode === 'ad') {
        toast.success(`${uploaded} ad${uploaded === 1 ? '' : 's'} uploaded & booked for ${resolvedClientName}!`)
      } else {
        toast.success(`${uploaded} file${uploaded === 1 ? '' : 's'} uploaded`)
      }
      closeUpload(false)
    } else {
      toast.error(`${failures} of ${queue.length} upload${queue.length === 1 ? '' : 's'} failed`)
    }
  }

  const closeUpload = (open: boolean) => {
    if (open) {
      setUploadOpen(true)
      if (!sellPlanId && sellPlans.length > 0) {
        setSellPlanId(String(sellPlans[0].id))
      }
      return
    }
    if (uploading) return
    setUploadOpen(false)
    createdClient.current = null
    setQueue([])
    setTags('')
    setIsNewClient(true)
    setNewClientName('')
    setNewClientEmail('')
    setNewClientPhone('')
    setSellClientId('')
    setSellPlanId('')
    setSellPrice('')
    setSellDays('30')
    setScreenDays({})
    setSellScreenIds([])
    setDragging(false)
  }

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteContent(id),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['content'] }); queryClient.invalidateQueries({ queryKey: ['playlists'] }); toast.success('Media removed'); setDeleteItem(null) },
    onError: (error: Error) => toast.error(error.message),
  })
  const retryMutation = useMutation({
    mutationFn: (id: number) => api.retryContentProcessing(id),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['content'] }); toast.success('Retry queued') },
    onError: (error: Error) => toast.error(error.message),
  })

  if (contentQuery.isError) return <ErrorState message={`The media library could not be loaded. Error: ${contentQuery.error?.message}`} onRetry={() => contentQuery.refetch()} />

  const pending = queue.filter((entry) => entry.status !== 'done').length

  const uploadDialog = (
    <Dialog open={uploadOpen} onOpenChange={closeUpload}>
      <DialogTrigger render={<Button />}><Upload data-icon="inline-start" /> Upload media</DialogTrigger>
      <DialogContent className="sm:max-w-lg max-h-[90dvh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Upload media &amp; ad creatives</DialogTitle>
          <DialogDescription>Add advert creatives for clients or general signage content.</DialogDescription>
        </DialogHeader>

        <div className="space-y-4 pt-1 pb-3">
          {/* Mode Switcher */}
          <div className="flex items-center gap-1 p-1 bg-muted/60 rounded-xl border border-border/40">
            <button
              type="button"
              onClick={() => setUploadMode('ad')}
              className={`flex-1 py-1.5 px-3 text-xs font-semibold rounded-lg flex items-center justify-center gap-1.5 transition-all ${
                uploadMode === 'ad'
                  ? 'bg-card text-foreground shadow-sm'
                  : 'text-muted-foreground hover:text-foreground'
              }`}
            >
              <Target className="size-3.5 text-primary dark:text-brand" />
              Client Advertisement
            </button>
            <button
              type="button"
              onClick={() => setUploadMode('general')}
              className={`flex-1 py-1.5 px-3 text-xs font-semibold rounded-lg flex items-center justify-center gap-1.5 transition-all ${
                uploadMode === 'general'
                  ? 'bg-card text-foreground shadow-sm'
                  : 'text-muted-foreground hover:text-foreground'
              }`}
            >
              <ImageIcon className="size-3.5" />
              General Media
            </button>
          </div>

          {/* Client Details Section when in 'ad' mode */}
          {uploadMode === 'ad' && (
            <div className="rounded-xl border border-border/70 bg-muted/30 p-3.5 space-y-3">
              <div className="flex items-center justify-between">
                <Label className="text-xs font-semibold flex items-center gap-1.5 text-foreground">
                  <Building2 className="size-3.5 text-primary dark:text-brand" /> Advertiser Client *
                </Label>
                <button
                  type="button"
                  onClick={() => setIsNewClient(!isNewClient)}
                  className="text-xs text-primary dark:text-brand hover:underline font-medium flex items-center gap-1 cursor-pointer"
                >
                  {isNewClient ? '← Pick existing client' : '+ Quick Add Client'}
                </button>
              </div>

              {isNewClient ? (
                <div className="space-y-2 pt-1 border-t border-border/40">
                  <div>
                    <Label htmlFor="new-client-name" className="text-[11px] text-muted-foreground">Company Name *</Label>
                    <Input
                      id="new-client-name"
                      placeholder="e.g. BrightMart Supermarket"
                      value={newClientName}
                      disabled={uploading}
                      onChange={(e) => setNewClientName(e.target.value)}
                      className="h-8 text-xs mt-1"
                      required
                    />
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <Label htmlFor="new-client-email" className="text-[11px] text-muted-foreground">Email (for reports)</Label>
                      <Input
                        id="new-client-email"
                        type="email"
                        placeholder="contact@brightmart.com"
                        value={newClientEmail}
                        disabled={uploading}
                        onChange={(e) => setNewClientEmail(e.target.value)}
                        className="h-8 text-xs mt-1"
                      />
                    </div>
                    <div>
                      <Label htmlFor="new-client-phone" className="text-[11px] text-muted-foreground">Phone</Label>
                      <Input
                        id="new-client-phone"
                        placeholder="+91 98765 43210"
                        value={newClientPhone}
                        disabled={uploading}
                        onChange={(e) => setNewClientPhone(e.target.value)}
                        className="h-8 text-xs mt-1"
                      />
                    </div>
                  </div>
                </div>
              ) : (
                <select
                  id="sell-client"
                  className="border-input bg-background h-9 w-full rounded-lg border px-3 text-xs"
                  value={sellClientId}
                  disabled={uploading}
                  onChange={(event) => setSellClientId(event.target.value)}
                >
                  <option value="">-- Choose existing client --</option>
                  {sellClients.map((client) => (
                    <option key={client.id} value={String(client.id)}>
                      {client.name} ({client.client_code})
                    </option>
                  ))}
                </select>
              )}

              {/* Plan selector */}
              <div className="space-y-1.5 pt-1">
                <Label htmlFor="sell-plan" className="text-xs font-semibold text-foreground">Pricing Plan *</Label>
                <select
                  id="sell-plan"
                  className="border-input bg-background h-9 w-full rounded-lg border px-3 text-xs"
                  value={sellPlanId}
                  disabled={uploading}
                  onChange={(event) => {
                    const value = event.target.value
                    setSellPlanId(value)
                    const next = sellPlans.find((plan) => String(plan.id) === value)
                    if (next) setSellScreenIds((current) => current.slice(0, next.max_locations))
                    else if (value !== 'custom') setSellScreenIds([])
                    // 'custom' keeps the current screen selection — it has no cap to trim to.
                  }}
                >
                  <option value="">-- Select plan --</option>
                  {sellPlans.map((plan) => (
                    <option key={plan.id} value={String(plan.id)}>
                      {plan.name} (₹{(plan.price_paise / 100).toLocaleString('en-IN')} · {plan.duration_days} days · up to {plan.max_locations} screens)
                    </option>
                  ))}
                  <option value="custom">Custom — set your own price &amp; days</option>
                </select>
              </div>

              {/* Custom pricing inputs — only when the Custom option is chosen. Mirrors the
                  booking modal: operator-set price and run length, no screen cap. */}
              {isCustomPlan && (
                <div className="grid grid-cols-2 gap-2 pt-1">
                  <div>
                    <Label htmlFor="sell-price" className="text-[11px] text-muted-foreground">Price (₹) *</Label>
                    <Input
                      id="sell-price"
                      type="number"
                      min="0"
                      step="0.01"
                      placeholder="25000"
                      value={sellPrice}
                      disabled={uploading}
                      onChange={(e) => setSellPrice(e.target.value)}
                      className="h-8 text-xs mt-1"
                    />
                  </div>
                  <div>
                    <Label htmlFor="sell-days" className="text-[11px] text-muted-foreground">Default days *</Label>
                    <Input
                      id="sell-days"
                      type="number"
                      min="1"
                      placeholder="30"
                      value={sellDays}
                      disabled={uploading}
                      onChange={(e) => setSellDays(e.target.value)}
                      className="h-8 text-xs mt-1"
                    />
                  </div>
                </div>
              )}

              {/* Screen selector — shown for a real plan or a custom deal */}
              {(chosenPlan || isCustomPlan) && (
                <div className="space-y-1.5 pt-1">
                  <div className="flex items-center justify-between">
                    <Label className="text-xs font-semibold text-foreground">
                      Target Screens *{isCustomPlan && <span className="ml-1 font-normal text-muted-foreground">— set each TV&apos;s days</span>}
                    </Label>
                    <span className="text-[10px] text-muted-foreground">
                      {sellScreenIds.length}{Number.isFinite(screenCap) ? ` of ${screenCap}` : ''} selected
                    </span>
                  </div>
                  <div className="border-hairline max-h-32 space-y-1 overflow-y-auto rounded-xl border p-2 bg-card">
                    {!sellScreens.length && <p className="text-muted-foreground p-2 text-xs">No screens paired yet.</p>}
                    {sellScreens.map((screen) => {
                      const picked = sellScreenIds.includes(screen.id)
                      const full = !picked && sellScreenIds.length >= screenCap
                      return (
                        <label
                          key={screen.id}
                          className={`flex items-center justify-between rounded-lg p-1.5 text-xs transition-colors ${
                            full ? 'opacity-40' : 'hover:bg-muted/60 cursor-pointer'
                          }`}
                        >
                          <div className="flex items-center gap-2 min-w-0">
                            <input
                              type="checkbox"
                              className="accent-primary size-3.5 rounded"
                              checked={picked}
                              disabled={uploading || full}
                              onChange={(event) =>
                                setSellScreenIds((current) =>
                                  event.target.checked ? [...current, screen.id] : current.filter((id) => id !== screen.id)
                                )
                              }
                            />
                            <span className="truncate font-medium text-foreground">{screen.name || `Screen ${screen.id}`}</span>
                          </div>
                          <div className="flex items-center gap-2 shrink-0">
                            {/* Per-TV airtime: only when this screen is part of a custom deal.
                                stopPropagation so typing here does not toggle the row's checkbox. */}
                            {isCustomPlan && picked && (
                              <div className="flex items-center gap-1" onClick={(e) => e.preventDefault()}>
                                <input
                                  type="number"
                                  min="1"
                                  value={screenDays[screen.id] ?? sellDays}
                                  disabled={uploading}
                                  onChange={(e) => setScreenDays((prev) => ({ ...prev, [screen.id]: e.target.value }))}
                                  className="border-input bg-background h-6 w-14 rounded border px-1.5 text-[11px]"
                                  aria-label={`Days for ${screen.name || `Screen ${screen.id}`}`}
                                />
                                <span className="text-[10px] text-muted-foreground">days</span>
                              </div>
                            )}
                            <Badge variant={screen.status === 'online' ? 'success' : 'outline'} className="text-[9px] uppercase py-0 px-1.5">
                              {screen.status}
                            </Badge>
                          </div>
                        </label>
                      )
                    })}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Drag & drop zone */}
          <div
            onDragOver={(event) => { event.preventDefault(); setDragging(true) }}
            onDragLeave={() => setDragging(false)}
            onDrop={(event) => { event.preventDefault(); setDragging(false); addFiles(event.dataTransfer.files) }}
            className={cn(
              'rounded-2xl border border-dashed px-6 py-6 text-center transition-colors',
              dragging ? 'border-primary bg-primary/5' : 'border-border bg-muted/40',
            )}
          >
            <UploadCloud className="text-muted-foreground/60 mx-auto size-7" aria-hidden="true" />
            <p className="text-foreground mt-2 text-sm font-medium">Drop media files here</p>
            <p className="text-muted-foreground/70 mt-0.5 text-xs">Images and video formats supported</p>
            <Button type="button" variant="outline" size="sm" className="bg-card mt-3" disabled={uploading} onClick={() => fileInput.current?.click()}>
              Choose files
            </Button>
            <input
              ref={fileInput}
              type="file"
              multiple
              accept="image/*,video/*"
              className="sr-only"
              onChange={(event) => { addFiles(event.target.files); event.target.value = '' }}
            />
          </div>

          {queue.length > 0 && (
            <>
              <ul className="max-h-48 space-y-2 overflow-y-auto pr-1">
                {queue.map((entry, index) => (
                  <li key={`${entry.file.name}-${index}`} className="border-hairline rounded-xl border p-2.5 bg-card">
                    <div className="flex items-center gap-2">
                      {entry.status === 'done'
                        ? <CheckCircle2 className="size-4 shrink-0 text-emerald-600 dark:text-emerald-400" aria-hidden="true" />
                        : entry.file.type.startsWith('video/')
                          ? <Video className="text-muted-foreground/70 size-4 shrink-0" aria-hidden="true" />
                          : <ImageIcon className="text-muted-foreground/70 size-4 shrink-0" aria-hidden="true" />}
                      <Input
                        value={entry.name}
                        disabled={uploading || entry.status === 'done'}
                        aria-label={`Display name for ${entry.file.name}`}
                        onChange={(event) => patchItem(index, { name: event.target.value })}
                        className="h-8 flex-1 text-xs"
                      />
                      {!uploading && entry.status !== 'done' && (
                        <button
                          type="button"
                          onClick={() => setQueue((current) => current.filter((_, position) => position !== index))}
                          className="text-muted-foreground/50 hover:text-destructive grid size-7 shrink-0 place-items-center rounded-lg cursor-pointer"
                          aria-label={`Remove ${entry.file.name} from the queue`}
                        >
                          <X className="size-4" />
                        </button>
                      )}
                    </div>

                    {entry.status === 'uploading' && (
                      <div className="mt-2 flex items-center gap-2">
                        <div className="bg-muted h-1.5 flex-1 overflow-hidden rounded-full">
                          <div className="bg-primary h-full rounded-full transition-[width] duration-150" style={{ width: `${entry.progress}%` }} />
                        </div>
                        <span className="text-muted-foreground w-9 shrink-0 text-right font-mono text-[11px] tabular-nums">{entry.progress}%</span>
                      </div>
                    )}
                    {entry.status === 'error' && <p className="text-destructive mt-2 text-xs">{entry.error}</p>}
                  </li>
                ))}
              </ul>

              <div className="space-y-1">
                <Label htmlFor="media-tags" className="text-xs text-muted-foreground">Tags <span className="font-normal">(optional, comma separated)</span></Label>
                <Input id="media-tags" value={tags} disabled={uploading} onChange={(event) => setTags(event.target.value)} placeholder="summer, promo, food" className="h-8 text-xs" />
              </div>

              <Button className="w-full" disabled={uploading || !pending} onClick={startUploads}>
                {uploading ? 'Uploading & Booking…' : uploadMode === 'ad' ? `Upload & Book ${pending} Ad${pending === 1 ? '' : 's'}` : `Upload ${pending} file${pending === 1 ? '' : 's'}`}
              </Button>
            </>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )

  const dropHandlers = canEdit ? {
    onDragEnter: (event: React.DragEvent) => {
      if (event.dataTransfer.types.includes('Files')) setPageDragDepth((depth) => depth + 1)
    },
    onDragOver: (event: React.DragEvent) => {
      if (event.dataTransfer.types.includes('Files')) event.preventDefault()
    },
    onDragLeave: () => setPageDragDepth((depth) => Math.max(0, depth - 1)),
    onDrop: (event: React.DragEvent) => {
      if (!event.dataTransfer.files?.length) return
      event.preventDefault()
      setPageDragDepth(0)
      addFiles(event.dataTransfer.files)
      setUploadOpen(true)
    },
  } : {}

  return (
    <div className="relative" {...dropHandlers}>
      {pageDragDepth > 0 && (
        <div className="border-primary bg-primary/5 pointer-events-none fixed inset-4 z-50 grid place-items-center rounded-2xl border-2 border-dashed backdrop-blur-sm">
          <div className="text-center">
            <UploadCloud className="text-primary dark:text-brand mx-auto size-10" aria-hidden="true" />
            <p className="text-foreground mt-3 text-lg font-semibold">Drop to upload</p>
            <p className="text-muted-foreground text-sm">Images and video</p>
          </div>
        </div>
      )}
      {/* Before the library, not after it: the blank tiles below are the symptom and this
          is the cause, so it has to be read first. */}
      <StorageWarning />

      <ListToolbar
        title="Content library"
        action={canEdit ? uploadDialog : <Badge variant="outline">View only</Badge>}
        sort={{ value: sort, onChange: setSort, options: commonSorts }}
        search={{ value: search, onChange: setSearch, placeholder: 'Search' }}
        filters={
          <>
            <DropdownMenuItem onClick={() => setTagFilter(null)} className={!tagFilter ? 'bg-accent font-medium' : undefined}>
              All tags
            </DropdownMenuItem>
            {allTags.map((tag) => (
              <DropdownMenuItem key={tag} onClick={() => setTagFilter(tag)} className={tagFilter === tag ? 'bg-accent font-medium' : undefined}>
                {tag}
              </DropdownMenuItem>
            ))}
            {allTags.length === 0 && <p className="text-muted-foreground px-3 py-2 text-sm">No tags yet</p>}
          </>
        }
      />

      {canEdit && filtered.length > 0 && (
        <div className="mb-3">
          <SelectAllCheckbox
            checked={bulk.allVisibleSelected}
            indeterminate={bulk.someVisibleSelected}
            onChange={bulk.toggleAll}
            label={`Select all ${filtered.length}`}
          />
        </div>
      )}

      {canEdit && (
        <BulkActionBar count={bulk.selected.length} noun="asset" onClear={bulk.clear}>
          <Button size="sm" variant="destructive" onClick={() => setBulkDeleteOpen(true)}>
            <Trash2 data-icon="inline-start" /> Delete selected
          </Button>
        </BulkActionBar>
      )}

      {contentQuery.isLoading ? (
        <AssetGrid>{Array.from({ length: 8 }).map((_, index) => <Skeleton key={index} className="h-64 rounded-xl" />)}</AssetGrid>
      ) : !content.length ? (
        <EmptyState icon={ImageIcon} title="Your library is empty" description="Upload an image or video, then add it to a playlist and publish it to a screen." action={canEdit ? <Button onClick={() => setUploadOpen(true)}><Upload data-icon="inline-start" /> Upload first asset</Button> : undefined} />
      ) : !filtered.length ? (
        <EmptyState icon={Search} title="No matching media" description="Try a different search term or clear the selected tag filter." action={<Button variant="outline" onClick={() => { setSearch(''); setTagFilter(null) }}>Clear filters</Button>} />
      ) : (
        <AssetGrid>
          {filtered.map((item) => {
            const expiry = expiryLabel(item.expires_at)
            const orientation = assetOrientation(item.renditions)
            const duration = clipDuration(item.duration_ms)
            return (
              <div key={item.id} className="relative">
                {canEdit && (
                  <label className="absolute top-2.5 right-2.5 z-10 grid size-7 cursor-pointer place-items-center rounded-md bg-black/50 backdrop-blur">
                    <input
                      type="checkbox"
                      className="accent-primary size-4"
                      checked={bulk.isSelected(item.id)}
                      onChange={(event) => bulk.toggle(item.id, event.target.checked)}
                      aria-label={`Select ${item.name}`}
                    />
                  </label>
                )}
              <AssetCard
                href={`/dashboard/content/${item.id}`}
                title={item.name}
                subtitle={
                  <div className="space-y-1 mt-0.5">
                    <p className="text-xs font-semibold text-primary truncate flex items-center gap-1">
                      <Building2 className="size-3 shrink-0" />
                      <span>{item.client_name || 'Direct Advertiser'}</span>
                    </p>
                    <p className="text-[11px] text-muted-foreground/80">
                      <span className="capitalize">{item.type}</span>
                      {orientation && ` • ${orientation}`}
                      {item.screen_ids && item.screen_ids.length > 0 && ` • ${item.screen_ids.length} screen${item.screen_ids.length > 1 ? 's' : ''}`}
                      {` • ${relativeTime(item.uploaded_at)}`}
                    </p>
                  </div>
                }
                preview={
                  <>
                    <MediaThumbnail item={item} className="size-full" />
                    {item.status === 'processing' && (
                      <div className="bg-background/50 absolute inset-0 grid place-items-center backdrop-blur-[2px]">
                        <div className="flex flex-col items-center gap-2">
                          <div className="border-primary size-6 animate-spin rounded-full border-2 border-t-transparent" />
                          <span className="text-foreground text-[11px] font-semibold tracking-wider uppercase">Processing</span>
                        </div>
                      </div>
                    )}
                  </>
                }
                badges={
                  <>
                    {item.client_name && (
                      <OverlayBadge tone="neutral" className="bg-slate-900/90 text-primary border-primary/40 font-semibold text-[10px]">
                        {item.client_name}
                      </OverlayBadge>
                    )}
                    {item.plan_name && (
                      <OverlayBadge tone="neutral" className="text-[10px]">
                        {item.plan_name}
                      </OverlayBadge>
                    )}
                    {expiry && <OverlayBadge tone="danger">{expiry}</OverlayBadge>}
                    {item.status === 'failed' && <OverlayBadge tone="danger">Failed</OverlayBadge>}
                  </>
                }
                cornerBadge={duration ? <OverlayBadge>{duration}</OverlayBadge> : undefined}
                menu={canEdit ? (
                  <>
                    <DropdownMenuItem onClick={() => setEditingClientItem(item)} className="font-medium">
                      <Building2 className="size-4 text-primary" /> Edit Client & Ad Details
                    </DropdownMenuItem>
                    {item.status === 'failed' && (
                      <DropdownMenuItem onClick={() => retryMutation.mutate(item.id)}>
                        <RefreshCw aria-hidden="true" /> Retry processing
                      </DropdownMenuItem>
                    )}
                    <DropdownMenuItem onClick={() => setDeleteItem(item)} className="text-destructive">
                      <Trash2 aria-hidden="true" /> Delete
                    </DropdownMenuItem>
                  </>
                ) : undefined}
              />
              </div>
            )
          })}
        </AssetGrid>
      )}

      <Dialog open={bulkDeleteOpen} onOpenChange={setBulkDeleteOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete {bulk.selected.length} asset{bulk.selected.length === 1 ? '' : 's'}?</DialogTitle>
            <DialogDescription>
              They are removed from every playlist, and their files and transcoded renditions are deleted from disk. Players stop using them on the next sync.
            </DialogDescription>
          </DialogHeader>
          {/* Same warning as the single delete, counted. Selecting a page of assets and
              deleting is exactly where a sold advert goes unnoticed. */}
          {soldInSelection.length > 0 && (
            <div className="rounded-xl border border-rose-500/30 bg-rose-500/10 p-3.5 text-sm">
              <p className="font-semibold text-foreground">
                {soldInSelection.length} of these {soldInSelection.length === 1 ? 'is' : 'are'} sold.
              </p>
              <p className="mt-1 text-muted-foreground">
                {soldInSelection.slice(0, 3).map((item) => item.client_name || item.name).join(', ')}
                {soldInSelection.length > 3 ? ` and ${soldInSelection.length - 3} more` : ''}.
                Their bookings and payment records are deleted too. Past play history is kept.
              </p>
            </div>
          )}
          <DialogFooter showCloseButton>
            <Button variant="destructive" disabled={bulkDelete.isPending} onClick={() => bulkDelete.mutate()}>
              {bulkDelete.isPending ? 'Deleting…' : `Delete ${bulk.selected.length}`}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={Boolean(deleteItem)} onOpenChange={(open) => !open && setDeleteItem(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete &ldquo;{deleteItem?.name}&rdquo;?</DialogTitle>
            <DialogDescription>
              This removes the asset from every playlist. Players will stop using it on their next sync.
            </DialogDescription>
          </DialogHeader>
          {/* The booking goes with it. AdPlacement.content_id is ON DELETE CASCADE, so
              deleting a creative takes the sale, its locations, its extensions and its
              payment record with it -- and the dialog used to mention only playlists, so a
              tenant tidying their library destroyed an invoice without being told. Play
              history survives (PlayLog.media_id carries no foreign key, deliberately), so
              past reports still add up. */}
          {deleteItem?.placement_id && (
            <div className="rounded-xl border border-rose-500/30 bg-rose-500/10 p-3.5 text-sm">
              <p className="font-semibold text-foreground">This advert is sold.</p>
              <p className="mt-1 text-muted-foreground">
                {deleteItem.client_name ? <><span className="text-foreground">{deleteItem.client_name}</span>&nbsp;&middot;&nbsp;</> : null}
                {deleteItem.placement_price_paise ? <>{rupees(deleteItem.placement_price_paise)}&nbsp;&middot;&nbsp;</> : null}
                {deleteItem.placement_ends_at ? <>runs until {new Date(deleteItem.placement_ends_at).toLocaleDateString()}</> : null}
              </p>
              <p className="mt-2 text-muted-foreground">
                Deleting it also deletes the booking and its payment record. Past play history
                and completed reports are kept.
              </p>
            </div>
          )}
          <DialogFooter showCloseButton><Button variant="destructive" disabled={deleteMutation.isPending} onClick={() => deleteItem && deleteMutation.mutate(deleteItem.id)}>{deleteMutation.isPending ? 'Deleting…' : 'Delete media'}</Button></DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Unified Edit Client & Ad Details Modal */}
      <EditClientAdModal
        open={Boolean(editingClientItem)}
        onOpenChange={(open) => !open && setEditingClientItem(null)}
        contentItem={editingClientItem}
      />
    </div>
  )
}
